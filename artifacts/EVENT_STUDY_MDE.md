# MDE-at-Discovery — the ten pre-registered event studies

**Date:** 2026-08-31 · **Rule:** `gate/discovery.py` · **Script:** `scripts/event_study_mde.py`
**Status:** arithmetic only. Nothing was built on this table, and nothing should be
built on it without a separate ruling.

---

## The headline

**4 of 9 pre-registered primary tests clear their own detection floor.**

Yesterday's retro found **7 of 7** entry/exit leads *below* the threshold of the study
that produced them. This is a different result in kind, not in degree — and the split
is not random. It lands exactly along one line:

> **Every unsigned (magnitude) metric clears the floor. Every signed (directional)
> metric fails it.** No exceptions in either direction.

That is the first thing in this repo's history to survive the MDE rule.

---

## The table

An effect clears its detection floor iff the study could have seen it at 80% power.

```
study                      n_ev  n_ctl     |t|   ratio  cohens_d  verdict      metric
spy_macro_decay           1,009  1,667  11.430   0.22x   +0.5707  ABOVE FLOOR  abs_ret
spy_premarket             1,008  1,667   9.496   0.26x   +0.4563  ABOVE FLOOR  abs_ret
spy_fomc_double_splash       84    363   5.789   0.43x   +1.3090  ABOVE FLOOR  abs_ret
spy_range_expansion       1,056  1,578   5.199   0.48x   +0.2088  ABOVE FLOOR  true_range/ATR20
spy_whipsaw                 873  1,238   2.168   1.15x   +0.1001  BELOW FLOOR  range/endpoint
spy_pre_fomc_drift           84    334   1.049   2.37x   +0.0951  BELOW FLOOR  signed_ret
spy_bracket_harvest         860  1,218   0.983   2.53x   +0.0505  BELOW FLOOR  net_bp (P&L)
spy_second_wave             720    964   0.609   4.08x   +0.0302  BELOW FLOOR  sign()*net_return
spy_splash_continuation     964  1,461   0.085  29.27x   -0.0039  BELOW FLOOR  sign()*r_continuation
```

`ratio` = MDE / |observed effect|. **Below 1.0 = the study could see what it reported.**
Above 1.0 = the reported effect is smaller than the smallest thing that design could
have detected, so the number on record is not evidence of the effect's size.

---

## Which studies report `d`, which report raw, which report a ratio — and why it stopped mattering

You asked for this explicitly, so here it is before the conclusions.

| family | studies | the effect is in |
|---|---|---|
| **standardized `d`** | all 9 (every one stores `cohens_d`) | pooled-sd units |
| **raw difference** | all 9 (every one *also* stores `diff_event_minus_control`) | fractional return, expansion units, basis points, a floored ratio |
| **ratio / lift** | `spy_range_expansion` only (1.1207) | dimensionless, and its own file marks it `NOT_the_floor_statistic` |
| **no `d` at all** | `nvda_macro_event` | raw only, 72 separate tests |

Three incompatible unit families, exactly as the plan feared. **The conversion turned
out to be unnecessary rather than difficult**, and that is worth stating plainly rather
than hiding behind a clean table:

```
|effect| ≥ MDE = (Z₀.₉₅ + Z₀.₈₀)·SE   ⟺   |effect|/SE ≥ 2.487   ⟺   |t| ≥ 2.487
```

`t` is unit-free. Every study stores a `welch_t`. So **the ratio is `2.487/|t|`, and no
unit was converted anywhere in this table.** The three families are reconciled by not
needing to be reconciled.

### Independent confirmation of the method

`spy_range_expansion` computed its **own** MDE, ahead of this exercise, from
`mechanisms.mde` with a stored `pooled_sd = 0.49798` and `n_effective = 632.64`:

| route | ratio |
|---|---|
| its stored `mde_expansion_units` (0.049239) ÷ its `diff` (0.104002) | **0.4734×** |
| this table's `2.487 / |t|` | **0.4782×** |

Two independent methods, **1.0% apart**. Its stored `n_effective` of 632.6378 also
matches `n₁n₂/(n₁+n₂)` = 632.6378 exactly, confirming the two-sample correction the
plan called for.

---

## A real finding: `cohens_d` and `welch_t` disagree, and `d` is the wrong one

Every study computes `cohens_d` with a **pooled** sd but tests significance with
**Welch** (`equal_var=False`). Those two denominators are not the same number, and the
gap is not small:

| study | `d` reported | `d` implied by its own `t` | inflation |
|---|---|---|---|
| spy_fomc_double_splash | 1.3090 | 0.7010 | **1.87×** |
| spy_macro_decay | 0.5707 | 0.4559 | 1.25× |
| spy_premarket | 0.4563 | 0.3789 | 1.20× |
| spy_whipsaw | 0.1001 | 0.0958 | 1.04× |
| spy_pre_fomc_drift | 0.0951 | 0.1281 | 0.74× |

The inflation is **largest where the event is most violent** — FOMC, 1.87×. That is not
a coincidence: event days have higher variance than control days *by construction*, so
the pooled sd sits below the true standard error and `d` overstates the effect exactly
when the event matters most.

