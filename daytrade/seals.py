#!/usr/bin/env python3
"""SEAL REGISTRY — spec 028. Pre-commitment as mechanism.

Past-you, who didn't yet have a stake in the outcome, binds present-you, who
does. Every sealed artifact is registered, hashed, and condition-bound in
SEALS.json; the test suite re-hashes them on every run; and unsealing
anything requires writing the reason down BEFORE looking.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "SEALS.json"
INTENTS = ROOT / "data" / "daytrade" / "unseal_intents.jsonl"

DOCTRINE = ("unsealing anything requires writing the reason down before "
            "looking — run `seals.py intent <path> --reason \"…\"` first, "
            "blind, then unseal. The moment a check gets uncomfortable is "
            "exactly the moment its authority is doing its job.")


class SealError(RuntimeError):
    """A seal being argued with. Never downgraded to a warning."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if not REGISTRY.exists():
        return {"spec": "028", "seals": []}
    try:
        return json.loads(REGISTRY.read_text())
    except json.JSONDecodeError as e:
        raise SealError(f"SEALS.json is not JSON ({e}) — refusing to guess")


def _save(reg: dict) -> None:
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1))
    os.replace(tmp, REGISTRY)


def _append_intent(entry: dict) -> None:
    INTENTS.parent.mkdir(parents=True, exist_ok=True)
    with INTENTS.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _entry(reg: dict, relpath: str) -> dict:
    for s in reg["seals"]:
        if s["path"] == relpath:
            return s
    raise SealError(f"{relpath} is not in the registry")


def seal(path: str, condition: str, by: str) -> int:
    p = (ROOT / path).resolve()
    if not p.exists():
        raise SealError(f"{path} does not exist")
    rel = str(p.relative_to(ROOT))
    dirty = subprocess.run(["git", "status", "--porcelain", rel], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        raise SealError(f"{rel} has uncommitted changes — past-you must be on "
                        "the record before the seal binds. Commit first. (I27)")
    reg = _load()
    if any(s["path"] == rel and s["status"] == "sealed" for s in reg["seals"]):
        raise SealError(f"{rel} is already sealed")
    reg["seals"].append({"path": rel, "sha256": _sha(p), "sealed_at": _now(),
                         "sealed_by": by, "unseal_condition": condition,
                         "status": "sealed"})
    _save(reg)
    print(f"  SEALED {rel}\n  condition: {condition}")
    return 0


def intent(path: str, reason: str, by: str) -> int:
    if not reason.strip():
        raise SealError("an empty reason is not a reason")
    entry = {"ts": _now(), "path": path, "reason": reason, "by": by,
             "consumed": False}
    _append_intent(entry)
    print(f"  INTENT LOGGED (blind, before any look):\n  {json.dumps(entry)}")
    return 0


def unseal(path: str, by: str) -> int:
    reg = _load()
    s = _entry(reg, path)
    if s["status"] != "sealed":
        raise SealError(f"{path} is {s['status']}, not sealed")
    intents = ([json.loads(l) for l in INTENTS.open() if l.strip()]
               if INTENTS.exists() else [])
    open_intents = [i for i in intents if i["path"] == path
                    and not i.get("consumed")]
    if not open_intents:
        raise SealError(f"no logged intent for {path} — {DOCTRINE} (I24)")
    used = open_intents[0]
    s["status"] = "unsealed"
    s["unsealed_at"] = _now()
    s["unsealed_by"] = by
    s["intent"] = used
    _save(reg)
    _append_intent({"ts": _now(), "path": path, "consumed_intent_ts": used["ts"],
                    "event": "UNSEALED", "by": by})
    print(f"  UNSEALED {path}\n  under intent [{used['ts']}]: {used['reason']}")
    return 0


def check(quiet: bool = False) -> int:
    reg = _load()
    failures = []
    n = 0
    for s in reg["seals"]:
        if s["status"] != "sealed" or s["path"] is None:
            continue                       # I26: history/boundaries, not contraband
        p = ROOT / s["path"]
        if not p.exists():
            failures.append(f"{s['path']}: MISSING")
            continue
        n += 1
        if _sha(p) != s["sha256"]:
            failures.append(f"{s['path']}: HASH DRIFT — sealed bytes changed "
                            "without an unseal (I23)")
    if failures:
        for f in failures:
            print(f"  !! SEAL VIOLATION: {f}")
        return 1
    if not quiet:
        print(f"  {n} sealed artifact(s) verified, 0 drifted")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 028 — the seal registry")
    ap.add_argument("--by", default=os.environ.get("USER", "unknown"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seal"); s.add_argument("path")
    s.add_argument("--condition", required=True)
    i = sub.add_parser("intent"); i.add_argument("path")
    i.add_argument("--reason", required=True)
    u = sub.add_parser("unseal"); u.add_argument("path")
    sub.add_parser("check")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "seal":
            return seal(a.path, a.condition, a.by)
        if a.cmd == "intent":
            return intent(a.path, a.reason, a.by)
        if a.cmd == "unseal":
            return unseal(a.path, a.by)
        return check()
    except SealError as e:
        print(f"  REFUSED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
