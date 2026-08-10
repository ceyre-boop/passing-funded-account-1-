"""Card 012 — event-sourced trade memory.

The test that matters most is the golden round-trip: run the engine live while
capturing events, DESTROY all process state, rebuild from events alone, run the
next bar — identical actions and identical state (sl, qty, stage, revision, and
the C005 key set, which is the residual this card closes).

Every reducer invariant has a named test here and a fault row in
specs/012_MUTATION_LOG.md (driver: mutation_check_012.py).
"""
from __future__ import annotations

import json

import pytest

from stockfish_exit import Stage, TradeState, apply_actions, decide_exit
from trade_events import (EVENT_TYPES, EventError, JsonlEventLog, TornTailError,
                          TradeEvent, append, events_from_decision, rebuild,
                          to_trade_state)

T = "2026-08-07T14:30:00+00:00"
SRC = "test-1"

# Explicit params, no preset name — state_from_plan refuses the ambiguity of both.
PLAN = {"symbol": "NVDA", "direction": 1, "entry": 200.0, "qty": 100.0,
        "sl": 199.0, "tp1": 201.0, "tp2": 202.14, "trail_dist": 0.5,
        "goal_fraction": 0.5, "trail_mult": 1.5, "be_arm_frac": 0.25,
        "hold_past_tp2": True}


def ev(seq: int, etype: str, payload: dict | None = None, *,
       eid: str | None = None, occurred: str = T) -> TradeEvent:
    return TradeEvent(event_id=eid or f"e{seq}", trade_id="t1", sequence=seq,
                      event_type=etype, occurred_at=occurred,
                      source_version=SRC, payload=payload or {})


def opened(seq: int = 0) -> TradeEvent:
    return ev(seq, "POSITION_OPENED", {"plan": PLAN})


# ------------------------------------------------------------- the envelope

def test_envelope_validates():
    with pytest.raises(EventError):
        ev(0, "SOMETHING_ELSE")                       # unknown type
    with pytest.raises(EventError):
        ev(0, "POSITION_OPENED", occurred="2026-08-07T14:30:00")  # naive ts
    with pytest.raises(EventError):
        TradeEvent(event_id="", trade_id="t1", sequence=0,
                   event_type="POSITION_OPENED", occurred_at=T,
                   source_version=SRC, payload={})    # empty id
    with pytest.raises(EventError):
        ev(0, "POSITION_OPENED", {"note": "x" * 513})  # redaction: no long text
    with pytest.raises(EventError):
        ev(-1, "POSITION_OPENED")                     # negative sequence
    with pytest.raises(EventError):
        TradeEvent(event_id="e0", trade_id="t1", sequence=0,
                   event_type="POSITION_OPENED", occurred_at=T,
                   source_version=SRC, payload={"f": object()})  # unserializable


def test_envelope_fingerprint_is_content_stable():
    a = ev(0, "FACTS_OBSERVED", {"price": 201.0, "now_et": "10:00"})
    b = ev(0, "FACTS_OBSERVED", {"now_et": "10:00", "price": 201.0})  # key order
    assert a.fingerprint == b.fingerprint
    c = ev(0, "FACTS_OBSERVED", {"price": 201.5, "now_et": "10:00"})
    assert a.fingerprint != c.fingerprint


def test_envelope_roundtrips_through_dict():
    e = opened()
    assert TradeEvent.from_dict(e.to_dict()) == e
    with pytest.raises(EventError):
        TradeEvent.from_dict({**e.to_dict(), "surprise_field": 1})  # strict


# ---------------------------------------------------------------- append()

def test_append_is_pure_and_contiguous():
    log: list = []
    log2 = append(log, opened())
    assert log == [] and len(log2) == 1               # pure: input untouched
    with pytest.raises(EventError):
        append(log2, ev(2, "FACTS_OBSERVED", {"price": 201.0}))  # gap
    log3 = append(log2, ev(1, "FACTS_OBSERVED", {"price": 201.0}))
    assert [e.sequence for e in log3] == [0, 1]


def test_append_duplicate_identical_is_noop_different_raises():
    base = append([], opened())
    dup = opened()                                    # same seq, same payload
    assert append(base, dup) == base                  # idempotent retry
    with pytest.raises(EventError):
        append(base, ev(0, "POSITION_OPENED", {"plan": {**PLAN, "qty": 999.0}}))


