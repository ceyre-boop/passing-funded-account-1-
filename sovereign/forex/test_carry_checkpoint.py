"""CARRY-FROZEN-001 — the opponent is pinned; any moved byte, present calibration file,
changed constant, or re-pin attempt fails. Each test is a deliberate violation."""
import json

import pytest

from sovereign.forex import carry_checkpoint as cc


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Pin a synthetic opponent under a temp root so the real record is never touched."""
    root = tmp_path
    (root / "eng").mkdir()
    files = []
    for name in ("a.py", "b.parquet"):
        p = root / "eng" / name
        p.write_bytes(name.encode() * 3)
        files.append(f"eng/{name}")
    monkeypatch.setattr(cc, "ROOT", root)
    monkeypatch.setattr(cc, "RECORD", root / "CARRY_FROZEN_001.json")
    monkeypatch.setattr(cc, "PINNED_FILES", tuple(files))
    monkeypatch.setattr(cc, "MUST_BE_ABSENT", ("calib.json",))
    monkeypatch.setattr(cc, "_config_snapshot", lambda: {"STOP_ATR_MULT": 2.0, "PAIR": {"X": 1.25}})
    return root


def test_pin_then_verify_intact(scratch):
    rec = cc.pin(why="fixture")
    assert set(rec["files"]) == {"eng/a.py", "eng/b.parquet"}
    assert cc.verify(quiet=True) == 0


def test_repin_refused(scratch):
    cc.pin(why="fixture")
    with pytest.raises(cc.CheckpointError, match="re-pinning is refused"):
        cc.pin(why="again")


def test_pin_requires_why(scratch):
    with pytest.raises(cc.CheckpointError, match="why"):
        cc.pin(why="  ")


def test_one_byte_in_engine_fails_verify(scratch):
    cc.pin(why="fixture")
    p = scratch / "eng" / "a.py"
    p.write_bytes(p.read_bytes()[:-1] + b"X")
    assert cc.verify(quiet=True) == 1


def test_missing_pinned_file_fails_verify(scratch):
    cc.pin(why="fixture")
    (scratch / "eng" / "b.parquet").unlink()
    assert cc.verify(quiet=True) == 1


def test_calibration_file_appearing_fails_verify(scratch):
    cc.pin(why="fixture")
    (scratch / "calib.json").write_text("{}")
    assert cc.verify(quiet=True) == 1


def test_calibration_present_at_pin_refused(scratch):
    (scratch / "calib.json").write_text("{}")
    with pytest.raises(cc.CheckpointError, match="exists"):
        cc.pin(why="fixture")


def test_changed_constant_fails_verify(scratch, monkeypatch):
    cc.pin(why="fixture")
    monkeypatch.setattr(cc, "_config_snapshot", lambda: {"STOP_ATR_MULT": 2.5, "PAIR": {"X": 1.25}})
    assert cc.verify(quiet=True) == 1


def test_attach_once_then_refused(scratch):
    cc.pin(why="fixture")
    d = scratch / "paths.parquet"
    d.write_bytes(b"paths")
    cc.attach("paths", "paths.parquet", why="frozen paths")
    assert cc.verify(quiet=True) == 0
    with pytest.raises(cc.CheckpointError, match="already attached"):
        cc.attach("paths", "paths.parquet", why="again")
    d.write_bytes(b"pathz")
    assert cc.verify(quiet=True) == 1


def test_real_record_config_snapshot_reads_code(monkeypatch):
    """The real snapshot must come from ForexBacktester, and the incumbent's known values must be there."""
    snap = cc._config_snapshot()
    assert snap["STOP_ATR_MULT"] == 2.0 and snap["TRAILING_ATR_MULT"] == 1.25
    assert "SPREAD_COST" in snap and "SWAP_RATES_ANNUAL" in snap
    json.dumps(snap)  # serialisable
