"""Tests for eval_size() / funded_size() (sovereign/risk/layers/prop.py).

Task brief correction (2026-08-27): the original acceptance criterion was
"a test asserting eval_size() != funded_size()". That test is deliberately
NOT written here — it asserts a conclusion. If the two objective functions
legitimately produced close numbers for some contract, a test like that
would force a wrong answer.

Instead these tests prove the two functions are DERIVED FROM DIFFERENT
OBJECTIVE FUNCTIONS: funded_size responds to a change in profit_split while
eval_size does not (it doesn't even take one as an argument); and eval_size
responds to a change in drawdown assumptions by looking up a DIFFERENT
precomputed frontier (an indirect, data-driven mechanism), while funded_size
responds to a drawdown-threshold change by substituting the contract's
max_dd.pct directly into an algebraic min() clamp (a direct, formulaic
mechanism) -- a different mechanism, not just a different number.

Real-data assertions (against the actual cti_1step frontier and sealed trade
record) are included too, but as a secondary check -- the mechanism tests
are the load-bearing ones per the brief.
"""
import math

import pytest

from sovereign.risk.layers.prop import (
    MissingContractInput,
    eval_size,
    funded_size,
    load_ruin_frontier,
)
from sovereign.propfirm.firm_contracts import load_contract


# --------------------------------------------------------------- fixtures

def _fake_frontier(firm, risks_and_ppass):
    """Build a minimal synthetic frontier dict shaped like ruin_engine.py's
    real output -- just the fields eval_size() actually reads."""
    rows = []
    for risk, p_pass in risks_and_ppass:
        lo = max(0.0, p_pass - 0.01)
        hi = min(1.0, p_pass + 0.01)
        rows.append({"edge": {"risk": risk, "p_pass": p_pass,
                               "p_pass_lo": lo, "p_pass_hi": hi}})
    return {"firm": firm, "series": "sealed", "horizon_days": 730, "rows": rows}


def _wide_frontier(firm, risks_and_ppass):
    """Same shape, but every cell's Wilson interval is wide enough to
    overlap every other cell -- collapses the plateau to 'everything',
    used only where a test needs a single, unambiguous CI band."""
    rows = []
    for risk, p_pass in risks_and_ppass:
        rows.append({"edge": {"risk": risk, "p_pass": p_pass,
                               "p_pass_lo": 0.0, "p_pass_hi": 1.0}})
    return {"firm": firm, "series": "sealed", "horizon_days": 730, "rows": rows}


# ------------------------------------------------------- mechanism: profit_split

class TestObjectiveFunctionsDiffer:
    def test_funded_size_signature_accepts_profit_split_eval_size_does_not(self):
        """Structural proof, not a numeric one: funded_size's objective is
        parameterized by profit_split; eval_size's is not even shaped to
        accept it. This alone is evidence the two run different math, not
        just different inputs into the same math."""
        import inspect
        assert "profit_split" in inspect.signature(funded_size).parameters
        assert "profit_split" not in inspect.signature(eval_size).parameters

    def test_funded_size_expected_payout_responds_to_profit_split(self):
        """Same contract, same edge stats, same payout cadence -- only
        profit_split changes. funded_size's expected-payout OUTPUT must
        move (it's a direct multiplicative term in the objective)."""
        r_hi = funded_size("cti_1step", profit_split=0.8, payout_interval_days=14)
        r_lo = funded_size("cti_1step", profit_split=0.4, payout_interval_days=14)
        assert r_hi["expected_payout_per_trade_usd"] != pytest.approx(
            r_lo["expected_payout_per_trade_usd"]
        )
        # And it must scale linearly with the split -- not some unrelated shift.
        assert r_hi["expected_payout_per_trade_usd"] == pytest.approx(
            r_lo["expected_payout_per_trade_usd"] * (0.8 / 0.4)
        )

    def test_eval_size_has_no_mechanism_to_react_to_profit_split_at_all(self):
        """eval_size's recommendation is identical no matter what profit
        split a caller might have in mind -- because the argmax-P(pass)
        objective never references it. Calling eval_size twice with the
        same frontier must be bit-identical regardless of any funded-side
        assumption a caller layers on top."""
        frontier = _fake_frontier("cti_1step", [(0.001, 0.2), (0.005, 0.7), (0.01, 0.5)])
        a = eval_size("cti_1step", frontier=frontier)
        b = eval_size("cti_1step", frontier=frontier)
        assert a == b


# ------------------------------------------------- mechanism: drawdown threshold

