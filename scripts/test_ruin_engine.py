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


# ---------------------------------------------------------------- controls

def test_make_control_random_is_thin_and_zero_expectancy_by_construction():
    """Every random-control trade is exactly +-1R (thin-tailed) — the EASY
    control. Mean must land close to 0 for a large-n sample (Bernoulli(0.5)
    on +-1 has population mean 0; check within a generous statistical band
    so this isn't flaky)."""
    trades = re.load_sealed()
    rng = np.random.default_rng(3)
    ctrl = re.make_control_trades(trades, "random", rng)
    rs = np.array([t["R"] for t in ctrl])
    assert set(np.unique(rs)) <= {-1.0, 1.0}
    assert abs(rs.mean()) < 0.15, "n=411 Bernoulli(0.5) mean should sit near 0"


def test_make_control_shuffled_preserves_magnitudes_exactly():
    """The shuffled control must contain the EXACT same multiset of |R|
    magnitudes as the real series — only the sign pairing changes. This is
    what "preserves the fat tails" means, made checkable."""
    trades = re.load_sealed()
    rng = np.random.default_rng(4)
    ctrl = re.make_control_trades(trades, "shuffled", rng)
    real_mags = sorted(abs(t["R"]) for t in trades)
    ctrl_mags = sorted(abs(t["R"]) for t in ctrl)
    assert real_mags == pytest.approx(ctrl_mags)


def test_make_control_shuffled_same_win_rate_different_pairing():
    """Shuffling signs (a permutation) preserves the win/loss COUNT exactly
    (same number of + and - labels) but must not, in general, reproduce the
    original per-trade sign assignment — otherwise nothing was destroyed."""
    trades = re.load_sealed()
    rng = np.random.default_rng(5)
    ctrl = re.make_control_trades(trades, "shuffled", rng)
    real_signs = [1 if t["R"] > 0 else (-1 if t["R"] < 0 else 0) for t in trades]
    ctrl_signs = [1 if t["R"] > 0 else (-1 if t["R"] < 0 else 0) for t in ctrl]
    assert sum(s > 0 for s in real_signs) == sum(s > 0 for s in ctrl_signs), (
        "permuting signs must preserve the win COUNT exactly")
    assert real_signs != ctrl_signs, (
        "a real shuffle must not reproduce the identity pairing on n=411 trades")


def test_make_control_preserves_dates_and_holds():
    """The control's calendar (entry/exit/hold) must be untouched — same
    costs, same weekend accounting, same swap-haircut day-count as the edge."""
    trades = re.load_sealed()
    rng = np.random.default_rng(6)
    ctrl = re.make_control_trades(trades, "random", rng)
    for t, c in zip(trades, ctrl):
        assert c["entry"] == t["entry"] and c["exit"] == t["exit"] and c["hold"] == t["hold"]


def test_make_control_unknown_kind_raises():
    trades = re.load_sealed()
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError):
        re.make_control_trades(trades, "not-a-real-kind", rng)


def test_control_series_same_length_as_edge_series():
    """Common random numbers require equal-length series — verify the
    control's build_series output actually matches the edge's, not just by
    assumption."""
    cti = load_contract("cti_1step")
    trades = re.load_sealed()
    haircut = cti.costs.swap_haircut_r_per_day
    _, evi, _, _ = re.build_series(trades, haircut, center=False)
    rng = np.random.default_rng(9)
    ctrl_trades = re.make_control_trades(trades, "shuffled", rng)
    _, cvi, _, _ = re.build_series(ctrl_trades, haircut, center=False)
    assert len(evi) == len(cvi)


# ------------------------------------------------------- simulate_frontier

def test_simulate_frontier_rejects_length_mismatch():
    """Common random numbers require equal-length series; a mismatch must
    raise loudly, not silently truncate or broadcast."""
    cti = load_contract("cti_1step")
    trades = re.load_sealed()
    haircut = cti.costs.swap_haircut_r_per_day
    _, vi, vw, vo = re.build_series(trades, haircut, center=False)
    short = (vi[:-5], vw[:-5], vo[:-5])
    rng = np.random.default_rng(11)
    with pytest.raises(ValueError):
        re.simulate_frontier({"edge": (vi, vw, vo), "random": short}, cti, 0.01,
                             horizon_td=100, rng=rng, min_paths=50, max_paths=50, batch=50)


def test_simulate_frontier_requires_at_least_one_series():
    cti = load_contract("cti_1step")
    rng = np.random.default_rng(12)
    with pytest.raises(ValueError):
        re.simulate_frontier({}, cti, 0.01, horizon_td=100, rng=rng)


