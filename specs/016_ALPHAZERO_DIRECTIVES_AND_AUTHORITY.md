# 016 — ALPHAZERO DIRECTIVES, ABSTENTION, AND AUTHORITY `[SPEC]` (promoted 2026-08-09, ratification pending)

Coverage: AlphaZero items 6–9. Depends on 015 and pairs with 018.

## Intended contract

Define a versioned `ContextDirective` containing scope, regime summary, thesis
state, recommendation, interrupt, confidence, evidence ids, issued/expiry
timestamps, model version, authority level, and abstention reason. Valid
abstentions include `NO_OPINION`, `LOW_CONFIDENCE`, `CONFLICTING_EVIDENCE`,
`STALE_CONTEXT`, and `DATA_INCOMPLETE`.

Authority is earned and configured per model version: observe, annotate,
recommend, interrupt, entry authority. The first implementation should permit
only observe/annotate/recommend for unpromoted models. Every directive must be
scope-checked and expire automatically; a stale directive cannot steer a live
position.

## Planning decisions

- exact allowed interrupt vocabulary and precedence with Stockfish urgency;
- authority registry, promotion/rollback, and operator override;
- nested context merge rules across market, sector, symbol, and trade;
- whether a recommendation can persist independently of an interrupt;
- audit record for accepted, rejected, expired, and superseded directives.

## DoD seed

Test that an NVDA directive cannot affect an unrelated symbol, a SPY event is
not automatically a symbol emergency, expiry is enforced at use time, and a
Level-2 model cannot issue a Level-3 interrupt.


---

## `[SPEC]` promotion — 2026-08-09, on Colin's direct instruction. Architect
## ratification post-hoc.

### Planning decisions, answered

1. **Interrupt vocabulary / precedence:** unchanged from 018 (TIGHTEN <
   REDUCE_RISK < INVALIDATE < EMERGENCY; MIN_AUTHORITY per interrupt).
   Stockfish-side arbitration is `resolve_channels` (most protective urgency
   wins; a policy candidate may only tighten).
2. **Authority registry:** `AuthorityRegistry` in `context_directive.py`.
   Append-only audit of grants/rollbacks. UNPROMOTED models are CAPPED at
   level 2 (recommend) — a grant above 2 requires `promotion_ref` (a 017
   promotion event id). Rollback is explicit and falls back to the default
   level. `granted_level(model)` is derived from the audit trail, never cached.
3. **Nested context merge:** deferred to the evidence aggregation in 015
   (trade > symbol > sector > market); directives themselves stay flat.
4. **Recommendation vs interrupt:** independent — and in the LIVE runner an
   unpromoted model's recommendation is LOGGED, never in force (observe/
   annotate/recommend). Only interrupts within granted authority reach the
   urgency channel.
5. **Audit record:** `evaluate()`'s Decision already records accepted/rejected
   with reasons (018); the registry adds the authority audit.

### Production wiring (what makes 018 WIRED)

The runner reads `data/daytrade/directives.json` each cycle (absent file =
no directives = byte-identical behavior to before this card). Directives go
through `evaluate()` with the receiver at DEFAULT_GRANTED_LEVEL (1: tighten
only — unpromoted). The resulting urgency merges with the bias channel through
`resolve_channels` (ONE arbitration implementation). Malformed files print a
loud note and refuse steering, mirroring the bias channel's MISSING/UNREADABLE
convention — a corrupt advisory input must not kill the cockpit NOR steer it.

### DoD

An NVDA directive cannot affect an unrelated symbol; a SPY market event is not
a symbol emergency for a non-index-linked receiver; expiry enforced at use
time; a level-2 model cannot issue a level-3 interrupt; unpromoted grants above
2 refused without promotion_ref; runner byte-parity with no directives file;
runner tighten with a valid TIGHTEN directive. Fault rows in
`mutation_check_015_016.py`.
