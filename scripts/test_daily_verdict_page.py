"""Mutation-oriented tests for the repointed daily verdict page — spec 021 P8."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_daily_verdict_page as page  # noqa: E402


def _write_state(tmp, *, age_days=0, gates=None, verdict="BUY"):
    d = tmp / "data" / "agent"
    d.mkdir(parents=True, exist_ok=True)
    if gates is None:
        gates = {g: {"status": "GREEN"} for g in ("G1", "G2", "G3", "G4", "G5")}
    ts = (datetime.now() - timedelta(days=age_days)).isoformat(timespec="seconds")
    (d / "carry_buy_gate_state.json").write_text(
        json.dumps(dict(timestamp=ts, gates=gates, verdict=verdict)))


def _run(tmp, monkeypatch, capsys):
    monkeypatch.setattr(page, "ROOT", tmp)
    page.main()
    out = capsys.readouterr().out
    html = (tmp / "daily_verdict.html").read_text()
    return out, html


def test_no_state_file_is_not_ready(tmp_path, monkeypatch, capsys):
    out, html = _run(tmp_path, monkeypatch, capsys)
    assert "NOT READY" in out and "NO TRUSTWORTHY GATE STATE" in html


def test_stale_ict_state_is_ignored(tmp_path, monkeypatch, capsys):
    """The legacy ICT snapshot must never influence the page, however green."""
    d = tmp_path / "data" / "agent"
    d.mkdir(parents=True)
    (d / "prop_challenge_state.json").write_text(json.dumps(dict(
        overall="GO", gates=[{"id": g, "status": "GREEN"} for g in
                             ("G1", "G2a", "G2b", "G3", "G4", "G5")])))
    out, html = _run(tmp_path, monkeypatch, capsys)
    assert "NOT READY" in out, "legacy ICT greens leaked into the verdict"


def test_fresh_all_green_buy_is_ready(tmp_path, monkeypatch, capsys):
    _write_state(tmp_path, age_days=0)
    out, _ = _run(tmp_path, monkeypatch, capsys)
    assert "READY — THE BUY GATE IS FULLY GREEN" in out


def test_eight_day_old_green_is_not_ready(tmp_path, monkeypatch, capsys):
    _write_state(tmp_path, age_days=8)
    out, html = _run(tmp_path, monkeypatch, capsys)
    assert "NOT READY" in out and "days old" in html


def test_missing_gate_key_is_not_ready(tmp_path, monkeypatch, capsys):
    gates = {g: {"status": "GREEN"} for g in ("G1", "G2", "G3", "G4")}  # no G5
    _write_state(tmp_path, gates=gates)
    out, _ = _run(tmp_path, monkeypatch, capsys)
    assert "NOT READY" in out


def test_wrong_gate_names_cannot_green_the_page(tmp_path, monkeypatch, capsys):
    """Fault: dropping the gate-name check. Five greens under bogus names with
    verdict=BUY must still be untrusted."""
    gates = {g: {"status": "GREEN"} for g in ("A1", "A2", "A3", "A4", "A5")}
    _write_state(tmp_path, gates=gates, verdict="BUY")
    out, _ = _run(tmp_path, monkeypatch, capsys)
    assert "NOT READY" in out


def test_one_red_gate_is_not_ready(tmp_path, monkeypatch, capsys):
    gates = {g: {"status": "GREEN"} for g in ("G1", "G2", "G3", "G4")}
    gates["G5"] = {"status": "RED", "why": "paper sprint incomplete"}
    _write_state(tmp_path, gates=gates, verdict="NOT READY")
    out, html = _run(tmp_path, monkeypatch, capsys)
    assert "NOT READY" in out
    assert "4 of 5 checks are green" in html


def test_verdict_word_alone_cannot_green_the_page(tmp_path, monkeypatch, capsys):
    """Fault: trusting state['verdict'] without checking the gates."""
    gates = {g: {"status": "RED"} for g in ("G1", "G2", "G3", "G4", "G5")}
    _write_state(tmp_path, gates=gates, verdict="BUY")
    out, _ = _run(tmp_path, monkeypatch, capsys)
    assert "NOT READY" in out
