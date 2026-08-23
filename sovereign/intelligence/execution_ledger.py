#!/usr/bin/env python3
"""EXECUTION LEDGER — the trade lifecycle, correlated by one `trade_id`.

`daytrade/trade_events.py` is Stockfish's DECISION history: an append-only,
fsync-backed event log of what the engine decided, keyed on
`trade_id = f"{symbol}-{session_date}"`. It is not changed or duplicated here.

What was missing is the other half of the join: the ENTRY and OUTCOME record —
what AlphaZero believed before the trade existed, what the plan was, what the
fill actually was, and what it made in R. `DecisionRecord` had no `trade_id`
field at all, while `decision_logger.find_recorded_outcome()` already read one.
This module writes that key, using the SAME id the event log uses, so the two
datasets join on a dict lookup.

WHY A SEPARATE MODULE FROM decision_logger.py
`decision_logger._safe_append()` is documented as "never raise — trades must not
be blocked". That is the right policy for the ICT/forex lane it serves. It is
the exact opposite of the daytrade lane's non-negotiable (`specs/README.md`
rule 2: stale, malformed or unknown -> raise, never a silent fallback). One
function cannot honestly hold both policies, so the lanes stay separate and
share only the schema.

FOUR GUARANTEES

  1. IDEMPOTENT ENTRY. `log_executed_trade()` creates exactly one record per
     trade_id. Re-delivery returns the stored record and appends nothing. A
     re-delivery whose entry facts DISAGREE with the stored ones raises, rather
     than silently keeping either version.

  2. MODE IS IN THE PATH. sim / shadow / paper / live / replay each get their
     own directory. A field alone is a convention a reader can forget; a
     directory makes blending simulated and real performance a deliberate act
     rather than an accident of a missing filter.

  3. CORRUPTION RAISES. A malformed line raises `LedgerCorruption`. Skipping it
     (the obvious `except json.JSONDecodeError: continue`) would let the
     existence check return "not found" for a trade that IS recorded — and the
     idempotency guarantee above would then cheerfully write a duplicate. The
     safe-looking `continue` is precisely what breaks the invariant.

  4. NO SECOND DECISION ENGINE. This module records; it never decides. It has
     no import of `stockfish_exit`, computes no exit, and moves no stop.

REALIZED R, AND THE ONE ARITHMETIC TRAP
R is computed from the ACTUAL fill prices, never the planned levels:

    risk_per_share = |entry_fill.price - initial_stop|
    gross          = (exit_fill.price - entry_fill.price) * direction * qty
    net            = gross - commissions
    R              = net / (risk_per_share * qty)

`SimulatedFill.slippage_per_share` is PROVENANCE ONLY — how far the fill landed
from its intended price. It is already embodied in `price`, so subtracting it
again would double-count it. That is the single easiest error to make here and
`test_slippage_is_not_double_counted` exists to keep it made-once.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

#: Absolute, resolved from the repo root — never the process CWD. A relative
#: Path("data/decision_logs") here silently scatters ledger rows depending on
#: where the caller was launched from (proven live: runner.py invoked from
#: inside daytrade/ wrote into daytrade/data/decision_logs/ instead of
#: data/decision_logs/). Same pattern as daytrade/write_baseline_plan.py's
#: ROOT = Path(__file__).resolve().parents[1] and daytrade/alpha_operator.py's.
#: This file lives at sovereign/intelligence/execution_ledger.py, two levels
#: below the repo root, so parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_ROOT = _REPO_ROOT / "data" / "decision_logs"

#: Closed enumeration. An unknown mode raises — a typo must never silently
#: create a sixth lane whose rows nothing will ever find again.
MODES: tuple[str, ...] = ("sim", "shadow", "paper", "live", "replay")

#: Modes whose numbers describe real money. Kept as an explicit set so a caller
#: can assert isolation rather than remembering which strings are which.
REAL_MONEY_MODES: frozenset[str] = frozenset({"paper", "live"})

SCHEMA_VERSION = "1"

#: Mirrors trade_events.MAX_PAYLOAD_STR. Snapshots reference evidence by id;
#: they do not carry prompt text or article bodies. Over-long strings are
#: REFUSED, not truncated — truncation silently changes what the record claims.
MAX_PAYLOAD_STR = 512

#: Content-level duplicate detection window (close_executed_trade). The bug
#: class this exists to catch is a trade_id minted from wall-clock instead of
#: the session/tape date (52a24e3) — that can drift the id by AT MOST one
#: calendar day. 24h is generous enough to catch that exact drift (including
#: a replay run near a day boundary) without being wide enough to start
#: matching two truly independent trades that happen to share a symbol and
#: direction days apart — the entry/exit/r fingerprint below already has to
#: match exactly before the window is even consulted.
DUPLICATE_EVENT_WINDOW_HOURS = 24


class LedgerError(RuntimeError):
    """Malformed input or an impossible lifecycle move. Never repaired."""


class LedgerCorruption(LedgerError):
    """A ledger line could not be parsed. Raised, never skipped — see guarantee 3."""


class DuplicateEntryConflict(LedgerError):
    """Same trade_id re-delivered with different entry facts."""


class DuplicateEventDetected(LedgerError):
    """A closed record already exists in this mode whose pair, direction,
    entry fill, exit fill, and r_realized all match this one, with an entry
    timestamp inside the drift window a wall-clock trade_id can produce (see
    DUPLICATE_EVENT_WINDOW). `DuplicateEntryConflict` cannot catch this shape
    of duplicate because it keys on trade_id, and two different trade_ids can
    describe one real event — that is exactly how NVDA-2026-08-21 and
    NVDA-2026-08-22 both ended up in the ledger (commit 52a24e3). A duplicate
    is an event, not an id.

    Raised only in non-real-money modes (see close_executed_trade) — in a
    real-money mode the trade already happened, so refusing to record it
    would be silent data loss, worse than the duplicate itself."""


# ─── helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aware(ts: str, field_: str) -> datetime:
    """ISO-8601 with an explicit zone. A naive timestamp is refused rather than
    assumed UTC — an assumed zone is how a record quietly claims the wrong hour."""
    if not isinstance(ts, str) or not ts.strip():
        raise LedgerError(f"{field_} must be a non-empty ISO-8601 string, got {ts!r}")
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError as e:
        raise LedgerError(f"{field_}={ts!r} is not ISO-8601: {e}") from e
    if dt.tzinfo is None:
        raise LedgerError(f"{field_}={ts!r} is timezone-naive — refusing to assume UTC")
    return dt


def _check_mode(mode: str) -> str:
    if mode not in MODES:
        raise LedgerError(f"unknown mode {mode!r}; known: {list(MODES)}")
    return mode


def _check_trade_id(trade_id: str) -> str:
    if not isinstance(trade_id, str) or not trade_id.strip():
        raise LedgerError("trade_id is required and must be a non-empty string — "
                          "an unidentifiable record cannot be correlated or closed")
    if trade_id != trade_id.strip():
        raise LedgerError(f"trade_id {trade_id!r} has leading/trailing whitespace; "
                          "it is a join key and must be byte-stable")
    return trade_id


def _check_payload(obj: Any, path: str = "payload") -> None:
    """Recursive size guard, same contract as trade_events._check_payload."""
    if isinstance(obj, str):
        if len(obj) > MAX_PAYLOAD_STR:
            raise LedgerError(f"{path} is {len(obj)} chars (max {MAX_PAYLOAD_STR}) — "
                              "reference evidence by id; refusing to truncate, because "
                              "a truncated payload silently changes what the record claims")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _check_payload(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_payload(v, f"{path}[{i}]")


def _norm_direction(direction: Any) -> int:
    """+1 long / -1 short. Accepts the DecisionRecord string form and the
    TradeState integer form; anything else raises."""
    if direction in (1, +1, "LONG", "long", "BUY", "buy"):
        return +1
    if direction in (-1, "SHORT", "short", "SELL", "sell"):
        return -1
    raise LedgerError(f"unknown direction {direction!r}; expected LONG/SHORT or +1/-1")


def _finite(x: Any, field_: str) -> float:
    if x is None:
        raise LedgerError(f"{field_} is required — refusing to substitute a default")
    try:
        v = float(x)
    except (TypeError, ValueError) as e:
        raise LedgerError(f"{field_}={x!r} is not a number") from e
    if not math.isfinite(v):
        raise LedgerError(f"{field_}={x!r} is not finite")
    return v


def mint_trade_id(symbol: str, session_date: Any) -> str:
    """The join key. Deterministic and IDENTICAL to the runner's own
    `f"{symbol}-{session_date}"` (daytrade/runner.py) — a different scheme here
    would defeat the entire purpose of this module.

    No randomness, no clock read: the same inputs must produce the same id in a
    replay months later, or the dataset cannot be rebuilt.
    """
    if not symbol or not str(symbol).strip():
        raise LedgerError("symbol is required to mint a trade_id")
    return _check_trade_id(f"{symbol}-{session_date}")


def ledger_dir(mode: str) -> Path:
    """One directory per mode — guarantee 2."""
    return LEDGER_ROOT / _check_mode(mode)


def ledger_path(mode: str, entry_timestamp: str) -> Path:
    """Month file keyed on the RECORD's own entry timestamp.

    `decision_logger._log_path()` uses `datetime.now()` at write time instead,
    so a record backfilled or replayed across a month boundary lands in the
    wrong file and the outcome updater then cannot find it.
    """
    month = _aware(entry_timestamp, "entry_timestamp").strftime("%Y_%m")
    d = ledger_dir(mode)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"decisions_{month}.jsonl"


# ─── fill metadata ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SimulatedFill:
    """What the simulator says actually happened at the venue.

    `price` is the EFFECTIVE fill — slippage is already inside it.
    `slippage_per_share` is provenance: how far `price` landed from
    `intended_price`. It is reported, never re-subtracted (see module docstring).
    """
    price: float
    qty: float
    filled_at: str                     # ISO tz-aware
    basis: str                         # e.g. 'sim:cycle-close', 'sim:next-bar-open'
    intended_price: Optional[float] = None
    slippage_per_share: float = 0.0
    commission: float = 0.0            # absolute dollars for THIS fill
    venue: str = "simulated"
    broker_order_id: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "price", _finite(self.price, "fill.price"))
        object.__setattr__(self, "qty", _finite(self.qty, "fill.qty"))
        object.__setattr__(self, "commission", _finite(self.commission, "fill.commission"))
        object.__setattr__(self, "slippage_per_share",
                           _finite(self.slippage_per_share, "fill.slippage_per_share"))
        if self.price <= 0:
            raise LedgerError(f"fill.price {self.price} must be positive")
        if self.qty <= 0:
            raise LedgerError(f"fill.qty {self.qty} must be positive — a zero-quantity "
                              "fill is not a fill")
        if self.commission < 0:
            raise LedgerError(f"fill.commission {self.commission} cannot be negative")
        if not self.basis or not self.basis.strip():
            raise LedgerError("fill.basis is required — a fill price with no stated "
                              "convention cannot be compared with any other fill")
        _aware(self.filled_at, "fill.filled_at")

    def to_dict(self) -> dict:
        return asdict(self)


# ─── R arithmetic (pure, separately testable) ────────────────────────────────

def realized_r(*, entry_price: float, initial_stop: float, exit_price: float,
               qty: float, direction: Any, commissions: float = 0.0) -> float:
    """R multiple for a SINGLE-LEG exit. Pure function, no I/O.

    Initial risk is measured against the ORIGINAL plan stop — the risk the trade
    was taken with. Using the stop as it stood at exit (after the breakeven and
    trail layers moved it) would make every winner look infinitely good and is
    the classic way an R column becomes fiction.

    For a position that was scaled out of, use `realized_r_multileg` — this
    function has no way to know about a partial that was banked earlier.
    """
    return realized_r_multileg(
        entry_price=entry_price, initial_stop=initial_stop, entry_qty=qty,
        direction=direction, legs=[{"price": exit_price, "qty": qty}],
        commissions=commissions)


def realized_r_multileg(*, entry_price: float, initial_stop: float,
                        entry_qty: float, direction: Any,
                        legs: list[dict], commissions: float = 0.0) -> float:
    """R over the WHOLE position, summed across every exit leg.

    WHY THIS EXISTS. Stockfish's ladder banks a partial at TP2 and lets the rest
    run, so most closed trades have two legs. Scoring only the final leg — and
    dividing by the REMAINING quantity's risk — reports a trade that took +0.45R
    off the table and then stopped the runner as a full -2R loss. That number is
    not conservative, it is wrong, and it is wrong in a way that would make every
    downstream aggregate lie in the same direction.

    1R is the position's ORIGINAL risk: `|entry - initial_stop| * entry_qty`.
    Every leg is scored against the same entry fill and summed.

    Leg quantities MUST reconcile to `entry_qty`. A short-fall means a leg was
    never recorded, and silently scoring the legs we happen to have would
    understate or overstate the result with no warning — so it raises.
    """
    d = _norm_direction(direction)
    entry = _finite(entry_price, "entry_price")
    stop = _finite(initial_stop, "initial_stop")
    eq = _finite(entry_qty, "entry_qty")
    costs = _finite(commissions, "commissions")

    if eq <= 0:
        raise LedgerError(f"entry_qty {eq} must be positive")
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        raise LedgerError(
            f"initial risk is zero (entry {entry:g} == stop {stop:g}) — R is undefined. "
            "Refusing to emit inf/NaN, which would poison every aggregate downstream.")
    if not legs:
        raise LedgerError("no exit legs — a closed trade left the position somehow")

    gross = 0.0
    total_qty = 0.0
    for i, leg in enumerate(legs):
        lp = _finite(leg.get("price"), f"legs[{i}].price")
        lq = _finite(leg.get("qty"), f"legs[{i}].qty")
        if lq <= 0:
            raise LedgerError(f"legs[{i}].qty {lq} must be positive")
        gross += (lp - entry) * d * lq
        total_qty += lq

    if not math.isclose(total_qty, eq, rel_tol=1e-9, abs_tol=1e-6):
        raise LedgerError(
            f"exit legs total {total_qty:g} but the entry filled {eq:g}. Refusing to "
            "score a partially-reconciled position: a missing leg silently shifts R, "
            "and the direction of the error depends on which leg went missing.")

    return (gross - costs) / (risk_per_share * eq)


# ─── read side ───────────────────────────────────────────────────────────────

def _read_records(path: Path) -> list[dict]:
    """Parse every line. A malformed line RAISES — guarantee 3.

    The tempting `except json.JSONDecodeError: continue` is what makes the
    idempotency check unsound: a corrupt line for trade X makes the lookup
    report "absent", and the caller then writes a second record for X.
    """
    if not path.exists():
        return []
    out: list[dict] = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise LedgerCorruption(
                f"{path}:{lineno} is not valid JSON ({e}). Refusing to skip it: a "
                "skipped line makes an existing trade look absent, and the next "
                "idempotent write would then create a duplicate. Repair the file."
            ) from e
        if not isinstance(obj, dict):
            raise LedgerCorruption(f"{path}:{lineno} is {type(obj).__name__}, not an object")
        out.append(obj)
    return out


def find_executed_trade(trade_id: str, mode: str) -> Optional[dict]:
    """The stored record for this trade_id in this mode, or None. Read-only.

    Scans this mode's directory only. A sim lookup can never return a live row.
    """
    tid = _check_trade_id(trade_id)
    d = ledger_dir(mode)
    if not d.exists():
        return None
    for path in sorted(d.glob("decisions_*.jsonl")):
        for obj in _read_records(path):
            if str(obj.get("trade_id", "")).strip() == tid:
                return obj
    return None


# ─── write side ──────────────────────────────────────────────────────────────

def _append_durable(path: Path, obj: dict) -> None:
    """Append one JSON line and fsync it. Same durability contract as
    trade_events.JsonlEventLog: the bytes are on disk before the caller
    proceeds, and the parent directory is fsynced when the file is created so
    the directory entry itself survives a crash."""
    created = not path.exists()
    line = json.dumps(obj, default=str, sort_keys=True) + "\n"
    with open(path, "a") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    if created:
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def _rewrite_durable(path: Path, records: list[dict]) -> None:
    """Rewrite the whole month file atomically (temp + fsync + rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "".join(json.dumps(r, default=str, sort_keys=True) + "\n" for r in records)
    with open(tmp, "w") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


#: Entry facts that identify a trade. A re-delivery must agree on all of them or
#: it is not the same trade and the conflict is surfaced instead of resolved.
_IDENTITY_FIELDS = ("pair", "direction", "entry_level", "stop_loss", "mode")


def log_executed_trade(
    *,
    trade_id: str,
    mode: str,
    system: str,
    pair: str,
    direction: Any,
    entry_fill: SimulatedFill,
    initial_stop: float,
    strategy_version: str,
    entry_timestamp: Optional[str] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    exit_policy: Optional[str] = None,
    stockfish_plan: Optional[dict] = None,
    alphazero_snapshot: Optional[dict] = None,
    risk_pct: Optional[float] = None,
    why_this_trade: str = "",
    why_this_size: str = "",
    evidence_ids: Optional[list[str]] = None,
) -> dict:
    """Record ONE executed entry. Idempotent on `trade_id` within `mode`.

    CALL THIS ONLY AFTER THE ENTRY FILL IS CONFIRMED. A signal, a candidate, or
    a submitted-but-unconfirmed order is not an executed trade; recording one as
    such is what made the forex scan path pollute the decision log with
    setups that were never taken.

    Returns the stored record dict. On re-delivery of a trade_id that is already
    present, returns the EXISTING record and writes nothing.
    """
    tid = _check_trade_id(trade_id)
    _check_mode(mode)
    if not isinstance(entry_fill, SimulatedFill):
        raise LedgerError("entry_fill must be a SimulatedFill — an unconfirmed or "
                          "untyped fill is not evidence that a trade exists")
    d = _norm_direction(direction)
    stop = _finite(initial_stop, "initial_stop")
    if not strategy_version or not str(strategy_version).strip():
        raise LedgerError("strategy_version is required — an unversioned record "
                          "cannot be attributed to the rules that produced it")

    snapshot = alphazero_snapshot if alphazero_snapshot is not None else {}
    plan = stockfish_plan if stockfish_plan is not None else {}
    _check_payload(snapshot, "alphazero_snapshot")
    _check_payload(plan, "stockfish_plan")

    ts = entry_timestamp or entry_fill.filled_at
    _aware(ts, "entry_timestamp")

    existing = find_executed_trade(tid, mode)
    if existing is not None:
        # Idempotency, with a conflict check. Returning a stored record that
        # disagrees with what the caller just presented would silently keep one
        # of two contradictory versions of the same trade.
        incoming = {"pair": pair, "direction": "LONG" if d > 0 else "SHORT",
                    "entry_level": entry_fill.price, "stop_loss": stop, "mode": mode}
        conflicts = {
            k: (existing.get(k), incoming[k]) for k in _IDENTITY_FIELDS
            if not _same(existing.get(k), incoming[k])
        }
        if conflicts:
            raise DuplicateEntryConflict(
                f"trade_id {tid!r} already recorded in mode {mode!r} with different "
                f"entry facts: {conflicts}. Refusing to pick a winner — either this "
                "is a different trade and needs its own id, or the ledger is wrong.")
        return existing

    risk_per_share = abs(entry_fill.price - stop)
    if risk_per_share <= 0:
        raise LedgerError(
            f"initial risk is zero (fill {entry_fill.price:g} == stop {stop:g}) — this "
            "trade could never be scored in R. Refusing to record an unscoreable trade.")

    record = {
        "schema_version": SCHEMA_VERSION,
        # --- correlation key: identical to daytrade/trade_events' trade_id ---
        "trade_id": tid,
        "mode": mode,
        "strategy_version": strategy_version,
        # --- identity ---
        "entry_timestamp": ts,
        "system": system,
        "pair": pair,
        "direction": "LONG" if d > 0 else "SHORT",
        # --- levels as actually filled / planned ---
        "entry_level": entry_fill.price,
        "planned_entry": plan.get("entry"),
        "stop_loss": stop,
        "tp1": tp1,
        "tp2": tp2,
        "exit_policy": exit_policy,
        # --- risk, from the ACTUAL fill ---
        "risk_per_share": risk_per_share,
        "risk_dollars": risk_per_share * entry_fill.qty,
        "risk_pct": risk_pct,
        # --- provenance ---
        "stockfish_plan": plan,
        "alphazero_snapshot": snapshot,
        "evidence_ids": list(evidence_ids or []),
        "why_this_trade": why_this_trade,
        "why_this_size": why_this_size,
        # --- fills ---
        "entry_fill": entry_fill.to_dict(),
        # Every scale-out leg, in order. Stockfish banks a partial at TP2, so a
        # closed trade normally has one or more of these plus the final exit_fill.
        "partial_exits": [],
        "exit_fill": None,
        "costs": {"entry_commission": entry_fill.commission,
                  "partial_commission": 0.0, "exit_commission": None,
                  "total_commission": None},
        # --- outcome, filled by close_executed_trade ---
        "outcome": "OPEN",
        "r_realized": None,
        "exit_timestamp": None,
        "exit_reason": None,
        "recorded_at": _now_iso(),
    }
    _append_durable(ledger_path(mode, ts), record)
    return record


def _same(a: Any, b: Any) -> bool:
    """Equality that tolerates float representation but nothing else."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def _locate(trade_id: str, mode: str):
    """(path, records, index) for this trade_id, or None. Read side only."""
    d = ledger_dir(mode)
    if not d.exists():
        return None
    for path in sorted(d.glob("decisions_*.jsonl")):
        records = _read_records(path)
        for i, obj in enumerate(records):
            if str(obj.get("trade_id", "")).strip() == trade_id:
                return path, records, i
    return None


def record_partial_exit(*, trade_id: str, mode: str, fill: SimulatedFill,
                        reason: str, leg_key: str) -> dict:
    """Record ONE scale-out leg (Stockfish's TAKE_PARTIAL).

    Idempotent on `leg_key`, which the caller derives from the decision that
    produced the reduction — the same discipline as the constitution's C005
    idempotency key. Re-delivery of a leg after a crash-retry must not book the
    partial twice, because a double-counted leg silently improves or worsens R.
    """
    tid = _check_trade_id(trade_id)
    _check_mode(mode)
    if not isinstance(fill, SimulatedFill):
        raise LedgerError("fill must be a SimulatedFill")
    if not leg_key or not str(leg_key).strip():
        raise LedgerError("leg_key is required — without it a retried partial "
                          "cannot be told apart from a genuine second partial")

    found = _locate(tid, mode)
    if found is None:
        raise LedgerError(f"no record with trade_id {tid!r} in mode {mode!r} — a "
                          "reduction cannot precede the entry that created the position")
    path, records, i = found
    obj = records[i]
    if obj.get("outcome") not in (None, "OPEN"):
        raise LedgerError(f"trade_id {tid!r} is already closed — refusing to add a leg")

    legs = obj.setdefault("partial_exits", [])
    for existing in legs:
        if existing.get("leg_key") == leg_key:
            return obj                                  # the forgivable retry
    legs.append({"leg_key": leg_key, "reason": reason, **fill.to_dict()})
    obj["costs"]["partial_commission"] = sum(l.get("commission", 0.0) for l in legs)
    records[i] = obj
    _rewrite_durable(path, records)
    return obj


def _content_duplicates(*, mode: str, tid: str, pair: Any, direction_label: str,
                        entry_price: float, exit_price: float, r: float,
                        entry_ts: str) -> list[dict]:
    """Every OTHER closed row in `mode` describing the same event as the one
    about to be written: same pair, same direction, same entry fill price,
    same exit fill price, same r_realized (within `_same`'s float tolerance —
    the same tolerance this module already uses for redelivery and reclose
    comparisons), entry timestamp within DUPLICATE_EVENT_WINDOW_HOURS.

    A row already marked `superseded` is excluded from the scan — it is
    already known and accounted for; re-flagging it would just be noise on
    every future close.
    """
    d = ledger_dir(mode)
    if not d.exists():
        return []
    this_ts = _aware(entry_ts, "entry_timestamp")
    window = timedelta(hours=DUPLICATE_EVENT_WINDOW_HOURS)
    hits: list[dict] = []
    for path in sorted(d.glob("decisions_*.jsonl")):
        for obj in _read_records(path):
            if str(obj.get("trade_id", "")).strip() == tid:
                continue
            if obj.get("outcome") in (None, "OPEN"):
                continue
            if obj.get("superseded"):
                continue
            if obj.get("pair") != pair or obj.get("direction") != direction_label:
                continue
            other_entry = (obj.get("entry_fill") or {}).get("price")
            other_exit = (obj.get("exit_fill") or {}).get("price")
            other_r = obj.get("r_realized")
            if other_r is None:
                continue
            if not (_same(other_entry, entry_price) and _same(other_exit, exit_price)
                    and _same(other_r, r)):
                continue
            other_ts_raw = obj.get("entry_timestamp")
            try:
                other_ts = _aware(other_ts_raw, "entry_timestamp")
            except LedgerError:
                continue                      # unparsable timestamp can't confirm the window
            if abs(other_ts - this_ts) <= window:
                hits.append(obj)
    return hits


def close_executed_trade(
    *,
    trade_id: str,
    mode: str,
    exit_fill: SimulatedFill,
    exit_reason: str,
    exit_timestamp: Optional[str] = None,
) -> dict:
    """Close the record with THIS exact trade_id. Updates in place; never appends.

    Realized R uses the stored ENTRY FILL and the ORIGINAL stop — not the plan's
    intended entry, and not the stop as it stood at exit after the ladder moved it.
    """
    tid = _check_trade_id(trade_id)
    _check_mode(mode)
    if not isinstance(exit_fill, SimulatedFill):
        raise LedgerError("exit_fill must be a SimulatedFill")
    if not exit_reason or not exit_reason.strip():
        raise LedgerError("exit_reason is required — an unexplained close cannot be "
                          "graded against the decision that caused it")
    _check_payload(exit_reason, "exit_reason")

    ts = exit_timestamp or exit_fill.filled_at
    _aware(ts, "exit_timestamp")

    found = _locate(tid, mode)
    if found is not None:
        path, records, i = found
        obj = records[i]

        entry_fill = obj.get("entry_fill") or {}
        entry_commission = float(entry_fill.get("commission", 0.0))
        legs_recorded = list(obj.get("partial_exits") or [])
        # Every leg the position left through: each banked partial, then this
        # final exit. Scored against ONE entry fill and ONE original risk.
        legs = [{"price": l["price"], "qty": l["qty"]} for l in legs_recorded]
        legs.append({"price": exit_fill.price, "qty": exit_fill.qty})
        commissions = (entry_commission
                       + sum(float(l.get("commission", 0.0)) for l in legs_recorded)
                       + exit_fill.commission)

        r = realized_r_multileg(
            entry_price=entry_fill.get("price"),
            initial_stop=obj.get("stop_loss"),
            entry_qty=entry_fill.get("qty"),
            direction=obj.get("direction"),
            legs=legs,
            commissions=commissions,
        )

        if obj.get("outcome") not in (None, "OPEN"):
            # Already closed. Identical re-delivery is the forgivable retry
            # (same rule trade_events applies to duplicate events); a
            # DIFFERENT close is a real conflict and is surfaced.
            if (_same(obj.get("r_realized"), r)
                    and obj.get("exit_reason") == exit_reason
                    and _same((obj.get("exit_fill") or {}).get("price"), exit_fill.price)):
                return obj
            raise LedgerError(
                f"trade_id {tid!r} is already closed as {obj.get('outcome')} at "
                f"R={obj.get('r_realized')}; refusing to re-close it at R={r:.4f} "
                f"({exit_reason!r}). An outcome is observed once, not re-litigated.")

        # Content-level duplicate guard (hardens the gap DuplicateEntryConflict
        # cannot close — see DuplicateEventDetected). Runs here, inside the
        # ONE function that ever sets r_realized/outcome, so no caller can
        # reach a closed row without passing through it.
        dupes = _content_duplicates(
            mode=mode, tid=tid, pair=obj.get("pair"), direction_label=obj.get("direction"),
            entry_price=entry_fill.get("price"), exit_price=exit_fill.price, r=r,
            entry_ts=obj.get("entry_timestamp"))
        if dupes:
            other_ids = sorted(str(o.get("trade_id")) for o in dupes)
            reason = (f"content-level duplicate of {other_ids} — same pair/direction/"
                     f"entry/exit/r_realized within {DUPLICATE_EVENT_WINDOW_HOURS}h "
                     "(a duplicate is an event, not an id — 52a24e3)")
            if mode in REAL_MONEY_MODES:
                # The trade already happened in the real market. Refusing to
                # record it would be exactly the non-negotiable #3 violation
                # this ledger exists to prevent — an unlogged trade is silent
                # data loss, strictly worse than a flagged one. Write it,
                # flagged, and let a human resolve it via mark_superseded().
                obj["possible_duplicate_of"] = other_ids
                obj["possible_duplicate_reason"] = reason
            else:
                # sim/shadow/replay: nothing real happened and nothing is lost
                # by refusing. This IS the exact shape of the bug that
                # produced NVDA-2026-08-21/-22 — fail loud before it is
                # written, per CLAUDE.md rule 5 and specs/README.md rule 2.
                raise DuplicateEventDetected(
                    f"trade_id {tid!r} in mode {mode!r} is a {reason}. Refusing to "
                    "write it. If this is genuinely a second, distinct trade that "
                    "happens to share every fact with an existing row, investigate "
                    "before re-closing it.")

        obj["outcome"] = "WIN" if r > 0 else ("LOSS" if r < 0 else "SCRATCH")
        obj["r_realized"] = r
        obj["exit_timestamp"] = ts
        obj["exit_reason"] = exit_reason
        obj["exit_fill"] = exit_fill.to_dict()
        obj["costs"] = {
            "entry_commission": entry_commission,
            "partial_commission": sum(float(l.get("commission", 0.0))
                                      for l in legs_recorded),
            "exit_commission": exit_fill.commission,
            "total_commission": commissions,
        }
        obj["closed_at"] = _now_iso()
        records[i] = obj
        _rewrite_durable(path, records)
        return obj

    raise LedgerError(
        f"no open record with trade_id {tid!r} in mode {mode!r}. Refusing to create one "
        "on close: a close with no matching entry means the entry boundary never fired, "
        "and inventing the entry here would fabricate the very correlation this ledger exists to prove.")


# ─── corrections — history is amended by addition, never by rewrite ──────────
#
# Both functions below exist because of one incident: NVDA-2026-08-21 and
# NVDA-2026-08-22 are the same replayed event, recorded twice under different
# trade_ids (a wall-clock trade_id, fixed in 52a24e3). The mis-dated row is
# real history — it is evidence the bug happened — so it is never deleted or
# rewritten. `mark_superseded` adds metadata to it (never touches a
# substantive field: fills, r_realized, outcome, timestamps are untouched).
# `log_correction` appends a human-readable marker row, the same idiom already
# used in data/decision_logs/decisions_2026_08.jsonl for spec_021_log_correction,
# adapted to this module's own schema rather than borrowed wholesale — that
# file's DecisionRecord schema and this ledger's schema are deliberately two
# different shapes (see the module docstring's WHY A SEPARATE MODULE section).

def mark_superseded(*, trade_id: str, mode: str, superseded_by: str, reason: str) -> dict:
    """Flag an existing CLOSED row as describing the same event as another,
    correct trade_id. Adds `superseded` / `superseded_by` / `superseded_reason`
    / `superseded_at` fields only — every substantive field is left
    byte-identical, so any diff against the pre-correction row shows nothing
    but the flag.

    Idempotent: re-marking with the SAME superseded_by/reason is a no-op, the
    same forgivable-retry shape as record_partial_exit. Re-marking with a
    DIFFERENT superseded_by/reason raises — a supersession is decided once,
    like an outcome, not re-litigated.
    """
    tid = _check_trade_id(trade_id)
    sup_by = _check_trade_id(superseded_by)
    _check_mode(mode)
    if not reason or not reason.strip():
        raise LedgerError("reason is required — a superseded row with no stated "
                          "cause is indistinguishable from silent data loss")
    _check_payload(reason, "reason")

    found = _locate(tid, mode)
    if found is None:
        raise LedgerError(f"no record with trade_id {tid!r} in mode {mode!r} — "
                          "cannot supersede a row that does not exist")
    path, records, i = found
    obj = records[i]
    if obj.get("outcome") in (None, "OPEN"):
        raise LedgerError(f"trade_id {tid!r} is not closed — an open row has no "
                          "outcome yet for another row to supersede")

    if obj.get("superseded"):
        if obj.get("superseded_by") == sup_by and obj.get("superseded_reason") == reason:
            return obj                                     # the forgivable retry
        raise LedgerError(
            f"trade_id {tid!r} is already marked superseded by "
            f"{obj.get('superseded_by')!r} ({obj.get('superseded_reason')!r}); "
            f"refusing to overwrite with superseded_by={sup_by!r} — a "
            "supersession is decided once, like an outcome.")

    obj["superseded"] = True
    obj["superseded_by"] = sup_by
    obj["superseded_reason"] = reason
    obj["superseded_at"] = _now_iso()
    records[i] = obj
    _rewrite_durable(path, records)
    return obj


def log_correction(*, mode: str, system: str, superseded_trade_id: str,
                   superseding_trade_id: str, reason: str,
                   as_of: Optional[str] = None) -> dict:
    """Append a zero-risk correction marker row to the same ledger file the
    rows it discusses live in — the repo's own idiom for "I know about this
    and here is why" (CLAUDE.md rule 3), matching the shape of the two
    DECISION_LOG/CORRECTION rows already in
    data/decision_logs/decisions_2026_08.jsonl (`pair: "DECISION_LOG"`,
    `direction: "CORRECTION"`, `risk_pct: 0`, `why_this_trade`), adapted to
    this module's own field vocabulary (trade_id/mode/entry_fill/outcome)
    rather than the FOREX DecisionRecord schema, since the two schemas are
    deliberately different (module docstring). `record_kind: "correction"`
    mirrors `log_scan_candidate`'s `record_kind: "candidate"` — a value every
    reader that filters non-executed rows can already recognize.

    This never mutates the rows it discusses; pair it with `mark_superseded`
    for the machine-readable half.
    """
    _check_mode(mode)
    sup = _check_trade_id(superseded_trade_id)
    by = _check_trade_id(superseding_trade_id)
    if not reason or not reason.strip():
        raise LedgerError("reason is required — a correction with no stated "
                          "cause is indistinguishable from an unexplained edit")
    _check_payload(reason, "reason")

    ts = as_of or _now_iso()
    _aware(ts, "as_of")

    record = {
        "schema_version": SCHEMA_VERSION,
        "record_kind": "correction",           # never "executed" — mirrors "candidate"
        "trade_id": None,
        "mode": mode,
        "system": system,
        "pair": "DECISION_LOG",                # idiom: spec_021_log_correction
        "direction": "CORRECTION",
        "risk_pct": 0,
        "why_this_trade": reason,
        "superseded_trade_id": sup,
        "superseding_trade_id": by,
        "entry_fill": None,
        "exit_fill": None,
        "outcome": None,
        "r_realized": None,
        "entry_timestamp": ts,
        "recorded_at": _now_iso(),
    }
    _append_durable(ledger_path(mode, ts), record)
    return record


# ─── scan candidates — explicitly NOT executed trades ────────────────────────

def log_scan_candidate(*, system: str, pair: str, direction: Any,
                       entry_level: float, stop_loss: float,
                       as_of: Optional[str] = None,
                       rationale: Optional[list[str]] = None,
                       extra: Optional[dict] = None) -> dict:
    """Record a SETUP THE SCANNER SAW. This is a forecast, not a trade.

    It lives in its own directory and carries `record_kind: "candidate"`, so it
    can never be counted as an executed trade by any reader that globs the
    executed lanes. Item 6 of the card: scan candidates are not executed trades,
    and recording them as such is how a decision log fills with trades nobody took.
    """
    ts = as_of or _now_iso()
    _aware(ts, "as_of")
    d = LEDGER_ROOT / "candidates"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"candidates_{_aware(ts, 'as_of').strftime('%Y_%m')}.jsonl"
    rec = {
        "schema_version": SCHEMA_VERSION,
        "record_kind": "candidate",          # never "executed"
        "as_of": ts,
        "system": system,
        "pair": pair,
        "direction": "LONG" if _norm_direction(direction) > 0 else "SHORT",
        "entry_level": _finite(entry_level, "entry_level"),
        "stop_loss": _finite(stop_loss, "stop_loss"),
        "rationale": list(rationale or []),
        "extra": extra or {},
    }
    _check_payload(rec, "candidate")
    _append_durable(path, rec)
    return rec


if __name__ == "__main__":
    print(__doc__)
    print(f"modes: {list(MODES)}   real-money: {sorted(REAL_MONEY_MODES)}")
    print("run the suite: pytest daytrade/test_execution_ledger.py -v")
