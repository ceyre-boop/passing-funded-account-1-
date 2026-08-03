#!/usr/bin/env python3
"""REGIME CLASSIFIER — spec 001. NOT BUILT. Next in the build order.

STATUS: blocked on a decision, not on effort. The ceiling measurement (spec 008,
`ceiling.py`) has run, and its numbers change what this file should be. Read
`data/daytrade/ceiling_report.json` before writing a single rule here.

WHAT THE CEILING FOUND (2026-08-03, 24 entries, 40 tune sessions, NVDA):
  - best fixed exit config      +0.083 R/trade
  - oracle (perfect hindsight)  +0.227 R/trade
  - prize available to a classifier   +0.144 R/trade  (narrow: +0.052)
  - days where ANY of 396 configs cleared +0.5R:  3 of 24
That last line is the one that matters. On 21 of 24 days no exit policy could
have made money, because the entry produced nothing to exit. The exit-side
prize is real but small and rests on 3 days.

WHAT THIS MEANS FOR THIS FILE:
  1. The pre-registered decision rule in spec 008 is NOT evaluable (it divides by
     Z, and Z came out near zero). A rule in R/trade must be re-registered by a
     human BEFORE this gets built. Do not pick a threshold now — that is the
     laundering spec 008 exists to prevent.
  2. The oracle reaches for `flatten_et` and `be_arm_frac`, essentially never for
     trail_mult. If a classifier is built, the thing worth classifying is
     "should I still be in this after 11:00?", not "how wide should the trail be."
     Spec 001's POLICY table points at the lever the oracle ignores.
  3. n=24 is far too few to trust any of this. More sessions, or a looser entry
     filter, before betting a build on it.

THE CONTRACT, when it is built (spec 001, unchanged):
    classify(bars_5m, bars_1m, ctx) -> RegimeRead
    RegimeRead(ts, symbol, regime, confidence, time_block, direction_of_flow,
               exit_policy, evidence, rule_version)
Pure function. No I/O, no clock reads — ctx carries the time. Fail loud on
malformed bars. `rule_version` bumps on ANY rule change so the scorecard never
mixes versions. Tuned on tune_sessions() ONLY (see splits.py).
"""
raise NotImplementedError(
    "regime.py is not built. Read data/daytrade/ceiling_report.json and this "
    "file's docstring first — the ceiling numbers change what should be built.")
