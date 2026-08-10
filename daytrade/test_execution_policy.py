"""Card 013 — execution policy and partial-fill reconciliation.

An exit DECISION and an execution ATTEMPT are different concepts: `decide_exit`
says what reduction is required; the planner says how to submit it; the fill
ledger reconciles what actually happened. The planner may never change the
requested reduction or infer market meaning.

Property tests drive random legal event sequences (fixed seeds) through the
ledger and assert the quantity invariants after every event. Fault rows in
specs/013_MUTATION_LOG.md (driver: mutation_check_013.py).
"""
from __future__ import annotations

import random

import pytest

from execution_policy import (ExecEvent, ExecutionRefused, ExitIntent, FillLedger,
                              Quote, ReconciliationError, plan_execution,
                              reduction_payloads)

T = "2026-08-07T14:30:00+00:00"


def intent(qty: float = 100.0, *, urgency: str | None = None,
           iid: str = "i1") -> ExitIntent:
    return ExitIntent(intent_id=iid, trade_id="t1", kind="REDUCE",
                      qty=qty, side="SELL", urgency=urgency,
                      idempotency_key="TAKE_PARTIAL:0.5:0",
                      reason="TP2: day goal banked", snapshot_id="snap-1",
                      created_at=T)


def quote(bid: float = 200.00, ask: float = 200.04, age_s: float = 1.0) -> Quote:
    return Quote(bid=bid, ask=ask, age_s=age_s, source="test")


# ------------------------------------------------------------------ planner

def test_planner_never_changes_the_requested_reduction():
    """The one rule that outranks all others: plan.qty == intent.qty, for every
    urgency and every quote. A planner that resizes is a second decision engine."""
    for urg in (None, "tighten", "exit"):
        for q in (quote(), quote(bid=200.0, ask=200.5)):
            p = plan_execution(intent(qty=73.0, urgency=urg), q,
                               slippage_budget=0.05)
            assert p.qty == 73.0, (urg, q)


def test_urgent_exit_goes_market():
    p = plan_execution(intent(urgency="exit"), quote(), slippage_budget=0.05)
    assert p.order_type == "MARKET" and p.limit_price is None
    assert p.snapshot_id == "snap-1" and p.reason        # deterministic provenance


def test_tight_spread_crosses_wide_spread_sits():
    # spread 0.04 <= budget 0.05 -> AGGRESSIVE_LIMIT at the far touch (bid, selling)
    p = plan_execution(intent(), quote(bid=200.00, ask=200.04), slippage_budget=0.05)
    assert p.order_type == "AGGRESSIVE_LIMIT" and p.limit_price == 200.00
    # spread 0.50 > budget -> PASSIVE_LIMIT at the near touch (ask, selling)
    p = plan_execution(intent(), quote(bid=200.00, ask=200.50), slippage_budget=0.05)
    assert p.order_type == "PASSIVE_LIMIT" and p.limit_price == 200.50


def test_bad_quotes_are_refused_never_guessed():
    with pytest.raises(ExecutionRefused):
        plan_execution(intent(), quote(age_s=999.0), slippage_budget=0.05)  # stale
    with pytest.raises(ExecutionRefused):
        plan_execution(intent(), quote(bid=200.10, ask=200.00),
                       slippage_budget=0.05)                                # crossed
    with pytest.raises(ExecutionRefused):
        plan_execution(intent(), quote(bid=0.0, ask=0.0), slippage_budget=0.05)
    with pytest.raises(ExecutionRefused):
        plan_execution(intent(qty=0.0), quote(), slippage_budget=0.05)      # nothing to do


def test_plans_are_deterministic():
    a = plan_execution(intent(), quote(), slippage_budget=0.05)
    b = plan_execution(intent(), quote(), slippage_budget=0.05)
    assert a == b


# ------------------------------------------------------------------- ledger

def submit(led: FillLedger, it: ExitIntent, qty: float, attempt: int = 0) -> str:
    oid = f"{it.intent_id}:{attempt}"
    led.apply(ExecEvent("SUBMIT", intent=it, order_id=oid, qty=qty))
    return oid


def test_full_fill_completes_partial_does_not():
    led = FillLedger()
    it = intent(qty=100.0)
    oid = submit(led, it, 100.0)
    led.apply(ExecEvent("FILL", order_id=oid, qty=60.0, price=200.0, fill_id="f1"))
    assert led.status(it.intent_id) == "OPEN"            # partial is not complete
    assert led.pending_exposure(it.intent_id) == pytest.approx(40.0)
    led.apply(ExecEvent("FILL", order_id=oid, qty=40.0, price=200.1, fill_id="f2"))
    assert led.status(it.intent_id) == "FILLED"
    assert led.filled(it.intent_id) == pytest.approx(100.0)
    assert led.avg_fill_price(it.intent_id) == pytest.approx(
        (60 * 200.0 + 40 * 200.1) / 100)


