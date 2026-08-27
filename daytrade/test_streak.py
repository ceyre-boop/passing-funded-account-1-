#!/usr/bin/env python3
"""Spec 004 (second half): streak tracker + the cooloff hard rule.

Fault-injection style: every test below should FAIL if the rule it names
stops being enforced, not just pass on the happy path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streak
from streak import StreakState, DayResultError, update, is_armed, blocks_trading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import friction_ladder  # noqa: E402


def _day(date, outcome, pnl=0.0, result_R=0.0, distance_to_goal=0.0,
         campaign_cushion=1000.0):
    return {"date": date, "outcome": outcome, "pnl": pnl, "result_R": result_R,
            "distance_to_goal": distance_to_goal, "campaign_cushion": campaign_cushion}


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "streak.json"


# ------------------------------------------------------------- constants pinned to friction_ladder

def test_cooloff_constants_match_friction_ladder():
    """The whole ladder's 93-98% pass probability is derived assuming these
    exact numbers. If either drifts from friction_ladder.py's, the campaign
    math and the enforced rule have silently diverged."""
    assert streak.COOLOFF_AFTER == friction_ladder.COOLOFF_AFTER == 2
    assert streak.COOLOFF_DAYS == friction_ladder.COOLOFF_DAYS == 5


# ------------------------------------------------------------- trigger

def test_two_consecutive_red_days_starts_cooloff(state_path):
    update(_day("2026-08-24", "red"), path=state_path)
    state = update(_day("2026-08-25", "red"), path=state_path)
    assert state.cooloff_until is not None, \
        "two consecutive red days must start a cooloff — it did not"


def test_single_red_day_does_not_trigger_cooloff(state_path):
    state = update(_day("2026-08-24", "red"), path=state_path)
    assert state.cooloff_until is None


def test_red_green_red_does_not_trigger_cooloff(state_path):
    """Non-consecutive reds must not trigger it — the rule is CONSECUTIVE."""
    update(_day("2026-08-24", "red"), path=state_path)
    update(_day("2026-08-25", "green"), path=state_path)
    state = update(_day("2026-08-26", "red"), path=state_path)
    assert state.cooloff_until is None


# ------------------------------------------------------------- the predicate actually blocks

def test_blocks_trading_true_during_active_cooloff(state_path):
    update(_day("2026-08-24", "red"), path=state_path)
    state = update(_day("2026-08-25", "red"), path=state_path)
    day_after_trigger = "2026-08-26"
    assert blocks_trading(state, day_after_trigger) is True, \
        "runner must be blocked the day after a cooloff triggers"
    assert is_armed(state, day_after_trigger) is False


def test_is_armed_true_before_any_cooloff(state_path):
    state = update(_day("2026-08-24", "green"), path=state_path)
    assert is_armed(state, "2026-08-25") is True
    assert blocks_trading(state, "2026-08-25") is False


def test_is_armed_true_once_cooloff_date_reached(state_path):
    update(_day("2026-08-24", "red"), path=state_path)  # Mon
    state = update(_day("2026-08-25", "red"), path=state_path)  # Tue -> triggers
    # 5 trading days forward from 2026-08-25 (Tue): Wed,Thu,Fri,Mon,Tue -> 2026-09-01
    assert state.cooloff_until == "2026-09-01"
    assert blocks_trading(state, "2026-08-31") is True   # Mon before it lifts
    assert is_armed(state, "2026-09-01") is True          # lifts exactly on schedule


# ------------------------------------------------------------- cannot be cleared early

def test_green_day_during_cooloff_does_not_clear_it_early(state_path):
    """This is the real rule per friction_ladder.py: `cool` only decrements
    per trading-day tick and is untouched by outcome — a green day inside the
    window does NOT reset or shorten it. Only the scheduled date does."""
    update(_day("2026-08-24", "red"), path=state_path)
    state = update(_day("2026-08-25", "red"), path=state_path)
    cooloff_until_before = state.cooloff_until

    state = update(_day("2026-08-26", "green"), path=state_path)
    assert state.cooloff_until == cooloff_until_before, \
        "a green day during cooloff must not clear or shorten it"
    assert blocks_trading(state, "2026-08-27") is True


def test_fresh_trigger_during_active_cooloff_extends_not_shortens(state_path):
    update(_day("2026-08-24", "red"), path=state_path)
    state = update(_day("2026-08-25", "red"), path=state_path)
    first_cooloff_until = state.cooloff_until

    # Two more consecutive reds land during the cooloff window (data can
    # still arrive even though the runner should have refused to trade).
    update(_day("2026-08-26", "red"), path=state_path)
    state = update(_day("2026-08-27", "red"), path=state_path)
    assert state.cooloff_until >= first_cooloff_until
    assert state.cooloff_until > first_cooloff_until


# ------------------------------------------------------------- streak bookkeeping

def test_green_streak_and_longest_streak(state_path):
    update(_day("2026-08-24", "green"), path=state_path)
    update(_day("2026-08-25", "green"), path=state_path)
    state = update(_day("2026-08-26", "green"), path=state_path)
    assert state.green_streak == 3
    assert state.longest_streak == 3

    state = update(_day("2026-08-27", "red"), path=state_path)
    assert state.green_streak == 0
    assert state.longest_streak == 3  # longest survives the break


def test_flat_day_is_neutral(state_path):
    update(_day("2026-08-24", "green"), path=state_path)
    before = update(_day("2026-08-25", "green"), path=state_path)
    state = update(_day("2026-08-26", "flat"), path=state_path)
    assert state.green_streak == before.green_streak
    assert state.consecutive_red_days == before.consecutive_red_days
    assert state.days_since_red == before.days_since_red
    assert state.recent_outcomes == before.recent_outcomes


def test_rolling_20d_winrate_ignores_flat_days(state_path):
    update(_day("2026-08-24", "green"), path=state_path)
    update(_day("2026-08-25", "red"), path=state_path)
    update(_day("2026-08-26", "flat"), path=state_path)
    state = update(_day("2026-08-27", "green"), path=state_path)
    # 2 green out of 3 TRADED days (flat excluded from the denominator)
    assert state.rolling_20d_winrate == pytest.approx(2 / 3)


def test_cumulative_R_and_day_pnl_pass_through(state_path):
    update(_day("2026-08-24", "green", pnl=300.0, result_R=1.5), path=state_path)
    state = update(_day("2026-08-25", "red", pnl=-150.0, result_R=-0.8), path=state_path)
    assert state.cumulative_R == pytest.approx(0.7)
    assert state.day_pnl == -150.0


def test_distance_to_goal_and_campaign_cushion_pass_through(state_path):
    state = update(_day("2026-08-24", "green", distance_to_goal=5.0,
                         campaign_cushion=860.0), path=state_path)
    assert state.distance_to_goal == 5.0
    assert state.campaign_cushion == 860.0


# ------------------------------------------------------------- durability

def test_state_persists_across_load(state_path):
    update(_day("2026-08-24", "red"), path=state_path)
    update(_day("2026-08-25", "red"), path=state_path)
    reloaded = streak.load_state(state_path)
    assert reloaded.cooloff_until is not None
    assert state_path.exists()


def test_state_file_has_no_leftover_tmp_files(state_path):
    update(_day("2026-08-24", "green"), path=state_path)
    leftovers = list(state_path.parent.glob(state_path.name + ".*.tmp"))
    assert leftovers == []


# ------------------------------------------------------------- missing fields never default to 0

@pytest.mark.parametrize("missing_field", list(streak.REQUIRED_FIELDS))
def test_missing_field_raises_not_defaults_to_zero(state_path, missing_field):
    day_result = _day("2026-08-24", "green")
    del day_result[missing_field]
    with pytest.raises(DayResultError):
        update(day_result, path=state_path)
    # And nothing was written as a side effect of the failed call.
    assert not state_path.exists()


def test_invalid_outcome_rejected(state_path):
    with pytest.raises(DayResultError):
        update(_day("2026-08-24", "purple"), path=state_path)


def test_invalid_date_rejected(state_path):
    with pytest.raises(DayResultError):
        update(_day("not-a-date", "green"), path=state_path)


def test_non_dict_day_result_rejected(state_path):
    with pytest.raises(DayResultError):
        update(["not", "a", "dict"], path=state_path)  # type: ignore[arg-type]
