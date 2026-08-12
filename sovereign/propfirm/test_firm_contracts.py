"""Mutation-oriented unit tests for the firm contract layer — spec 021.

Each test states the fault it exists to catch; the fault-injection rows live in
specs/021_MUTATION_LOG.md.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sovereign.propfirm.firm_contracts import (
    NO_DAILY_LIMIT_PCT,
    DrawdownRule,
    load_contract,
    load_contracts,
)
from sovereign.risk.layers.prop import prop_ceiling


def _state(equity=100_000.0, peak=100_000.0, start=100_000.0,
           daily_realized=0.0, daily_open=0.0, open_positions=()):
    return SimpleNamespace(
        equity=equity, peak_equity=peak, starting_balance=start,
        daily_realized_pnl=daily_realized, daily_open_pnl=daily_open,
        open_positions=list(open_positions),
    )


def _ceiling(contract, state, phase_index=0):
    return prop_ceiling(None, state, {"prop": contract.to_prop_cfg(phase_index)})


def test_contracts_load_and_validate():
    contracts = load_contracts()
    assert {"cti_1step", "alpha_swing", "ftmo_swing"} <= set(contracts)
    # CTI 1-Step uses trailing; alpha_swing and ftmo_swing use static (verified 2026-08-12).
    for c in contracts.values():
        if c.key == "cti_1step":
            assert c.max_dd.type == "trailing", f"{c.key}: live rules use trailing DD"
        else:
            assert c.max_dd.type == "static", f"{c.key}: static DD per rules"
        assert not c.has_deadline, f"{c.key}: no-deadline lane; max_days must be null"


def test_unknown_firm_raises():
    with pytest.raises(KeyError):
        load_contract("lucid_flex")  # futures lane is deliberately absent


def test_static_vs_trailing_floor_moves():
    """Fault: flipping static->trailing must change the ceiling once peak > start."""
    cti = load_contract("cti_1step")  # Now uses trailing (verified 2026-08-12)
    # Account ran up to 110k then back to 104k. Static floor: 95k. Trailing floor: 104.5k.
    state = _state(equity=104_000.0, peak=110_000.0, start=100_000.0)
    trailing_ceiling = _ceiling(cti, state)  # CTI is trailing; floor 104.5k above equity -> 0
    static = replace(cti, max_dd=DrawdownRule(pct=0.05, basis="balance", mark="close",
                                              type="static"))
    static_ceiling = _ceiling(static, state)
    # static: (104k - 95k)/104k ≈ 8.65% ; trailing: floor 104.5k above equity -> 0
    assert static_ceiling == pytest.approx((104_000 - 95_000) / 104_000)
    assert trailing_ceiling == 0.0
    assert static_ceiling != trailing_ceiling


def test_no_daily_limit_means_daily_floor_never_binds():
    """Fault: giving CTI a daily budget. With none, the trailing-DD floor
    is the only constraint (CTI uses trailing, verified 2026-08-12)."""
    cti = load_contract("cti_1step")
    assert cti.daily_dd is None
    # Literal, not NO_DAILY_LIMIT_PCT: comparing the module constant against
    # itself is a tautology that survives any mutation of the constant.
    assert cti.to_prop_cfg()["daily_loss_limit_pct"] == 1.0
    assert NO_DAILY_LIMIT_PCT == 1.0, "sentinel must mean '100%, never binds'"
    # The scenario must leave the max-DD floor NOT binding, otherwise both arms
    # return 0.0 and the comparison cannot observe the daily floor at all.
    # Day opened at the 100k high-water mark and is down 4k: equity 96k.
    # Trailing max-DD floor = 100k * 0.95 = 95k, which is below equity.
    state = _state(equity=96_000.0, daily_realized=-4_000.0, peak=100_000.0)
    ceiling_no_daily = _ceiling(cti, state)
    assert ceiling_no_daily == pytest.approx((96_000 - 95_000) / 96_000)
    assert ceiling_no_daily > 0.0, "max-DD floor must not bind, or this proves nothing"

    # Same state, but give CTI a daily budget tighter than the 5% max-DD floor
    # (3% -> daily floor 97k) so the two floors are separable. It now binds and
    # zeroes the ceiling. That difference IS the invariant: absent daily_dd, the
    # NO_DAILY_LIMIT_PCT sentinel must make the daily floor unreachable.
    daily = replace(cti, daily_dd=DrawdownRule(pct=0.03, basis="balance", mark="close"))
    assert _ceiling(daily, state) == 0.0
    assert _ceiling(daily, state) != ceiling_no_daily


def test_alpha_swing_two_phase_targets():
    """Fault: phase-2 target transcribed as 10% instead of 5%."""
    alpha = load_contract("alpha_swing")
    assert [p.target_pct for p in alpha.phases] == [0.10, 0.05]
    assert alpha.daily_dd is not None and alpha.daily_dd.pct == 0.05


def test_phase_index_bounds():
    cti = load_contract("cti_1step")
    with pytest.raises(IndexError):
        cti.to_prop_cfg(phase_index=1)  # 1-step firm has no phase 2


def test_open_positions_consume_budget():
    """Correlated worst case: open risk is deducted from the ceiling."""
    cti = load_contract("cti_1step")
    open_pos = [SimpleNamespace(risk_pct_at_entry=0.02)]
    with_open = _ceiling(cti, _state(open_positions=open_pos))
    without = _ceiling(cti, _state())
    assert with_open == pytest.approx(without - 0.02)


def test_malformed_contract_rejected(tmp_path):
    """Fault: schema drift — a missing field or illegal enum must raise, not default."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "broken:\n"
        "  display_name: X\n"
        "  account_size: 100000\n"
        "  phases: [{target_pct: 0.08, min_trading_days: 0, max_days: null}]\n"
        "  max_dd: {type: sideways, pct: 0.05, basis: balance, mark: close}\n"
        "  daily_dd: null\n"
        "  permissions: {weekend_hold: true, overnight_hold: true, news_hold: true}\n"
        "  costs: {fee_usd: 1, refund_on_pass: 0, swap_haircut_r_per_day: 0.004}\n"
        "  rules_asof: '2026-08-10'\n"
        "  source_url: x\n"
    )
    with pytest.raises(ValueError):
        load_contract("broken", path=bad)


def test_mark_and_basis_are_preserved():
    """The evaluator branches on mark (close vs intraday); losing the field would
    silently change bust semantics."""
    ftmo = load_contract("ftmo_swing")
    assert ftmo.daily_dd.mark == "close"
    assert ftmo.max_dd.basis == "balance"
