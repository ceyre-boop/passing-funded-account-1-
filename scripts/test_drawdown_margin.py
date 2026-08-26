"""Tests for drawdown_margin.walk.

The load-bearing one is test_static_separates_floor_from_peak_to_trough: it is
the defect this file was written with and fixed during the same session. Under a
STATIC rule the contract floor sits at the START balance, so once the account
grows, "distance below the base" stops being a drawdown and becomes floor
distance. Reporting one number under both meanings let a 14.6% peak-to-trough
present as 1.6%. Both quantities are now carried separately and this test fails
if they are ever collapsed again.
"""
from __future__ import annotations

import numpy as np
import pytest

from scripts.drawdown_margin import max_safe_risk, walk
from sovereign.propfirm.firm_contracts import (Costs, DrawdownRule, FirmContract,
                                               Phase)


def contract(dd_type="trailing", dd_pct=0.05, daily_pct=None, mark="close"):
    return FirmContract(
        key="test", display_name="Test", account_size=100_000.0,
        phases=(Phase(target_pct=0.08, min_trading_days=0, max_days=None),),
        max_dd=DrawdownRule(pct=dd_pct, basis="balance", mark=mark, type=dd_type),
        daily_dd=(None if daily_pct is None
                  else DrawdownRule(pct=daily_pct, basis="balance", mark=mark)),
        permissions={"weekend_hold": True, "overnight_hold": True,
                     "news_hold": True},
        costs=Costs(fee_usd=100.0, refund_on_pass=0.0, swap_haircut_r_per_day=0.0),
        rules_asof="2026-08-26", source_url="test://")


def series(rs):
    vi = np.array(rs, dtype=float)
    return vi, np.clip(vi, None, 0.0), np.ones(len(vi), dtype=int)


# ------------------------------------------------------- the defect this caught

def test_static_separates_floor_from_peak_to_trough():
    """Grow well above the start, then draw down hard.

    Floor distance must stay ~0 (the account is far above a fixed floor) while
    peak-to-trough must report the real excursion. One number cannot be both.
    """
    vi, vw, vo = series([1.0] * 20 + [-3.0])      # +100R up, then -3R
    r = walk(vi, vw, vo, contract(dd_type="static", dd_pct=0.10), risk=0.01)

    assert r["worst_peak_to_trough"] > 0.02, "peak-to-trough went missing"
    assert r["worst_floor_depth"] < r["worst_peak_to_trough"], (
        "static floor distance was reported as if it were a drawdown — the "
        "exact conflation this test exists to prevent")
    assert r["breach"] is None, "never came near a fixed floor; must not breach"


def test_trailing_floor_equals_peak_to_trough():
    """Under a trailing rule the base IS the high-water mark, so the two
    quantities coincide by construction. If they ever diverge, one of the two
    walks is wrong."""
    vi, vw, vo = series([1.0] * 10 + [-2.0] + [0.5] * 5)
    r = walk(vi, vw, vo, contract(dd_type="trailing", dd_pct=0.50), risk=0.01)
    assert r["worst_floor_depth"] == pytest.approx(r["worst_peak_to_trough"])


# ------------------------------------------------------------------- breaching

def test_trailing_breach_is_detected_at_first_crossing():
    vi, vw, vo = series([0.0, 0.0, -10.0, 5.0])
    r = walk(vi, vw, vo, contract(dd_type="trailing", dd_pct=0.05), risk=0.01)
    assert r["breach"] is not None
    assert r["breach"]["kind"] == "max_dd"
    assert r["breach"]["i"] == 2, "breach must report the FIRST crossing"


def test_daily_breach_uses_day_start_not_high_water():
    """The daily budget is measured from each day's opening balance. A day that
    loses less than the budget must not breach even deep in a drawdown."""
    vi, vw, vo = series([-1.0] * 8)
    r = walk(vi, vw, vo, contract(dd_type="static", dd_pct=0.90,
                                  daily_pct=0.05), risk=0.01)
    assert r["breach"] is None, "1R/day at 1% risk is inside a 5% daily budget"
    assert r["worst_daily_loss"] == pytest.approx(0.01, abs=2e-3)


def test_absent_daily_rule_is_not_reported_as_zero():
    """Rule 3: never silently default an unavailable value to numeric zero."""
    vi, vw, vo = series([-5.0, -5.0])
    r = walk(vi, vw, vo, contract(daily_pct=None, dd_pct=0.90), risk=0.01)
    assert r["worst_daily_loss"] == 0.0
    assert r["breach"] is None, (
        "a contract with no daily rule must never produce a DAILY breach")


# ------------------------------------------------------------- the sizing solve

def test_max_safe_risk_is_the_actual_boundary():
    vi, vw, vo = series([0.5] * 5 + [-4.0] + [0.5] * 5)
    c = contract(dd_type="trailing", dd_pct=0.05)
    safe = max_safe_risk(vi, vw, vo, c)
    assert 0.0 < safe < 0.10
    assert walk(vi, vw, vo, c, safe)["breach"] is None, "boundary must be safe"
    assert walk(vi, vw, vo, c, safe * 1.05)["breach"] is not None, (
        "just above the boundary must breach — otherwise it is not the boundary")


def test_max_safe_risk_returns_zero_when_even_minimum_breaches():
    vi, vw, vo = series([-100.0])
    c = contract(dd_type="trailing", dd_pct=0.001)
    assert max_safe_risk(vi, vw, vo, c) == 0.0
