# Card 017 — mutation evidence (Gate 6)

The record-before-outcome seal, claim/resolution immutability, the
identical-case baseline, and every promotion gate fault-injected:
fault -> named test RED -> restore byte-identical -> GREEN. Note the
structural guarantee no mutation can test: promotion_decision takes
no return/PnL parameter at all — 'but it made more money' is
unrepresentable by signature.

| test | fault applied | result |
|---|---|---|
| `test_forecast.py::test_forecast_validates` | accept a non-distribution | RED under fault, GREEN after revert |
| `test_forecast.py::test_forecast_validates` | accept unknown scenarios in a forecast | RED under fault, GREEN after revert |
| `test_forecast.py::test_outcome_cannot_enter_before_horizon` | let an outcome enter before the horizon (the card's core seal) | RED under fault, GREEN after revert |
| `test_forecast.py::test_claims_are_made_once_and_resolved_once` | record the same claim twice | RED under fault, GREEN after revert |
| `test_forecast.py::test_claims_are_made_once_and_resolved_once` | re-litigate a resolved outcome | RED under fault, GREEN after revert |
| `test_forecast.py::test_resolution_never_edits_the_original_claim` | let resolution clobber the original claim | RED under fault, GREEN after revert |
| `test_forecast.py::test_brier_and_baseline_on_identical_case` | brier ignores a surprise outcome outside the forecast's vocabulary | RED under fault, GREEN after revert |
| `test_forecast.py::test_brier_and_baseline_on_identical_case` | baseline stops being uniform on the identical case | RED under fault, GREEN after revert |
| `test_forecast.py::test_false_urgent_and_missed_shock_rates` | invert the false-urgent definition | RED under fault, GREEN after revert |
| `test_forecast.py::test_brier_must_be_strictly_better` | let a tied challenger through | RED under fault, GREEN after revert |
| `test_forecast.py::test_one_failed_gate_rejects_even_a_brilliant_challenger` | promote despite failed gates | RED under fault, GREEN after revert |
| `test_forecast.py::test_failed_gate_list_is_complete_not_first_only` | report only the first failed gate | RED under fault, GREEN after revert |
| `test_forecast.py::test_regime_instability_and_missing_bucket_reject` | ignore regime instability | RED under fault, GREEN after revert |
| `test_forecast.py::test_regime_instability_and_missing_bucket_reject` | ignore a missing regime bucket | RED under fault, GREEN after revert |
| `test_forecast.py::test_gates_with_no_data_fail_not_pass` | regime gate passes vacuously with no data (review finding 6) | RED under fault, GREEN after revert |
| `test_forecast.py::test_gates_with_no_data_fail_not_pass` | tail gate passes vacuously with no regret data (review finding 6) | RED under fault, GREEN after revert |
| `test_forecast.py::test_brier_and_baseline_on_identical_case` | constant-0.5 baseline masquerading as uniform — indistinguishable on 2-name cases, caught by the 3-name numeric pin (review finding 7) | RED under fault, GREEN after revert |

**17/17 rows verified.**
