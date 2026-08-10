# 013 — EXECUTION POLICY AND PARTIAL-FILL RECONCILIATION `[SPEC]` (promoted 2026-08-09, ratification pending)

Coverage: long-term vision Stockfish items 6–7. Depends on 011–012.

## Boundary

`decide_exit` answers what reduction is required. A separate mechanical
execution planner answers how to submit it using spread, liquidity, urgency,
time remaining, and slippage budget. It must never infer market meaning or
change the requested reduction.

## Planned interfaces

Define immutable `ExitIntent` and `ExecutionPlan`, plus a fill ledger tracking
intended, submitted, filled, canceled, remaining quantity, average fill price,
and pending exposure. Every plan must include a deterministic reason and input
snapshot id. Broker acknowledgements and fills become events under 012.

## Planning decisions

- market/aggressive/passive/staged order vocabulary and urgency thresholds;
- cancel/replace idempotency and broker-order identity;
- partial-fill effect on lifecycle stage and future legal actions;
- slippage budget units and rejection behavior when liquidity is missing;
- paper adapter behavior and no-network unit-test fixtures.

## DoD seed

Fixtures cover full fill, partial fill, cancel, retry, stale quote, and broker
rejection. Reconciliation never marks a TP or close complete from submission
alone. The same broker event replay yields the same pending exposure.


---

## `[SPEC]` promotion — 2026-08-09, on Colin's direct instruction. Architect
## ratification post-hoc; decisions below are explicit so they can be overruled.

### Planning decisions, answered

1. **Order vocabulary:** `MARKET`, `AGGRESSIVE_LIMIT` (far touch — crosses the
   spread), `PASSIVE_LIMIT` (near touch). `STAGED` is reserved vocabulary, not
   emitted in v1. Urgency mapping is deterministic: urgency `exit` → MARKET;
   otherwise spread within the slippage budget → AGGRESSIVE_LIMIT; spread wider
   than budget → PASSIVE_LIMIT. A stale, crossed, or empty quote REFUSES
   (raises) — never a guessed price.
2. **Cancel/replace identity:** `broker_order_id = f"{intent_id}:{attempt}"`;
   every replace is a new attempt against the same intent. Fills carry a
   `fill_id`; a duplicate `fill_id` with identical qty+price is an idempotent
   no-op (broker retry), with different values it is corruption (raise).
3. **Partial fills vs lifecycle:** an intent is COMPLETE only when
   `filled == intended`. Submission alone never completes anything — the
   PARTIAL_FILL_CONFIRMED trade-event payload is only available from a
   completed intent. Stage transitions remain the engine's, driven by
   confirmed facts.
4. **Slippage budget:** dollars per share, supplied by the caller from the
   plan. MARKET has no budget (urgency already decided the trade-off). A
   missing/blown budget on a limit path degrades to PASSIVE_LIMIT, never to a
   silently worse fill.
5. **Paper adapter / fixtures:** the ledger is a pure fold over `ExecEvent`s
   (SUBMIT/ACK/FILL/CANCEL/REJECT) — no network anywhere in unit tests;
   `broker.py` remains the only transport and is untouched by this card.

### Ledger invariants (each a named test; property-tested under random legal
### sequences with fixed seeds)

- `filled ≤ submitted` per order and `filled ≤ intended` per intent — an
  over-fill raises `ReconciliationError`, it is never absorbed
- open exposure (`submitted − filled − canceled`) is never negative and cancel
  never resurrects it; a late fill racing a cancel is honored only within the
  order's submitted quantity
- over-submission beyond the intent's remaining quantity raises
- the planner NEVER changes the requested reduction: `ExecutionPlan.qty` equals
  the intent's remaining quantity, always
- the fold is pure/deterministic: the same event list twice yields identical
  ledger state and identical pending exposure

### Code surface

`daytrade/execution_policy.py`: `ExitIntent` (frozen), `plan_execution(intent,
quote, budget) -> ExecutionPlan` (frozen, deterministic reason + snapshot id),
`ExecEvent`, `FillLedger` (pure fold + invariants), `reduction_payloads`
(the bridge to 012 event payloads, carrying the intent's idempotency key).

### DoD

Fixtures cover full fill, partial fill, cancel, retry-after-cancel, late fill
racing a cancel, duplicate fill retry, over-fill, stale/crossed/empty quote,
budget degradation. Property test over seeded random legal sequences holds all
invariants. Fault rows in `mutation_check_013.py` → `specs/013_MUTATION_LOG.md`.
