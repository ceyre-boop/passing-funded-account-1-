# The exit ceiling on 4,512 trades — numbers of record

Established 2026-08-29 by `scripts/ceiling_10y.py`, which calls
`ceiling.policy_ceiling` and `ceiling.report` **unmodified**. Only the population
changed. Nothing after `splits.TUNE_END` (2026-07-06) was read; `sealed_sessions()`
was never called.

## THE GUARD — read this before quoting any number below

**The median config is the number of record. The best config is not a result.**

`Z_best_fixed` is a max over 396 correlated configs. On NVDA the best config
returns +0.0583 R/trade while `best − median` is **0.0727** — the selection spread
is larger than the entire apparent edge, and the best sits above p95 of the config
distribution. That is the same `null_leak` already measured at 1.74 R against a
0.15 gate (`data/daytrade/oracle_audit.json`), reappearing independently on 88×
the data.

NVDA's +0.0583 will look like validation later, when exit work is hard and that
number is sitting there. It is p95 of a distribution whose median is negative.
Quoting it as edge is relitigating a question this file closes.

## The population

| sym | cache | span | sessions | entries |
|---|---|---|---|---|
| SPY | bars_premarket | 2016-01-04 → 2026-07-02 | 2,618 | 2,115 |
| QQQ | bars_premarket | 2016-01-04 → 2026-07-02 | 2,614 | 1,996 |
| NVDA | bars_extended | 2024-01-02 → 2026-07-02 | 627 | 401 |

Session construction mirrors `bars.load_sessions`, except that gapped sessions are
**excluded and counted** rather than raising — which is what that function's own
docstring says it does, and what its code does not. Bars are never interpolated.
SPY excluded 1 half-day and 20 gapped; NVDA excluded 0 and 0.

## What replicates

**Days on which SOME config wins: 31.4% (SPY) · 32.7% (QQQ) · 33.9% (NVDA).**
Three instruments, two disjoint periods, 4,512 entries. The prior figure of
3-of-24 (12.5%) from `data/daytrade/ceiling_report.json` was a 24-session
artifact and is superseded. **31–34% is the number.**

## What does not replicate — and the decisive result

| sym | Z/trade (best fixed) | configs profitable | median/trade | best − median |
|---|---|---|---|---|
| SPY | −0.0137 | **0 of 396** | −0.0741 | 0.0604 |
| QQQ | +0.0284 | — | — | — |
| NVDA | +0.0583 | 26% | −0.0144 | 0.0727 |

**On SPY, zero of 396 exit configurations are profitable over 2,115 trades.** Not
the best; none. No exit policy in the space rescues the opening-range-break entry
on that instrument. That is a verdict on the entry.

The best-fixed configs also disagree across instruments — NVDA selects
`be1.0_fl11:00_exitTP2` (flatten 11:00, take TP2); SPY and QQQ select
`tmNone_be0.5_pf0.0_*_hold1` (no trail, no partial, ride past TP2). There is no
global fixed policy; there is a different max-of-396 per symbol.

Two independent confirmations fall out: `tmNone` on both index instruments
re-confirms **MECH-001** (trailing does not help) on 88× the prior sample, and the
`best − median` spread re-confirms **MECH-004** / the `null_leak` finding.

## What this does NOT say

It does not say exits are worthless — it says exit-config *selection* is, and that
this entry cannot be rescued by exit choice on SPY. It says nothing about a
different entry, and nothing about magnitude-native expressions of the same
signal, which is the open question (`dispersion.py`).

Verdict from `ceiling.report` on all three: `NO_VERDICT — pre-registered rule is
not evaluable`, because spec 008's "% of Z" divides by a Z that is genuinely near
zero. On 24 sessions that was a possible small-sample excuse. On 4,512 trades it
is a property of the population.
