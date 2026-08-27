"""Tests for daytrade/paper_carry_runner.py — Phase 2 of THE_BIG_PLAN.

Fault-injection style throughout: every guard test should FAIL if the rule
it names stops being enforced, not just pass on the happy path.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paper_carry_runner as pcr  # noqa: E402
import streak  # noqa: E402
from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402

TODAY = date(2026, 8, 26)


def proposal(**over):
    base = dict(pair="EURUSD=X", direction="LONG", entry=1.1000, stop=1.0900,
               risk_pct=0.01, qty=100_000)
    base.update(over)
    return pcr.TradeProposal(**base)


def guard_inputs(**over):
    base = dict(account_size=100_000.0, daily_goal_pct=1.0, target_dollars=1_000.0,
               cushion_remaining=5_000.0, day_pnl_so_far=0.0,
               max_total_open_risk_r=3.0, max_per_symbol_exposure_r=1.5,
               max_correlated_exposure_r=2.0, max_unprotected_count=1,
               daily_loss_lock_r=2.0, emergency_flatten_r=3.0)
    base.update(over)
    return pcr.GuardInputs(**base)


def clean_streak_state():
    return streak.StreakState()


# --------------------------------------------------------------- shipped disarmed

def test_module_ships_disarmed():
    assert pcr.ARMED is False, "paper_carry_runner must ship with ARMED=False"


# --------------------------------------------------------------- gate ordering

def test_cooloff_blocks_open_before_anything_else():
    state = streak.StreakState(cooloff_until=(TODAY + timedelta(days=3)).isoformat())
    v = pcr.gate_open(proposal(), guard_inputs(), streak_state=state,
                      open_records=[], today=TODAY)
    assert v.allowed is False
    assert "cooloff" in v.reasons[0]
    assert v.survival_check is None, "cooloff must refuse before survival even runs"


def test_two_consecutive_red_days_feeds_survivals_own_no_trade_rule():
    """streak.py doesn't set cooloff_until until the SECOND red day's update()
    call persists it; consecutive_red_days=2 passed straight through is what
    survival.py's own rule 1 (>=2 consecutive red days) catches independently."""
    state = streak.StreakState(consecutive_red_days=2)
    v = pcr.gate_open(proposal(), guard_inputs(), streak_state=state,
                      open_records=[], today=TODAY)
    assert v.allowed is False
    assert "cooloff" in v.reasons[0] or "consecutive" in v.reasons[0]


def test_portfolio_daily_loss_lock_blocks_open():
    records = [dict(id="c1", pair="EURUSD=X", status="closed", R=-2.5,
                    exit_date=TODAY.isoformat())]
    v = pcr.gate_open(proposal(), guard_inputs(daily_loss_lock_r=2.0),
                      streak_state=clean_streak_state(), open_records=records,
                      today=TODAY)
    assert v.allowed is False
    assert "G005" in v.reasons[0]


def test_portfolio_emergency_flatten_outranks_and_also_blocks():
    records = [dict(id="c1", pair="EURUSD=X", status="closed", R=-3.5,
                    exit_date=TODAY.isoformat())]
    v = pcr.gate_open(proposal(), guard_inputs(), streak_state=clean_streak_state(),
                      open_records=records, today=TODAY)
    assert v.allowed is False
    assert "G006" in v.reasons[0]


def test_per_symbol_exposure_limit_blocks_a_second_same_pair_trade():
    records = [dict(id="o1", pair="EURUSD=X", status="open")]
    v = pcr.gate_open(proposal(pair="EURUSD=X"),
                      guard_inputs(max_per_symbol_exposure_r=1.5),
                      streak_state=clean_streak_state(), open_records=records,
                      today=TODAY)
    # existing 1R + proposed 1R = 2R > 1.5R limit
    assert v.allowed is False
    assert "G002" in v.reasons[0]


def test_correlated_group_limit_blocks_across_pairs():
    records = [dict(id="o1", pair="EURUSD=X", status="open")]
    gi = guard_inputs(max_correlated_exposure_r=1.5,
                      correlated_groups={"eur_group": ["EURUSD=X", "EURGBP=X"]})
    v = pcr.gate_open(proposal(pair="EURGBP=X"), gi, streak_state=clean_streak_state(),
                      open_records=records, today=TODAY)
    assert v.allowed is False
    assert "G003" in v.reasons[0]


