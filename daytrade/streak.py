#!/usr/bin/env python3
"""STREAK TRACKER — spec 004, second half. `data/streak.json`.

THE ONE RULE THAT CARRIES THE LADDER: two consecutive red days starts a 5
TRADING-day cooloff, enforced here and honoured by the runner (and by
survival.py's `Campaign.cooloff_until` check) via `is_armed` / `blocks_trading`.
friction_ladder.py showed the entire campaign's 93-98% pass probability
depends on the cooloff being real. It is not advice.

COOLOFF SEMANTICS — matched to scripts/friction_ladder.py exactly:
    COOLOFF_AFTER = 2   # consecutive red days that trigger it
    COOLOFF_DAYS  = 5   # length of the cooloff, in TRADING days (not calendar)

friction_ladder's inner loop decrements its `cool` counter once per
trading-day tick (`td`), never per calendar day, and a green day inside the
window does NOT clear it early — only the countdown reaching zero does
(`cool > 0: cool -= 1; continue`, independent of `consec`). This module
mirrors that: the cooloff, once triggered, only ever lengthens (a fresh
2-red-day event while already in cooloff extends it) or expires on its own
schedule — it is never shortened by a later green day.

`cooloff_until` has to be a checkable calendar date the RUNNER can compare
against *before* the next session starts (that is the entire point of making
it "a real, checkable value, not advice") — it can't be a countdown that only
advances retroactively as more `update()` calls arrive. So at the moment the
cooloff triggers, COOLOFF_DAYS trading days are converted to a calendar date
by walking forward COOLOFF_DAYS Mon-Fri days from the triggering day_result's
date. This repo has no market-holiday calendar anywhere else (regime.py,
survival.py, friction_ladder.py all use the same 5-trading-days-per-week
approximation) — this is not a new assumption, just the existing one applied
here. If a market holiday falls inside a live cooloff window, `cooloff_until`
will land one trading day early; fix centrally (a shared trading-calendar
module) if that gap ever matters in practice.

THE CONTRACT (spec 004, verbatim):
    StreakState(green_streak, longest_streak, days_since_red, rolling_20d_winrate,
                cumulative_R, day_pnl, distance_to_goal, consecutive_red_days,
                campaign_cushion, cooloff_until)
    update(day_result) -> None       # called at every session close
    -> data/streak.json

`day_result` contract (this module's addition — spec 004 specifies the output
StreakState but not the input shape). Every field below is REQUIRED. None is
ever defaulted to 0 when missing (CLAUDE.md rule 3) — a missing field is a
`DayResultError`, on purpose:
    date: str                  "YYYY-MM-DD"
    outcome: str                one of "green", "red", "flat"
                                 ("flat" = no trade taken / scratch day — it
                                 does not advance or reset green_streak,
                                 days_since_red, consecutive_red_days, the
                                 cooloff, or the rolling winrate. It is a
                                 neutral day, not a loss.)
    pnl: float                  the day's realized $ P&L (0.0 for flat is a
                                 real, explicit value)
    result_R: float              the day's R-multiple (0.0 for flat)
    distance_to_goal: float      pass-through from the caller (survival.py
                                 owns the campaign-goal math; this module just
                                 carries the latest value in StreakState)
    campaign_cushion: float      pass-through from the caller, same reason
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Optional, Union

COOLOFF_AFTER = 2   # consecutive red days that trigger the cooloff
COOLOFF_DAYS = 5    # length of the cooloff, in TRADING (Mon-Fri) days

VALID_OUTCOMES = ("green", "red", "flat")
REQUIRED_FIELDS = ("date", "outcome", "pnl", "result_R", "distance_to_goal",
                    "campaign_cushion")

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "streak.json"

_ROLLING_WINDOW = 20  # trading (non-flat) days for rolling_20d_winrate


class DayResultError(ValueError):
    """`day_result` failed validation. Raised, never silently coerced."""


@dataclass
class StreakState:
    green_streak: int = 0
    longest_streak: int = 0
    days_since_red: int = 0
    rolling_20d_winrate: float = 0.0
    cumulative_R: float = 0.0
    day_pnl: float = 0.0
    distance_to_goal: float = 0.0
    consecutive_red_days: int = 0
    campaign_cushion: float = 0.0
    cooloff_until: Optional[str] = None
    # Bookkeeping needed to compute rolling_20d_winrate honestly across
    # restarts. Not in spec 004's field list verbatim, but it has to live
    # somewhere durable, and this is the one file the spec names — keeping
    # it out of the persisted state would mean recomputing history from a
    # source this module doesn't own.
    recent_outcomes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StreakState":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ------------------------------------------------------------- validation

def _validate_day_result(day_result: dict) -> None:
    if not isinstance(day_result, dict):
        raise DayResultError(f"day_result must be a dict, got {type(day_result)!r}")
    missing = [f for f in REQUIRED_FIELDS if f not in day_result]
    if missing:
        raise DayResultError(
            f"day_result missing required field(s) {missing} — refusing to "
            "default any of them to 0 (CLAUDE.md rule 3)")
    if day_result["outcome"] not in VALID_OUTCOMES:
        raise DayResultError(
            f"outcome must be one of {VALID_OUTCOMES}, got {day_result['outcome']!r}")
    try:
        _date.fromisoformat(day_result["date"])
    except (TypeError, ValueError) as e:
        raise DayResultError(f"date must be YYYY-MM-DD, got {day_result['date']!r}") from e


# ------------------------------------------------------------- trading-day math

def _advance_trading_days(start: _date, n: int) -> _date:
    """Walk forward `n` Mon-Fri days from `start` (weekends skipped), return
    the date of the nth one. See module docstring for the holiday caveat."""
    d = start
    counted = 0
    while counted < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:  # Mon=0 .. Fri=4
            counted += 1
    return d


def _as_date(on: Union[str, _date]) -> _date:
    return on if isinstance(on, _date) else _date.fromisoformat(on)


# ------------------------------------------------------------- runner predicates

def is_armed(state: StreakState, on: Union[str, _date]) -> bool:
    """True if the campaign is clear to trade on date `on` — i.e. no active
    cooloff. This is the checkable value the runner must call; a
    `cooloff_until` field nobody acts on is what this replaces."""
    if state.cooloff_until is None:
        return True
    return _as_date(on) >= _as_date(state.cooloff_until)


def blocks_trading(state: StreakState, on: Union[str, _date]) -> bool:
    """Inverse of `is_armed` — named for call sites that read better as a
    guard ('if blocks_trading(...): refuse')."""
    return not is_armed(state, on)


# ------------------------------------------------------------- persistence

def load_state(path: Path = STATE_PATH) -> StreakState:
    if not path.exists():
        return StreakState()
    with path.open() as fh:
        raw = json.load(fh)
    return StreakState.from_dict(raw)


def save_state(state: StreakState, path: Path = STATE_PATH) -> None:
    """Write-temp-then-rename, flush+fsync before the rename — same
    durability discipline as `alpha_operator._append_jsonl`. A crash mid-write
    must leave the previous, valid `streak.json` in place, never a half-written
    one, because the runner reads this file to decide whether it's allowed to
    trade."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                                     suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state.to_dict(), fh, sort_keys=True, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


