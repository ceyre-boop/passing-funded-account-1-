# Walk-forward analysis — DEFERRED, with the evidence

> Supersedes this file's previous contents (the splash-loop plan), which was
> executed and is preserved in git at commit `9f3b014`. Read it there.

## Context

A proposal arrived to build a walk-forward analysis pipeline: rolling
in-sample/out-of-sample windows, a boundary generator, purge/embargo between
train and test, and stitched rather than averaged OOS equity curves.

**The method is correct. It is being deferred because this repo has almost
nothing to walk forward.** Two read-only recon passes established the facts
below. This document exists so the proposal is not re-raised without them.

Decision made 2026-08-22 by Colin: **build nothing.**

## Why — what the recon actually found

### 1. Almost nothing here fits parameters

| component | status |
|---|---|
| `daytrade/alphazero_bias.py` | HAND-SET — three literal word lists (`:25-29`), self-labelled `"keyword-valence v1 (placeholder, unvalidated)"` |
| `daytrade/news_claude.py` + `alpha_operator.py` | NO FITTED PARAMETERS — a model call, a prompt, and hand-set constants (`FLAT_BAND = 0.0015`, no provenance) |
| `daytrade/forecast.py` promotion gate | HAND-SET — eight thresholds as dataclass defaults (`:279-287`); **no provenance for seven of the eight** |
| `daytrade/stockfish_exit.py` `INTENT` | HAND-SET, *informed by* the 24 oracle picks (`:288-291`), not fitted to them |
| `sovereign/risk/kelly_engine.py` | NOT FITTED — static priors (55% WR, 2:1) plus Hoeffding shrinkage; no caller ever passes real history |

Walk-forward answers "did my fitted values survive data they never saw." With
nothing fitted, it has nothing to bite on.

### 2. The experiment has already been run here — twice — and both times said no

- `daytrade/oracle_audit.py` — depth-3 decision tree, **day-grouped 5-fold OOF**
  (correct methodology), scored **−0.028 R/trade** vs a trivial baseline.
  `MECHANISMS.json` MECH-004 status `killed`.
- `daytrade/residual_model.py` — regression on 7 entry-time features, n=336,
  `oof_mae 0.7768` vs `baseline_mae 0.6349`, `skill_vs_baseline −0.2235`,
  `usable: false`, verdict `NO_SKILL`.

### 3. Disciplined out-of-sample selection already exists and already ran

`daytrade/stockfish_tune.py` sweeps 396 configs × 336 entries under a selection
rule pre-registered **before** the run (`:18-33`, written 2026-08-17).
Verdict: **KEEP_SHIPPED** — the winner beat the shipped table by −0.0444R,
inside the pre-registered 0.05 margin, so nothing was promoted.

`daytrade/splits.py` enforces a 40/20 date holdout whose unseal shells out to
`git status` and `git log -S <rule_version>` and refuses on a dirty tree,
logging every unseal. That is stronger discipline than most of the proposal.

### 4. A working rolling WFA already exists, unused

`backtester/walk_forward.py` — rolling windows, pooled metrics computed only
from concatenated **test** slices (`:161-162`) so IS numbers structurally cannot
reach the summary, plus `param_stability.refit_churn` and
`window_sharpe.positive_frac`. **Zero importers.** It already satisfies the
proposal's "stitch, don't average" rule. It lacks purge and is row-indexed.

### 5. The daytrade lane cannot support the geometry

The proposal assumes four years of tape. `bars.load_sessions` returns **74**
complete NVDA sessions (2026-05-07 → 2026-08-21), of which `ceiling.py`'s
tune split yields **24 entries, 3 of which cleared +0.5R**. The forecast ledger
holds **13 resolved** forecasts against `forecast.py`'s `min_decisions = 50`.
A six-month in-sample window does not fit inside three and a half months.

### 6. Colin's own spec already ruled on this

`specs/005_BACKTEST.md:75-77` marks walk-forward parameter fitting `[SKETCH]`,
gated behind spec 004's prerequisites and a pre-registered protocol, *no
exceptions*, for the stated reason: **"The bench makes it mechanically easy,
which is exactly the danger."**

## The one real finding — record it, do not act on it yet

`sovereign/forex/forex_backtester.py:154-159` — `PAIR_VIX_GATES`, five per-pair
VIX thresholds (15/15/18/18/20), **selected in-sample on 2015–2024**, with the
per-pair Sharpe table sitting in the comments at `:147-153` (USDJPY 1.004 →
1.770, n 57–120/pair). `SIGNAL_THRESHOLD` was also lowered 0.20 → 0.15 (`:118`)
for sample size, with no OOS check noted.

That decade is the same decade as the sealed 411-trade proof set. **The
Sharpe 1.25 this repo exists to exploit was measured on a series produced by an
engine carrying five thresholds tuned on that series.**

This is the only place in the repo where fitted parameters sit underneath a live
money claim. It is a real, un-quantified exposure and it should be written down
as one — but investigating it is not this session's work, and it cannot be
cleanly measured today anyway: `data/oos_trades_2025_2026.json` (60 trades) has
**no committed generator**, and the rig that plausibly produced it reproduces
only 296 of the 411 sealed in-sample trades (`data/agent/repro_gap_report.json`).

Precedent that this instinct works: `PAIR_HOLD_OVERRIDES` was rolled back to
`{}` on 2026-06-07 after a walk-forward failure (OOS delta +0.055,
regime-concentrated, AUDUSD negative). The method has caught something here
before.

## Actions

1. **Build nothing.** No WFA pipeline, no revival of `backtester/walk_forward.py`,
   no new split machinery.
2. **Record the `PAIR_VIX_GATES` exposure** as a known risk — a `NEXT.md` entry
   or a `MECHANISMS.json` `proposed` entry, stating that v015's headline Sharpe
   is not independent of in-sample threshold selection, and that no sizing
   decision should treat it as though it were.
3. **Revive this proposal only when a trigger is met**, not on vibes:
   - a component acquires genuinely fitted parameters that are candidates for
     production, AND
   - the relevant lane has enough samples to split (the daytrade lane needs far
     more than 24 entries / 13 resolved forecasts), AND
   - the pre-registered protocol `specs/005_BACKTEST.md:75-77` demands is
     written and ratified first.
   When that happens, start from `backtester/walk_forward.py` — do not write a
   new one — and add the purge gap, which is the proposal's one genuinely
   missing idea (`grep -rniE "purge|embargo"` returns zero implementations
   repo-wide).

## Verification

No code changes, so nothing to test. This plan is correctly executed when:

- `git status` shows no source modifications from this session.
- The `PAIR_VIX_GATES` exposure exists in writing somewhere durable.
- The next session that proposes walk-forward reads this file first.

## What is actually next

Monday 2026-08-24, 08:00 ET: the reloaded `com.alta.alpha-operator` tick fires
for the first time since the interpreter fix. The thing to check is whether
`data/daytrade/plan.json` appears with `_writer: "mechanical-or-break-v1"`.
That file has never existed. It is worth more than any harness built this week.
