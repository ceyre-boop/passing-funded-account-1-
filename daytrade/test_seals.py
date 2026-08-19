#!/usr/bin/env python3
"""Spec 028 invariants. test_registry_integrity is the always-on guard: it
runs on every suite invocation, so a sealed artifact drifting fails every
test run until restored or properly unsealed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seals


def test_i23_registry_integrity_always_on():
    """THE guard: every sealed artifact's bytes match its registered hash."""
    assert seals.check(quiet=True) == 0, \
        "a sealed artifact drifted — see `python3 daytrade/seals.py check`"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(seals, "ROOT", tmp_path)
    monkeypatch.setattr(seals, "REGISTRY", tmp_path / "SEALS.json")
    monkeypatch.setattr(seals, "INTENTS", tmp_path / "unseal_intents.jsonl")
    target = tmp_path / "artifact.json"
    target.write_text('{"claim": "frozen"}')
    (tmp_path / "SEALS.json").write_text(json.dumps({"spec": "028", "seals": [
        {"path": "artifact.json", "sha256": seals._sha(target),
         "sealed_at": "t0", "sealed_by": "test",
         "unseal_condition": "stated trigger", "status": "sealed"}]}))
    return tmp_path


def test_i23_drift_fails_check(sandbox):
    assert seals.check(quiet=True) == 0
    (sandbox / "artifact.json").write_text('{"claim": "quietly edited"}')
    assert seals.check(quiet=True) == 1


def test_i23_missing_sealed_file_fails(sandbox):
    (sandbox / "artifact.json").unlink()
    assert seals.check(quiet=True) == 1


def test_i24_unseal_without_intent_refused(sandbox):
    with pytest.raises(seals.SealError, match="before looking"):
        seals.unseal("artifact.json", by="test")
    # registry unchanged
    reg = json.loads((sandbox / "SEALS.json").read_text())
    assert reg["seals"][0]["status"] == "sealed"


def test_i24_intent_then_unseal_succeeds_and_records(sandbox):
    seals.intent("artifact.json", "condition met: stated trigger fired", by="test")
    assert seals.unseal("artifact.json", by="test") == 0
    reg = json.loads((sandbox / "SEALS.json").read_text())
    assert reg["seals"][0]["status"] == "unsealed"
    assert "condition met" in reg["seals"][0]["intent"]["reason"]
    events = [json.loads(l) for l in (sandbox / "unseal_intents.jsonl").open()]
    assert events[-1]["event"] == "UNSEALED"


def test_i25_empty_reason_refused(sandbox):
    with pytest.raises(seals.SealError, match="not a reason"):
        seals.intent("artifact.json", "   ", by="test")


def test_i26_unsealed_and_retired_exempt_from_hashing(sandbox):
    seals.intent("artifact.json", "trigger fired", by="test")
    seals.unseal("artifact.json", by="test")
    (sandbox / "artifact.json").write_text("changed after honest unseal")
    assert seals.check(quiet=True) == 0     # history, not contraband


def test_i27_seal_refuses_dirty_target(sandbox, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": " M artifact2.json"})())
    (sandbox / "artifact2.json").write_text("{}")
    with pytest.raises(seals.SealError, match="uncommitted"):
        seals.seal("artifact2.json", "cond", by="test")
