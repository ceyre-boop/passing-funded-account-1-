"""sovereign/forex/carry_checkpoint.py — CARRY-FROZEN-001: the carry incumbent, pinned.

The rule that produced the sealed 411 and the honest 350 — `exit_machine.decide_exit`
driven by `fast_backtester._simulate_forex_core` under `ForexBacktester`'s constants and
`_apply_costs` — was content-hashed nowhere (Phase 0, 2026-08-29). SEALS.json hashes
OUTPUT csvs; nothing hashed the opponent. A candidate cannot be measured against a
moving self, so this pins the opponent's bytes, its declared configuration, its cost
tables, its path data, its population, and the ABSENCE of the two calibration files
whose absence selects the static cost tables.

Sibling of `daytrade/frozen_policy.py`, deliberately not merged into it — spec 034 I59:
the intraday checkpoint refuses to price a carry position, and vice versa.

Rules (I49 pattern): `pin()` refuses if the record exists. `attach()` appends a dataset
hash once and refuses a second attachment under the same name. `verify()` re-hashes
everything and returns the number of failures, printing each. Nothing here downgrades
to a warning.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "data" / "carry" / "CARRY_FROZEN_001.json"
CHECKPOINT_ID = "CARRY-FROZEN-001"

PINNED_FILES: tuple[str, ...] = (
    "sovereign/forex/exit_machine.py",
    "sovereign/forex/fast_backtester.py",
    "sovereign/forex/forex_backtester.py",
    "sovereign/forex/swap_model.py",
    "sovereign/forex/signal_engine.py",
    "data/research/spot_cache/EURUSD_ohlc.parquet",
    "data/research/spot_cache/GBPUSD_ohlc.parquet",
    "data/research/spot_cache/USDJPY_ohlc.parquet",
    "data/research/spot_cache/AUDUSD_ohlc.parquet",
    "data/cb_ab/cb_off_trades.csv",
    "data/cb_ab/stats.json",
)
# Their ABSENCE is a pinned fact: present, `_apply_costs` / `swap_model` would read them.
MUST_BE_ABSENT: tuple[str, ...] = (
    "data/research/swap_calibration.json",
    "data/execution/calibrated_costs.json",
)
# ForexBacktester class attributes that define the incumbent. Missing name → raise.
CONFIG_CLASS_ATTRS: tuple[str, ...] = (
    "STOP_ATR_MULT", "TRAILING_ATR_MULT", "PAIR_TRAILING_OVERRIDES", "PAIR_HOLD_OVERRIDES",
    "HOLD_DAYS", "PAIR_VIX_GATES", "STOP_PCT", "SIGNAL_THRESHOLD",
)
CONFIG_MODULE_ATTRS: tuple[str, ...] = (
    "SPREAD_COST", "SWAP_RATES_ANNUAL", "_DEFAULT_SWAP", "_DEFAULT_SPREAD", "SLIPPAGE_PER_SIDE",
)


class CheckpointError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _config_snapshot() -> dict:
    """The incumbent's declared numbers, read from the code — never typed by hand."""
    from sovereign.forex import forex_backtester as fb
    snap: dict = {}
    for name in CONFIG_CLASS_ATTRS:
        if not hasattr(fb.ForexBacktester, name):
            raise CheckpointError(f"ForexBacktester has no attribute {name}; the checkpoint would pin a guess")
        snap[name] = getattr(fb.ForexBacktester, name)
    for name in CONFIG_MODULE_ATTRS:
        if not hasattr(fb, name):
            raise CheckpointError(f"forex_backtester has no module attribute {name}")
        snap[name] = getattr(fb, name)
    return json.loads(json.dumps(snap, sort_keys=True, default=str))


