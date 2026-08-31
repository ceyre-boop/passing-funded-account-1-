# Economic-Floor Pre-Registration — the three undeclared survivors

**Status: PRE-REGISTRATION. Committed before any conversion is computed.**
Nothing in this document reports a result. It contains no expected R, no capture
fraction, no verdict. Every number cited is already published in
`artifacts/EVENT_STUDY_MDE.md` or the study summaries; nothing here is new
arithmetic. If a number appears below that is not traceable to one of those, it is
a defect in this document.

**Date:** 2026-08-31 · **Applies to:** `spy_macro_decay`, `spy_premarket`,
`spy_fomc_double_splash` · **Does not build an entry rule.**

---

## Why this exists

The MDE table found four studies clearing their detection floor. `spy_range_expansion`
was the **only** one that had declared an economic floor in advance — and it cleared
detection and then failed that floor, on record, as *"SIGNIFICANT AND USELESS."*

The other three never declared one. They have not failed a tradability test; **they
have not taken one.** This document is that test, written down before the number is
known, because a floor chosen after seeing the answer is not a floor.

---

## 0. The blocking problem, stated first

**`abs_ret` is unsigned. R requires a signed entry.**

All four survivors measure *magnitude*. All three studies that attempted to convert
magnitude into direction are below their detection floor — `pre_fomc_drift` 2.37×,
`second_wave` 4.08×, `splash_continuation` 29.27×. **No directional edge may be
assumed in this conversion**, because the repo has three pre-registered failures
saying there isn't one.

Consequence, declared now: the conversion may only assume a **direction-free**
structure — one whose payoff depends on how far price travels, not which way. Any
conversion that quietly assumes the correct side is picked is void, and this
sentence is the test for that.

---

## 1. The quantity that converts the effect to expected R per event

Declared identity. Each term's source is fixed here; none may be chosen later.

```
                    κ · Δ|ret| · P  −  c_total
  E[R per event]  = ───────────────────────────
                          k_stop · ATR14
```

| term | what it is | where it must come from | fixed now? |
|---|---|---|---|
| `Δ\|ret\|` | **the differential**, event mean minus control mean — *not* the event mean | the study's own `diff_event_minus_control` | **yes** |
| `κ` | capture fraction: the share of the window's absolute move a direction-free structure actually realizes | must be **declared or bounded before computation**, never fitted | **no — must be set before running** |
| `P` | underlying price at the event | as-of at `t` | yes |
| `c_total` | round-trip cost + adverse slippage | the pessimistic fill model in §4 | **yes** |
| `k_stop` | stop width in ATR units | `az/candidates.geometry` — **required, no default**, "a different `k_stop` is a different study" | **no — must be declared before running** |
| `ATR14` | as-of computable at `t` | `az/candidates.geometry` | yes |

### The one substantive choice, made now

**The numerator uses the differential `Δ|ret|`, not the event mean.** Trading on a
non-event day is always available, so the edge is the *increment* over an ordinary
day, not the gross size of the move. Using the event mean would credit the strategy
with volatility it can obtain any day of the week without an event.

This is the decision most likely to be quietly reversed once the differential turns
out to be small. It is fixed here so that reversal is visible.

The published differentials this will draw on — already on record, cited, not
recomputed:

| study | `diff_event_minus_control` | event mean | window |
|---|---|---|---|
| `spy_macro_decay` | 0.000828 | 0.001240 | 08:30–08:35 |
| `spy_premarket` | 0.000893 | 0.001783 | 08:30–09:00 |
| `spy_fomc_double_splash` | 0.001822 | 0.002485 | 14:00–14:05 |

Note the shape of that table before any conversion: **every differential is smaller
than its event mean, in two cases by roughly half.** Anything computed against the
event mean will look about twice as good as the honest number.

---

## 2. What counts as tradeable — the floor, declared now

Two conditions. **Both** must hold. Failing either closes the study.

### (a) The economic floor

```
  E[R_net per event]  ≥  FLOOR  =  max( 0.10 R ,  2 × cost drag in R )
```

A definition, not a computed claim — the cost drag term is evaluated at computation
time under §4's parameters and the declared `k_stop`. Whichever bound is larger
governs. Rationale: a fixed 0.10R alone becomes meaningless if the stop is set wide
enough to make costs look small, so the floor is pinned to costs as well as to an
absolute.

