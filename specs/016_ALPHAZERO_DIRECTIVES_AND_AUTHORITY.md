# 016 — ALPHAZERO DIRECTIVES, ABSTENTION, AND AUTHORITY `[PLAN]`

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

