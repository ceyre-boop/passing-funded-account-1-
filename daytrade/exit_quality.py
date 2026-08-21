#!/usr/bin/env python3
"""EXIT QUALITY — how good are Stockfish's exits, judged by hindsight?

The question the dashboard needs answered: for every session it actually
traded, how much of the achievable move did the shipped exit policy keep?
Three reference points per session, all computed on the same entry:

  realized   what the shipped policy got
  mfe        the best the position was EVER worth (max favorable excursion)
  oracle     the best any config in the engine's own vocabulary would have got

  efficiency = realized / oracle    (share of the reachable result kept)
  giveback   = mfe - realized       (favorable excursion handed back)

MFE and ORACLE ARE HINDSIGHT and unachievable — labeled as such everywhere,
per ceiling.py's standing rule that no component may cite an oracle as an
expected result. They are the yardstick, never the target.

Tune split only. Read-only. Writes one report the dashboard reads.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions                                   # noqa: E402
from ceiling import find_entry, simulate, wide_space, COST_PER_SHARE  # noqa: E402
from stockfish_tune import CLASSES                                 # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "exit_quality.json"
# The comparison target is the PINNED checkpoint (SF-FROZEN-001), never an
# inline dict — stack exam LL-2. If the engine or the pinned params drift,
# frozen_policy.policy() refuses rather than quietly re-baselining.
import frozen_policy


def SHIPPED_FOR(cls: str) -> dict:
    return frozen_policy.policy(cls)


def mfe_r(session, e) -> float:
    """Max favorable excursion in R — the best the position was ever worth,
    net of the same round-trip cost the simulator charges."""
    fwd = session.after(e.ts[11:16])
    if not len(fwd):
        return 0.0
    best = float(fwd["High"].max() if e.direction > 0 else fwd["Low"].min())
    return ((best - e.entry) * e.direction - COST_PER_SHARE) / e.risk


def main() -> int:
    sym_class = {s: c for c, syms in CLASSES.items() for s in syms}
    grid = list(wide_space().values())
    rows = []
    for sym in [s for v in CLASSES.values() for s in v]:
        try:
            sessions = tune_sessions(load_sessions(sym, "5m", allow_fetch=False))
        except BarDataError as ex:
            print(f"  !! {sym}: {ex} — excluded loudly")
            continue
        cls = sym_class[sym]
        for sess in sessions:
            e = find_entry(sess)
            if e is None:
                continue
            realized = simulate(sess, e, SHIPPED_FOR(cls))
            oracle = max(simulate(sess, e, dict(c)) for c in grid)
            mfe = mfe_r(sess, e)
            rows.append({
                "symbol": sym, "class": cls, "session": str(sess.day),
                "direction": "long" if e.direction > 0 else "short",
                "realized_r": round(realized, 4),
                "oracle_r": round(oracle, 4), "mfe_r": round(mfe, 4),
                "efficiency": (round(realized / oracle, 4)
                               if oracle > 0 else None),
                "giveback_r": round(max(mfe - realized, 0.0), 4)})

    if not rows:
        print("  no sessions with an entry — nothing to score")
        return 1

    eff = [r["efficiency"] for r in rows if r["efficiency"] is not None]
    give = [r["giveback_r"] for r in rows]
    winners = [r for r in rows if r["realized_r"] > 0]
    # A session where NO config could profit is an ENTRY failure, not an exit
    # failure — separating them is the whole point of this report.
    unwinnable = [r for r in rows if r["oracle_r"] <= 0]

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compared_against": frozen_policy.CHECKPOINT_ID,
        "hindsight_warning": "oracle_r and mfe_r use perfect hindsight and are "
                             "NOT achievable; they are the yardstick, never a target",
        "n_sessions": len(rows),
        "median_efficiency": round(statistics.median(eff), 4) if eff else None,
        "mean_efficiency": round(sum(eff) / len(eff), 4) if eff else None,
        "median_giveback_r": round(statistics.median(give), 4),
        "mean_realized_r": round(sum(r["realized_r"] for r in rows) / len(rows), 4),
        "mean_oracle_r": round(sum(r["oracle_r"] for r in rows) / len(rows), 4),
        "mean_mfe_r": round(sum(r["mfe_r"] for r in rows) / len(rows), 4),
        "n_winners": len(winners),
        "n_unwinnable_entries": len(unwinnable),
        "pct_unwinnable": round(100 * len(unwinnable) / len(rows), 1),
        "by_class": {},
        # ALL rows: the residual model joins on these, and a
        # truncated report silently starved it to 10 samples.
        "rows": rows,
    }
    for cls in CLASSES:
        sub = [r for r in rows if r["class"] == cls]
        if not sub:
            continue
        e2 = [r["efficiency"] for r in sub if r["efficiency"] is not None]
        summary["by_class"][cls] = {
            "n": len(sub),
            "median_efficiency": round(statistics.median(e2), 4) if e2 else None,
            "median_giveback_r": round(statistics.median(
                [r["giveback_r"] for r in sub]), 4),
            "mean_realized_r": round(sum(r["realized_r"] for r in sub) / len(sub), 4),
            "pct_unwinnable": round(100 * sum(1 for r in sub if r["oracle_r"] <= 0)
                                    / len(sub), 1)}

    OUT.write_text(json.dumps(summary, indent=1))
    print(f"  {len(rows)} scored sessions")
    print(f"  median exit efficiency   {summary['median_efficiency']}")
    print(f"  median giveback          {summary['median_giveback_r']} R")
    print(f"  mean realized / oracle   {summary['mean_realized_r']:+.3f} / "
          f"{summary['mean_oracle_r']:+.3f} R  (mfe {summary['mean_mfe_r']:+.3f})")
    print(f"  entries no config could win: {summary['pct_unwinnable']}%")
    for cls, d in summary["by_class"].items():
        print(f"    {cls:12s} n {d['n']:3d}  eff {d['median_efficiency']}  "
              f"giveback {d['median_giveback_r']}  unwinnable {d['pct_unwinnable']}%")
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
