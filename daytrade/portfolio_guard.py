#!/usr/bin/env python3
"""PORTFOLIO GUARDS — spec 014 (third product, deliberately last: Gate 7).

Enforces limits SUPPLIED by the upstream risk layer. This module discovers
nothing: it computes no correlations, fetches no market data (it imports no
market module — tested), and invents no exposure judgments. Like the
constitution, it is a predicate — it can flag and demand, never size or order.

Guard ids are stable forever (they will appear in logs and session records):

  G001 total open risk          G004 unprotected position count
  G002 per-symbol exposure      G005 daily loss lock      -> LOCKOUT
  G003 correlated exposure      G006 emergency flatten    -> FLATTEN_ALL

FLATTEN_ALL outranks LOCKOUT. Malformed inputs raise — a guard that guesses
about a malformed limit is a guard in name only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional

GUARDS: dict[str, str] = {
    "G001": "total open risk over limit",
    "G002": "per-symbol exposure over limit",
    "G003": "correlated exposure over limit",
    "G004": "too many unprotected positions",
    "G005": "daily loss lock",
    "G006": "emergency flatten",
}


class GuardError(RuntimeError):
    """Malformed limits or positions. Never guessed around."""


@dataclass(frozen=True)
class PortfolioLimits:
    """Supplied by upstream. The guard consumes these; it never derives them."""
    max_total_open_risk_r: float
    max_per_symbol_exposure_r: float
    max_correlated_exposure_r: float
    max_unprotected_count: int
    daily_loss_lock_r: float          # at/beyond this loss: no NEW risk
    emergency_flatten_r: float        # at/beyond this loss: everything closes

    def __post_init__(self):
        for f_ in ("max_total_open_risk_r", "max_per_symbol_exposure_r",
                   "max_correlated_exposure_r", "daily_loss_lock_r",
                   "emergency_flatten_r"):
            v = getattr(self, f_)
            # isfinite FIRST: every guard comparison against NaN is False, so a
            # single NaN limit silently disarms the entire risk backstop
            # (adversarial review finding 3).
            if not math.isfinite(v) or v <= 0:
                raise GuardError(f"{f_} must be a positive finite number, got {v}")
        if self.max_unprotected_count < 0:
            raise GuardError("max_unprotected_count must be >= 0")
        if self.emergency_flatten_r <= self.daily_loss_lock_r:
            raise GuardError(
                "emergency_flatten_r must sit BEYOND daily_loss_lock_r — a "
                "breaker that fires before the lock makes the lock unreachable "
                "(the exact mis-ordering the 2026-07-01 ratification flagged)")


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    open_risk_r: float                # distance to stop, in R — supplied
    protected: bool                   # stop at/beyond breakeven

    def __post_init__(self):
        if not self.symbol:
            raise GuardError("a position needs a symbol")
        if not math.isfinite(self.open_risk_r) or self.open_risk_r < 0:
            raise GuardError(f"{self.symbol}: open risk {self.open_risk_r} is "
                             "not a fact — it must be finite and non-negative")


@dataclass(frozen=True)
class Violation:
    guard_id: str
    guard: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GuardVerdict:
    violations: tuple
    required_action: Optional[str]    # None | 'LOCKOUT' | 'FLATTEN_ALL'
    why: str


def check(limits: PortfolioLimits, positions: list, *,
          realized_today_r: float,
          correlated_groups: dict | None = None) -> GuardVerdict:
    """Pure predicate. `correlated_groups` ({name: [symbols]}) comes from
    upstream — a group the caller did not supply DOES NOT EXIST here, however
    obvious the correlation might look to a human. Discovering it is the
    research factory's job, with evidence; enforcing it is ours."""
    correlated_groups = correlated_groups or {}
    if not math.isfinite(realized_today_r):
        raise GuardError(f"realized_today_r={realized_today_r} is not a fact — "
                         "a NaN day would sail past both breakers unnoticed")
    v: list[Violation] = []

    total = sum(p.open_risk_r for p in positions)
    if total > limits.max_total_open_risk_r:
        v.append(Violation("G001", GUARDS["G001"],
                 f"open risk {total:g}R > limit {limits.max_total_open_risk_r:g}R"))

    by_symbol: dict[str, float] = {}
    for p in positions:
        by_symbol[p.symbol] = by_symbol.get(p.symbol, 0.0) + p.open_risk_r
    for sym, r in sorted(by_symbol.items()):
        if r > limits.max_per_symbol_exposure_r:
            v.append(Violation("G002", GUARDS["G002"],
                     f"{sym} {r:g}R > limit {limits.max_per_symbol_exposure_r:g}R"))

    for name, symbols in sorted(correlated_groups.items()):
        r = sum(by_symbol.get(s, 0.0) for s in symbols)
        if r > limits.max_correlated_exposure_r:
            v.append(Violation("G003", GUARDS["G003"],
                     f"group {name!r} ({sorted(symbols)}) {r:g}R > limit "
                     f"{limits.max_correlated_exposure_r:g}R"))

    unprotected = sum(1 for p in positions if not p.protected)
    if unprotected > limits.max_unprotected_count:
        v.append(Violation("G004", GUARDS["G004"],
                 f"{unprotected} unprotected > limit {limits.max_unprotected_count}"))

    action = None
    if realized_today_r <= -limits.daily_loss_lock_r:
        v.append(Violation("G005", GUARDS["G005"],
                 f"day at {realized_today_r:g}R, lock at "
                 f"-{limits.daily_loss_lock_r:g}R — no new risk today"))
        action = "LOCKOUT"
    if realized_today_r <= -limits.emergency_flatten_r:
        v.append(Violation("G006", GUARDS["G006"],
                 f"day at {realized_today_r:g}R, breaker at "
                 f"-{limits.emergency_flatten_r:g}R — everything closes"))
        action = "FLATTEN_ALL"        # outranks LOCKOUT

    return GuardVerdict(
        violations=tuple(v), required_action=action,
        why=("clear" if not v else "; ".join(f"[{x.guard_id}] {x.detail}" for x in v)))


if __name__ == "__main__":
    print(__doc__)
    print("run the suite: pytest daytrade/test_portfolio_guard.py -v")
