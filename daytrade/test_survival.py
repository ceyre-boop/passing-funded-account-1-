"""Tests for survival.py (spec 002). Every expected number is hand-computed in
the docstring of the test that uses it."""
from __future__ import annotations

import inspect
from datetime import date

import pytest

from daytrade import survival
from daytrade.survival import (BET2_MULT, Campaign, Proposal, SurvivalError,
                               check, sentence)

TODAY = date(2026, 10, 6)


def camp(**kw) -> Campaign:
    base = dict(account_size=25000.0, daily_goal_pct=1.2, cushion_remaining=1000.0,
                day_pnl_so_far=0.0, consecutive_red_days=0, cooloff_until=None)
    base.update(kw)
    return Campaign(**base)


def prop(risk: float = 140.0, target: float = 300.0) -> Proposal:
    return Proposal(risk_dollars=risk, target_dollars=target)


def test_clean_go():
    """goal = 25000*1.2/100 = 300. risk 140 < 0.5*1000 -> rule 5 GO, 1.0x.
    worst = 25000-140 = 24860; cushion = 1000-140 = 860; days = 140/300."""
    r = check(camp(), prop(), today=TODAY)
    assert r.verdict == "GO"
    assert r.size_multiplier == 1.0
    assert r.worst_case_balance == 24860.0
    assert r.cushion_after_loss == 860.0
    assert r.days_to_recover_at_goal == pytest.approx(140 / 300)
    assert r.still_on_track is True


def test_rule1_cooloff_active_and_boundary():
    """cooloff 2026-10-10, today 10-06 -> NO_TRADE with date in reason.
    On the cooloff date itself (today == until) it is no longer active -> GO."""
    r = check(camp(cooloff_until="2026-10-10"), prop(), today=TODAY)
    assert r.verdict == "NO_TRADE" and r.size_multiplier == 0.0
    assert "2026-10-10" in r.reason
    r2 = check(camp(cooloff_until="2026-10-10"), prop(), today=date(2026, 10, 10))
    assert r2.verdict == "GO"


def test_today_is_an_argument_not_a_clock():
    """Same campaign, today 10-09 (before 10-10) -> NO_TRADE; today 10-11 -> GO.
    Only `today` differs, so the outcome depends on the argument, not a clock."""
    c = camp(cooloff_until="2026-10-10")
    assert check(c, prop(), today=date(2026, 10, 9)).verdict == "NO_TRADE"
    assert check(c, prop(), today=date(2026, 10, 11)).verdict == "GO"
    src = inspect.getsource(survival)
    for banned in ("today()", ".now(", "time.time", "import time"):
        assert banned not in src.replace("`today()`", "")


def test_rule1_two_red_days():
    """consecutive_red_days=2 -> NO_TRADE 'two consecutive red days'."""
    r = check(camp(consecutive_red_days=2), prop(), today=TODAY)
    assert r.verdict == "NO_TRADE"
    assert "two consecutive red days" in r.reason
    assert check(camp(consecutive_red_days=1), prop(), today=TODAY).verdict == "GO"


def test_rule2_risk_equals_cushion_no_trade():
    """risk 1000 >= cushion 1000 -> NO_TRADE; cushion_after = 0, not on track."""
    r = check(camp(), prop(risk=1000.0), today=TODAY)
    assert r.verdict == "NO_TRADE"
    assert "ends the account" in r.reason
    assert r.cushion_after_loss == 0.0
    assert r.still_on_track is False


def test_rule2_size_down_to_half_cushion():
    """cushion 1000, risk 800 > 500 -> mult = 0.5*1000/800 = 0.625.
    Scaled loss = 800*0.625 = 500; worst = 24500; cushion_after = 500;
    days = 500/300."""
    r = check(camp(), prop(risk=800.0), today=TODAY)
    assert r.verdict == "SIZE_DOWN"
    assert r.size_multiplier == pytest.approx(0.625)
    assert r.worst_case_balance == pytest.approx(24500.0)
    assert r.cushion_after_loss == pytest.approx(500.0)
    assert r.days_to_recover_at_goal == pytest.approx(500 / 300)
    assert r.still_on_track is False  # 1.67 days > 1


