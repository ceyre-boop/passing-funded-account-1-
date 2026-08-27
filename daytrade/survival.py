#!/usr/bin/env python3
"""SURVIVAL PLANNER — spec 002.

The pre-trade "risk $X -> worst case $Y" sentence the repo's doctrine
(`I_AM_A_GOOD_TRADER.md`, `ALTA_METHOD.md`) requires before every entry.

Pure arithmetic, no market data, no dependencies, no file writes.

THE DELIVERABLE is one sentence printed before every entry:
    SURVIVAL: risk $140 -> worst case $24,860, cushion $860 left, 1 day back to
    on-track at $300/day goal. Still on track: YES. Verdict: GO (1.0x).

RULES IN PRIORITY ORDER (spec 002, first hit wins): cooloff absolute · a loss
must never break the eval · already at goal today means stop · after a loss
today bet 2 is SMALLER with wider room · else GO.

INVARIANTS, enforced with assertions in __post_init__:
  - size_multiplier <= 1.0 ALWAYS. No code path scales size up. Not after a
    loss, not after a win, not on high confidence. Bet 2 smaller than bet 1 is
    the single rule separating the doctrine from tilt.
  - daily_goal_pct read from config, never computed from recent P&L (there is
    no code path in this module that derives it from anything but the
    Campaign the caller supplies).
  - Regime confidence does NOT enter this calculation, by design — Campaign
    and Proposal carry no confidence/regime field for check() to consume.

AMBIGUITY FLAGGED, NOT RESOLVED HERE (see report): spec 002 computes
`goal = account_size * daily_goal_pct / 100`, which drifts as the account
grows. The doctrine and THE_SHOT.md use a FIXED $300/day. They disagree. This
module implements the written [SPEC] formula exactly, as instructed; picking
between the two is a doctrine decision outside this module's scope.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

# Bet 2 (after a red trade, same day) is fixed at half size, never adjusted
# upward. This is doctrine, not a tuning knob — see spec 002 rule 4.
BET2_MULT = 0.5


def _today() -> date:
    """Seam for tests: wraps date.today() so cooloff logic is mockable
    without touching the system clock."""
    return date.today()


def _require(value, name: str):
    """Fail loud on a missing correctness-critical input. Absence is never
    silently treated as 0 (CLAUDE.md rule 3)."""
    if value is None:
        raise ValueError(f"{name} is required and must not be None")
    return value


@dataclass(frozen=True)
class Campaign:
    account_size: float
    daily_goal_pct: float          # 1.0-2.0 — ONE number per account, forever
    cushion_remaining: float       # eval drawdown room, dollars
    day_pnl_so_far: float
    consecutive_red_days: int
    cooloff_until: Optional[str]   # ISO date string ("YYYY-MM-DD") or None, from streak.json

    def __post_init__(self):
        _require(self.account_size, "account_size")
        _require(self.daily_goal_pct, "daily_goal_pct")
        _require(self.cushion_remaining, "cushion_remaining")
        _require(self.day_pnl_so_far, "day_pnl_so_far")
        _require(self.consecutive_red_days, "consecutive_red_days")

        if self.account_size <= 0:
            raise ValueError(f"account_size must be positive: {self.account_size}")
        if not 1.0 <= self.daily_goal_pct <= 2.0:
            raise ValueError(
                f"daily_goal_pct out of documented range 1.0-2.0: {self.daily_goal_pct}"
            )
        if self.cushion_remaining < 0:
            raise ValueError(f"cushion_remaining cannot be negative: {self.cushion_remaining}")
        if self.consecutive_red_days < 0:
            raise ValueError(
                f"consecutive_red_days cannot be negative: {self.consecutive_red_days}"
            )
        if self.cooloff_until is not None:
            try:
                date.fromisoformat(self.cooloff_until)
            except ValueError as exc:
                raise ValueError(
                    f"cooloff_until must be an ISO date string (YYYY-MM-DD): "
                    f"{self.cooloff_until!r}"
                ) from exc


@dataclass(frozen=True)
class Proposal:
    risk_dollars: float            # what this trade loses if stopped
    target_dollars: float          # what it makes at TP2 (the day goal)

    def __post_init__(self):
        _require(self.risk_dollars, "risk_dollars")
        _require(self.target_dollars, "target_dollars")
        if self.risk_dollars <= 0:
            raise ValueError(f"risk_dollars must be positive: {self.risk_dollars}")
        if self.target_dollars <= 0:
            raise ValueError(f"target_dollars must be positive: {self.target_dollars}")


@dataclass(frozen=True)
class SurvivalCheck:
    verdict: Literal["GO", "SIZE_DOWN", "NO_TRADE"]
    size_multiplier: float         # <= 1.0 ALWAYS. never scales up. ever.
    worst_case_balance: float
    cushion_after_loss: float
    days_to_recover_at_goal: float
    still_on_track: bool
    reason: str                    # one plain sentence, printed before entry

    def __post_init__(self):
        if self.verdict not in ("GO", "SIZE_DOWN", "NO_TRADE"):
            raise ValueError(f"unknown verdict: {self.verdict!r}")
        # The single rule that separates the doctrine from tilt: no code
        # path may scale size up. Enforced here, not just documented.
        if self.size_multiplier > 1.0:
            raise ValueError(
                f"size_multiplier must never exceed 1.0, got {self.size_multiplier}"
            )
        if self.size_multiplier < 0.0:
            raise ValueError(
                f"size_multiplier must not be negative, got {self.size_multiplier}"
            )


def check(campaign: Campaign, proposal: Proposal) -> SurvivalCheck:
    """Pre-trade survival check. Pure function, first-hit-wins priority
    order per spec 002. Never mutates campaign/proposal, never touches
    market data or the filesystem."""
    c, p = campaign, proposal
    goal = c.account_size * c.daily_goal_pct / 100

    # 1. cooloff is absolute — the friction model says the whole ladder
    # rests on it.
    if c.cooloff_until is not None and _today() < date.fromisoformat(c.cooloff_until):
        return _no_trade(c, p, goal, f"cooloff active until {c.cooloff_until}")
    if c.consecutive_red_days >= 2:
        return _no_trade(
            c, p, goal, "two consecutive red days -> 5-day cooloff starts now"
        )

    # 2. a loss must never break the eval
    if p.risk_dollars >= c.cushion_remaining:
        return _no_trade(
            c, p, goal, "a stop-out ends the account; no setup is worth that"
        )
    if p.risk_dollars > 0.5 * c.cushion_remaining:
        mult = 0.5 * c.cushion_remaining / p.risk_dollars
        return _sized(
            c, p, goal, mult,
            "one loss would eat over half the remaining cushion",
        )

    # 3. one shot per day — already green means done (doctrine: take the
    # day, leave)
    if c.day_pnl_so_far >= goal:
        return _no_trade(c, p, goal, "daily goal already banked. the day is won. stop.")

    # 4. after a loss today, bet 2 is SMALLER with wider room (doctrine, not
    # revenge)
    if c.day_pnl_so_far < 0:
        return _sized(
            c, p, goal, BET2_MULT,
            "bet 2: smaller size, wider room, not a recovery bet",
        )

    # 5. clean
    return _sized(c, p, goal, 1.0, "clean setup, full size, on track")


def _metrics(c: Campaign, p: Proposal, goal: float, effective_risk: float):
    """Worst-case metrics for the risk actually being taken (proposal risk
    scaled by whatever size_multiplier the verdict applies). For NO_TRADE,
    effective_risk is 0 — no trade happens, so nothing changes."""
    worst_case_balance = c.account_size + c.day_pnl_so_far - effective_risk
    cushion_after_loss = c.cushion_remaining - effective_risk
    days_to_recover_at_goal = (
        0.0 if effective_risk <= 0 else float(math.ceil(effective_risk / goal))
    )
    still_on_track = cushion_after_loss > 0
    return worst_case_balance, cushion_after_loss, days_to_recover_at_goal, still_on_track


def _no_trade(c: Campaign, p: Proposal, goal: float, reason: str) -> SurvivalCheck:
    wcb, cal, days, on_track = _metrics(c, p, goal, effective_risk=0.0)
    return SurvivalCheck(
        verdict="NO_TRADE",
        size_multiplier=0.0,
        worst_case_balance=wcb,
        cushion_after_loss=cal,
        days_to_recover_at_goal=days,
        still_on_track=on_track,
        reason=reason,
    )


def _sized(c: Campaign, p: Proposal, goal: float, mult: float, reason: str) -> SurvivalCheck:
    verdict: Literal["GO", "SIZE_DOWN"] = "GO" if mult >= 1.0 else "SIZE_DOWN"
    effective_risk = p.risk_dollars * mult
    wcb, cal, days, on_track = _metrics(c, p, goal, effective_risk)
    return SurvivalCheck(
        verdict=verdict,
        size_multiplier=mult,
        worst_case_balance=wcb,
        cushion_after_loss=cal,
        days_to_recover_at_goal=days,
        still_on_track=on_track,
        reason=reason,
    )


def sentence(c: Campaign, p: Proposal, sc: SurvivalCheck) -> str:
    """The actual deliverable: the one printed sentence per spec 002."""
    goal = c.account_size * c.daily_goal_pct / 100
    return (
        f"SURVIVAL: risk ${p.risk_dollars:,.0f} -> worst case "
        f"${sc.worst_case_balance:,.0f}, cushion ${sc.cushion_after_loss:,.0f} left, "
        f"{sc.days_to_recover_at_goal:g} day{'s' if sc.days_to_recover_at_goal != 1 else ''} "
        f"back to on-track at ${goal:,.0f}/day goal. "
        f"Still on track: {'YES' if sc.still_on_track else 'NO'}. "
        f"Verdict: {sc.verdict} ({sc.size_multiplier:g}x)."
    )
