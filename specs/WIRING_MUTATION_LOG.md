# Constitution wiring — mutation evidence

Each C004/C005/C007 threading point deliberately broken; the named
live-path test in test_constitution_wiring.py confirmed RED, module
restored, test confirmed GREEN.

| test | fault applied | result |
|---|---|---|
| `test_constitution_wiring.py::test_c004_backwards_clock_refused_through_apply_action` | stop forwarding the clock into enforce() | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_c007_action_after_exit_all_refused_through_apply_actions` | stop forwarding the batch into enforce() | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_c005_replayed_reduction_refused_through_apply_actions` | stop recording applied reduction keys in apply_actions | RED under fault, GREEN after revert |
| `test_constitution_wiring.py::test_c005_replayed_reduction_refused_through_apply_actions` | record keys but stop passing them to apply_action | RED under fault, GREEN after revert |

**4/4 rows verified.**
