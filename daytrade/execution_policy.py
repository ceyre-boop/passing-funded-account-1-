#!/usr/bin/env python3
"""EXECUTION POLICY + FILL RECONCILIATION — spec 013.

An exit DECISION and an execution ATTEMPT are different concepts. `decide_exit`
says WHAT reduction is required (that authority never moves). This module says
HOW to submit it — order type, price, provenance — and reconciles what the
broker actually did, partial fill by partial fill.

TWO HARD RULES:
  1. The planner NEVER changes the requested reduction. `ExecutionPlan.qty` is
     the intent's quantity, always. A planner that resizes is a second decision
     engine, which is the one thing this architecture forbids.
  2. Submission completes nothing. An intent is FILLED when filled == intended,
     and only confirmed fills produce the PARTIAL_FILL_CONFIRMED payload for
     the 012 event log.

FAIL LOUD: a stale, crossed, or empty quote refuses. An over-fill, an unknown
order, a conflicting duplicate fill, or an over-submission raises
`ReconciliationError` — absorbed quantity errors are how exposure quietly
diverges from reality. `broker.py` remains the only transport; this module
never talks to a network.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

MAX_QUOTE_AGE_S = 180.0

ORDER_TYPES = ("MARKET", "AGGRESSIVE_LIMIT", "PASSIVE_LIMIT")  # STAGED reserved


class ExecutionRefused(RuntimeError):
    """No plan we are willing to act on. Never downgraded to a guess."""


class ReconciliationError(RuntimeError):
    """The broker's story and ours diverged. Raised, never absorbed."""


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    age_s: float
    source: str


@dataclass(frozen=True)
class ExitIntent:
    """WHAT the engine requires. Immutable — the planner reads it, period."""
    intent_id: str
    trade_id: str
    kind: str                   # REDUCE | CLOSE
    qty: float                  # shares to reduce
    side: str                   # SELL (long reduction) | BUY (short reduction)
    urgency: Optional[str]      # None | 'tighten' | 'exit'
    idempotency_key: str
    reason: str
    snapshot_id: str            # the input snapshot this intent was computed from
    created_at: str

    def __post_init__(self):
        if self.kind not in ("REDUCE", "CLOSE"):
            raise ExecutionRefused(f"unknown intent kind {self.kind!r}")
        if self.side not in ("SELL", "BUY"):
            raise ExecutionRefused(f"unknown side {self.side!r}")


@dataclass(frozen=True)
class ExecutionPlan:
    """HOW to submit it. Deterministic, self-explaining, resize-free."""
    intent_id: str
    order_type: str
    qty: float
    limit_price: Optional[float]
    reason: str
    snapshot_id: str


def plan_execution(intent: ExitIntent, q: Quote, *,
                   slippage_budget: float) -> ExecutionPlan:
    """Deterministic: same intent + same quote -> same plan.

      urgency 'exit'          -> MARKET (urgency already decided the trade-off)
      spread <= budget        -> AGGRESSIVE_LIMIT at the far touch (cross now)
      spread >  budget        -> PASSIVE_LIMIT at the near touch (sit, don't pay)

    Refusals: zero quantity, stale quote, crossed or non-positive quote. A bad
    quote never becomes a guessed price.
    """
    if intent.qty <= 0:
        raise ExecutionRefused(f"{intent.intent_id}: qty {intent.qty} — nothing to do "
                               "is not an order")
    if q.age_s > MAX_QUOTE_AGE_S:
        raise ExecutionRefused(f"quote is {q.age_s:.0f}s old (max {MAX_QUOTE_AGE_S:.0f}) "
                               "— refusing to price an order on a stale book")
    if intent.urgency == "exit":
        return ExecutionPlan(intent.intent_id, "MARKET", intent.qty, None,
                             f"urgent exit -> MARKET ({intent.reason}) "
                             f"[snapshot {intent.snapshot_id}]", intent.snapshot_id)
    if q.bid <= 0 or q.ask <= 0 or q.bid > q.ask:
        raise ExecutionRefused(f"malformed quote bid={q.bid} ask={q.ask} — crossed or "
                               "empty books are not tradable facts")
    spread = q.ask - q.bid
    far, near = (q.bid, q.ask) if intent.side == "SELL" else (q.ask, q.bid)
    if spread <= slippage_budget:
        return ExecutionPlan(intent.intent_id, "AGGRESSIVE_LIMIT", intent.qty, far,
                             f"spread {spread:.4f} within budget {slippage_budget:.4f} "
                             f"-> cross at {far:g} [snapshot {intent.snapshot_id}]",
                             intent.snapshot_id)
    return ExecutionPlan(intent.intent_id, "PASSIVE_LIMIT", intent.qty, near,
                         f"spread {spread:.4f} exceeds budget {slippage_budget:.4f} "
                         f"-> sit at {near:g} [snapshot {intent.snapshot_id}]",
                         intent.snapshot_id)


# ------------------------------------------------------------------- ledger

@dataclass(frozen=True)
class ExecEvent:
    """One broker fact. The ledger is a pure fold over these."""
    kind: str                   # SUBMIT | ACK | FILL | CANCEL | REJECT
    order_id: str
    intent: Optional[ExitIntent] = None     # SUBMIT only
    qty: float = 0.0
    price: float = 0.0
    fill_id: Optional[str] = None           # FILL only


