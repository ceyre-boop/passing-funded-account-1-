# Card 012 — mutation evidence (Gate 2)

Every reducer/envelope/persistence invariant from the promoted spec
fault-injected: fault applied -> named test RED -> module restored
byte-identical -> GREEN. The golden destroy-state->rebuild test has
four rows of its own (revision fold, key-revision reconstruction,
stage adapter, stop adapter) so the crash-recovery claim is not one
monolithic assertion.

| test | fault applied | result |
|---|---|---|
| `test_trade_events.py::test_envelope_validates` | accept unknown event types | RED under fault, GREEN after revert |
| `test_trade_events.py::test_envelope_validates` | accept timezone-naive timestamps | RED under fault, GREEN after revert |
| `test_trade_events.py::test_envelope_validates` | drop the redaction bound on payload strings | RED under fault, GREEN after revert |
| `test_trade_events.py::test_envelope_roundtrips_through_dict` | accept unknown envelope fields in from_dict | RED under fault, GREEN after revert |
| `test_trade_events.py::test_append_is_pure_and_contiguous` | accept a sequence gap in append | RED under fault, GREEN after revert |
| `test_trade_events.py::test_append_duplicate_identical_is_noop_different_raises` | treat a conflicting duplicate as a harmless retry | RED under fault, GREEN after revert |
| `test_trade_events.py::test_rebuild_requires_opened_first` | drop the dedicated first-event-must-open check | RED under fault, GREEN after revert |
| `test_trade_events.py::test_rebuild_requires_opened_first` | allow a second POSITION_OPENED | RED under fault, GREEN after revert |
| `test_trade_events.py::test_rebuild_closed_is_terminal` | accept events after POSITION_CLOSED | RED under fault, GREEN after revert |
| `test_trade_events.py::test_rebuild_rejects_gap_and_regression` | accept a sequence break in rebuild | RED under fault, GREEN after revert |
| `test_trade_events.py::test_rebuild_rejects_unknown_schema_version` | best-effort parse an unknown schema version | RED under fault, GREEN after revert |
| `test_trade_events.py::test_stop_advance_never_loosens` | let STOP_ADVANCED loosen the stop | RED under fault, GREEN after revert |
| `test_trade_events.py::test_hwm_never_regresses` | let HWM_UPDATED regress | RED under fault, GREEN after revert |
| `test_trade_events.py::test_partial_fill_requires_submission_and_keys_are_replay_safe` | accept a fill with no matching submission | RED under fault, GREEN after revert |
| `test_trade_events.py::test_partial_fill_requires_submission_and_keys_are_replay_safe` | drop the applied-key double-reduce refusal (message-pinned test) | RED under fault, GREEN after revert |
| `test_trade_events.py::test_partial_fill_requires_submission_and_keys_are_replay_safe` | stop persisting applied reduction keys (C005 persistence) | RED under fault, GREEN after revert |
| `test_trade_events.py::test_golden_destroy_state_rebuild_identical_decision` | stop advancing state_revision in the reducer | RED under fault, GREEN after revert |
| `test_trade_events.py::test_golden_destroy_state_rebuild_identical_decision` | translator records keys against the wrong (post-apply) revision | RED under fault, GREEN after revert |
| `test_trade_events.py::test_golden_destroy_state_rebuild_identical_decision` | adapter forgets the lifecycle stage | RED under fault, GREEN after revert |
| `test_trade_events.py::test_golden_destroy_state_rebuild_identical_decision` | adapter forgets the folded stop | RED under fault, GREEN after revert |
| `test_trade_events.py::test_jsonl_log_roundtrip_and_torn_tail` | treat a torn tail as ordinary mid-log corruption | RED under fault, GREEN after revert |

**21/21 rows verified.**