def test_rule2_exactly_half_cushion_is_not_size_down():
    """risk 500 == 0.5*1000, not strictly greater -> falls through to GO."""
    assert check(camp(), prop(risk=500.0), today=TODAY).verdict == "GO"


def test_rule3_goal_banked():
    """goal 300; day_pnl 300 >= 300 -> NO_TRADE. day_pnl 299.99 -> GO."""
    r = check(camp(day_pnl_so_far=300.0), prop(), today=TODAY)
    assert r.verdict == "NO_TRADE" and "goal already banked" in r.reason
    assert check(camp(day_pnl_so_far=299.99), prop(), today=TODAY).verdict == "GO"


def test_rule4_bet2_smaller_than_bet1():
    """Bet 1 (pnl 0): GO 1.0x. Bet 2 (pnl -140): SIZE_DOWN at BET2_MULT=0.5.
    Scaled loss 140*0.5 = 70 -> worst 24930, cushion 930."""
    bet1 = check(camp(day_pnl_so_far=0.0), prop(), today=TODAY)
    bet2 = check(camp(day_pnl_so_far=-140.0), prop(), today=TODAY)
    assert bet1.size_multiplier == 1.0
    assert bet2.verdict == "SIZE_DOWN"
    assert bet2.size_multiplier == 0.5 == BET2_MULT
    assert bet2.size_multiplier < bet1.size_multiplier
    assert bet2.worst_case_balance == 24930.0
    assert bet2.cushion_after_loss == 930.0


def test_bet2_never_scales_up_with_deeper_loss_or_big_target():
    """Whatever the loss size or target, bet-2 mult stays exactly 0.5."""
    for pnl in (-1.0, -140.0, -900.0):
        r = check(camp(day_pnl_so_far=pnl), prop(target=99999.0), today=TODAY)
        assert r.size_multiplier == 0.5


def test_priority_cooloff_beats_goal_banked():
    """Cooloff active AND day_pnl 400 >= goal 300: rule 1 wins -> reason is
    cooloff, not 'goal banked'."""
    r = check(camp(cooloff_until="2026-10-20", day_pnl_so_far=400.0),
              prop(), today=TODAY)
    assert "cooloff" in r.reason and "banked" not in r.reason


def test_priority_cooloff_beats_cushion():
    """Cooloff AND risk 1000 >= cushion: cooloff reason wins."""
    r = check(camp(cooloff_until="2026-10-20"), prop(risk=1000.0), today=TODAY)
    assert "cooloff" in r.reason and "ends the account" not in r.reason


def test_priority_red_days_beat_cushion():
    """2 red days AND risk >= cushion: red-days reason wins."""
    r = check(camp(consecutive_red_days=3), prop(risk=1000.0), today=TODAY)
    assert "two consecutive red days" in r.reason


def test_priority_cushion_beats_goal_banked():
    """risk 1000 >= cushion AND pnl 400 >= goal: cushion reason wins."""
    r = check(camp(day_pnl_so_far=400.0), prop(risk=1000.0), today=TODAY)
    assert "ends the account" in r.reason


def test_priority_size_down_cushion_beats_goal_banked():
    """risk 800 (>half cushion) AND pnl 400 >= goal 300: SIZE_DOWN wins over
    the goal-banked NO_TRADE."""
    r = check(camp(day_pnl_so_far=400.0), prop(risk=800.0), today=TODAY)
    assert r.verdict == "SIZE_DOWN" and "over half" in r.reason


def test_priority_cushion_size_down_beats_bet2():
    """risk 800, pnl -100: cushion rule gives 0.625, not BET2_MULT 0.5."""
    r = check(camp(day_pnl_so_far=-100.0), prop(risk=800.0), today=TODAY)
    assert r.size_multiplier == pytest.approx(0.625)
    assert "over half" in r.reason


def test_priority_goal_banked_beats_bet2_unreachable_but_ordered():
    """pnl cannot be both >= goal and < 0, so goal-banked vs bet2 is disjoint;
    pnl exactly 0 (not < 0) is GO, confirming bet2 needs a strict loss."""
    assert check(camp(day_pnl_so_far=0.0), prop(), today=TODAY).verdict == "GO"
    assert check(camp(day_pnl_so_far=-0.01), prop(), today=TODAY).verdict == "SIZE_DOWN"


