# 039 — RATE VINTAGE LOOK-AHEAD IN THE SEALED PROOF  `[FINDING]` `[QUANTIFIED 2026-08-26 — EDGE SURVIVES]`

**Found 2026-08-26 while investigating why AUDUSD/USDJPY carry legs read 81 days
stale. The staleness was the symptom. This is the disease, and it is upstream of
the sealed 411-trade proof set.**

Status: reported, NOT fixed. Fixing requires re-running the sealed backtest,
which is a ruling, not a refactor. Colin decides.

---

## The mechanism

`sovereign/forex/signal_engine.py:522-523`:

```python
b_rate = float(base_rates.asof(date)) if ... else FALLBACK_RATES.get(...)
q_rate = float(quote_rates.asof(date)) if ... else FALLBACK_RATES.get(...)
```

`.asof(date)` is point-in-time with respect to the observation's **nominal
date**. It is NOT point-in-time with respect to the observation's **publication
date**. For a daily series those coincide. For a monthly series published in
arrears they do not, and the gap is the look-ahead.

Measured live against the FRED API on 2026-08-26:

| series | currency | freq | obs_end | last_updated | publication lag |
|---|---|---|---|---|---|
| `IRSTCI01JPM156N` | JPY | **M** | 2026-06-01 | 2026-07-16 | **45 days** |
| `IR3TIB01AUM156N` | AUD | **M** | 2026-06-01 | 2026-07-16 | **45 days** |
| `ECBDFR` | EUR | D | 2026-08-26 | 2026-08-26 | 0 days |
| `IUDSOIA` | GBP | D | 2026-08-24 | — | ~0 days |

So a backtest evaluating 2026-06-15 calls `.asof(2026-06-15)`, receives the
2026-06-01 observation, and that observation **did not exist until 2026-07-16.**

## Why it matters

`sovereign/forex/signal_engine.py:46`:

```python
rate_weight: float = 0.50   # real rate differential (carry) component
```

**Half the signal weight.** Plus `swap_model.ratediff_financing_rate` varies the
financing model by "the FRED rate-differential change since the snapshot date",
so the cost model inherits it too.

Affected: any pair whose leg uses a monthly OECD MEI series — **AUD, JPY**, and
latently CHF/CAD/NZD (`IR3TIB01{CH,CA,NZ}M156N`, same family, same lag). Two of
the four pairs in the sealed book. EUR and GBP legs are clean.

## What this does NOT say

- It does **not** say the edge is fake. It says a known bias exists on half the
  signal weight for half the book, of **unquantified** magnitude.
- It does **not** say `.asof()` is a coding error. It is the correct call for a
  daily series and the repo's own convention elsewhere. The defect is applying
  it to a series whose publication calendar differs from its observation
  calendar.
- It is **separate from**, and compounds with, the already-logged
  `PAIR_VIX_GATES` in-sample tuning exposure (`NEXT.md`). Both inflate the same
  Sharpe from different directions.

## Why the staleness "fix" was about to make this worse

The investigation that found this was scoped to make AUD/JPY live rates fresher
(FRED monthly → RBA/BOJ same-day, 81 days → ~14). That would have **increased**
backtest-vs-live divergence, not reduced it: the backtest would keep reading
45-day-early monthly values while live read same-day central-bank targets. The
live system would then be running on inputs the sealed proof never saw.

**The current 81-day live staleness is, perversely, closer to honest** than a
fresh live feed would be, because it at least resembles the lagged information
set the backtest was built on. It is still wrong — the backtest gets the value
45 days early and live gets it 45 days late — but the fix order matters.

## The right order

1. **Quantify it.** Re-run the v015 backtest with the rate series shifted
   forward by their real publication lag (or, better, against FRED's ALFRED
   vintage endpoint, which serves what was actually knowable on a given date).
   Compare against the sealed 411. The delta is the answer.
2. **Then** decide the live data source, knowing what the honest backtest says.
3. Do not touch `data/proof/` — sealed. A re-run produces a NEW series beside
   it, per `specs/033`'s pinned-opponent discipline.

If the edge survives step 1, the live-source question becomes a straightforward
freshness upgrade. If it does not, no amount of live data quality matters.

## Invariant to add once resolved

- **I56** — any rate series used by the signal must be read at its
  publication-date vintage, not its observation-date nominal. A monthly series
  read with `.asof()` on the nominal date fails this.

