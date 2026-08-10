# Claude handoff — long-term Stockfish/AlphaZero scaffold

This repository contains planning scaffolds, not permission to implement the
whole vision in one pass. Read `specs/README.md`, then
`specs/000_RULINGS_AND_ORDER.md`, then `specs/010_LONG_TERM_VISION.md`.

## Mission

Turn one planning card at a time from `[PLAN]` into a reviewed `[SPEC]`, then
implement only that card in a separate commit. Preserve the boundary:

> AlphaZero communicates meaning. Stockfish controls mechanics.

## Roles

Four seats, three of them AI. No single seat authors a spec, writes its
tests, implements it, and then declares it done — that is how an agent
convinces itself its own interpretation was correct.

- **Colin — product owner / final authority.** Decides what ships, what
  trades live, what gets cut.
- **Lead architect.** Owns sequencing, invariants, interfaces, and Definition
  of Done for each card, and is the one who says whether a phase clears its
  gate. Writes the `[SPEC]`, not the implementation.
- **Claude Code — engineering team.** Writes tests, implementation,
  migrations, and integration harnesses inside isolated worktrees, from an
  approved `[SPEC]`. Does not approve its own DoD.
- **Claude Cowork (this seat) — program manager / auditor.** Keeps the
  vision/spec/card matrix in `specs/README.md` current, reviews evidence from
  PRs/CI against what a card actually claims, finds inconsistencies between
  spec and implementation, prepares the next card for the architect and
  engineering seats, and tracks each component's position in the maturity
  sequence below. Writes cards and audits; does not write the implementation
  it will later audit.

## Maturity sequence

`[IMPLEMENTED] → [UNIT VERIFIED] → [INTEGRATION VERIFIED] → [WIRED] →
[EXERCISED]`. This replaces the flat `[BUILT]` label everywhere it's still
used in `specs/README.md`'s status legend — `[BUILT]` remains as the label
for a module that exists at all, before it's earned any of the five stages.

- **IMPLEMENTED** — the module exists, runs, has a `__main__` demo. No claim
  about correctness.
- **UNIT VERIFIED** — every invariant the module's own docstring/spec states
  has a named automated test, and a deliberate violation of *each* invariant
  makes the suite fail. (This is the VERIFIED definition already checked into
  `CLAUDE.md`, split here into its unit half.) Proven by fault injection, not
  by the test suite existing.
- **INTEGRATION VERIFIED** — the module is exercised through the real call
  path it will eventually live on (not a standalone script), and any
  cross-module guarantee it depends on or provides (e.g. a stop staying
  monotonic across an oscillating upstream signal) has its own named test on
  that live path, also proven by fault injection.
- **WIRED** — a live caller imports and calls the module as part of the
  normal runner/backtest flow. Importing it in a test file does not count.
- **EXERCISED** — there is evidence from an actual run (paper, shadow, or
  live session log) that the wired path executed, not just that it compiled
  and could.

A component's status line in `specs/README.md` should show all five,
explicitly, e.g. `IMPLEMENTED, UNIT VERIFIED, not yet WIRED` — not just the
highest one reached, since the gap itself is the information a reviewer
needs.

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


## Model per section of the build

Not every job on this scaffold carries the same risk if it goes wrong. Design
and boundary decisions get it wrong once and every card built after inherits
the mistake; mechanical work gets it wrong once and CI catches it. Route each
job to the model that matches that risk, not to whichever is cheaper or
faster.