def test_multiplier_never_exceeds_one_on_any_path():
    """Sweep every path; each must have 0 <= mult <= 1 and the expected verdict."""
    cases = [
        (camp(cooloff_until="2026-12-01"), prop(), "NO_TRADE"),
        (camp(consecutive_red_days=2), prop(), "NO_TRADE"),
        (camp(), prop(risk=1000.0), "NO_TRADE"),
        (camp(), prop(risk=999.0), "SIZE_DOWN"),
        (camp(), prop(risk=501.0), "SIZE_DOWN"),
        (camp(day_pnl_so_far=300.0), prop(), "NO_TRADE"),
        (camp(day_pnl_so_far=-5.0), prop(), "SIZE_DOWN"),
        (camp(), prop(risk=1.0), "GO"),
    ]
    for c, p, verdict in cases:
        r = check(c, p, today=TODAY)
        assert r.verdict == verdict
        assert 0.0 <= r.size_multiplier <= 1.0


def test_sentence_pinned():
    """account 25000, goal 1.2% = $300, risk 140, cushion 1000, pnl 0.
    worst 24860, cushion left 860, days = ceil(140/300)=1, YES, GO 1.0x."""
    c, p = camp(), prop()
    s = sentence(check(c, p, today=TODAY), c, p)
    assert s == ("SURVIVAL: risk $140 -> worst case $24,860, cushion $860 left, "
                 "1 day back to on-track at $300/day goal. Still on track: YES. "
                 "Verdict: GO (1.0x).")


def test_sentence_size_down_and_no():
    """risk 800 -> mult 0.625 -> loss 500: worst 24,500, cushion 500, days
    ceil(1.667)=2, NO, 'SIZE_DOWN (0.62x)' (round(0.625,2)=0.62)."""
    c, p = camp(), prop(risk=800.0)
    s = sentence(check(c, p, today=TODAY), c, p)
    assert s == ("SURVIVAL: risk $500 -> worst case $24,500, cushion $500 left, "
                 "2 days back to on-track at $300/day goal. Still on track: NO. "
                 "Verdict: SIZE_DOWN (0.62x).")


@pytest.mark.parametrize("kw", [
    dict(account_size=0.0), dict(account_size=-1.0),
    dict(daily_goal_pct=0.0), dict(daily_goal_pct=-1.0),
    dict(daily_goal_pct=5.01), dict(cushion_remaining=-0.01),
    dict(day_pnl_so_far=float("nan")), dict(account_size=float("inf")),
    dict(consecutive_red_days=-1), dict(consecutive_red_days=1.5),
    dict(cooloff_until="not-a-date"),
])
def test_campaign_validation(kw):
    with pytest.raises(SurvivalError):
        camp(**kw)


@pytest.mark.parametrize("kw", [
    dict(risk_dollars=0.0), dict(risk_dollars=-1.0),
    dict(risk_dollars=float("nan")), dict(target_dollars=0.0),
    dict(target_dollars=-5.0),
])
def test_proposal_validation(kw):
    base = dict(risk_dollars=140.0, target_dollars=300.0)
    base.update(kw)
    with pytest.raises(SurvivalError):
        Proposal(**base)


def test_goal_pct_upper_bound_accepted():
    assert camp(daily_goal_pct=5.0).daily_goal_pct == 5.0


def test_check_rejects_non_date_today():
    with pytest.raises(SurvivalError):
        check(camp(), prop(), today="2026-10-06")  # type: ignore[arg-type]


def test_no_confidence_or_pnl_goal_parameter():
    """Regime confidence and P&L-derived goals are not inputs."""
    names = set(inspect.signature(check).parameters)
    assert names == {"campaign", "proposal", "today"}
    fields = set(Campaign.__dataclass_fields__) | set(Proposal.__dataclass_fields__)
    assert not any("conf" in f or "regime" in f for f in fields)


def test_frozen_and_constant():
    with pytest.raises(Exception):
        camp().account_size = 1.0  # type: ignore[misc]
    assert BET2_MULT == 0.5


def test_build_backstop_assertion_rejects_scale_up():
    """The assertion itself: _build with mult 1.5 must raise, not return."""
    with pytest.raises(AssertionError):
        survival._build(camp(), prop(), 300.0, "GO", 1.5, "x")