# ------------------------------------------------------------- the update

def update(day_result: dict, path: Path = STATE_PATH) -> StreakState:
    """Apply one session-close result to persisted streak state and write it
    back to `path` (default `data/streak.json`). Returns the new state."""
    _validate_day_result(day_result)
    state = load_state(path)

    today = _date.fromisoformat(day_result["date"])
    outcome = day_result["outcome"]

    # Pass-throughs / running totals — updated regardless of outcome.
    state.day_pnl = day_result["pnl"]
    state.cumulative_R += day_result["result_R"]
    state.distance_to_goal = day_result["distance_to_goal"]
    state.campaign_cushion = day_result["campaign_cushion"]

    if outcome == "green":
        state.green_streak += 1
        state.longest_streak = max(state.longest_streak, state.green_streak)
        state.consecutive_red_days = 0
        state.days_since_red += 1
        state.recent_outcomes.append("green")
    elif outcome == "red":
        state.green_streak = 0
        state.consecutive_red_days += 1
        state.days_since_red = 0
        state.recent_outcomes.append("red")
        if state.consecutive_red_days >= COOLOFF_AFTER:
            candidate = _advance_trading_days(today, COOLOFF_DAYS).isoformat()
            # Cooloff only ever lengthens — a fresh trigger while one is
            # already active extends it, it never shortens or clears early.
            if state.cooloff_until is None or candidate > state.cooloff_until:
                state.cooloff_until = candidate
            # Matches friction_ladder.py: `consec` resets to 0 the instant
            # the cooloff fires, so it takes two FRESH consecutive reds
            # after the cooloff lifts to trigger it again.
            state.consecutive_red_days = 0
    else:  # "flat" — neutral, touches nothing but the pass-throughs above
        pass

    state.recent_outcomes = state.recent_outcomes[-_ROLLING_WINDOW:]
    traded = len(state.recent_outcomes)
    wins = state.recent_outcomes.count("green")
    state.rolling_20d_winrate = (wins / traded) if traded else 0.0

    save_state(state, path)
    return state