def test_simulate_frontier_is_common_random_numbers_deterministic():
    """Same seed, same (firm, risk) -> identical results across an
    independent re-run — the paired sampling must be reproducible, not just
    each series independently reproducible."""
    cti = load_contract("cti_1step")
    trades = re.load_sealed()
    haircut = cti.costs.swap_haircut_r_per_day
    _, vi, vw, vo = re.build_series(trades, haircut, center=False)
    rng1 = np.random.default_rng(21)
    ctrl_trades = re.make_control_trades(trades, "random", np.random.default_rng(1))
    _, cvi, cvw, cvo = re.build_series(ctrl_trades, haircut, center=False)
    series_map = {"edge": (vi, vw, vo), "random": (cvi, cvw, cvo)}

    r1 = re.simulate_frontier(series_map, cti, 0.01, horizon_td=150, rng=rng1,
                              min_paths=200, max_paths=200, batch=200)
    rng2 = np.random.default_rng(21)
    r2 = re.simulate_frontier(series_map, cti, 0.01, horizon_td=150, rng=rng2,
                              min_paths=200, max_paths=200, batch=200)
    assert r1["edge"]["p_pass"] == r2["edge"]["p_pass"]
    assert r1["random"]["p_pass"] == r2["random"]["p_pass"]


def test_simulate_risk_wrapper_matches_frontier_single_key():
    """simulate_risk must be a faithful thin wrapper, not a divergent copy."""
    cti = load_contract("cti_1step")
    trades = re.load_sealed()
    haircut = cti.costs.swap_haircut_r_per_day
    _, vi, vw, vo = re.build_series(trades, haircut, center=False)
    a = re.simulate_risk(vi, vw, vo, cti, 0.01, horizon_td=120,
                         rng=np.random.default_rng(31),
                         min_paths=150, max_paths=150, batch=150)
    b = re.simulate_frontier({"edge": (vi, vw, vo)}, cti, 0.01, horizon_td=120,
                             rng=np.random.default_rng(31),
                             min_paths=150, max_paths=150, batch=150)["edge"]
    assert a["p_pass"] == b["p_pass"] and a["n_paths"] == b["n_paths"]


# ------------------------------------------------------------ reporting

def test_fmt_control_row_refuses_missing_control():
    """Mirrors carry_buy_gate.fmt_row's 'takes both or raises' discipline —
    deliberate violation (calling without a control) must fail loudly."""
    edge = dict(p_pass=0.5, p_pass_lo=0.4, p_pass_hi=0.6)
    with pytest.raises(ValueError):
        re.fmt_control_row(0.01, edge, None, "random")
    with pytest.raises(ValueError):
        re.fmt_control_row(0.01, None, edge, "random")
    with pytest.raises(ValueError):
        re.fmt_control_row(0.01, None, None, "random")


def test_fmt_control_row_prints_edge_and_control_on_one_line():
    edge = dict(p_pass=0.756, p_pass_lo=0.742, p_pass_hi=0.770)
    ctrl = dict(p_pass=0.118, p_pass_lo=0.108, p_pass_hi=0.129)
    line = re.fmt_control_row(0.006, edge, ctrl, "shuffled")
    assert "EDGE" in line and "SHUFFLED" in line
    assert "75.6%" in line and "11.8%" in line
    assert "\n" not in line, "edge and control must be on the SAME line"


def test_separation_summary_reports_no_separation_plainly():
    identical = dict(risk=0.01, p_pass=0.5, p_pass_lo=0.4, p_pass_hi=0.6)
    rows = [dict(edge=identical, random=dict(identical))]
    summary = re.separation_summary(rows, "edge", "random")
    assert summary["separates"] is False
    assert summary["risk"] is None


def test_separation_summary_finds_first_separating_risk():
    rows = [
        dict(edge=dict(risk=0.001, p_pass=0.1, p_pass_lo=0.05, p_pass_hi=0.15),
             random=dict(p_pass=0.09, p_pass_lo=0.04, p_pass_hi=0.14)),   # overlaps
        dict(edge=dict(risk=0.002, p_pass=0.8, p_pass_lo=0.75, p_pass_hi=0.85),
             random=dict(p_pass=0.1, p_pass_lo=0.05, p_pass_hi=0.15)),    # separates
    ]
    summary = re.separation_summary(rows, "edge", "random")
    assert summary["separates"] is True
    assert summary["risk"] == 0.002
