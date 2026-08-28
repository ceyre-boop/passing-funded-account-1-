# Macro calendar — rewrite each morning before the Opus read.
# Public information. Supplying it beats paying higher effort to recall it
# (measured 2026-08-06: medium+calendar matched high effort at 21% less cost).
# Times ET. Delete stale lines; a wrong date here is worse than a missing one.
#
# 2026-08-27: scheduled US CPI/PPI/NFP/GDP/PCE/claims and FOMC dates now come
# from machine-maintained sources (data/daytrade/macro_calendar.json, built by
# scripts/build_macro_calendar.py from FRED's release-dates API, and
# data/daytrade/fomc_calendar.json, hand-verified against the Fed's own
# calendar) — see daytrade/macro_calendar.py. This file stays ADDITIVE for
# anything those two don't carry: a single stock's earnings date, an
# unscheduled event once it's known, ad hoc notes for the morning read.

2026-08-06 (Thu)
  08:30  Initial jobless claims + Q2 nonfarm productivity / unit labor costs
  13:00  Treasury 30-year bond auction (final leg of quarterly refunding: 3y Mon, 10y Wed, 30y Thu)
  after-close  no NVDA event

2026-08-07 (Fri)
  08:30  August nonfarm payrolls  <- the week's real event; expect de-risking into it

Known ahead: NVDA earnings 2026-08-26 (after close)
