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
    for c in contracts.values():
        assert c.max_dd.type == "static", (
            f"{c.key}: the carry lane is static-DD; trailing here is a transcription fault"
        )
        assert not c.has_deadline, f"{c.key}: no-deadline lane; max_days must be null"


def test_unknown_firm_raises():
    with pytest.raises(KeyError):
        load_contract("lucid_flex")  # futures lane is deliberately absent


def test_static_vs_trailing_floor_moves():
    """Fault: flipping static->trailing must change the ceiling once peak > start."""
    cti = load_contract("cti_1step")
    # Account ran up to 110k then back to 104k. Static floor: 95k. Trailing floor: 104.5k.
    state = _state(equity=104_000.0, peak=110_000.0, start=100_000.0)
    static_ceiling = _ceiling(cti, state)
    trailing = replace(cti, max_dd=DrawdownRule(pct=0.05, basis="balance", mark="close",
                                                type="trailing"))
    trailing_ceiling = _ceiling(trailing, state)
    # static: (104k - 95k)/104k ≈ 8.65% ; trailing: floor 104.5k above equity -> 0
    assert static_ceiling == pytest.approx((104_000 - 95_000) / 104_000)
    assert trailing_ceiling == 0.0
    assert static_ceiling != trailing_ceiling


def test_no_daily_limit_means_daily_floor_never_binds():
    """Fault: giving CTI a daily budget. With none, a deep intraday day still
    leaves the max-DD floor as the only constraint."""
    cti = load_contract("cti_1step")
    assert cti.daily_dd is None
    assert cti.to_prop_cfg()["daily_loss_limit_pct"] == NO_DAILY_LIMIT_PCT
    # Down 4k today intraday; static 5% floor at 95k is the binding one.
    state = _state(equity=96_000.0, daily_realized=-4_000.0)
    assert _ceiling(cti, state) == pytest.approx((96_000 - 95_000) / 96_000)
    # Mutate in an FTMO-style 5% daily budget: day start 100k -> daily floor 95k,
    # same binding floor here, so push the day deeper to separate them.
    state2 = _state(equity=93_000.0, daily_realized=-7_000.0, start=90_000.0)
    # static floor: 90k*(1-0.05)=85.5k ; no daily -> budget (93-85.5)/93
    assert _ceiling(cti, state2) == pytest.approx((93_000 - 85_500) / 93_000)
    daily = replace(cti, daily_dd=DrawdownRule(pct=0.05, basis="balance", mark="close"))
    # daily floor: 100k - 5k = 95k > equity 93k -> zero risk permitted
    assert _ceiling(daily, state2) == 0.0


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
