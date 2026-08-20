#!/usr/bin/env python3
"""HASH-CHAINED APPEND-ONLY LOG — one implementation, used by every ledger.

Extracted from decision_ledger (spec 030) when the FX state vector needed the
same primitive: each row carries the sha256 of the row before it, so a
rewritten past breaks the chain rather than passing unnoticed. Rule 1 — the
repo has one of these, not one per ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class ChainError(RuntimeError):
    """Corrupt or tampered log. Never repaired silently."""


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.open(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ChainError(f"{path.name}:{i} is not JSON ({e})") from e
    return out


def row_hash(row: dict) -> str:
    body = {k: v for k, v in row.items() if k != "row_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def append(path: Path, row: dict) -> dict:
    """Append + fsync. An audit row the OS buffered and lost is a row that
    never happened, at the worst possible moment."""
    prev = rows(path)
    row["seq"] = len(prev)
    row["prev_sha256"] = prev[-1]["row_sha256"] if prev else None
    row["row_sha256"] = row_hash(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return row


def verify(path: Path, *, quiet: bool = False) -> int:
    prev_hash = None
    all_rows = rows(path)
    for r in all_rows:
        if r.get("prev_sha256") != prev_hash:
            print(f"  !! CHAIN BREAK at seq {r.get('seq')} in {path.name}")
            return 1
        if row_hash(r) != r.get("row_sha256"):
            print(f"  !! ROW TAMPERED at seq {r.get('seq')} in {path.name}")
            return 1
        prev_hash = r["row_sha256"]
    if not quiet:
        print(f"  {len(all_rows)} row(s) in {path.name}, chain intact")
    return 0
