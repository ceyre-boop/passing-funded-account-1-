#!/usr/bin/env python3
"""STOCKFISH CONDITIONAL COMPETENCE — spec 026 (the reframed question).

Not "does Stockfish make money" — it was never meant to trade alone. The
question: GIVEN its vocabulary, does the engine always take the best exit
available, where does it leak R, and how much of the leak would a CORRECT
AlphaZero signal — sent through the existing urgency channel, nothing new —
recover? This turns exit quality into a labeling/logging/grading problem with
unlimited cheap trials, while AlphaZero trains on calendar time in the soak.

Measured per day-type (labeled from the tape with alpha_operator's own outcome
semantics), per asset class, on the TUNE split only:

  shipped_R    what the class's shipped policy realized
  vocab_oracle what the BEST config in the engine's own vocabulary realized
               (perfect per-day config pick — the total leak, upper bound)
  regret       vocab_oracle − shipped: R the engine's vocabulary could reach
               but the fixed policy didn't. THE WEAKNESS MAP.
  az_exit      shipped policy + a perfect 'exit' urgency injected at shock
               onset (first bar with TR > 3x prior-20 median) — what a
               correct AlphaZero EMERGENCY call is worth through the channel
  az_tighten   same with 'tighten' from shock onset — what the operator's
               CURRENT authority (capped at tighten) is worth

az_* uplifts are hindsight counterfactuals — labeled as such, never cited as
expected results (ceiling.py's oracle discipline). Their value is comparative:
cells where az_exit >> az_tighten quantify what the authority cap costs; cells
where regret is large but az_* is small mean the leak needs a POLICY signal
(config choice), not an interrupt — different AlphaZero output, same channel.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions, TUNE_END                         # noqa: E402
from ceiling import find_entry, simulate, wide_space               # noqa: E402
from stockfish_tune import CLASSES                                 # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "competence_report.json"

SYM_CLASS = {s: c for c, syms in CLASSES.items() for s in syms}
SHIPPED = {
    "SINGLE_NAME": {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
                    "flatten_et": None, "hold_past_tp2": True},      # STATIC
    "CASH_INDEX": {"trail_mult": 1.5, "be_arm_frac": 1.0, "partial_frac": 0.5,
                   "flatten_et": None, "hold_past_tp2": True},       # TRAIL_WIDE
    "FUTURES": {"trail_mult": 1.5, "be_arm_frac": 1.0, "partial_frac": 0.5,
                "flatten_et": None, "hold_past_tp2": True},          # TRAIL_WIDE
}
# Day-type labeling: alpha_operator's outcome semantics, applied to the
# entry-forward window (same constants; local so this harness stays
# import-light and deterministic).
FLAT_BAND = 0.0015
SHOCK_TR_MULT = 3.0


def label_day(session, e) -> tuple[str, object]:
    """(scenario, shock_onset_ts|None) from the entry-forward tape."""
    fwd = session.after(e.ts[11:16])
    if len(fwd) < 3:
        return "range_consolidation", None
    prior = session.df[session.df.index < fwd.index[0]]
    tr_prior = float((prior["High"] - prior["Low"]).abs().tail(20).median()) \
        if len(prior) >= 5 else None
    onset = None
    if tr_prior and tr_prior > 0:
        for ts, bar in fwd.iterrows():
            if float(bar["High"] - bar["Low"]) > SHOCK_TR_MULT * tr_prior:
                onset = ts
                break
    start, end = float(fwd["Close"].iloc[0]), float(fwd["Close"].iloc[-1])
    net = (end - start) / start * e.direction        # signed WITH the trade
    if onset is not None:
        return "risk_event", onset
    hi, lo = float(fwd["High"].max()), float(fwd["Low"].min())
    broke = hi > start * (1 + FLAT_BAND) or lo < start * (1 - FLAT_BAND)
    if abs(net) <= FLAT_BAND:
        return ("failed_breakout" if broke else "range_consolidation"), None
    return ("with_trade" if net > 0 else "against_trade"), None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 026 — where does Stockfish leak R?")
    ap.add_argument("--symbols", default=",".join(
        s for syms in CLASSES.values() for s in syms))
    a = ap.parse_args(argv)

    entries = []
    for sym in [s.strip().upper() for s in a.symbols.split(",") if s.strip()]:
        try:
            for s in tune_sessions(load_sessions(sym, "5m", allow_fetch=False)):
                e = find_entry(s)
                if e:
                    entries.append((sym, s, e))
        except BarDataError as ex:
            print(f"  !! {sym}: {ex} — excluded loudly")
    print(f"  {len(entries)} tune entries (<= {TUNE_END})")

    space = wide_space()
    t0 = datetime.now()
    cells: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
    for sym, s, e in entries:
        cls = SYM_CLASS[sym]
        scenario, onset = label_day(s, e)
        shipped_cfg = SHIPPED[cls]

        shipped_r = simulate(s, e, dict(shipped_cfg))
        oracle_r = max(simulate(s, e, dict(cfg)) for cfg in space.values())

        def sched(kind):
            return (lambda ts: kind if onset is not None and ts >= onset else None)
        az_exit_r = (simulate(s, e, dict(shipped_cfg), urgency_schedule=sched("exit"))
                     if onset is not None else shipped_r)
        az_tight_r = (simulate(s, e, dict(shipped_cfg), urgency_schedule=sched("tighten"))
                      if onset is not None else shipped_r)

        c = cells[(cls, scenario)]
        c["shipped"].append(shipped_r)
        c["oracle"].append(oracle_r)
        c["az_exit"].append(az_exit_r)
        c["az_tighten"].append(az_tight_r)

    n_sims = len(entries) * (len(space) + 3)
    dt = (datetime.now() - t0).total_seconds()
    print(f"  {n_sims:,} simulations in {dt:.1f}s\n")

    rows = []
    print(f"  {'class':12s} {'day-type':18s} {'n':>3s} {'shipped':>8s} "
          f"{'oracle':>8s} {'REGRET':>8s} {'az_exit':>8s} {'az_tight':>9s}")
    for (cls, scen), c in sorted(cells.items()):
        n = len(c["shipped"])
        m = {k: sum(v) / n for k, v in c.items()}
        regret = m["oracle"] - m["shipped"]
        rows.append({"class": cls, "scenario": scen, "n": n,
                     "shipped_R": round(m["shipped"], 4),
                     "vocab_oracle_R": round(m["oracle"], 4),
                     "regret_R": round(regret, 4),
                     "az_exit_uplift_R": round(m["az_exit"] - m["shipped"], 4),
                     "az_tighten_uplift_R": round(m["az_tighten"] - m["shipped"], 4)})
        print(f"  {cls:12s} {scen:18s} {n:3d} {m['shipped']:+8.3f} "
              f"{m['oracle']:+8.3f} {regret:+8.3f} "
              f"{m['az_exit'] - m['shipped']:+8.3f} {m['az_tighten'] - m['shipped']:+9.3f}")

    total_regret = sum(r["regret_R"] * r["n"] for r in rows) / len(entries)
    print(f"\n  TOTAL leak (vocab oracle − shipped, hindsight): "
          f"{total_regret:+.3f} R/trade over {len(entries)} entries")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_entries": len(entries), "n_sims": n_sims,
        "oracle_warning": "vocab_oracle and az_* are HINDSIGHT counterfactuals "
                          "— upper bounds, never expected results",
        "total_regret_R_per_trade": round(total_regret, 4),
        "cells": rows,
    }, indent=1))
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