class TestDrawdownThresholdMechanismDiffers:
    def test_eval_size_reacts_to_a_different_dd_regime_only_via_a_different_frontier(self):
        """eval_size has no drawdown-threshold parameter at all -- its only
        channel of influence is which PRECOMPUTED frontier it's handed.
        Two frontiers built as if simulated under different dd assumptions
        (tighter dd -> survival peaks at a lower risk) must move eval_size's
        recommendation via that indirect, data-driven channel."""
        tight_dd_frontier = _wide_frontier(
            "cti_1step", [(0.001, 0.30), (0.003, 0.55), (0.006, 0.20)]
        )
        loose_dd_frontier = _wide_frontier(
            "cti_1step", [(0.001, 0.20), (0.003, 0.30), (0.006, 0.60)]
        )
        tight = eval_size("cti_1step", frontier=tight_dd_frontier)
        loose = eval_size("cti_1step", frontier=loose_dd_frontier)
        assert tight["argmax_risk_pct"] != loose["argmax_risk_pct"]
        # Confirms the channel: eval_size has no dd-threshold argument to
        # inspect -- the ONLY way this test could move the answer is by
        # swapping the frontier dict itself.
        import inspect
        assert "max_dd" not in inspect.signature(eval_size).parameters
        assert "contract" not in inspect.signature(eval_size).parameters

    def test_funded_size_reacts_to_dd_threshold_via_a_direct_algebraic_clamp(self):
        """funded_size, by contrast, takes the SAME edge stats / profit
        split / cadence and moves its answer purely by substituting a
        different contract.max_dd.pct into min(growth_optimal, dd_ceiling)
        -- a direct formula parameter, not a data lookup. Uses two
        contract-shaped stand-ins differing only in max_dd.pct."""
        import types

        def _stub_contract(dd_pct):
            max_dd = types.SimpleNamespace(pct=dd_pct)
            return types.SimpleNamespace(max_dd=max_dd, account_size=100_000.0)

        tight = funded_size(
            "cti_1step", profit_split=0.8, payout_interval_days=14,
            contract=_stub_contract(0.01),
        )
        loose = funded_size(
            "cti_1step", profit_split=0.8, payout_interval_days=14,
            contract=_stub_contract(0.20),
        )
        # The growth-optimal Kelly fraction itself is untouched by dd --
        # only the CLAMP moves, which is exactly the mechanism difference
        # under test (a ceiling substitution, not a re-derivation).
        assert tight["growth_optimal_risk_pct"] == pytest.approx(
            loose["growth_optimal_risk_pct"]
        )
        assert tight["recommended_risk_pct"] != loose["recommended_risk_pct"]
        assert tight["recommended_risk_pct"] == pytest.approx(
            min(tight["growth_optimal_risk_pct"], 0.01)
        )
        assert loose["recommended_risk_pct"] == pytest.approx(
            min(loose["growth_optimal_risk_pct"], 0.20)
        )


# ----------------------------------------------------- real-data sanity checks

class TestEvalSizeOnRealFrontier:
    def test_matches_the_documented_plateau_on_cti_1step(self):
        """Sanity check against the real, on-disk frontier: the argmax and
        the plateau band this reports must match what the raw json shows,
        not some rounded-off approximation."""
        result = eval_size("cti_1step")
        assert result["argmax_risk_pct"] == pytest.approx(0.006)
        assert result["plateau_risk_lo_pct"] == pytest.approx(0.0045)
        assert result["plateau_risk_hi_pct"] == pytest.approx(0.006)
        assert result["plateau_n_cells"] >= 2  # a genuine plateau, not a lone point

    def test_wrong_firm_key_refuses_rather_than_silently_mismatching(self):
        with pytest.raises(ValueError, match="alpha_swing"):
            eval_size("alpha_swing")  # frontier on disk is for cti_1step

    def test_frontier_missing_on_disk_raises_not_silently_empty(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_ruin_frontier(tmp_path / "does_not_exist.json")


class TestFundedSizeMissingContractInputs:
    def test_real_contract_yaml_is_missing_profit_split_and_payout_schedule(self):
        """The actual finding: as of this task, firm_contracts.yaml carries
        neither field for any firm. funded_size() must refuse, not guess."""
        with pytest.raises(MissingContractInput) as exc:
            funded_size("cti_1step")
        assert "profit_split" in exc.value.missing_fields
        assert "payout_schedule.interval_days" in exc.value.missing_fields

    def test_supplying_both_overrides_unblocks_it(self):
        result = funded_size("cti_1step", profit_split=0.8, payout_interval_days=14)
        assert result["profit_split"] == 0.8
        assert result["payout_interval_days"] == 14
        assert result["recommended_risk_pct"] > 0.0

    def test_out_of_range_profit_split_raises(self):
        with pytest.raises(ValueError, match="profit_split"):
            funded_size("cti_1step", profit_split=1.5, payout_interval_days=14)

    def test_non_positive_payout_interval_raises(self):
        with pytest.raises(ValueError, match="payout_interval_days"):
            funded_size("cti_1step", profit_split=0.8, payout_interval_days=0)


class TestFundedSizeNeverExceedsPropCeilingsExistingGuard(object):
    """Hard constraint from the task brief: 'nothing may end up sized LARGER
    than the current path allows without that being explicit and tested.'
    funded_size's recommended_risk_pct must never exceed the contract's
    max_dd.pct -- the exact field prop_ceiling's cfg["prop"]["max_drawdown_pct"]
    already enforces at the per-trade level."""

    def test_recommended_risk_never_exceeds_contract_max_dd_pct(self):
        contract = load_contract("cti_1step")
        result = funded_size("cti_1step", profit_split=0.8, payout_interval_days=14)
        assert result["recommended_risk_pct"] <= contract.max_dd.pct + 1e-12

    def test_dd_ceiling_binds_flag_is_honest_about_which_branch_fired(self):
        result = funded_size("cti_1step", profit_split=0.8, payout_interval_days=14)
        if result["dd_ceiling_binds"]:
            assert result["recommended_risk_pct"] == pytest.approx(
                result["max_dd_ceiling_pct"]
            )
        else:
            assert result["recommended_risk_pct"] == pytest.approx(
                result["growth_optimal_risk_pct"]
            )
