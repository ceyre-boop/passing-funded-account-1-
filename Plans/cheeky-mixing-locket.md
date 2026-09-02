# Write docs/EXIT_ENGINE_SPEC.md — the hindsight-trained exit engine

## Context

A full implementation spec for a learned, live-executing exit engine: hindsight
oracle labels, per-bar live-computable features, two model heads, calibrated
thresholds, hard rails, walk-forward validation. Intraday only, entry out of scope.

**Deliverable is the document. No implementation this session.**

The nine sections are specified and will be written as specified. What follows is
the repo-specific material that has to go *inside* them, because an engineer
building from this spec without it would repeat work that has already returned
null three times.

## The finding that shapes the whole document

**This is attempt four.** Three prior attempts at a learned exit on this data are
recorded, and all three are null:

| attempt | what it was | result |
|---|---|---|
| spec 025 `daytrade/exit_evaluator.py` | per-bar (state, action) → reward, day-grouped GBM, derived exit policy | **NO_SUPERSEDE** |
| oracle audit, feature-based config selection | pick the config from entry-time features | **−0.028, DRAWER** |
| `daytrade/residual_model.py::fit_stockfish` | giveback regressed on entry-time conditions | **NO_SKILL**, killed |
| `MECHANISMS.json` MECH-004 | "the right exit configuration is knowable from entry-time price alone" | **killed** |

Spec 025's numbers, from `data/daytrade/exit_evaluator_report.json`, are the honest
prior for section 0 of the spec:

- 336 entries, **18,305 decision points**, OOF advantage correlation **0.081**
- derived policy vs shipped: SINGLE_NAME **−0.263 R**, CASH_INDEX **−0.153 R**,
  FUTURES **+0.097 R** — 1 of 3 classes, and it did not beat the frozen futures candidate
- top features: `hwm_r`, `minutes_to_close`, `tr5_r`, `dd_from_hwm_r`, `bars_held`,
  `dist_to_stop_r`, `unrealized_r`, `r_banked`

**Those features are substantially §3's position-state and price-state families.**
So the spec must state plainly what is genuinely new here — two heads rather than one
regressor, a normalized remaining-excursion label, calibration with a held-out
threshold, scale-out rather than binary flatten, walk-forward with purge/embargo
rather than GroupKFold, and hard rails as unconditional overrides. That list is the
spec's reason to exist and belongs up front, not in a footnote.

## Three constraints the spec must state, all verified

**1 · Most of §3's flow state is not computable.** Committed market data is
OHLCV 5-minute bars only (`data/daytrade/bars/`, `bars_premarket/`). There is no
tick, quote, or book data anywhere in the repo. So *delta imbalance*, *spread width*
and *trade size distribution shift* have no data source. Volume decay and relative
volume are computable. The spec lists the flow family as **BLOCKED — needs a data
source that does not exist**, with the acquisition named as a precondition, rather
than listing features an engineer would discover are unbuildable in week two.

**2 · The label set is dominated by dead trades.** From
`data/daytrade/exit_quality.json`: 336 sessions, **192 unwinnable entries (57%)**,
mean oracle R 0.8234, mean MFE R 3.4223, median efficiency 0.654, median giveback
2.146 R. On an unwinnable trade the oracle label is *exit at bar 1*, so 57% of labels
say get out immediately — and an unweighted fit learns "flatten early" as a near
universal policy, which scores well on exactly the metric that rewards it.

**Ruling (yours):** train on the whole set, sample-weighted so the unwinnable
trades cannot dominate the loss, with **`w` frozen before the first fit** — same
discipline as `k_stop`. The spec declares `w` as a pre-registered constant and says
explicitly that a `w` chosen after seeing results is a fit, not a weight.

**3 · Purge and embargo do not exist in this repo.** §4 requires them and
`grep embargo` returns one unrelated hit. `backtester/walk_forward.py::walk_forward_backtest`
exists and is unused, but has no purge gap. The spec specifies both as **to be
built**, and names the session-boundary semantics (an intraday engine embargoes
whole sessions, not bars).

## What must be reused rather than rebuilt

| need | existing | note |
|---|---|---|
| hindsight oracle | `daytrade/ceiling.py` + `exit_quality.py` | already computes `oracle_r`, `mfe_r`, giveback, efficiency, carries the "yardstick, never a target" warning |
| the decision loop | `daytrade/ceiling.py::simulate` (backtest) / `runner.py::run` (live) | **Rule 1**: `daytrade/test_one_implementation.py` fails the suite on a second per-bar loop |
| lookahead defence | `az/state.py::truncate_at` + `assert_no_lookahead` | the named seam and its fault-injection corruption test — §2's lookahead boundary and §8's leakage defence both route here |
| detection floor | `gate/discovery.py` | any claimed improvement must clear MDE at n = **entries**, not bars |
| walk-forward skeleton | `backtester/walk_forward.py` | add purge/embargo |

## Two structural facts the spec must not omit

- **`daytrade/stockfish_exit.py` is frozen** under `SF-FROZEN-004`, whose sha256 is
  verified on every T_SIM run. A learned engine is a **challenger**, not an edit: it
  ships alongside, and replacing the frozen one requires minting a new checkpoint.
- **n is entries, not bars.** 18,305 decision points come from 336 entries
  (171/100/65 by class). Bars within a trade are one bet. Every power statement in
  §7 uses the entry count.

## Document plan

`docs/EXIT_ENGINE_SPEC.md`. The nine requested sections, verbatim in scope and
order, plus:

- **§0 Prior attempts and what would have to be different** — the table above.
- Amendments folded into their own sections: flow state marked BLOCKED in §3; the
  weighted-label ruling and `w` freeze in §2; purge/embargo as to-be-built in §4;
  challenger-not-replacement in §5; the 57%-unwinnable bound and entries-not-bars in
  §7; the "labels teach exiting" hazard added to §8's list.
- **§10 Kill criteria** — pre-registered, since three nulls precede this: what result
  ends the attempt, declared before any fitting.

Prose is spec register — an engineer builds from it. No essay.

## Verification

It is a document, so verification is that every claim in it is checkable:

```bash
python3 -c "import json;print(json.load(open('data/daytrade/exit_evaluator_report.json'))['verdict'])"
python3 -c "import json;d=json.load(open('data/daytrade/exit_quality.json'));print(d['n_sessions'],d['n_unwinnable_entries'],d['mean_oracle_r'])"
python3 -c "import pandas as pd;print(list(pd.read_parquet('data/daytrade/bars_premarket/SPY_5m.parquet').columns))"
grep -rn '"MECH-004"' -A3 MECHANISMS.json
```

Every figure quoted in the spec traces to one of these. No new tests — nothing
executable is added.

## Out of scope

All implementation: no labeler, no feature store, no model, no harness. No change to
`stockfish_exit.py`, `ceiling.py`, or any frozen artifact. No new spec number under
`specs/` (the file goes to `docs/`, as asked). No re-run of spec 025.
