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
