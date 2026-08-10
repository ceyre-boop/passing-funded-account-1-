#!/usr/bin/env python3
"""EVENT-SOURCED TRADE MEMORY — spec 012. The trade's history as facts.

A position's state today is a fold over everything that happened to it. This
module makes that literal: an append-only log of immutable events, a pure
reducer that rebuilds `TradeMemory` from them, and an adapter back to
`TradeState` — which remains the ONLY decision input. There is no second exit
engine here; the reducer records what the engine decided, it never decides.

WHY: crash recovery, auditability, deterministic replay. And it is where C005
finally becomes effective rather than mechanical: `PARTIAL_FILL_CONFIRMED`
events carry the reduction's idempotency key, so `rebuild()` reconstructs the
applied-key set TOGETHER with the state — a restarted process cannot lose one
without the other (the exact residual flagged at Gate 0).

FAIL LOUD: unknown event types, schema versions, sequence gaps, duplicate
sequences with differing payloads, events after close, stops that loosen, and
high-water marks that regress all raise. A duplicate with an IDENTICAL
fingerprint is the one forgivable case — the classic retry — and it is a no-op.

REDACTION: payloads reference evidence by id. No prompt text, no article
bodies: any payload string over 512 characters is refused at construction.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

SCHEMA_VERSION = "1"
MAX_PAYLOAD_STR = 512

EVENT_TYPES: frozenset[str] = frozenset({
    "POSITION_OPENED", "FACTS_OBSERVED", "HWM_UPDATED", "TP1_TRIGGERED",
    "STOP_ADVANCED", "DIRECTIVE_ACCEPTED", "PARTIAL_ORDER_SUBMITTED",
    "PARTIAL_FILL_CONFIRMED", "TIME_FLATTEN_TRIGGERED", "POSITION_CLOSED",
    "RECONCILIATION_FAILED",
})

# Which event types advance state_revision, mirroring MUTATING in the
# constitution: revision is a state identity, not a call counter.
_REVISION_ADVANCING = frozenset({"STOP_ADVANCED", "PARTIAL_FILL_CONFIRMED",
                                 "POSITION_CLOSED"})

_PLAN_REQUIRED = ("direction", "entry", "qty", "sl", "tp1", "tp2", "trail_dist")


class EventError(RuntimeError):
    """Malformed event or impossible history. Never repaired silently."""


class TornTailError(EventError):
    """The log's FINAL line is torn (crash mid-write). Recoverable — but only
    by the operator's explicit choice, never by default."""


def _check_payload(obj, path="payload"):
    """JSON-primitives only, strings bounded (redaction rule)."""
    if isinstance(obj, str):
        if len(obj) > MAX_PAYLOAD_STR:
            raise EventError(f"{path}: string of {len(obj)} chars — payloads "
                             "reference evidence by id, they do not carry text "
                             f"(max {MAX_PAYLOAD_STR})")
        return
    if obj is None or isinstance(obj, (int, float, bool)):
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise EventError(f"{path}: non-string key {k!r}")
            _check_payload(v, f"{path}.{k}")
        return
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_payload(v, f"{path}[{i}]")
        return
    raise EventError(f"{path}: {type(obj).__name__} is not JSON-serializable")


