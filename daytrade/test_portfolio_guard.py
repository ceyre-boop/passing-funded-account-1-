"""Card 014 portfolio guards (Gate 7) + the gate's hard operational boundary.

The guard enforces SUPPLIED limits and discovers nothing — a correlation the
caller didn't name does not exist here. Gate 7 also pins the boundary that
already holds in code: no live-broker surface in agent environments.
Fault rows in specs/014_GUARDS_MUTATION_LOG.md (driver: mutation_check_guards.py).
"""
from __future__ import annotations

import pytest

from portfolio_guard import (GuardError, PortfolioLimits, PositionSnapshot,
                             check)

LIMITS = PortfolioLimits(max_total_open_risk_r=3.0,
                         max_per_symbol_exposure_r=1.5,
                         max_correlated_exposure_r=2.0,
                         max_unprotected_count=1,
                         daily_loss_lock_r=2.0,
                         emergency_flatten_r=3.0)


def pos(symbol: str, risk: float, protected: bool = True) -> PositionSnapshot:
    return PositionSnapshot(symbol=symbol, open_risk_r=risk, protected=protected)


def guard_ids(verdict) -> set:
    return {v.guard_id for v in verdict.violations}


# ---------------------------------------------------------------- validation

def test_limits_validate_and_breaker_ordering_is_enforced():
    with pytest.raises(GuardError):
        PortfolioLimits(0.0, 1.5, 2.0, 1, 2.0, 3.0)          # non-positive limit
    with pytest.raises(GuardError):
        PortfolioLimits(3.0, 1.5, 2.0, -1, 2.0, 3.0)         # negative count
    # the 2026-07-01 ratification finding, structural: a breaker at/below the
    # lock makes the lock unreachable
    with pytest.raises(GuardError, match="unreachable"):
        PortfolioLimits(3.0, 1.5, 2.0, 1, 3.0, 2.0)
    with pytest.raises(GuardError):
        pos("NVDA", -0.5)                                    # negative risk


# ------------------------------------------------------------------ guards

def test_clear_book_is_clear():
    v = check(LIMITS, [pos("NVDA", 1.0)], realized_today_r=0.5)
    assert v.violations == () and v.required_action is None and v.why == "clear"


def test_g001_total_open_risk():
    v = check(LIMITS, [pos("NVDA", 1.4), pos("TSLA", 1.4), pos("AMD", 0.4)],
              realized_today_r=0.0)
    assert guard_ids(v) == {"G001"}


def test_g002_per_symbol_exposure():
    v = check(LIMITS, [pos("NVDA", 1.0), pos("NVDA", 0.6)], realized_today_r=0.0)
    assert guard_ids(v) == {"G002"}
    assert "NVDA" in v.violations[0].detail


def test_g003_correlated_exposure_only_for_supplied_groups():
    book = [pos("NVDA", 1.2), pos("AMD", 1.2)]
    # the group is supplied -> enforced
    v = check(LIMITS, book, realized_today_r=0.0,
              correlated_groups={"semis": ["NVDA", "AMD"]})
    assert "G003" in guard_ids(v)
    # the SAME book with no group supplied -> the guard invents nothing,
    # however obvious the correlation looks. Discovery is upstream's job.
    v = check(LIMITS, book, realized_today_r=0.0)
    assert "G003" not in guard_ids(v)


def test_g004_unprotected_count():
    v = check(LIMITS, [pos("NVDA", 0.5, protected=False),
                       pos("TSLA", 0.5, protected=False)], realized_today_r=0.0)
    assert guard_ids(v) == {"G004"}


def test_g005_daily_loss_lock_at_boundary():
    v = check(LIMITS, [], realized_today_r=-2.0)             # inclusive: AT the lock
    assert "G005" in guard_ids(v) and v.required_action == "LOCKOUT"
    v = check(LIMITS, [], realized_today_r=-1.99)
    assert v.required_action is None


def test_g006_emergency_flatten_outranks_lockout():
    v = check(LIMITS, [], realized_today_r=-3.5)
    assert {"G005", "G006"} <= guard_ids(v)                  # both recorded
    assert v.required_action == "FLATTEN_ALL"                # the stronger wins


def test_verdict_is_deterministic_and_complete():
    book = [pos("NVDA", 2.0), pos("NVDA", 0.1, protected=False),
            pos("TSLA", 1.5, protected=False)]
    a = check(LIMITS, book, realized_today_r=-3.5,
              correlated_groups={"semis": ["NVDA"]})
    b = check(LIMITS, book, realized_today_r=-3.5,
              correlated_groups={"semis": ["NVDA"]})
    assert a == b
    assert {"G001", "G002", "G003", "G004", "G005", "G006"} == guard_ids(a)


def test_guard_module_has_no_market_surface():
    """The guard discovers nothing — structurally: it imports no market-data,
    broker, or network module. If this fails, someone taught it to look at
    the market, which is the exact scope creep the card forbids."""
    import sys
    import portfolio_guard  # noqa: F401
    mod = sys.modules["portfolio_guard"]
    forbidden = {"broker", "bars", "requests", "urllib", "yfinance",
                 "polygon_news", "news_claude", "statistics"}
    assert not (set(dir(mod)) & forbidden)
    src = open(mod.__file__).read()
    for name in forbidden:
        assert f"import {name}" not in src, name


# -------------------------------------------- the Gate 7 operational boundary

def test_no_live_broker_surface_in_agent_environment():
    """CLAUDE.md dev rule 9 as a named test: the broker transport refuses any
    non-paper host, so a live order surface does not exist here to misuse."""
    import broker
    assert "paper-api.alpaca.markets" in broker.PAPER_HOST if hasattr(
        broker, "PAPER_HOST") else True
    with pytest.raises(Exception) as e:
        broker._check_host("https://api.alpaca.markets")
    assert "paper" in str(e.value).lower()
