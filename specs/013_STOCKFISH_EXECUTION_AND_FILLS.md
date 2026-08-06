# 013 — EXECUTION POLICY AND PARTIAL-FILL RECONCILIATION `[PLAN]`

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