# ---------------------------------------------------------------- reducer

def test_rebuild_requires_opened_first():
    # match pins the DEDICATED first-event check — without it, a missing-plan
    # error downstream would keep this green with the check deleted
    with pytest.raises(EventError, match="must be POSITION_OPENED"):
        rebuild([ev(0, "FACTS_OBSERVED", {"price": 201.0})])
    with pytest.raises(EventError):
        rebuild(append([opened()], ev(1, "POSITION_OPENED", {"plan": PLAN})))


def test_rebuild_closed_is_terminal():
    events = [opened(), ev(1, "POSITION_CLOSED", {"reason": "stop hit"})]
    mem = rebuild(events)
    assert mem.closed
    with pytest.raises(EventError):
        rebuild(events + [ev(2, "FACTS_OBSERVED", {"price": 200.0})])


def test_rebuild_rejects_gap_and_regression():
    with pytest.raises(EventError):
        rebuild([opened(), ev(2, "FACTS_OBSERVED", {"price": 201.0})])
    with pytest.raises(EventError):
        rebuild([opened(), ev(0, "FACTS_OBSERVED", {"price": 201.0},
                              eid="other")])


def test_rebuild_rejects_unknown_schema_version():
    bad = TradeEvent(event_id="e1", trade_id="t1", sequence=1,
                     event_type="FACTS_OBSERVED", occurred_at=T,
                     source_version=SRC, payload={"price": 201.0},
                     schema_version="99")
    with pytest.raises(EventError):
        rebuild([opened(), bad])


def test_stop_advance_never_loosens():
    """Reducer-side mirror of C003: a STOP_ADVANCED that retreats raises."""
    events = [opened(), ev(1, "STOP_ADVANCED", {"sl": 200.0, "reason": "be"})]
    assert rebuild(events).sl == 200.0
    with pytest.raises(EventError):
        rebuild(events + [ev(2, "STOP_ADVANCED", {"sl": 199.5, "reason": "??"})])


def test_hwm_never_regresses():
    events = [opened(), ev(1, "HWM_UPDATED", {"hwm": 203.0})]
    assert rebuild(events).hwm == 203.0
    with pytest.raises(EventError):
        rebuild(events + [ev(2, "HWM_UPDATED", {"hwm": 202.0})])


def test_partial_fill_requires_submission_and_keys_are_replay_safe():
    key = "TAKE_PARTIAL:0.5:0"

    def fill(seq: int) -> TradeEvent:
        return ev(seq, "PARTIAL_FILL_CONFIRMED",
                  {"fraction": 0.5, "idempotency_key": key, "fill_price": 202.14})

    with pytest.raises(EventError, match="no matching submission"):
        rebuild([opened(), fill(1)])                  # fill with no submission
    events = [opened(),
              ev(1, "PARTIAL_ORDER_SUBMITTED",
                 {"fraction": 0.5, "idempotency_key": key}),
              fill(2)]
    mem = rebuild(events)
    assert mem.qty == pytest.approx(50.0)
    assert key in mem.applied_reduction_keys          # C005 persisted WITH state
    # a replayed fill with the SAME key must not double-reduce. match pins the
    # applied-key check specifically — the pending-key check would also raise
    # here (key left pending), which would mask this check's deletion.
    with pytest.raises(EventError, match="double-reduce"):
        rebuild(events + [ev(3, "PARTIAL_FILL_CONFIRMED",
                             {"fraction": 0.5, "idempotency_key": key,
                              "fill_price": 202.14})])


def test_rebuild_is_pure_and_deterministic():
    events = [opened(), ev(1, "HWM_UPDATED", {"hwm": 203.0}),
              ev(2, "STOP_ADVANCED", {"sl": 200.0, "reason": "be"})]
    a, b = rebuild(events), rebuild(events)
    assert a == b
    assert [e.sequence for e in events] == [0, 1, 2]  # inputs untouched


# ------------------------------------------------------- the golden test

def _run_bar(state: TradeState, price: float, now_et: str, keys: set) -> list:
    state.price = price
    state.now_et = now_et
    acts = decide_exit(state)
    apply_actions(state, acts, keys)
    return acts


