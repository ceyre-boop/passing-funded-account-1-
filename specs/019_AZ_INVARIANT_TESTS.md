# 019 — AZ invariant tests: scenarios.py, thesis.py, regime_vector.py

`[SPEC]` — written by the architect seat, safe to build from. Implementation
belongs to Claude Code, in an isolated worktree, against exactly this card.
The seat that writes this card does not also write or approve its own tests —
see `CLAUDE_LONG_TERM_HANDOFF.md`'s "Roles" section.

## Why this card exists

`scenarios.py`, `thesis.py`, and `regime_vector.py` are `[BUILT]` (they exist,
run, and every invariant below was confirmed to hold by hand-probing) but
`IMPLEMENTED` only — none has a real automated test. Each module's `__main__`
is a demo, not a suite: deleting a `raise` from `scenarios.py`'s validation
and re-running its self-test still exits 0. This card is the gate between
`IMPLEMENTED` and `UNIT VERIFIED` for all three, per the maturity sequence.

This card is unit-only. Cross-module guarantees — the thesis-oscillation vs.
stop-monotonicity interaction, and `regime_vector.available()`'s
unavailable-vs-zero collapse at the `stockfish_exit.py` call site — are
`INTEGRATION VERIFIED` territory and belong to the wiring card that follows
this one, not here. Do not fold them in.

## Scope

Three new files, no changes to the modules under test unless noted:
- `daytrade/test_scenarios.py`
- `daytrade/test_thesis.py`
- `daytrade/test_regime_vector.py`

One narrow interface change, in scope for this card because the tests can't
be written honestly without it:
- `regime_vector.py` gains a `require(vec, name)` accessor (raises
  `RegimeError` if `name` is unavailable or missing, returns the value
  otherwise) alongside the existing `value()`/`available()`. This does not
  replace `available()` — callers that legitimately want "0.0 if unavailable"
  still have it. It gives callers who need the number for a decision, not a
  display, a way to fail loud instead of silently treating "unavailable" as
  "computed zero." No existing call site is required to switch as part of
  this card.

Parquet-free: no fixture in this card may require `pyarrow`/`fastparquet`.
`regime_vector.compute()` (the bar-arithmetic path) is out of scope — these
tests exercise the dataclasses and accessors directly, not `compute()`.

## Invariants and their tests

Each row is one named test. "Fault" is the specific mutation that must make
the test fail — write the fault, confirm red, then confirm green on the real
module, before calling the test done. That loop is the DoD, not a step before
it.

### `scenarios.py`

| test | invariant | fault that must turn it red |
|---|---|---|
| `test_prob_must_sum_to_one` | `ScenarioSet.__post_init__` raises `ScenarioError` if probabilities don't sum to 1.0 (within tolerance) | comment out the sum check |
| `test_rejects_negative_prob` | a negative `prob` or `confidence` raises | remove the `>= 0` bound |
| `test_rejects_prob_above_one` | a `prob`/`confidence` `> 1.0` raises | remove the `<= 1` bound |
| `test_rejects_nan_and_inf` | `nan`/`inf` in `prob`, `confidence`, or `duration_min` raises | remove the range check these ride on |
| `test_rejects_unknown_scenario_name` | a name outside `ALL_SCENARIOS` raises | remove the membership check |
| `test_rejects_duplicate_scenario_names` | two scenarios with the same name in one set raises | remove the dedup check |
| `test_rejects_empty_set` | an empty `ScenarioSet` raises | remove the non-empty check |
| `test_rejects_missing_invalidation_or_evidence` | a scenario missing `invalidation` or `evidence` raises | remove that required-field check |
| `test_freshness_at_exact_boundary_is_fresh` | `is_fresh()` at exactly the boundary age returns `True`, not `False` (off-by-one on the comparator direction) | flip `<=` to `<` in `is_fresh()` |
| `test_decisive_picks_top_scenario_policy` | `is_decisive()` true → `policy_from_scenarios()` (in `stockfish_exit.py`) returns the top scenario's `recommended_policy` | swap `top` for the second-highest scenario in the lookup |
| `test_indecisive_picks_most_conservative_represented_policy` | `is_decisive()` false → `policy_from_scenarios()` returns the most conservative policy among only the scenarios actually present in the set, via `CONSERVATISM` | change the fallback to the least conservative, or to a policy not present in the set |
| `test_never_averages_into_unrepresented_policy` | the returned policy is always one of the set's `recommended_policy` values, never a blend or a policy absent from the set | replace the lookup with an averaged/interpolated value |
| `test_unreadable_is_flat_three_way` | `unreadable()` returns an evenly-spread `ScenarioSet` (the honest "I don't know"), not a skewed default | change one weight in `unreadable()` |

### `thesis.py`

