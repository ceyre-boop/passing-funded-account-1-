# 017 — FORECAST LEDGER, COUNTERFACTUALS, AND MODEL PROMOTION `[SPEC]` (promoted 2026-08-09, ratification pending)

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


---

## `[SPEC]` promotion — 2026-08-09, on Colin's direct instruction. Architect
## ratification post-hoc.

### Decisions, answered

1. **Record-before-outcome is structural:** `resolve()` refuses a resolution
   timestamped before `as_of + horizon` — an outcome physically cannot enter a
   forecast early. Resolution is a SEPARATE record; the original claim is
   frozen and never edited.
2. **Grading:** multi-class Brier over the forecast's scenario distribution;
   binned calibration error on the top-scenario probability; directional
   accuracy; false-urgent rate (urgent interrupt claimed, no shock occurred);
   missed-shock rate (no urgent claim, shock occurred); stale-signal rate;
   mean policy-selection regret (fed from 014 regret deltas). Rule 3 applies:
   every report carries the UNIFORM-distribution baseline Brier scored on the
   identical cases.
3. **Promotion gate as code:** `promotion_decision()` takes score reports, not
   returns — aggregate return is not even a parameter, so "but it made more
   money" is unrepresentable. Gates (ALL must pass; the failure list is
   complete, not first-only): OOS-labeled scores required; minimum resolved
   decisions; strictly better Brier than the incumbent; calibration bound;
   per-regime stability (no regime bucket worse by more than the tolerance);
   tail ceiling on worst policy regret; stale-rate ceiling; false-urgent
   ceiling; explainability completeness (evidence ids present).
4. **Promotion/rollback are explicit events** producing a `promotion_ref` that
   is exactly what 016's AuthorityRegistry requires for a grant above the
   unpromoted cap — the two cards interlock. Historical scores are derived
   from the ledger and cannot be mutated by a promotion (tested).

### DoD

Early resolution refused; original claim immutable through resolution;
baseline on identical cases; a challenger better on aggregate but failing ONE
gate is rejected with that gate named; grade-before == grade-after promotion.
Fault rows in `mutation_check_017.py` → `specs/017_MUTATION_LOG.md`.