def test_survival_no_trade_when_stop_out_would_break_the_eval():
    gi = guard_inputs(cushion_remaining=500.0)  # risk_dollars = 0.01*100000 = 1000 >= 500
    v = pcr.gate_open(proposal(), gi, streak_state=clean_streak_state(),
                      open_records=[], today=TODAY)
    assert v.allowed is False
    assert "account" in v.reasons[0]


def test_survival_bet2_smaller_after_a_red_day():
    gi = guard_inputs(day_pnl_so_far=-200.0)
    v = pcr.gate_open(proposal(), gi, streak_state=clean_streak_state(),
                      open_records=[], today=TODAY)
    assert v.allowed is True
    assert v.size_multiplier == pytest.approx(0.5)


def test_clean_setup_goes_at_full_size():
    v = pcr.gate_open(proposal(), guard_inputs(), streak_state=clean_streak_state(),
                      open_records=[], today=TODAY)
    assert v.allowed is True
    assert v.size_multiplier == pytest.approx(1.0)


def test_size_multiplier_never_exceeds_one():
    """Mutation guard: no combination of guard inputs can produce a verdict
    that scales size UP. survival.SurvivalCheck.__post_init__ already
    enforces this; this test pins it at the gate_open boundary too."""
    for day_pnl in (0.0, 100.0, 1000.0, -50.0):
        v = pcr.gate_open(proposal(), guard_inputs(day_pnl_so_far=day_pnl),
                          streak_state=clean_streak_state(), open_records=[],
                          today=TODAY)
        assert v.size_multiplier <= 1.0


# --------------------------------------------------------------- ledger helpers

def test_positions_as_snapshots_reads_one_r_per_open_trade():
    records = [dict(id="a", pair="EURUSD=X", status="open"),
              dict(id="b", pair="GBPUSD=X", status="closed", R=0.4,
                   exit_date="2026-08-20")]
    snaps = pcr.positions_as_snapshots(pcr.open_positions_from_ledger(records))
    assert len(snaps) == 1
    assert snaps[0].symbol == "EURUSD=X"
    assert snaps[0].open_risk_r == 1.0
    assert snaps[0].protected is True


def test_realized_today_r_sums_only_trades_closed_today_and_is_explicit_zero():
    records = [dict(status="closed", R=0.5, exit_date=TODAY.isoformat()),
              dict(status="closed", R=-0.3, exit_date=TODAY.isoformat()),
              dict(status="closed", R=9.0, exit_date="2026-08-01"),
              dict(status="open", R=None)]
    assert pcr.realized_today_r(records, TODAY) == pytest.approx(0.2)
    assert pcr.realized_today_r([], TODAY) == 0.0


# --------------------------------------------------------------- arming ritual

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    log = tmp_path / "paper.jsonl"
    streak_path = tmp_path / "streak.json"
    logged_open, logged_close = [], []
    import sovereign.intelligence.decision_logger as dl
    monkeypatch.setattr(dl, "log_forex_decision",
                        lambda **kw: logged_open.append(kw) or "test-decision-id")
    monkeypatch.setattr(dl, "update_outcome",
                        lambda **kw: logged_close.append(kw) or True)
    return dict(log_path=log, streak_path=streak_path,
               logged_open=logged_open, logged_close=logged_close)


def test_shadow_mode_writes_nothing_even_when_the_gate_would_allow(isolated):
    result = pcr.open_paper_trade(proposal(), guard_inputs(), armed=False,
                                  today=TODAY, log_path=isolated["log_path"],
                                  streak_path=isolated["streak_path"])
    assert result["status"] == "SHADOW"
    assert result["sent"] is False
    assert not isolated["log_path"].exists() or pcr._read_ledger(isolated["log_path"]) == []
    assert isolated["logged_open"] == []


def test_refused_gate_never_reaches_the_write_even_if_armed(isolated):
    """A guard refusal must make the write unreachable regardless of ARMED —
    arming controls whether an ALLOWED trade gets written, never whether a
    REFUSED one does."""
    gi = guard_inputs(cushion_remaining=500.0)
    result = pcr.open_paper_trade(proposal(), gi, armed=True, assume_yes=True,
                                  today=TODAY, log_path=isolated["log_path"],
                                  streak_path=isolated["streak_path"])
    assert result["status"] == "REFUSED"
    assert result["sent"] is False
    assert pcr._read_ledger(isolated["log_path"]) == []
    assert isolated["logged_open"] == []


