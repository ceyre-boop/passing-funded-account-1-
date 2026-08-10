"""Card 014 (Gate 4) — shadow tournament + regret grader.

Containment is the load-bearing invariant: a ShadowAction must be PHYSICALLY
unable to travel the execution path (type-level, not convention), and a regret
report must never crown a hindsight winner. Fault rows in
specs/014_MUTATION_LOG.md (driver: mutation_check_014.py).
"""
from __future__ import annotations

import pytest

from regret import RegretError, RegretReport, grade
from shadow import ShadowAction, ShadowContainment, run_shadow
from stockfish_exit import Action, TradeState, apply_action

PLAN = {"symbol": "NVDA", "direction": 1, "entry": 200.0, "qty": 100.0,
        "sl": 199.0, "tp1": 201.0, "tp2": 202.14, "trail_dist": 0.5,
        "goal_fraction": 0.5}

# A path with a real peak-and-fade so DEFEND (no trail) and RIDE (wide trail)
# genuinely diverge, and MFE > realized for the regret fixture.
CYCLES = [(200.4, "09:35"), (201.2, "09:40"), (202.5, "09:45"),
          (203.8, "09:50"), (203.1, "09:55"), (202.4, "10:00"),
          (201.9, "10:05")]


# -------------------------------------------------------------- containment

def test_shadow_action_kind_access_raises():
    sa = ShadowAction(hypothetical_kind="EXIT_ALL", policy_name="DEFEND",
                      reason="hypothetical")
    with pytest.raises(ShadowContainment):
        _ = sa.kind


def test_shadow_action_cannot_reach_apply_action():
    """The one funnel reads .kind before anything else — a shadow action dies
    at the door, loudly and by name."""
    s = TradeState(direction=1, entry=200.0, qty=100, price=201.0, sl=199.0,
                   tp1=201.0, tp2=202.14, trail_dist=0.5)
    sa = ShadowAction(hypothetical_kind="MOVE_SL", policy_name="RIDE", sl=200.5)
    with pytest.raises(ShadowContainment):
        apply_action(s, sa)
    assert s.sl == 199.0 and s.state_revision == 0        # nothing happened


def test_shadow_serialization_is_unmistakable():
    d = ShadowAction(hypothetical_kind="EXIT_ALL", policy_name="DEFEND").to_dict()
    assert d["shadow"] is True
    assert "hypothetical_kind" in d and "kind" not in d
    # and the authoritative shape has neither marker
    a = Action("EXIT_ALL", reason="real")
    assert not hasattr(a, "shadow") and not hasattr(a, "hypothetical_kind")


# ------------------------------------------------------------- the tournament

def test_shadow_is_deterministic_and_identically_fed():
    a = run_shadow(PLAN, CYCLES, ["DEFEND", "RIDE", "HARVEST"])
    b = run_shadow(PLAN, CYCLES, ["DEFEND", "RIDE", "HARVEST"])
    assert {k: (v.realized_r, v.final_sl, v.final_stage) for k, v in a.items()} \
        == {k: (v.realized_r, v.final_sl, v.final_stage) for k, v in b.items()}
    for r in a.values():
        assert len(r.actions) == len(CYCLES)              # every policy saw every cycle


def test_policies_genuinely_diverge():
    """Non-vacuity: if every policy produces the same result on a peak-and-fade
    path, the tournament measures nothing."""
    res = run_shadow(PLAN, CYCLES, ["DEFEND", "RIDE", "HARVEST"])
    outcomes = {(round(r.realized_r, 6), r.closed) for r in res.values()}
    assert len(outcomes) > 1, {k: v.realized_r for k, v in res.items()}
    # HARVEST (hold_past_tp2=False) banks the goal and is done
    assert res["HARVEST"].closed


def test_shadow_records_policy_version_identity():
    res = run_shadow(PLAN, CYCLES, ["RIDE"])
    p = res["RIDE"].policy_params
    assert p["trail_mult"] == 1.5 and p["exit_policy"] == "RIDE"


def test_prefix_property_no_future_data():
    """Results over cycles[:k] equal the first k cycles of the full run — the
    fold cannot have peeked ahead."""
    full = run_shadow(PLAN, CYCLES, ["DEFEND", "RIDE"])
    for k in (2, 4, 6):
        part = run_shadow(PLAN, CYCLES[:k], ["DEFEND", "RIDE"])
        for name in ("DEFEND", "RIDE"):
            got = [[a.to_dict() for a in row] for row in part[name].actions]
            want = [[a.to_dict() for a in row] for row in full[name].actions[:k]]
            assert got == want, (name, k)


def test_bad_plan_risk_refused():
    with pytest.raises(ValueError):
        run_shadow({**PLAN, "sl": 200.0}, CYCLES, ["DEFEND"])


# ------------------------------------------------------------------- regret

def test_grade_open_trade_refused():
    res = run_shadow(PLAN, CYCLES, ["DEFEND"])
    with pytest.raises(RegretError):
        grade(trade_id="t1", realized_r=0.5, closed=False, plan=PLAN,
              cycles=CYCLES, shadow_results=res)


def test_grade_metrics_match_hand_computation():
    """risk = 1.0/share. Peak 203.8 -> MFE 3.8R; no cycle below entry -> MAE 0;
    fade 203.8 -> 201.9 -> max drawdown -1.9R."""
    res = run_shadow(PLAN, CYCLES, ["DEFEND", "RIDE"])
    rep = grade(trade_id="t1", realized_r=1.2, closed=True, plan=PLAN,
                cycles=CYCLES, shadow_results=res)
    assert rep.mfe_r == pytest.approx(3.8)
    assert rep.mae_r == pytest.approx(0.0)
    assert rep.giveback_r == pytest.approx(3.8 - 1.2)
    assert rep.captured_frac == pytest.approx(1.2 / 3.8)
    assert rep.max_drawdown_r == pytest.approx(-1.9)
    assert rep.hold_time_min == pytest.approx(30.0)
    assert rep.slippage_r == 0.0 and rep.slippage_basis == "paper"
    assert set(rep.counterfactual_delta_r) == {"DEFEND", "RIDE"}
    assert rep.counterfactual_delta_r["RIDE"] == pytest.approx(
        res["RIDE"].realized_r - 1.2)


def test_report_never_crowns_a_winner():
    """The schema itself must not contain a selection: no winner/best-policy
    field, and the deltas carry every named counterfactual symmetrically."""
    res = run_shadow(PLAN, CYCLES, ["DEFEND", "RIDE", "HARVEST"])
    rep = grade(trade_id="t1", realized_r=1.0, closed=True, plan=PLAN,
                cycles=CYCLES, shadow_results=res)
    d = rep.to_dict()
    assert not any("winner" in k or "best_policy" in k for k in d)
    assert set(d["counterfactual_delta_r"]) == {"DEFEND", "RIDE", "HARVEST"}
    assert grade(trade_id="t1", realized_r=1.0, closed=True, plan=PLAN,
                 cycles=CYCLES, shadow_results=res) == rep   # deterministic
