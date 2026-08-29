"""
test_legal_moves.py — TIGHTEN is illegal, not merely unsupported, when
cfg['trail_mult'] is None. Three things are exercised:

  1. move generation (transitions.py): a SINGLE_NAME build never carries
     TIGHTEN in legal_moves, and a CASH_INDEX build always does.
  2. move selection (tablebase.py): Cell.best(legal=...) cannot return an
     action outside the mask, even when that action wins the unmasked
     argmax. Fault-injected: the mask is temporarily bypassed to prove the
     test would fail without it (see the report, not this file — the
     bypass is not left behind).
  3. the iff invariant: assert_legal_moves_consistent raises the moment
     legal_moves and r_if_tightened disagree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))

import frozen_policy                                        # noqa: E402
from state_space import COARSENINGS, State                  # noqa: E402
from tablebase import Action, Cell, Transition               # noqa: E402
from transitions import (                                    # noqa: E402
    assert_legal_moves_consistent,
    transitions_from_sessions,
)

from test_transitions import _real_sessions                  # noqa: E402


EDGES = COARSENINGS["minimal"]

SOME_STATE = State(r_b=0, hold_b=0, atr_b=0, carry_b=0, time_block="MORNING", weekend=False)


# ---------------------------------------------------------------------------
# 1. Move generation: SINGLE_NAME excludes TIGHTEN, CASH_INDEX includes it.
# ---------------------------------------------------------------------------

def test_single_name_build_excludes_tighten_from_legal_moves():
    sessions = _real_sessions("NVDA", 6)
    cfg = frozen_policy.POLICIES["SINGLE_NAME"]
    assert cfg["trail_mult"] is None

    transitions, _ = transitions_from_sessions(sessions, cfg, edges=EDGES, min_paths=1)
    assert transitions, "expected at least one transition from real NVDA sessions"
    assert all(Action.TIGHTEN not in t.legal_moves for t in transitions)
    assert all(t.r_if_tightened is None for t in transitions)


def test_cash_index_build_includes_tighten_in_legal_moves():
    sessions = _real_sessions("QQQ", 3)
    cfg = frozen_policy.POLICIES["CASH_INDEX"]
    assert cfg["trail_mult"] is not None

    transitions, _ = transitions_from_sessions(sessions, cfg, edges=EDGES, min_paths=1)
    assert transitions, "expected at least one transition from real QQQ sessions"
    assert all(Action.TIGHTEN in t.legal_moves for t in transitions)
    assert all(isinstance(t.r_if_tightened, float) for t in transitions)


# ---------------------------------------------------------------------------
# 2. The load-bearing one: an illegal action can never win the masked argmax,
# even when it wins the unmasked one.
# ---------------------------------------------------------------------------

def test_masked_best_excludes_illegal_tighten_even_when_it_would_win():
    cell = Cell(q_hold=0.1, q_tighten=0.9, q_exit=0.2,
                n_paths=50, episodes=frozenset({"E:1"}))

    # Sanity: rig the cell so TIGHTEN wins the unmasked argmax.
    unmasked_action, _ = cell.best()
    assert unmasked_action is Action.TIGHTEN

    masked_action, masked_value = cell.best(legal=frozenset({Action.HOLD, Action.EXIT}))
    assert masked_action is not Action.TIGHTEN
    assert masked_action is Action.EXIT          # the higher of the two legal Qs
    assert masked_value == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# 3. The iff-assertion fires when legal_moves and r_if_tightened disagree.
# ---------------------------------------------------------------------------

def test_iff_assertion_fires_when_tighten_legal_but_no_r_if_tightened():
    bad = Transition(
        episode="E:1", phase=0, state=SOME_STATE, realized_r=0.0,
        next_state=None, r_if_tightened=None, terminal_r=0.0,
        legal_moves=frozenset({Action.HOLD, Action.TIGHTEN, Action.EXIT}),
    )
    with pytest.raises(ValueError, match="disagreement"):
        assert_legal_moves_consistent([bad])


def test_iff_assertion_fires_when_tighten_illegal_but_r_if_tightened_present():
    bad = Transition(
        episode="E:1", phase=0, state=SOME_STATE, realized_r=0.0,
        next_state=None, r_if_tightened=0.5, terminal_r=0.0,
        legal_moves=frozenset({Action.HOLD, Action.EXIT}),
    )
    with pytest.raises(ValueError, match="disagreement"):
        assert_legal_moves_consistent([bad])


def test_iff_assertion_does_not_fire_when_consistent():
    ok = Transition(
        episode="E:1", phase=0, state=SOME_STATE, realized_r=0.0,
        next_state=None, r_if_tightened=None, terminal_r=0.0,
        legal_moves=frozenset({Action.HOLD, Action.EXIT}),
    )
    assert_legal_moves_consistent([ok])   # must not raise
