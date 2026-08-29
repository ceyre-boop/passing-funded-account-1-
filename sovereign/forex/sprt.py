"""Wald SPRT for paired differences under a normal model with known sigma.

The test compares H0: mean = 0 against H1: mean = delta, using per-observation
log-likelihood ratios ``delta / sigma**2 * (d_i - delta / 2)``.  It accepts H1
at ``A = ln((1 - beta) / alpha)`` and H0 at
``B = ln(beta / (1 - alpha))``.  This imports Fishtest's discipline exactly:
only ACCEPT_H1 may replace the incumbent; INCONCLUSIVE keeps the incumbent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
import math


class SprtError(ValueError):
    """Raised when an SPRT input or computation is invalid."""


class Decision(str, Enum):
    """Possible outcomes of a sequential probability ratio test."""

    ACCEPT_H1 = "ACCEPT_H1"
    ACCEPT_H0 = "ACCEPT_H0"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class SprtResult:
    """The decision and complete state at the point consumption stopped."""

    decision: Decision
    stop_index: int | None
    llr_final: float
    llr_trace: tuple[float, ...]
    n_consumed: int
    n_available: int
    upper_bound: float
    lower_bound: float
    delta: float
    sigma: float
    alpha: float
    beta: float


def _validate_parameters(*, delta: float, sigma: float, alpha: float, beta: float) -> None:
    """Check the fixed design parameters before observations are consumed."""
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise SprtError(f"sigma must be finite and greater than zero; got sigma={sigma!r}")
    if not math.isfinite(delta) or delta <= 0.0:
        raise SprtError(f"delta must be finite and greater than zero; got delta={delta!r}")
    if not math.isfinite(alpha) or not 0.0 < alpha < 0.5:
        raise SprtError(f"alpha must be finite and in (0, 0.5); got alpha={alpha!r}")
    if not math.isfinite(beta) or not 0.0 < beta < 0.5:
        raise SprtError(f"beta must be finite and in (0, 0.5); got beta={beta!r}")


def _bounds(*, alpha: float, beta: float) -> tuple[float, float]:
    """Return Wald's upper and lower log-likelihood-ratio bounds."""
    return math.log((1.0 - beta) / alpha), math.log(beta / (1.0 - alpha))


def sprt(
    deltas: Sequence[float], *, delta: float, sigma: float, alpha: float, beta: float
) -> SprtResult:
    """Consume chronological paired differences until a Wald bound is crossed."""
    _validate_parameters(delta=delta, sigma=sigma, alpha=alpha, beta=beta)
    n_available = len(deltas)
    if n_available == 0:
        raise SprtError(f"deltas must not be empty; got deltas={deltas!r}")

    upper_bound, lower_bound = _bounds(alpha=alpha, beta=beta)
    llr_scale = delta / (sigma * sigma)
    cumulative_llr = 0.0
    trace: list[float] = []

    for index, observed_delta in enumerate(deltas, start=1):
        if not math.isfinite(observed_delta):
            raise SprtError(
                "each delta must be finite; "
                f"got deltas[{index - 1}]={observed_delta!r}"
            )
        cumulative_llr += llr_scale * (observed_delta - delta / 2.0)
        if not math.isfinite(cumulative_llr):
            raise SprtError(
                "cumulative log-likelihood ratio must be finite; "
                f"got cumulative_llr={cumulative_llr!r}"
            )
        trace.append(cumulative_llr)
        if cumulative_llr >= upper_bound:
            return SprtResult(
                decision=Decision.ACCEPT_H1,
                stop_index=index,
                llr_final=cumulative_llr,
                llr_trace=tuple(trace),
                n_consumed=index,
                n_available=n_available,
                upper_bound=upper_bound,
                lower_bound=lower_bound,
                delta=delta,
                sigma=sigma,
                alpha=alpha,
                beta=beta,
            )
        if cumulative_llr <= lower_bound:
            return SprtResult(
                decision=Decision.ACCEPT_H0,
                stop_index=index,
                llr_final=cumulative_llr,
                llr_trace=tuple(trace),
                n_consumed=index,
                n_available=n_available,
                upper_bound=upper_bound,
                lower_bound=lower_bound,
                delta=delta,
                sigma=sigma,
                alpha=alpha,
                beta=beta,
            )

    return SprtResult(
        decision=Decision.INCONCLUSIVE,
        stop_index=None,
        llr_final=cumulative_llr,
        llr_trace=tuple(trace),
        n_consumed=n_available,
        n_available=n_available,
        upper_bound=upper_bound,
        lower_bound=lower_bound,
        delta=delta,
        sigma=sigma,
        alpha=alpha,
        beta=beta,
    )


def expected_stop_steps_null(*, delta: float, sigma: float, alpha: float, beta: float) -> int:
    """Return the exact H0-bound crossing step when every difference is zero."""
    _validate_parameters(delta=delta, sigma=sigma, alpha=alpha, beta=beta)
    _, lower_bound = _bounds(alpha=alpha, beta=beta)
    null_step_size = delta * delta / (2.0 * sigma * sigma)
    return math.ceil(abs(lower_bound) / null_step_size)
