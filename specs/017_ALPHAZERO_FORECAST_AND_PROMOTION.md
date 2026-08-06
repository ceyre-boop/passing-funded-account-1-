# 017 — FORECAST LEDGER, COUNTERFACTUALS, AND MODEL PROMOTION `[PLAN]`

Coverage: AlphaZero items 10–12. Depends on 015–016 and the existing scorecard,
backtest, ceiling, and split rules.

## Intended contract

Persist each forecast before its outcome is known: forecast id, as-of time,
scope, target horizon, probability/scenario distribution, recommendation,
confidence, evidence ids, model/prompt versions, and resolution status. Later
resolution records the observed outcome without editing the original claim.

Grade Brier score, calibration error, directional accuracy, false urgent-exit
rate, missed-shock rate, policy-selection regret, and stale-signal rate. Shadow
interpretations may run, but exactly one model is authoritative.

## Promotion gate

Plan a challenger/ incumbent registry requiring minimum decisions, strictly
out-of-sample improvement, stability by regime, tail-loss ceiling, calibration,
explainability completeness, stale-signal ceiling, and false-emergency ceiling.
Promotion and rollback must be explicit events and must not mutate historical
scores.

## DoD seed

A sealed fixture demonstrates that an outcome cannot enter a forecast before its
resolution horizon, the baseline is scored on identical cases, and a challenger
that fails one gate is rejected even if aggregate return is higher.

