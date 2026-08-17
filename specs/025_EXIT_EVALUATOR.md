# 025 — POOLED EXIT EVALUATOR `[SPEC]`

**Component:** `daytrade/exit_evaluator.py`, observer hook in `ceiling.simulate`
**Status:** `[SPEC]` — written 2026-08-17 BEFORE the code and before any model
saw any data. Everything below is pre-registered; changing it after reading
results requires a dated re-registration note.
**Origin:** cross-chat architecture review of the furnace: the sweep is
already a (state, action) → reward table, not just a leaderboard; a pooled
evaluator lets 65 futures entries borrow statistical strength from the other
271 instead of standing alone. Unseal of `futures-exit-v1` is ON HOLD until
this decides what we're validating.

## The crack in the analogy, named (repo-side addition)

Two honesty constraints the cross-chat review couldn't see from outside:

1. **The sweep rows are (entry, config) → FINAL R, not per-decision-point.**
   A real evaluator needs state at every bar. So the dataset is built by a new
   logging pass through the SAME simulator (observer hook — the engine and
   harness stay one implementation), not by reshaping the leaderboard.
2. **336 entries overstate the sample.** SPY/QQQ/ES/NQ entries on the same
   day are nearly one bet. ALL cross-validation is therefore **grouped by
   calendar date** (GroupKFold on day) — a model validated on random splits
   would leak same-day cross-symbol correlation and report fiction.

## Pre-registered design

**Dataset.** One row per (entry, config, bar-while-open):
features = r_banked, unrealized_r, hwm_r, drawdown_from_hwm_r, bars_held,
minutes_to_close, dist_to_stop_r, dist_to_tp2_r, stage_ordinal, recent
true-range (5-bar mean, in R), asset_class (one-hot over
SINGLE_NAME/CASH_INDEX/FUTURES), config params (trail_mult with a no-trail
flag, be_arm_frac, partial_frac, flatten-minutes-remaining or -1,
hold_past_tp2).
Label = **continuation advantage**: final trajectory R − exit-now R at this
bar (exit-now = mark the held quantity at this close, minus cost).

**Model.** `sklearn.ensemble.HistGradientBoostingRegressor`, default depth
family, max_iter 300, no per-class models — asset class is a feature.
Interpretability requirement: permutation feature importances reported.

**Validation.** GroupKFold(5) grouped by session DATE over TUNE-split entries
only. All reported numbers are out-of-fold.

**Derived policy.** On a held trajectory (base config = the class's best
shipped policy), exit at the first bar where OOF-predicted advantage < 0;
otherwise ride to the trajectory's own end. Realized R is then the logged
exit-now R at that bar (no new simulation — the counterfactual is already in
the dataset).

**Verdict rule (registered now):**
- SUPERSEDE_STATIC iff the derived policy's mean R/trade, OOF and
  day-grouped, beats BOTH (a) the class's best shipped policy and (b) the
  frozen `futures-exit-v1` on FUTURES, each by ≥ +0.05 R/trade, in ≥ 2 of 3
  asset classes including FUTURES.
- Anything less: NO_SUPERSEDE — the static candidate remains the object the
  eventual sealed read validates. A null result is a result.
- The sealed holdout is untouched by this spec in every branch. Only after
  the SUPERSEDE/NO_SUPERSEDE verdict does Colin choose what to unseal on.

## Out of scope

Deep RL / self-play (data doesn't support it); per-decision branching
counterfactuals beyond exit-now (v2, priced separately); any live-path
change; any holdout read.