| test | invariant | fault that must turn it red |
|---|---|---|
| `test_legal_transitions_only` | `evaluate()` only moves through PENDING→CONFIRMED/WEAKENING→INVALIDATED/EXPIRED per the documented precedence | remove one precedence branch |
| `test_idempotent_on_repeated_observation` | feeding the same `observed` twice in a row does not change state a second time | remove the no-op short-circuit (if present) or assert on a mutation that shouldn't happen |
| `test_invalidated_is_sticky` | once `THESIS_INVALIDATED`, no later observation (including a perfect confirmation) moves it out | remove `TERMINAL` gating in `evaluate()` |
| `test_expired_is_sticky` | once `THESIS_EXPIRED`, no later observation moves it out | same as above, for EXPIRED |
| `test_invalidation_outranks_expiry_when_both_fire` | if both invalidation and expiry conditions are true in the same `evaluate()` call, INVALIDATED wins | swap the precedence order |
| `test_weakening_is_not_sticky` | `THESIS_WEAKENING` reverts to `THESIS_CONFIRMED` when the weakening condition is no longer observed — this is documented, undocumented-until-now behavior; the test makes it an explicit contract instead of an accident | make WEAKENING sticky (this fault SHOULD make the test fail if the test correctly encodes today's behavior — write the test to match the current, confirmed-by-probing behavior, not the behavior that seems safer) |
| `test_unknown_observed_condition_key_raises` | an `observed` dict with a key `evaluate()` doesn't recognize raises, never silently ignored | remove the unknown-key check |
| `test_urgency_for_thesis_covers_all_states` | `urgency_for_thesis()` in `stockfish_exit.py` has a mapping for all 5 `ThesisState` values × both armed booleans, no `None`-by-omission | delete one state's branch and confirm the test catches the gap rather than silently returning `None` |

### `regime_vector.py`

| test | invariant | fault that must turn it red |
|---|---|---|
| `test_rejects_unknown_dimension` | a `Dimension` name outside `SPEC` raises `RegimeError` | remove the membership check |
| `test_rejects_unavailable_with_value` | `Source == "unavailable"` but `value is not None` raises | remove that check |
| `test_rejects_computed_without_value` | `Source == "computed"` but `value is None` raises | remove that check |
| `test_rejects_out_of_range` | a value outside `SPEC[name]`'s `(low, high)` raises | remove the range check |
| `test_rejects_nan_explicitly` | `nan` is rejected by an explicit `math.isnan` check, not by accident of `nan` comparisons being `False` — today's rejection is confirmed to work but only as a side effect of the range check; make it an explicit, named check so a future refactor of the range check can't silently stop catching `nan` | remove the explicit `isnan` check once added, confirm the test (not just the accidental side effect) is what catches it |
| `test_rejects_incomplete_vector` | a `RegimeVector` missing any `SPEC` key raises | remove the completeness check |
| `test_available_returns_source_not_just_value` | `available()`'s return value lets a caller distinguish "unavailable" from "computed zero" — this test should currently FAIL against the unmodified module, since `available()` today collapses both into `0.0`; either the test is written to lock in a fix landed in this card, or it's written `xfail` and this row becomes a known gap the card explicitly does not close (pick one, state which in the PR) | n/a — this is the one row where "red" may be the honest starting state |
| `test_require_raises_on_unavailable` | new `require(vec, name)` raises `RegimeError` if `name`'s source is `unavailable` | remove the check in `require()` |
| `test_require_raises_on_missing` | `require(vec, name)` raises if `name` isn't in the vector at all | remove that check |
| `test_require_returns_value_when_computed_or_judged` | `require(vec, name)` returns the plain value when source is `computed` or `judged` | break the happy path |

## Fixtures

Plain Python literals — construct `Scenario`/`ScenarioSet`, `Condition`/
`Thesis`, `Dimension`/`RegimeVector` instances directly in each test file. No
JSON fixtures, no parquet, no network. Reuse `SPEC`, `ALL_SCENARIOS`, and
`CONSERVATISM` from the modules under test rather than hardcoding copies that
can drift.

## DoD

- `pytest daytrade/test_scenarios.py daytrade/test_thesis.py
  daytrade/test_regime_vector.py -v` — all green against the unmodified
  modules (except the one row above that may start red, if that's the choice
  made).
- For every row in the three tables: the fault column's mutation, applied
  temporarily, turns that row's test red. This is the actual acceptance
  criterion — a green suite alone is not evidence.
- `regime_vector.py`'s `require()` addition does not change any existing
  function's signature or behavior; existing callers are unaffected.
- No new dependency on `pyarrow`/`fastparquet`.

## Test command

```
pytest daytrade/test_scenarios.py daytrade/test_thesis.py daytrade/test_regime_vector.py -v
```

## Not in scope (belongs to the next card — AZ wiring)

- Thesis-oscillation vs. stop-monotonicity as an explicit, tested contract
  (currently true only as an accident of `stockfish_exit.py`'s
  max-over-layers logic).
- Any call site actually switching from `available()` to `require()`.
- `regime_vector.compute()` (needs `pyarrow`; out of scope for parquet-free
  unit tests).
