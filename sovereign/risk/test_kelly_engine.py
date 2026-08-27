"""Characterisation + invariant tests for sovereign/risk/kelly_engine.py.

CONTEXT (2026-08-26): CLAUDE.md non-negotiable #4 states kelly_engine.py "computes
proper quarter-Kelly (f* = (p.b - q) / b, capped at 25% of full Kelly)" and that this
control sits on the sizing path. `rg kelly_engine|risk_engine` outside sovereign/
returns nothing — it has zero callers anywhere else in the repo.

FINDING #1 (structural, most important): kelly_engine.py is UNIMPORTABLE as shipped
in this repo. Its module-level imports are:
    from layer2.risk_engine import RiskEngine
    from layer2.dynamic_rr_engine import DynamicRREngine
    from contracts.types import RiskOutput, BiasOutput, RouterOutput
    from config.loader import params
`layer2` does not exist anywhere in this repo (it exists only in the separate
~/quant and ~/quant-master-wt repos this file was copied from). Top-level `config`
does not exist either (only sovereign/risk/config exists, a different package).
`contracts.types` DOES resolve (top-level ./contracts/types.py). So 2 of 4 imports
are dead. `python3 -c "import sovereign.risk.kelly_engine"` raises
ModuleNotFoundError: No module named 'layer2' — see test_module_is_unimportable_as_shipped
below, run as a subprocess with a pristine sys.path (no stubbing) to prove it.

Because of this, EVERYTHING in kelly_engine.py — including the standalone pure
functions this file otherwise characterises — cannot be imported by anything else in
the repo without first stubbing `layer2` and `config.loader`, which is exactly what
this test file does below (documented, isolated, and does not touch the module
itself). Any code path that tries to `import` this module for real (e.g.
sovereign/risk/layers/kelly.py, see test_risk_layers.py) fails the same way.

The `SovereignRiskEngine` class in this file is untestable in any meaningful sense
here: it composes `layer2.risk_engine.RiskEngine` and `layer2.dynamic_rr_engine.
DynamicRREngine`, both of which would have to be fabricated stand-ins with no
relationship to real behaviour. This file therefore characterises ONLY the three
standalone pure functions (`fractional_kelly`, `hoeffding_win_rate`,
`sample_complexity_confidence`), which do not depend on the broken imports for their
logic — only for the module to load at all.
"""
import math
import subprocess
import sys
import types

import pytest


