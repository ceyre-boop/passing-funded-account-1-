#!/usr/bin/env python3
"""The frozen checkpoint (stack exam LL-2) and the self-exclusion invariant
(the shape behind M9, M13, M26 and SF-1)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frozen_policy as fp
import measure


# ─────────────────────────── SF-FROZEN-001 ──────────────────────────────

def test_checkpoint_is_intact_always_on():
    """THE guard: the frozen opponent has not moved. Any edit to the engine
    or the pinned params fails every suite run until it is faced."""
    assert fp.verify(quiet=True) == 0, (
        "SF-FROZEN-001 drifted — every measurement compared against it was "
        "against a different engine than the one running now")


def test_policy_returns_the_pinned_target():
    for cls in ("SINGLE_NAME", "CASH_INDEX", "FUTURES"):
        assert fp.policy(cls) == fp.POLICIES[cls]


def test_engine_drift_is_detected(tmp_path, monkeypatch):
    rec = json.loads(fp.RECORD.read_text())
    rec["engine_sha256"] = "0" * 64
    p = tmp_path / "cp.json"
    p.write_text(json.dumps(rec))
    monkeypatch.setattr(fp, "RECORD", p)
    assert fp.verify(quiet=True) == 1
    with pytest.raises(fp.CheckpointError, match="drifted"):
        fp.policy("FUTURES")


def test_policy_drift_is_detected(tmp_path, monkeypatch):
    rec = json.loads(fp.RECORD.read_text())
    rec["policies_sha256"] = "0" * 64
    p = tmp_path / "cp.json"
    p.write_text(json.dumps(rec))
    monkeypatch.setattr(fp, "RECORD", p)
    assert fp.verify(quiet=True) == 1


def test_repinning_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "cp.json"
    p.write_text("{}")
    monkeypatch.setattr(fp, "RECORD", p)
    with pytest.raises(fp.CheckpointError, match="already exists"):
        fp.pin()


def test_checkpoint_refuses_a_class_it_does_not_price():
    with pytest.raises(fp.CheckpointError, match="prices no policy"):
        fp.policy("FX_CARRY")          # SF-3: it is blind to carry, by design


def test_exit_quality_compares_against_the_pin():
    import exit_quality as eq
    assert eq.SHIPPED_FOR("FUTURES") == fp.POLICIES["FUTURES"]
    report = json.loads((Path(fp.ROOT) / "data" / "daytrade"
                         / "exit_quality.json").read_text())
    assert report["compared_against"] == fp.CHECKPOINT_ID


# ──────────────────── the self-exclusion invariant ──────────────────────

def test_exclude_self_removes_the_caller(tmp_path):
    me = Path(__file__).resolve()
    other = tmp_path / "other.py"
    other.write_text("x")
    kept = measure.exclude_self([me, other], self_path=me)
    assert kept == [other.resolve()]


def test_a_no_op_filter_is_refused(tmp_path):
    """The failure mode is a filter that removes nothing while looking like
    protection — that is how SF-1 survived its first run."""
    me = Path(__file__).resolve()
    other = tmp_path / "other.py"
    other.write_text("x")
    with pytest.raises(measure.MeasurementError, match="no-op"):
        measure.exclude_self([other], self_path=me)
    # ...and opting out is allowed, but must be explicit
    assert measure.exclude_self([other], self_path=me,
                                require_exclusion=False) == [other.resolve()]


def test_grep_sources_excludes_the_instrument():
    """The literal SF-1 error: a grep for a pattern the instrument contains."""
    here = Path(__file__).resolve().parent
    hits = measure.grep_sources("def exclude_self", here,
                                self_path=Path(measure.__file__).resolve())
    assert all(h.name != "measure.py" for h in hits)


def test_assert_disjoint_catches_reading_own_output(tmp_path):
    a, b = tmp_path / "in.json", tmp_path / "out.json"
    measure.assert_disjoint([a], [b])                  # fine
    with pytest.raises(measure.MeasurementError, match="own exhaust"):
        measure.assert_disjoint([a, b], [b], label="audit")


def test_stack_exam_uses_the_asserted_helper():
    src = (Path(__file__).resolve().parent / "stack_exam.py").read_text()
    assert "measure.grep_sources" in src, \
        "the exam must use the asserted helper, not a hand-rolled filter"
