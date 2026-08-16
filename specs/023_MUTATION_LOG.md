# 023 mutation log — alpha_operator.py + four_books.py

2026-08-15. Method: deliberately break the invariant in source, run the named
test, require failure, restore. One row per fault. Suite before and after:
248/248.

| # | fault injected | invariant | killed by | result |
|---|---|---|---|---|
| M1 | directive authority_level 1 → 3 | I1 | test_i1_runner_side_default_context_accepts_the_emitted_directive | KILLED |
| M2 | EXIT suppression removed (`suppressed = None`) | I9 | test_i9_exit_sealed_verbatim_directive_capped_with_suppression_noted | KILLED |
| M3 | directive written to disk BEFORE the sealed record | I3 | test_i3_sealed_record_exists_before_directive_is_observable | KILLED |
| M4 | corrupt JSONL line silently skipped on load | I10 | test_i10_corrupt_persistence_line_raises_on_load | KILLED |
| M5 | resolver early-horizon guard removed | I4 | test_i4_resolution_before_horizon_is_refused_forecast_stays_open (017 ledger raises) | KILLED |
| M6 | scenario-prob renorm tolerance unbounded | prob honesty | test_probs_outside_tolerance_refused | KILLED |
| M7 | record expiry check dropped in `active_records` | veto correctness | test_veto_ignores_expired_record | KILLED |
| M8 | in-position urgency escalated to "exit" for EXIT records | unpromoted cap | test_mid_trade_record_tightens_but_never_exits (engine-side spy) | KILLED |
| M9 | one book fed a truncated bar frame | I8 | test_i8_all_books_consume_identical_bar_data | **SURVIVED first run** → harness fixed → KILLED |
| M10 | `"quantity": 100` smuggled into the directives.json payload | I2 / 018 envelope | test_i2_directive_round_trips_unchanged (from_dict refuses) | KILLED |

## The M9 finding (why this log exists)

First run of M9 survived: `run_session` computed the bars fingerprint ONCE and
stamped it onto all four book results, so the "books consumed identical data"
assertion compared a value with itself — decorative, exactly the class of test
the DoD exists to catch. Fix: each `run_book` now fingerprints the frame it was
actually handed; `run_session` compares the four independently-computed hashes.
Re-run: KILLED.

## Not mutation-testable tonight (stated, not hidden)

- I5 (no trigger → zero API calls) is covered by a direct test with a spy on
  the priced call; a mutation of `check_triggers` itself is equivalent to the
  test's own stub, so a fault row would be circular.
- The live Claude call path (`news_claude._call`) is 009's machinery, already
  under its own log; these tests stub it by design.
