"""Tests for decision-log -> journal projection.

The decision log is shared by trade decisions and non-trade records that reuse
the same schema (spec 021 provenance waivers, log corrections). The projection
must emit only real entries.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import experience.journal_sync as js  # noqa: E402


def _row(**over):
    base = dict(entry_timestamp="2026-08-12T12:00:00+00:00", system="FOREX",
                pair="EURUSD=X", direction="LONG", risk_pct=0.01,
                why_this_trade="paper_carry_sprint_021", outcome=None)
    base.update(over)
    return base


def _write(tmp_path, rows):
    f = tmp_path / "decisions_2026_08.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return f


def test_real_trades_are_projected(tmp_path, monkeypatch):
    monkeypatch.setattr(js, "DECISION_DIR", tmp_path)
    monkeypatch.setattr(js, "ROOT", tmp_path)
    _write(tmp_path, [_row(), _row(direction="SHORT", pair="USDJPY=X")])
    rows = js.rows_from_decision_logs()
    assert len(rows) == 2
    assert {r["pair"] for r in rows} == {"EURUSD", "USDJPY"}


def test_non_trade_records_are_not_projected_as_entries(tmp_path, monkeypatch):
    """Fault: a waiver or correction row reaching the journal as an ENTER.
    Both use system=FOREX, so filtering on system alone is not enough."""
    monkeypatch.setattr(js, "DECISION_DIR", tmp_path)
    monkeypatch.setattr(js, "ROOT", tmp_path)
    _write(tmp_path, [
        _row(),
        _row(pair="OOS_ARTIFACT", direction="WAIVER", risk_pct=0,
             why_this_trade="spec_021_oos_provenance_waiver"),
        _row(pair="DECISION_LOG", direction="CORRECTION", risk_pct=0,
             why_this_trade="spec_021_log_correction"),
    ])
    rows = js.rows_from_decision_logs()
    assert len(rows) == 1, "only the real trade may be projected"
    assert rows[0]["pair"] == "EURUSD"
    assert all(r["action"] == "ENTER" for r in rows)
    assert "OOSARTIFACT" not in {r["pair"] for r in rows}
    assert "DECISIONLOG" not in {r["pair"] for r in rows}
