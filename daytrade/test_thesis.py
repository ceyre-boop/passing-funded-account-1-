"""Card 019 — invariant suite for thesis.py (and its mechanical adapter
urgency_for_thesis in stockfish_exit.py).

Each test is one row of 019's table; the fault-injection loop in MUTATION_LOG.md
is the DoD. Fixtures are plain Python literals; no wall clock is consulted —
expiry is driven by explicit now_et strings.
"""
from __future__ import annotations

from typing import get_args

import pytest

from thesis import TERMINAL, Condition, Thesis, ThesisError, ThesisState
from stockfish_exit import urgency_for_thesis


def make_thesis(expires_at_et: str | None = "15:45") -> Thesis:
    return Thesis(
        symbol="NVDA", opened_ts="2026-08-07T13:31:00+00:00",
        statement="relative strength held while the peer group broke down",
        confirmations=[Condition("c1", "holds above the prior close all morning"),
                       Condition("c2", "outperforms SMH on the session")],
        weakeners=[Condition("w1", "closes back below session VWAP")],
        invalidators=[Condition("i1", "trades below 203 and fails to reclaim"),
                      Condition("i2", "peer strength thesis refuted by tape")],
        expires_at_et=expires_at_et)


# ------------------------------------------------------------- the machine

def test_legal_transitions_only():
    """evaluate() moves only along the documented precedence:
    invalidation > expiry > weakening > confirmation > pending."""
    t = make_thesis()
    assert t.state == "THESIS_PENDING"
    assert t.evaluate({"c1"}, now_et="10:00") == "THESIS_PENDING"          # partial confirm
    assert t.evaluate({"c1", "c2"}, now_et="10:05") == "THESIS_CONFIRMED"  # all confirms
    assert t.evaluate({"c1", "c2", "w1"}, now_et="10:10") == "THESIS_WEAKENING"  # weak > confirm
    assert t.evaluate({"w1"}, now_et="16:00") == "THESIS_EXPIRED"          # expiry > weakening

    t2 = make_thesis()
    # invalidation outranks everything else firing in the same call
    assert t2.evaluate({"i1", "w1", "c1", "c2"}, now_et="16:00") == "THESIS_INVALIDATED"

    for st in ("THESIS_INVALIDATED", "THESIS_EXPIRED"):
        assert st in TERMINAL


def test_idempotent_on_repeated_observation():
    t = make_thesis()
    first = t.evaluate({"c1", "c2"}, now_et="10:00")
    depth = len(t.history)
    second = t.evaluate({"c1", "c2"}, now_et="10:01")
    assert first == second == "THESIS_CONFIRMED"
    assert len(t.history) == depth        # a non-transition writes no history


def test_invalidated_is_sticky():
    t = make_thesis()
    assert t.evaluate({"i1"}, now_et="10:00") == "THESIS_INVALIDATED"
    depth = len(t.history)
    # a later perfect confirmation must NOT resurrect it
    assert t.evaluate({"c1", "c2"}, now_et="10:30") == "THESIS_INVALIDATED"
    assert t.evaluate(set(), now_et="10:45") == "THESIS_INVALIDATED"
    assert len(t.history) == depth


def test_expired_is_sticky():
    t = make_thesis(expires_at_et="15:45")
    assert t.evaluate(set(), now_et="15:45") == "THESIS_EXPIRED"
    depth = len(t.history)
    # even an earlier clock plus full confirmation must not revive it
    assert t.evaluate({"c1", "c2"}, now_et="10:00") == "THESIS_EXPIRED"
    assert len(t.history) == depth


def test_invalidation_outranks_expiry_when_both_fire():
    """'The reason died' is more informative than 'the clock ran out' — when
    both are true in one evaluate() call, INVALIDATED wins."""
    t = make_thesis(expires_at_et="15:45")
    assert t.evaluate({"i2"}, now_et="16:00") == "THESIS_INVALIDATED"
    assert t.history[-1]["to"] == "THESIS_INVALIDATED"


def test_weakening_is_not_sticky():
    """WEAKENING reverts when the weakener is no longer observed. This locks in
    the current, probe-confirmed behavior as an explicit contract (019:79):
    only INVALIDATED and EXPIRED are terminal — a weakener is a warning, not a
    verdict, and the state machine recomputes from each observation set."""
    t = make_thesis()
    assert t.evaluate({"c1", "c2", "w1"}, now_et="10:00") == "THESIS_WEAKENING"
    assert t.evaluate({"c1", "c2"}, now_et="10:05") == "THESIS_CONFIRMED"
    # and it can oscillate again — WEAKENING is re-enterable, not latched
    assert t.evaluate({"c1", "c2", "w1"}, now_et="10:10") == "THESIS_WEAKENING"


def test_unknown_observed_condition_key_raises():
    t = make_thesis()
    with pytest.raises(ThesisError):
        t.evaluate({"nobody_wrote_this_down"}, now_et="10:00")
    # and it raises even when mixed with legitimate keys
    with pytest.raises(ThesisError):
        t.evaluate({"c1", "typo_key"}, now_et="10:00")


# ------------------------------------------------------- mechanical adapter

def test_urgency_for_thesis_covers_all_states():
    """Every ThesisState × both armed booleans maps to a (urgency, why) pair in
    the engine's existing bounded vocabulary — no None-by-omission, no new
    mechanical authority."""
    states = get_args(ThesisState)
    assert len(states) == 5               # if the vocabulary grows, this test must too
    for state in states:
        for armed in (False, True):
            out = urgency_for_thesis(state, armed=armed)
            assert isinstance(out, tuple) and len(out) == 2, (state, armed)
            urgency, why = out
            assert urgency in (None, "tighten", "exit"), (state, armed, urgency)
            assert isinstance(why, str) and why, (state, armed)
    # unarmed terminal states must not be allowed to flatten
    for state in TERMINAL:
        urgency, _ = urgency_for_thesis(state, armed=False)
        assert urgency == "tighten"
        urgency, _ = urgency_for_thesis(state, armed=True)
        assert urgency == "exit"
    with pytest.raises(ValueError):
        urgency_for_thesis("THESIS_ZOMBIE", armed=False)
