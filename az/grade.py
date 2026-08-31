#!/usr/bin/env python3
"""az/grade.py — Gate 3a: grade every legal candidate under three exit policies.

Exact, not estimated: the path already happened.

THREE GRADERS ON IDENTICAL GEOMETRY. Only the exit policy varies; entry, stop,
risk, tp1/tp2 come from `az.candidates.geometry` and never change, so R
denominators are comparable and rank correlation between graders is meaningful.

G2 IS ARITHMETIC, AND THAT IS NOT A SHORTCUT. Pure hold-to-close is not
expressible through the engine: `be_arm_frac` has no off value (the constructor
enforces 0 < be_arm_frac <= 1.0) and `profit_lock` arms unconditionally once TP2
is touched. So G2 is computed directly. It is NOT a second implementation of the
decision loop — hold-to-close makes NO decisions, so there is no loop to
duplicate. It is the null exit policy, which is exactly what makes it the right
control for grader exploitation: a candidate that scores well under G1 but poorly
under "just hold it" is exploiting the exit policy, not picking a good entry.

COST IS SEPARABLE, SLIPPAGE IS NOT. `simulate` returns (realized - COST)/risk with
COST applied once, so a cost multiplier is an exact post-hoc adjustment. Slippage
moves the entry and therefore the stop/target levels, changing WHICH bar exits, so
it needs a real re-simulation.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))

from az.candidates import geometry                       # noqa: E402
from ceiling import COST_PER_SHARE, Entry, simulate      # noqa: E402
import frozen_policy as fp                               # noqa: E402

G1 = dict(fp.POLICIES["CASH_INDEX"])                     # the frozen grader, SF-FROZEN-004
G3 = {"partial_frac": 0.0, "trail_mult": None, "be_arm_frac": 1.0,
      "hold_past_tp2": False, "flatten_et": None}        # fixed-R: TP2 or the stop
GRADERS = ("G1_frozen", "G2_hold_to_close", "G3_fixed_r")

# Declared pessimistic parameters (plan). Grounded: COST_PER_SHARE is 0.52 bps on
# SPY's median price, and the median 5-min bar range is 8.7 bps, so 2 bps of
# adverse slippage is about a quarter of a median bar.
COST_MULT, SLIP_BPS = 2.0, 2.0


def hold_to_close_r(session, cand, *, entry_px: float, cost: float) -> float:
    """G2. No decisions, so no decision loop to duplicate. Mirrors `simulate`'s
    own fall-through expression exactly — (realized - cost) / risk, realized taken
    at the session's last close."""
    last = float(session.df["Close"].iloc[-1])
    realized = (last - entry_px) * cand.direction
    return (realized - cost) / cand.entry.risk


def pessimistic_entry_px(cand) -> float:
    """Slippage always against the trade: a long fills higher, a short lower."""
    return cand.entry.entry * (1.0 + cand.direction * SLIP_BPS / 10_000.0)


def shift_entry(cand, px: float) -> Entry:
    """Same declared geometry, different fill. `risk` is k_stop*ATR and does NOT
    move with the fill, which is what keeps R comparable across fills."""
    e, r, d = cand.entry, cand.entry.risk, cand.direction
    return Entry(day=e.day, ts=e.ts, time_block=e.time_block, direction=d,
                 entry=px, stop=px - d * r, risk=r,
                 tp1=px + d * r, tp2=px + d * 2 * r, trail_dist=e.trail_dist)


def grade_all(session, cand) -> dict:
    """Six numbers per candidate: 3 graders x {base, pessimistic}.

    `session` is the real bars.Session — passed through, never reconstructed, so
    its HH:MM memo survives across the ~40 candidates that share it. Rebuilding it
    per call re-renders the index and was profiled at 47% of simulate time.
    """
    base_e = cand.entry
    pess_e = shift_entry(cand, pessimistic_entry_px(cand))
    # cost is exactly separable: simulate returns (realized - COST)/risk with COST
    # applied once, so a multiplier is arithmetic, not a re-simulation.
    extra_cost_r = (COST_PER_SHARE - COST_PER_SHARE * COST_MULT) / base_e.risk
    out = {}
    for label, cfg in (("G1_frozen", G1), ("G3_fixed_r", G3)):
        out[(label, "base")] = simulate(session, base_e, dict(cfg))
        out[(label, "pess")] = simulate(session, pess_e, dict(cfg)) + extra_cost_r
    out[("G2_hold_to_close", "base")] = hold_to_close_r(
        session, cand, entry_px=base_e.entry, cost=COST_PER_SHARE)
    out[("G2_hold_to_close", "pess")] = hold_to_close_r(
        session, cand, entry_px=pess_e.entry, cost=COST_PER_SHARE * COST_MULT)
    return out
