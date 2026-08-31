# Adversarial pass on the week's five rulings — 2026-08-30

All numbers recomputed from `bars_premarket/SPY_5m.parquet` through the repo's own unmodified `ceiling.simulate`
/ `transitions` / `tablebase` / `gate.sprt`. **Reproduced exactly:** the SPRT (−172.71 / −119.00 / σ 0.6573 /
ACCEPT_H0 stop 91), the ceiling (0 of 396 profitable, median −0.0741/tr), all three granularity rows, every
Phase 0 cell. **Not reproducible:** the k-sweep — no committed script, no stated ATR definition, five plausible
ones giving the same shape and none the same numbers; the bind rates quoted (61.3%, 11.8%) are in no file here.

## 1 — `trail` stays ACTIVE · **WEAKENED** (outcome stands; the stated warrant is void)

δ=0.10 did **not** rig the test, and that is not the defect. **No δ in (0.001, 0.100] accepts H1** on this data:
max LLR at any δ is **+1.53** (δ≈0.03) against an upper bound of +2.7726 — the +53.71 R carries at most 55% of
the log-evidence needed at the most favourable bar available. δ is still badly chosen (2.8× the MDE — one-sided,
α=.05, 80% power, σ=0.6573, n=2,116: **0.0355** — and its drift term auto-ACCEPTs H0 for any effect below
δ/2=0.0508), but fixing δ changes nothing.

The real defect: **the gate is direction-blind.** Mirror run (incumbent = no-trail, candidate = trail-1.5):
**ACCEPT_H0, stop 100** vs 91, same deviation, at every δ tried (.02/.03/.05/.10). It returns "keep the
incumbent" whichever policy is labelled incumbent — and `CALIBRATION_RECORD.md`, which names this limit,
prescribes stopping-speed + deviation as the discriminator: 91 vs 100 with identical deviation is
*indistinguishable*. "`trail` stays ACTIVE because the test said so" is a fact about who was shipped, not about
trail. Three more:

- The 23% deviation cited to certify "a real null, not a hollow one" is a **full-population** figure. Of the
  **91 episodes the decision consumed, 72 (79.1%) were ΔR ≡ 0**, each donating −0.01157 to the LLR for free;
  zeros alone cross the H0 bound in 135 steps — precisely the hollow-null failure the calibration record was
  written about.
- On the **486 acting episodes, mean ΔR = +0.1105, above δ** — and the SPRT there returns **INCONCLUSIVE** (LLR
  +1.57), not ACCEPT_H0. That record's own "never pool acting and non-acting units into a headline" was
  declared, then not applied to the first candidate.
- Order-dependent: over 2,000 permutations of the same 2,116 differences, **252 (12.6%) return ACCEPT_H1**.
  Fixed-sample one-sided t on all 2,116: **t=1.776, p=0.038**.

**Settles it:** re-run with δ pre-registered from the MDE (≈0.035), restricted to acting episodes, mirror arm
alongside — if the mirror also fails, the four lines against trail are all the evidence there is.

## 2 — `time_decay` OUT · **SURVIVES** (the record under-argued it)

"No evidence to set its parameter" was asserted, not tested — and it is testable on the record's own statistic
(median config, not best-of-396). Full population, matched pairs (same config, `flatten_et` swapped):

| flatten_et | median cfg /tr | matched-pair vs None (median) | pairs favouring |
|---|---|---|---|
| 11:00 | −0.07664 | **−2.02 R** (−0.00095/tr) | 44% |
| 12:00 | −0.07486 | +0.54 R | 53% |
| 15:45 | −0.07258 | **+1.58 R** (+0.00075/tr) | **88% of 99** |
| None | −0.07346 | — | — |

There *is* a signal (88/99 pairs); it is **0.00075 R/trade**, 1/45 of the MDE, and its sign says *flatten later*
— against a tightening schedule, not for one. The median statistic reaches the record's conclusion admissibly. But **cliff-vs-schedule is a false dichotomy**: layers 1–4 already
coexist and compose by tightest-wins, so "a second time instrument competing with the first" applies equally to
`breakeven` beside `catastrophic`. Drop that; keep effect size.

## 3 — volatility, no k selected · **SURVIVES**, decisively

+17.83 R over 8 draws is not merely within the noise, it is *at the mean of the noise*. On my reconstruction
(+18.19 R at k=0.75, within 0.4 R) I built the null by sign-flipping paired differences per episode across all
eight arms, preserving the real cross-arm correlation (0.65–0.71 between neighbours): null max-over-8 **mean
+18.50 R, median +13.1 R, p95 +55.8 R**; observed +18.19 R → **p = 0.396**; single-arm k=0.75 **t = 0.81**.

The bind shape is coherent, not suspicious. At k=0.75 the vol stop sits tighter than the plan stop on 62.2% of
entries but changes realized R on only 32.0% (mean +0.027 on those); at k=1.5, 9.0% / 5.0% / −0.040. Half of all
binds change nothing because the trade stopped or targeted there anyway — a near-EV-neutral stop relocation is
exactly what a 61%-bind / +0.008-per-trade row looks like. Refusing a k is not an over-correction. **The
reproducibility gap is the real problem.**

