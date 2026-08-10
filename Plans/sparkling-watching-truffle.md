# Plan — Gate 0: card 019 invariant suites + constitution wiring fix + draft spec 020

## Context

The gated-development proposal Colin brought in (Gate 0 → Gate 7) says: before any card 012
work, clear the verification debt — the invariant suites, `require()`, the freshness boundary,
and making constitution rules C004/C005/C007 reachable — then build one end-to-end
AlphaZero→Stockfish spine. Exploration confirms the repo already encodes most of this:

- **Card 019 IS Gate 0** (`specs/019_AZ_INVARIANT_TESTS.md`, `[SPEC]`, "next — gate before 012").
  It names ~31 tests across three files that do not exist yet. The repo has **zero pytest
  tests** anywhere — all verification is `__main__` self-tests, which is exactly the debt
  (deleting a `raise` from `scenarios.py` still exits 0).
- **Two items Gate 0 as pasted folds in are explicitly deferred by 019** (019:125-132,
  "Do not fold them in"): thesis-oscillation-vs-stop-monotonicity in composition, and switching
  call sites from `available()` to `require()`. Those belong to the AZ wiring card — which does
  not exist yet as a `[SPEC]`. That card is Gate 1's spine; we draft it here as **spec 020**,
  draft only.
- **C004/C005/C007 never fire in production** (`CLAUDE_LONG_TERM_HANDOFF.md:143-148`): the sole
  live call `enforce(state, action, applied_keys=applied_keys)` at
  `daytrade/stockfish_exit.py:530` passes neither `now_et=` nor `actions=`, and the three
  production `apply_action` callers (`runner.py:381`, `backtest.py:92`, `ceiling.py:168`) pass
  no `applied_keys`. The handoff already routes this as an ordinary implementation fix.
- **Spec/code discrepancy to adjudicate in-card**: 019:93 claims `available()` collapses
  unavailable into `0.0` today; current `regime_vector.py:96-97` filters unavailable dims out.
  Colin's ruling: verify actual behavior first, then either land the minimal fix in-card or
  lock current correct behavior and note the spec correction in the PR.

