# The seven stop layers, adjudicated — 2026-08-29

Phase 1 gate 2. Every layer now carries a *decided* status, not an undecided one.
Engine at `engine_sha256 = 89b4db77…`, pinned as **SF-FROZEN-003** (SF-FROZEN-001
and -002 untouched; I49 forbids re-pinning, so this is a new id).

| # | layer | status | decided because |
|---|---|---|---|
| 1 | catastrophic | **ACTIVE** | the original plan stop; never removed, never loosened. Unchanged |
| 2 | breakeven | **ACTIVE** | arms at TP1; the day cannot go red after it. Unchanged |
| 3 | profit_lock | **ACTIVE** | arms at TP2; stop rides to TP1. Unchanged |
| 4 | trail | **ACTIVE, evidence-doubted** | see ruling below — kept, but it is the one live layer the evidence argues against |
| 5 | volatility | **BUILT, opt-in, BLOCKED** | built as placement; blocked on a pre-existing `StageError` gap, not on the layer |
| 6 | thesis | **DARK — decided** | blocked on `regime.py`, and the unblocking path is now specified: regime as a *magnitude* conditioner, not a direction classifier. Not built this session |
| 7 | time_decay | **DARK — decided, ruled OUT** | see ruling below |

## Layer 5 — volatility, built as placement not as a trail

`sl_vol = entry − direction · k · ATR`, computed once from entry and the ATR at
entry. It never reads `hwm` and never moves. This is deliberately **not**
`hwm − k·ATR`, which is a trailing stop and the instrument this repo's evidence
is most negative about.

`k` is required with no default; absent `k`, the layer stays dark with its reason.
`atr` and `vol_k` are validated in `__post_init__` — positive and finite or raise,
following `carry_engine._compute_atr` which raises, never `regime_vector._atr`
which returns 0.0 (that default caused an 817× mis-size and has its own
regression suite).

**Inertness proven twice.** With no `k` supplied, the post-change engine returns
values identical to the pre-change engine over **9,576 per-config R values**
(24 entries × 399 configs). Verified by the builder, then re-verified
independently by the architect seat via an in-place engine swap comparing a hash
of the full value vector: `9c17d3dfe50aa522c8ac83ac` both ways. Monotone
tightening re-checked with the layer active: 500 randomized trials, 2,403 price
steps, 0 loosening violations.

### The blocking finding — pre-existing, exposed not caused

Any layer active from `Stage.ENTERED` that binds tighter than the catastrophic
stop before `PROTECTED` raises `StageError`, because `ALLOWED_ACTIONS[ENTERED]`
contains no `MOVE_SL`. Reproduced identically via `thesis_sl` on the **unmodified
SF-FROZEN-002 engine**, so this is a pre-existing architectural gap; the
volatility layer is simply the first caller to ever supply realistic always-on
values. Until it is resolved, the layer cannot be used in production regardless
of `k`.

### The k-sweep is UNINTERPRETABLE as run — do not quote it

SPY tune lane, 2,116 entries, grid 0.5–3.0 step 0.25, baseline (no layer)
**−0.0562 R/trade**:

| k | n_ok | crashes | R/trade | bind rate |
|---|---|---|---|---|
| 0.50 | 478 | 1,638 | −0.5796 | 87.5% |
| 1.00 | 1,273 | 843 | −0.1280 | 41.4% |
| 1.50 | 1,841 | 275 | −0.0476 | 13.1% |
| 2.00 | 2,034 | 82 | −0.0532 | 3.9% |
| 2.50 | 2,093 | 23 | −0.0605 | 1.1% |
| 3.00 | 2,103 | 13 | −0.0568 | 0.6% |

**k = 1.50 appears to beat baseline (−0.0476 vs −0.0562) and that comparison is
invalid.** Each row is computed on a *different population*: the crashes are
`StageError`, which fires precisely when the volatility stop would have bound
early — so the dropped entries are not a random sample, they are disproportionately
the trades where a tight stop engaged. Dropping them flatters the survivors.
Comparing R/trade across rows with different `n` is survivorship, and the
apparent optimum at k=1.5 is exactly where the crash count is still large
(275 dropped) but no longer overwhelming.

**No k is selected, and none may be until the sweep is re-run with baseline
recomputed on each k's own surviving subset.** That matched-population rerun is
the next step and it is cheap. Until then this table records only that the layer
runs, not that any k helps.

## Layer 4 — trail: kept, and the ruling it needs

`trail` is the one ACTIVE layer the evidence argues against. MECH-001 is
indeterminate at p=0.116; `tmNone` was the best fixed config on both SPY and QQQ
over 4,512 trades; and today's gate calibration re-confirmed it a third way —
the first attempted "degradation" was `trail_mult × 3`, and **widening the trail
made things better** (total R −132.42 vs the incumbent's −172.71). The
degradation had to be built by choking the trail instead.

That is three independent lines pointing the same way. **Whether `trail` should
remain ACTIVE is a ruling, not a build step**, and it is not taken here.

## Layer 7 — time_decay: ruled OUT, with the reason

The existing dark reason was a design claim: *"time pressure is expressed as the
`flatten_at_et` EXIT_ALL rule, not as a stop level."* That claim stands, and it
is now a decision rather than a deferral:

`flatten_at_et` is already ACTIVE, already configurable, and already the time
instrument. A tightening *schedule* would be a **second** time instrument
competing with the first, and there is no evidence distinguishing them — building
it would add a parameter with nothing to set it from. The best-fixed configs do
show time-flatten mattering and differing by instrument (NVDA selected
`fl11:00`, QQQ `fl15:45`, SPY `flNone`), but best-of-396 is a selection artifact
and explicitly not a result (`CEILING_10Y_RECORD.md`).

**Ruled OUT.** Re-opening requires evidence that a schedule beats the cliff on
days that reached TP1 — a counterfactual nobody has produced.
