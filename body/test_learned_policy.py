"""body/test_learned_policy.py — the agent, and the guards on its scoring."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from body.learned_policy import (FLAT, LONG, N_PARAMS, SHORT,  # noqa: E402
                                 actions, activity, score, unpack)
from body.features import N_FEATURES  # noqa: E402

X = np.array([[1.0] + [0.0] * (N_FEATURES - 1),
              [-1.0] + [0.0] * (N_FEATURES - 1),
              [0.0] * N_FEATURES])


def theta(w0=0.0, b=0.0, thr=0.5):
    t = np.zeros(N_PARAMS); t[0] = w0; t[N_FEATURES] = b; t[N_FEATURES + 1] = thr
    return t


def test_it_can_learn_to_never_trade():
    """A learner that cannot decline is only learning WHEN to act, not whether."""
    assert activity(theta(w0=1.0, thr=99.0), X) == 0.0


def test_it_can_act_both_ways():
    a = actions(theta(w0=1.0, thr=0.5), X)
    assert a[0] == LONG and a[1] == SHORT and a[2] == FLAT


def test_threshold_is_always_symmetric():
    """abs() on the threshold — a negative one would invert the flat band and
    make the policy trade only when it is least confident."""
    a = actions(theta(w0=1.0, thr=-0.5), X)
    b = actions(theta(w0=1.0, thr=0.5), X)
    assert (a == b).all()


def test_score_is_per_opportunity_not_per_trade():
    """Per-trade would reward taking one lucky trade and stopping."""
    rl = np.array([10.0, 0.0, 0.0]); rs = np.zeros(3)
    s = score(theta(w0=1.0, thr=0.5), X, rl, rs)
    assert s == pytest.approx(10.0 / 3)


def test_declining_costs_nothing_and_earns_nothing():
    rl = np.array([5.0, 5.0, 5.0]); rs = np.zeros(3)
    assert score(theta(w0=1.0, thr=99.0), X, rl, rs) == 0.0


def test_wrong_param_count_raises():
    with pytest.raises(ValueError):
        unpack(np.zeros(N_PARAMS - 1))
