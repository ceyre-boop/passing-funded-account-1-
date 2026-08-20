# 032 — FX STATE VECTOR `[SPEC]`

**Component:** `daytrade/fx_state.py` (to be built), `data/carry/fx_state.jsonl`
**Status:** `[SPEC]` — written 2026-08-20, AFTER `031` was committed and
sealed. That ordering is load-bearing: the re-registration exists first, so
this instrumentation cannot become a way to defer the decision it depends on.
**Depends on:** 030 (ledger shape, hash chain, live/backfill honesty), 029
(mechanisms measured against state), 021 (the carry lane and its contract).

## Why

The machinery reads five-minute equity bars and is graded on six-day FX carry
positions. No amount of learning survives an input/outcome mismatch — the
gradient has nowhere to flow. This is not a rewrite of the machinery; it is a
rewrite of **the dictionary the machinery reads**, in the units carry actually
experiences.

Everything downstream — decision ledger, mechanism ledger, ontology audit, and
whatever meta layer eventually sits on top — is currently describing a market
the system never enters. This card fixes the input, and nothing else.

## Feasibility, verified before specification (not assumed)

| field | source | status |
|---|---|---|
| daily FX bars, 4 pairs | yfinance `EURUSD=X` etc. | **verified** — daily bars current to 2026-08-20 |
| US policy rate | FRED `DFF` | **verified** — 3.63 @ 2026-08-19 |
| EUR policy rate | FRED `ECBDFR` | **verified** — 2.25 @ 2026-08-20 |
| GBP rate | FRED `IUDSOIA` (SONIA) | **verified** — 3.7311 @ 2026-08-18 |
| JPY rate | FRED `IRSTCI01JPM156N` | **verified** — 0.841 @ 2026-06-01 (monthly, lags) |
| AUD rate | FRED `IR3TIB01AUM156N` | **verified** — 4.46 @ 2026-06-01 (monthly, lags) |
| swap accrual | `firm_contracts.yaml` `swap_haircut_r_per_day: 0.004` | **verified** — the contract, never re-declared |

Two honest limits recorded up front: **JPY and AUD series are monthly and lag
~2 months**, so `rate_diff` for USDJPY and AUDUSD carries a staleness flag and
its own `as_of`, never a forward-filled pretence of currency. Central-bank
*calendar* proximity has **no verified free source yet** and is specified below
as `null` until one exists — absent, stated, never guessed.

## The state vector — one row per (pair, date)

Point-in-time discipline is 030's, unchanged: nothing newer than `as_of`
enters, `max_data_ts` is recorded, rows are hash-chained, and `source` is
`live | backfill` and never conflated.

```
pair · as_of · session_date · source · max_data_ts

--- the carry itself, in its own units ---
rate_base            policy/overnight rate of the base leg, with its own as_of
rate_quote           same for the quote leg
rate_diff            base - quote (the carry)
rate_diff_stale_days max staleness of the two legs (JPY/AUD run ~60)
rate_diff_d5         change in rate_diff over 5 sessions
rate_diff_d20        change over 20 sessions

--- what holding actually costs ---
swap_accrual_r_per_day   from the contract (0.004), never re-derived
days_in_position         0 when flat; the position's own clock
swap_accrued_r           days_in_position * swap_accrual_r_per_day
weekend_next_session     True if the next session is across a weekend
weekends_crossed         count so far in this position
weekend_exposure_r       weekends_crossed * 3 * swap_accrual_r_per_day

--- price state, on DAILY bars (not 5-minute) ---
close · atr14_pct · realized_vol_20d_pct
trend_pct_vs_ma20 · trend_pct_vs_ma50
drawdown_from_20d_high_pct

--- event risk ---
cb_calendar_days_to_next   null until a verified source exists (stated absent)
positioning_extreme        null until a verified source exists (stated absent)
```

## The regime vocabulary starts EMPTY — and that is the point

The equity ontology asserted nine labels; the audit found six were decoration
and two were the same label twice. That mistake is not repeated here.

**No FX regime label is defined in this spec.** State is recorded numerically.
A label enters the vocabulary only by passing `ontology_audit` — it must
separate outcomes better than a random partition of the same size — and it is
retired the moment it stops doing so. Candidate carvings will be *proposed
from the recorded state*, not asserted by whoever writes the next spec card.

This is the growth mechanism the meta layer needs, applied at the point where
it costs nothing: the dictionary is built from measurement rather than from
opinion.

## Invariants

- I41: no row contains data newer than its own `as_of`.
- I42: a stale rate leg is flagged with its true staleness, never forward-
  filled into an implied freshness.
- I43: unavailable fields are `null` with the absence stated; never zero,
  never a proxy silently substituted.
- I44: `swap_accrual_r_per_day` is read from the firm contract and never
  re-declared in this module.
- I45: the vocabulary is empty until a label earns entry via `ontology_audit`;
  no label may be defined in code without an audit row.
- I46: hash chain intact (030's guard, same shape).

## Explicitly out of scope

Trading anything; the mechanism/meta layers; deleting or rewriting the
equity-lane ledger (it stays as history and as the worked example of a
vocabulary that failed its audit); any change to the carry gate — that lives
in `031` and is Colin's to ratify.

## The sequencing check this card is subject to

The review that prompted this named the risk precisely: none of this needs a
live trade, which makes it exactly the kind of real work that defers exposure.
`031` was written, committed, and sealed before this card was drafted. If
implementation of `032` begins while `031` sits unratified, the pattern won —
and that sentence is in the spec so a future session has to read it.
