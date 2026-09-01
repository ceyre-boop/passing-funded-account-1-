# Economic-Floor Test — Result

**Date:** 2026-08-31 · **Pre-registration:** `artifacts/ECONOMIC_FLOOR_PREREG.md`
(freeze `8981e63`) · **Constants:** `az/floor_params.py` (freeze `3b10e8b`)
· **Script:** `scripts/floor_test.py`

---

> **COST SENSITIVITY ADDED 2026-09-01 — verdicts unchanged.** `SLIP_BPS = 2.0` is
> 15.5x the SPY half-spread and 66% of the modelled cost, and costs set both floors.
> Both families clear comfortably at realistic costs (`scripts/floor_test.py --sweep`;
> full table in `artifacts/COUNCIL_REVIEW_2026-09-01.md`). **This does not reopen
> anything.** The fill model was frozen at `3b10e8b` before the computation precisely
> so it could not be retuned after the answer was visible; the 15.5x is a defect in
> the pre-registration, and reopening needs a new one written beforehand.

## Verdict

**0 of 2 families clear the floor. The magnitude axis is closed on economics.**

Both families passed the falsifiability check — the tests were informative, and
both were run rather than closed unrun. Both then failed the economic floor.

| family | N | gross R | − costs | **net E[R]** | **FLOOR** | short by | verdict |
|---|---|---|---|---|---|---|---|
| `spy_macro_decay` (08:30–08:35, governs premarket) | 1,009 | 0.8202 | 0.6457 | **+0.1745** | **1.2915** | 1.1170 | **FAILS — CLOSED** |
| `spy_fomc_double_splash` (14:00–14:05) | 84 | 1.5158 | 0.5292 | **+0.9866** | **1.0585** | 0.0719 | **FAILS — CLOSED** |

N matched the recorded event counts exactly in both cases (1,009 and 84), which is
the check that the split was reconstructed correctly rather than silently truncated.

Benjamini–Hochberg across the two families is moot — nothing reached a pass.

---

## The one number that decides both

**Costs, not the effect, set the floor.** The pre-registered floor is
`max(0.10 R, 2 × cost drag)`, and in both families the cost term wins by an order
of magnitude — the 0.10 R absolute floor never binds:

| family | cost/share (pessimistic) | risk/share (1×ATR14) | cost drag |
|---|---|---|---|
| `spy_macro_decay` | $0.1176 | $0.3292 | **0.6457 R** |
| `spy_fomc_double_splash` | $0.1198 | $0.2986 | **0.5292 R** |

A 5-minute intraday ATR14 on SPY is roughly $0.30/share. A pessimistic round trip
is roughly $0.12/share. **So the declared stop of 1×ATR14 puts a third to a half of
the risk unit into costs before the trade has done anything.** That is the whole
result. The effect is not the binding constraint; the size of the risk unit relative
to execution cost is.

Note what this means for `k_stop`: the drag scales as `1/k_stop`. A wider stop would
shrink cost drag and lower the floor — but `k_stop = 1.0` was frozen at `3b10e8b`
before any of this was computed, precisely so that it could not be widened once the
answer was visible. Re-running at a different `k_stop` is a different study and needs
its own pre-registration.

---

## Both failures are decisive, not marginal in the way that matters

`spy_fomc_double_splash` came within 7% of its floor (+0.9866 vs 1.0585), and that
proximity is worth stating plainly rather than burying. It does not rescue the axis,
for a reason fixed in advance:

**κ = 0.50 is a ceiling on the result, not an estimate of it.** It was declared
generous on purpose so that a failure could not be blamed on stacked conservatism —
generous capture paired with hostile execution. A direction-free structure realizing
*half* of every event window's absolute move is already optimistic; the true capture
is lower, and the true E[R] is therefore lower than both numbers above. FOMC fails at
its own ceiling, and the real figure sits further below.

The same logic disposes of the temptation to read `+0.1745 R` and `+0.9866 R` as
"positive expectancy, just below a strict bar." They are positive, and they are
ceilings. Positive-at-the-ceiling is not an edge.

---

## What was checked first, and why it mattered

Prereg §5 orders the falsifiability check ahead of the economic one: if the floor
sits below what the sample can detect, neither a pass nor a fail is informative and
the study closes *without being run*. Both families cleared it:

| family | σ_R | N | MDE | floor ≥ MDE? |
|---|---|---|---|---|
| `spy_macro_decay` | 1.5492 | 1,009 | 0.1213 R | yes, 10.6× margin |
| `spy_fomc_double_splash` | 1.6710 | 84 | 0.4534 R | yes, 2.3× margin |

This is the first time in this repo's history that a pre-registered test has been
**run rather than closed unrun** — the entry family, the sealed-half `C5_gap` test,
and the exit tablebase all closed at the detection floor. These two got past it and
failed on their merits instead. That is a better failure than the previous ones.

---

## A defect caught mid-run, recorded because it nearly became the result

The first execution returned `N_events = 2` for FOMC against a recorded 84, and
reported `CLOSED WITHOUT BEING RUN — the floor is 7.94× below what 2 events can
detect.` That verdict was a **bar-coverage artifact, not a finding**:
`data/daytrade/bars/SPY_5m.parquet` holds only 66 sessions (2026-05-12 → 2026-08-14),
so the merge dropped 82 of 84 event days.

The correct source is `data/daytrade/bars_premarket/SPY_5m.parquet`, which despite
its name is a full extended-day cache — 04:00–20:20 ET, 2,678 days, 2016–2026 — and
is what both studies name in their own `bar_source` fields.

It is recorded here because it would have produced a clean, plausible, entirely
false "closed, underpowered" verdict for FOMC, and the only thing that caught it was
checking reconstructed N against the recorded N. **That check belongs in every
future test of this shape.**

---

## Scope

Two families, not three: `spy_macro_decay` and `spy_premarket` share the same six
FRED releases, the same day universe (1,009 vs 1,008 event days), and nested windows
(08:30–08:35 ⊂ 08:30–09:00). Collapsed under the pre-declared rule "the shorter
window governs." `spy_range_expansion`, the fourth MDE survivor, was already closed
by its own pre-registered economic floor as *"SIGNIFICANT AND USELESS."*

So all four detection-floor survivors are now closed on economics:

| survivor | closed by |
|---|---|
| `spy_macro_decay` | this test |
| `spy_premarket` | collapsed into macro_decay; same verdict |
| `spy_fomc_double_splash` | this test |
| `spy_range_expansion` | its own floor, declared in spec 043 |

No entry rule was built. Per prereg §5, a failing family is closed — no further work,
no re-parameterization, no second look at a different `k_stop` without a new
pre-registration.

---

## Verification

```bash
python3 scripts/floor_test.py                  # this result
python3 -m pytest gate/ az/ -q                 # 58 passed
```
