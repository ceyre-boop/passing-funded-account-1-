# 033 mutation log

2026-08-20. Harness asserts each named test is green unmutated before
injecting. Suite: 412 passed, 1 skipped.

| # | fault injected | invariant | killed by | result |
|---|---|---|---|---|
| M46 | engine-drift check removed from `verify` | I47 | test_engine_drift_is_detected | KILLED |
| M47 | policy-drift check removed | I47 | test_policy_drift_is_detected | KILLED |
| M48 | re-pin silently overwrites the checkpoint | I49 | test_repinning_is_refused | KILLED |
| M49 | `policy()` hands out a drifted target | I48 | test_engine_drift_is_detected | KILLED |
| M50 | self-exclusion assertion downgraded to silence | I51 | test_a_no_op_filter_is_refused | KILLED |
| M51 | `assert_disjoint` stops checking overlap | I52 | test_assert_disjoint_catches_reading_own_output | KILLED |

Note on M50: this is the mutation that reproduces SF-1 exactly — a filter
that runs, removes nothing, and reports success. It is now a test failure.