### (b) The falsifiability constraint — the floor must be detectable

```
  FLOOR  ≥  MDE( σ_R , N_events )        [daytrade/mechanisms.mde]
```

If the declared floor sits **below** what the available sample can detect, then no
outcome of the computation is informative: a "pass" would be indistinguishable from
noise and a "fail" would prove nothing. In that case the study is **untestable at
this sample size and is closed without being run** — the same ruling that kept the
sealed-half `C5_gap` test from being run at 68× below floor.

This is the MDE rule applied to the *floor* rather than to the effect, and it is the
condition this pre-registration is most likely to fail on. That is the point of
checking it first.

---

## 3. N available at each event frequency

From the record. `N_events` is the binding sample size — control days do not add
power to a per-event R estimate.

| study | event days | control days | span | ≈ events/yr |
|---|---|---|---|---|
| `spy_macro_decay` | **1,009** | 1,667 | 2016-01-04 → 2026-08-26 | ≈ 95 |
| `spy_premarket` | **1,008** | 1,667 | same universe | ≈ 95 |
| `spy_fomc_double_splash` | **84** | 363 | scheduled FOMC only | ≈ 8 |

`spy_macro_decay`'s event set is now reproducible from committed data — 1,221 FRED
events over the exact span, persisted at
`data/daytrade/event_study/spy_macro_decay_event_calendar.json`, with the split at
`spy_macro_decay_primary_days.csv`.

### These are not three independent tests

Declared before computation, because it will be inconvenient afterwards:

- `spy_macro_decay` and `spy_premarket` draw on the **same six FRED releases** and
  the **same day universe** (1,009 vs 1,008 event days, differing only by one day's
  bar availability). Their windows are **nested**: 08:30–08:35 is a strict subset of
  08:30–09:00.
- They are therefore **one finding measured at two resolutions, not two findings.**
  Treating them as independent confirmations would be double-counting the same
  1,000 mornings.

**Ruling for the computation:** the two overlapping studies are collapsed into one
family and contribute **one** verdict, chosen by the pre-declared rule *"the shorter
window governs"* (08:30–08:35 — the tighter window, the one that cannot borrow
drift from the surrounding 25 minutes). `spy_fomc_double_splash` is a genuinely
separate event universe and stands alone.

So this pre-registration commits to **two** economic-floor tests, not three, and
Benjamini–Hochberg applies across those two.

---

## 4. The pessimistic fill model — parameters fixed now

`az/fills.py::PessimisticFill`. It is currently instantiated nowhere in committed
code, so its parameters are declared here rather than inherited:

| parameter | value | effect |
|---|---|---|
| `cost_mult` | **2.0** | `cost_per_share` = 2 × `COST_PER_SHARE` (0.02 → 0.04/share round trip) |
| `slip_bps` | **2.0** | charged against the entry, always adverse |
| `delay_bars` | **1** | fill pushed one bar later — where adverse selection actually bites |

The class enforces `cost_mult ≥ 1.0` and `slip_bps ≥ 0.0` at construction: pessimism
may not help the candidate. The handicap applies to the **candidate only**, so it can
only tighten the gate, never loosen it.

Any conversion run under lighter assumptions than these does not satisfy this
pre-registration.

---

## 5. What closes each study

A study is **closed** — no entry rule, no further work — if any of these fires:

1. `FLOOR < MDE(σ_R, N_events)` — untestable at this N (§2b). Closed without running.
2. `E[R_net per event] < FLOOR` — fails economics, the `spy_range_expansion` outcome.
3. The conversion requires assuming a direction (§0).
4. `κ` or `k_stop` is chosen after seeing the resulting R.

A study **survives** only by clearing §2a and §2b together, under §4's fill model,
with `κ` and `k_stop` fixed beforehand. Surviving licenses **one** thing: a written
proposal for what would be built next. It does not license building it.

---

## 6. Freeze

This document is committed **before** any term in §1 is evaluated. The commit that
adds it is the freeze point; `κ` and `k_stop` are set in a follow-up commit that
must also land before the conversion runs, and both must be visible in git history
ahead of any computed R.

If those values ever appear in the same commit as a result, this pre-registration
has been violated and the result is void.
