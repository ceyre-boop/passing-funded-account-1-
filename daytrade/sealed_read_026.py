#!/usr/bin/env python3
"""SEALED-002 READ — spec 026 SUPERHUMAN-001, the one-time holdout number.

THIS SCRIPT BURNS A HOLDOUT. Running it is Colin's call, not an agent's.

It evaluates the six SUPERHUMAN-001 gates on SEALED-002 (sessions >= 2026-08-18,
which existed at no prior tuning time) and writes ONE record. After it runs,
SEALED-002 is burned for this `rule_version` exactly as SEALED-001 was burned on
2026-08-17 by `futures-exit-v1` — a verdict of NOT ACHIEVED does NOT license a
re-read, a re-tune or a third holdout. Spec 026's stop conditions are explicit
about that.

PRECONDITIONS, all enforced rather than trusted:
  1. `splits.sealed_002_sessions` refuses unless a `rule_version` is supplied,
     the working tree under daytrade/ is clean, and that rule_version already
     appears in a commit — so the rules were frozen BEFORE the holdout was seen.
  2. n >= 250 entries (spec 026's power requirement). Below that the script
     REFUSES and reports BLOCKED-underpowered rather than producing a number
     with an error bar wider than the effect.
  3. The TRAIN result must already exist, so the holdout is confirming a
     pre-stated expectation rather than fishing for one.

WHAT IT REPORTS, always together:
  G-S1 net R vs bank-like, with a DATE-clustered bootstrap CI
  G-S2 margin over the best hand-executable fixed policy
  G-S3 a placebo control through the identical pipeline (regime labels permuted
       within date) — it must FAIL where the real one passes
  G-S4 both halves by date, and the per-symbol breakdown
  G-S5 a permutation test paying for the number of models considered
  G-S6 read from the regime_labels report, not recomputed here
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import entry_selection as es                                   # noqa: E402
import regime as regime_mod                                    # noqa: E402
import splits                                                  # noqa: E402

ROOT = HERE.parent
OUT = ROOT / "data" / "daytrade" / "sealed_read_002_superhuman.json"
TRAIN_REPORT = ROOT / "data" / "daytrade" / "entry_selection_report.json"
LABELS_REPORT = ROOT / "data" / "daytrade" / "regime_labels_report.json"

MIN_N = 250
BANKLIKE = es.BANKLIKE_NET_R
MARGIN_REQ = 0.05


def _half(rows):
    days = sorted({r["session"] for r in rows})
    mid = days[len(days) // 2]
    return ([r for r in rows if r["session"] < mid],
            [r for r in rows if r["session"] >= mid])


def _permutation_p(rows, col, base_col, n_models, iters=10000, seed=7):
    """Is the margin bigger than relabelling chance, paid for n_models looks?

    Within each DATE, swap which of the two policies' R values is called "the
    model". The null is that the two are exchangeable; dates stay intact so the
    one-bet-per-morning structure is preserved. The p-value is then Bonferroni-
    adjusted by the number of models that were considered before this read.
    """
    by_day = {}
    for r in rows:
        by_day.setdefault(r["session"], []).append(r)
    obs = (statistics.fmean([es.net(r, col) for r in rows])
           - statistics.fmean([es.net(r, base_col) for r in rows]))
    rng = random.Random(seed)
    hits = 0
    for _ in range(iters):
        a, b = [], []
        for day, rs in by_day.items():
            flip = rng.random() < 0.5
            for r in rs:
                a.append(es.net(r, base_col if flip else col))
                b.append(es.net(r, col if flip else base_col))
        if (statistics.fmean(a) - statistics.fmean(b)) >= obs:
            hits += 1
    raw = (hits + 1) / (iters + 1)
    return raw, min(1.0, raw * max(n_models, 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 026 SEALED-002 read — BURNS THE HOLDOUT")
    ap.add_argument("--rule-version", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--n-models", type=int, required=True,
                    help="how many models/configs were considered on TRAIN before "
                         "this read — G-S5 pays for every one of them")
    ap.add_argument("--i-am-colin-and-i-accept-this-burns-the-holdout",
                    action="store_true", required=False, dest="consent")
    a = ap.parse_args(argv)

    if not a.consent:
        print("REFUSED: this read burns SEALED-002 permanently. Re-run with "
              "--i-am-colin-and-i-accept-this-burns-the-holdout if that is the "
              "intention. No agent may pass this flag on Colin's behalf.")
        return 2

    if not TRAIN_REPORT.exists():
        print(f"REFUSED: {TRAIN_REPORT.name} does not exist. The holdout confirms "
              f"a pre-stated TRAIN expectation; it does not go first.")
        return 2
    train = json.loads(TRAIN_REPORT.read_text())
    e1 = train.get("E1_policy_selection_train") or {}
    base_col = train.get("E1_best_fixed")
    train_margin = train.get("E1_margin_vs_best_fixed_r")
    print(f"  TRAIN said: best fixed {base_col}, regime margin {train_margin:+.4f}R")

    rows = [r for r in train.get("rows", []) if r.get("band") == "SEALED"]
    if len(rows) < MIN_N:
        print(f"  BLOCKED — underpowered: SEALED-002 holds {len(rows)} entries, "
              f"spec 026 requires >= {MIN_N}. Not reading.")
        return 3

    # The guard that actually matters: refuses unless the rules are committed.
    import bars
    probe = []
    for sym in [s for v in es.CLASSES.values() for s in v]:
        try:
            probe.extend(bars.load_sessions(sym, "5m", allow_fetch=False,
                                            on_gap="exclude"))
        except Exception:
            pass
    splits.sealed_002_sessions(probe, unseal_reason=a.reason,
                              rule_version=a.rule_version)

    cols = [c for c in rows[0] if c.startswith("r_") and c != "r_oracle"]
    res = {c: es.summarise(rows, c) for c in cols}

    print(f"\n  SEALED-002: n {len(rows)} entries, "
          f"{len({r['session'] for r in rows})} dates")
    for c in cols:
        s = res[c]
        print(f"    {c:14} gross {s['mean_gross_r']:+.4f}  net {s['mean_net_r']:+.4f}  "
              f"CI {s['net_ci95']}  win {s['win_rate']:.0%}")

    m = res["r_regime"]["mean_net_r"] - res[base_col]["mean_net_r"]
    lo = res["r_regime"]["net_ci95"][0]
    g1 = res["r_regime"]["mean_net_r"] >= BANKLIKE and lo is not None and lo > 0
    g2 = m >= MARGIN_REQ

    # G-S3 placebo: permute the regime label within each date, re-derive policy.
    rng = random.Random(99)
    by_day = {}
    for r in rows:
        by_day.setdefault(r["session"], []).append(r)
    placebo = []
    for day, rs in by_day.items():
        labels = [r["regime"] for r in rs]
        rng.shuffle(labels)
        for r, lab in zip(rs, labels):
            pol = regime_mod.POLICY[lab]
            key = {"RIDE": "r_TRAIL_WIDE", "DEFEND": "r_STATIC",
                   "HARVEST": "r_TRAIL_TIGHT"}.get(pol, base_col)
            placebo.append({**r, "r_placebo": r.get(key, r[base_col])})
    pl = es.summarise(placebo, "r_placebo")
    g3 = not (pl["mean_net_r"] >= BANKLIKE)

    h1, h2 = _half(rows)
    s1, s2 = es.summarise(h1, "r_regime"), es.summarise(h2, "r_regime")
    by_sym = {}
    for sym in {r["symbol"] for r in rows}:
        sub = [r for r in rows if r["symbol"] == sym]
        if len(sub) >= 10:
            by_sym[sym] = es.summarise(sub, "r_regime")["mean_net_r"]
    pos = sum(1 for v in by_sym.values() if v > 0)
    g4 = (s1["mean_net_r"] > 0 and s2["mean_net_r"] > 0
          and by_sym and pos / len(by_sym) >= 0.60)

    raw_p, adj_p = _permutation_p(rows, "r_regime", base_col, a.n_models)
    g5 = adj_p < 0.05

    labels = json.loads(LABELS_REPORT.read_text()) if LABELS_REPORT.exists() else {}
    g6 = bool(labels.get("gate_G_S6_n_met") and labels.get("gate_G_S6_brier_skill_met"))

    gates = {"G_S1_banklike_oos": g1, "G_S2_beats_hand_executable": g2,
             "G_S3_placebo_fails": g3, "G_S4_not_one_name_or_month": g4,
             "G_S5_multiple_comparisons": g5, "G_S6_reward_signal_scored": g6}
    verdict = "ACHIEVED" if all(gates.values()) else "NOT ACHIEVED"

    print(f"\n  placebo (G-S3) net {pl['mean_net_r']:+.4f} — must NOT clear {BANKLIKE}")
    print(f"  halves: {s1['mean_net_r']:+.4f} / {s2['mean_net_r']:+.4f}")
    print(f"  symbols net-positive: {pos}/{len(by_sym)}")
    print(f"  permutation p raw {raw_p:.4f} -> adjusted for {a.n_models} models {adj_p:.4f}")
    print(f"\n  MARGIN vs {base_col}: {m:+.4f}R")
    for k, v in gates.items():
        print(f"    {k:34} {'PASS' if v else 'FAIL'}")
    print(f"\n  SUPERHUMAN-001: {verdict}")

    OUT.write_text(json.dumps({
        "read_at": datetime.now(timezone.utc).isoformat(),
        "rule_version": a.rule_version, "reason": a.reason,
        "sealed_boundary": f">= {splits.SEALED_002_START}",
        "n_entries": len(rows), "n_dates": len({r["session"] for r in rows}),
        "train_margin_r": train_margin, "best_fixed": base_col,
        "results": res, "margin_r": round(m, 4),
        "placebo": pl, "halves": [s1, s2], "by_symbol_net_r": by_sym,
        "permutation_p_raw": round(raw_p, 5),
        "permutation_p_adjusted": round(adj_p, 5), "n_models_considered": a.n_models,
        "gates": gates, "verdict": verdict,
        "law": "SEALED-002 is now BURNED for this rule_version. A NOT ACHIEVED "
               "verdict does not license a re-read, a re-tune, or a third "
               "holdout (spec 026 stop conditions).",
    }, indent=1, default=str))
    print(f"  record: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
