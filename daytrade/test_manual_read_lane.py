#!/usr/bin/env python3
"""Manual read lane (Claude Code, blackout-only). The quarantine is the
point: these tests would fail the moment anyone wires manual_reads.jsonl
into anything the promotion gate or the operator's own scoring reads."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manual_read_lane as mrl
import news_archive as na

HERE = Path(__file__).resolve().parent

# Every source file in daytrade/ that reads the operator's scored artifacts
# (records.jsonl, forecasts.jsonl, evidence.jsonl, decision_ledger.jsonl) or
# sits on the promotion / scoring path. If a manual read is ever pooled into
# scoring, it will show up as a new reference in one of these files.
SCORING_PATH_READERS = [
    "alpha_operator.py", "forecast.py", "evidence.py", "decision_ledger.py",
    "mechanisms.py", "context_directive.py", "science_loop.py", "gap_log.py",
    "runner.py", "build_dashboard_data.py", "scorecard.py", "survival.py",
    "seals.py", "build_cockpit.py", "carry_sentinel.py", "fx_state.py",
    "macro_state.py", "residual_model.py", "stack_exam.py", "news_claude.py",
    "oracle_audit.py", "regret.py",
]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(mrl, "MANUAL_READS", tmp_path / "manual_reads.jsonl")
    monkeypatch.setattr(na, "ARCHIVE", tmp_path / "news_archive.jsonl")
    return tmp_path


def _archived(symbol="NVDA", article_id="a1"):
    row = {"kind": "headline", "symbol": symbol, "article_id": article_id,
           "archived_at": "2026-08-21T10:05:00+00:00",
           "published_utc": "2026-08-21T10:00:00+00:00", "publisher": "Reuters",
           "title": "Nvidia thing", "description": "desc", "url": "http://x",
           "polygon_sentiment": "positive", "insights": []}
    with na.ARCHIVE.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def _valid_payload(**overrides):
    p = {
        "symbol": "NVDA",
        "window_start": "2026-08-21T09:00:00+00:00",
        "window_end": "2026-08-21T11:00:00+00:00",
        "article_ids": ["a1"],
        "direction": "bullish",
        "confidence_label": "medium",
        "summary": "Read the headline, looks constructive.",
        "authored_by_model": "claude-opus-4-6",
    }
    p.update(overrides)
    return p


# ------------------------------------------------------------- the quarantine

def test_no_scoring_path_reader_mentions_manual_reads():
    """The test that MUST fail if someone later wires the two lanes together."""
    hits = []
    for name in SCORING_PATH_READERS:
        path = HERE / name
        if not path.exists():
            continue
        text = path.read_text()
        if "manual_reads" in text or "manual_read_lane" in text or "ManualRead" in text:
            hits.append(name)
    assert hits == [], f"scoring-path file(s) reference the manual lane: {hits}"


def test_no_py_file_in_daytrade_references_manual_reads_except_its_own_lane():
    """Broader net: scan every .py file in daytrade/, not just the named list."""
    allowed = {"manual_read_lane.py", "test_manual_read_lane.py"}
    hits = []
    for path in sorted(HERE.glob("*.py")):
        if path.name in allowed:
            continue
        text = path.read_text()
        if "manual_reads.jsonl" in text:
            hits.append(path.name)
    assert hits == [], f"unexpected reference(s) to manual_reads.jsonl: {hits}"


def test_manual_read_lane_module_imports_nothing_from_the_scored_pipeline():
    src = (HERE / "manual_read_lane.py").read_text()
    for forbidden in ("import alpha_operator", "import forecast", "import evidence",
                       "import decision_ledger", "from forecast import",
                       "from evidence import", "from decision_ledger import"):
        assert forbidden not in src, f"manual_read_lane.py imports {forbidden!r}"


def test_record_writes_only_to_manual_reads_file(sandbox):
    _archived()
    mrl.record(_valid_payload())
    assert mrl.MANUAL_READS.exists()
    # nothing else materialized in the sandbox
    other_files = {p.name for p in sandbox.iterdir()} - {"manual_reads.jsonl", "news_archive.jsonl"}
    assert other_files == set()


def test_manual_read_id_prefix_cannot_collide_with_operator_namespace(sandbox):
    _archived()
    mr = mrl.record(_valid_payload())
    assert mr.manual_read_id.startswith("mr-")
    assert not mr.manual_read_id.startswith("op-")
    assert not mr.manual_read_id.startswith("fc-op-")
    assert not mr.manual_read_id.startswith("dir-op-")


def test_authored_by_states_claude_code_not_api(sandbox):
    _archived()
    mr = mrl.record(_valid_payload())
    assert "Claude Code" in mr.authored_by
    assert "not Anthropic API" in mr.authored_by
    assert "claude-opus-4-6" in mr.authored_by


def test_source_is_always_manual():
    with pytest.raises(mrl.ManualReadError):
        mrl.ManualRead(
            manual_read_id="mr-x", symbol="NVDA", window_start="2026-08-21T09:00:00+00:00",
            window_end="2026-08-21T11:00:00+00:00", article_ids=("a1",),
            direction="bullish", confidence_label="medium", summary="s",
            authored_by="model via Claude Code", authored_at="2026-08-21T11:00:00+00:00",
            source="live",           # not allowed in this lane
        )


# -------------------------------------------------- forecast-shape unrepresentable

def test_manual_read_cannot_be_constructed_with_scenario_probs():
    """The dataclass has no such field — Python itself refuses the construction."""
    with pytest.raises(TypeError):
        mrl.ManualRead(
            manual_read_id="mr-x", symbol="NVDA", window_start="2026-08-21T09:00:00+00:00",
            window_end="2026-08-21T11:00:00+00:00", article_ids=("a1",),
            direction="bullish", confidence_label="medium", summary="s",
            authored_by="model via Claude Code", authored_at="2026-08-21T11:00:00+00:00",
            scenario_probs={"bull_continuation": 1.0},        # not a real field
        )


def test_manual_read_cannot_be_constructed_with_expected_r():
    with pytest.raises(TypeError):
        mrl.ManualRead(
            manual_read_id="mr-x", symbol="NVDA", window_start="2026-08-21T09:00:00+00:00",
            window_end="2026-08-21T11:00:00+00:00", article_ids=("a1",),
            direction="bullish", confidence_label="medium", summary="s",
            authored_by="model via Claude Code", authored_at="2026-08-21T11:00:00+00:00",
            expected_r=1.5,
        )


@pytest.mark.parametrize("key,value", [
    ("scenario_probs", {"bull_continuation": 0.5, "bear_continuation": 0.5}),
    ("expected_r", 2.0),
    ("expected_r_band", [0.5, 1.5]),
    ("probability", 0.8),
    ("probabilities", {"up": 0.6}),
    ("recommendation", "BUY"),
    ("confidence", 0.9),
])
def test_record_refuses_forecast_shaped_input_payload(sandbox, key, value):
    """Even before construction is attempted: a payload merely NAMING a
    forecast-shaped field is refused."""
    _archived()
    payload = _valid_payload(**{key: value})
    with pytest.raises(mrl.ManualReadError, match="forecast-shaped"):
        mrl.record(payload)
    assert not mrl.MANUAL_READS.exists() or mrl.MANUAL_READS.read_text() == ""


# --------------------------------------------------------------- validation

def test_record_requires_all_fields(sandbox):
    _archived()
    for key in ("symbol", "window_start", "window_end", "article_ids",
                "direction", "confidence_label", "summary", "authored_by_model"):
        payload = _valid_payload()
        del payload[key]
        with pytest.raises(mrl.ManualReadError):
            mrl.record(payload)


def test_record_rejects_unknown_direction(sandbox):
    _archived()
    with pytest.raises(mrl.ManualReadError):
        mrl.record(_valid_payload(direction="mooning"))


def test_record_rejects_unknown_confidence_label(sandbox):
    _archived()
    with pytest.raises(mrl.ManualReadError):
        mrl.record(_valid_payload(confidence_label="extremely high"))


def test_record_rejects_article_id_not_in_archive(sandbox):
    _archived()
    with pytest.raises(mrl.ManualReadError, match="not found in the archive"):
        mrl.record(_valid_payload(article_ids=["nonexistent"]))


def test_record_rejects_empty_article_ids(sandbox):
    _archived()
    with pytest.raises(mrl.ManualReadError):
        mrl.record(_valid_payload(article_ids=[]))


def test_record_rejects_non_iso_timestamp(sandbox):
    _archived()
    with pytest.raises(mrl.ManualReadError):
        mrl.record(_valid_payload(window_start="not-a-date"))


def test_record_rejects_malformed_json_via_cli(sandbox, capsys):
    rc = mrl.main(["record", "--file", "/dev/null"])   # empty input, invalid JSON
    assert rc == 1


def test_valid_record_round_trips(sandbox):
    _archived()
    mr = mrl.record(_valid_payload())
    on_disk = json.loads(mrl.MANUAL_READS.read_text().splitlines()[0])
    assert on_disk["manual_read_id"] == mr.manual_read_id
    assert on_disk["source"] == "manual"
    assert on_disk["article_ids"] == ["a1"]


# --------------------------------------------------------------------- emit

def test_emit_packet_excludes_polygon_sentiment(sandbox):
    _archived()
    packet = mrl.emit_packet("NVDA", since_hours=999999)
    assert "a1" in packet
    assert "Nvidia thing" in packet
    assert "positive" not in packet          # polygon_sentiment must not leak into the prompt


def test_emit_packet_empty_window_says_so(sandbox):
    packet = mrl.emit_packet("NVDA", since_hours=1)
    assert "no archived headlines" in packet
