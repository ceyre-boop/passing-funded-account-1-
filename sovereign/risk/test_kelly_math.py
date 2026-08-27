"""Characterisation + invariant tests for sovereign/risk/kelly_math.py.

CONTEXT (2026-08-26): this file used to live in test_kelly_engine.py, stubbing
`layer2`/`config.loader` in sys.modules just to reach the three pure functions that
don't actually need them. Those functions have been split out to
sovereign/risk/kelly_math.py (no `layer2`/`contracts`/`config` imports at all), and
sovereign/risk/layers/kelly.py — the real Layer 2 ceiling on risk_engine.decide()'s
sizing path — now imports directly from here. No stubbing needed: this module is
importable exactly as shipped, unlike kelly_engine.py (see test_kelly_engine.py).

NaN FIX (2026-08-26): `fractional_kelly` used to silently return `ceiling` (the
MAXIMUM position size) on any NaN input, because NaN comparisons are always False in
Python so every guard (`win_rate<=0`, `win_rate>=1`, `f_star<=0`) got skipped. NaN is
now treated as a malformed input like the other degenerate cases and returns `floor`.
See TestNaNInputsReturnFloorNotCeiling below — this replaces the old
test_kelly_engine.py tests that asserted (and merely documented) the ceiling bug as
"current behaviour"; those assertions would now fail, which is the point: this file
is the fault-injection that must fail if the NaN hazard ever comes back.
"""
import math

import pytest

from sovereign.risk.kelly_math import (
    fractional_kelly,
    hoeffding_win_rate,
    sample_complexity_confidence,
)


def test_kelly_math_module_is_importable_as_shipped():
    """Unlike kelly_engine.py, this module has zero external dependencies beyond
    the stdlib `math` — it must import cleanly with no stubbing, in a subprocess
    with this repo's real sys.path, so layers/kelly.py can rely on it."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import sovereign.risk.kelly_math"],
        cwd=__file__.rsplit("/sovereign/", 1)[0],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


class TestFractionalKellyFormula:
    """Does fractional_kelly actually compute f* = (p.b - q)/b, quarter-Kelly'd?"""

    def test_hand_computed_example_within_floor_ceiling_band(self):
        # p=0.52, b=1.0 (1:1 R:R): f* = (0.52*1 - 0.48)/1 = 0.04
        # quarter-Kelly: 0.04 * 0.25 = 0.01, inside [floor=0.005, ceiling=0.04] unclamped.
        result = fractional_kelly(win_rate=0.52, avg_win_r=1.0, avg_loss_r=1.0)
        assert result == pytest.approx(0.01, abs=1e-9)

    def test_hand_computed_example_2to1_reward_risk(self):
        # p=0.60, b=2.0: f* = (0.6*2 - 0.4)/2 = (1.2 - 0.4)/2 = 0.4
        # quarter-Kelly: 0.4 * 0.25 = 0.10 -> clamped to default ceiling 0.04.
        result = fractional_kelly(win_rate=0.60, avg_win_r=2.0, avg_loss_r=1.0)
        assert result == pytest.approx(0.04, abs=1e-9)

    def test_unclamped_practical_kelly_matches_formula_exactly(self):
        """Custom floor/ceiling wide enough to observe the raw f*×fraction, to
        directly verify the formula rather than the clamp."""
        p, win_b, loss_b, fraction = 0.55, 1.5, 1.0, 0.25
        b = win_b / loss_b
        q = 1 - p
        f_star = (p * b - q) / b
        expected = f_star * fraction
        result = fractional_kelly(
            win_rate=p, avg_win_r=win_b, avg_loss_r=loss_b,
            fraction=fraction, floor=0.0, ceiling=1.0,
        )
        assert result == pytest.approx(expected, rel=1e-9)


class TestQuarterKellyFractionBoundary:
    """Is it actually capped at 25% of full Kelly (the `fraction` parameter), not
    some other fraction?"""

    def test_fraction_scales_linearly(self):
        kwargs = dict(win_rate=0.55, avg_win_r=1.5, avg_loss_r=1.0, floor=0.0, ceiling=1.0)
        half = fractional_kelly(fraction=0.50, **kwargs)
        quarter = fractional_kelly(fraction=0.25, **kwargs)
        assert half == pytest.approx(quarter * 2, rel=1e-9)

    def test_full_kelly_fraction_1_0_vs_quarter_kelly_fraction_0_25(self):
        kwargs = dict(win_rate=0.70, avg_win_r=3.0, avg_loss_r=1.0, floor=0.0, ceiling=10.0)
        full = fractional_kelly(fraction=1.0, **kwargs)
        quarter = fractional_kelly(fraction=0.25, **kwargs)
        assert quarter == pytest.approx(full * 0.25, rel=1e-9)