def test_overfill_is_reconciliation_failure_not_absorbed():
    led = FillLedger()
    it = intent(qty=100.0)
    oid = submit(led, it, 100.0)
    led.apply(ExecEvent("FILL", order_id=oid, qty=100.0, price=200.0, fill_id="f1"))
    # match pins the PER-ORDER check: the intent-level cross-order guard also
    # catches this shape, which would mask this check's deletion (same
    # defense-in-depth lesson as 012's fill-path pins)
    with pytest.raises(ReconciliationError, match="refusing to absorb"):
        led.apply(ExecEvent("FILL", order_id=oid, qty=1.0, price=200.0,
                            fill_id="f2"))


def test_duplicate_fill_id_identical_is_retry_different_is_corruption():
    led = FillLedger()
    it = intent(qty=100.0)
    oid = submit(led, it, 100.0)
    e = ExecEvent("FILL", order_id=oid, qty=60.0, price=200.0, fill_id="f1")
    led.apply(e)
    led.apply(e)                                         # broker retry: no-op
    assert led.filled(it.intent_id) == pytest.approx(60.0)
    with pytest.raises(ReconciliationError):
        led.apply(ExecEvent("FILL", order_id=oid, qty=61.0, price=200.0,
                            fill_id="f1"))


def test_cancel_releases_exposure_and_retry_resubmits_remainder():
    led = FillLedger()
    it = intent(qty=100.0)
    o0 = submit(led, it, 100.0)
    led.apply(ExecEvent("FILL", order_id=o0, qty=30.0, price=200.0, fill_id="f1"))
    led.apply(ExecEvent("CANCEL", order_id=o0))
    assert led.pending_exposure(it.intent_id) == pytest.approx(70.0)  # still owed
    assert led.open_submitted(it.intent_id) == pytest.approx(0.0)     # nothing live
    o1 = submit(led, it, 70.0, attempt=1)                # retry the remainder
    led.apply(ExecEvent("FILL", order_id=o1, qty=70.0, price=200.2, fill_id="f2"))
    assert led.status(it.intent_id) == "FILLED"


def test_oversubmission_beyond_remaining_raises():
    led = FillLedger()
    it = intent(qty=100.0)
    o0 = submit(led, it, 100.0)
    led.apply(ExecEvent("FILL", order_id=o0, qty=100.0, price=200.0, fill_id="f1"))
    with pytest.raises(ReconciliationError):
        submit(led, it, 1.0, attempt=1)                  # nothing remains


def test_oversubmission_counts_live_open_orders():
    """Adversarial-review finding 1: remaining must subtract LIVE submissions,
    not just fills — otherwise two full-size orders against one intent both
    pass and the books carry twice the decided reduction."""
    led = FillLedger()
    it = intent(qty=100.0)
    submit(led, it, 100.0)                               # live, unfilled
    with pytest.raises(ReconciliationError):
        submit(led, it, 100.0, attempt=1)                # nothing actually remains
    with pytest.raises(ReconciliationError):
        submit(led, it, 1.0, attempt=2)                  # not even one share


def test_cross_order_overfill_raises_never_reopens():
    """Adversarial-review finding 2: a fill on a second order that pushes the
    INTENT past intended must raise — not flip status FILLED->OPEN with
    negative pending exposure (a doubly-executed reduction reading as owed)."""
    led = FillLedger()
    it = intent(qty=100.0)
    o0 = submit(led, it, 100.0)
    led.apply(ExecEvent("CANCEL", order_id=o0))          # frees the submission
    o1 = submit(led, it, 100.0, attempt=1)
    led.apply(ExecEvent("FILL", order_id=o1, qty=100.0, price=200.0, fill_id="f1"))
    assert led.status(it.intent_id) == "FILLED"
    # the canceled order's late fill would double the reduction: refuse loudly
    with pytest.raises(ReconciliationError):
        led.apply(ExecEvent("FILL", order_id=o0, qty=50.0, price=200.0,
                            fill_id="f2"))
    assert led.status(it.intent_id) == "FILLED"          # never reopened
    assert led.pending_exposure(it.intent_id) == pytest.approx(0.0)


