# 046 — DOES STATE-CONDITIONING MOVE THE FORWARD-RETURN *DISTRIBUTION*?  `[SPEC]` `[PRE-REGISTERED]`

**Component:** `scripts/state_conditioning_test.py` (new), output `artifacts/state_conditioning.json`.
**Written:** 2026-08-29, before the script existed and before any statistic was computed.
**Decides:** whether a leaf evaluator / path sampler (the "position-evaluation layer" items 1–3)
is worth building at all. A null here kills items 1–3 and the deterministic engine stays.

---

## 1. Why this is not the fourth run at an answered question

Three attempts have already asked whether state predicts the forward **mean** and all three failed:

| attempt | what it fit | result |
|---|---|---|
| spec 025 `daytrade/exit_evaluator.py` | HistGradientBoosting on 19 position+config features → continuation advantage, day-grouped OOF, 18,305 decision points | **OOF corr 0.081**; derived policy −0.044 / −0.027 / +0.125 vs shipped +0.218 / +0.126 / +0.028 → `NO_SUPERSEDE` |
| HYP-071 (`~/quant`) | tabular exit value function, λ-penalized | `METRIC_ARTIFACT` |
| spec 045 (this repo, 2026-08-29) | carry exit tablebase, pure E[R] backward induction | `ACCEPT_H0` both arms, perm p 0.726 |

**A gradient-boosted model is a strictly stronger learner over that feature space than
nearest-neighbour retrieval is.** Any conditional-mean structure kNN could find, the GBM
already had the capacity to find, and it found `corr = 0.081`. Re-testing the conditional mean
is therefore pre-registered here as **expected null**, and is included only as a control.

**What is untried is the rest of the distribution.** All three attempts predicted a mean.
Retrieval yields a distribution, and the two can come apart: conditioning may leave `E[r]`
unchanged while moving variance, skew, or tail frequency. For an exit decision that is
decision-relevant, because **a stop is a path event with an asymmetric payoff** — two states
with identical `E[r]` but different path shape have different `P(stop touched)`, and therefore
different realized R after the stop truncates the distribution.

**Prior from this repo, different lane, so not circular:** `specs/043_RANGE_EXPANSION.md` —
7+ pre-registered nulls found no directional effect around scheduled macro events, while the
magnitude effect is real and well-powered (**lift 1.1207, p = 1.09e-07, n = 1056/1578**),
robust to denominator and to buffering. That is a measured instance of conditioning that moves
the second moment while the first stays flat. It is the reason this card exists.

## 2. The freedom this spec spends in advance

The critique this answers, verbatim: *"it has no fitted parameters, but it has a metric —
feature selection, scaling, weighting, choice of k — and those are researcher-chosen, which is
a worse kind of freedom than a cross-validated one. Invisible, and not penalized by anything."*

Correct. Every one of those choices is therefore fixed below, before the run. **A different
feature set, scaling rule, weighting, k, horizon, or statistic is spec 047, not an edit to
this file.** Changing any declared value after the first execution forfeits the
pre-registration.

## 3. Population

`data/daytrade/bars_premarket/SPY_5m.parquet` — 491,644 bars, 2,678 sessions,
2015-12-31 → 2026-08-26, America/New_York, columns Open/High/Low/Close/Volume.
SPY chosen because it is the only 10-year 5-minute series in the repo; the equity basket is
4,680 bars (≈3 months) per symbol, over which nearest neighbours are far by construction.

**Query bars:** RTH only (09:30–16:00 ET), and only bars with at least `H + 1` further bars in
the same session, so no forward window crosses an overnight gap. Extended-hours bars are used
for state computation but are never query bars.

## 4. The state vector — 6 dimensions, declared

All computable from SPY bars alone, strictly backward-looking, scale-free by construction.
Formulas reused from `daytrade/regime_vector.py` where one exists (`_atr`, `_slope_per_atr`,
`_vwap`, `_autocorr1`) — one implementation, per repo rule 1.

| dim | definition | source |
|---|---|---|
| `trend_strength` | `_slope_per_atr` over the trailing 24 bars | `regime_vector.py:159` |
| `realized_vol` | ATR14 (bars) / close | `regime_vector._atr:147` |
| `vol_expansion` | `log(ATR14_trailing_12 / ATR14_trailing_78)` | derived |
| `dist_from_vwap` | `(close − session VWAP) / ATR14` | `regime_vector._vwap:187` |
| `momentum_persistence` | lag-1 autocorrelation of the trailing 24 bar returns | `regime_vector._autocorr1:196` |
| `minutes_into_rth` | minutes since 09:30 ÷ 390 | derived (`minutes_to_close` was a top-3 feature in spec 025) |

