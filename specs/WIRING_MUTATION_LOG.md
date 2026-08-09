# Constitution wiring — mutation evidence

Each C004/C005/C007 threading point deliberately broken (engine side and
the backtest caller side); the named live-path test in
test_constitution_wiring.py confirmed RED, module restored, test GREEN.

Known residual (recorded in 020's backlog, not silently dropped): the
runner and ceiling caller-side threading has no automated test yet —
run()'s loop is not unit-callable and simulate() needs bar fixtures.
In-process C005 is mechanical only until card 012 persists keys+state.

| test | fault applied | result |
|---|---|---|
| `test_constitution_wiring.py::test_c004_backwards_clock_refused_through_apply_action` | stop forwarding the clock into enforce() | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_c007_action_after_exit_all_refused_through_apply_actions` | stop forwarding the batch into enforce() | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_c005_replayed_reduction_refused_through_apply_actions` | stop recording applied reduction keys in apply_actions | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_c005_replayed_reduction_refused_through_apply_actions` | record keys but stop passing them to apply_action | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_backtest_replay_threads_one_session_lifetime_key_set` | backtest caller: fresh key set per row instead of the session-lifetime set | RED under fault, GREEN after revert |

**5/5 rows verified.**
