# 034 mutation log — carry-exit-v1

2026-08-20. Harness asserts each named test is green unmutated before
injecting. Suite: 433 passed, 1 skipped.

| # | fault injected | invariant | killed by | result |
|---|---|---|---|---|
| M52 | weekends billed as one day, not three | I54 | test_financing_accrues_daily_and_weekends_bill_three | KILLED |
| M53 | `net_r` ignores financing | I54 | test_net_r_subtracts_financing_from_the_price_move | KILLED |
| M54 | a stale rate read may trigger a reversal | I55 | test_a_stale_rate_read_is_not_evidence_of_a_flip | KILLED |
| M55 | stop no longer outranks reversal | I56 | test_stop_outranks_everything | KILLED |
| M56 | `MOVE_SL` allowed to loosen the stop | I57 | test_move_sl_may_never_loosen_the_stop | KILLED |
| M57 | engine may invent an exit reason | I58 | test_an_exit_outside_the_sealed_vocabulary_is_refused | KILLED |
| M58 | trail arms immediately, ignoring its delay | I56 | test_trail_is_unarmed_before_its_delay_and_without_atr | KILLED |

One test-fixture defect found and fixed while writing these: the precedence
test set price exactly equal to the trail level, which lands on a float
boundary (1.1300 − 2×0.0050 = 1.1199999999999999) and tested nothing. Moved
the price clearly below the trail.
