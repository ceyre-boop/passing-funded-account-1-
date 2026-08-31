#!/usr/bin/env python3
"""az/candidates.py — Gate 2: enumeration and the legality mask. Spec 048/049.

LEGALITY IS A MASK AT GENERATION, NEVER A SCORING PENALTY. The lesson from the
Stockfish side: 65% of transitions had TIGHTEN as an ILLEGAL move, not a
weakly-supported one, and scoring it low rather than masking it corrupted the
comparison. Masked candidates are never graded, never scored, never averaged.

ENTRY GEOMETRY IS A DECLARED RULE. `ceiling.find_entry` is the only thing that
computes geometry and it is welded to the OR-break, so an arbitrary timestamp has
none. The rule below is a substantive choice, not plumbing: a different `k_stop`
is a different study.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))

from az.state import GRID_HHMM, MIN_FORWARD_MIN, StateError, raw_features, truncate_at  # noqa: E402
from ceiling import Entry, time_block  # noqa: E402


class CandidateError(RuntimeError):
    pass


# ---- legality reasons: explicit, quotable, one constant each ----------------
ILLEGAL_TOO_FEW_BARS = ("insufficient bars at or before t to build the state vector "
                        "(needs >=3; 09:30 has exactly 1)")
ILLEGAL_NO_FORWARD_PATH = (f"fewer than {MIN_FORWARD_MIN} minutes of remaining intraday path — "
                           "the frozen grader cannot reach a genuine close")
ILLEGAL_NO_FILL_BAR = "no bar after t to fill against"
ILLEGAL_BAD_STATE = "state vector refused to build (degenerate ATR)"

DIRECTIONS = (1, -1)


@dataclass(frozen=True)
class Candidate:
    symbol: str
    day: object
    hhmm: str
    direction: int
    legal: bool
    reason: str = ""
    entry: object = None          # ceiling.Entry when legal
    raw: dict = None              # state features when legal


def geometry(*, symbol, day, hhmm, direction, hist, next_open, atr14, k_stop):
    """The DECLARED entry geometry (spec 048 addendum / plan).

    fill  = next bar's open after t          (realistic; never the close at t)
    stop  = entry - direction * k_stop * ATR14(t)
    risk  = k_stop * ATR14(t)
    tp1/2 = 1R / 2R
    trail = 1 * ATR14(t)

    Every term is as-of computable at t. k_stop is REQUIRED — no default, because
    a default here is an untested empirical claim about how wide a stop should be.
    """
    if k_stop is None:
        raise CandidateError("k_stop is required — the stop width is a declared "
                             "parameter, not a default")
    risk = k_stop * atr14
    if not (risk > 0):
        raise CandidateError(f"risk={risk!r} from atr14={atr14!r}")
    entry_px = float(next_open)
    stop = entry_px - direction * risk
    return Entry(day=str(day), ts=f"{day}T{hhmm}:00", time_block=time_block(hhmm),
                 direction=direction, entry=entry_px, stop=stop, risk=risk,
                 tp1=entry_px + direction * risk, tp2=entry_px + direction * 2 * risk,
                 trail_dist=atr14)


def enumerate_day(symbol, day, chunk: pd.DataFrame, prior_close, *, k_stop):
    """Every (timestamp x direction) slot for one session, each legal or masked
    WITH a reason. Slots are always emitted so the grid stays aligned across days
    — that alignment is what makes the day-shift null exact (spec 049 §2.1)."""
    idx = chunk.index.strftime("%H:%M")
    out = []
    for hhmm in GRID_HHMM:
        hist = truncate_at(chunk, hhmm)
        after = chunk[idx > hhmm]
        reason, raw, nxt, atr = "", None, None, None
        if len(hist) < 3:
            reason = ILLEGAL_TOO_FEW_BARS
        elif len(after) == 0:
            reason = ILLEGAL_NO_FILL_BAR
        elif len(after) * 5 < MIN_FORWARD_MIN:
            reason = ILLEGAL_NO_FORWARD_PATH
        else:
            try:
                raw = raw_features(hist, prior_close)
                atr = raw["vol"] * float(hist["Close"].iloc[-1])
                nxt = float(after["Open"].iloc[0])
            except StateError:
                reason = ILLEGAL_BAD_STATE
        for d in DIRECTIONS:
            if reason:
                out.append(Candidate(symbol, day, hhmm, d, False, reason))
            else:
                out.append(Candidate(symbol, day, hhmm, d, True, "",
                                     geometry(symbol=symbol, day=day, hhmm=hhmm, direction=d,
                                              hist=hist, next_open=nxt, atr14=atr, k_stop=k_stop),
                                     raw))
    return out
