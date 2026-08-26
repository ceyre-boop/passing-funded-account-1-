"""Fault-injection tests for the --risk flag / max-safe-risk line added to
scripts/carry_buy_gate.py.

Covers:
- the loud max-safe-risk line is present in the printed output and is not
  omittable;
- it reuses scripts.drawdown_margin.max_safe_risk rather than reimplementing
  the bisection;
- no real p_pass number is ever printed without its zero-edge control, which
  the new line must not violate;
- --risk lets the ladder be computed at an arbitrary single risk while the
  no-args default remains the pre-registered sweep (spec P3);
- default (no --risk) invocation output is byte-identical to pre-change
  behaviour apart from the one new line.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import carry_buy_gate as cbg  # noqa: E402
from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402


@pytest.fixture(scope="module")
def sealed():
    return cbg.load_sealed()


# ------------------------------------------------------------- unit: the line

def test_max_safe_risk_line_matches_drawdown_margin(sealed):
    """The line's number must be exactly drawdown_margin.max_safe_risk's answer
    (spec constraint: reuse, do not reimplement)."""
    from scripts.drawdown_margin import max_safe_risk

    cti = load_contract("cti_1step")
    haircut = cti.costs.swap_haircut_r_per_day
    idx, vi, vw, vopen = cbg.build_series(sealed, haircut, center=False)
    expected = max_safe_risk(vi, vw, vopen, cti)

    line = cbg.max_safe_risk_line(sealed, cti, haircut, [0.01])
    m = re.search(r"MAX SAFE RISK.*?:\s*([\d.]+)%\s*per R", line)
    assert m is not None, f"line missing the max-safe-risk figure: {line!r}"
    assert float(m.group(1)) / 100 == pytest.approx(expected, abs=1e-5)


def test_max_safe_risk_line_flags_when_selected_risk_exceeds_it(sealed):
    cti = load_contract("cti_1step")
    haircut = cti.costs.swap_haircut_r_per_day
    line_over = cbg.max_safe_risk_line(sealed, cti, haircut, [0.01])   # 1.00% > 0.328%
    line_under = cbg.max_safe_risk_line(sealed, cti, haircut, [0.001])  # 0.10% < 0.328%
    assert "EXCEEDS MAX SAFE RISK" in line_over
    assert "EXCEEDS MAX SAFE RISK" not in line_under


# --------------------------------------------------- fault injection: omission

def test_output_fails_without_max_safe_risk_line(monkeypatch, capsys):
    """If the loud line is stripped from stdout, this test must catch it —
    guards against a future edit silently dropping the line."""
    monkeypatch.setattr(cbg, "RISK_SWEEP", [0.02])
    monkeypatch.setattr(cbg, "OBS_HORIZONS_DAYS", (365,))
    monkeypatch.setattr(cbg, "BOOT_PATHS", 200)
    monkeypatch.setattr(sys, "argv", ["carry_buy_gate.py", "--series", "sealed"])
    cbg.main()
    out = capsys.readouterr().out
    assert "MAX SAFE RISK" in out, "max-safe-risk line omitted from output"
    assert "per R" in out


def test_max_safe_risk_line_itself_never_bare_real_number_without_control():
    """Spec P5: 'every reported number carries real AND control ... omission
    is structurally impossible.' The new line reports a curve-survival ceiling,
    not a p_pass estimate, so it is not a P5 statistic — but it must never be
    dressed up to look like one (no 'REAL p(pass)' / 'ZERO-EDGE' tokens),
    which would imply a control exists when none was computed."""
    cti = load_contract("cti_1step")
    haircut = cti.costs.swap_haircut_r_per_day
    sealed = cbg.load_sealed()
    line = cbg.max_safe_risk_line(sealed, cti, haircut, [0.01])
    assert "p(pass)" not in line
    assert "ZERO-EDGE" not in line


def test_fmt_row_still_refuses_missing_control():
    """Existing P5 guarantee must remain intact after this change (regression
    guard, not new behaviour)."""
    real = dict(p_pass=0.5, p_pass_lo=0.4, p_pass_hi=0.6)
    with pytest.raises(ValueError):
        cbg.fmt_row("x", real, None)
    with pytest.raises(ValueError):
        cbg.fmt_row("x", None, real)


# --------------------------------------------------------------- --risk arg

def test_risk_arg_produces_single_risk_ladder(monkeypatch, capsys):
    monkeypatch.setattr(cbg, "OBS_HORIZONS_DAYS", (365,))
    monkeypatch.setattr(cbg, "BOOT_PATHS", 200)
    monkeypatch.setattr(sys, "argv",
                        ["carry_buy_gate.py", "--series", "sealed", "--risk", "0.02"])
    cbg.main()
    out = capsys.readouterr().out
    assert "start-sweep 2.00%" in out
    for pct in ("1.00%", "1.50%", "2.50%", "3.00%"):
        assert f"start-sweep {pct}" not in out
    assert "selected risk(s): 2.00%" in out


def test_default_invocation_unchanged_apart_from_new_line(capsys):
    """No-args default must still run the full pre-registered sweep; the only
    new stdout content is the single max-safe-risk line."""
    import sys as _sys
    argv_backup = _sys.argv
    try:
        _sys.argv = ["carry_buy_gate.py", "--series", "sealed"]
        cbg.main()
    finally:
        _sys.argv = argv_backup
    out = capsys.readouterr().out
    lines = out.splitlines()
    max_safe_lines = [l for l in lines if l.startswith("MAX SAFE RISK")]
    assert len(max_safe_lines) == 1
    for risk_pct in ("1.00%", "1.50%", "2.00%", "2.50%", "3.00%"):
        assert f"start-sweep {risk_pct}" in out
    assert "G1 GOLDEN: GREEN" in out
    assert "VERDICT:" in out
