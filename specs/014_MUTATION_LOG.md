# Card 014 (shadow/regret) — mutation evidence (Gate 4)

Containment (type-level no-execution), the no-future-data fold, the
policy version identity, and every regret metric fault-injected:
fault applied -> named test RED -> module restored byte-identical ->
GREEN. Portfolio guards (the card's third product) land at Gate 7
with their own rows.

| test | fault applied | result |
|---|---|---|
| `test_shadow_regret.py::test_shadow_action_kind_access_raises` | let .kind on a shadow action return a value instead of raising | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_shadow_action_cannot_reach_apply_action` | same fault — the execution funnel must also refuse it | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_shadow_serialization_is_unmistakable` | serialize a shadow action without its marker | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_prefix_property_no_future_data` | fold peeks at the future (max with the final price) | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_policies_genuinely_diverge` | every policy silently runs as DEFEND (tournament measures nothing) | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_shadow_records_policy_version_identity` | drop the policy version identity from results | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_bad_plan_risk_refused` | accept a zero-risk plan | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_grade_open_trade_refused` | grade an open trade | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_report_never_crowns_a_winner` | crown the hindsight winner in the report | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_grade_metrics_match_hand_computation` | drawdown stops measuring peak-to-trough | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_grade_metrics_match_hand_computation` | giveback forgets what was realized | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_grade_metrics_match_hand_computation` | hold time collapses to zero | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_grade_metrics_match_hand_computation` | slippage loses its 'paper' label (a zero must say why it is zero) | RED under fault, GREEN after revert |
| `test_shadow_regret.py::test_grade_metrics_match_hand_computation` | counterfactual deltas stop being deltas | RED under fault, GREEN after revert |

**14/14 rows verified.**
