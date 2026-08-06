# 012 — EVENT-SOURCED TRADE MEMORY `[PLAN]`

Coverage: long-term vision Stockfish item 5. Depends on 011.

## Intended contract

Define an append-only event envelope with `event_id`, `trade_id`, monotonic
`sequence`, `event_type`, `occurred_at`, `source_version`, and immutable payload.
Initial event types: `POSITION_OPENED`, `FACTS_OBSERVED`, `HWM_UPDATED`,
`TP1_TRIGGERED`, `STOP_ADVANCED`, `DIRECTIVE_ACCEPTED`,
`PARTIAL_ORDER_SUBMITTED`, `PARTIAL_FILL_CONFIRMED`, `TIME_FLATTEN_TRIGGERED`,
`POSITION_CLOSED`, and `RECONCILIATION_FAILED`.

Plan `append(event)` and `rebuild(events)` as pure/idempotent operations. The
reducer must reject gaps, duplicate sequence numbers with different payloads,
unknown event versions, and impossible transitions. Existing `TradeState`
remains the decision input; the adapter reconstructs it rather than making a
second exit engine.

## Required planning decisions

- event clock versus market-fact clock and timezone rules;
- crash-safe persistence format and fsync/recovery behavior;
- event identity for broker retries and duplicate fills;
- redaction policy for model prompts/news evidence;
- snapshot cadence and replay compatibility.

## DoD seed

Round-trip open → decisions → fills → close reproduces every state revision and
action from a fixture log. Replaying the same log twice produces the same state
and no duplicate side effects.

