"""Tests for the paper carry log — spec 021 G5."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import paper_carry_log as pcl  # noqa: E402
import carry_buy_gate as cbg  # noqa: E402
from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402

HAIRCUT = load_contract("cti_1step").costs.swap_haircut_r_per_day


def test_r_matches_hand_computation():
    # LONG: entry 1.0850, stop 1.0790 (risk 0.005530), exit 1.0930 (+0.007373),
    # 5 hold days -> R = 0.007373/0.005530 - 5*haircut
    r = pcl.compute_r("LONG", 1.0850, 1.0790, 1.0930, 5, HAIRCUT)
    expected = ((1.0930 - 1.0850) / 1.0850) / ((1.0850 - 1.0790) / 1.0850) - 5 * HAIRCUT
    assert r == pytest.approx(expected)
    assert r == pytest.approx(0.08 / 0.06 - 5 * HAIRCUT, abs=1e-9)


def test_r_short_direction_sign():
    r = pcl.compute_r("SHORT", 1.0850, 1.0910, 1.0790, 1, HAIRCUT)
    assert r == pytest.approx(0.06 / 0.06 - HAIRCUT)


def test_zero_risk_rejected():
    with pytest.raises(ValueError):
        pcl.compute_r("LONG", 1.0, 1.0, 1.1, 1, HAIRCUT)


def test_haircut_uses_contract_constant_not_a_local_copy():
    """Fault: re-declaring the haircut locally. The module must have no numeric
    haircut literal; it imports the contract's."""
    import inspect
    src = inspect.getsource(pcl)
    assert "0.004" not in src, "haircut re-declared locally instead of imported"
    assert "swap_haircut_r_per_day" in src


def test_g5_counts_only_closed_trades(tmp_path, monkeypatch):
    log = tmp_path / "paper.jsonl"
    monkeypatch.setattr(pcl, "LOG_PATH", log)
    monkeypatch.setattr(cbg, "PAPER_JSONL", log)
    recs = ([dict(id=f"o{i}", status="open", R=None) for i in range(5)] +
            [dict(id=f"c{i}", status="closed", R=0.36) for i in range(79)])
    pcl._write(recs)
    g5 = cbg.paper_gate()
    assert g5["status"] == "RED" and g5["n"] == 79
    recs.append(dict(id="c79", status="closed", R=0.36))
    pcl._write(recs)
    assert cbg.paper_gate()["status"] == "GREEN"


def test_min_n_matches_evaluator():
    assert pcl.G5_MIN_N == cbg.G5_MIN_N


def test_double_close_rejected(tmp_path, monkeypatch, capsys):
    log = tmp_path / "paper.jsonl"
    monkeypatch.setattr(pcl, "LOG_PATH", log)
    pcl._write([dict(id="t1", status="closed", R=0.1, pair="EURUSD=X",
                     direction="LONG", entry=1.0, stop=0.99, entry_date="2026-08-01")])
    class A:
        id = "t1"; exit = 1.01; date = "2026-08-05"; reason = "x"
    with pytest.raises(SystemExit):
        pcl.cmd_close(A)
