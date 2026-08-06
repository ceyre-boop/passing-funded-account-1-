# Claude handoff — long-term Stockfish/AlphaZero scaffold

This repository contains planning scaffolds, not permission to implement the
whole vision in one pass. Read `specs/README.md`, then
`specs/000_RULINGS_AND_ORDER.md`, then `specs/010_LONG_TERM_VISION.md`.

## Mission

Turn one planning card at a time from `[PLAN]` into a reviewed `[SPEC]`, then
implement only that card in a separate commit. Preserve the boundary:

> AlphaZero communicates meaning. Stockfish controls mechanics.

## Starting sequence

1. Write and review 011 + 018 together. Do not add new order behavior.
2. Write and review 012. Build replay fixtures before any live persistence.
3. Write and review 015 + 016. Keep `news_claude.py` as a producer of meaning,
   never an order producer.
4. Write and review 013. Use paper/shadow broker fixtures only.
5. Write and review 014. Prove shadow outputs cannot reach the broker.
6. Write and review 017. Use existing split/baseline/ceiling rules; do not
   promote models based on a single aggregate return.

## For every card

- Inspect the current modules and replay schema before editing.
- Add the concrete interface, state transitions, error cases, persistence
  format, migration plan, fixtures, DoD, and test command to the card.
- Keep decision logic in the owning engine; runner/broker/harness only perform
  I/O and orchestration.
- Make stale, unknown, malformed, duplicate, and conflicting data explicit.
- Add deterministic unit/property/replay tests before wiring callers.
- Run the existing replay/parity tests and show the diff. Do not rewrite the
  v3 baseline casually.
- Use TUNE only for tuning. Seal OOS once, after the version is frozen. Record
  `rule_version`, `model_version`, `prompt_version`, and schema versions.
- Stop and report if a design choice changes authority, broker behavior, or
  historical log compatibility; do not guess.

## Commit shape

Use separate commits for: planning promotion, pure data types/reducer, tests,
integration, and any migration. Keep live execution disabled until paper/shadow
acceptance is demonstrated. Every commit message names the card, for example:
`011: add constitution validation types`.

## Definition of done for the whole scaffold

The vision is not “done” when all modules exist. It is done only when each
component has a versioned contract, deterministic replay, failure tests,
baseline/OOS scorecard, and an explicit authority level. Unimplemented ideas
must remain visibly marked `[PLAN]` or `[SKETCH]`.

