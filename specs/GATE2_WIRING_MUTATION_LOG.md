# Gate 2 wiring — mutation evidence

The runner's event emission fault-injected end to end: the append
itself, append-before-apply ordering, the pre-apply revision base
(key ...:1 pin), resume key threading through the TP2 kill point,
the pure pre-validation gate, the open-trade fork refusal, and the
directory fsync on creation. Each fault -> named test RED ->
restore byte-identical -> GREEN. Run drivers sequentially only.

| test | fault applied | result |
|---|---|---|
| `test_runner_event_wiring.py::test_event_stream_agrees_with_session_log` | stop appending the cycle's events | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_append_happens_before_apply` | append AFTER apply (the crash window the spec forbids) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_event_stream_agrees_with_session_log` | wrong revision base at the pre-apply call site (keys record ...:-1) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_destroy_and_resume_matches_unbroken` | resume with a fresh key set instead of the reconstructed one (C005 lost) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_constitution_refusal_appends_nothing` | drop the pure pre-validation gate (the log can claim refused actions) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_open_trade_refuses_non_resume_start` | silently fork an open trade's history on a non-resume start | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_first_append_fsyncs_the_directory` | skip the directory fsync on log creation | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_closed_trade_rotates_aside_on_fresh_start` | refuse a fresh start over a CLOSED trade instead of rotating it aside | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_torn_tail_tolerated_on_resume_refused_otherwise` | crash unhelpfully on resume over a torn tail (the exact defended crash) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_pending_submission_refuses_resume` | resume past an unreconciled submission (013's window, guessed at) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_torn_tail_tolerated_on_resume_refused_otherwise` | skip truncating the torn fragment (the next append concatenates onto it) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_dry_run_carries_the_clock_c004` | dry-run drops the clock (review finding 1: false events go durable) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_per_action_dry_run_catches_what_the_batch_check_cannot` | drop the per-action dry-run (review finding 2: it was dead weight) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_resume_the_logs_plan_wins_over_a_doctored_cli_plan` | resume trusts the CLI plan over the log (review finding 3) | RED under fault, GREEN after revert |
| `test_runner_event_wiring.py::test_newline_terminated_garbage_final_line_is_corruption_not_torn` | grant complete-but-corrupt final lines the tear exemption (finding 4) | RED under fault, GREEN after revert |

**15/15 rows verified.**
