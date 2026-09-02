# Base rate — low-priced / high-RVOL NASDAQ screen, development half

Prereg: `artifacts/LOWFLOAT_SCREEN_PREREG.md` (frozen at `abd7f34`, Amendment 1
before compute). Development half **2016-01-01 → 2021-12-31**. The sealed half
(2022-2026) has not been read.

## Headline — the screen is real and it is not tradeable

Both halves of that sentence are load-bearing.

| | n | ran (high ≥ +20% over prior close) |
|---|---|---|
| every NASDAQ common-stock day | 4,366,034 | **0.995%** |
| + price $1–$10, prior dollar-vol ≥ $1M | 534,229 | **2.178%** (2.19×) |
| + prior volume ≥ 3× 20-day median | **87,237** | **5.779%** (**5.81×**) |

**Effect 4.784 pp against a detection floor of 0.088 pp — 54.2× above floor
(z = 134.8).** For context: every prior finding in this repo landed 3–7×
*below* its floor. This is the first thing that clears by a wide margin.

Stable across years, so not a 2020 artifact:

| year | n | hit rate | lift | med close | med MAE | wide tape |
|---|---|---|---|---|---|---|
| 2016 | 8,443 | 4.16% | 4.2× | −0.27% | −3.53% | 1.31% |
| 2017 | 11,990 | 5.05% | 5.1× | −0.36% | −3.85% | 1.88% |
| 2018 | 11,393 | 4.55% | 4.6× | −0.32% | −3.78% | 1.68% |
| 2019 | 10,912 | 4.64% | 4.7× | −0.20% | −3.75% | 1.46% |
| 2020 | 17,640 | 8.78% | 8.8× | −0.89% | −5.17% | 3.38% |
| 2021 | 26,859 | 5.63% | 5.7× | −0.10% | −3.25% | 2.00% |

Excluding 2020 entirely: **5.02%** on n = 69,597. The screen is not a museum piece.

## Why it is still not a trade

Hit rate 5.78% = **1 in 17.3**. Breakeven payoff is `(1−W)/W` = **16.3×**.

| runner pays | EV per candidate-day (1R stop on every miss) |
|---|---|
| 2R | **−0.827R** |
| 5R | **−0.653R** |
| 10R | **−0.364R** |
| 16.3R | −0.000R |

Nothing pays 16.3R. **Blind screen-following is catastrophically negative at
every realistic payoff.** The screen is a candidate generator, not a signal.

## What the median candidate actually did (n = 87,237)

| | median | mean |
|---|---|---|
| overnight gap | 0.00% | +0.51% |
| best excursion (high) | **+2.61%** | +5.78% |
| close vs prior close | −0.25% | −0.26% |
| worst (low) | **−3.64%** | −4.92% |
| MAE from the open | −3.87% | −5.27% |

**The median candidate has more downside than upside — 0.72×.** Its best moment
is +2.61%; its worst is −3.64%.

Bootstrap on realized close-to-close (10k resamples, n=2000): mean −0.26%,
95% CI **[−0.76%, +0.32%]**, P(mean < 0) = 82.3%. Indistinguishable from zero,
leaning negative — before spread, and these are $1–$10 names.

Left tail, close vs prior close: p1 **−24.57%**, p5 −12.74%, p10 −8.78%.

## What a hit paid, when it hit (n = 5,041)

| | median | p25 | p75 | p95 |
|---|---|---|---|---|
| best excursion | **+31.79%** | +24.31% | +48.61% | +123.30% |
| close vs prior close | +17.63% | +9.39% | +27.72% | +67.75% |
| **MAE from open** | **−5.21%** | **−12.47%** | −1.68% | 0.00% |
| overnight gap | +6.63% | +1.00% | +19.54% | +64.38% |

**Even the winners hurt first.** A quarter of runners drew down ≥12.47% from the
open *before* running. A stop tight enough to make the 94% survivable is tight
enough to remove a quarter of the 6%.

## Data integrity notes

- **Raw (unadjusted) prices.** Amendment 1: split-adjusted series delete this
  universe (SNDL 2021-02-10 traded $2.95, shows as $29.50 adjusted; MULN shows
  as $3.86T). Of 13,274 US splits since 2016, **8,692 are reverse splits** —
  endemic, exactly here.
- 62 split-corrupted candidate-days dropped.
- **Survivorship-free.** Universe is 6,592 NASDAQ common stocks ever;
  **3,255 (49.4%) are delisted** and are included. Delisted-name bars verified
  entitled (SIVB 2023-03-09, ATVI 2023).
- Warrants/units/rights excluded twice (Polygon `type=CS` + suffix rejection).
  A 2026-08-21 spot check found the raw %-gainer list topped by sub-penny
  warrants (CINGW $0.005→$0.009 = "+71.7%") — one tick of spread, not a move.
- `wide_tape` (2.09% of candidate-days) is a **range proxy, not a halt record.**

## Stage-two hypothesis, generated here, NOT a result

The development half shows runners gapping harder than the cohort (median gap
+6.63% vs 0.00%). That is an observation on a pre-registered recorded field, not
a tested claim. If it becomes a filter it needs its own prereg and the trial is
counted. **It is not evidence yet.**

## Standing

Sealed half (2022-2026) untouched — one shot, after the stage-two rule is frozen.
