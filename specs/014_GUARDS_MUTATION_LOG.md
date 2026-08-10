# Card 014 portfolio guards — mutation evidence (Gate 7)

Every guard, the inclusive lock boundary, the breaker ordering rule,
and the no-invented-correlations rule fault-injected. The live-broker
boundary test pins broker.py's existing paper-host refusal and is
deliberately NOT mutated — the transport is live-path code this card
does not touch.

| test | fault applied | result |
|---|---|---|
| `test_portfolio_guard.py::test_limits_validate_and_breaker_ordering_is_enforced` | allow a breaker that makes the daily lock unreachable | RED under fault, GREEN after revert |
| `test_portfolio_guard.py::test_limits_validate_and_breaker_ordering_is_enforced` | accept negative open risk as a fact | RED under fault, GREEN after revert |
| `test_portfolio_guard.py::test_g001_total_open_risk` | stop enforcing total open risk | RED under fault, GREEN after revert |
| `test_portfolio_guard.py::test_g002_per_symbol_exposure` | stop enforcing per-symbol exposure | RED under fault, GREEN after revert |
| `test_portfolio_guard.py::test_g003_correlated_exposure_only_for_supplied_groups` | invent a correlation group the caller never supplied | RED under fault, GREEN after revert |
| `test_portfolio_guard.py::test_g003_correlated_exposure_only_for_supplied_groups` | stop enforcing supplied correlated exposure | RED under fault, GREEN after revert |
| `test_portfolio_guard.py::test_g004_unprotected_count` | stop counting unprotected positions | RED under fault, GREEN after revert |
| `test_portfolio_guard.py::test_g005_daily_loss_lock_at_boundary` | flip the inclusive lock boundary | RED under fault, GREEN after revert |
| `test_portfolio_guard.py::test_g006_emergency_flatten_outranks_lockout` | let LOCKOUT outrank the emergency flatten | RED under fault, GREEN after revert |

**9/9 rows verified.**
