"""daytrade/test_plan_keys.py — a typo'd plan key must fail loud on the live path.

THE DEFECT THIS PINS
    `state_from_plan` collects the exit-policy params out of the plan by
    whitelist comprehension:

        explicit = {k: plan[k] for k in ("trail_mult", "be_arm_frac",
                                         "hold_past_tp2") if k in plan}

    A key spelled `trail_multiplier` is therefore not collected at all, and the
    engine runs its own default instead of the plan's intent. No error, no log
    line, no difference visible until the trade behaves wrongly. `goal_frac`
    silently becomes 0.5; `flatten_et` silently means "never flatten".

    That is the confident-wrong-answer class rather than the loud-failure class,
    and it sat on the LIVE path. `load_plan` checked that REQUIRED keys were
    PRESENT and never checked that present keys were RECOGNISED.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import runner  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "data" / "daytrade" / "plan.example.json"

# (typo, the field it was meant to be)
TYPOS = [
    ("trail_multiplier", "trail_mult"),
    ("goal_frac", "goal_fraction"),
    ("flatten_et", "flatten_at_et"),
    ("be_arm_frack", "be_arm_frac"),
    ("exit_polcy", "exit_policy"),
    ("hold_past_tp_2", "hold_past_tp2"),
]


def _base() -> dict:
    return json.loads(EXAMPLE.read_text())


def _write(tmp_path: Path, plan: dict) -> Path:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    return p


def test_the_shipped_example_still_loads(tmp_path):
    """Guard against the fix being too strict: plan.example.json carries
    _what/_levels/_flatten/_source as annotations and must keep working."""
    assert runner.load_plan(_write(tmp_path, _base()))


@pytest.mark.parametrize("typo,intended", TYPOS)
def test_typod_key_raises_on_load(tmp_path, typo, intended):
    plan = _base()
    plan.pop(intended, None)
    plan[typo] = 2.0
    with pytest.raises(ValueError) as e:
        runner.load_plan(_write(tmp_path, plan))
    assert typo in str(e.value)


@pytest.mark.parametrize("typo,intended", TYPOS)
def test_the_error_names_the_field_it_meant(tmp_path, typo, intended):
    """It fires at 09:31. Loud is necessary; useful is the point."""
    plan = _base()
    plan.pop(intended, None)
    plan[typo] = 2.0
    with pytest.raises(ValueError) as e:
        runner.load_plan(_write(tmp_path, plan))
    assert f"did you mean {intended!r}" in str(e.value)


def test_unknown_key_with_no_near_match_still_raises(tmp_path):
    plan = _base()
    plan["moon_phase"] = 3
    with pytest.raises(ValueError, match="unrecognised field"):
        runner.load_plan(_write(tmp_path, plan))


def test_underscore_annotations_are_allowed(tmp_path):
    plan = _base()
    plan["_note"] = "why this trade exists"
    assert runner.load_plan(_write(tmp_path, plan))


def test_state_from_plan_also_refuses(tmp_path):
    """Defence in depth: a plan dict reaching the engine by another route --
    not through load_plan -- is checked at the comprehension itself."""
    plan = runner.load_plan(_write(tmp_path, _base()))
    plan["trail_multiplier"] = 2.0
    with pytest.raises(ValueError, match="unrecognised field"):
        runner.state_from_plan(plan)


def test_derived_keys_survive_a_round_trip(tmp_path):
    """load_plan writes risk_per_share into the plan; validating again after
    derivation must not reject the engine's own work."""
    plan = runner.load_plan(_write(tmp_path, _base()))
    assert "risk_per_share" in plan
    runner.validate_plan_keys(plan)          # must not raise


def test_every_required_field_is_a_known_field():
    """A REQUIRED key missing from KNOWN_PLAN_KEYS would make every valid plan
    unloadable — the failure mode of the fix itself."""
    assert set(runner.REQUIRED) <= runner.KNOWN_PLAN_KEYS


def test_the_three_exit_params_are_known_fields():
    """The exact keys the whitelist comprehension reads must be accepted, or the
    silent-default bug returns wearing a different hat."""
    assert {"trail_mult", "be_arm_frac", "hold_past_tp2"} <= runner.KNOWN_PLAN_KEYS
