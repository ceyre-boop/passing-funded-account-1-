# TSMOM — pre-registered specification

Written 2026-08-27 BEFORE any backtest was run. Every constant below is fixed.
The rule has ZERO free parameters to tune. Any change to this file after the
hash below was recorded invalidates every result produced under it.

Source of the rule: Moskowitz, Ooi, Pedersen (2012), "Time Series Momentum",
Journal of Financial Economics. Standard form, no modification. Chosen because
it is the simplest systematic rule with decades of external, peer-reviewed
out-of-sample evidence across asset classes — evidence that was NOT produced
in this repo and cannot have been fitted to this repo's data.

## Rule

At the close of trading day t, for each instrument i:

    signal_i(t) = sign( P_i(t) / P_i(t - 252) - 1 )

252 trading-day lookback (12 months). Exactly zero if fewer than 253 closes.

    realized_vol_i(t) = stdev( daily log returns over the last 60 closes ) * sqrt(252)

    notional_frac_i(t) = min( VOL_TARGET / realized_vol_i(t), MAX_NOTIONAL )

Position = signal_i(t) x notional_frac_i(t), as a fraction of account equity.

## Constants

    LOOKBACK      = 252    trading days
    VOL_WINDOW    = 60     trading days
    VOL_TARGET    = 0.05   annualized, per instrument
    MAX_NOTIONAL  = 1.00   no leverage beyond 1x notional per instrument
    RESIZE_DAY    = Monday first trading day of each week (vol re-scaling only)

## Position changes — the only three events

1. ENTRY / FLIP: signal sign changes vs the currently held sign. Close the old
   position at that close, open the new one at that close.
2. RESIZE: on RESIZE_DAY, if |notional_frac(t) - held_frac| > 0.10, resize to
   notional_frac(t). Same sign. Counted as a partial close/open for costs.
3. EXIT TO FLAT: signal becomes exactly 0 (insufficient history only).

There is NO stop loss. The signal flip is the exit. This is the published rule.

## Costs — charged on every notional change

    spread (one-way, in price units, both sides charged):
      EURUSD 0.00010   GBPUSD 0.00015   USDJPY 0.010   AUDUSD 0.00012
    holding cost: swap_haircut_r_per_day from data/propfirm/firm_contracts.yaml
      cti_1step.costs (currently 0.004 R/day), applied per calendar day held,
      where 1 R = VOL_TARGET / sqrt(252) of equity (one target daily-vol unit).
      READ FROM THE CONTRACT. Never re-declared.

## R convention (for the ledger and dashboard)

    R = pnl_fraction_of_equity / (VOL_TARGET / sqrt(252))

One R is one target daily-vol move of the account. Matches the shape of
scripts/paper_carry_log.py::compute_r (pnl_frac / risk_frac - haircut).

## Universe

PRIMARY (the pre-registered test — what the firm contract can actually trade):
    EURUSD=X  GBPUSD=X  USDJPY=X  AUDUSD=X
    data: data/carry/bars/*.parquet (daily OHLC, 2016-08-22 .. present)

SECONDARY (diversification check, reported separately, informational):
    SPY EFA EEM TLT IEF GLD DBC  +  the four FX pairs
    data: yfinance daily, 10y

Equal notional budget per instrument. No instrument weighting.

## Evaluation — reported exactly like this, no other cut

    Full window                 2017-08-22 .. 2026-08-26  (first 252 days warm-up)
    Sub-window A                2017-08-22 .. 2020-12-31
    Sub-window B                2021-01-01 .. 2024-12-31
    Sub-window C                2025-01-01 .. 2026-08-26

Per window: trades, win rate, mean R, total return, annualized Sharpe (daily
returns, sqrt(252)), max drawdown (equity fraction), longest drawdown (days).

ZERO-EDGE CONTROL (mandatory, SANITY_AUDIT.md): 1000 placebo runs where each
instrument's daily signal sequence is circularly shifted by a random offset
(preserves autocorrelation, destroys alignment with returns). Report the real
Sharpe's percentile within the placebo distribution. No Sharpe is quotable
without this number beside it.

## Lookahead invariants (each needs a named test)

    I1  decide(closes, as_of) reads only closes.index <= as_of
    I2  truncating the series after as_of changes no signal at or before as_of
    I3  a position opened at close(t) earns from close(t) to close(t+1), never
        from close(t-1)
    I4  cost is charged at the bar the notional changes, never deferred
    I5  vol at t uses returns ending at t, never t+1

## One engine, zero second implementation

sovereign/trend/tsmom_engine.py exposes exactly:

    decide(closes: pd.Series, as_of: date) -> Decision
    Decision = (signal: int in {-1,0,1}, notional_frac: float, realized_vol: float)

The backtester AND the paper daily loop both import decide(). Neither
re-implements any part of the rule. This is the repo's own rule.

## What this spec does NOT promise

Published FX-only TSMOM Sharpe over 2010-2024 is roughly 0.2-0.5 net. On four
pairs it may be zero or negative over this window. If so, that is the result.
