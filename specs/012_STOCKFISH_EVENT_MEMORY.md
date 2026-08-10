# 012 — EVENT-SOURCED TRADE MEMORY `[SPEC]` (promoted 2026-08-09, ratification pending)

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


---

## `[SPEC]` promotion — 2026-08-09, on Colin's direct instruction ("finish gate
## 2-close"). Architect ratification post-hoc; the decisions below are the
## implementing seat's, made explicit so they can be overruled cheaply.

### Planning decisions, answered

1. **Event clock vs market-fact clock:** the envelope's `occurred_at` is the
   producer's market-fact clock as an ISO-8601 **timezone-aware** string
   (naive timestamps are MALFORMED, same rule as 018). The session HH:MM clock
   (`now_et`) stays inside payloads as a fact — the envelope never interprets it.
2. **Persistence:** append-only JSONL, one event per line, `flush + fsync` per
   append (`JsonlEventLog`). Recovery: a torn FINAL line is detected and raises
   `TornTailError` by default; `load(tolerate_torn_tail=True)` is the explicit
   operator choice to drop it. Any other malformed line always raises — a
   corrupt middle is not recoverable by guessing.
3. **Event identity / broker retries:** `event_id` is caller-supplied and
   unique per trade. The reducer treats a duplicate `sequence` with an
   IDENTICAL payload fingerprint (sha256 of canonical JSON) as an idempotent
   no-op — the classic retry — and a duplicate sequence with a DIFFERENT
   payload as corruption (raise). Gaps raise. This is also where C005 becomes
   effective: `PARTIAL_FILL_CONFIRMED` payloads carry the idempotency key, and
   `rebuild()` reconstructs `applied_reduction_keys`, so a restarted process
   reloads keys AND state together — closing the mechanical-only residual from
   the Gate-0 wiring card.
4. **Redaction:** payloads reference evidence by id only. No prompt text, no
   article bodies: any payload string value over 512 chars is refused at
   construction. Enforced, not advisory.
5. **Snapshots:** none in v1. One trade per day means full-history rebuild is
   microseconds; a snapshot is a second source of truth we do not need yet.
   Revisit only if rebuild cost is ever measured to matter.

### Reducer invariants (each a named test + mutation row)

- first event must be `POSITION_OPENED`; a second one raises
- `POSITION_CLOSED` is terminal — any later event raises
- sequences are 0-based, contiguous; gap or regression raises
- duplicate sequence: identical fingerprint = no-op, different = raise
- unknown `event_type` / `schema_version` raises
- `STOP_ADVANCED` may never loosen the stop (reducer-side C003 mirror)
- `HWM_UPDATED` may never regress against the trade direction
- `PARTIAL_FILL_CONFIRMED` requires a prior `PARTIAL_ORDER_SUBMITTED` and
  replays of the same idempotency key do not double-reduce
- `rebuild(events)` is pure: same list, same `TradeMemory`, no side effects

### Code surface

`daytrade/trade_events.py`: `EVENT_TYPES`, `TradeEvent` (validating, frozen,
fingerprinted), `append(events, event)` (pure), `rebuild(events) ->
TradeMemory`, `to_trade_state(memory) -> TradeState` (adapter — TradeState
stays the ONLY decision input; no second exit engine), `events_from_decision`
(the ONE translator from engine actions to events), `JsonlEventLog`.

### DoD

- Golden round-trip: run a session live (decide_exit/apply_actions) capturing
  events; destroy all process state; `rebuild` + `to_trade_state`; run the next
  bar — actions AND state (sl/qty/stage/revision/keys) identical to the
  uninterrupted run.
- Replaying the same log twice yields the same state and no duplicate effects.
- Every reducer invariant above: fault applied → named test red → revert →
  green (`mutation_check_012.py` → `specs/012_MUTATION_LOG.md`).
- Torn-tail and corrupt-line behavior covered by tests.

### Wiring-card obligation (adversarial review, 2026-08-09 — finding 10)

The golden test covers a crash at a bar boundary AFTER the event append. The
apply→append window (crash after apply_actions, before events_from_decision
persists) is the real risk once a live producer exists, and the mitigation is
ordering: the wiring card must append-and-fsync BEFORE apply, or emit both in
one transactional step, and must add prefix-crash tests at every cycle index.
Also: JsonlEventLog fsyncs the file, not the directory — the first append of a
brand-new log can vanish on power loss; the wiring card should fsync the parent
directory on file creation.

### Gate-2 wiring: known limitations (adversarial review, 2026-08-10 — LOWs, recorded not dropped)

- **Same-day trade identity:** after a CLOSED log rotates aside, a second
  same-day trade reuses `trade_id = f"{symbol}-{date}"` and restarts event ids
  at `…-0`. Audit joins across the two same-day logs need the filename (the
  rotated log carries `.closed-N`); a future card should widen trade identity
  (e.g. an open-timestamp component) before multi-trade days are routine.
- **No cross-midnight resume:** the events filename embeds the session date, so
  a trade cannot be resumed after midnight. Deliberate — consistent with the
  same-day session-clock doctrine (C004 refuses midnight crossings by design).
- **Unreconciled-submission recovery is manual:** resume refuses when a
  PARTIAL_ORDER_SUBMITTED has no CONFIRMED (the crash landed between the two
  appends). Procedure until card 013 wires reconciliation: check the broker's
  fills page by hand; if the order filled, append the PARTIAL_FILL_CONFIRMED
  event with the recorded key and the real fill price; if it never reached the
  broker (paper/advice mode: it never does), delete the trailing SUBMITTED
  line and resume. Either edit is a deliberate operator act on the log, not
  something the runner will ever guess at.
