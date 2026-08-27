"""Tests for daytrade/survival.py — spec 002.

Per CLAUDE.md's definition of VERIFIED, every stated invariant gets a test
that fails on deliberate violation, not just a happy-path test. Each test
states the fault it exists to catch, matching the style of
sovereign/forex/test_carry_engine_atr.py and daytrade/test_datasource.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from daytrade import survival as sv


def _campaign(**overrides):
    defaults = dict(
        account_size=25_000.0,
        daily_goal_pct=1.2,          # goal = 25000 * 1.2 / 100 = $300
        cushion_remaining=1_000.0,
        day_pnl_so_far=0.0,
        consecutive_red_days=0,
        cooloff_until=None,
    )
    defaults.update(overrides)
    return sv.Campaign(**defaults)


def _proposal(**overrides):
    defaults = dict(risk_dollars=140.0, target_dollars=300.0)
    defaults.update(overrides)
    return sv.Proposal(**defaults)


# --------------------------------------------------------------- the worked
# example from spec 002 itself, hand-computed:
#   goal = 25000 * 1.2 / 100 = $300
#   risk $140, cushion_remaining $1000 -> not >= cushion, not > half cushion (500)
#   day_pnl_so_far = 0 -> not >= goal, not < 0 -> falls through to clean GO
#   worst_case_balance = account_size + day_pnl_so_far - (risk * mult)
#                       = 25000 + 0 - 140*1.0 = 24860
#   cushion_after_loss  = cushion_remaining - (risk * mult) = 1000 - 140 = 860
#   days_to_recover_at_goal = ceil(140 / 300) = 1
#   still_on_track = cushion_after_loss (860) > 0 -> True

class TestSpecWorkedExample:
    def test_matches_spec_002_worked_example_exactly(self):
        c = _campaign()
        p = _proposal(risk_dollars=140.0)
        result = sv.check(c, p)
        assert result.verdict == "GO"
        assert result.size_multiplier == 1.0
        assert result.worst_case_balance == pytest.approx(24_860.0)
        assert result.cushion_after_loss == pytest.approx(860.0)
        assert result.days_to_recover_at_goal == pytest.approx(1.0)
        assert result.still_on_track is True

    def test_sentence_matches_spec_002_deliverable_text(self):
        c = _campaign()
        p = _proposal(risk_dollars=140.0)
        result = sv.check(c, p)
        rendered = sv.sentence(c, p, result)
        assert "risk $140" in rendered
        assert "worst case $24,860" in rendered
        assert "cushion $860 left" in rendered
        assert "1 day back to on-track at $300/day goal" in rendered
        assert "Still on track: YES" in rendered
        assert "Verdict: GO (1x)." in rendered


# --------------------------------------------------------- priority ordering
# first-hit-wins: each higher-priority rule must win even when a lower rule
# would also fire, proving the checks are ordered, not independently OR'd.

class TestPriorityOrder:
    def test_cooloff_wins_over_everything_else(self):
        """Fault: a red-day size-down or a beyond-goal stop masking the
        cooloff gate. Cooloff must win even when every other rule would also
        say stop for a different reason."""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        c = _campaign(
            cooloff_until=tomorrow,
            consecutive_red_days=5,
            day_pnl_so_far=-500.0,
        )
        p = _proposal(risk_dollars=2_000.0)  # would also trip gate 2
        result = sv.check(c, p)
        assert result.verdict == "NO_TRADE"
        assert "cooloff" in result.reason

    def test_two_red_days_wins_over_cushion_and_goal_rules(self):
        c = _campaign(consecutive_red_days=2, day_pnl_so_far=-10.0)
        p = _proposal(risk_dollars=10.0)  # trivially small, would pass gate 2/3/4
        result = sv.check(c, p)
        assert result.verdict == "NO_TRADE"
        assert "two consecutive red days" in result.reason

    def test_cushion_breach_wins_over_already_at_goal(self):
        c = _campaign(cushion_remaining=100.0, day_pnl_so_far=500.0)  # already past goal
        p = _proposal(risk_dollars=100.0)  # risk >= cushion_remaining
        result = sv.check(c, p)
        assert result.verdict == "NO_TRADE"
        assert "stop-out ends the account" in result.reason

    def test_half_cushion_size_down_wins_over_bet2_size_down(self):
        c = _campaign(cushion_remaining=200.0, day_pnl_so_far=-50.0)
        p = _proposal(risk_dollars=150.0)  # > half of 200 (100), but < 200
        result = sv.check(c, p)
        assert result.verdict == "SIZE_DOWN"
        assert "half the remaining cushion" in result.reason


# ---------------------------------------------------------------- gate rules

class TestCooloffGate:
    def test_cooloff_active_blocks(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        c = _campaign(cooloff_until=tomorrow)
        result = sv.check(c, _proposal())
        assert result.verdict == "NO_TRADE"

    def test_cooloff_today_is_not_active(self):
        """Fault: an off-by-one that treats the cooloff end date itself as
        still blocked. today() < cooloff_until means the boundary date
        itself is clear."""
        today_str = date.today().isoformat()
        c = _campaign(cooloff_until=today_str)
        result = sv.check(c, _proposal())
        assert result.verdict != "NO_TRADE" or "cooloff" not in result.reason

    def test_cooloff_in_past_does_not_block(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        c = _campaign(cooloff_until=yesterday)
        result = sv.check(c, _proposal())
        assert "cooloff" not in result.reason

    def test_cooloff_none_does_not_block(self):
        c = _campaign(cooloff_until=None)
        result = sv.check(c, _proposal())
        assert "cooloff" not in result.reason

    def test_uses_injectable_today_seam(self, monkeypatch):
        """The seam must actually be consulted by check(), not just present
        for decoration."""
        monkeypatch.setattr(sv, "_today", lambda: date(2020, 1, 1))
        c = _campaign(cooloff_until="2020-06-01")
        result = sv.check(c, _proposal())
        assert result.verdict == "NO_TRADE"
        assert "cooloff" in result.reason


class TestConsecutiveRedDaysGate:
    def test_zero_red_days_does_not_block(self):
        c = _campaign(consecutive_red_days=0)
        result = sv.check(c, _proposal())
        assert result.verdict != "NO_TRADE"

    def test_one_red_day_does_not_block(self):
        """Fault: off-by-one treating 1 consecutive red day as already
        triggering the cooloff-equivalent stop; spec says >= 2."""
        c = _campaign(consecutive_red_days=1)
        result = sv.check(c, _proposal())
        assert "two consecutive red days" not in result.reason

    def test_two_red_days_blocks(self):
        c = _campaign(consecutive_red_days=2)
        result = sv.check(c, _proposal())
        assert result.verdict == "NO_TRADE"
        assert "two consecutive red days" in result.reason

    def test_more_than_two_red_days_blocks(self):
        c = _campaign(consecutive_red_days=5)
        result = sv.check(c, _proposal())
        assert result.verdict == "NO_TRADE"


class TestCushionGate:
    def test_risk_equal_to_cushion_blocks(self):
        """Fault: using > instead of >= would let a stop-out that consumes
        the entire remaining cushion slip through as SIZE_DOWN."""
        c = _campaign(cushion_remaining=140.0)
        p = _proposal(risk_dollars=140.0)
        result = sv.check(c, p)
        assert result.verdict == "NO_TRADE"

    def test_risk_over_cushion_blocks(self):
        c = _campaign(cushion_remaining=100.0)
        p = _proposal(risk_dollars=150.0)
        result = sv.check(c, p)
        assert result.verdict == "NO_TRADE"

    def test_risk_just_over_half_cushion_sizes_down(self):
        c = _campaign(cushion_remaining=1_000.0)
        p = _proposal(risk_dollars=501.0)
        result = sv.check(c, p)
        assert result.verdict == "SIZE_DOWN"
        expected_mult = 0.5 * 1_000.0 / 501.0
        assert result.size_multiplier == pytest.approx(expected_mult)

    def test_risk_exactly_half_cushion_does_not_size_down_on_this_gate(self):
        """Fault: using >= instead of > on the half-cushion check would
        needlessly size down a trade that risks exactly half the cushion."""
        c = _campaign(cushion_remaining=1_000.0, day_pnl_so_far=0.0)
        p = _proposal(risk_dollars=500.0)
        result = sv.check(c, p)
        assert "half the remaining cushion" not in result.reason

    def test_size_down_multiplier_never_exceeds_one(self):
        c = _campaign(cushion_remaining=1_000.0)
        p = _proposal(risk_dollars=900.0)  # far more than half, but under cushion
        result = sv.check(c, p)
        assert result.size_multiplier <= 1.0


class TestAlreadyAtGoalGate:
    def test_pnl_exactly_at_goal_stops(self):
        """Fault: using > instead of >= would let a trader who exactly hit
        the goal keep trading."""
        c = _campaign(account_size=25_000.0, daily_goal_pct=1.2, day_pnl_so_far=300.0)
        result = sv.check(c, _proposal())
        assert result.verdict == "NO_TRADE"
        assert "goal already banked" in result.reason

    def test_pnl_above_goal_stops(self):
        c = _campaign(account_size=25_000.0, daily_goal_pct=1.2, day_pnl_so_far=350.0)
        result = sv.check(c, _proposal())
        assert result.verdict == "NO_TRADE"

    def test_pnl_just_under_goal_does_not_stop_on_this_gate(self):
        c = _campaign(account_size=25_000.0, daily_goal_pct=1.2, day_pnl_so_far=299.0)
        result = sv.check(c, _proposal())
        assert "goal already banked" not in result.reason


class TestBet2Gate:
    def test_negative_pnl_sizes_down_to_fixed_half(self):
        c = _campaign(day_pnl_so_far=-1.0)
        result = sv.check(c, _proposal(risk_dollars=140.0))
        assert result.verdict == "SIZE_DOWN"
        assert result.size_multiplier == pytest.approx(sv.BET2_MULT)
        assert result.size_multiplier == pytest.approx(0.5)

    def test_zero_pnl_does_not_trigger_bet2(self):
        """Fault: treating day_pnl_so_far == 0 as 'a loss today'. Spec says
        strictly < 0."""
        c = _campaign(day_pnl_so_far=0.0)
        result = sv.check(c, _proposal())
        assert "recovery bet" not in result.reason

    def test_positive_pnl_under_goal_is_clean_go(self):
        c = _campaign(account_size=25_000.0, daily_goal_pct=1.2, day_pnl_so_far=100.0)
        result = sv.check(c, _proposal())
        assert result.verdict == "GO"
        assert result.size_multiplier == 1.0


# ------------------------------------------------------ core invariant: 1.0x
# ceiling, no path ever scales size up.

class TestNeverScalesUp:
    def test_size_multiplier_never_exceeds_one_across_all_verdicts(self):
        """Sweep a battery of campaigns/proposals across every gate and
        assert the ceiling holds everywhere, not just in the branch that
        happens to be tested."""
        scenarios = [
            _campaign(),
            _campaign(consecutive_red_days=2),
            _campaign(cushion_remaining=50.0),
            _campaign(day_pnl_so_far=500.0),
            _campaign(day_pnl_so_far=-50.0),
            _campaign(cooloff_until=(date.today() + timedelta(days=3)).isoformat()),
        ]
        for c in scenarios:
            for risk in (1.0, 50.0, 140.0, 900.0, 5_000.0):
                try:
                    p = _proposal(risk_dollars=risk)
                except ValueError:
                    continue
                result = sv.check(c, p)
                assert result.size_multiplier <= 1.0

    def test_survivalcheck_rejects_multiplier_above_one(self):
        """Fault injection directly on the invariant: constructing a
        SurvivalCheck with size_multiplier > 1.0 must raise, proving the
        ceiling is enforced by the type, not just by check()'s callers being
        well-behaved."""
        with pytest.raises(ValueError):
            sv.SurvivalCheck(
                verdict="GO",
                size_multiplier=1.5,
                worst_case_balance=1.0,
                cushion_after_loss=1.0,
                days_to_recover_at_goal=0.0,
                still_on_track=True,
                reason="tilt",
            )

    def test_survivalcheck_rejects_negative_multiplier(self):
        with pytest.raises(ValueError):
            sv.SurvivalCheck(
                verdict="NO_TRADE",
                size_multiplier=-0.1,
                worst_case_balance=1.0,
                cushion_after_loss=1.0,
                days_to_recover_at_goal=0.0,
                still_on_track=True,
                reason="bad",
            )

    def test_no_trade_multiplier_is_zero_not_positive(self):
        c = _campaign(consecutive_red_days=2)
        result = sv.check(c, _proposal())
        assert result.size_multiplier == 0.0


# ------------------------------------------------- missing input never == 0

class TestMissingInputsRaise:
    """Fault: treating None as 0 anywhere in Campaign/Proposal construction.
    CLAUDE.md rule 3 — absence is never numeric zero."""

    def test_campaign_none_account_size_raises(self):
        with pytest.raises(ValueError):
            _campaign(account_size=None)

    def test_campaign_none_daily_goal_pct_raises(self):
        with pytest.raises(ValueError):
            _campaign(daily_goal_pct=None)

    def test_campaign_none_cushion_remaining_raises(self):
        with pytest.raises(ValueError):
            _campaign(cushion_remaining=None)

    def test_campaign_none_day_pnl_so_far_raises(self):
        with pytest.raises(ValueError):
            _campaign(day_pnl_so_far=None)

    def test_campaign_none_consecutive_red_days_raises(self):
        with pytest.raises(ValueError):
            _campaign(consecutive_red_days=None)

    def test_campaign_zero_consecutive_red_days_is_valid_not_an_error(self):
        """Control: 0 is a legitimate value distinct from missing (None).
        Proves the None-check isn't a falsy-check in disguise."""
        c = _campaign(consecutive_red_days=0)
        assert c.consecutive_red_days == 0

    def test_proposal_none_risk_dollars_raises(self):
        with pytest.raises(ValueError):
            _proposal(risk_dollars=None)

    def test_proposal_none_target_dollars_raises(self):
        with pytest.raises(ValueError):
            _proposal(target_dollars=None)