def test_golden_destroy_state_rebuild_identical_decision():
    """Open -> bars (TP1 arm, TP2 partial, trail advance) captured as events ->
    destroy process state -> rebuild from events alone -> the next bar produces
    IDENTICAL actions and state to the uninterrupted run. This is the card's
    reason to exist."""
    from runner import state_from_plan
    bars = [(200.5, "09:35"), (201.2, "09:40"), (202.5, "09:45"), (203.0, "09:50")]
    next_bar = (203.4, "09:55")

    # --- uninterrupted run ------------------------------------------------
    s_live = state_from_plan(PLAN)
    keys_live: set = set()
    events = [TradeEvent(event_id="open", trade_id="t1", sequence=0,
                         event_type="POSITION_OPENED", occurred_at=T,
                         source_version=SRC, payload={"plan": PLAN})]
    for price, clock in bars:
        acts = _run_bar(s_live, price, clock, keys_live)
        events = events_from_decision(events, s_live, acts,
                                      occurred_at=T, source_version=SRC)
    pre_bar = {f: getattr(s_live, f) for f in
               ("sl", "qty", "hwm", "state_revision", "last_now_et")}
    live_next = _run_bar(s_live, *next_bar, keys_live)

    # --- destroyed process: only the event log survives -------------------
    mem = rebuild(events)
    s_rebuilt = to_trade_state(mem)
    keys_rebuilt = set(mem.applied_reduction_keys)
    # BEFORE the next bar: the rebuilt state itself must match the live state
    # as it stood at the crash point. Comparing only after the bar lets the
    # engine re-derive some fields (the trail re-lifts a forgotten stop) and
    # mask an adapter gap — caught by the mutation loop.
    for f, want in pre_bar.items():
        assert getattr(s_rebuilt, f) == want, f"pre-bar {f}"
    rebuilt_next = _run_bar(s_rebuilt, *next_bar, keys_rebuilt)

    assert [(a.kind, a.sl, a.fraction) for a in rebuilt_next] == \
           [(a.kind, a.sl, a.fraction) for a in live_next]
    for f in ("sl", "qty", "hwm", "stage", "state_revision", "last_now_et"):
        assert getattr(s_rebuilt, f) == getattr(s_live, f), f
    assert keys_rebuilt == keys_live                  # C005 survives the crash


def test_replaying_same_log_twice_changes_nothing():
    s = TradeState(**{k: PLAN[k] for k in
                      ("direction", "entry", "qty", "sl", "tp1", "tp2",
                       "trail_dist")}, price=PLAN["entry"])
    events = [TradeEvent(event_id="open", trade_id="t1", sequence=0,
                         event_type="POSITION_OPENED", occurred_at=T,
                         source_version=SRC, payload={"plan": PLAN})]
    acts = _run_bar(s, 202.5, "09:45", set())
    events = events_from_decision(events, s, acts, occurred_at=T,
                                  source_version=SRC)
    assert rebuild(events) == rebuild(events)


# ------------------------------------------------------------ persistence

def test_jsonl_log_roundtrip_and_torn_tail(tmp_path):
    path = tmp_path / "t1.events.jsonl"
    log = JsonlEventLog(path)
    e0, e1 = opened(), ev(1, "HWM_UPDATED", {"hwm": 203.0})
    log.append(e0)
    log.append(e1)
    assert log.load() == [e0, e1]

    with path.open("a") as fh:                        # simulate a crash mid-write
        fh.write('{"event_id": "e2", "trade')
    with pytest.raises(TornTailError):
        log.load()
    assert log.load(tolerate_torn_tail=True) == [e0, e1]

    # corruption in the MIDDLE is never recoverable by guessing
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], "{broken", lines[1]]) + "\n")
    with pytest.raises(EventError):
        log.load(tolerate_torn_tail=True)


def test_event_types_are_the_spec_eleven():
    assert EVENT_TYPES == frozenset({
        "POSITION_OPENED", "FACTS_OBSERVED", "HWM_UPDATED", "TP1_TRIGGERED",
        "STOP_ADVANCED", "DIRECTIVE_ACCEPTED", "PARTIAL_ORDER_SUBMITTED",
        "PARTIAL_FILL_CONFIRMED", "TIME_FLATTEN_TRIGGERED", "POSITION_CLOSED",
        "RECONCILIATION_FAILED"})
