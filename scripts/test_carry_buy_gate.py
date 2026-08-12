"""Mutation-oriented tests for the carry buy gate evaluator — spec 021.

Fault rows in specs/021_MUTATION_LOG.md.
"""
import csv
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import carry_buy_gate as cbg  # noqa: E402
from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402


@pytest.fixture(scope="module")
def sealed():
    return cbg.load_sealed()


def test_golden_green_on_real_data(sealed):
    assert cbg.golden_gate(sealed)["status"] == "GREEN"


def test_golden_fails_on_sign_flip(sealed):
    flipped = [dict(t, R=-t["R"]) for t in sealed]
    g = cbg.golden_gate(flipped)
    assert g["status"] == "RED"
    assert not g["checks"]["avg_r"] and not g["checks"]["wr"]


def test_golden_fails_on_constant_divisor():
    """Fault: dividing every pnl_pct by 0.0075 instead of the row's own risk_pct.
    The sealed CSV contains 0.007969 rows; a constant divisor inflates their R."""
    trades = []
    with open(cbg.SEALED_CSV) as f:
        for row in csv.DictReader(f):
            trades.append(dict(pair=row["pair"], entry=row["entry_date"][:10],
                               exit=row["exit_date"][:10],
                               R=float(row["pnl_pct"]) / 0.0075,
                               hold=int(row["hold_days"])))
    g = cbg.golden_gate(trades)
    assert g["status"] == "RED", "constant divisor must not reproduce the golden stats"


def test_formatter_refuses_missing_control():
    real = dict(p_pass=0.5, p_pass_lo=0.4, p_pass_hi=0.6)
    with pytest.raises(ValueError):
        cbg.fmt_row("x", real, None)
    with pytest.raises(ValueError):
        cbg.fmt_row("x", None, real)


def _flat_series(n, r_on=(), open_units=1):
    vi = np.zeros(n)
    for i, r in r_on:
        vi[i] = r
    vw = np.clip(vi, None, 0.0)
    vo = np.full(n, open_units)
    return vi, vw, vo


def test_no_deadline_never_times_out():
    """Outcome set for a no-deadline contract is PASS/BUST/UNRESOLVED — never TIMEOUT."""
    cti = load_contract("cti_1step")
    vi, vw, vo = _flat_series(300)
    out, _, _ = cbg.run_phase(vi, vw, vo, 0, cti, 0, 0.02, 300)
    assert out == "UNRESOLVED"


def test_deadline_contract_refused():
    """A contract phase with max_days must be rejected loudly, not silently modeled."""
    import dataclasses
    cti = load_contract("cti_1step")
    phase = dataclasses.replace(cti.phases[0], max_days=90)
    deadline = dataclasses.replace(cti, phases=(phase,))
    vi, vw, vo = _flat_series(50)
    with pytest.raises(AssertionError):
        cbg.run_phase(vi, vw, vo, 0, deadline, 0, 0.02, 50)


def test_static_dd_busts_at_floor():
    """CTI static 5%: a -6R day at 1% risk -> equity 0.94 < 0.95 floor -> BUST."""
    cti = load_contract("cti_1step")
    vi, vw, vo = _flat_series(10, r_on=[(2, -6.0)])
    out, _, _ = cbg.run_phase(vi, vw, vo, 0, cti, 0, 0.01, 10)
    assert out == "BUST"
    # -4R at 1% -> 0.96 stays above the floor.
    vi2, vw2, vo2 = _flat_series(10, r_on=[(2, -4.0)])
    out2, _, _ = cbg.run_phase(vi2, vw2, vo2, 0, cti, 0, 0.01, 10)
    assert out2 == "UNRESOLVED"


def test_trailing_on_cti_flips_verdict():
    """Fault: static->trailing on CTI. Run up then a pullback that only busts trailing."""
    import dataclasses
    cti = load_contract("cti_1step")
    # CTI itself is trailing as of 2026-08-12, so BOTH fixtures are built
    # explicitly. Sourcing either arm from the live contract makes this test a
    # no-op the day that contract's type changes — which is exactly what
    # happened when the DD type was corrected.
    static = dataclasses.replace(
        cti, max_dd=dataclasses.replace(cti.max_dd, type="static"))
    trailing = dataclasses.replace(
        cti, max_dd=dataclasses.replace(cti.max_dd, type="trailing"))
    # +4R then -5R at 1%: peak 1.04, close 1.04*0.95=0.988 <= trailing floor
    # 1.04*0.95=0.988 but above static floor 0.95.
    vi, vw, vo = _flat_series(10, r_on=[(1, 4.0), (3, -5.0)])
    out_static, _, _ = cbg.run_phase(vi, vw, vo, 0, static, 0, 0.01, 10)
    out_trail, _, _ = cbg.run_phase(vi, vw, vo, 0, trailing, 0, 0.01, 10)
    assert out_static == "UNRESOLVED" and out_trail == "BUST"


def test_daily_floor_uses_day_start_not_open():
    """Alpha 5% daily (close mark): a single -6R day at 1% is fine (-6%... wait, -6% < -5% busts);
    -4R day passes. Checks the budget is applied to the day's move, not cumulative."""
    alpha = load_contract("alpha_swing")
    vi, vw, vo = _flat_series(10, r_on=[(2, -6.0)])
    out, _, _ = cbg.run_phase(vi, vw, vo, 0, alpha, 0, 0.01, 10)
    assert out == "BUST"  # -6% day vs 5% daily budget
    # Two separate -3R days: each day only -3%, never breaches the 5% daily budget
    # and cumulative -6% stays above the 10% max-DD floor.
    vi2, vw2, vo2 = _flat_series(10, r_on=[(2, -3.0), (5, -3.0)])
    out2, _, _ = cbg.run_phase(vi2, vw2, vo2, 0, alpha, 0, 0.01, 10)
    assert out2 == "UNRESOLVED"


