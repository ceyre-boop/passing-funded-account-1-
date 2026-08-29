"""sovereign/forex/inventory.py — content hashes for everything an evaluation depends on.

Guardrail (session brief 2026-08-29): any script that writes a configuration file,
a discretization table, a checkpoint, or a dataset that an evaluation gate reads
must record that file's sha256 here BEFORE the gate runs. A gate that finds a
dependency with no recorded hash, or whose bytes no longer match, HALTS — it
never warns and never proceeds on a best guess.

The inventory is `artifacts/inventory.json`:
    {"generated": "<iso>", "head": "<git sha>", "hashes": {"<relpath>": "<sha256>"},
     "notes": {"<relpath>": "<why this file matters>"}}

Paths are stored relative to the repo root so the file is portable across
worktrees. Nothing in here reads data — it hashes bytes.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
INVENTORY = ARTIFACTS / "inventory.json"


class InventoryError(RuntimeError):
    """A dependency is unhashed or its bytes moved. Never downgraded to a warning."""


def sha256_file(path: Path | str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.is_file():
        raise InventoryError(f"cannot hash {p}: not a file")
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: Path | str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.relative_to(ROOT)
        except ValueError as e:
            raise InventoryError(f"{path} is outside the repo root {ROOT}") from e
    return p.as_posix()


def _head() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 — git absent in some sandboxes; the hash map is the authority
        return "unknown"


def load() -> dict:
    if not INVENTORY.is_file():
        return {"generated": None, "head": None, "hashes": {}, "notes": {}}
    with INVENTORY.open() as fh:
        inv = json.load(fh)
    inv.setdefault("hashes", {})
    inv.setdefault("notes", {})
    return inv


def _write(inv: dict) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    inv["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inv["head"] = _head()
    inv["hashes"] = dict(sorted(inv["hashes"].items()))
    inv["notes"] = dict(sorted(inv["notes"].items()))
    tmp = INVENTORY.with_suffix(".json.tmp")
    with tmp.open("w") as fh:
        json.dump(inv, fh, indent=2, sort_keys=False)
        fh.write("\n")
    tmp.replace(INVENTORY)


def record(paths: Iterable[Path | str] | Mapping[Path | str, str], *, note: str | None = None) -> dict[str, str]:
    """Hash each path and merge into the inventory. Returns {relpath: sha256}.

    `paths` may be a mapping {path: note}. Re-recording a path OVERWRITES its hash —
    that is the point (the file was regenerated); the git history of inventory.json
    is the audit trail of when it changed.
    """
    items = list(paths.items()) if isinstance(paths, Mapping) else [(p, note) for p in paths]
    inv = load()
    out: dict[str, str] = {}
    for p, n in items:
        rel = _rel(p)
        out[rel] = sha256_file(p)
        inv["hashes"][rel] = out[rel]
        if n:
            inv["notes"][rel] = n
    _write(inv)
    return out


def require_hashes(paths: Iterable[Path | str]) -> dict[str, str]:
    """HALT unless every path is recorded AND its current bytes match the record.

    Collects every failure before raising so the message names all of them.
    """
    inv = load()
    failures: list[str] = []
    out: dict[str, str] = {}
    for p in paths:
        rel = _rel(p)
        recorded = inv["hashes"].get(rel)
        if recorded is None:
            failures.append(f"UNHASHED {rel}: no entry in {INVENTORY.relative_to(ROOT)}")
            continue
        try:
            current = sha256_file(p)
        except InventoryError as e:
            failures.append(f"MISSING {rel}: {e}")
            continue
        if current != recorded:
            failures.append(f"MISMATCH {rel}: recorded {recorded[:12]}… current {current[:12]}…")
            continue
        out[rel] = current
    if failures:
        raise InventoryError("inventory guardrail — halting:\n  " + "\n  ".join(failures))
    return out
