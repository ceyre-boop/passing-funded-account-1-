"""Tests for scripts/ruin_engine.py.

The load-bearing one is test_dd_walk_agrees_with_run_phase_bust_index: it is
the cross-check the task brief demanded ("a disagreement you cannot explain
is a finding, not a rounding error") between the two PINNED walks this
engine reuses (carry_buy_gate.run_phase for the bust/pass decision,
drawdown_margin.walk for the drawdown-path stats). If they ever disagree on
where a bust happened, this fails loudly instead of silently producing a
wrong drawdown percentile.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ruin_engine as re  # noqa: E402
from carry_buy_gate import run_phase  # noqa: E402
from drawdown_margin import walk as dd_walk  # noqa: E402
from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402


def _flat_series(n, r_on=(), open_units=1):
    vi = np.zeros(n)
    for i, r in r_on:
        vi[i] = r
    vw = np.clip(vi, None, 0.0)
    vo = np.full(n, open_units)
    return vi, vw, vo


# --------------------------------------------------------- cross-check (spec)

def test_dd_walk_agrees_with_run_phase_bust_index():
    """CTI static-drawdown bust fixture straight from test_carry_buy_gate.py's
    own convention: -6R day at 1% risk on the (currently trailing) CTI
    contract must bust, and the pinned drawdown walk over the same slice must
    report its breach on the exact same day run_phase stopped at."""
    cti = load_contract("cti_1step")
    vi, vw, vo = _flat_series(10, r_on=[(2, -6.0)])
    outcome, nxt, _heat = run_phase(vi, vw, vo, 0, cti, 0, 0.01, 10)
    assert outcome == "BUST"
    seg = dd_walk(vi[0:nxt], vw[0:nxt], vo[0:nxt], cti, 0.01)
    assert seg["breach"] is not None
    assert seg["breach"]["i"] == nxt - 1, (
        "run_phase's bust day and drawdown_margin.walk's breach day must be "
        "the exact same index on the exact same slice")


def test_dd_walk_agrees_on_unresolved_series():
    """No bust: both walks must agree there is no breach over the full slice."""
    cti = load_contract("cti_1step")
    vi, vw, vo = _flat_series(20, r_on=[(2, -2.0)])
    outcome, nxt, _heat = run_phase(vi, vw, vo, 0, cti, 0, 0.01, 20)
    assert outcome == "UNRESOLVED"
    seg = dd_walk(vi[0:nxt], vw[0:nxt], vo[0:nxt], cti, 0.01)
    assert seg["breach"] is None


# ------------------------------------------------------- run_single_attempt

def test_single_attempt_ruin_is_terminal_no_rebuy():
    """Unlike run_campaign, a bust must end the attempt as RUIN — no restart."""
    cti = load_contract("cti_1step")
    vi, vw, vo = _flat_series(30, r_on=[(1, -6.0), (10, 9.0)])
    res = re.run_single_attempt(vi, vw, vo, cti, 0.01, 30)
    assert res["outcome"] == "RUIN"
    assert res["end_i"] == 2, "must stop at the bust day, not keep walking to the later win"


def test_single_attempt_pass():
    cti = load_contract("cti_1step")
    vi, vw, vo = _flat_series(10, r_on=[(0, 9.0)])
    res = re.run_single_attempt(vi, vw, vo, cti, 0.01, 10)
    assert res["outcome"] == "PASS"


def test_single_attempt_open_not_timeout():
    """No-deadline contract: an attempt that neither busts nor reaches target
    by the horizon is OPEN, never a synthetic TIMEOUT/failure."""
    cti = load_contract("cti_1step")
    vi, vw, vo = _flat_series(20, r_on=[(2, -1.0)])
    res = re.run_single_attempt(vi, vw, vo, cti, 0.01, 20)
    assert res["outcome"] == "OPEN"
    assert res["end_i"] == 20


def test_single_attempt_two_phase_requires_both():
    alpha = load_contract("alpha_swing")
    vi, vw, vo = _flat_series(20, r_on=[(1, 11.0)])
    res = re.run_single_attempt(vi, vw, vo, alpha, 0.01, 20)
    assert res["outcome"] == "OPEN", "phase 2 (5%) never cleared -> still open, not passed"

    vi2, vw2, vo2 = _flat_series(20, r_on=[(1, 11.0), (5, 6.0)])
    res2 = re.run_single_attempt(vi2, vw2, vo2, alpha, 0.01, 20)
    assert res2["outcome"] == "PASS"


def test_single_attempt_worst_floor_maxes_across_phases():
    """Phase 2 draws down harder than phase 1: the reported worst_floor for
    the whole attempt must reflect phase 2's excursion, not phase 1's."""
    alpha = load_contract("alpha_swing")
    # Phase 1: clears cleanly with +11R (no drawdown). Phase 2: dips -2R
    # before clearing with +6R -> a real, nonzero excursion in phase 2 only.
    vi, vw, vo = _flat_series(20, r_on=[(1, 11.0), (3, -2.0), (5, 8.0)])
    res = re.run_single_attempt(vi, vw, vo, alpha, 0.01, 20)
    assert res["outcome"] == "PASS"
    assert res["worst_floor"] > 0.0, "phase-2 excursion must not be lost"


# ------------------------------------------------------------------ sweep spec

def test_risk_sweep_covers_010_to_300_step_005():
    assert re.RISK_SWEEP[0] == pytest.approx(0.0010)
    assert re.RISK_SWEEP[-1] == pytest.approx(0.0300)
    assert len(re.RISK_SWEEP) == 59
    diffs = np.diff(re.RISK_SWEEP)
    assert np.allclose(diffs, 0.0005)


# --------------------------------------------------------------- sampling

def test_block_bootstrap_path_has_requested_length():
    cti = load_contract("cti_1step")
    trades = re.load_sealed()
    haircut = cti.costs.swap_haircut_r_per_day
    _, vi, vw, vo = re.build_series(trades, haircut, center=False)
    rng = np.random.default_rng(0)
    pi, pw, po = re.block_bootstrap_path(vi, vw, vo, 137, rng)
    assert len(pi) == len(pw) == len(po) == 137


# ------------------------------------------------------------- Monte Carlo

def test_simulate_risk_outcome_fractions_partition():
    """PASS + RUIN + OPEN must exactly partition the sampled paths (no path
    silently lost or double-counted)."""
    cti = load_contract("cti_1step")
    trades = re.load_sealed()
    haircut = cti.costs.swap_haircut_r_per_day
    _, vi, vw, vo = re.build_series(trades, haircut, center=False)
    rng = np.random.default_rng(1)
    result = re.simulate_risk(vi, vw, vo, cti, 0.01, horizon_td=200, rng=rng,
                              min_paths=300, max_paths=300, batch=300)
    total_frac = result["p_pass"] + result["p_ruin"] + result["p_open"]
    assert total_frac == pytest.approx(1.0, abs=1e-9)
    assert result["n_paths"] == 300


def test_simulate_risk_every_probability_carries_an_interval():
    """spec 021 P5 discipline, reused here: no probability without bounds."""
    cti = load_contract("cti_1step")
    trades = re.load_sealed()
    haircut = cti.costs.swap_haircut_r_per_day
    _, vi, vw, vo = re.build_series(trades, haircut, center=False)
    rng = np.random.default_rng(2)
    result = re.simulate_risk(vi, vw, vo, cti, 0.01, horizon_td=150, rng=rng,
                              min_paths=200, max_paths=200, batch=200)
    for key in ("p_pass", "p_ruin"):
        assert f"{key}_lo" in result and f"{key}_hi" in result
        assert result[f"{key}_lo"] <= result[key] <= result[f"{key}_hi"]


def test_higher_risk_increases_ruin_on_a_fixed_seed():
    """Structural monotonicity that MUST hold regardless of the P(pass) hump:
    scaling risk up scales every adverse excursion, so ruin probability must
    be non-decreasing in risk on the same sampled paths (same seed)."""
    cti = load_contract("cti_1step")
    trades = re.load_sealed()
    haircut = cti.costs.swap_haircut_r_per_day
    _, vi, vw, vo = re.build_series(trades, haircut, center=False)
    low = re.simulate_risk(vi, vw, vo, cti, 0.003, horizon_td=250,
                           rng=np.random.default_rng(7),
                           min_paths=400, max_paths=400, batch=400)
    high = re.simulate_risk(vi, vw, vo, cti, 0.02, horizon_td=250,
                            rng=np.random.default_rng(7),
                            min_paths=400, max_paths=400, batch=400)
    assert high["p_ruin"] >= low["p_ruin"]