Scope per Colin: **(1) implement 019, (2) the C004/C005/C007 wiring fix, (3) draft spec 020
for the AZ wiring card — draft only, no Gate 1 code.** No card 012+ work. Development rules
in project CLAUDE.md apply (fail loud, no silent zero defaults, tests are the DoD via fault
injection, don't touch sealed data).

## Execution discipline — the per-card pipeline (added per Colin's follow-up)

Gate 0 runs through **SPEC → RED → IMPLEMENT → MUTATE → INTEGRATE → ADVERSARIAL
REVIEW → HUMAN VALIDATION → MERGE**, mapped to this environment:

- **SPEC** — already done: 019 is architect-authored, `[SPEC]`, test names and fault
  columns contractual. No re-planning of it here.
- **RED** — write the three test files first and run them against unmodified modules.
  Rows that need module changes (`require()` rows, explicit-NaN row, the NaN/inf
  duration gap found in `scenarios.py:85`) must be demonstrated red BEFORE the module
  edits land. Capture the red run output.
- **IMPLEMENT** — the minimal module changes (`require()`, named `isnan` guard,
  `isfinite` duration guard), never edits to the approved tests to make them pass.
- **MUTATE** — the 019 fault-column loop, every row: apply fault → red → revert →
  green. Logged in `MUTATION_LOG.md`. This is the VERIFIED bar, not pytest-green.
- **INTEGRATE** — existing self-tests + backtest replay diff stay green (019 is
  unit-only; real integration invariants are spec 020's job).
- **ADVERSARIAL REVIEW** — a fresh-context review agent gets the 019 spec text + final
  diff + test output (not this session's history) and is asked how the implementation
  could satisfy the tests while violating intent. Findings fixed or surfaced.
- **HUMAN VALIDATION / MERGE** — Colin. I do not certify the gate (handoff roles);
  the deliverable ends at "evidence assembled, commits staged, Colin reviews."

Same pipeline applies to the C004/C005/C007 wiring fix (its RED = a live-path test
that fails before the threading lands).

## Work items

### 0. Gate roadmap recorded durably (no code)
Colin's Gate 2–7 sequence maps cleanly onto the existing cards; write it down once so
it stops being re-derived, as a short "Gate map" section in the spec 020 draft (and a
one-line pointer in `specs/README.md`): Gate 2 = card 012 · Gate 3 = card 013 (treat
vision Stockfish items 6+7 as one vertical program even if the card stays one card) ·
Gate 4 = card 014 · Gate 5 = cards 015+016 · Gate 6 = card 017 · Gate 7 = portfolio
guards half of 014, last. Note where a gate's hard boundary already exists in code
(Gate 7's "no live-broker credentials in agent environments" = CLAUDE.md rule 9 +
`broker.py`'s paper-host refusal).

### 1. Pytest infrastructure (small, enabling)
- New `conftest.py` at repo root: insert `daytrade/` onto `sys.path` so flat sibling imports
  (`from scenarios import ...`) resolve under pytest collection. Nothing else — no pytest.ini
  opinions beyond what's needed to collect `daytrade/test_*.py`.
- Test command (from 019:122): `pytest daytrade/test_scenarios.py daytrade/test_thesis.py
  daytrade/test_regime_vector.py -v`.

### 2. Card 019 implementation (the bulk)
Build the three files exactly as the spec enumerates — names are contractual:

- **`daytrade/test_scenarios.py`** — 13 tests (019:52-68): probability-sum/negative/above-one/
  NaN-inf rejection, unknown/duplicate names, empty set, missing invalidation-or-evidence,
  **inclusive freshness boundary** (`is_fresh()` at exact boundary is True — `scenarios.py:154`
  uses `<=`; the red-fault is flipping to `<`), decisive → top scenario's policy via
  `policy_from_scenarios` (`stockfish_exit.py:343`), indecisive → most conservative policy
  **among represented scenarios only** via `CONSERVATISM`, never-average rule, `unreadable()`
  is an even three-way spread.
- **`daytrade/test_thesis.py`** — 8 tests (019:70-81): legal transitions only, idempotence,
  INVALIDATED/EXPIRED sticky, INVALIDATED outranks EXPIRED in one `evaluate()`,
  **weakening is NOT sticky** (oscillation WEAKENING→CONFIRMED locked as explicit contract —
  test the current probed behavior, not the "safer" one, per 019:79), unknown condition key
  raises, `urgency_for_thesis` (`stockfish_exit.py:386`) covers all 5 states × both armed
  booleans with no None-by-omission.
- **`daytrade/test_regime_vector.py`** — 10 tests (019:83-96): unknown dimension, unavailable-
  with-value, computed-without-value, out-of-range, **explicit NaN check** (add a named
  `math.isnan` guard in `Dimension.__post_init__` so NaN rejection survives a future
  range-check refactor — currently only caught as a range side effect), incomplete vector,
  `available()` returns source not just value (see adjudication below), and the three
  `require()` rows.
- **New accessor `require(vec, name)` in `daytrade/regime_vector.py`** (019:30-39): raises
  `RegimeError` on unavailable or missing, returns the plain value for `computed`/`judged`.
  Sits alongside `value()`/`available()`; **no existing call site switches** (deferred to 020).
- **019:93 adjudication (Colin's ruling: verify-then-fix-in-card)**: probe what `available()`
  actually returns. If it genuinely loses the source (test name implies it should return
  (name, value, source), not name→value), land the minimal accessor change in-card and write
  the test green against it. If the spec premise is stale and behavior is already correct,
  write the test to lock current behavior and record the spec correction in the PR summary.
- **Constraints** (019:41-43, 98-104): parquet-free; `compute()` out of scope (its five
  silent-zero fallbacks at `regime_vector.py:211,217,257,174,272` get NOTED in spec 020's
  backlog, not fixed here); fixtures are plain Python literals reusing `SPEC`, `ALL_SCENARIOS`,
  `CONSERVATISM` from the modules.

### 3. Mutation verification — the actual DoD (019:47-50, 106-117)
For **every** test row, apply the spec's fault-column mutation temporarily, confirm that row
goes red, revert, confirm green. This loop IS the DoD, not a step before it. Evidence:
a `MUTATION_LOG.md` (or PR-body table) listing row → mutation applied → red confirmed →
reverted → green confirmed. No mutation framework — manual edit/run/revert per the spec.

### 4. C004/C005/C007 wiring fix (separate commit)
- Thread `now_et=` and `actions=` through the live path: `apply_action`
  (`stockfish_exit.py:513`) accepts/forwards them into `enforce()`
  (`stockfish_constitution.py:203`); production callers supply what they have
  (`runner.py` has the clock; batch context where applicable).
- Ensure `applied_keys` idempotency state (C005) is actually threaded from the three
  production callers, not just the self-test.
- Verify with fault injection on the live path: a stale-clock / duplicate-reduction /
  emergency-precedence violation constructed through `apply_action` (not the module self-test)
  must raise. v3 replay must stay byte-identical (011 DoD); run `python3 daytrade/backtest.py`
  diff + `stockfish_exit.py`/`stockfish_constitution.py` self-tests.
- Per handoff routing: if any part of the fix would change authority semantics or historical
  log compatibility, stop and surface it instead of proceeding.

### 5. Draft spec 020 — AZ wiring card (Gate 1 spine) — DRAFT ONLY
New `specs/020_AZ_WIRING.md`, marked `[DRAFT — awaiting Colin/architect approval, do not
build]`. Contents drawn from what 019 explicitly deferred plus the handoff's spine:
- One synthetic end-to-end route: regime vector + scenario set + thesis → `ContextDirective`
  → freshness/authority checks (`context_directive.evaluate`, currently zero callers) →
  policy/urgency → `decide_exit` → constitution → deterministic decision.
- The three integration invariants as named tests on that live path: unavailable regime data
  never becomes a number (call-site switch `available()`→`require()`), stale scenario/directive
  state cannot influence the decision, thesis oscillation cannot mechanically loosen a stop
  (monotonicity currently true only as an accident of max-over-layers).
- Backlog notes: `compute()` silent-zero fallbacks; 018 signing deferral; relationship to
  card 016 (which owns the *production* directive caller — 020 wires the synthetic/test spine
  only, so it does not front-run 016).
- README.md specs table row added for 020 as `[DRAFT]`.

### 6. Status bookkeeping
Update `specs/README.md`: 019 → built status with the five-stage maturity line (per
handoff:61-64 the full line, not just the highest stage); 011/018 lines gain their five-stage
form (018: IMPLEMENTED, self-test-only, not WIRED — 016 wires it); note the C004/C005/C007
fix. Do NOT mark anything INTEGRATION VERIFIED — that's 020 territory, and per the roles
section the implementing seat doesn't certify its own gate.

## Verification
1. `pytest daytrade/test_scenarios.py daytrade/test_thesis.py daytrade/test_regime_vector.py -v`
   → all green (31 tests; the one adjudicated row green or spec-corrected, per ruling).
2. Mutation loop evidence complete: every row demonstrated red under its fault, green after
   revert (MUTATION_LOG.md).
3. Existing self-tests still exit 0: `scenarios.py`, `thesis.py`, `context_directive.py`,
   `stockfish_constitution.py`, `stockfish_exit.py`.
4. Wiring fix: constructed C004/C005/C007 violations through the live `apply_action` path
   raise; legal play doesn't; `backtest.py` replay diff byte-identical.
5. Spec 020 exists as `[DRAFT]`, no production code written from it.

## Explicitly out of scope
Card 012+ implementation · any `[PLAN]`/`[SKETCH]` build · call-site `available()`→`require()`
switches · `regime_vector.compute()` fixes · wiring `context_directive` into runner (016) ·
touching sealed evaluation data · broker/live paths beyond the enforce() threading.