# -------------------------------------------------- other __post_init__ validation

class TestValidation:
    def test_negative_account_size_raises(self):
        with pytest.raises(ValueError):
            _campaign(account_size=-1.0)

    def test_zero_account_size_raises(self):
        with pytest.raises(ValueError):
            _campaign(account_size=0.0)

    def test_daily_goal_pct_below_documented_range_raises(self):
        with pytest.raises(ValueError):
            _campaign(daily_goal_pct=0.5)

    def test_daily_goal_pct_above_documented_range_raises(self):
        with pytest.raises(ValueError):
            _campaign(daily_goal_pct=3.0)

    def test_negative_cushion_remaining_raises(self):
        with pytest.raises(ValueError):
            _campaign(cushion_remaining=-1.0)

    def test_negative_consecutive_red_days_raises(self):
        with pytest.raises(ValueError):
            _campaign(consecutive_red_days=-1)

    def test_malformed_cooloff_until_raises(self):
        with pytest.raises(ValueError):
            _campaign(cooloff_until="not-a-date")

    def test_zero_risk_dollars_raises(self):
        """A trade proposal with zero risk is not a real trade and would
        also divide-by-zero in the half-cushion sizing branch."""
        with pytest.raises(ValueError):
            _proposal(risk_dollars=0.0)

    def test_negative_risk_dollars_raises(self):
        with pytest.raises(ValueError):
            _proposal(risk_dollars=-10.0)

    def test_zero_target_dollars_raises(self):
        with pytest.raises(ValueError):
            _proposal(target_dollars=0.0)

    def test_unknown_verdict_raises(self):
        with pytest.raises(ValueError):
            sv.SurvivalCheck(
                verdict="MAYBE",
                size_multiplier=1.0,
                worst_case_balance=1.0,
                cushion_after_loss=1.0,
                days_to_recover_at_goal=0.0,
                still_on_track=True,
                reason="bad",
            )


