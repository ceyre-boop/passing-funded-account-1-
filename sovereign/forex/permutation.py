"""sovereign/forex/permutation.py — sign-flip permutation on paired per-unit differences.

The distribution-free companion to the SPRT (spec 045 §6): paired unit ΔR has a
point mass at zero (units where the candidate agreed with the incumbent) and
stop-driven tails, so the normal model is misspecified. Under H0 the sign of
each unit's ΔR is exchangeable; the one-sided p-value is the fraction of
sign-flipped draws whose mean is at least the observed mean.

Every parameter is required. No defaults.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


class PermutationError(ValueError):
    pass


@dataclass(frozen=True)
class SignFlipResult:
    observed_mean: float
    p_one_sided: float          # P(mean_perm >= observed) under sign exchangeability, with +1 correction
    draws: int
    seed: int
    n: int
    n_nonzero: int              # units that deviated at all
    null_q05: float
    null_q95: float


def sign_flip_test(deltas, *, draws: int, seed: int) -> SignFlipResult:
    d = np.asarray(deltas, dtype=float)
    if d.ndim != 1 or d.size == 0:
        raise PermutationError("deltas must be a non-empty 1-D sequence")
    if not np.all(np.isfinite(d)):
        raise PermutationError("deltas contain a non-finite value — a NaN cannot be permuted into a p-value")
    if not (isinstance(draws, int) and draws >= 1000):
        raise PermutationError(f"draws={draws!r}: need an int >= 1000 for a usable p-value")
    if not isinstance(seed, int):
        raise PermutationError(f"seed={seed!r} must be an int")
    rng = np.random.default_rng(seed)
    obs = float(d.mean())
    signs = rng.choice(np.array([-1.0, 1.0]), size=(draws, d.size))
    means = (signs * d).mean(axis=1)
    # +1 correction: the observed arrangement is itself one of the permutations
    p = (float(np.sum(means >= obs)) + 1.0) / (draws + 1.0)
    return SignFlipResult(observed_mean=obs, p_one_sided=p, draws=draws, seed=seed, n=int(d.size),
                          n_nonzero=int(np.sum(d != 0.0)), null_q05=float(np.quantile(means, 0.05)),
                          null_q95=float(np.quantile(means, 0.95)))
