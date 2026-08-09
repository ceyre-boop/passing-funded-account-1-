# 020 — AZ wiring: one AlphaZero → Stockfish spine

`[SPEC — promoted 2026-08-09. Build only after Gate 0 certification.]`

Promoted by the architect seat after review of card 019, the wiring mutation
evidence, and the 41-test suite. It exists so the two items 019 explicitly
deferred (019:125-132) and the handoff's "AZ wiring card" have a written home,
and so the gate sequence is recorded once instead of re-derived.

## Architect rulings at promotion

- **C004 midnight wraparound: refuse by design.** `_et_minutes` is a
  same-session clock, not a date-aware timestamp. A clock transition from
  `23:59` to `00:00` is a session-boundary violation and must raise C004; no
  overnight position is in scope for this day-trade engine. Gate 1 tests must
  pin the refusal and its explicit error message.
- **`compute()` silent zeros: harden in this card.** The five fallbacks in
  `regime_vector.compute()` (ATR/price, volatility expansion, VWAP stretch,
  zero-denominator autocorrelation, and index-correlation `StatisticsError`)
  must emit `unavailable` with a reason, or raise when input is malformed; they
  may not emit `0.0` with source `computed`. A downstream `require()` call
  cannot repair a fabricated zero. Add deterministic compute fixtures and
  mutation rows for these cases to the Gate 1 evidence.
- **Caller-side wiring: required in 020.** Add named tests for runner and
  ceiling caller threading, using small testable loop helpers or synthetic bar
  fixtures. The existing backtest test remains required. These are part of the
  wiring spine, not an accepted residual; missing coverage blocks Gate 1
  integration certification.

## Why this card exists

After 019, `scenarios.py`, `thesis.py` and `regime_vector.py` are UNIT
VERIFIED, and after the constitution-wiring fix all nine C-rules fire on the
live path. But the AlphaZero side is still five good modules, not a system:
`context_directive.py` has zero callers, no test exercises regime + scenarios +
thesis + directive + engine + constitution as ONE route, and the cross-module
guarantees hold only as accidents. This card builds the spine and makes those
guarantees named, fault-injected contracts — INTEGRATION VERIFIED territory.

## Scope

One synthetic end-to-end route (a test harness path, not a production caller —
016 owns the production directive caller; this card must not front-run it):

    regime vector + scenario set + thesis
      → ContextDirective (freshness / scope / authority via context_directive.evaluate)
      → policy_from_scenarios / urgency_for_thesis
      → decide_exit → constitution → deterministic decision

New file: `daytrade/test_az_spine.py` (name final at promotion). No production
module changes except the call-site switches named below.

## The three integration invariants (each a named test + fault injection)

1. **Unavailable regime data never becomes a number on a decision path.**
   The call-site switch from `available()` to `require()` deferred by 019:130,
   applied wherever a regime value feeds a decision (none exist yet — the first
   consumer lands here and starts correct). Fault: switch back to `available()`
   with a default.
2. **Stale state cannot influence the decision.** A stale `ScenarioSet` and an
   expired/naive-timestamped `ContextDirective` fed through the spine leave the
   decision identical to their absence. Fault: flip the freshness comparator at
   either point of use.
3. **Thesis oscillation cannot mechanically loosen protection.** Across
   WEAKENING → CONFIRMED → WEAKENING cycles, the effective stop is monotone
   (never retreats) — today true only as an accident of `stockfish_exit`'s
   max-over-layers logic (019:127-129); this makes it a contract. Fault: make
   the tighten channel symmetric (allow re-widening on recovery).

## Deferred backlog this card inherits (noted, not necessarily in scope)

- **C005 is mechanical-only until persistence exists** (adversarial review,
  2026-08-09): the runner's key set and state both die with the process, so the
  crash-retry scenario the rule exists for is undefended until card 012
  persists keys+state together. The threading is in place precisely so 012 can
  make it effective. Comments in runner.py/backtest.py say this explicitly.
- Caller-side runner and ceiling tests are in scope above; the pre-promotion
  gap is closed only when those tests and their mutation rows land.
- **C004 midnight wraparound** (adversarial review, LOW): `_et_minutes`-based
  clock comparison has no day component; a live runner held past midnight with
  `flatten_at_et` unset would raise C004 on the 00:00 sample. Architect call:
  refuse-by-design (a day-trade cockpit has no business open at midnight — make
  the error message say so) or add a day-aware clock.

- The five `compute()` silent-zero fallbacks are in scope above; 019 remains
  correctly parquet-free and is not retroactively expanded.
- 018 signing deferral (018:110-114): still fine while in-process; must be
  revisited before any directive crosses a process boundary.
- `regime_vector.compute()` test coverage generally (needs pyarrow fixtures).

## Gate map (recorded once — the sequence Colin set, in card terms)

| Gate | What | Card(s) | Notes |
|---|---|---|---|
| 0 | Verifier before vision | 019 + constitution wiring | 019 certified 2026-08-09; wiring 5/5 mutations verified, with runner/ceiling caller tests carried into 020 |
| 1 | One AZ→Stockfish spine | **this card (020)** | integration invariants above |
| 2 | Event-sourced memory | 012 | golden histories; destroy-state → reconstruct → identical decision |
| 3 | Execution + partial fills | 013 | treat vision Stockfish items 6+7 as one vertical program |
| 4 | Shadow tournament + regret | 014 (shadow/regret half) | needs Gate 2's replay first |
| 5 | AZ middle layer | 015 + 016 | evidence objects, abstention, authority — eventful concepts, after Gate 2 |
| 6 | Forecast ledger + promotion | 017 | promotion is code, not judgment |
| 7 | Portfolio protection | 014 (guards half) | last; the no-live-credentials boundary already holds (CLAUDE.md dev rule 9, `broker.py` paper-host refusal) |

Per-card pipeline for every gate: SPEC → RED → IMPLEMENT → MUTATE → INTEGRATE →
ADVERSARIAL REVIEW → HUMAN VALIDATION → MERGE. The mutation loop is the
VERIFIED bar; pytest-green alone is not evidence.

## DoD

- The spine test runs the full route on synthetic fixtures, deterministic, no
  network, no parquet.
- Each of the three invariants: named test, fault applied → red → reverted →
  green, logged in a mutation log like 019's.
- `regime_vector.compute()` has no silent-zero fallback on the five named paths;
  each behavior is covered by a named test and mutation row.
- Runner, backtest, and ceiling caller threading each have a named test and
  mutation row.
- The C004 `23:59 → 00:00` session-boundary refusal has a named test.
- `context_directive.evaluate` gains its first real caller on a test path;
  production wiring remains 016's.
- Engine replay stays byte-identical; ceiling R values unchanged.
