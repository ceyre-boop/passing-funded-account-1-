# Pre-registration — low-priced / high-RVOL NASDAQ screen base rate

**Written 2026-09-02, BEFORE any result was computed.** Frozen. Any change to
the screen or the outcome definition after a result is seen invalidates the
run and requires a new prereg with the trial counted.

Origin: a live 0/10 on premarket mover prediction (2026-09-02). The post-mortem
proposed filters (cap <$500M, float <20M, RVOL >5x) that were reverse-engineered
from three winners. This measures the denominator those filters never had.

## The question

Of every NASDAQ name that looked like a candidate at yesterday's close, what
fraction actually ran, what did the median one do instead, and what did the left
tail cost?

## Universe (point-in-time, survivorship-free)

- NASDAQ-listed (`primary_exchange = XNAS`), Polygon `type = CS` (common stock).
- **Includes delisted names.** Membership on date D is determined by the tape,
  not by today's listings: a ticker is in the universe on D iff it has a daily
  bar on D-1 and has not been delisted before D. Bars are ground truth for
  tradability.
- **Excluded:** warrants, units, rights, preferreds. Enforced twice — by
  `type = CS` and by suffix rejection (`W`, `WW`, `WS`, `U`, `R`, `P`, and any
  ticker over 4 chars ending in W/U/R). Rationale: a 2026-08-21 spot check found
  the top of the raw %-gainer list was sub-penny warrants (CINGW 0.005 -> 0.009
  = "+71.7%"), where the percentage is one tick of spread, not a move.

## Screen — all inputs knowable at the close of D-1

| # | Filter | Threshold |
|---|--------|-----------|
| S1 | prior close | **$1.00 <= close(D-1) <= $10.00** |
| S2 | prior dollar volume | **close(D-1) x volume(D-1) >= $1,000,000** |
| S3 | relative volume | **volume(D-1) >= 3.0 x median(volume, D-21..D-2)** |

$1.00 floor excludes tick-artifact names. $1M dollar-volume floor excludes names
that cannot be filled at any size. RVOL uses median (not mean) so one prior spike
does not set the bar.

## Outcome — measured on D

**Primary — "ran":** `high(D) >= close(D-1) x 1.20` (intraday high at least +20%
over the prior close). Binary.

**Recorded per candidate-day regardless of the flag:**

| field | definition |
|---|---|
| `gap` | `open(D)/close(D-1) - 1` |
| `high_r` | `high(D)/close(D-1) - 1` |
| `close_r` | `close(D)/close(D-1) - 1` |
| `low_r` | `low(D)/close(D-1) - 1` |
| `mae_from_open` | `low(D)/open(D) - 1` — worst adverse move for an open-entry |
| `range_pct` | `(high(D)-low(D))/close(D-1)` |
| `wide_tape_flag` | `range_pct > 0.40` — **proxy** for halt / spread blowout. Daily bars carry no halt record; this is a flag, not a halt log, and is labelled as such wherever it is reported. |

## Split — sealed holdout

- **Development half: 2016-01-01 -> 2021-12-31.** Iteration allowed here.
- **Sealed half: 2022-01-01 -> 2026-08-31.** Untouched until the rule is frozen,
  then run **exactly once** as the verdict.
- **Per-year hit rate is reported alongside the pooled number, always.** A screen
  that only worked in 2020-21 is a museum piece; pooling would hide that.

## Reporting rules (repo standing format)

- Every rate ships with its n.
- Absolute values beside ratios.
- The median non-runner outcome is reported beside the hit rate — a hit rate
  without the cost of the misses is not a result.
- If every arm is negative, the header says so.

## Anti-criteria

- **Anti:** no threshold in S1-S3 or in the "ran" definition is adjusted after a
  result is seen. Trial count carried if it is.
- **Anti:** the sealed half is not read, plotted, or summarised before the freeze.
- **Anti:** no fill assumption is made from daily bars. Stage two (the narrated
  entry, minute bars) runs only inside the ex-ante true-positive cohort, and
  fillability is treated as unknown until modelled.

## Data provenance

- Universe + delisting: Polygon `/v3/reference/tickers` (free tier serves
  delisted names).
- Daily bars 2016-> : Alpaca SIP (`feed=sip`), verified to serve delisted
  tickers — SIVB minute bars on 2023-03-09 and ATVI 2023 both returned.
- Polygon aggregates are free-tier limited to ~2 years and are used only for the
  60-session whole-market cross-check, never for the decade.
