"""Card 019 — invariant suite for scenarios.py (and its mechanical adapter
policy_from_scenarios in stockfish_exit.py).

Each test is one row of 019's table. The DoD is the fault-injection loop: every
row's fault column, applied temporarily, must turn exactly that row red. See
MUTATION_LOG.md for the evidence.

Fixtures are plain Python literals per the spec — no JSON, no parquet, no
network, no wall clock (every freshness check passes `now` explicitly).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from scenarios import (ALL_SCENARIOS, DEFAULT_MAX_AGE_MIN, Scenario, ScenarioError,
                       ScenarioSet, unreadable)
from stockfish_exit import CONSERVATISM, policy_from_scenarios

T0 = datetime(2026, 8, 7, 14, 0, 0, tzinfo=timezone.utc)
TS = T0.isoformat()


def scenario(name: str, prob: float, policy: str, **kw) -> Scenario:
    base = dict(name=name, probability=prob, confidence=0.6,
                expected_duration_min=60, evidence=["because the tape says so"],
                invalidation=["a decisive break the other way"],
                affected_symbols=["NVDA"], recommended_policy=policy)
    base.update(kw)
    return Scenario(**base)


def scenario_set(pairs: list[tuple[str, float, str]], **kw) -> ScenarioSet:
    base = dict(ts=TS, symbol="NVDA", source="test",
                scenarios=[scenario(n, p, pol) for n, p, pol in pairs])
    base.update(kw)
    return ScenarioSet(**base)


# ---------------------------------------------------------------- validation

def test_prob_must_sum_to_one():
    with pytest.raises(ScenarioError):
        scenario_set([("bull_continuation", 0.6, "RIDE"),
                      ("range_consolidation", 0.3, "DEFEND")])   # sums to 0.9


def test_rejects_negative_prob():
    with pytest.raises(ScenarioError):
        scenario("bull_continuation", -0.1, "RIDE")
    with pytest.raises(ScenarioError):
        scenario("bull_continuation", 0.5, "RIDE", confidence=-0.2)


def test_rejects_prob_above_one():
    with pytest.raises(ScenarioError):
        scenario("bull_continuation", 1.1, "RIDE")
    with pytest.raises(ScenarioError):
        scenario("bull_continuation", 0.5, "RIDE", confidence=1.5)


def test_rejects_nan_and_inf():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ScenarioError):
            scenario("bull_continuation", bad, "RIDE")
        with pytest.raises(ScenarioError):
            scenario("bull_continuation", 0.5, "RIDE", confidence=bad)
        with pytest.raises(ScenarioError):
            scenario("bull_continuation", 0.5, "RIDE", expected_duration_min=bad)


def test_rejects_unknown_scenario_name():
    with pytest.raises(ScenarioError):
        scenario("melt_up_forever", 1.0, "RIDE")


def test_rejects_duplicate_scenario_names():
    with pytest.raises(ScenarioError):
        scenario_set([("bull_continuation", 0.5, "RIDE"),
                      ("bull_continuation", 0.5, "DEFEND")])


def test_rejects_empty_set():
    # match pins the DEDICATED non-empty check. Without it this test stays green
    # even with that check deleted, because an empty set also happens to trip the
    # probability-sum check (sum == 0) — found by the 019 fault-injection loop.
    with pytest.raises(ScenarioError, match="empty scenario set"):
        ScenarioSet(ts=TS, symbol="NVDA", source="test", scenarios=[])


def test_rejects_missing_invalidation_or_evidence():
    with pytest.raises(ScenarioError):
        scenario("bull_continuation", 1.0, "RIDE", invalidation=[])
    with pytest.raises(ScenarioError):
        scenario("bull_continuation", 1.0, "RIDE", evidence=[])


# ----------------------------------------------------------------- freshness

def test_freshness_at_exact_boundary_is_fresh():
    """The boundary is INCLUSIVE: a set exactly max_age_min old is still fresh.
    is_fresh() uses `<=` (scenarios.py); the off-by-one fault is flipping it to
    `<`, which would refuse a set at the precise moment its age equals the
    limit it declared."""
    s = scenario_set([("bull_continuation", 1.0, "RIDE")], max_age_min=30)
    at_boundary = T0 + timedelta(minutes=30)
    assert s.age_min(at_boundary) == pytest.approx(30.0)
    assert s.is_fresh(at_boundary) is True
    assert s.is_fresh(at_boundary + timedelta(seconds=1)) is False


# ------------------------------------------------- distribution -> one policy

def test_decisive_picks_top_scenario_policy():
    s = scenario_set([("bull_continuation", 0.60, "RIDE"),
                      ("range_consolidation", 0.40, "DEFEND")])
    assert s.is_decisive()
    policy, why = policy_from_scenarios(s, fallback="STATIC", now=T0)
    assert policy == "RIDE"
    assert "bull_continuation" in why


def test_indecisive_picks_most_conservative_represented_policy():
    s = scenario_set([("bull_continuation", 0.40, "RIDE"),
                      ("range_consolidation", 0.35, "HARVEST"),
                      ("failed_breakout", 0.25, "DEFEND")])
    assert not s.is_decisive()
    policy, why = policy_from_scenarios(s, fallback="STATIC", now=T0)
    represented = {sc.recommended_policy for sc in s.scenarios}
    assert policy == min(represented, key=lambda p: CONSERVATISM[p])
    assert policy == "DEFEND"          # DEFEND(2) < HARVEST(3) < RIDE(4)


def test_never_averages_into_unrepresented_policy():
    """Whatever the spread, the resolved policy is one a scenario actually
    recommended — never a blend, never a middle-ground policy absent from the
    set. (Stale/None inputs return the caller's fallback by contract; those
    paths are exercised separately and excluded here.)"""
    spreads = [
        [("bull_continuation", 0.60, "RIDE"), ("range_consolidation", 0.40, "DEFEND")],
        [("bull_continuation", 0.40, "RIDE"), ("range_consolidation", 0.35, "HARVEST"),
         ("failed_breakout", 0.25, "DEFEND")],
        [("bull_continuation", 0.34, "RIDE"), ("bear_continuation", 0.33, "RIDE"),
         ("range_consolidation", 0.33, "HARVEST")],
        [("failed_breakout", 0.51, "HARVEST"), ("range_consolidation", 0.49, "DEFEND")],
    ]
    for pairs in spreads:
        s = scenario_set(pairs)
        policy, _ = policy_from_scenarios(s, fallback="STATIC", now=T0)
        assert policy in {sc.recommended_policy for sc in s.scenarios}, pairs


def test_unreadable_is_flat_three_way():
    s = unreadable("NVDA", "no directional evidence", source="test", ts=TS)
    probs = [sc.probability for sc in s.scenarios]
    assert len(probs) == 3
    for p in probs:
        assert p == pytest.approx(1.0 / 3.0, abs=2e-6)
    assert max(probs) - min(probs) <= 2e-6      # flat, not a disguised lean
    assert not s.is_decisive()                  # the honest "I don't know"
    for sc in s.scenarios:
        assert sc.name in ALL_SCENARIOS
