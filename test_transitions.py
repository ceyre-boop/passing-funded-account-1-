"""
test_transitions.py — the leak test.

transitions.py's job is to hand tablebase.py raw paths that its
leave-one-episode-out guard can actually enforce. A test suite that never
makes `LeakError` fire is not testing the guard, it is testing that the
module imports. This file makes it fire, makes it correctly NOT fire on an
uninvolved episode, and exercises the three constructor-time invariants
(episode identity, phase/chronological monotonicity, and `r_if_tightened`'s
None-vs-float split) with a case designed to trip each one.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))

from bars import Session, load_sessions, ET, BarDataError   # noqa: E402
from ceiling import find_entry                              # noqa: E402
from splits import tune_sessions, TUNE_END                  # noqa: E402
import frozen_policy                                        # noqa: E402
from exit_evaluator import SYM_CLASS                        # noqa: E402

from state_space import COARSENINGS                         # noqa: E402
from tablebase import Tablebase, LeakError                  # noqa: E402
from transitions import assert_phases_monotone, transitions_from_sessions   # noqa: E402


EDGES = COARSENINGS["minimal"]


def _real_sessions(symbol: str, n: int) -> list[Session]:
    """First `n` tune-lane sessions with a real OR-break entry, strictly
    before TUNE_END (tune_sessions() itself includes the boundary day)."""
    try:
        sessions = load_sessions(symbol, "5m", allow_fetch=False)
    except BarDataError:
        pytest.skip(f"no local bar cache for {symbol}")
    tuned = [s for s in tune_sessions(sessions) if s.day < TUNE_END]
    with_entry = [s for s in tuned if find_entry(s) is not None]
    if not with_entry:
        pytest.skip(f"no tune-split entries for {symbol} in the local bar cache")
    return with_entry[:n]


# ---------------------------------------------------------------------------
# The leak test itself, on real transitions.
# ---------------------------------------------------------------------------

def test_leak_fires_for_a_contributing_episode():
    sessions = _real_sessions("NVDA", 6)
    cfg = frozen_policy.POLICIES[SYM_CLASS["NVDA"]]
    transitions, _ = transitions_from_sessions(sessions, cfg, edges=EDGES, min_paths=1)
    assert transitions, "expected at least one transition from real NVDA sessions"

    t0 = transitions[0]
    tb = Tablebase(min_paths=1).build(transitions)

    assert t0.episode in tb.cells[t0.state].episodes
    with pytest.raises(LeakError):
        tb.evaluate(t0.state, scoring_episode=t0.episode)


def test_no_leak_for_a_non_contributing_episode():
    sessions = _real_sessions("NVDA", 6)
    cfg = frozen_policy.POLICIES[SYM_CLASS["NVDA"]]
    transitions, _ = transitions_from_sessions(sessions, cfg, edges=EDGES, min_paths=1)

    t0 = transitions[0]
    tb = Tablebase(min_paths=1).build(transitions)

    outside_episode = t0.episode + "::not-a-real-contributor"
    assert outside_episode not in tb.cells[t0.state].episodes

    action, _value = tb.evaluate(t0.state, scoring_episode=outside_episode)
    assert action is not None   # returned normally; did not raise


# ---------------------------------------------------------------------------
# Episode-identity assertion.
# ---------------------------------------------------------------------------

def test_episode_identity_assertion_fires_on_duplicate_session():
    sessions = _real_sessions("NVDA", 1)
    cfg = frozen_policy.POLICIES[SYM_CLASS["NVDA"]]
    with pytest.raises(ValueError, match="more than one session"):
        transitions_from_sessions(sessions + sessions, cfg, edges=EDGES, min_paths=1)


# ---------------------------------------------------------------------------
# Phase / chronological-order assertion — needs a fabricated session, since
# `phase` is assigned as a list index and would pass no matter what; the
# invariant that can actually break is the bar timestamps behind it.
# ---------------------------------------------------------------------------

def _synthetic_out_of_order_session() -> Session:
    rows: list[tuple] = []

    def bar(hhmm: str, o: float, h: float, l: float, c: float) -> None:
        ts = pd.Timestamp(f"2016-01-05 {hhmm}:00", tz=ET)
        rows.append((ts, {"Open": o, "High": h, "Low": l, "Close": c}))

    # Opening range 09:30-10:00, flat, 7 bars (find_entry needs >= 6).
    for hhmm in ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55", "10:00"):
        bar(hhmm, 100.0, 100.5, 99.5, 100.0)

    # Breakout bar inside the trigger window: closes above the OR high.
    bar("10:05", 100.2, 102.3, 100.1, 102.0)

    # Two post-entry bars, STORED out of chronological order (10:15 before
    # 10:10). Both calm — no stop/tp/trail fires — so the engine stays open
    # through both and the observer records both, in that reversed order.
    bar("10:15", 101.9, 102.1, 101.8, 102.0)
    bar("10:10", 102.0, 102.2, 101.9, 102.0)

    idx = pd.DatetimeIndex([r[0] for r in rows])
    df = pd.DataFrame([r[1] for r in rows], index=idx)
    return Session(symbol="SYNTH", day=date(2016, 1, 5), df=df)


def test_out_of_order_bars_are_refused_by_the_constitution():
    """Reordered BARS never reach the transitions module: a backwards clock trips
    C004 'no stale facts' inside the engine first. Defence in depth — this test
    pins WHERE the refusal happens, so a future refactor that moves the check
    cannot silently drop it."""
    from stockfish_constitution import ConstitutionError
    s = _synthetic_out_of_order_session()
    e = find_entry(s)
    assert e is not None, "synthetic session must produce a real OR-break entry"

    cfg = {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
           "flatten_et": None, "hold_past_tp2": True}
    with pytest.raises(ConstitutionError, match="C004"):
        transitions_from_sessions([s], cfg, edges=EDGES, min_paths=1)


def test_phase_monotonicity_assertion_fires_on_reordered_transitions():
    """The module's OWN guard, exercised where C004 cannot see: transitions
    reordered AFTER extraction. C004 protects the bar stream; this protects the
    record that leaves the module."""
    sessions = _real_sessions("NVDA", 3)
    cfg = frozen_policy.POLICIES["SINGLE_NAME"]
    transitions, _ = transitions_from_sessions(sessions, cfg, edges=EDGES, min_paths=1)

    by_ep = {}
    for t in transitions:
        by_ep.setdefault(t.episode, []).append(t)
    victim = next(v for v in by_ep.values() if len(v) >= 2)
    scrambled = [t for t in transitions if t.episode != victim[0].episode]
    scrambled += list(reversed(victim))

    with pytest.raises(ValueError, match="chronological order"):
        assert_phases_monotone(scrambled)


# ---------------------------------------------------------------------------
# r_if_tightened: None under trail_mult=None, a float under a trailing cfg.
# ---------------------------------------------------------------------------

def test_r_if_tightened_none_for_trail_mult_none_config():
    sessions = _real_sessions("NVDA", 3)
    cfg = frozen_policy.POLICIES["SINGLE_NAME"]
    assert cfg["trail_mult"] is None

    transitions, report = transitions_from_sessions(sessions, cfg, edges=EDGES, min_paths=1)
    assert transitions
    assert all(t.r_if_tightened is None for t in transitions)
    assert report["n_r_if_tightened_none"] == len(transitions)


def test_r_if_tightened_is_a_float_for_a_trailing_config():
    sessions = _real_sessions("QQQ", 3)
    cfg = frozen_policy.POLICIES["CASH_INDEX"]
    assert cfg["trail_mult"] is not None

    transitions, report = transitions_from_sessions(sessions, cfg, edges=EDGES, min_paths=1)
    assert transitions
    assert all(isinstance(t.r_if_tightened, float) for t in transitions)
    assert report["n_r_if_tightened_none"] == 0
