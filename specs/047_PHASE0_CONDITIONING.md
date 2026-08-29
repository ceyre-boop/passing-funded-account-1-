# 047 — PHASE 0: does ANY conditioner make this entry positive?  `[SPEC]` `[PRE-REGISTERED]`

Written 2026-08-29, before the sweep ran. Population and harness are those of
`artifacts/CEILING_10Y_RECORD.md` (commit 73fb8ce), unchanged.

## The two questions

1. **Direction.** On SPY, 0 of 396 exit configs are profitable over 2,115 entries.
   Does conditioning on state make any subset positive?
2. **Magnitude.** If direction stays null everywhere, does the same population
   show conditionable *dispersion*? A dispersion bet needs no sign, and FOMC at
   d=1.31 says that axis is real.

Both are answered by one sweep, because the cells are the same.

## THE STATISTIC IS THE MEDIAN CONFIG

Per cell: run all 396 configs over that cell's entries, take the **median** config
total, divide by cell n. The best config is never the statistic — that is the
guard in `CEILING_10Y_RECORD.md` and this spec inherits it. `best − median` is
reported alongside so the selection spread stays visible.

## Declared partition — MARGINAL ONLY, no crossing

2,115 entries against `state_space.audit`'s own `min_paths=30` affords roughly
70 cells. Crossing four conditioners would blow that, so each is tested
**separately** against the same population. A 2-way cross is a follow-up card,
permitted only if a marginal cell counts.

| # | conditioner | levels | source, as-of at entry |
|---|---|---|---|
| C1 | opening-range width / price | terciles | `Entry.risk / Entry.entry` — scale-free, the magnitude quantity itself |
| C2 | FOMC proximity | none · ±1 day · day-of | `data/daytrade/fomc_calendar.json`, 89 dates, full span |
| C3 | time block | `Entry.time_block` levels present | `ceiling.time_block` |
| C4 | direction | long · short | `Entry.direction` — control |
| C5 | gap vs prior close | terciles | prior session close, computed from consecutive sessions |

Terciles are cut on the full population before splitting. A level holding fewer
than 30 entries is reported and **excluded from the decision**, never merged.

## Null — label shuffle, 1,000 draws, seed 20260829

Per-cell statistics are compared against a null that permutes the conditioner
labels across entries, preserving every marginal and destroying only the
state→outcome link. A cell **counts** only if its median-config R/trade falls
outside the 5–95% band of that null. No parametric p-values: 396 correlated
configs and 2,115 entries make any t-test meaningless here.

## Magnitude readout, same cells

Per cell, `(W_oracle − anti_oracle) / n` — the span between the best and worst
achievable outcome per trade. Direction-free by construction. If this varies
outside its own shuffle band while median-config R/trade does not, that is
direction-null with magnitude-conditionable, and `dispersion.py` is the
consumer.

## Decision rule, fixed before the run

- **DIRECTION LIVES** — some cell's median-config R/trade is positive and outside
  the null band. Then Phase 1 tunes exits against that conditioned entry.
- **DIRECTION DEAD** — no cell counts. Then Stockfish is built as a general
  position manager, not tuned against an entry it cannot save, and the entry
  question moves to a magnitude-native expression.
- **MAGNITUDE CONDITIONABLE** — the span statistic counts in cells where R/trade
  does not.

Prediction, recorded before running: **direction dead, magnitude conditionable
on C1 and C2.** Nothing is re-run with a changed conditioner; a different
partition is spec 048.

---

# RESULT — 2026-08-29, SPY, 2,115 entries × 396 configs

`artifacts/phase0_SPY.json`. The rule as written fires **DIRECTION LIVES** on one
cell. It should not be acted on as a directional finding, for three reasons given
below — and the pre-registration above is defective in a way that this result
exposes. Both are recorded rather than repaired.

## What the rule returned

**DIRECTION LIVES — `C5_gap:high`** — n=705, median-config **+0.0137 R/trade**
against a null band of [−0.1192, −0.0287], and **68% of 396 configs profitable**
versus 0% on the full population.

**MAGNITUDE CONDITIONABLE** — 4 cells outside their span null: `C3:MORNING`
(1.255, high), `C3:OPEN_DRIVE` (1.029, low), `C5:low` (0.967, low), `C5:high`
(1.264, high).

## Why the directional reading fails anyway

1. **It is exactly the chance rate.** 13 cells were tested against a 5–95% band,
   so ~1.3 cells are expected outside by chance. Exactly 1 was.
   **Spec 047 declared no multiple-comparison correction. That is a defect in
   this pre-registration**, not a property of the data, and it is why the cell
   cannot be promoted on this run.
2. **It does not survive a half-split.** 2016–2021: −0.0093 R/trade, 34% of
   configs profitable. 2021–2026: +0.0321, 78%. The positive result lives
   entirely in the second half. No stability requirement was declared either.
3. **The cell is direction-balanced** — 363 long, 342 short. Whatever separates
   it is not a directional signal.

## What is actually there, and it is the stronger result

Both conditioners that separate are **magnitude** conditioners, and both are
**monotone**:

| level | C5 gap median/tr | cfg>0 | C1 OR-width median/tr | cfg>0 |
|---|---|---|---|---|
| low | −0.1399 | 0% | −0.1574 | 0% |
| mid | −0.1082 | 0% | −0.0398 | 3% |
| high | **+0.0137** | **68%** | −0.0310 | **26%** |

Two independent variables, ordered gradients on both, and the high-gap cell is
spread evenly across eleven years (22–45% of each year's entries; not a 2020
artifact). A single false positive does not arrive with a monotone gradient on
two separate conditioners.

**Reading: the entry is not less wrong in a direction — it is less wrong on big
days.** That is dispersion, not sign, and the cell where "direction lives" was
selected by asking how large today is, not which way it goes.

## Prediction vs outcome

Recorded before the run: *"direction dead, magnitude conditionable on C1 and C2."*
- Magnitude conditionable: **correct**, though it landed on C3 and C5, not C2.
  FOMC (C2) separated nothing — `day_of` n=65 is under-powered and its null band
  is enormous ([−0.2607, +0.1122]).
- Direction dead: **the rule says otherwise**, and the honest answer is that the
  rule was too weak to ask the question. Not a vindication of the prediction.

## Consequence

`C5_gap:high` is a **lead, not a result**. Promoting it requires a fresh
pre-registration with a declared multiple-comparison correction and a stability
requirement, tested on data this run did not consume. Everything after TUNE_END
is still sealed and is the natural place for it.

Phase 1 direction unchanged: build Stockfish as a general position manager. The
magnitude axis is where the conditioning lives, and `dispersion.py` is its
consumer.
