# 039 — RATE VINTAGE LOOK-AHEAD IN THE SEALED PROOF  `[FINDING]` `[UNQUANTIFIED]`

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