**Consequence:** an MDE computed against `cohens_d` would have reported FOMC at ~0.23×
instead of 0.43× — nearly twice as far above its floor as it really is. The MDE must be
computed in the metric of the test that was actually run. **Every ratio in this table
uses `t`, not `d`.** The `cohens_d` column is printed for reference only.

This is a defect in how the studies report, not in their conclusions — the *p*-values
were always Welch and were always right. But `cohens_d` should not be quoted from these
files as an effect size without this correction beside it.

---

## `nvda_macro_event` — the trap the rule caught

72 tests, no pre-registered primary. Its best arm looks like a pass and is not one:

- best of 72: **CPI / `range_pct`**, n = 32/435, |t| = 2.918
- naive `2.487/|t|` = **0.85× — would read as ABOVE FLOOR**
- but the max of 72 tests needs a multiplicity-adjusted floor: |t| ≥ 3.20 + 0.842 = **4.04**
- adjusted ratio = **1.38× → BELOW NOISE FLOOR**

Its own record agrees: 9 raw-significant against 3.6 expected by chance, and **0
surviving BH correction**. A multiple-comparison survivor and a below-floor effect are
different failures; this one is both, and the naive ratio would have hidden it.

## Excluded from the ranking, and why

5 robustness variants and 72 `NO_INFERENTIAL_WEIGHT` / `EXPLORATORY` tests were kept out
of the table. This matters more than it sounds: `spy_splash_continuation`'s exploratory
horizon sweep contains a **|t| = 12.856** — vastly larger than anything in the ranked
table — sitting inside a study whose pre-registered verdict is NULL at |t| = 0.085.
`spy_range_expansion` carries a sub-block literally named
`CONFOUNDED_raw_range_never_primary`. Ranking on the best available `t` rather than the
pre-registered one is the selection error this rule exists to catch.

Range expansion's three genuine robustness variants all hold: 0.49× (RTH denominator),
0.64× (±1-session buffered control). The finding is not an artifact of one denominator.

---

## The second floor — and the warning that comes with it

Detection and economics are different questions, and this repo already owns the worked
example of passing one and failing the other.

`spy_range_expansion` clears detection at **0.48×** and then fails its own
pre-registered economic floor:

> `economic_floor: 1.1` · `clears_economic_floor: false`
> **"SIGNIFICANT AND USELESS** — the expansion ratio is below the pre-registered 1.10
> economic floor and cannot move a profit target or an ORB threshold beyond noise and
> slippage."

It is the **only** one of the four survivors that declared an economic floor in advance.
The other three — `macro_decay`, `premarket`, `fomc_double_splash` — never stated what
magnitude would be large enough to trade, so **they have not passed a tradability test;
they have not taken one.**

`spy_bracket_harvest` is the other half of this warning. It is the one study that
measured **P&L in basis points** rather than a statistical effect, and it is
**2.53× below floor** with a headline of `NULL`. The single study that asked "does this
make money" could not detect an answer.

---

## What this table does and does not license

**Does:** the magnitude axis has a detectable subject. Volatility around scheduled
macro events is real, large, and measured at 0.22–0.48× of the detection floor across
three independent windows (08:30–08:35, 08:30–09:00, 14:00–14:05) and n over 1,000 days
for two of them. This is not the seven-below-floor pattern. Something is there.

**Does not:** license a single line of code.

1. **Magnitude is not direction.** All four survivors measure *unsigned* movement. All
   three directional studies are below floor — 2.37×, 4.08×, 29.27×. Knowing a day will
   be big is not knowing which way it goes. The three studies that tried to convert
   magnitude into direction are the three that failed hardest.
2. **The one study that priced its own finding called it useless.** Range expansion
   cleared detection and failed economics. That is the specific, pre-registered,
   on-the-record outcome for the magnitude axis when someone asked whether the effect
   was big enough to move a target.
3. **The one study that measured money found nothing.** Bracket harvest, 2.53× below.
4. Any use of these effects needs its **own** pre-registered economic floor, declared
   before the fit, in the units of the decision it would drive (stop distance, target
   distance, size). Detection is necessary, not sufficient.

## Reproducibility gap

`spy_macro_decay` — the **strongest** result in the table at 0.22× — has **no
`*_days.csv` on disk.** Its script names `DECAY_CSV`/`BREATH_CSV_TEMPLATE` as targets,
but neither file was persisted. Its `t` and `n` cannot be independently recomputed from
committed data, only read from the summary it wrote about itself. None of the other
per-day CSVs carry an event/control flag either, so no study's split is currently
reproducible from committed artifacts alone. That is a gap worth closing before anything
is built on the magnitude axis — the top result is the least verifiable one.

---

## Verification

```bash
python3 scripts/event_study_mde.py     # this table
python3 -m pytest gate/ az/ -q         # 58 passed, unchanged
```

No file under `gate/`, `az/`, `daytrade/`, `specs/`, or `data/` was modified.
