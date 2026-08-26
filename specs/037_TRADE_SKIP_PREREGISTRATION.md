# 037 — TRADE / SKIP PRE-REGISTRATION  `[SPEC]`  `[UNRUN]`

**Everything in this document is fixed before any model is fitted.** Written
2026-08-26, after the oracle audit's second `NOTHING_QUOTABLE` and before a
single feature has been regressed against a single label. Changing any value
below after results exist requires a logged decision naming what changed and
why — same discipline as spec 008's re-registration clause and spec 021's P-block.

---

## Why this is a different question, and why the last one's failure does not
## transfer

`daytrade/oracle_audit.py` asked: *given that we traded, which exit family
should we have chosen?* It failed twice, and on 2026-08-26 the failure was shown
to be structural rather than a data shortage:

```
     n     null_leak     gate 0.15
    39        1.4496
    80        1.5186
   160        1.5612
   402        1.5813        FAIL (10.5x)
```

`null_leak = E[max of K permuted columns] − best_fixed`. That is a property of
the **marginal** R distribution, not an estimation error. More rows measure the
constant more precisely; they never drive it to zero. A 6-family max on σ≈1.13
returns sits permanently above a 0.15 gate. **No quantity of history fixes it.**

This spec asks a different question and the difference is load-bearing:

> Given the entry rule fired, should we have taken the trade **at all**?

The statistic here is **not a max over columns**. It is the realized R of a
single, pre-chosen policy column, gated by a binary decision. Under shuffled
features the expected improvement is **≈ 0**, not a positive constant. So a gate
set at a small positive number is meaningful here in a way it structurally could
not be for the oracle question.

This is also what `daytrade/ceiling.py` has been saying in its own output all
along: `days_with_any_winning_config = 105/429`. Its comment — *"If this is
small, the ceiling is set by the ENTRY, and no exit policy can raise it."*

## Population (frozen)

- Symbol: **NVDA only.** One entry per day, so entries == distinct days == 1.00.
  The prior audit's 336 entries were 39 days at 8.62 correlated symbols/day;
  both the permutation null and the `GroupKFold` are day-blocked, so its
  effective n was ~39. `daytrade/mechanisms.py` prices this as `K_EFF = 8.7`.
- Cache: `data/daytrade/bars_extended/NVDA_5m.parquet` (Alpaca SIP, 664 sessions).
- Split: `daytrade/splits.tune_sessions`, `TUNE_END = 2026-07-06`, **unedited**.
  → **402 tune entry days**, 2024-01-02 .. 2026-07-06.
- The 36 sealed sessions after `TUNE_END` are **not read by this experiment.**

## Target and decision (frozen)

- Policy column: `EARLY_BANK` — the best fixed family on this population.
  Chosen now, on the population's marginals, before any feature is examined.
- Label: `R_EARLY_BANK` on the entry day. A continuous R, not a class.
- Decision: **trade** or **skip**. Skipping realizes exactly `0.0` R.
- Realized series: `R_i if predicted_trade_i else 0.0`.

## Measured baselines (computed 2026-08-26, before fitting)

| quantity | value |
|---|---|
| always-trade mean R | **−0.0061** |
| per-day σ | **0.9137** |
| days with R > 0 | **12.7%** |
| days with R < 0 | **87.3%** |
| n | **402** |

**Power:** detecting +0.20 R/trade at one-sided 95% / 80% needs
`((1.645+0.842)² · (0.9137/0.20)²) ≈ 129` days. We have 402. **This question is
powered.** The exit question never was.

## The degenerate-win trap, named in advance

87.3% of days are negative. A model that **skips everything** realizes exactly
0.0 R and therefore "beats" always-trade's −0.0061. That is not skill; it is the
sign of the unconditional mean. Any gate that only compares against always-trade
would be passed by a constant function.

Two controls are therefore mandatory, and the second is the important one:

1. **Shuffled-feature control.** Permute the feature rows against labels, refit
   the identical pipeline, same folds. Expected improvement ≈ 0.
2. **Rate-matched random skipper.** Skip the *same number of days* as the model
   did, chosen uniformly at random, 1000 draws. This holds the skip rate fixed
   and isolates whether the **selection** carries information or only the rate
   does. A model that beats always-trade but not its own rate-matched random
   skipper has learned nothing except that the unconditional mean is negative.

## Gates (all must pass; pre-registered)

| gate | condition |
|---|---|
| **T1 POWER** | n ≥ 129 tune days. *(satisfied: 402)* |
| **T2 NON-DEGENERATE** | model trades on **≥ 40%** of days. Below that the result is reported as `DEGENERATE_SKIPPER` and nothing else is quotable. |
| **T3 BEATS RANDOM** | OOF realized mean R interval **lower** bound > rate-matched random skipper interval **upper** bound. Intervals are 5–95%, 1000 draws. Mirrors spec 021 G3's shape. |
| **T4 BEATS NOISE** | OOF realized mean R > shuffled-feature control mean R + 0.05 R/trade. |
| **T5 BEATS ALWAYS-TRADE** | OOF realized mean R > −0.0061 + 0.05 R/trade. Listed last deliberately: it is the weakest of the five and passing it alone means nothing. |

Failing T2 short-circuits: T3–T5 are not computed or quoted.

## Method (frozen)

- Features: the ex-ante table from `scripts/build_extended_features.py` —
  bar-derived up to the entry bar, plus `macro_state` joined on **D−1**. No
  feature may be knowable only after the entry timestamp; the boundary assertion
  required by `specs/005_BACKTEST.md` is a hard prerequisite for this run.
- Model: **one** class — gradient-boosted regressor on R, threshold at 0.
  `sklearn`, already a dependency. No model search. No hyperparameter sweep.
- Validation: `GroupKFold(5)` grouped on **calendar date**, per
  `daytrade/exit_evaluator.day_grouped_oof`. All reported numbers are
  out-of-fold.
- `RNG_SEED = 26`, matching the rest of the daytrade lane.
- **One feature set, one model class, one threshold, one run.** A second
  configuration is a second test and forfeits this pre-registration.

## Reporting (spec 021 P5 discipline)

Every output prints the full reference set on one screen — never the model alone:

```
always-trade  →  rate-matched random  →  shuffled-feature  →  MODEL (OOF)
```

with the trade rate beside them. The formatter takes all four or raises.

## On-the-hook prediction

Mine, recorded before the run, so it can be scored against:

- **T2 is the one that fails.** With 87.3% of days negative I expect the model to
  learn "skip" as a near-constant and trade on well under 40% of days.
- If T2 passes, I expect T3 to fail — the skip rate carries the improvement, not
  the selection.
- Probability all five pass: **~15%**.
- If all five pass, that is a genuine, powered, entry-side finding and it is the
  first one this lane has produced.

## What a failure means

`DEGENERATE_SKIPPER` or a T3 failure closes the **entry-side** question on this
population the way 2026-08-26 closed the exit-side one, and the NVDA
opening-range-break lane is then finished as an edge source pending a different
entry rule — not a different exit, and not more data.

That is a result. It is not a disappointment.

## Out of scope

- The 36 sealed sessions.
- Any second model, feature set, or threshold under this pre-registration.
- The carry lane, which is unaffected by anything in this document.
