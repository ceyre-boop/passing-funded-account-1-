#!/usr/bin/env python3
"""ENTRY SELECTION — the SUPERHUMAN-001 measurement harness (spec 026).

WHAT QUESTION THIS ANSWERS
--------------------------
`data/daytrade/exit_quality.json` settled where the headroom is NOT: 57.1% of
entries are UNWINNABLE — no exit config in the 396-wide space profits on them —
and realized gross is +0.1538R against a perfect-hindsight ceiling of +0.8234R.
Spec 026 drew the ruling from that: **exit tuning is the wrong lever, entry
selection is the binding constraint.**

So this harness runs two experiments, both on the TRAIN split only:

  E1  POLICY SELECTION. Does conditioning the exit intent on the regime read
      (regime.POLICY) beat the best FIXED policy? This is spec 001's stated
      payoff and it keeps every entry.

  E2  ENTRY FILTERING. Does the regime read, available at entry time, predict
      which entries are winnable — so the unwinnable ones can be SKIPPED? This
      is the bigger prize and the one exit_quality points at.

HONEST PRIOR, STATED BEFORE THE RUN
-----------------------------------
Two closely related attempts on this data already returned null:
  - spec 025's pooled exit evaluator: NO_SUPERSEDE.
  - `residual_model.py`: entry-time conditions -> giveback, skill_vs_baseline
    -0.2458, OOF correlation -0.0728, verdict NO_SKILL.
Both were run at n_days=39. The deepened cache (commit 431fb94) takes TRAIN from
374 to 5,126 entries over ~650 session-days, so those nulls were underpowered
rather than conclusive — but the prior is clearly unfavourable and is recorded
here so a positive result has to argue against it.

THE COST MODEL, STATED ONCE
---------------------------
`ceiling.simulate` already charges COST_PER_SHARE = $0.02 round-trip, divided by
the entry's risk — roughly 0.01R, NOT the 0.10R all-in figure CLAUDE.md names as
realistic. Those are different quantities and adding them naively double-counts.
So every number here carries both, explicitly:

    gross_R  = simulate_R + embedded_cost_R      (before ANY cost)
    net_R    = gross_R - COST_TARGET_R           (COST_TARGET_R = 0.10)

`embedded_cost_R` is measured per entry, not assumed. Spec 026's gates are on
net_R, and the measured embedded cost is reported so the 0.10R assumption can be
replaced by a real number when the live ledger has one.

NO LOOKAHEAD
------------
`classify` is handed the tape ending at the ENTRY bar and nothing later —
`regime.build_evidence` raises if the frame reaches past `ctx.now_et`. The oracle
and realized R are computed from bars after the entry, as they must be, and
never flow back into the classification.

SPLIT DISCIPLINE
----------------
This module reads TRAIN and DEV only and has no code path to SEALED-002. Reading
the holdout goes through `splits.sealed_002_sessions` from a separate, deliberate
script after a `rule_version` is frozen and committed. There is no flag here that
would do it by accident.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bars                                                   # noqa: E402
import ceiling                                                # noqa: E402
import regime as regime_mod                                   # noqa: E402
import splits                                                 # noqa: E402
from bars import BarDataError, load_sessions                  # noqa: E402
from ceiling import COST_PER_SHARE, find_entry, simulate, wide_space  # noqa: E402

ROOT = HERE.parent
OUT = ROOT / "data" / "daytrade" / "entry_selection_report.json"

COST_TARGET_R = 0.10          # CLAUDE.md's realistic all-in cost per trade
BREAKEVEN_NET_R = 0.05        # CLAUDE.md: net above this is break-even
BANKLIKE_NET_R = 0.10         # CLAUDE.md: net around this is bank-like

CLASSES = {
    "SINGLE_NAME": ["NVDA", "AAPL", "AMD", "AMZN", "GOOGL", "META", "MSFT", "TSLA"],
    "CASH_INDEX": ["SPY", "QQQ", "DIA", "IWM"],
    "FUTURES": ["ES=F", "NQ=F", "RTY=F", "YM=F", "CL=F", "GC=F", "SI=F"],
}
SYM_CLASS = {s: c for c, v in CLASSES.items() for s in v}

# The three hand-executable baselines, as spec 003 shipped them. A human can run
# any of these from a chart; that is exactly why they are the "better than a
# human" benchmark in spec 026 gate G-S2.
SHIPPED = ceiling.narrow_space()


def _intent_cfg(intent: str) -> dict:
    """Exit intent name -> a ceiling.simulate cfg. One implementation: the
    parameters come from stockfish_exit.INTENT, never re-typed here."""
    import stockfish_exit
    trail_mult, be_arm_frac, hold_past_tp2 = stockfish_exit.INTENT[intent]
    return {"trail_mult": trail_mult, "be_arm_frac": be_arm_frac,
            "hold_past_tp2": hold_past_tp2, "partial_frac": 0.5,
            "flatten_et": None}


INTENT_CFG = None      # built lazily inside workers (import order)


LOOKBACK_SESSIONS = 60        # fixed history window handed to classify()


def _lookback_start(cont, day):
    """First timestamp of the LOOKBACK_SESSIONS-th session before `day`."""
    prior = sorted({d for d in cont.index.date if d < day})
    if len(prior) <= LOOKBACK_SESSIONS:
        return cont.index[0]
    first_day = prior[-LOOKBACK_SESSIONS]
    return cont.index[cont.index.date >= first_day][0]


def _continuous(sessions):
    """One ET-indexed frame from a list of complete Sessions, in order.

    EMA200 on 5m spans about 2.5 sessions, so the classifier needs more than the
    day it is reading. Concatenating the SAME Session objects the simulator uses
    keeps one source of bars — rebuilding from parquet here would be a second
    path that could drift (the 70-bar completeness gate excludes half days, and
    a parallel reader would silently include them).
    """
    import pandas as pd
    return pd.concat([s.df for s in sessions]).sort_index()


def _row_for(sym, sessions, cont, sess, grid, want_oracle):
    e = find_entry(sess)
    if e is None:
        return None
    import pandas as pd
    entry_ts = pd.Timestamp(e.ts)
    entry_hhmm = entry_ts.strftime("%H:%M")

    # The tape AS IT STOOD at the entry bar — nothing later. This slice IS the
    # no-lookahead guarantee for the classifier; regime.build_evidence also
    # re-asserts that the frame ends exactly at ctx.now_et.
    # Bounded lookback. The classifier's deepest need is EMA200 on 5m (200 bars),
    # the 14-session daily ATR and the volume-at-this-minute average over prior
    # sessions, so LOOKBACK_SESSIONS of history is more than sufficient. It is
    # FIXED rather than "all history" for two reasons: slicing 54k bars per entry
    # made the run quadratic, and an EMA whose warmup length grows with calendar
    # position would make the same session classify differently in 2024 and 2026.
    cut = _lookback_start(cont, sess.day)
    upto = cont[(cont.index >= cut) & (cont.index <= entry_ts)]

    prior_days = sorted({d for d in cont.index.date if d < sess.day})
    pdh = pdl = None
    if prior_days:
        pd_ = cont[cont.index.date == prior_days[-1]]
        pdh, pdl = float(pd_["High"].max()), float(pd_["Low"].min())

    ctx = regime_mod.Context(
        symbol=sym, now_et=entry_hhmm, session_day=sess.day,
        pdh=pdh, pdl=pdl, onh=None, onl=None,
        unavailable=("onh", "onl"),
        unavailable_reason="overnight pools need extended-hours bars; the Alpaca "
                           "source is clipped to regular hours by design")
    try:
        read = regime_mod.classify(upto, None, ctx)
    except regime_mod.RegimeError as ex:
        return {"symbol": sym, "session": str(sess.day), "skipped": str(ex)}

    global INTENT_CFG
    if INTENT_CFG is None:
        INTENT_CFG = {k: _intent_cfg(k) for k in ("RIDE", "DEFEND", "HARVEST")}

    embedded = COST_PER_SHARE / e.risk
    out = {
        "symbol": sym, "class": SYM_CLASS[sym], "session": str(sess.day),
        "entry_et": entry_hhmm, "direction": int(e.direction),
        "risk": round(float(e.risk), 6),
        "embedded_cost_r": round(embedded, 6),
        "regime": read.regime, "confidence": read.confidence,
        "time_block": read.time_block, "flow": read.direction_of_flow,
        "exit_policy": read.exit_policy,
        "conf_floored": read.evidence["conf_floored"],
        "score_raw": read.evidence["score_raw"],
    }
    for name, cfg in SHIPPED.items():
        out[f"r_{name}"] = round(simulate(sess, e, dict(cfg)), 6)
    out["r_regime"] = round(simulate(sess, e, dict(INTENT_CFG[read.exit_policy])), 6)
    if want_oracle:
        out["r_oracle"] = round(max(simulate(sess, e, dict(c)) for c in grid), 6)
    # a handful of evidence fields the scorecard/regression will want
    for k in ("atr5", "atr_daily", "atr_expansion", "range_pct_of_atr",
              "volume_ratio", "wick_dominance", "vwap_crosses", "or_range",
              "or_complete", "ema_stack"):
        out[k] = read.evidence.get(k)
    return out


def _work(args):
    sym, want_oracle = args
    try:
        sessions = load_sessions(sym, "5m", allow_fetch=False, on_gap="exclude")
    except BarDataError as ex:
        return sym, [], f"{ex}"
    if not sessions:
        return sym, [], "no complete sessions"
    cont = _continuous(sessions)
    grid = list(wide_space().values()) if want_oracle else []
    rows = []
    for sess in sessions:
        try:
            r = _row_for(sym, sessions, cont, sess, grid, want_oracle)
        except Exception as ex:                   # fail loud, per symbol
            return sym, rows, f"{type(ex).__name__} on {sess.day}: {ex}"
        if r:
            rows.append(r)
    return sym, rows, None


def band_of(day_str: str) -> str:
    from datetime import date
    y, m, d = (int(x) for x in day_str.split("-"))
    day = date(y, m, d)
    if day <= splits.TUNE_END:
        return "TRAIN"
    if day < splits.SEALED_002_START:
        return "DEV"
    return "SEALED"


def gross(row, col): return row[col] + row["embedded_cost_r"]
def net(row, col):   return gross(row, col) - COST_TARGET_R


def cluster_bootstrap(rows, col, n=5000, seed=12345):
    """95% CI on mean net R, resampling ENTRY DATES, not trades.

    The entry rule fires across many symbols the same morning — that is
    approximately one bet, not nineteen. Resampling trades would shrink the
    interval by pretending they were independent. Spec 026 G-S1 requires this.
    """
    by_day = {}
    for r in rows:
        by_day.setdefault(r["session"], []).append(net(r, col))
    days = list(by_day)
    if len(days) < 2:
        return None, None
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        pick = [rng.choice(days) for _ in days]
        vals = [v for d in pick for v in by_day[d]]
        means.append(sum(vals) / len(vals))
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n)]


def summarise(rows, col):
    if not rows:
        return None
    g = [gross(r, col) for r in rows]
    nt = [net(r, col) for r in rows]
    lo, hi = cluster_bootstrap(rows, col)
    return {
        "n": len(rows), "n_days": len({r["session"] for r in rows}),
        "mean_gross_r": round(sum(g) / len(g), 4),
        "mean_net_r": round(sum(nt) / len(nt), 4),
        "median_net_r": round(statistics.median(nt), 4),
        "win_rate": round(sum(1 for v in nt if v > 0) / len(nt), 4),
        "net_ci95": [None if lo is None else round(lo, 4),
                     None if hi is None else round(hi, 4)],
        "clears_breakeven": (sum(nt) / len(nt)) >= BREAKEVEN_NET_R,
        "clears_banklike": (sum(nt) / len(nt)) >= BANKLIKE_NET_R,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 026 E1/E2 measurement (TRAIN/DEV only)")
    ap.add_argument("--no-oracle", action="store_true",
                    help="skip the 396-config hindsight ceiling (much faster)")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    want_oracle = not a.no_oracle

    syms = [s for v in CLASSES.values() for s in v]
    rows, problems = [], []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for sym, got, err in ex.map(_work, [(s, want_oracle) for s in syms]):
            if err:
                problems.append(f"{sym}: {err}")
            rows.extend(got)
            print(f"  {sym:7} {len(got):5} entries"
                  + (f"   !! {err}" if err else ""), flush=True)

    skipped = [r for r in rows if "skipped" in r]
    rows = [r for r in rows if "skipped" not in r]
    for r in rows:
        r["band"] = band_of(r["session"])
    train = [r for r in rows if r["band"] == "TRAIN"]
    dev = [r for r in rows if r["band"] == "DEV"]
    sealed_n = sum(1 for r in rows if r["band"] == "SEALED")

    print(f"\n  rows {len(rows)}  TRAIN {len(train)}  DEV {len(dev)}  "
          f"SEALED {sealed_n} (NOT READ)  classifier-skipped {len(skipped)}")
    emb = [r["embedded_cost_r"] for r in train]
    print(f"  embedded cost/trade (measured): median {statistics.median(emb):.4f}R  "
          f"mean {sum(emb)/len(emb):.4f}R   | COST_TARGET {COST_TARGET_R}R")

    cols = [f"r_{n}" for n in SHIPPED] + ["r_regime"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule_version": regime_mod.RULE_VERSION,
        "spec": "026_SUPERHUMAN_TARGET",
        "cost_model": {"embedded_median_r": round(statistics.median(emb), 4),
                       "cost_target_r": COST_TARGET_R,
                       "breakeven_net_r": BREAKEVEN_NET_R,
                       "banklike_net_r": BANKLIKE_NET_R},
        "counts": {"rows": len(rows), "train": len(train), "dev": len(dev),
                   "sealed_not_read": sealed_n, "classifier_skipped": len(skipped)},
        "problems": problems,
    }

    # ---- E1: policy selection, TRAIN
    print("\n  ── E1  POLICY SELECTION (TRAIN) ──")
    e1 = {c: summarise(train, c) for c in cols}
    report["E1_policy_selection_train"] = e1
    base_col = max((c for c in cols if c != "r_regime"),
                   key=lambda c: e1[c]["mean_net_r"])
    for c in cols:
        s = e1[c]
        tag = " <- best fixed" if c == base_col else (" <- REGIME" if c == "r_regime" else "")
        print(f"    {c:14} n {s['n']:5}  gross {s['mean_gross_r']:+.4f}  "
              f"net {s['mean_net_r']:+.4f}  CI {s['net_ci95']}  "
              f"win {s['win_rate']:.0%}{tag}")
    margin = e1["r_regime"]["mean_net_r"] - e1[base_col]["mean_net_r"]
    report["E1_margin_vs_best_fixed_r"] = round(margin, 4)
    report["E1_best_fixed"] = base_col
    print(f"    MARGIN regime vs {base_col}: {margin:+.4f}R  "
          f"(spec 026 G-S2 needs >= +0.05R)")

    # ---- E2: is winnability predictable at entry time?
    if want_oracle:
        print("\n  ── E2  ENTRY FILTERING (TRAIN) ──")
        winnable = [r for r in train if r["r_oracle"] > 0]
        print(f"    unwinnable {len(train)-len(winnable)}/{len(train)} "
              f"= {100*(1-len(winnable)/len(train)):.1f}%  "
              f"(exit_quality reported 57.1% at n=336)")
        e2 = {"pct_unwinnable": round(100 * (1 - len(winnable) / len(train)), 2),
              "by_regime": {}, "by_confidence": {}}
        for rg in ("CONTINUATION", "MANIPULATION", "CONSOLIDATION"):
            sub = [r for r in train if r["regime"] == rg]
            if not sub:
                continue
            w = sum(1 for r in sub if r["r_oracle"] > 0) / len(sub)
            s = summarise(sub, base_col)
            e2["by_regime"][rg] = {"n": len(sub), "winnable_rate": round(w, 4),
                                   **{k: s[k] for k in ("mean_net_r", "net_ci95")}}
            print(f"    {rg:14} n {len(sub):5}  winnable {w:.1%}  "
                  f"net {s['mean_net_r']:+.4f}  CI {s['net_ci95']}")
        base_rate = len(winnable) / len(train)
        print(f"    base winnable rate {base_rate:.1%} — a regime only helps if a "
              f"bucket's rate differs from this by more than its error bar")
        report["E2_entry_filtering_train"] = e2

    OUT.write_text(json.dumps({**report, "rows": rows}, indent=1))
    print(f"\n  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
