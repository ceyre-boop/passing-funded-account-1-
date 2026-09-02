"""body/learned_policy.py — the agent. Weights, not rules.

WHAT IT IS GIVEN
    The state vector (`body/features.py`), three legal actions, and a score.
    That is the whole framework.

WHAT IT IS NOT GIVEN
    Which features matter. What a good setup looks like. Any threshold anyone
    believes in. `VetoEntryPolicy` was told that range expansion is a reason to
    trade, at a weight I picked — this one is told nothing and has to find out.

    It can also learn NOT to trade: a large `threshold` makes it flat forever,
    and that is a legal, reachable answer. A learner that cannot decline is not
    learning whether to act, only when.

FORM
        score = w . x + b
        LONG  if score >  threshold
        SHORT if score < -threshold
        FLAT  otherwise

    Linear and symmetric on purpose. Linear because 15 parameters over 16,800
    points is already generous and a deeper model would fit noise faster than
    it fits anything else; symmetric because long and short share one model and
    the features are sign-normalized.
"""
from __future__ import annotations

import numpy as np

from body.features import N_FEATURES

N_PARAMS = N_FEATURES + 2          # weights + bias + threshold
FLAT, LONG, SHORT = 0, 1, -1


def unpack(theta):
    theta = np.asarray(theta, dtype=np.float64)
    if theta.shape[-1] != N_PARAMS:
        raise ValueError(f"expected {N_PARAMS} params, got {theta.shape[-1]}")
    return theta[..., :N_FEATURES], theta[..., N_FEATURES], abs(theta[..., N_FEATURES + 1])


def actions(theta, X) -> np.ndarray:
    """Vectorized over every decision point. Returns -1 / 0 / +1."""
    w, b, thr = unpack(theta)
    s = X @ w + b
    return np.where(s > thr, LONG, np.where(s < -thr, SHORT, FLAT)).astype(np.int8)


def score(theta, X, r_long, r_short) -> float:
    """Expected R per DECISION OPPORTUNITY, not per trade.

    Per-trade would reward a policy that takes one lucky trade and stops.
    Dividing by every opportunity it had prices selectivity honestly: declining
    costs nothing and earns nothing, which is the correct incentive for an
    agent that is allowed to sit out."""
    a = actions(theta, X)
    took = np.where(a == LONG, r_long, np.where(a == SHORT, r_short, 0.0))
    return float(took.sum() / len(X)) if len(X) else 0.0


def activity(theta, X) -> float:
    return float(np.mean(actions(theta, X) != FLAT))
