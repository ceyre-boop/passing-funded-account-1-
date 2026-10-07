#!/usr/bin/env python3
"""SURVIVAL PLANNER — spec 002. Plan for the worst before the order exists.

Answers, out loud, before every entry: "if this loses, where am I, is the day
still salvageable, and how many days back to on-track at goal?" It is computed
BEFORE the order so the answer is arithmetic, not a feeling after the loss.

THE DELIVERABLE is one sentence (see `sentence`):
    SURVIVAL: risk $140 -> worst case $24,860, cushion $860 left, 1 day back to
    on-track at $300/day goal. Still on track: YES. Verdict: GO (1.0x).

RULES IN PRIORITY ORDER (spec 002, first hit wins):
  1. cooloff is absolute: an active cooloff, then two consecutive red days
  2. a loss must never break the eval: risk >= cushion is NO_TRADE; risk over
     half the cushion is SIZE_DOWN to exactly half the cushion
  3. already at the daily goal means stop (take the day, leave)
  4. after a loss today, bet 2 is SMALLER (BET2_MULT), never a recovery bet
  5. else GO at 1.0x

WHY IT IS PURE, AND WHY `today` IS AN ARGUMENT
----------------------------------------------
No market data, no network, no I/O, and no clock. The spec's pseudocode calls
`today()`; that would make the verdict depend on when the test happens to run,
and a cooloff boundary bug would only show up on the day it bites. `check`
takes `today` explicitly, so the same inputs always give the same verdict and
the cooloff edge can be tested on any date.

INVARIANTS, enforced by assertion on every return path
------------------------------------------------------
  - size_multiplier <= 1.0, always. No code path scales size up: not after a
    loss, not after a win, not on high confidence. Bet 2 smaller than bet 1 is
    the single rule that separates the doctrine from tilt.
  - Regime confidence is deliberately NOT an input. Survival math is
    independent of how good the setup looks; that is the entire point.
  - `daily_goal_pct` is one number per account, read from config. This module
    accepts no P&L-derived input for it. A goal that moves to catch up is how
    accounts die.

UNAVAILABLE IS NOT ZERO
-----------------------
Inputs are validated in `__post_init__` and nonsense raises `SurvivalError`
(CLAUDE.md rules 3 and 5). A zero risk is rejected too: a trade that loses
nothing has no survival question and is almost certainly a missing stop.

NOT HERE (spec 002 [SKETCH], not now): multi-day recovery pathing and
Kelly-style sizing off measured win rate.

OPEN ITEM FROM THE SPEC: goal = account_size * daily_goal_pct / 100 drifts as
the account grows, while the doctrine uses a fixed $300/day. Implemented as the
spec's formula; `account_size` is whatever the caller passes, so a caller who
wants a fixed goal passes the starting size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

Verdict = Literal["GO", "SIZE_DOWN", "NO_TRADE"]

# Fixed. Bet 2 after a loss is half size. Never adjusted upward.
BET2_MULT = 0.5
GOAL_PCT_MAX = 5.0


class SurvivalError(RuntimeError):
    """Invalid survival input or a broken invariant. Fail loud, never default."""


def _finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SurvivalError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise SurvivalError(f"{name} must be finite, got {value!r}")


@dataclass(frozen=True)
class Campaign:
    account_size: float
    # ONE number per account, from config. Never derived from recent P&L.
    daily_goal_pct: float
    cushion_remaining: float       # eval drawdown room, dollars
    day_pnl_so_far: float
    consecutive_red_days: int
    cooloff_until: Optional[str]   # ISO date from streak.json, or None

    def __post_init__(self) -> None:
        for name in ("account_size", "daily_goal_pct", "cushion_remaining",
                     "day_pnl_so_far"):
            _finite(name, getattr(self, name))
        if self.account_size <= 0:
            raise SurvivalError(f"account_size must be > 0, got {self.account_size}")
        if not (0 < self.daily_goal_pct <= GOAL_PCT_MAX):
            raise SurvivalError(
                f"daily_goal_pct must be in (0, {GOAL_PCT_MAX}], "
                f"got {self.daily_goal_pct}")
        if self.cushion_remaining < 0:
            raise SurvivalError(
                f"cushion_remaining must be >= 0, got {self.cushion_remaining}")
        if (isinstance(self.consecutive_red_days, bool)
                or not isinstance(self.consecutive_red_days, int)
                or self.consecutive_red_days < 0):
            raise SurvivalError(
                "consecutive_red_days must be an int >= 0, "
                f"got {self.consecutive_red_days!r}")
        if self.cooloff_until is not None:
            try:
                date.fromisoformat(self.cooloff_until)
            except (TypeError, ValueError) as exc:
                raise SurvivalError(
                    f"cooloff_until must be an ISO date or None, "
                    f"got {self.cooloff_until!r}") from exc


@dataclass(frozen=True)
class Proposal:
    risk_dollars: float            # what this trade loses if stopped
    target_dollars: float          # what it makes at TP2 (the day goal)

    def __post_init__(self) -> None:
        for name in ("risk_dollars", "target_dollars"):
            _finite(name, getattr(self, name))
        if self.risk_dollars <= 0:
            raise SurvivalError(f"risk_dollars must be > 0, got {self.risk_dollars}")
        if self.target_dollars <= 0:
            raise SurvivalError(
                f"target_dollars must be > 0, got {self.target_dollars}")


@dataclass(frozen=True)
class SurvivalCheck:
    verdict: Verdict
    size_multiplier: float         # <= 1.0 ALWAYS. never scales up. ever.
    worst_case_balance: float
    cushion_after_loss: float
    days_to_recover_at_goal: float
    still_on_track: bool
    reason: str                    # one plain sentence, printed before entry


def _build(c: Campaign, p: Proposal, goal: float, verdict: Verdict,
           mult: float, reason: str) -> SurvivalCheck:
    # NO_TRADE reports the loss of the trade as proposed (what would have been
    # risked); GO / SIZE_DOWN report the loss of the size actually taken.
    loss = p.risk_dollars if verdict == "NO_TRADE" else p.risk_dollars * mult
    cushion_after = c.cushion_remaining - loss
    days = loss / goal
    # On track = the eval survives the loss AND one day at goal earns it back.
    on_track = cushion_after > 0 and days <= 1.0
    out = SurvivalCheck(
        verdict=verdict,
        size_multiplier=mult,
        worst_case_balance=c.account_size - loss,
        cushion_after_loss=cushion_after,
        days_to_recover_at_goal=days,
        still_on_track=on_track,
        reason=reason,
    )
    assert out.size_multiplier <= 1.0, "survival must never scale size up"
    return out


def check(campaign: Campaign, proposal: Proposal, *, today: date) -> SurvivalCheck:
    """Apply the five rules in priority order; first hit wins. Pure."""
    c, p = campaign, proposal
    if not isinstance(today, date):
        raise SurvivalError(f"today must be a date, got {today!r}")
    goal = c.account_size * c.daily_goal_pct / 100

    # 1. cooloff is absolute.
    if c.cooloff_until is not None and today < date.fromisoformat(c.cooloff_until):
        return _build(c, p, goal, "NO_TRADE", 0.0,
                      f"cooloff active until {c.cooloff_until}")
    if c.consecutive_red_days >= 2:
        return _build(c, p, goal, "NO_TRADE", 0.0,
                      "two consecutive red days -> 5-day cooloff starts now")

    # 2. a loss must never break the eval.
    if p.risk_dollars >= c.cushion_remaining:
        return _build(c, p, goal, "NO_TRADE", 0.0,
                      "a stop-out ends the account; no setup is worth that")
    if p.risk_dollars > 0.5 * c.cushion_remaining:
        return _build(c, p, goal, "SIZE_DOWN",
                      0.5 * c.cushion_remaining / p.risk_dollars,
                      "one loss would eat over half the remaining cushion")

    # 3. one shot per day: already green means done.
    if c.day_pnl_so_far >= goal:
        return _build(c, p, goal, "NO_TRADE", 0.0,
                      "daily goal already banked. the day is won. stop.")

    # 4. after a loss today, bet 2 is smaller, not a recovery bet.
    if c.day_pnl_so_far < 0:
        return _build(c, p, goal, "SIZE_DOWN", BET2_MULT,
                      "bet 2: smaller size, wider room, not a recovery bet")

    # 5. clean.
    return _build(c, p, goal, "GO", 1.0,
                  "no rule objects: full size, one shot, take the day and leave")


def _mult_str(mult: float) -> str:
    s = f"{round(mult, 2):g}"
    return f"{s}.0x" if "." not in s else f"{s}x"


def sentence(result: SurvivalCheck, campaign: Campaign,
             proposal: Proposal) -> str:
    """The one-line human deliverable. If a human hesitates, it did its job."""
    goal = campaign.account_size * campaign.daily_goal_pct / 100
    loss = campaign.account_size - result.worst_case_balance
    days = max(1, math.ceil(result.days_to_recover_at_goal))
    unit = "day" if days == 1 else "days"
    return (
        f"SURVIVAL: risk ${loss:,.0f} -> worst case "
        f"${result.worst_case_balance:,.0f}, cushion "
        f"${result.cushion_after_loss:,.0f} left, {days} {unit} back to "
        f"on-track at ${goal:,.0f}/day goal. Still on track: "
        f"{'YES' if result.still_on_track else 'NO'}. "
        f"Verdict: {result.verdict} ({_mult_str(result.size_multiplier)})."
    )
