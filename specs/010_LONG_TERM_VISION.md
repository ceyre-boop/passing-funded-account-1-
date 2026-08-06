# 010 — LONG-TERM VISION: STOCKFISH AS OS, ALPHAZERO AS CONTEXT ENGINE `[SKETCH]`

This is the backlog and architecture map for the work after the current v1/v2
slice. It is deliberately not a build order. Do not implement a row below until
its planning card has been promoted to `[SPEC]` with a concrete interface,
Definition of Done, fixtures, and split protocol.

## Boundary that does not move

**AlphaZero communicates meaning. Stockfish controls mechanics.**

AlphaZero may describe scenarios, regime, evidence, thesis state, confidence,
and an expiring recommendation. Stockfish validates freshness and authority,
enforces invariants, chooses stops and quantities, and decides how an approved
reduction is executed. The runner, broker, and replay harness remain I/O only.

## Current inventory

Already present in this repository:

- Stockfish 1–3: lifecycle stages, layered stop candidates, and named intent
  policies in `daytrade/stockfish_exit.py`.
- AlphaZero 1–3: scenario distributions, multidimensional regime vectors, and
  thesis monitoring in `daytrade/scenarios.py`, `daytrade/regime_vector.py`, and
  `daytrade/thesis.py`.

The remaining vision is scaffolded by the planning cards below. These cards are
not implementation specifications yet.

| Card | Area | Vision coverage | Planned code surface |
|---|---|---|---|
| 011 | Stockfish constitution | 4 | `stockfish_exit.py` or `stockfish_constitution.py` |
| 012 | Event-sourced trade memory | 5 | `trade_events.py`, replay/state adapter |
| 013 | Execution and partial fills | 6–7 | `execution_policy.py`, broker adapter |
| 014 | Shadow policies, regret, portfolio | 8–10 | `shadow.py`, `regret.py`, portfolio guard |
| 015 | Evidence and event lifecycle | AlphaZero 4–5 | `evidence.py`, event store |
| 016 | Authority, directives, context scope | AlphaZero 6–9 | `context_directive.py`, gate |
| 017 | Forecast ledger and promotion | AlphaZero 10–12 | `forecast.py`, promotion scorecard |
| 018 | Narrow inter-engine contract | bridge | versioned directive envelope |

## Dependency order for the planning pass

1. Promote 011 and 018 together: the constitution must define exactly what a
   directive is allowed to ask Stockfish to do.
2. Promote 012 before any live reconciliation or regret work: state must be
   replayable before it is made more detailed.
3. Promote 015 and 016 before expanding `news_claude.py`: raw headlines must
   become scoped, fresh evidence and expiring directives first.
4. Promote 013, then 014: execution outcomes are required inputs to shadow and
   regret measurements.
5. Promote 017 only after the event, directive, and outcome schemas are stable.

## Global constraints for every future card

- No new exit authority outside `decide_exit`.
- No AlphaZero output can directly set a broker order type, quantity, or price.
- Stale, unknown, malformed, duplicate, and contradictory inputs fail loudly or
  become an explicit abstention; they never silently default.
- Every decision is reproducible from versioned inputs and persisted state.
- Tuning uses TUNE only; sealed evaluation is one final measurement after the
  rule/model version is frozen. No look-ahead.
- Paper/shadow mode precedes any armed execution.

## Promotion rule

Each planning card starts as `[PLAN]`. A card may become `[SPEC]` only after it
states: dataclasses or JSON schema, transition/invariant table, persistence and
idempotency rules, test fixtures, failure behavior, migration path, DoD, and
the exact scorecard/baseline protocol. Claude should not write production code
for `[SKETCH]` or `[PLAN]` cards.

