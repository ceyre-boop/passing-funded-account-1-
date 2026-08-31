"""az/test_odd.py — invariants of the ODD chassis.

Every test here asserts something ODD.md states, and is written so that
deliberately removing the guard makes it fail. Tests marked FAULT INJECTION
construct the violation directly rather than trusting the happy path.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from odd import (  # noqa: E402
    FORBIDDEN_TRANSITIONS, MRM_TABLE, PRECONDITIONS, GateResult, Maneuver, Mrm,
    OddError, Precondition, Recovery, Tier, Truth, UNSET, authorize_entry,
    degrade, evaluate_gate, recover, resolve_mrm,
)

FULL = Recovery(True, True, True, manual_rearm=True)


# ------------------------------------------------------------- fail-closed

def test_unknown_does_not_authorize():
    assert Truth.TRUE.authorizes
    assert not Truth.FALSE.authorizes
    assert not Truth.UNKNOWN.authorizes, "UNKNOWN must fail closed"


def test_unset_has_no_truth_value():
    with pytest.raises(OddError):
        bool(UNSET)


@pytest.mark.parametrize("op", ["__lt__", "__le__", "__gt__", "__ge__"])
def test_unset_refuses_comparison(op):
    """FAULT INJECTION: an UNSET threshold that silently compares is exactly how
    an unmeasured ODD.md proposal becomes a live gate."""
    with pytest.raises(OddError):
        getattr(UNSET, op)(0.5)


def test_unset_threshold_yields_unknown():
    assert Precondition("x", "t").evaluate() is Truth.UNKNOWN


def test_measured_threshold_refuses_to_invent_an_evaluator():
    """A threshold with no comparison logic must raise, not guess."""
    with pytest.raises(OddError):
        Precondition("x", "t", threshold=1.0, value=2.0).evaluate()


# ------------------------------------------------------- the v0.1 gate state

def test_gate_cannot_pass_in_v01():
    g = evaluate_gate()
    assert not g.passed
    assert "t0_unseal" in g.false, "ODD.md §2: T0 unseal authorization absent in v0.1"


def test_every_threshold_is_unset():
    """No measured number may enter the chassis. ODD.md §1: the envelope bounds
    are proposed, not measured."""
    for p in PRECONDITIONS:
        assert p.threshold is UNSET, f"{p.key} carries a baked-in threshold"


def test_gate_blocked_by_a_single_unknown():
    """FAULT INJECTION: all TRUE except one UNKNOWN must still fail."""
    ps = [replace(p, _forced=Truth.TRUE) for p in PRECONDITIONS]
    ps[3] = replace(ps[3], _forced=Truth.UNKNOWN)
    g = evaluate_gate(tuple(ps))
    assert not g.passed and g.unknown == (ps[3].key,)


def test_gate_opens_only_when_all_true():
    g = evaluate_gate(tuple(replace(p, _forced=Truth.TRUE) for p in PRECONDITIONS))
    assert g.passed and not g.false and not g.unknown


# ------------------------------------------------------------ the ratchet

def test_t0_is_sealed_and_unreachable():
    assert Tier.T0_NOMINAL.sealed
    assert recover(Tier.T1_RESTRICTED, FULL) is Tier.T1_RESTRICTED, \
        "T0 is SEALED IN v0.1 and must not be reachable by any recovery"


def test_t3_to_t0_is_forbidden_outright():
    assert (Tier.T3_HALT, Tier.T0_NOMINAL) in FORBIDDEN_TRANSITIONS


def test_recovery_is_one_tier_at_a_time():
    assert recover(Tier.T3_HALT, FULL) is Tier.T2_DEFENSIVE
    assert recover(Tier.T2_DEFENSIVE, FULL) is Tier.T1_RESTRICTED


@pytest.mark.parametrize("missing", ["condition_back_in_domain_full_session",
                                     "root_cause_logged", "one_session_delay_served"])
def test_recovery_requires_every_condition(missing):
    """FAULT INJECTION: drop one requirement, recovery must not happen."""
    r = replace(FULL, **{missing: False})
    assert recover(Tier.T2_DEFENSIVE, r) is Tier.T2_DEFENSIVE


def test_leaving_t3_additionally_requires_manual_rearm():
    r = Recovery(True, True, True, manual_rearm=False)
    assert recover(Tier.T3_HALT, r) is Tier.T3_HALT, "no automatic path out of T3"
    assert recover(Tier.T2_DEFENSIVE, r) is Tier.T1_RESTRICTED, \
        "manual re-arm is a T3-only requirement"


def test_degradation_is_instant_and_ungated():
    assert degrade(Tier.T1_RESTRICTED, Tier.T3_HALT) is Tier.T3_HALT


def test_degrade_cannot_raise_a_tier():
    """FAULT INJECTION: the ratchet is asymmetric; degrade() must never promote."""
    with pytest.raises(OddError):
        degrade(Tier.T3_HALT, Tier.T1_RESTRICTED)


def test_tier_order_is_what_min_max_logic_depends_on():
    """The conservatism helpers use min()/max() over Tier, so a reordering of the
    enum would silently invert the safety logic."""
    assert Tier.T3_HALT < Tier.T2_DEFENSIVE < Tier.T1_RESTRICTED < Tier.T0_NOMINAL
    assert min(Tier.T1_RESTRICTED, Tier.T3_HALT) is Tier.T3_HALT
    assert not Tier.T2_DEFENSIVE.may_open_risk
    assert not Tier.T3_HALT.may_open_risk


# ----------------------------------------------------------------- the MRM

def test_ambiguity_resolves_to_the_more_conservative_maneuver():
    m = resolve_mrm("data_staleness", "orphaned_position")
    assert m.maneuver is Maneuver.FLAT_IMMEDIATELY
    assert m.tier_floor is Tier.T3_HALT


def test_maneuver_order_encodes_conservatism():
    """FAULT INJECTION: resolve_mrm uses max() over Maneuver."""
    assert (Maneuver.HALT_NEW_ENTRIES < Maneuver.HAND_TO_EXIT_NO_REENTRY
            < Maneuver.FLAT_AT_NEXT_LIQUID_WINDOW
            < Maneuver.FLAT_AT_REOPEN_NO_REENTRY < Maneuver.FLAT_IMMEDIATELY)


def test_nobody_override_wins_over_an_overridable_trigger():
    m = resolve_mrm("vendor_price_disagreement", "vol_regime_exits_band")
    assert m.override == "nobody" and not m.overridable


def test_unknown_trigger_raises_rather_than_resolving_mildly():
    """FAULT INJECTION: an unrecognised trigger must never fall through to a
    lenient maneuver."""
    with pytest.raises(OddError):
        resolve_mrm("data_staleness", "not_a_real_trigger")


def test_resolve_mrm_requires_a_trigger():
    with pytest.raises(OddError):
        resolve_mrm()


def test_every_odd_trigger_that_demands_flat_floors_at_least_t2():
    for m in MRM_TABLE.values():
        if m.maneuver is Maneuver.FLAT_IMMEDIATELY:
            assert m.tier_floor is Tier.T3_HALT, f"{m.trigger} flattens but not T3"


# --------------------------------------------------- handoff / authorization

def _open_gate() -> GateResult:
    return evaluate_gate(tuple(replace(p, _forced=Truth.TRUE) for p in PRECONDITIONS))


def test_exit_core_refusal_blocks_entry_even_when_entry_layer_agrees():
    ok, why = authorize_entry(Tier.T1_RESTRICTED, _open_gate(),
                              exit_core_in_domain=Truth.FALSE,
                              entry_layer_in_domain=Truth.TRUE)
    assert not ok and "orphan" in why


def test_unknown_exit_domain_blocks_entry():
    """FAULT INJECTION: UNKNOWN must be as blocking as FALSE here — this is the
    orphaned-position failure ODD.md §1b calls a T3 event."""
    ok, _ = authorize_entry(Tier.T1_RESTRICTED, _open_gate(),
                            exit_core_in_domain=Truth.UNKNOWN,
                            entry_layer_in_domain=Truth.TRUE)
    assert not ok


def test_entry_layer_cannot_open_risk_at_a_defensive_tier():
    ok, why = authorize_entry(Tier.T2_DEFENSIVE, _open_gate(),
                              exit_core_in_domain=Truth.TRUE,
                              entry_layer_in_domain=Truth.TRUE)
    assert not ok and "may not open risk" in why


def test_closed_gate_blocks_entry():
    ok, why = authorize_entry(Tier.T1_RESTRICTED, evaluate_gate(),
                              exit_core_in_domain=Truth.TRUE,
                              entry_layer_in_domain=Truth.TRUE)
    assert not ok and "preconditions" in why


def test_authorization_is_possible_only_with_everything_aligned():
    ok, why = authorize_entry(Tier.T1_RESTRICTED, _open_gate(),
                              exit_core_in_domain=Truth.TRUE,
                              entry_layer_in_domain=Truth.TRUE)
    assert ok and why == "authorized"


def test_the_shipped_system_cannot_authorize_anything_today():
    """The end-to-end statement of ODD.md's current resolution: never."""
    ok, _ = authorize_entry(Tier.T2_DEFENSIVE, evaluate_gate(),
                            exit_core_in_domain=Truth.UNKNOWN,
                            entry_layer_in_domain=Truth.UNKNOWN)
    assert not ok