| job type | model |
|---|---|
| Turn a `[PLAN]`/vision section into a reviewed `[SPEC]` (interface, state transitions, error cases, persistence format, migration plan) | Opus 4.8 |
| Cross-module architecture (how a new card's output reaches another engine without moving the Stockfish/AlphaZero boundary) | Opus 4.8 |
| State-machine semantics (stage transitions, terminal states, precedence rules) | Opus 4.8 |
| Event-sourcing / replay design (log schema, provenance, byte-identical replay guarantees) | Opus 4.8 |
| Execution / partial-fill correctness | Opus 4.8 |
| Adversarial final PR review before a card is marked `[BUILT]` | Opus 4.8 |
| Large "what assumption have we missed?" audit | Opus 4.8 |
| Ordinary Python implementation from an approved `[SPEC]` | Sonnet 5 |
| Unit tests from an approved contract (including invariant tests) | Sonnet 5 |
| Refactors with an obvious, mechanical result | Sonnet 5 |
| Docs / README / card status updates | Sonnet 5 |
| Fixing deterministic CI failures | Sonnet 5 |
| Integration tests (a new module wired into a live call path) | Sonnet 5, then Opus 4.8 review |

Mapped onto the four seats above: everything in the Opus 4.8 rows is
**architect** work — it produces or approves a `[SPEC]`, a DoD, or a gate
decision, never code. Everything in the Sonnet 5 rows is **Claude Code**
work — it runs inside isolated worktrees against an approved `[SPEC]` it did
not write. Reviewing the resulting evidence against the card, updating the
matrix, and flagging spec/implementation drift is **Claude Cowork** —
distinct from both, and not a row in the table above because it's a standing
job, not a per-card task.

### Applied to the starting sequence

1. **011 + 018** — spec review and constitution/directive invariant layer:
   done. Any follow-up wiring fix (e.g. threading `now_et=`/`actions=` into
   the live `enforce()` call so C004/C005/C007 actually fire) is ordinary
   implementation once the fix is agreed → **Sonnet**, with the "does this
   change authority or historical log compatibility" question that gates it
   → **Opus**.
2. **AZ invariant-test card** (`scenarios.py`, `thesis.py`, `regime_vector.py`
   — unit tests from the invariant list already agreed on, parquet-free,
   each proven to fail under fault injection before delivery) → **Sonnet**.
3. **AZ wiring card** (making the thesis-oscillation / stop-monotonicity
   guarantee and the `regime_vector.available()` unavailable-vs-zero gap
   explicit contracts, not accidents of `stockfish_exit.py`'s max-over-layers
   logic) — the cross-module design and the "does this change authority"
   call → **Opus**; the resulting implementation → **Sonnet**; the
   integration tests proving the guarantee holds under a live call path →
   **Sonnet, then Opus review**.
4. **012** (event-sourced memory, replay fixtures before live persistence) —
   schema/design → **Opus**; implementation and unit tests → **Sonnet**;
   replay/parity diff and integration tests → **Sonnet, then Opus review**.
5. **015 + 016** (evidence/directives + authority, `news_claude.py` stays a
   meaning producer, never an order producer) — the authority-boundary
   design is a state-machine/cross-module call → **Opus**; implementation →
   **Sonnet**.
6. **013** (paper/shadow broker fixtures, partial-fill correctness) →
   **Opus** for the correctness design, **Sonnet** for implementation and
   fixtures.
7. **014** (shadow/regret/portfolio, proving shadow outputs cannot reach the
   broker) — proving a negative like this is a cross-module architecture
   question → **Opus**; the fixtures and tests that encode the proof →
   **Sonnet**.
8. **017** (forecast/promotion, no promotion on a single aggregate return) —
   the promotion rule itself is a "what have we missed" audit against
   `SANITY_AUDIT.md` → **Opus**; the split/baseline/ceiling plumbing →
   **Sonnet**.

### Applied to "for every card" (the per-card checklist above)

- Concrete interface, state transitions, error cases, persistence format,
  migration plan, DoD → **Opus**.
- Deterministic unit/property/replay tests once that design is approved →
  **Sonnet**.
- Running the existing replay/parity tests and producing the diff →
  **Sonnet** (mechanical, CI-shaped).
- "Stop and report if a design choice changes authority, broker behavior, or
  historical log compatibility" → this judgment call is why the design step
  above is Opus, not Sonnet — it is the same check, made before the card is
  written rather than after.