## Provenance

Established by direct FRED API query on 2026-08-26 (`frequency_short`,
`observation_end`, `last_updated`), not from documentation or memory. The
`rate_weight = 0.50` figure is `SignalConfig`'s default; whether the sealed run
used the default is **not yet verified** and is step 0 of any quantification.

---

# RESOLVED 2026-08-26 — the edge survives, and the finding was wider than written

Measured with ALFRED realtime vintages (`realtime_start=1776-07-04`), not a lag
approximation — lags are not constant (JP/AU first-appearance: median 43d, p10
31d, p90 144d) and only ALFRED handles revisions.

## Two corrections to this document, both WIDENING it

1. **`FEDFUNDS` is monthly too** — 31-day median publication lag. Every sealed
   pair has a USD leg, so this touched **4 of 4 pairs**, not 2. The claim above
   that "EUR and GBP legs are clean" is true of the LEGS and false of the PAIRS.
2. **CPI is contaminated on the same term.** `real_rate_diff =
   (b_rate − b_cpi) − (q_rate − q_cpi)`, all monthly/quarterly, all read
   `.asof(nominal)`. Mean |nominal − vintage|: US 0.37pp, EU 0.44pp, UK 0.53pp,
   AU 0.75pp — larger than the rate errors. With `irp_weight` also using the
   rates, **100% of the macro score was contaminated, not 50%.**

## The result

| arm | n | avg R | WR | Sharpe |
|---|---|---|---|---|
| SEALED 411 (untouched) | 411 | +0.3556 | 48.66% | 1.072 |
| rig, nominal vintage (look-ahead) | 288 | +0.3226 | 50.00% | 0.913 |
| **rig, publication vintage (honest)** | 292 | **+0.3049** | 48.97% | **0.874** |

avg R falls 0.018 against a per-arm standard error of ±0.083. The paired test on
276 matched trades is **positive** (+0.0134, 5–95% [−0.021, +0.058]).
Direction-permutation null (2000 draws) p(observed) = 0.0000 in both arms.

**Signal level, population-independent — the stronger result:** 480 pair-months,
**zero sign flips**, 89.6% identical. That is the mechanism and it is hard to
vary: a rate differential is slow and persistent, so shifting it 30–45 days
moves which side of the 0.15 threshold a marginal month lands on and **never
which sign it has.** A 100%-contaminated input costs ~10% of signal months and
~0% of directional accuracy.

## What this does NOT rescue

- `v015_manifest.json`'s own fresh 2025–26 remeasurement: **Sharpe 0.038, CI
  [−0.26, 0.34], UNDETERMINED.** Untouched by this.
- The `PAIR_VIX_GATES` in-sample exposure. Untouched.
- Measured on 288 trades reproducing 285 of the sealed 411 — if the 126 missing
  trades are disproportionately macro-driven, this understates the effect. The
  signal-level table is the hedge against exactly that.

## I56, now enforced

`signal_engine` in a vintage-controlled run EXCLUDES and COUNTS a NaN date;
never fills from `FALLBACK_*`. 2 dates excluded (EU CPI 2015-01-01, 2015-02-02,
before ALFRED coverage). Mutation-verified: disabling the guard fails 1 test,
wiring `publication` at the nominal tree fails 6.

## A SECOND look-ahead this document did not name

`swap_model.ratediff_financing_rate` anchors to `series.dropna().iloc[-1]` — the
**2026** value — for every 2015 trade. Identical in both arms so it does not
affect the delta above, but it is a straight look-ahead in the sealed proof's
cost model and it is still there. **I57**: a cost anchor must be as-of the trade,
not as-of the series end.

## Live-source ruling (recommendation, not implemented)

**Stay FRED-only.** The honest backtest's information set is a 31–45-day-lagged
monthly rate; same-day CB targets would give live a set the backtest has never
been tested against, in the direction with no evidence behind it. The signal is
threshold-marginal, so fresher inputs move ~10% of months across the line
unvalidated.

**Do close the cheap gap:** live is ~81 days stale vs the backtest's ~45. That
difference is `CACHE_DAYS = 30` stacked on the publication lag — a config value,
not a data-source problem. A daily refresh aligns live to the backtest's own
information set with no new dependency. Colin's call.

If same-day CB targets are ever wanted the order is: backfill to 2015 → run as a
THIRD arm against the publication baseline → then decide. Never a live-only
upgrade.
