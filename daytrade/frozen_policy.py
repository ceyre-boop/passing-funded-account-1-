#!/usr/bin/env python3
"""SF-FROZEN-001 — the pinned exit-policy checkpoint.

Stack exam LL-2 recorded the gap: mechanisms were measured against "the
shipped policy", which is stable but not PINNED. Nothing stopped the
comparison target from drifting under a future edit, so "better" could have
meant "the baseline moved". Beating yesterday's self is drift; beating a
frozen opponent is progress.

This pins it. The checkpoint carries the exact policy parameters AND a content
hash of the engine that executes them, so an edit to `stockfish_exit.py`
breaks verification loudly instead of silently redefining what everything was
measured against.

It becomes load-bearing the moment a second evaluator exists: once there is a
carry exit evaluator beside the intraday one, "the shipped policy" is
ambiguous — shipped which? A pinned id is not.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE = HERE / "stockfish_exit.py"
RECORD = ROOT / "data" / "daytrade" / "SF_FROZEN_001.json"

CHECKPOINT_ID = "SF-FROZEN-001"
PINNED_AT = "2026-08-20"

# The exact parameters in force per asset class at pin time. Copied here
# deliberately rather than imported: if the source moves, this record must
# still say what the comparison target WAS.
POLICIES = {
    "SINGLE_NAME": {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
                    "flatten_et": None, "hold_past_tp2": True},
    "CASH_INDEX": {"trail_mult": 1.5, "be_arm_frac": 1.0, "partial_frac": 0.5,
                   "flatten_et": None, "hold_past_tp2": True},
    "FUTURES": {"trail_mult": 1.5, "be_arm_frac": 1.0, "partial_frac": 0.5,
                "flatten_et": None, "hold_past_tp2": True},
}
PRICES = "intraday equity/futures positions only — see SF-3, it has no term " \
         "for swap accrual, days held, weekend exposure, or rate differential"


class CheckpointError(RuntimeError):
    """The frozen opponent moved. Never downgraded to a warning."""


def engine_sha256() -> str:
    return hashlib.sha256(ENGINE.read_bytes()).hexdigest()


def policies_sha256() -> str:
    return hashlib.sha256(
        json.dumps(POLICIES, sort_keys=True).encode()).hexdigest()


def pin() -> dict:
    """Write the checkpoint record. Refuses to overwrite an existing pin —
    re-pinning is a deliberate act with its own reason, not a side effect."""
    if RECORD.exists():
        raise CheckpointError(
            f"{RECORD.name} already exists. Re-pinning redefines what every "
            "prior measurement was compared against; delete it deliberately, "
            "with the reason recorded, or pin a NEW checkpoint id.")
    body = {"checkpoint_id": CHECKPOINT_ID, "pinned_at": PINNED_AT,
            "engine": str(ENGINE.relative_to(ROOT)),
            "engine_sha256": engine_sha256(),
            "policies": POLICIES, "policies_sha256": policies_sha256(),
            "prices": PRICES,
            "why": "stack exam LL-2: improvement must be measured against a "
                   "frozen opponent, not against a moving self"}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(body, indent=1))
    return body


def load() -> dict:
    if not RECORD.exists():
        raise CheckpointError(f"no checkpoint at {RECORD} — run `pin` first")
    return json.loads(RECORD.read_text())


def verify(*, quiet: bool = False) -> int:
    """Has the frozen opponent moved? Engine bytes and policy params both."""
    rec = load()
    problems = []
    if rec["engine_sha256"] != engine_sha256():
        problems.append(
            f"ENGINE DRIFT: {rec['engine']} no longer hashes to the pinned "
            f"value. Every comparison made against {CHECKPOINT_ID} was against "
            "a different engine than the one running now.")
    if rec["policies_sha256"] != policies_sha256():
        problems.append("POLICY DRIFT: the pinned parameters were edited.")
    if problems:
        for p in problems:
            print(f"  !! {p}")
        return 1
    if not quiet:
        print(f"  {CHECKPOINT_ID} intact — engine and policies match the pin "
              f"of {rec['pinned_at']}")
    return 0


def policy(asset_class: str) -> dict:
    """THE comparison target. Callers use this instead of an inline dict, so
    there is exactly one answer to 'better than what'."""
    rec = load()
    if verify(quiet=True) != 0:
        raise CheckpointError(
            f"{CHECKPOINT_ID} failed verification — refusing to hand out a "
            "comparison target that has drifted from what it was pinned as")
    if asset_class not in rec["policies"]:
        raise CheckpointError(f"{CHECKPOINT_ID} prices no policy for "
                              f"{asset_class!r}. {rec['prices']}")
    return dict(rec["policies"][asset_class])


def main(argv=None) -> int:
    cmd = (argv or sys.argv[1:] or ["verify"])[0]
    try:
        if cmd == "pin":
            b = pin()
            print(f"  PINNED {b['checkpoint_id']} @ {b['pinned_at']}")
            print(f"  engine   {b['engine_sha256'][:16]}…")
            print(f"  policies {b['policies_sha256'][:16]}…")
            return 0
        if cmd == "verify":
            return verify()
        print(f"unknown command {cmd!r}; use pin | verify")
        return 2
    except CheckpointError as e:
        print(f"  REFUSED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