class TestCeilingClamp:
    """Regardless of what Kelly computes, is a hard ceiling actually enforced?"""

    def test_large_edge_clamps_to_default_ceiling_0_04(self):
        # p=0.9, b=5 -> f* = (0.9*5 - 0.1)/5 = 0.88; ×0.25 = 0.22, way above ceiling.
        result = fractional_kelly(win_rate=0.9, avg_win_r=5.0, avg_loss_r=1.0)
        assert result == pytest.approx(0.04)

    def test_custom_ceiling_is_respected(self):
        result = fractional_kelly(
            win_rate=0.9, avg_win_r=5.0, avg_loss_r=1.0, ceiling=0.02,
        )
        assert result == pytest.approx(0.02)

    def test_boundary_just_under_and_over_ceiling(self):
        # Tune win_rate so f*×0.25 lands just under vs just over a tight ceiling.
        ceiling = 0.02
        just_under = fractional_kelly(
            win_rate=0.55, avg_win_r=1.0, avg_loss_r=1.0, ceiling=ceiling, floor=0.0,
        )
        just_over = fractional_kelly(
            win_rate=0.90, avg_win_r=1.0, avg_loss_r=1.0, ceiling=ceiling, floor=0.0,
        )
        assert just_under <= ceiling
        assert just_over == ceiling  # clamped


class TestDegenerateInputsReturnFloorNotCrash:
    """CLAUDE.md non-negotiable #3: 'Never silently default unavailable values to
    numeric zero.' fractional_kelly does not raise on ANY degenerate input — it
    always returns a number, which is a real gap against rule #3 (documented, not
    fixed here — fixing it would mean deciding what should raise, a scope call
    beyond 'structure and wiring only'). These tests characterise the code's actual
    current behaviour for the non-NaN degenerate cases; NaN is covered separately
    below since that path's behaviour was the actively dangerous one and has been
    fixed.
    """

    def test_win_rate_zero_silently_returns_floor(self):
        assert fractional_kelly(win_rate=0.0, avg_win_r=2.0, avg_loss_r=1.0) == 0.005

    def test_win_rate_one_silently_returns_floor(self):
        assert fractional_kelly(win_rate=1.0, avg_win_r=2.0, avg_loss_r=1.0) == 0.005

    def test_avg_loss_r_zero_silently_returns_floor(self):
        assert fractional_kelly(win_rate=0.6, avg_win_r=2.0, avg_loss_r=0.0) == 0.005

    def test_avg_loss_r_negative_silently_returns_floor(self):
        assert fractional_kelly(win_rate=0.6, avg_win_r=2.0, avg_loss_r=-1.0) == 0.005

    def test_negative_expected_value_returns_hard_zero_not_floor(self):
        # This path DOES distinguish itself: f*<=0 returns 0.0 exactly (not the
        # floor), i.e. "don't bet" is representable.
        assert fractional_kelly(win_rate=0.3, avg_win_r=1.0, avg_loss_r=1.0) == 0.0


class TestNaNInputsReturnFloorNotCeiling:
    """FIX VERIFICATION (2026-08-26). Before the fix, NaN comparisons being always
    False meant every guard was skipped and `max(floor, min(ceiling, nan))`
    evaluated to `ceiling` — the function silently sized the MAXIMUM position on
    garbage input, the worst possible failure mode for a sizing function. Now NaN
    is checked explicitly up front and treated like the other malformed-input
    cases: returns `floor`. Any regression back to the ceiling-on-NaN behaviour
    must fail every test in this class."""

    def test_nan_win_rate_returns_floor(self):
        result = fractional_kelly(win_rate=float("nan"), avg_win_r=2.0, avg_loss_r=1.0)
        assert math.isnan(result) is False
        assert result == 0.005

    def test_nan_avg_win_r_returns_floor(self):
        result = fractional_kelly(win_rate=0.6, avg_win_r=float("nan"), avg_loss_r=1.0)
        assert result == 0.005

    def test_nan_avg_loss_r_returns_floor(self):
        result = fractional_kelly(win_rate=0.6, avg_win_r=2.0, avg_loss_r=float("nan"))
        assert result == 0.005

    def test_nan_never_returns_ceiling_regardless_of_which_ceiling_is_configured(self):
        # Regression guard: a NaN input must not silently return whatever the
        # caller's ceiling happens to be, at any ceiling value.
        result = fractional_kelly(
            win_rate=float("nan"), avg_win_r=2.0, avg_loss_r=1.0, ceiling=0.99,
        )
        assert result != 0.99
        assert result == 0.005


class TestHoeffdingWinRateDegenerateInputs:
    """hoeffding_win_rate is the confidence-interval correction fed into Kelly
    upstream (see layers/kelly.py). It also silently defaults rather than raising."""

    def test_zero_trades_silently_returns_uninformative_prior(self):
        assert hoeffding_win_rate(0.7, n_trades=0) == 0.50

    def test_negative_trades_also_silently_returns_prior(self):
        assert hoeffding_win_rate(0.7, n_trades=-5) == 0.50

    def test_result_is_clamped_to_0_10_0_95(self):
        # Very few trades -> huge gamma -> corrected win_rate would go negative,
        # but it's clamped to 0.10 rather than raising or going negative.
        result = hoeffding_win_rate(0.05, n_trades=1)
        assert result == pytest.approx(0.10)

    def test_lower_mode_is_conservative_vs_upper(self):
        lower = hoeffding_win_rate(0.6, n_trades=100, mode="lower")
        upper = hoeffding_win_rate(0.6, n_trades=100, mode="upper")
        assert lower < 0.6 < upper


class TestSampleComplexityConfidence:
    def test_zero_trades_returns_zero_confidence(self):
        assert sample_complexity_confidence(0) == 0.0

    def test_confidence_saturates_at_1_0(self):
        assert sample_complexity_confidence(10_000_000) == 1.0

    def test_confidence_is_monotonic_in_n_trades(self):
        low = sample_complexity_confidence(10)
        high = sample_complexity_confidence(1000)
        assert high >= low