## 4 — granularity `coarse_both` · **SHOULD BE REVERSED**

The complement argument is legitimate: `valued` ⟺ `n_paths ≥ min_paths` (a non-thin cell always gets a finite
`q_exit`), so the two fractions are genuine complements, and the choice is robust across any threshold in
(0.7198, 0.8905]. **The statistic itself is broken.** `Tablebase.build` buckets by `phase` but assigns
`self.cells[state]` — and `State` carries no phase (`phase` = bar index; only the coarse `hold_b` bucket encodes
it). A state seen at several bar positions is **overwritten**, and `coverage()` then sums `n_paths` over
survivors only:

| granularity | coverage() obs | true obs | dropped | **coverage() frac_obs_valued** | **true frac_obs_valued** |
|---|---|---|---|---|---|
| minimal | 4,484 | 17,603 | 74.5% | 0.9097 | 0.9788 |
| coarse_both | 5,944 | 17,603 | 66.2% | 0.8905 | 0.9640 |
| full | 6,052 | 17,603 | 65.6% | 0.7198 | **0.8950** |

**Applied to the correctly-computed statistic, the declared rule — finest grid with frac_obs_valued ≥ 0.85 —
selects `full` (357 cells, 148 valued, median 44 distinct episodes per valued cell), not `coarse_both`.** The
ruling is inverted by a bug in the number it reads, and the same bug taints the basket's quoted 0.4613.
Run-order disclosure is beside the point: the rule was applied faithfully to a wrong input. **Settles it:** key
`Tablebase.cells` by `(phase, state)` or aggregate per state, re-run, re-declare.

## 5 — `C5_gap:high` "a lead" · **label SURVIVES; the three reasons and the consequence SHOULD BE REVERSED**

The dismissal first — the chance-rate argument is the wrong test and the half-split claim is false. **3 of 13
cells fall outside the band, not 1** (`C5:high` +3.19σ, `C1:low` −2.70σ, `C5:low` −2.06σ), and a count
statistic throws away tail depth: at 4,000 shuffle draws **3 reach the observed value — p = 0.00075; Bonferroni
over 13 cells p ≈ 0.0098** — so it survives the correction the record says it never got. **It also does not fail
a half-split** — the record checked whether the cell was positive in each half, but the
declared statistic is whether it is outside its own shuffle band, and against the same-half population it is
**OUT in both**: 2016–2021 −0.0054 vs population −0.1111 (z=+2.64, 42% of configs profitable vs 0%);
2021–2026 +0.0281 vs −0.0394 (z=+3.33, 77% vs 7%). Direction-balance (363/342) is evidence against nothing, and
`C4_direction`, the declared control, correctly separates nothing (z +0.61 / −0.56) — the null machinery works.

**The part that matters more — the interpretation is backwards.** `phase0_conditioning.py` computes `gap =
open/prev_close − 1` and terciles the **signed** value. Measured: `high` = **100% gap-ups**, median +0.50%;
`low` = **100% gap-downs**, median −0.42%; `mid` = |gap| median 0.089%. Both tails are large in magnitude, so a
magnitude effect would show both beating the middle — instead `low` is the **worst cell in the study**
(−0.1399). Re-run on **|gap|** terciles: high z=+1.71, 8.8% of configs profitable, versus signed-gap high
z=+3.01, **67.9% profitable**. The signal is in the **sign** of the gap, not its size. So *"less wrong on big
days… the magnitude axis is where the conditioning lives, `dispersion.py` is its consumer"* is contradicted by
the study's own cell table; only C1 (`risk/entry`) is a true magnitude conditioner and it counts nothing on the
positive side. **Settles it:** re-run Phase 0 with `|gap|` and signed `gap` as separate conditioners first.

## Bonus — "no exit mechanism recovers a negative entry" · **not airtight**

`wide_space` is 396 **fixed** configs; "0 of 396" bounds that family only. A max over fixed policies is a lower
bound on the max over state-conditioned ones, and a tablebase is by definition the latter. The class the brief
names — behaviour that changes *which* trades complete — is the counterexample, since a zero-size position is
an exit at t=0. With **no in-cell config selection**: the config best on the full population
(`tmNone_be0.5_pf0.0_flNone_hold1`, total −28.92 R) run on gap-up days only returns **+111.96 R over 705 trades
(+0.1588/trade)**; the *median* config there returns +9.68 R with 269 of 396 profitable. Not a free lunch (the
tercile cut is in-sample and the subset was chosen after looking) but a live existence proof. Restate as *"no
fixed exit configuration recovers this entry on the full population."* That is true; the general version is not,
and the general version is what sets Phase 1's scope.

## The one most likely to be wrong

**Ruling 4, `coarse_both`.** The only one where the verdict demonstrably flips: the declared rule, applied to
the statistic it names but computed correctly, selects `full`. No judgement involved — `Tablebase.coverage()`
discards 66–75% of the transitions it claims to count, and it reproduces in one run. Everything Phase 1 fits
sits on that grid: cheapest to fix now, most expensive to inherit. The most *costly* error is Ruling 5's consequence
rather than its verdict — a signed-gap effect carried forward as a magnitude one, currently aiming Phase 1 at
`dispersion.py`.