def test_late_fill_racing_cancel_is_honored_within_submitted():
    led = FillLedger()
    it = intent(qty=100.0)
    o0 = submit(led, it, 100.0)
    led.apply(ExecEvent("CANCEL", order_id=o0))
    led.apply(ExecEvent("FILL", order_id=o0, qty=20.0, price=200.0, fill_id="f1"))
    assert led.filled(it.intent_id) == pytest.approx(20.0)
    # the canceled quantity must shrink to keep submitted = filled + canceled
    # exact — otherwise open exposure goes negative and the books lie
    assert led.open_submitted(it.intent_id) == pytest.approx(0.0)
    with pytest.raises(ReconciliationError):             # but never past submitted
        led.apply(ExecEvent("FILL", order_id=o0, qty=90.0, price=200.0,
                            fill_id="f2"))


def test_unknown_order_and_unknown_event_raise():
    led = FillLedger()
    with pytest.raises(ReconciliationError):
        led.apply(ExecEvent("FILL", order_id="ghost:0", qty=1.0, price=200.0,
                            fill_id="f1"))
    with pytest.raises(ReconciliationError):
        led.apply(ExecEvent("TELEPORT", order_id="x"))
    it = intent(qty=100.0)
    oid = submit(led, it, 50.0)
    with pytest.raises(ReconciliationError):             # same order id twice
        led.apply(ExecEvent("SUBMIT", intent=it, order_id=oid, qty=10.0))
    with pytest.raises(ReconciliationError):             # a fill needs identity
        led.apply(ExecEvent("FILL", order_id=oid, qty=1.0, price=200.0))


def test_ledger_fold_is_deterministic():
    """Same broker event replay -> same pending exposure. The fold is pure."""
    it = intent(qty=100.0)
    events = [ExecEvent("SUBMIT", intent=it, order_id="i1:0", qty=100.0),
              ExecEvent("FILL", order_id="i1:0", qty=30.0, price=200.0, fill_id="f1"),
              ExecEvent("CANCEL", order_id="i1:0"),
              ExecEvent("SUBMIT", intent=it, order_id="i1:1", qty=70.0),
              ExecEvent("FILL", order_id="i1:1", qty=50.0, price=200.1, fill_id="f2")]
    a, b = FillLedger(), FillLedger()
    for e in events:
        a.apply(e)
        b.apply(e)
    assert a.snapshot() == b.snapshot()
    assert a.pending_exposure("i1") == pytest.approx(20.0)


def test_property_random_legal_sequences_hold_invariants():
    """Seeded random walks over legal operations: after EVERY event, no
    quantity invariant is violated — filled never exceeds intended, open
    exposure is never negative, canceled+filled never exceeds submitted."""
    for seed in (7, 17, 42, 1009):
        rng = random.Random(seed)
        led = FillLedger()
        it = intent(qty=100.0, iid=f"p{seed}")
        attempt = 0
        oid = submit(led, it, 100.0, attempt)
        fill_n = 0
        for _ in range(200):
            op = rng.choice(("fill", "fill", "cancel_resubmit", "ack"))
            if op == "ack":
                led.apply(ExecEvent("ACK", order_id=oid))
            elif op == "fill":
                room = led.order_room(oid)
                if room < 0.5:
                    continue
                fill_n += 1
                led.apply(ExecEvent("FILL", order_id=oid,
                                    qty=round(rng.uniform(0.5, room), 4),
                                    price=round(200 + rng.uniform(-1, 1), 2),
                                    fill_id=f"f{fill_n}"))
            else:
                led.apply(ExecEvent("CANCEL", order_id=oid))
                remaining = led.pending_exposure(it.intent_id)
                if remaining <= 0:
                    break
                attempt += 1
                oid = submit(led, it, remaining, attempt)
            # ---- the invariants, checked after every single event ----
            assert led.filled(it.intent_id) <= it.qty + 1e-9
            assert led.pending_exposure(it.intent_id) >= -1e-9
            assert led.open_submitted(it.intent_id) >= -1e-9
            if led.status(it.intent_id) == "FILLED":
                assert led.filled(it.intent_id) == pytest.approx(it.qty)
                break


# ------------------------------------------------------- bridge to card 012

def test_reduction_payloads_submission_alone_completes_nothing():
    led = FillLedger()
    it = intent(qty=100.0)
    oid = submit(led, it, 100.0)
    sub, fill = reduction_payloads(led, it, fraction=0.5)
    assert sub["idempotency_key"] == it.idempotency_key
    assert fill is None                                  # submitted != done
    led.apply(ExecEvent("FILL", order_id=oid, qty=100.0, price=200.05, fill_id="f1"))
    sub, fill = reduction_payloads(led, it, fraction=0.5)
    assert fill is not None
    assert fill["idempotency_key"] == it.idempotency_key
    assert fill["fill_price"] == pytest.approx(200.05)