# --------------------------------------------------------------- worst-case
# arithmetic, hand-computed against a second example distinct from the spec's.

class TestWorstCaseArithmetic:
    def test_sized_down_worst_case_uses_effective_risk_not_nominal_risk(self):
        """Hand-computed example:
        account_size=10,000, daily_goal_pct=2.0 -> goal = 200
        cushion_remaining=1,000, day_pnl_so_far=-5 (bet-2 gate fires)
        proposal risk_dollars=140 -> BET2_MULT halves it to an effective
        risk of 70.
        worst_case_balance = 10,000 + (-5) - 70 = 9,925
        cushion_after_loss  = 1,000 - 70 = 930
        days_to_recover_at_goal = ceil(70 / 200) = 1
        still_on_track = 930 > 0 -> True
        """
        c = _campaign(
            account_size=10_000.0, daily_goal_pct=2.0,
            cushion_remaining=1_000.0, day_pnl_so_far=-5.0,
        )
        p = _proposal(risk_dollars=140.0)
        result = sv.check(c, p)
        assert result.verdict == "SIZE_DOWN"
        assert result.size_multiplier == pytest.approx(0.5)
        assert result.worst_case_balance == pytest.approx(9_925.0)
        assert result.cushion_after_loss == pytest.approx(930.0)
        assert result.days_to_recover_at_goal == pytest.approx(1.0)
        assert result.still_on_track is True

    def test_no_trade_worst_case_reflects_zero_risk_taken(self):
        """No trade happens on NO_TRADE, so nothing about the account
        worsens: worst_case_balance == current balance, cushion unchanged."""
        c = _campaign(
            account_size=25_000.0, daily_goal_pct=1.2,
            cushion_remaining=1_000.0, day_pnl_so_far=50.0,
            consecutive_red_days=2,
        )
        p = _proposal(risk_dollars=140.0)
        result = sv.check(c, p)
        assert result.verdict == "NO_TRADE"
        assert result.worst_case_balance == pytest.approx(25_050.0)
        assert result.cushion_after_loss == pytest.approx(1_000.0)
        assert result.days_to_recover_at_goal == pytest.approx(0.0)

    def test_days_to_recover_rounds_up_to_whole_trading_days(self):
        """Hand-computed: effective_risk=140, goal=300 -> 140/300 = 0.4667,
        which must round UP to 1 whole trading day, not truncate to 0."""
        c = _campaign(account_size=25_000.0, daily_goal_pct=1.2, day_pnl_so_far=0.0)
        p = _proposal(risk_dollars=140.0)
        result = sv.check(c, p)
        assert result.days_to_recover_at_goal == 1.0

    def test_days_to_recover_exact_multiple_does_not_overcount(self):
        """Hand-computed: effective_risk=300, goal=300 -> exactly 1 day, not
        rounded up to 2 by an off-by-one in the ceiling."""
        c = _campaign(account_size=25_000.0, daily_goal_pct=1.2, day_pnl_so_far=0.0,
                       cushion_remaining=1_000.0)
        p = _proposal(risk_dollars=300.0)
        result = sv.check(c, p)
        assert result.days_to_recover_at_goal == 1.0


# --------------------------------------------------------- regime-independence

class TestNoRegimeConfidenceInput:
    def test_campaign_and_proposal_carry_no_confidence_field(self):
        """Fault: someone later adds a confidence/regime field and lets it
        influence sizing, violating spec 002's explicit independence rule."""
        campaign_fields = {f for f in sv.Campaign.__dataclass_fields__}
        proposal_fields = {f for f in sv.Proposal.__dataclass_fields__}
        for forbidden in ("confidence", "regime", "regime_confidence"):
            assert forbidden not in campaign_fields
            assert forbidden not in proposal_fields