@dataclass(frozen=True)
class TradeEvent:
    event_id: str
    trade_id: str
    sequence: int
    event_type: str
    occurred_at: str            # ISO-8601, timezone-aware (naive = MALFORMED)
    source_version: str
    payload: dict = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        for f_ in ("event_id", "trade_id", "source_version"):
            if not getattr(self, f_):
                raise EventError(f"{f_} is required — an unidentifiable event "
                                 "cannot be audited or deduplicated")
        if self.event_type not in EVENT_TYPES:
            raise EventError(f"unknown event_type {self.event_type!r}; "
                             f"known: {sorted(EVENT_TYPES)}")
        if not isinstance(self.sequence, int) or self.sequence < 0:
            raise EventError(f"sequence must be a non-negative int, got "
                             f"{self.sequence!r}")
        try:
            dt = datetime.fromisoformat(self.occurred_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as e:
            raise EventError(f"occurred_at {self.occurred_at!r} is not ISO-8601") from e
        if dt.tzinfo is None:
            raise EventError(f"occurred_at {self.occurred_at!r} is timezone-naive "
                             "— refusing to assume a zone")
        _check_payload(self.payload)

    @property
    def fingerprint(self) -> str:
        """Content identity of the payload — what makes a retry recognisable."""
        canon = json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TradeEvent":
        known = set(TradeEvent.__dataclass_fields__)
        extra = set(d) - known
        if extra:
            raise EventError(f"unknown field(s) {sorted(extra)} — the envelope is "
                             "strict; an unrecognised field is corruption, not an "
                             "extension point")
        return TradeEvent(**d)


# -------------------------------------------------------------- append (pure)

def append(events: List[TradeEvent], event: TradeEvent) -> List[TradeEvent]:
    """Pure: returns a NEW list; the input is never mutated.

    sequence == len(events)  -> appended
    sequence <  len(events)  -> retry: identical fingerprint+type is a no-op,
                                anything else is corruption
    sequence >  len(events)  -> a gap, always corruption
    """
    if events and event.trade_id != events[0].trade_id:
        raise EventError(f"trade_id mismatch: log is {events[0].trade_id!r}, "
                         f"event is {event.trade_id!r}")
    n = len(events)
    if event.sequence < n:
        prior = events[event.sequence]
        if (prior.event_type == event.event_type
                and prior.fingerprint == event.fingerprint):
            return list(events)                       # idempotent retry
        raise EventError(
            f"duplicate sequence {event.sequence} with a DIFFERENT payload — "
            "two histories are claiming the same slot; this log is corrupt")
    if event.sequence > n:
        raise EventError(f"sequence gap: expected {n}, got {event.sequence} — "
                         "a missing event means missing state; refusing to skip it")
    return list(events) + [event]


# ------------------------------------------------------------------- reducer

@dataclass
class TradeMemory:
    """The fold. Everything a restart needs, INCLUDING the C005 key set."""
    trade_id: str
    plan: dict
    direction: int
    qty: float
    sl: float
    hwm: float
    tp1_done: bool = False
    tp2_done: bool = False
    closed: bool = False
    reconciliation_failed: bool = False
    state_revision: int = 0
    last_now_et: Optional[str] = None
    applied_reduction_keys: frozenset = frozenset()
    pending_reduction_keys: frozenset = frozenset()
    seq_next: int = 0
    close_reason: Optional[str] = None


def _require(payload: dict, key: str, etype: str):
    if key not in payload:
        raise EventError(f"{etype}: payload missing required {key!r}")
    return payload[key]


def rebuild(events: List[TradeEvent]) -> TradeMemory:
    """Pure fold. Same events, same memory, no side effects. Every impossible
    history raises with the sequence number that broke it."""
    if not events:
        raise EventError("empty event list — a trade with no history is not a trade")

    mem: Optional[TradeMemory] = None
    for i, e in enumerate(events):
        if e.schema_version != SCHEMA_VERSION:
            raise EventError(f"seq {e.sequence}: unknown schema_version "
                             f"{e.schema_version!r} — refusing a best-effort parse")
        if e.sequence != i:
            raise EventError(f"sequence break at index {i}: expected {i}, "
                             f"got {e.sequence}")
        if events[0].trade_id != e.trade_id:
            raise EventError(f"seq {e.sequence}: trade_id mismatch")

        if mem is None:
            if e.event_type != "POSITION_OPENED":
                raise EventError(f"seq {e.sequence}: first event must be "
                                 f"POSITION_OPENED, got {e.event_type}")
            plan = _require(e.payload, "plan", e.event_type)
            missing = [k for k in _PLAN_REQUIRED if plan.get(k) is None]
            if missing:
                raise EventError(f"POSITION_OPENED plan missing {missing}")
            mem = TradeMemory(trade_id=e.trade_id, plan=dict(plan),
                              direction=int(plan["direction"]),
                              qty=float(plan["qty"]), sl=float(plan["sl"]),
                              hwm=float(plan["entry"]))
            mem.seq_next = 1
            continue

        if e.event_type == "POSITION_OPENED":
            raise EventError(f"seq {e.sequence}: POSITION_OPENED twice — a trade "
                             "opens once")
        if mem.closed:
            raise EventError(f"seq {e.sequence}: {e.event_type} after "
                             "POSITION_CLOSED — closed is terminal")

        p, t = e.payload, e.event_type
        if t == "FACTS_OBSERVED":
            if "now_et" in p and p["now_et"]:
                mem.last_now_et = p["now_et"]
        elif t == "HWM_UPDATED":
            hwm = float(_require(p, "hwm", t))
            improved = hwm > mem.hwm if mem.direction > 0 else hwm < mem.hwm
            if not improved:
                raise EventError(
                    f"seq {e.sequence}: HWM_UPDATED {mem.hwm:g} -> {hwm:g} is not "
                    "an improvement — the high-water mark never regresses")
            mem.hwm = hwm
        elif t == "TP1_TRIGGERED":
            if mem.tp1_done:
                raise EventError(f"seq {e.sequence}: TP1_TRIGGERED twice")
            mem.tp1_done = True
        elif t == "STOP_ADVANCED":
            sl = float(_require(p, "sl", t))
            looser = sl < mem.sl if mem.direction > 0 else sl > mem.sl
            if looser:
                raise EventError(
                    f"seq {e.sequence}: STOP_ADVANCED {mem.sl:g} -> {sl:g} loosens "
                    "the stop — the reducer mirrors C003, history included")
            mem.sl = sl
        elif t == "DIRECTIVE_ACCEPTED":
            _require(p, "directive_id", t)            # provenance only
        elif t == "PARTIAL_ORDER_SUBMITTED":
            key = _require(p, "idempotency_key", t)
            if key in mem.applied_reduction_keys:
                raise EventError(f"seq {e.sequence}: submission for already-applied "
                                 f"key {key!r} — this reduction already happened")
            if key in mem.pending_reduction_keys:
                raise EventError(f"seq {e.sequence}: duplicate submission {key!r}")
            mem.pending_reduction_keys = mem.pending_reduction_keys | {key}
        elif t == "PARTIAL_FILL_CONFIRMED":
            key = _require(p, "idempotency_key", t)
            frac = float(_require(p, "fraction", t))
            if key in mem.applied_reduction_keys:
                raise EventError(
                    f"seq {e.sequence}: fill for already-applied key {key!r} — "
                    "refusing the double-reduce (C005, persisted form)")
            if key not in mem.pending_reduction_keys:
                raise EventError(
                    f"seq {e.sequence}: fill with no matching submission {key!r} — "
                    "a fill from nowhere is a reconciliation failure, not a fact")
            if not (0.0 < frac <= 1.0):
                raise EventError(f"seq {e.sequence}: fraction {frac} outside (0, 1]")
            mem.qty = mem.qty * (1.0 - frac)
            mem.pending_reduction_keys = mem.pending_reduction_keys - {key}
            mem.applied_reduction_keys = mem.applied_reduction_keys | {key}
            mem.tp2_done = True
        elif t == "TIME_FLATTEN_TRIGGERED":
            pass                                      # marker; close follows
        elif t == "POSITION_CLOSED":
            mem.closed = True
            mem.close_reason = p.get("reason")
        elif t == "RECONCILIATION_FAILED":
            mem.reconciliation_failed = True
        # (unknown types are impossible: TradeEvent refused them at construction)

        if t in _REVISION_ADVANCING:
            mem.state_revision += 1
        mem.seq_next = e.sequence + 1

    return mem


# ------------------------------------------------------------------ adapter

def to_trade_state(mem: TradeMemory):
    """TradeMemory -> TradeState via the runner's OWN plan constructor (one
    implementation of plan->state, same reason backtest uses it), then overlay
    the folded facts. TradeState stays the only decision input."""
    from runner import state_from_plan
    from stockfish_exit import Stage
    s = state_from_plan(mem.plan)
    s.qty = mem.qty
    s.sl = mem.sl
    s.hwm = mem.hwm
    s.state_revision = mem.state_revision
    s.last_now_et = mem.last_now_et
    if mem.closed:
        s.stage = Stage.CLOSED
    elif mem.tp2_done:
        s.stage = Stage.SCALED         # the engine advances SCALED->RUNNER itself
    elif mem.tp1_done:
        s.stage = Stage.PROTECTED
    return s


# --------------------------------------------------------------- translator

def events_from_decision(events: List[TradeEvent], state, actions, *,
                         occurred_at: str, source_version: str,
                         post_apply: bool = True) -> List[TradeEvent]:
    """The ONE translator from an engine cycle to events. Two documented call
    points, one implementation:

      post_apply=True (default, the 012 fixtures): called AFTER apply_actions;
      reconstructs each reduction's pre-apply revision by subtracting the
      batch's mutating count.

      post_apply=False (the Gate-2 runner, append-BEFORE-apply): called after
      decide_exit but before apply_actions — decide_exit has already performed
      the semantic transitions (hwm, stage), so everything needed exists, and
      the revision base is simply the CURRENT revision, no subtraction.

    Either way the recorded idempotency keys match what the caller threads
    (key = kind:fraction:revision-computed-against)."""
    from stockfish_constitution import MUTATING
    if not events:
        raise EventError("events_from_decision needs an opened log — emit "
                         "POSITION_OPENED first")
    mem = rebuild(events)
    trade_id = events[0].trade_id
    out = list(events)
    seq = mem.seq_next

    def emit(etype: str, payload: dict):
        nonlocal seq, out
        out = append(out, TradeEvent(
            event_id=f"{trade_id}-{seq}", trade_id=trade_id, sequence=seq,
            event_type=etype, occurred_at=occurred_at,
            source_version=source_version, payload=payload))
        seq += 1

    emit("FACTS_OBSERVED", {"price": state.price, "now_et": state.now_et})
    if state.tp1_done and not mem.tp1_done:
        emit("TP1_TRIGGERED", {"price": state.price})
    hwm_improved = (state.hwm > mem.hwm if mem.direction > 0
                    else state.hwm < mem.hwm)
    if hwm_improved:
        emit("HWM_UPDATED", {"hwm": state.hwm})

    # revision base: post-apply calls reconstruct pre-apply by subtracting one
    # per mutating action; pre-apply calls are already AT the base
    rev = state.state_revision
    if post_apply:
        rev -= sum(1 for a in actions if a.kind in MUTATING)
    for a in actions:
        if a.kind == "MOVE_SL":
            emit("STOP_ADVANCED", {"sl": a.sl, "reason": a.reason})
        elif a.kind == "TAKE_PARTIAL":
            key = f"{a.kind}:{a.fraction!r}:{rev}"
            emit("PARTIAL_ORDER_SUBMITTED",
                 {"fraction": a.fraction, "idempotency_key": key,
                  "reason": a.reason})
            emit("PARTIAL_FILL_CONFIRMED",
                 {"fraction": a.fraction, "idempotency_key": key,
                  "fill_price": state.price})
        elif a.kind == "EXIT_ALL":
            if "time flatten" in (a.reason or ""):
                emit("TIME_FLATTEN_TRIGGERED", {"reason": a.reason})
            emit("POSITION_CLOSED", {"reason": a.reason})
        # HOLD: the FACTS_OBSERVED above already recorded the cycle
        if a.kind in MUTATING:
            rev += 1
    return out


# --------------------------------------------------------------- persistence

class JsonlEventLog:
    """Append-only JSONL, fsync per append. Crash-safe by construction: the
    only possible damage is a torn final line, which load() detects."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, event: TradeEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        created = not self.path.exists()
        with self.path.open("a") as fh:
            fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if created:
            # Spec 012 wiring obligation: fsync the PARENT DIRECTORY when the
            # file is first created — a file fsync makes the bytes durable, but
            # the directory entry itself can vanish on power loss, taking the
            # whole brand-new log with it.
            dfd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)

    def truncate_torn_tail(self) -> int:
        """Physically remove a torn final line (crash mid-append) so a future
        append cannot CONCATENATE onto the fragment and corrupt the log
        mid-line. The torn line was never durable, so this is recovery, not
        history editing. Returns bytes removed. fsyncs the truncation."""
        data = self.path.read_bytes()
        if not data:
            return 0
        if data.endswith(b"\n"):
            # A newline-terminated final line was a COMPLETE write. If it does
            # not parse, that is mid-log corruption, not a tear — and corruption
            # is never repaired by guessing (adversarial review finding 4: the
            # last line must not get a repair exemption the middle doesn't).
            return 0
        cut = data.rfind(b"\n") + 1                   # drop the open fragment
        with self.path.open("rb+") as fh:
            fh.truncate(cut)
            fh.flush()
            os.fsync(fh.fileno())
        return len(data) - cut

    def load(self, *, tolerate_torn_tail: bool = False) -> List[TradeEvent]:
        if not self.path.exists():
            return []
        text = self.path.read_text()
        # Only an UNTERMINATED final fragment is a tear (crash mid-write). A
        # newline-terminated line that fails to parse was a complete write and
        # is mid-log corruption wherever it sits — never repairable by guessing.
        complete = text.endswith("\n") or not text
        lines = text.splitlines()
        events: List[TradeEvent] = []
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as err:
                if i == len(lines) - 1 and not complete:   # torn tail
                    if tolerate_torn_tail:
                        break
                    raise TornTailError(
                        f"{self.path}: final line is torn (crash mid-write). "
                        "Re-load with tolerate_torn_tail=True to drop it — an "
                        "explicit operator choice, never a default.") from err
                raise EventError(f"{self.path}: line {i + 1} is corrupt mid-log "
                                 "— not recoverable by guessing") from err
            events.append(TradeEvent.from_dict(d))
        return events


if __name__ == "__main__":
    print(__doc__)
    print(f"event types ({len(EVENT_TYPES)}):")
    for t in sorted(EVENT_TYPES):
        print(f"  {t}")
    print("\nrun the suite: pytest daytrade/test_trade_events.py -v")