def test_armed_without_confirmation_writes_nothing(isolated, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(EOFError()))
    result = pcr.open_paper_trade(proposal(), guard_inputs(), armed=True,
                                  assume_yes=False, today=TODAY,
                                  log_path=isolated["log_path"],
                                  streak_path=isolated["streak_path"])
    assert result["status"] == "DECLINED_BY_USER"
    assert pcr._read_ledger(isolated["log_path"]) == []
    assert isolated["logged_open"] == []


def test_armed_plus_yes_writes_ledger_and_decision_log(isolated):
    result = pcr.open_paper_trade(proposal(), guard_inputs(), armed=True,
                                  assume_yes=True, today=TODAY,
                                  log_path=isolated["log_path"],
                                  streak_path=isolated["streak_path"])
    assert result["status"] == "SENT"
    assert result["sent"] is True
    records = pcr._read_ledger(isolated["log_path"])
    assert len(records) == 1 and records[0]["status"] == "open"
    assert len(isolated["logged_open"]) == 1


def test_close_is_never_gated_by_streak_or_portfolio_state(isolated):
    """close_paper_trade takes no streak/portfolio arguments at all — this
    pins that structurally: an emergency FLATTEN_ALL or an active cooloff
    must never be able to prevent closing a position, only opening one."""
    import inspect
    sig = inspect.signature(pcr.close_paper_trade)
    assert "streak_state" not in sig.parameters
    assert "guard_in" not in sig.parameters


def test_close_shadow_writes_nothing(isolated):
    pcr._write_ledger([dict(id="t1", status="open", pair="EURUSD=X", direction="LONG",
                            entry=1.1000, stop=1.0900, entry_date="2026-08-20")],
                      isolated["log_path"])
    result = pcr.close_paper_trade("t1", 1.1100, exit_date=TODAY, armed=False,
                                   log_path=isolated["log_path"])
    assert result["status"] == "SHADOW"
    records = pcr._read_ledger(isolated["log_path"])
    assert records[0]["status"] == "open", "shadow close must not mutate the ledger"
    assert isolated["logged_close"] == []


def test_close_armed_computes_r_and_writes(isolated):
    pcr._write_ledger([dict(id="t1", status="open", pair="EURUSD=X", direction="LONG",
                            entry=1.1000, stop=1.0900, entry_date="2026-08-20")],
                      isolated["log_path"])
    result = pcr.close_paper_trade("t1", 1.1100, exit_date=TODAY, armed=True,
                                   assume_yes=True, log_path=isolated["log_path"])
    assert result["status"] == "SENT"
    assert result["R"] == pytest.approx(1.0 - 6 * 0.004)  # 6 calendar days, cti haircut
    records = pcr._read_ledger(isolated["log_path"])
    assert records[0]["status"] == "closed"
    assert len(isolated["logged_close"]) == 1


def test_double_close_refused(isolated):
    pcr._write_ledger([dict(id="t1", status="closed", R=0.1, pair="EURUSD=X",
                            direction="LONG", entry=1.0, stop=0.99,
                            entry_date="2026-08-01")], isolated["log_path"])
    with pytest.raises(pcr.GateRefused):
        pcr.close_paper_trade("t1", 1.01, exit_date=TODAY, armed=True,
                              assume_yes=True, log_path=isolated["log_path"])


# --------------------------------------------------------------- derived R-limits

def test_resolve_r_limits_orders_daily_before_emergency():
    # alpha_swing HAS a daily_dd (0.05) strictly under its max_dd (0.10) —
    # cti_1step has none, see test_resolve_r_limits_refuses_when_no_daily_dd.
    daily, emergency = pcr.resolve_r_limits("alpha_swing")
    assert daily < emergency
    assert daily > 0 and emergency > 0


def test_resolve_r_limits_uses_the_real_contract_not_a_reimplementation():
    contract = load_contract("alpha_swing")
    daily, emergency = pcr.resolve_r_limits("alpha_swing")
    # both scaled by the same ref_risk -> the ratio is exactly the contract's
    # own daily_dd/max_dd ratio, independent of what ref_risk turned out to be.
    assert daily / emergency == pytest.approx(contract.daily_dd.pct / contract.max_dd.pct,
                                               rel=1e-6)


def test_resolve_r_limits_refuses_rather_than_fabricate_when_no_daily_dd():
    """cti_1step has no daily_dd. Falling back to a permissive 100%-of-account
    daily limit would derive a daily_loss_lock_r WIDER than emergency_flatten_r
    — a lock that fires after the flatten it's supposed to precede. Refusing
    is the correct behavior, not a bug to route around."""
    with pytest.raises(pcr.GateRefused, match="no daily_dd"):
        pcr.resolve_r_limits("cti_1step")
