#!/usr/bin/env python3
"""scripts/backfill_disengagement.py — build the ODD §5 log from a T_SIM run.

WHAT IT PRODUCES
    decisions.jsonl   one row per directive (the ENTRY arm) plus a
                      RATE-MATCHED sample of declined bars (the CONTROL arm).
                      Contains only what was knowable at the decision.
    outcomes.jsonl    what happened next. Written HERE and read by nothing the
                      adjudicator touches.

WHY A RATE-MATCHED CONTROL
    MECH-006 -- the one hypothesis about the entry layer still standing -- says
    "the entry veto carries information a rate-matched coin does not: the days
    it refuses are worse than average, not merely fewer." Comparing 39 entries
    against all 1,521 refusals compares two different things. The control draws
    declined bars at the SAME rate entries were taken, stratified across
    sessions so it is not clustered in the quietest day.

NO R, NO P&L
    Outcomes are completions language plus magnitude. `forward_range_atr` is
    realized range over the following window normalized by ATR at the decision.
    Magnitude is the one quantity this repo has measured as detectable, and it
    is not a return.

THE BACKFILL IS DETERMINISTIC
    Same run, same rows. It re-runs the same 20 sessions the committed run used
    and refuses to write if the directive count does not match what that run
    reported -- a backfill that silently describes a different run is worse
    than none.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from body.disengagement import (  # noqa: E402
    AGREED, ARM_CONTROL, ARM_ENTRY, DECISIONS, DISAGREED, ENGINE_ALPHAZERO,
    ENGINE_STOCKFISH, DecisionRow, append_jsonl, rate_matched_control)
from body.disengagement_outcomes import (  # noqa: E402
    EXIT_NOT_TAKEN, EXIT_SESSION_CLOSE, EXIT_STOP, EXIT_TARGET, OUTCOMES,
    OutcomeRow)
from body.entry_policy import ATR_LOOKBACK, VetoEntryPolicy  # noqa: E402

FORWARD_BARS = 12          # window for forward magnitude, declared
EXPECTED_DIRECTIVES = 39   # what 07fbe8f's run reported


def _runner():
    spec = importlib.util.spec_from_file_location("ssr", ROOT / "scripts" / "sim_session_run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def forward_range_atr(chunk, i: int) -> float:
    """Realized range over the next FORWARD_BARS bars ÷ ATR at the decision.
    Magnitude, not return. Uses only bars AFTER i, which is why it lives in the
    OUTCOME file and not the decision row."""
    fwd = chunk.iloc[i + 1: i + 1 + FORWARD_BARS]
    if len(fwd) < 2:
        return 0.0
    hist = chunk.iloc[max(0, i - ATR_LOOKBACK + 1): i + 1]
    atr = float((hist["High"] - hist["Low"]).mean())
    if not (atr > 0):
        return 0.0
    return float((fwd["High"].max() - fwd["Low"].min()) / atr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="backfill the ODD §5 log")
    ap.add_argument("--sessions", type=int, default=20)
    a = ap.parse_args(argv)

    if DECISIONS.exists() or OUTCOMES.exists():
        print("REFUSING: log files already exist. These are append-only audit "
              "logs; delete them deliberately if you mean to rebuild.")
        return 1

    m = _runner()
    att, _ = m.attest(VetoEntryPolicy)
    sessions = m.load_sessions(a.sessions)
    print(f"backfilling {len(sessions)} sessions "
          f"({sessions[0][0]} .. {sessions[-1][0]})")

    ledgers_by_session, chunks, rows, outs = {}, {}, [], []
    n_entries = 0
    for day, chunk in sessions:
        r = m.run_session(day, chunk, att, VetoEntryPolicy)
        ledgers_by_session[day] = r["ledgers"]
        chunks[day] = chunk
        n_entries += r["published"]
        # engine agreement: AZ published, did SF authorize all of them?
        agreement = AGREED if r["strategy_refused"] == 0 else DISAGREED
        for idx, led in enumerate(r["ledgers"]):
            if not led["fired"]:
                continue
            rid = f"{day}-{led['ts_event']}-E"
            rows.append(DecisionRow(
                row_id=rid, date=day, tier_at_time="T_SIM",
                engine=ENGINE_ALPHAZERO, engine_agreement=agreement,
                arm=ARM_ENTRY,
                what_system_did_or_wanted=(
                    "AlphaZero published an entry directive; Stockfish "
                    + ("authorized and executed it" if agreement == AGREED
                       else "refused it")),
                decision_ts=led["ts_event"],
                state={"eagerness": led["eagerness"], "vetoes": led["vetoes"],
                       "margin": led["margin"], "for": led["for_reasons"],
                       "against": led["against_reasons"], "bar_index": idx}))
            outs.append(OutcomeRow(
                row_id=rid, filled=True,
                exit_kind=EXIT_SESSION_CLOSE if idx + FORWARD_BARS >= len(chunk)
                else (EXIT_TARGET if forward_range_atr(chunk, idx) > 2.0 else EXIT_STOP),
                bars_held=min(FORWARD_BARS, len(chunk) - idx - 1),
                forward_range_atr=forward_range_atr(chunk, idx),
                orphaned=False))

    if n_entries != EXPECTED_DIRECTIVES:
        print(f"REFUSING: this run produced {n_entries} directives, the committed "
              f"run reported {EXPECTED_DIRECTIVES}. A backfill that silently "
              "describes a different run is worse than none.")
        return 2

    for day, led in rate_matched_control(ledgers_by_session, n_entries):
        rid = f"{day}-{led['ts_event']}-C"
        idx = led.get("bar_index")
        if idx is None:
            idx = ledgers_by_session[day].index(led)
        rows.append(DecisionRow(
            row_id=rid, date=day, tier_at_time="T_SIM",
            engine=ENGINE_ALPHAZERO, engine_agreement=AGREED,
            arm=ARM_CONTROL,
            what_system_did_or_wanted=(
                "AlphaZero declined to publish; no directive was issued and "
                "Stockfish was never consulted"),
            decision_ts=led["ts_event"],
            state={"eagerness": led["eagerness"], "vetoes": led["vetoes"],
                   "margin": led["margin"], "for": led["for_reasons"],
                   "against": led["against_reasons"], "bar_index": idx}))
        outs.append(OutcomeRow(
            row_id=rid, filled=False, exit_kind=EXIT_NOT_TAKEN, bars_held=0,
            forward_range_atr=forward_range_atr(chunks[day], idx),
            orphaned=False))

    for r_ in rows:
        append_jsonl(DECISIONS, r_.to_dict())
    for o in outs:
        append_jsonl(OUTCOMES, o.to_dict())

    n_e = sum(1 for r_ in rows if r_.arm == ARM_ENTRY)
    n_c = len(rows) - n_e
    print(f"\n  decisions.jsonl  {len(rows)} rows   ENTRY {n_e} · CONTROL {n_c}")
    print(f"  outcomes.jsonl   {len(outs)} rows   (sealed — the adjudicator "
          "cannot import this path)")
    print(f"  judgments.jsonl  0 rows            (yours to write)")
    print(f"\n  rate match: {n_c} controls against {n_e} entries — "
          f"{'matched' if n_c == n_e else 'NOT MATCHED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
