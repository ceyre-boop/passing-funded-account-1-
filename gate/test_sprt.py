"""Invariant tests for the known-sigma, one-sided Wald SPRT."""

import math

import pytest

from gate.sprt import Decision, SprtError, expected_stop_steps_null, sprt


def test_hand_computed_cumulative_lambda_trace() -> None:
    """The implementation follows the literal per-observation LLR arithmetic."""
    deltas = [0.5, -0.2, 1.0, 0.3, 0.0, 2.0, 1.5]
    result = sprt(deltas, delta=0.25, sigma=1.0, alpha=0.05, beta=0.20)

    llr_1 = 0.25 * (0.5 - 0.125)
    llr_2 = 0.25 * (-0.2 - 0.125)
    llr_3 = 0.25 * (1.0 - 0.125)
    llr_4 = 0.25 * (0.3 - 0.125)
    llr_5 = 0.25 * (0.0 - 0.125)
    llr_6 = 0.25 * (2.0 - 0.125)
    llr_7 = 0.25 * (1.5 - 0.125)
    hand_computed_trace = [
        llr_1,
        llr_1 + llr_2,
        llr_1 + llr_2 + llr_3,
        llr_1 + llr_2 + llr_3 + llr_4,
        llr_1 + llr_2 + llr_3 + llr_4 + llr_5,
        llr_1 + llr_2 + llr_3 + llr_4 + llr_5 + llr_6,
        llr_1 + llr_2 + llr_3 + llr_4 + llr_5 + llr_6 + llr_7,
    ]

    assert result.llr_trace == pytest.approx(hand_computed_trace, abs=1e-12)
    assert result.decision is Decision.INCONCLUSIVE
    assert result.stop_index is None
    assert result.n_consumed == len(deltas)
    assert result.llr_final == pytest.approx(hand_computed_trace[-1], abs=1e-12)


@pytest.mark.parametrize(
    ("delta", "sigma", "known_steps"),
    [(0.25, 1.0, 50), (0.5, 1.0, 13), (0.25, 2.0, 200)],
)
def test_incumbent_against_itself_accepts_h0_at_null_crossing(
    delta: float, sigma: float, known_steps: int
) -> None:
    """Zero paired differences never accept H1 and stop at the exact null step."""
    alpha = 0.05
    beta = 0.20
    expected_steps = expected_stop_steps_null(
        delta=delta, sigma=sigma, alpha=alpha, beta=beta
    )
    result = sprt([0.0] * 500, delta=delta, sigma=sigma, alpha=alpha, beta=beta)

    direct_formula_steps = math.ceil(
        abs(math.log(beta / (1.0 - alpha))) / (delta**2 / (2.0 * sigma**2))
    )
    assert expected_steps == direct_formula_steps == known_steps
    assert result.decision is Decision.ACCEPT_H0
    assert result.stop_index == expected_steps
    assert result.n_consumed == expected_steps
    assert len(result.llr_trace) == expected_steps


def test_exhausted_observations_are_inconclusive() -> None:
    """A trace inside both bounds remains inconclusive once data run out."""
    result = sprt([0.5, -0.2], delta=0.25, sigma=1.0, alpha=0.05, beta=0.20)

    assert result.decision is Decision.INCONCLUSIVE
    assert result.stop_index is None
    assert result.n_consumed == result.n_available == 2


@pytest.mark.parametrize(
    ("deltas", "delta", "sigma", "alpha", "beta", "offending_name"),
    [
        ([0.0], 0.25, 0.0, 0.05, 0.20, "sigma"),
        ([0.0], 0.25, math.nan, 0.05, 0.20, "sigma"),
        ([0.0], 0.0, 1.0, 0.05, 0.20, "delta"),
        ([0.0], 0.25, 1.0, 0.60, 0.20, "alpha"),
        ([], 0.25, 1.0, 0.05, 0.20, "deltas"),
    ],
)
def test_invalid_inputs_name_the_offending_value(
    deltas: list[float],
    delta: float,
    sigma: float,
    alpha: float,
    beta: float,
    offending_name: str,
) -> None:
    """Configuration and empty-input guards identify the bad argument."""
    with pytest.raises(SprtError, match=offending_name):
        sprt(deltas, delta=delta, sigma=sigma, alpha=alpha, beta=beta)


def test_nan_delta_cannot_yield_inconclusive() -> None:
    """NaN is rejected before its comparison with the Wald bounds."""
    with pytest.raises(SprtError, match=r"deltas\[1\]"):
        sprt([0.0, math.nan], delta=0.25, sigma=1.0, alpha=0.05, beta=0.20)


def test_order_changes_the_first_crossing_index() -> None:
    """Chronological order can change when the same observations stop the test."""
    early_crossing = sprt([12.0, -1.0, 1.0], delta=0.25, sigma=1.0, alpha=0.05, beta=0.20)
    delayed_crossing = sprt([-1.0, 1.0, 12.0], delta=0.25, sigma=1.0, alpha=0.05, beta=0.20)

    assert early_crossing.decision is delayed_crossing.decision is Decision.ACCEPT_H1
    assert early_crossing.stop_index == 1
    assert delayed_crossing.stop_index == 3
    assert early_crossing.n_consumed == 1
    assert delayed_crossing.n_consumed == 3


def test_result_reports_wald_bounds() -> None:
    """The result exposes the exact design bounds used for its decision."""
    alpha = 0.05
    beta = 0.20
    result = sprt([0.0], delta=0.25, sigma=1.0, alpha=alpha, beta=beta)

    assert result.upper_bound == pytest.approx(math.log((1.0 - beta) / alpha))
    assert result.lower_bound == pytest.approx(math.log(beta / (1.0 - alpha)))


@pytest.mark.parametrize("beta", [0.0, 0.5, 0.9, float("nan")])
def test_beta_outside_open_interval_refused(beta):
    with pytest.raises(SprtError):
        sprt([0.1, 0.2], delta=0.25, sigma=1.0, alpha=0.05, beta=beta)