@dataclass
class _Order:
    intent_id: str
    submitted: float
    filled: float = 0.0
    canceled: float = 0.0
    acked: bool = False
    status: str = "OPEN"        # OPEN | CANCELED | REJECTED
    fills: dict = field(default_factory=dict)   # fill_id -> (qty, price)

    @property
    def room(self) -> float:
        return self.submitted - self.filled


@dataclass
class _Intent:
    intent: ExitIntent
    orders: list = field(default_factory=list)  # order_ids


class FillLedger:
    """Reconciles intents against broker facts. Every method that reports a
    quantity derives it — there is no cached number to drift."""

    def __init__(self):
        self._orders: dict[str, _Order] = {}
        self._intents: dict[str, _Intent] = {}

    # ---- the fold ----------------------------------------------------------
    def apply(self, e: ExecEvent) -> None:
        if e.kind == "SUBMIT":
            if e.intent is None:
                raise ReconciliationError("SUBMIT without an intent")
            if e.order_id in self._orders:
                raise ReconciliationError(f"order {e.order_id!r} submitted twice")
            rec = self._intents.setdefault(e.intent.intent_id, _Intent(e.intent))
            remaining = rec.intent.qty - self.filled(e.intent.intent_id)
            if e.qty > remaining + 1e-9:
                raise ReconciliationError(
                    f"over-submission: {e.qty:g} against {remaining:g} remaining on "
                    f"{e.intent.intent_id!r} — submitting more than is owed is how "
                    "a reduction becomes an accidental position")
            self._orders[e.order_id] = _Order(e.intent.intent_id, submitted=e.qty)
            rec.orders.append(e.order_id)
            return

        o = self._orders.get(e.order_id)
        if o is None:
            raise ReconciliationError(f"{e.kind} for unknown order {e.order_id!r}")

        if e.kind == "ACK":
            o.acked = True
        elif e.kind == "FILL":
            if e.fill_id is None:
                raise ReconciliationError(f"fill on {e.order_id!r} carries no fill_id")
            prior = o.fills.get(e.fill_id)
            if prior is not None:
                if prior == (e.qty, e.price):
                    return                              # broker retry: no-op
                raise ReconciliationError(
                    f"fill_id {e.fill_id!r} replayed with different qty/price — "
                    "two facts are claiming the same fill")
            if o.filled + e.qty > o.submitted + 1e-9:
                raise ReconciliationError(
                    f"over-fill on {e.order_id!r}: {o.filled:g}+{e.qty:g} > "
                    f"submitted {o.submitted:g} — refusing to absorb it")
            o.fills[e.fill_id] = (e.qty, e.price)
            o.filled += e.qty
            if o.status == "CANCELED":
                # late fill racing the cancel: honored within submitted; the
                # canceled quantity shrinks to keep the identity exact
                o.canceled = o.submitted - o.filled
        elif e.kind == "CANCEL":
            o.status = "CANCELED"
            o.canceled = o.submitted - o.filled
        elif e.kind == "REJECT":
            o.status = "REJECTED"
            o.canceled = o.submitted - o.filled
        else:
            raise ReconciliationError(f"unknown event kind {e.kind!r}")

    # ---- derived truth ------------------------------------------------------
    def _orders_of(self, intent_id: str) -> list:
        rec = self._intents.get(intent_id)
        return [self._orders[oid] for oid in rec.orders] if rec else []

    def filled(self, intent_id: str) -> float:
        return sum(o.filled for o in self._orders_of(intent_id))

    def open_submitted(self, intent_id: str) -> float:
        return sum(o.submitted - o.filled - o.canceled
                   for o in self._orders_of(intent_id))

    def pending_exposure(self, intent_id: str) -> float:
        """What is still OWED against the decision: intended minus confirmed."""
        rec = self._intents.get(intent_id)
        if rec is None:
            return 0.0
        return rec.intent.qty - self.filled(intent_id)

    def avg_fill_price(self, intent_id: str) -> Optional[float]:
        pairs = [(q, p) for o in self._orders_of(intent_id)
                 for q, p in o.fills.values()]
        total = sum(q for q, _ in pairs)
        return None if total == 0 else sum(q * p for q, p in pairs) / total

    def status(self, intent_id: str) -> str:
        """FILLED only when filled == intended. Submission completes nothing."""
        rec = self._intents.get(intent_id)
        if rec is None:
            return "UNKNOWN"
        if abs(self.filled(intent_id) - rec.intent.qty) <= 1e-9:
            return "FILLED"
        return "OPEN"

    def order_room(self, order_id: str) -> float:
        o = self._orders.get(order_id)
        return 0.0 if o is None else o.room

    def snapshot(self) -> dict:
        return {oid: asdict(o) for oid, o in sorted(self._orders.items())}


# ------------------------------------------------------- bridge to card 012

def reduction_payloads(led: FillLedger, intent: ExitIntent, *,
                       fraction: float) -> tuple[dict, Optional[dict]]:
    """The 012 event payloads for one reduction. The submission payload always
    exists once submitted; the FILL payload exists ONLY when the intent is
    complete — a submitted order is not a banked partial, and writing it as one
    would be the exact lie the reconciliation rule forbids."""
    sub = {"fraction": fraction, "idempotency_key": intent.idempotency_key,
           "reason": intent.reason}
    if led.status(intent.intent_id) != "FILLED":
        return sub, None
    return sub, {"fraction": fraction, "idempotency_key": intent.idempotency_key,
                 "fill_price": led.avg_fill_price(intent.intent_id)}


if __name__ == "__main__":
    print(__doc__)
    print("run the suite: pytest daytrade/test_execution_policy.py -v")