def _config_sha256(snap: dict) -> str:
    return hashlib.sha256(json.dumps(snap, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _files_map(files: tuple[str, ...]) -> dict[str, str]:
    out = {}
    for rel in files:
        p = ROOT / rel
        if not p.is_file():
            raise CheckpointError(f"cannot pin {rel}: not a file")
        out[rel] = sha256_file(p)
    return out


def pin(*, why: str) -> dict:
    if not why or not why.strip():
        raise CheckpointError("pin() needs a non-empty `why`")
    if RECORD.exists():
        raise CheckpointError(f"{RECORD.relative_to(ROOT)} exists — re-pinning is refused (I49). "
                              "A new opponent needs a new checkpoint id.")
    for rel in MUST_BE_ABSENT:
        if (ROOT / rel).exists():
            raise CheckpointError(f"{rel} exists; the incumbent's cost model is being pinned as the static table")
    snap = _config_snapshot()
    rec = {
        "id": CHECKPOINT_ID,
        "pinned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "why": why,
        "files": _files_map(PINNED_FILES),
        "must_be_absent": list(MUST_BE_ABSENT),
        "config": snap,
        "config_sha256": _config_sha256(snap),
        "datasets": {},
    }
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    with RECORD.open("w") as fh:
        json.dump(rec, fh, indent=2)
        fh.write("\n")
    return rec


def load() -> dict:
    if not RECORD.exists():
        raise CheckpointError(f"{RECORD.relative_to(ROOT)} missing — nothing is pinned")
    with RECORD.open() as fh:
        return json.load(fh)


def attach(name: str, path: str, *, why: str) -> dict:
    """Append one dataset hash (e.g. the frozen path parquet). Refuses a second attach under the same name."""
    if not why or not why.strip():
        raise CheckpointError("attach() needs a non-empty `why`")
    rec = load()
    if name in rec["datasets"]:
        raise CheckpointError(f"dataset {name!r} already attached — re-attaching is refused (I49)")
    p = ROOT / path
    if not p.is_file():
        raise CheckpointError(f"cannot attach {path}: not a file")
    rec["datasets"][name] = {"path": path, "sha256": sha256_file(p), "why": why,
                             "attached_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with RECORD.open("w") as fh:
        json.dump(rec, fh, indent=2)
        fh.write("\n")
    return rec


def verify(*, quiet: bool = False) -> int:
    """Return the number of failures (0 == intact). Prints each failure unless quiet."""
    try:
        rec = load()
    except CheckpointError as e:
        if not quiet:
            print(f"FAIL {e}")
        return 1
    failures: list[str] = []
    for rel, sha in rec["files"].items():
        p = ROOT / rel
        if not p.is_file():
            failures.append(f"MISSING {rel}")
        elif sha256_file(p) != sha:
            failures.append(f"MOVED {rel}")
    for rel in rec["must_be_absent"]:
        if (ROOT / rel).exists():
            failures.append(f"PRESENT {rel} (pinned as absent — the cost model would change)")
    try:
        if _config_sha256(_config_snapshot()) != rec["config_sha256"]:
            failures.append("CONFIG the incumbent's declared constants changed")
    except CheckpointError as e:
        failures.append(f"CONFIG {e}")
    for name, d in rec["datasets"].items():
        p = ROOT / d["path"]
        if not p.is_file():
            failures.append(f"MISSING dataset {name} ({d['path']})")
        elif sha256_file(p) != d["sha256"]:
            failures.append(f"MOVED dataset {name} ({d['path']})")
    if not quiet:
        for f in failures:
            print(f"FAIL {f}")
        if not failures:
            print(f"{CHECKPOINT_ID} intact: {len(rec['files'])} files, {len(rec['datasets'])} datasets, config {rec['config_sha256'][:12]}…")
    return len(failures)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in {"pin", "verify", "attach"}:
        print("usage: carry_checkpoint pin --why '<reason>' | verify | attach <name> <path> --why '<reason>'")
        return 2
    try:
        if argv[0] == "verify":
            return 1 if verify() else 0
        if "--why" not in argv:
            raise CheckpointError("--why is required")
        why = argv[argv.index("--why") + 1]
        if argv[0] == "pin":
            rec = pin(why=why)
            print(f"pinned {CHECKPOINT_ID}: {len(rec['files'])} files, config {rec['config_sha256'][:12]}…")
            return 0
        rec = attach(argv[1], argv[2], why=why)
        print(f"attached {argv[1]} -> {rec['datasets'][argv[1]]['sha256'][:12]}…")
        return 0
    except CheckpointError as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
