#!/usr/bin/env python3
"""carry-exit-v1 (spec 034). A separate evaluator, held to the same discipline
as the intraday one: pure, deterministic, loud, and unable to name an exit
the sealed record does not contain."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from carry_exit import (CarryState, CarryStage, CarryAction, CarryStateError,
                        EXIT_REASONS, decide_carry_exit, apply_carry_action,
                        effective_stop, trail_level)


def mk(**kw):
    base = dict(pair="EURUSD=X", direction=1, entry=1.1000, price=1.1000,
                sl=1.0918, swap_accrual_r_per_day=0.004)
    return CarryState(**{**base, **kw})


# ───────────────── the terms futures-exit-v1 cannot express ──────────────

def test_financing_accrues_daily_and_weekends_bill_three():
    s = mk(days_held=5, weekends_crossed=1)
    # 5 days held + 2 extra days for the weekend = 7 * 0.004
    assert s.swap_paid_r == pytest.approx(0.028)
    assert mk(days_held=5).swap_paid_r == pytest.approx(0.020)


def test_net_r_subtracts_financing_from_the_price_move():
    s = mk(price=1.1082, days_held=5)          # +1R gross on a 0.0082 risk
    assert s.gross_r == pytest.approx(1.0, abs=1e-3)
    assert s.net_r == pytest.approx(1.0 - 0.020, abs=1e-3)
    assert s.net_r < s.gross_r                  # holding is never free


def test_carry_flip_is_a_sign_change_against_the_position():
    assert not mk(rate_diff=1.0, rate_diff_at_entry=1.0).carry_flipped
    assert mk(rate_diff=-0.5, rate_diff_at_entry=1.0).carry_flipped
    # short: a flip is the mirror image
    assert mk(direction=-1, sl=1.1082, rate_diff=0.5,
              rate_diff_at_entry=-1.0).carry_flipped


def test_a_stale_rate_read_is_not_evidence_of_a_flip():
    """JPY/AUD series lag ~60-80 days; a stale read must not trigger an exit."""
    s = mk(rate_diff=-0.5, rate_diff_at_entry=1.0, rate_diff_stale_days=120)
    assert not s.carry_flipped


def test_missing_rate_data_never_triggers_a_flip():
    assert not mk(rate_diff=None, rate_diff_at_entry=1.0).carry_flipped


# ───────────────────────────── precedence ────────────────────────────────

def test_stop_outranks_everything():
    s = mk(price=1.0900, days_held=99, rate_diff=-1.0, rate_diff_at_entry=1.0)
    a = decide_carry_exit(s)[0]
    assert a.kind == "EXIT_ALL" and a.exit_reason == "stop"


def test_reversal_outranks_time():
    s = mk(price=1.1050, days_held=99, rate_diff=-1.0, rate_diff_at_entry=1.0)
    assert decide_carry_exit(s)[0].exit_reason == "reversal"


def test_time_stop_fires_at_the_declared_horizon():
    assert decide_carry_exit(mk(price=1.1050, days_held=4))[0].kind == "HOLD"
    assert decide_carry_exit(mk(price=1.1050, days_held=5))[0].exit_reason == "time"


def test_time_stop_can_be_disabled():
    assert decide_carry_exit(
        mk(price=1.1050, days_held=99, time_stop_days=None))[0].kind == "HOLD"


def test_precedence_switch_lets_the_trail_outrank_time():
    """Sealed trailing_stop exits hold a median 7 days, longer than the 5-day
    time stop — so which rule outranks which is empirical, and switchable."""
    # price clearly BELOW the trail (1.1300 - 2*0.0050 = 1.1200); an exactly
    # equal price lands on a float boundary and tests nothing
    kw = dict(price=1.1150, days_held=6, atr14=0.0050, hwm=1.1300)
    assert decide_carry_exit(mk(**kw))[0].exit_reason == "time"
    a = decide_carry_exit(mk(trail_outranks_time=True, **kw))[0]
    assert a.exit_reason == "trailing_stop"     # trail crossed first


# ─────────────────────────────── the trail ───────────────────────────────

def test_trail_is_unarmed_before_its_delay_and_without_atr():
    assert trail_level(mk(days_held=0, atr14=0.005)) is None
    assert trail_level(mk(days_held=5, atr14=None)) is None
    assert trail_level(mk(days_held=5, atr14=None, trail_atr_mult=None)) is None


def test_trail_hangs_below_the_high_water_mark():
    s = mk(days_held=3, atr14=0.0050, hwm=1.1300, price=1.1300)
    assert trail_level(s) == pytest.approx(1.1300 - 2.0 * 0.0050)


def test_effective_stop_never_loosens_below_the_catastrophic():
    s = mk(days_held=3, atr14=0.0050, hwm=1.1000, price=1.1000)
    assert effective_stop(s) >= s.catastrophic_sl


# ───────────────────────── loudness / containment ────────────────────────

def test_malformed_state_raises_at_construction():
    with pytest.raises(CarryStateError):
        mk(direction=0)
    with pytest.raises(CarryStateError):
        mk(sl=1.1000)                            # entry == stop, R undefined
    with pytest.raises(CarryStateError):
        mk(swap_accrual_r_per_day=-0.001)
    with pytest.raises(CarryStateError):
        mk(days_held=-1)
    with pytest.raises(CarryStateError):
        mk(time_stop_days=0)


def test_move_sl_may_never_loosen_the_stop():
    s = mk()
    with pytest.raises(CarryStateError, match="loosen"):
        apply_carry_action(s, CarryAction("MOVE_SL", sl=1.0500))


def test_an_exit_outside_the_sealed_vocabulary_is_refused():
    """The engine may not invent an exit the strategy has never taken."""
    s = mk()
    with pytest.raises(CarryStateError, match="vocabulary"):
        apply_carry_action(s, CarryAction("EXIT_ALL", exit_reason="vibes"))
    assert set(EXIT_REASONS) == {"stop", "reversal", "time", "trailing_stop"}


def test_unknown_action_kind_raises():
    with pytest.raises(CarryStateError, match="unknown action"):
        apply_carry_action(mk(), CarryAction("TELEPORT"))


def test_lifecycle_cannot_run_backwards():
    s = mk(price=1.0900)
    apply_carry_action(s, decide_carry_exit(s)[0])
    assert s.stage is CarryStage.CLOSED
    assert decide_carry_exit(s)[0].kind == "HOLD"      # closed stays closed


# ──────────────────────── determinism / separation ───────────────────────

def test_decide_is_deterministic():
    outs = set()
    for _ in range(50):
        s = mk(price=1.1050, days_held=3, atr14=0.005, hwm=1.1100)
        outs.add(tuple((a.kind, a.exit_reason, a.sl)
                       for a in decide_carry_exit(s)))
    assert len(outs) == 1


def test_carry_state_is_a_separate_type_not_a_superset():
    """SF-3's remedy: a second evaluator, not an extended one. CarryState must
    NOT carry intraday terms, and TradeState must not carry carry terms."""
    from stockfish_exit import TradeState
    carry_f = set(CarryState.__dataclass_fields__)
    intra_f = set(TradeState.__dataclass_fields__)
    for intraday_only in ("tp1", "tp2", "flatten_at_et", "now_et", "goal_fraction"):
        assert intraday_only not in carry_f
    for carry_only in ("swap_accrual_r_per_day", "days_held", "rate_diff",
                       "weekends_crossed"):
        assert carry_only not in intra_f


def test_frozen_checkpoint_refuses_to_price_carry():
    """The guardrail that makes borrowing the intraday opponent fail loudly."""
    import frozen_policy as fp
    with pytest.raises(fp.CheckpointError, match="prices no policy"):
        fp.policy("FX_CARRY")
