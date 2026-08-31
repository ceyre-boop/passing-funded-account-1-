#!/usr/bin/env python3
"""az/stop.py — the Gate 3a STOP. Reports; fits nothing.

Spec 049 §2: STOP fires only if BOTH fail, adjudicated on the PESSIMISTIC arm.
Absolute R everywhere, no deltas. N_days in every header.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))
from az.prereg import ArmResult, adjudicate, max_stat_null, sub_period_verdict, sub_periods  # noqa: E402
from az.report import Tally                                                                  # noqa: E402
from mechanisms import mde                                                                   # noqa: E402

TABLE = ROOT / "artifacts" / "az_gate3a_table.parquet"
ADJUDICATES = "pess"
MIN_DAYS = 30


def main() -> int:
    df = pd.read_parquet(TABLE)
    df["day_dt"] = pd.to_datetime(df["day"]).dt.date
    n_days = df.groupby("symbol")["day"].nunique().sum()
    print(f"GATE 3a STOP REPORT   {Tally(n_days=int(n_days), n_candidates=int(len(df)//6)).header()}")
    print(f"adjudicated on the PESSIMISTIC arm (locked before the table existed)\n")

    # ---- distribution, all graders, both fills, ABSOLUTE R
    print(f"{'grader':<18} {'fill':<5} {'N_days':>7} {'n_cand':>8} {'mean R':>9} {'sd':>7} "
          f"{'p10':>8} {'p50':>8} {'p90':>8} {'frac>0':>7}")
    print("-"*96)
    for g in ("G1_frozen", "G2_hold_to_close", "G3_fixed_r"):
        for f in ("base", "pess"):
            s = df[(df.grader == g) & (df.fill == f)]
            print(f"{g:<18} {f:<5} {s.day.nunique():>7,} {len(s):>8,} {s.R.mean():>+9.4f} "
                  f"{s.R.std():>7.3f} {np.percentile(s.R,10):>+8.3f} {np.percentile(s.R,50):>+8.3f} "
                  f"{np.percentile(s.R,90):>+8.3f} {(s.R>0).mean():>7.1%}")

    # ---- PRIMARY: pooled mean R per symbol, no cell selection
    print(f"\nPRIMARY — pooled mean R, no cell selection, {ADJUDICATES} arm")
    arms = []
    for sym in ("SPY", "QQQ"):
        s = df[(df.symbol == sym) & (df.grader == "G1_frozen") & (df.fill == ADJUDICATES)]
        per_day = s.groupby("day_dt").R.mean()
        thr = mde(float(per_day.std()), len(per_day))
        arms.append(ArmResult(sym, float(per_day.mean()), float(thr)))
        print(f"  {sym}  N_days {len(per_day):,}  mean R {per_day.mean():+.5f}  "
              f"total R {s.R.sum():+.1f}  threshold {thr:.5f}")
    pair = adjudicate(*arms)
    primary_pass = pair.verdict == "HOLDS" and all(a.value > 0 for a in arms)
    print(f"  pair rule: {pair.header()}")
    print(f"  PRIMARY {'SURVIVES' if primary_pass else 'FAILS'}")

    # ---- SECONDARY: max cell mean vs the day-shift null, over EVERY cell scanned
    print(f"\nSECONDARY — max cell mean vs circular day-shift null, {ADJUDICATES} arm")
    sec_survives = False
    for sym in ("SPY", "QQQ"):
        s = df[(df.symbol == sym) & (df.grader == "G1_frozen") & (df.fill == ADJUDICATES)] \
            .sort_values(["day", "hhmm", "direction"])
        codes, uniq = pd.factorize(s.cell_dir)
        dcodes, _ = pd.factorize(s.day)
        try:
            r = max_stat_null(codes, s.R.to_numpy(), dcodes, min_days=MIN_DAYS)
            print(f"  {sym}  cells scanned {len(uniq):,}  valued {r['n_cells_valued']:,}  "
                  f"best cell mean R {r['observed_max_cell_mean']:+.4f}  "
                  f"null p95 {r['null_p95']:+.4f}  p={r['p_value']:.4f}  "
                  f"{'SURVIVES' if r['survives'] else 'does not survive'}")
            sec_survives = sec_survives or r["survives"]
        except Exception as e:
            print(f"  {sym}  null refused: {type(e).__name__}: {str(e)[:90]}")
    print(f"  SECONDARY {'SURVIVES' if sec_survives else 'FAILS'}")

    # ---- sub-periods
    print(f"\nSUB-PERIODS (G1, {ADJUDICATES})")
    s = df[(df.grader == "G1_frozen") & (df.fill == ADJUDICATES)]
    dv = s.groupby("day_dt").R.mean().to_dict()
    res = sub_periods(dv, float(np.std(list(dv.values()))))
    for p in res:
        print(f"  {p.name}  N_days {p.n_days:>5,}  mean R {p.value:+.5f}  thr {p.threshold:.5f}  "
              f"share {p.contribution:+.1%}  {'clears' if p.clears else 'below'}")
    print(f"  {' | '.join(sub_period_verdict(res))}")

    # ---- multi-grader stability (amendment 3)
    print("\nMULTI-GRADER STABILITY")
    piv = df[df.fill == ADJUDICATES].pivot_table(
        index=["symbol", "day", "hhmm", "direction"], columns="grader", values="R")
    for a, b in (("G1_frozen","G2_hold_to_close"), ("G1_frozen","G3_fixed_r"), ("G2_hold_to_close","G3_fixed_r")):
        print(f"  Spearman {a} vs {b}: {piv[a].corr(piv[b], method='spearman'):+.4f}")
    top = piv.nlargest(max(1, len(piv)//100), "G1_frozen")
    for other in ("G2_hold_to_close", "G3_fixed_r"):
        pct = (piv[other].rank(pct=True).reindex(top.index)).mean()
        print(f"  top 1% by G1 sit at the {pct:.1%} percentile of {other}"
              f"{'  <-- grader exploitation' if pct < 0.60 else ''}")

    verdict = "STOP" if (not primary_pass and not sec_survives) else "NO STOP"
    print(f"\n{'='*70}\nVERDICT: {verdict}")
    print("  spec 049 §2 — STOP fires only if BOTH primary and secondary fail.")
    if verdict == "STOP":
        print("  No positive-expectancy region. The policy layer is moot; this finding IS the output.")
    else:
        print("  A region survived. Gate 3b is not built without a ruling.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
