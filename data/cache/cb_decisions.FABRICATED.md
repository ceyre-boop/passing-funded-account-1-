# cb_decisions.json — FABRICATED, quarantined 2026-08-27

This file (1201 entries) was presented as central-bank policy decisions and
consumed by `sovereign/forex/entry_engine.py`'s `CBEventTrigger` (Edge 3 —
Post-Decision Drift). It is not what it claims to be.

## What it actually is

The step-difference of *market* rate series (SONIA, effective fed funds,
monthly OECD proxies), mislabelled as central-bank policy decisions.

## Evidence

- 47.7% of `actual_change_bps` values are +-1bp; only 29 of 1201 are +-25bp
  — real policy moves cluster at round numbers (25bp, 50bp), market-rate
  noise does not.
- 16.5% of entries fall on weekends. Central banks do not announce policy
  decisions on weekends.
- FED/BOJ/RBA: all 313 of 313 entries are dated the 1st of a month — a
  signature of a monthly-resampled proxy series, not a decision calendar.
- 83.6% have `expected_change_bps == 0`, which makes
  `surprise_bps === actual_change_bps` by construction — there was never a
  real "expected" value to compare against.
- Only the 24 ECB records are consistent with a genuine policy-step series
  (ECBDFR is real). The other seven banks' entries are not.
- Its named builder, `scripts/build_cb_decisions.py` (referenced in
  `entry_engine.py`'s docstring and in the file's own provenance), has never
  existed anywhere in this repo's git history. The file cannot be
  regenerated because nothing ever generated it in a reproducible way.

## Decision

The owner decided to cut the CB-surprise layer rather than attempt to
rebuild it. The 24 real ECB records were NOT salvaged — that is a separate
future decision, not made here. This file is quarantined, not deleted, so
the evidence trail and the option to revisit stay intact.

`sovereign/forex/entry_engine.py`'s `CB_LAYER_DISABLED = True` is the single
source of truth for the disabled state; `scripts/carry_scan.py`'s
`preflight()` reads it from there and reports the layer as deliberately off
rather than as a missing-input failure.

## Known downstream impact

Approximately 102 of the 411 sealed trades in
`data/proof/backtest_trades_v015_2015_2024.csv` had entries falling inside
entry windows this file opened. Those trades are part of the sealed record
and are not modified by this quarantine — this note exists so a future
review of that record's provenance does not have to rediscover this fact.

## Path

- Quarantined artifact: `data/cache/cb_decisions.json.FABRICATED`
- Original path (now unused): `data/cache/cb_decisions.json`
