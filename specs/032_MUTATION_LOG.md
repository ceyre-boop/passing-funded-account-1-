# 032 mutation log — the FX state vector

2026-08-20. Harness asserts each named test is green unmutated before
injecting. Suite: 400 passed, 1 skipped.

| # | fault injected | invariant | killed by | result |
|---|---|---|---|---|
| M39 | rate lookup reads observations after the session date | I41 | test_rate_at_never_reads_the_future | KILLED |
| M40 | rate staleness silently reported as 0 | I42 | test_i42_stale_rate_leg_is_flagged_not_forward_filled | KILLED |
| M41 | missing rate leg defaults to 0.0 instead of null | I43 | test_i43_missing_rate_is_null_not_zero | KILLED |
| M42 | swap hardcoded instead of read from the firm contract | I44 | test_i44_swap_comes_from_the_contract_and_refuses_without_it | KILLED |
| M43 | weekend charged one day of swap instead of three | I44 | test_weekend_cost_uses_three_days_of_swap | KILLED |
| M44 | build dedupe removed | idempotence | test_build_is_idempotent | KILLED |
| M45 | point-in-time bar filter removed | I41 | test_i41_row_contains_nothing_newer_than_its_session | KILLED |

## First population — the machine is now reading the market it trades

```
10,188 FX state rows · 2,547 per pair · 2016-11-04 .. 2026-08-20
rows with a usable rate_diff: 10,188 / 10,188
rate staleness: median 0 days, max 80  (JPY/AUD monthly series, flagged per row)
weekend-crossing sessions: 2,068
regime labels defined: 0
```

That last line is the point. The equity ontology asserted nine labels and the
audit found six were decoration and two were the same label twice. The FX
vocabulary is empty and stays empty until a label passes `ontology_audit` on
FX outcomes. Nothing is named until it earns the name.

Also folded in: `daytrade/chain.py` extracts the hash-chained append-only log
that `decision_ledger` had grown, so both ledgers use ONE implementation
(rule 1). The refactor is behaviour-identical — decision_ledger's 12 tests
pass unchanged and its live 4,496-row chain still verifies.