**Scaling:** each dim z-scored using median and IQR (robust, not mean/sd) computed **on the
2016–2021 half only** and applied unchanged to both halves. **Weighting: equal.** Distance:
Euclidean. These three sentences are the whole metric; there is no tuning surface.

## 5. Retrieval and leakage guards — non-negotiable

For a query bar `q`, a neighbour bar `n` is admissible only if **all** hold:
1. `n` is strictly earlier than `q`;
2. `n`'s entire forward window has closed before `q`: `n + H + EMBARGO < q`, `EMBARGO = 78` bars (one RTH day);
3. `n` is not in the same session as `q`;
4. `n` satisfies the same query-bar conditions (RTH, full forward window in-session).

`k = 200` nearest admissible neighbours. Retrieval is exact (brute force in chunks); no ANN
index, so there is no approximation parameter.

**Neighbour-distance diagnostic (required output):** the distribution of the k-th neighbour
distance per query, and the fraction of queries whose k-th distance exceeds the median
*unconditional* pairwise distance. If neighbours are far, conditioning is fabricating and the
result must be read as such regardless of the statistics.

## 6. Horizons and statistics

Horizons `H ∈ {12, 24, 48}` bars (1h, 2h, 4h). Forward return `r_H = close[q+H]/close[q] − 1`,
expressed in ATR14 units at `q` so it is comparable across the decade.

Per (half, H), comparing the pooled neighbour-conditional distribution against the
unconditional distribution of the same query set:

| # | statistic | why |
|---|---|---|
| S1 | mean difference | **control — pre-registered as expected null** (spec 025 already answered it) |
| S2 | two-sample KS distance | whole-distribution shift |
| S3 | variance ratio `var(cond)/var(uncond)` | the range-expansion mechanism |
| S4 | skew difference | asymmetry |
| S5 | tail frequency at ±1 ATR, each side separately | the payoff is asymmetric; sides must not be pooled |
| S6 | **first passage**: `P(low touches −1 ATR before high touches +2 ATR)` within H | the decision-relevant quantity — a stop is a path event, not a terminal one |

## 7. The null — effect sizes, never p-values

At n ≈ 250,000 a KS test rejects any difference, including one far too small to trade. So no
p-value from a parametric or two-sample test is admissible as evidence here. Each statistic is
compared against **two** empirical null bands, 200 draws each, seed 20260829:

- **N1 shuffled-state:** permute the state vectors across timestamps, preserving every marginal
  and destroying only the state↔future link. Rerun retrieval.
- **N2 random-neighbour placebo:** draw k *random admissible* bars instead of the k nearest.

## 8. Decision rule — pre-registered, not negotiable after the run

A statistic **counts** only if its observed value lies outside the 5–95% band of **both** N1
and N2, **in the same direction**, in **both** date halves (2016–2021 and 2022–2026),
for **at least 2 of the 3 horizons**.

- **CONDITIONABLE** — at least one of S2–S6 counts. Items 1–2 proceed: `V(state)` is the
  neighbour-conditional estimator itself (no regressor), and the same retrieval is the path
  sampler. The counting statistic names which moment carries the signal.
- **NULL** — nothing counts. **Items 1–3 are dead.** The deterministic engine stays and the
  position-evaluation layer is not built. This is a complete, successful outcome.
- S1 counting while S2–S6 do not would contradict spec 025 and must be reported as a defect
  hunt, not a discovery.

Nothing is re-run with a changed parameter. A second metric is spec 047.

## 9. What a CONDITIONABLE result does NOT license

It establishes that the forward *distribution* is conditionable. It does **not** establish that
the conditioning converts to R — that is the item 6 harness, walk-forward, against the shipped
policy. And the shipped policy's own number is a backtest number, not a live one
(`data/trade_logs/paper_carry_trades.jsonl` is 0/50 closed): any future improvement measured
against it inherits that uncertainty. The harness must report the baseline's measurement
spread, not a point estimate.

Also excluded from any narrow follow-up: **spec 025's FUTURES class (+0.125 vs +0.028, n=65).**
One hit in three comparisons at that sample size is what noise looks like, and spec 025's own
pre-registered rule already refused it (it required ≥2 of 3 classes). It must not become a target.

## 10. Invariants

- **I72** — no neighbour's forward window overlaps or post-dates its query bar; a deliberate
  off-by-one in the embargo makes a named test fail.
- **I73** — scaling statistics are computed on the first date half only; perturbing a
  second-half row leaves them unchanged.
- **I74** — every reported statistic carries both null bands; a statistic emitted without them
  is a failure, not a warning.
- **I75** — the neighbour-distance diagnostic is emitted for every (half, H) cell.
