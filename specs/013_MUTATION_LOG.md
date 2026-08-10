# Card 013 — mutation evidence (Gate 3)

Planner rules (never resize, refuse bad quotes, urgency mapping) and
every ledger quantity invariant fault-injected: fault applied -> named
test RED -> module restored byte-identical -> GREEN. The seeded
property walk additionally asserts the invariants after every event
of 200-step random legal sequences.

| test | fault applied | result |
|---|---|---|
| `test_execution_policy.py::test_planner_never_changes_the_requested_reduction` | planner resizes the requested reduction (the forbidden second engine) | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_urgent_exit_goes_market` | urgent exit stops going MARKET | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_bad_quotes_are_refused_never_guessed` | price an order on a stale quote | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_bad_quotes_are_refused_never_guessed` | accept a crossed/empty quote | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_bad_quotes_are_refused_never_guessed` | plan a zero-quantity order | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_tight_spread_crosses_wide_spread_sits` | always cross the spread regardless of budget | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_unknown_order_and_unknown_event_raise` | accept the same broker order id twice | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_unknown_order_and_unknown_event_raise` | accept an unidentifiable fill | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_oversubmission_beyond_remaining_raises` | submit more than the intent's remaining quantity | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_oversubmission_counts_live_open_orders` | remaining ignores live open orders (review finding 1) | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_cross_order_overfill_raises_never_reopens` | absorb a cross-order intent over-fill (review finding 2) | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_overfill_is_reconciliation_failure_not_absorbed` | absorb an over-fill | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_duplicate_fill_id_identical_is_retry_different_is_corruption` | treat a conflicting duplicate fill as a harmless retry | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_late_fill_racing_cancel_is_honored_within_submitted` | stop shrinking canceled qty on a late fill (books go negative) | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_full_fill_completes_partial_does_not` | mark an intent FILLED from submission alone | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_cancel_releases_exposure_and_retry_resubmits_remainder` | report zero pending exposure while quantity is still owed | RED under fault, GREEN after revert |
| `test_execution_policy.py::test_reduction_payloads_submission_alone_completes_nothing` | emit the FILL payload for a merely-submitted reduction | RED under fault, GREEN after revert |

**17/17 rows verified.**
