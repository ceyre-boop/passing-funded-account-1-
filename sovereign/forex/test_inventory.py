"""I64 — the driver halts on an unhashed or moved dependency. Never a warning."""
from pathlib import Path

import pytest

from sovereign.forex import inventory as inv


@pytest.fixture
def scratch_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(inv, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(inv, "INVENTORY", tmp_path / "inventory.json")
    monkeypatch.setattr(inv, "ROOT", tmp_path)
    f = tmp_path / "dep.bin"
    f.write_bytes(b"alpha")
    return f


def test_record_then_require_passes(scratch_inventory):
    f = scratch_inventory
    rec = inv.record([f], note="fixture")
    assert rec == {"dep.bin": inv.sha256_file(f)}
    assert inv.require_hashes([f]) == rec
    assert inv.load()["notes"]["dep.bin"] == "fixture"


def test_unhashed_dependency_halts(scratch_inventory):
    with pytest.raises(inv.InventoryError, match="UNHASHED"):
        inv.require_hashes([scratch_inventory])


def test_mutated_dependency_halts(scratch_inventory):
    f = scratch_inventory
    inv.record([f])
    f.write_bytes(b"alphb")  # one byte
    with pytest.raises(inv.InventoryError, match="MISMATCH"):
        inv.require_hashes([f])


def test_deleted_dependency_halts(scratch_inventory):
    f = scratch_inventory
    inv.record([f])
    f.unlink()
    with pytest.raises(inv.InventoryError, match="MISSING"):
        inv.require_hashes([f])


def test_all_failures_reported_at_once(scratch_inventory):
    f = scratch_inventory
    g = f.parent / "other.bin"
    g.write_bytes(b"x")
    inv.record([f])
    f.write_bytes(b"y")
    with pytest.raises(inv.InventoryError) as ei:
        inv.require_hashes([f, g])
    msg = str(ei.value)
    assert "MISMATCH dep.bin" in msg and "UNHASHED other.bin" in msg


def test_path_outside_root_refused(scratch_inventory):
    with pytest.raises(inv.InventoryError, match="outside"):
        inv.require_hashes([Path("/etc/hosts")])