def test_module_is_unimportable_as_shipped():
    """FINDING: kelly_engine.py cannot be imported anywhere in this repo without
    stubbing `layer2` (missing entirely) and `config.loader` (top-level `config`
    package does not exist; only the unrelated sovereign/risk/config package does).
    Run in a subprocess so this repo's real sys.path is used, uncontaminated by the
    stubbing the rest of this file does to get at the pure functions."""
    result = subprocess.run(
        [sys.executable, "-c", "import sovereign.risk.kelly_engine"],
        cwd=__file__.rsplit("/sovereign/", 1)[0],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr
    assert "layer2" in result.stderr


def _stub_missing_dependencies():
    """Fabricate empty stand-ins for the two packages that don't exist in this repo
    (`layer2`, top-level `config`) so kelly_engine.py's module-level imports succeed
    and the standalone functions become importable. Does not modify kelly_engine.py."""
    if "layer2" not in sys.modules:
        layer2 = types.ModuleType("layer2")
        layer2_risk_engine = types.ModuleType("layer2.risk_engine")
        layer2_risk_engine.RiskEngine = type("RiskEngine", (), {})
        layer2_dynamic_rr = types.ModuleType("layer2.dynamic_rr_engine")
        layer2_dynamic_rr.DynamicRREngine = type("DynamicRREngine", (), {})
        sys.modules["layer2"] = layer2
        sys.modules["layer2.risk_engine"] = layer2_risk_engine
        sys.modules["layer2.dynamic_rr_engine"] = layer2_dynamic_rr
    if "config" not in sys.modules:
        config_pkg = types.ModuleType("config")
        config_loader = types.ModuleType("config.loader")
        config_loader.params = {}
        sys.modules["config"] = config_pkg
        sys.modules["config.loader"] = config_loader


_stub_missing_dependencies()
from sovereign.risk.kelly_engine import (  # noqa: E402
    fractional_kelly,
    hoeffding_win_rate,
    sample_complexity_confidence,
)


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


class TestDegenerateInputsContradictRule3:
    """CLAUDE.md non-negotiable #3: 'Never silently default unavailable values to
    numeric zero.' fractional_kelly does not raise on ANY degenerate input — it
    always returns a number. These tests assert the CODE'S ACTUAL current
    behaviour (a documentation of what it does today), not desired behaviour.
    Every one of these is a contradiction of rule #3 and is flagged as a finding.
    """

    def test_win_rate_zero_silently_returns_floor(self):
        # FINDING: p=0 (impossible edge) silently returns floor=0.005, not an error.
        assert fractional_kelly(win_rate=0.0, avg_win_r=2.0, avg_loss_r=1.0) == 0.005

    def test_win_rate_one_silently_returns_floor(self):
        # FINDING: p=1 (certain win — an unusable/garbage input in practice)
        # silently returns floor=0.005, not an error.
        assert fractional_kelly(win_rate=1.0, avg_win_r=2.0, avg_loss_r=1.0) == 0.005

    def test_avg_loss_r_zero_silently_returns_floor(self):
        # FINDING: b undefined (division by zero avoided by an early return) but the
        # function silently substitutes floor=0.005 instead of raising.
        assert fractional_kelly(win_rate=0.6, avg_win_r=2.0, avg_loss_r=0.0) == 0.005

    def test_avg_loss_r_negative_silently_returns_floor(self):
        # FINDING: a negative avg_loss_r is a malformed input (loss magnitudes are
        # defined positive elsewhere in the codebase) — silently returns floor.
        assert fractional_kelly(win_rate=0.6, avg_win_r=2.0, avg_loss_r=-1.0) == 0.005

    def test_negative_expected_value_returns_hard_zero_not_floor(self):
        # This one path DOES distinguish itself: f*<=0 returns 0.0 exactly (not the
        # floor), i.e. "don't bet" is representable. Still a silent numeric result,
        # not a raise, but at least it is not conflated with the "bad input" floor path.
        assert fractional_kelly(win_rate=0.3, avg_win_r=1.0, avg_loss_r=1.0) == 0.0

    def test_nan_win_rate_silently_returns_the_ceiling_not_the_floor(self):
        # CRITICAL FINDING: this is the most dangerous case. NaN comparisons are
        # always False in Python, so `win_rate <= 0`, `win_rate >= 1`, and
        # `f_star <= 0` are all skipped even though win_rate is NaN. f_star becomes
        # NaN, and `max(floor, min(ceiling, nan))` evaluates to `ceiling`
        # (min(ceiling, nan) returns ceiling in CPython, since nan < ceiling is
        # False). The function silently returns the MAXIMUM allowed position size
        # on a NaN win_rate — the worst possible failure mode for a sizing function,
        # and a direct contradiction of "never silently default" (this doesn't even
        # default to zero — it defaults to max size).
        result = fractional_kelly(win_rate=float("nan"), avg_win_r=2.0, avg_loss_r=1.0)
        assert math.isnan(result) is False
        assert result == 0.04  # the default `ceiling` argument

    def test_nan_avg_win_r_also_silently_returns_the_ceiling(self):
        result = fractional_kelly(win_rate=0.6, avg_win_r=float("nan"), avg_loss_r=1.0)
        assert result == 0.04

    def test_nan_avg_loss_r_treated_as_bad_input_floor(self):
        # avg_loss_r's only guard is `<= 0`, which NaN fails (False), so it does NOT
        # take the early-return floor path here; it flows through to b = avg_win_r/NaN
        # = NaN, then the same NaN-clamps-to-ceiling behaviour as above.
        result = fractional_kelly(win_rate=0.6, avg_win_r=2.0, avg_loss_r=float("nan"))
        assert result == 0.04


class TestHoeffdingWinRateDegenerateInputs:
    """hoeffding_win_rate is the confidence-interval correction fed into Kelly
    upstream (see layers/kelly.py). It also silently defaults rather than raising."""

    def test_zero_trades_silently_returns_uninformative_prior(self):
        # FINDING: n_trades < 1 silently returns 0.50 (an "uninformative prior"
        # per the docstring) rather than raising, even though the caller asked
        # for a correction on data that does not exist.
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