def test_min_trading_days_blocks_instant_pass():
    """FTMO phase 1 (min 4 trading days): a +12R day 0 must not clear the phase."""
    ftmo = load_contract("ftmo_swing")
    vi, vw, vo = _flat_series(3, r_on=[(0, 12.0)], open_units=0)
    vo[0] = 1  # one trading day only
    out, _, _ = cbg.run_phase(vi, vw, vo, 0, ftmo, 0, 0.01, 3)
    assert out == "UNRESOLVED", "target hit but min_trading_days not met"
    # Same series with enough runway and trading days does pass.
    vi2, vw2, vo2 = _flat_series(10, r_on=[(0, 12.0)], open_units=1)
    out2, _, _ = cbg.run_phase(vi2, vw2, vo2, 0, ftmo, 0, 0.01, 10)
    assert out2 == "PASS"


def test_two_phase_campaign_requires_both():
    """Alpha Swing: clearing phase 1 alone must not report funded."""
    alpha = load_contract("alpha_swing")
    vi, vw, vo = _flat_series(20, r_on=[(1, 11.0)])
    res = cbg.run_campaign(vi, vw, vo, 0, alpha, 0.01, 20)
    assert not res["passed"], "phase 2 (5%) was never cleared"
    vi2, vw2, vo2 = _flat_series(20, r_on=[(1, 11.0), (5, 6.0)])
    res2 = cbg.run_campaign(vi2, vw2, vo2, 0, alpha, 0.01, 20)
    assert res2["passed"] and res2["evals"] == 1


def test_bust_increments_evals_and_restarts():
    cti = load_contract("cti_1step")
    vi, vw, vo = _flat_series(30, r_on=[(1, -6.0), (10, 9.0)])
    res = cbg.run_campaign(vi, vw, vo, 0, cti, 0.01, 30)
    assert res["passed"] and res["evals"] == 2


def test_swap_haircut_applied_per_hold_day(sealed):
    """Fault: dropping the swap haircut. Total credited R must equal
    sum(R) - haircut * sum(max(hold,1)) exactly."""
    haircut = load_contract("cti_1step").costs.swap_haircut_r_per_day
    _, vi, _, _ = cbg.build_series(sealed, haircut, center=False)
    expected = sum(t["R"] for t in sealed) - haircut * sum(max(t["hold"], 1) for t in sealed)
    assert vi.sum() == pytest.approx(expected, abs=1e-9)
    assert haircut > 0, "contract haircut must be nonzero for this test to bite"


def test_zero_edge_centering_zeroes_the_mean(sealed):
    haircut = load_contract("cti_1step").costs.swap_haircut_r_per_day
    _, vi, _, _ = cbg.build_series(sealed, haircut, center=True)
    assert abs(vi.sum()) < 1e-9


def test_g4_flags_weekend_violations(sealed):
    import dataclasses
    cti = load_contract("cti_1step")
    assert cbg.fit_gate(sealed, cti)["status"] == "GREEN"
    no_weekend = dataclasses.replace(
        cti, permissions={**cti.permissions, "weekend_hold": False})
    g4 = cbg.fit_gate(sealed, no_weekend)
    assert g4["status"] == "RED"
    assert len(g4["violations"]) == pytest.approx(0.7226 * 411, abs=2)


def test_oos_refused_when_golden_fails(tmp_path, monkeypatch, capsys):
    """Spec P7: a broken sealed replay blocks scoring anything else, exit 2."""
    bad = tmp_path / "sealed.csv"
    with open(cbg.SEALED_CSV) as f:
        rows = list(csv.DictReader(f))
    fields = rows[0].keys()
    for r in rows:
        r["pnl_pct"] = str(-float(r["pnl_pct"]))
    with open(bad, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    monkeypatch.setattr(cbg, "SEALED_CSV", bad)
    monkeypatch.setattr(sys, "argv", ["carry_buy_gate.py", "--series", "oos"])
    with pytest.raises(SystemExit) as e:
        cbg.main()
    assert e.value.code == 2


def test_g3_stays_red_on_sealed_run(monkeypatch, capsys):
    """G3 is pre-registered as an OOS comparison; an in-sample run must not green it."""
    monkeypatch.setattr(cbg, "RISK_SWEEP", [0.02])
    monkeypatch.setattr(cbg, "BOOT_PATHS", 400)  # enough paths that real/control CIs separate in-sample
    monkeypatch.setattr(cbg, "OBS_HORIZONS_DAYS", (365,))
    monkeypatch.setattr(sys, "argv", ["carry_buy_gate.py", "--series", "sealed"])
    cbg.main()
    out = capsys.readouterr().out
    assert "G3:RED" in out and "VERDICT: NOT READY" in out


def test_g5_red_below_80(tmp_path, monkeypatch):
    log = tmp_path / "paper.jsonl"
    lines = [f'{{"status": "closed", "R": 0.36}}' for _ in range(79)]
    log.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(cbg, "PAPER_JSONL", log)
    assert cbg.paper_gate()["status"] == "RED"
    log.write_text("\n".join(lines + ['{"status": "closed", "R": 0.36}']) + "\n")
    assert cbg.paper_gate()["status"] == "GREEN"


def test_g5_red_out_of_band(tmp_path, monkeypatch):
    log = tmp_path / "paper.jsonl"
    log.write_text("\n".join('{"status": "closed", "R": -0.10}' for _ in range(80)) + "\n")
    monkeypatch.setattr(cbg, "PAPER_JSONL", log)
    g5 = cbg.paper_gate()
    assert g5["status"] == "RED" and g5["n"] == 80
