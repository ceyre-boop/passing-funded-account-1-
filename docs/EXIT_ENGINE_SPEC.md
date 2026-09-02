# EXIT ENGINE SPEC — hindsight-trained, live-executing

> ## STATUS: SPECIFIED, NOT SCHEDULED
>
> **Deferred behind the drive milestone. Not abandoned.**
>
> This is a challenger to `SF-FROZEN-004` — a better exit engine for a car that has
> not yet completed a session under a real entry policy. The frozen exit is good
> enough to drive with; the loop is what is unproven. Nothing here advances the
> operator's 13-of-50, and that is the only clock that leads to trading authority.
>
> **Unblocks when:** the full loop completes a run at T_SIM under a
> non-calibration entry policy, and the ODD ladder has reached T3 on live paper.
>
> **Three things in here decay if it sits, and are the reason to reread it before
> starting rather than starting from scratch:** §0 (this is attempt four, and it
> argues against itself), §4.6 (n = 336 entries, not 18,305 bars), and §3.3
> (BLOCKED beats proxied).

**Codename:** Stockfish (learned). **Scope:** intraday, single-session, no overnight
holds. **Status:** SPECIFIED, NOT SCHEDULED — see the banner above.

**Reference implementation targets:** `daytrade/` (challenger modules), `docs/` (this file).
**Frozen incumbent:** `daytrade/stockfish_exit.py` @ `SF-FROZEN-004`.

---

## 0. Prior attempts, and what would have to be different

Read this section before building anything. **Three prior attempts at a learned exit
on this data are recorded, and all three are null.**

| attempt | what it was | result |
|---|---|---|
| spec 025 — `daytrade/exit_evaluator.py` | per-bar (state, action) → reward, day-grouped GBM, derived exit policy | **NO_SUPERSEDE** |
| oracle audit — feature-based config selection | choose the config from entry-time features | **−0.028 R, DRAWER** |
| `daytrade/residual_model.py::fit_stockfish` | giveback regressed on entry-time conditions | **NO_SKILL** |
| `MECHANISMS.json` MECH-004 | *"The right exit configuration is knowable from entry-time price features, so a per-day config choice beats one fixed policy."* | **killed** |

Spec 025 is the closest prior and its numbers are the honest prior for this one
(`data/daytrade/exit_evaluator_report.json`):

```
n_entries            336
n_decision_points  18,305
oof_advantage_corr  0.081          <- essentially no signal

derived policy vs shipped, mean R/trade:
  SINGLE_NAME   -0.2626      (171 entries)
  CASH_INDEX    -0.1534      (100 entries)
  FUTURES       +0.0972      ( 65 entries)   1 of 3 classes, and it did not
                                             beat the frozen futures candidate

top features: hwm_r, minutes_to_close, tr5_r, dd_from_hwm_r,
              bars_held, dist_to_stop_r, unrealized_r, r_banked
```

**Those features are substantially §3's position-state and price-state families.**
An engineer who builds §3 and stops has rebuilt spec 025.

### What is actually new here

This list is the spec's reason to exist. If a design decision is not on it, spec 025
already tested it and it returned null.

1. **Two heads** — `P(exit optimal now)` and `E[remaining R]` — where 025 had a single
   advantage regressor. A classifier and a regressor disagree in informative ways;
   one number cannot express "probably over, but if it runs it runs far."
2. **A continuous label** — remaining favourable excursion in R, normalized —
   alongside the binary one. 025 had only an advantage scalar.
3. **Calibration as a first-class stage** (§6). 025 had none, and its derived policy
   used a hard `advantage < 0` rule with no reliability check.
4. **Scale-out** rather than binary flatten. 025's policy exited fully at the first
   negative bar. Partial exits are a different action space.
5. **Walk-forward with purge and embargo** (§4) rather than GroupKFold by date.
6. **Hard rails as unconditional overrides** (§5), so model failure degrades to the
   frozen behaviour rather than to nothing.

### Kill criteria are in §10 and are pre-registered

Three nulls precede this. §10 is declared before any fitting and is not negotiable
afterwards.

---

## 1. Problem statement

Optimal exit is not predictable ex ante. The engine **maximizes expected value over
the remaining path of an open position**. It does not attempt to find the peak, and
any evaluation that rewards peak-finding is measuring the wrong thing.

Entry is assumed already valid and is **out of scope**. The engine answers exactly one
question, once per bar, for an open position:

> **hold · scale · flatten**

Non-goals, stated so they cannot drift in:

- It does not select entries, size positions, or choose instruments.
- It does not hold overnight. Every position is flat by session close (§5 rail).
- It does not predict price. It predicts the *value of continuing to hold*.

### The bound that governs the whole project

From `data/daytrade/exit_quality.json` (336 sessions, compared against `SF-FROZEN-004`):

```
n_sessions             336
n_unwinnable_entries   192        <- 57% of entries: NO exit is profitable
mean_oracle_r        0.8234       <- perfect-hindsight ceiling
mean_mfe_r           3.4223
median_efficiency    0.654
median_giveback_r    2.146
```

**57% of historical entries cannot be saved by any exit policy.** The headroom this
engine competes for is the gap between realized R and `mean_oracle_r` on the other
43%. State that number in every result. An engine that "improves" mostly by cutting
dead trades one bar sooner has found the base rate, not an edge.

---

## 2. Hindsight oracle (label generation)

### 2.1 Definition

For every bar `t` of every historical trade, compute the forward-optimal action from
the **realized future path within the same session**.

```
label_binary(t)     = 1 if t is the R-maximizing exit bar, else 0
label_continuous(t) = remaining favourable excursion in R from t, normalized
                    = (max_{u>=t} R(u) - R(t)) / risk        [see 2.3]
```

Subject to a **hard flatten at session close**: the oracle may not select an exit
after the close bar, so `label_binary` is always defined and always within-session.

### 2.2 Reuse — do not write a second oracle

`daytrade/ceiling.py` and `daytrade/exit_quality.py` already compute the hindsight
quantities and carry the warning that governs their use:

> `oracle_r` and `mfe_r` use perfect hindsight and are NOT achievable; they are the
> yardstick, never a target.

`exit_quality.py::mfe_r(session, e)` gives maximum favourable excursion.
`ceiling.py::simulate(session, e, cfg, observer=...)` replays a session under one
config. **The `observer` hook at `ceiling.py:192` is the sanctioned per-bar tap** — a
read-only view of `(ts, bar, st, realized, held)` at each bar open, before that bar's
decisions. Spec 025 built its dataset through it.

> **Rule 1 — one implementation.** `daytrade/test_one_implementation.py` fails the
> suite on any function that calls `decide_exit` and drives it through its own
> per-bar loop. The labeler and the feature store both go through `simulate` +
> `observer`. This is not style; a second loop produces numbers quietly incomparable
> to every other measurement in the repo.

### 2.3 Normalization

All R is in units of the trade's own risk, `risk = |entry − stop|` per share, which
is what `ceiling.Entry.risk` already carries. `label_continuous` is clipped to
`[0, MAX_LABEL_R]` with `MAX_LABEL_R` declared in the config; unclipped, a handful of
runaway sessions dominate the regression.

### 2.4 Per-trade calibration store

Persist, per trade: `MFE`, `MAE`, `giveback = oracle_r − realized_r`,
`time_to_peak` (bars), `efficiency = realized_r / oracle_r`, and the `unwinnable`
flag. These feed §6 and §7 and are already partly produced by `exit_quality.py`.

### 2.5 Class weighting — pre-registered, frozen before the first fit

57% of labels say *exit at bar 1* (§1). Unweighted, the model learns "flatten early"
as a near-universal policy and scores well on precisely the metric that rewards it.

```
w_unwinnable = <DECLARE BEFORE FIRST FIT>     # sample weight on unwinnable trades
w_winnable   = 1.0
```

**`w_unwinnable` is frozen before any model is fitted**, in its own commit, the same
discipline as `k_stop` in the economic-floor work. A weight chosen after seeing
results is a fit, not a weight, and it silently converts this spec into a search over
`w`.

The whole set is trained on. Filtering to winnable trades only would produce a model
that has never observed a dead trade and therefore has no learned basis for cutting
one — the survivorship failure §8 names, entered deliberately.

### 2.6 The lookahead boundary — explicit

**No oracle output may appear in any feature, at any horizon, under any name.**

The boundary is enforced structurally, not by review:

- Features are computed only from `truncate_at(session_df, t)` — bars at or **before**
  `t` (`az/state.py:126`). That single named seam is the only place feature code sees
  data.
- `az/state.py::assert_no_lookahead` computes the features, **corrupts every bar
  strictly after `t`**, recomputes, and raises if anything moved. A feature that
  changes has read the future.
- The feature store and the label store are written by **separate passes** and joined
  on `(trade_id, bar_ts)` afterwards. They never share a dataframe in memory.
- CI check: any column name matching `oracle|mfe|future|label|remaining|peak` in the
  feature table fails the build.

---

## 3. State representation — per bar, live-computable only

Every feature must be computable at bar `t` from `truncate_at(df, t)` alone, and must
be **sign-normalized** so long and short share one model: a long's "distance above
VWAP" and a short's "distance below VWAP" are the same feature.

### 3.1 Position state  `[BUILDABLE]`

| feature | definition |
|---|---|
| `unrealized_r` | `(price − entry) · direction / risk` |
| `bars_held` | bars since fill |
| `hwm_r` | max favourable excursion so far, in R |
| `dd_from_hwm_r` | `hwm_r − unrealized_r` — giveback so far |
| `dist_to_stop_r` | `(price − effective_stop) · direction / risk` |
| `minutes_to_close` | to session close, ET |
| `r_banked` | R already realized by partials |

### 3.2 Price state  `[BUILDABLE]`

| feature | definition |
|---|---|
| `velocity` | ΔClose over last k bars, ATR-normalized |
| `acceleration` | Δvelocity |
| `tr_atr` | current true range ÷ ATR14 |
| `dist_vwap_atr` | `(price − VWAP) · direction / ATR` |
| `dist_session_extreme_atr` | to session high (long) / low (short), ATR-normalized |
| `bars_since_extreme` | since last higher high (long) / lower low (short) |

ATR14 reuses `az/state.py::_atr` — mean(High−Low) over the last 14 bars. One
implementation.

### 3.3 Flow state  `[BLOCKED — NO DATA SOURCE]`

**Committed market data is OHLCV 5-minute bars only.** `data/daytrade/bars/` and
`bars_premarket/` carry exactly `Open, High, Low, Close, Volume`. There is **no tick,
quote, or order-book data anywhere in the repo.**

| feature | status |
|---|---|
| volume decay vs trade-average | **buildable** — `Volume` exists |
| relative volume for the day | **buildable** |
| delta imbalance | **BLOCKED** — needs signed trade prints |
| spread width | **BLOCKED** — needs L1 quotes |
| trade size distribution shift | **BLOCKED** — needs tick prints |

**Acquiring a tick/quote feed is a precondition of the flow family, not a task
inside it.** Do not stub these with bar-derived proxies: a "spread proxy" from
OHLC is a different quantity wearing the name, and it will be believed. v1 ships
with the two buildable volume features and the family marked incomplete.

### 3.4 Regime state  `[BUILDABLE]`

`session_phase` ∈ {`OPEN_DRIVE`, `MIDDAY`, `CLOSE`} (bucketed from `minutes_to_close`
and time since open); realized vol vs trailing baseline; relative volume for the day.

### 3.5 Sign normalization — enforced

A test asserts that for a synthetic long and its exact mirror short, **every feature
vector is identical to 1e-9**. This is the same mirror test that refuted a claimed
short-side stop inversion; it is cheap and it is the only way to know one model can
serve both sides.

---

## 4. Model

### 4.1 Heads

```
head_p : features -> P(exit is optimal now)      binary, calibrated (§6)
head_r : features -> E[remaining R]              regression, R units
```

Both trained on the same feature rows. They are allowed to disagree; §5 defines what
happens when they do.

### 4.2 v1 — gradient-boosted trees. Ship this first.

`HistGradientBoostingClassifier` / `HistGradientBoostingRegressor`. Sample weights
from §2.5. Permutation feature importances reported — an uninterpretable exit engine
does not reach live under any result.

### 4.3 v2 — sequence model. Not before v1 has a verdict.

GRU or temporal convolution over the last N bars of the feature sequence. v2 exists
because per-bar features discard trajectory shape, and "this has been grinding
sideways for forty minutes" is a sequence fact. **Do not start v2 until v1 has
returned a §10 verdict** — spec 025's failure was not caused by model capacity, and
reaching for more capacity first is how a fourth null becomes a fifth.

### 4.4 Per-asset vs pooled

Train both:
- **per-asset** — one model per symbol
- **pooled + asset embedding** — one model, symbol as a learned categorical

Compare on the §7 metrics and **document which wins per asset class**. Spec 025's
result was class-split (FUTURES +0.097, the other two negative), so a pooled model
that wins on average while losing on two of three classes is a specific, anticipated
failure — report per class or the comparison is meaningless.

### 4.5 Validation — walk-forward only

**No random splits. No GroupKFold.** Rolling walk-forward:

```
train [t0, t1)  →  purge  →  embargo  →  test [t2, t3)
```

- **Purge**: drop training rows whose *trade* overlaps the test window. A trade's bars
  are one bet; a trade straddling the boundary leaks its outcome.
- **Embargo**: after each test window, exclude **whole sessions**, not bars. This is an
  intraday engine; the unit of dependence is the session.

> **Both must be built.** `backtester/walk_forward.py::walk_forward_backtest` exists
> and is unused, but has **no purge gap**, and `grep -r embargo` returns no
> implementation anywhere in the repo. Extend that function; do not write a second
> walk-forward.

### 4.6 n is entries, not bars

18,305 decision points come from **336 entries** (171 / 100 / 65 by class). Bars
within a trade are one bet. Every power and significance statement uses the **entry**
count, and any claimed improvement must clear the detection floor at that n via
`gate/discovery.py`. An improvement quoted against 18,305 is quoting the wrong
denominator by roughly 54×.

---

## 5. Live decision rule

### 5.1 The rule

```
cost_of_holding = slippage + spread + opportunity_cost      # declared constants

HOLD     while E[remaining R] > cost_of_holding
SCALE    as P(exit) rises through the calibrated ladder (§5.3)
FLATTEN  when E[remaining R] crosses zero
      OR when P(exit) > threshold_flatten            (§6, from calibration set)
      OR on any hard rail                            (§5.2)
```

When the heads disagree — `P(exit)` high but `E[remaining R]` also high — **the
conservative action wins**: scale, do not add. This is a stated tie-break, not an
emergent one.

### 5.2 Hard rails — unconditional, model cannot override

| rail | behaviour |
|---|---|
| stop loss | flatten. never widened, ever |
| session-close flatten | flatten by declared time. no overnight hold |
| max giveback from MFE | flatten when `dd_from_hwm_r` exceeds the declared cap |
| max time in position | flatten after declared bars |

Rails are evaluated **before** the model, and a rail firing is logged with the model's
output at that moment — that log is the primary evidence for §8's threshold-drift and
illiquidity checks.

The frozen engine's precedence ladder (`stockfish_exit.decide_exit`) already
implements urgent-exit → stop → time-flatten → ladder → layered stop, and the
never-loosen invariant lives in `effective_stop`'s `max()`. **The learned engine
inherits those rails rather than reimplementing them.**

### 5.3 Partial exits

Scale out on rising `P(exit)` rather than flattening binary:

```
P(exit) in [p1, p2)  ->  reduce to 2/3
P(exit) in [p2, p3)  ->  reduce to 1/3
P(exit) >= p3        ->  flat
```

`p1 < p2 < p3` come from the calibration set (§6), never the training set. Ratchet:
size may only decrease within a trade.

### 5.4 It is a CHALLENGER, not a replacement

`daytrade/stockfish_exit.py` is frozen under `SF-FROZEN-004`, and its sha256 is
recomputed and verified on every simulation run before the loop is allowed to drive.
**This engine ships alongside it.** Replacing the frozen incumbent requires minting a
new checkpoint and is a separate, deliberate decision — not an outcome of this spec.

Deployment order is §9, and the honest default at every stage is: rails and frozen
behaviour carry the position; the learned engine only ever narrows.

---

## 6. Calibration

Raw GBM probabilities are not probabilities. Calibrate with **isotonic regression**
(preferred at this n) or **Platt scaling**, fitted on a **calibration split disjoint
from both train and test**.

- Report **reliability curves** per asset class and per session phase. A model
  well-calibrated at midday and overconfident into the close is a specific, expected
  failure and the curve is where it shows.
- Report **Brier score against a climatology baseline** — the unconditional exit
  base rate — not against uniform. Beating uniform is a test of arithmetic; beating
  base rates is a test of information. This mirrors the promotion gate's
  discrimination requirement in `daytrade/forecast.py`.
- **Thresholds `p1, p2, p3` and `threshold_flatten` are chosen on the calibration
  set and then frozen.** A threshold tuned on test is a fit reported as a result.

---

## 7. Evaluation

### 7.1 Benchmarks — all four, always

| benchmark | why |
|---|---|
| fixed R target | the naive ladder |
| trailing ATR stop | the naive dynamic exit |
| VWAP-cross exit | a common discretionary heuristic |
| **the shipped frozen policy** | the actual incumbent — the only one that matters for shipping |
| **the hindsight oracle** | the unachievable ceiling |

Spec 025 lost to the shipped policy in 2 of 3 classes while its model looked
reasonable in isolation. **Report against the incumbent or the result is not
decision-relevant.**

### 7.2 Metrics

- **captured fraction of oracle R** = `realized_r / oracle_r`, the headline
- average giveback from MFE
- win rate, expectancy per trade (R)
- **per-regime breakdown** — by session phase, by realized-vol bucket, by asset class

### 7.3 Report the bound with every number

`n_unwinnable_entries = 192 / 336`. Report metrics **split by winnable / unwinnable**.
An engine whose entire gain comes from cutting dead trades a bar sooner has learned
the base rate, and pooling the two hides that.

### 7.4 Power

Every claim clears `gate/discovery.py`'s detection floor at n = **entries**. State
the MDE beside the effect, always, in the same sentence.

---

## 8. Failure modes to defend against

| failure | defence |
|---|---|
| **lookahead leakage** | §2.6 — `truncate_at` seam, `assert_no_lookahead` corruption test, separate label/feature passes, CI column-name check |
| **survivorship in the trade set** | trades come from the *entry* log, including entries that never became trades where recoverable. The 57% unwinnable rate is reported, never filtered |
| **labels teach exiting** | §2.5 weighting, frozen before the first fit. Report the model's exit rate against the base rate — if they match, it has learned the prior |
| **regime overfit** | §4.5 walk-forward with purge and embargo; §7.2 per-regime breakdown; a model winning only in one vol bucket does not ship |
| **threshold drift** | thresholds frozen from the calibration set (§6); monitor realized `P(exit)` distribution vs the calibration distribution and alarm on divergence |
| **confidence during illiquid periods** | relative-volume feature is an input, and a low-liquidity gate suppresses model authority to rails-only. Report calibration in the lowest volume decile separately |
| **already near the stop** | when `dist_to_stop_r` is small the model has almost no room to be right and the rail is about to fire. Below a declared distance the model is advisory only; the rail decides |
| **a fourth null read as a third-place finish** | §10 |

---

## 9. Build order

Ship in this order. Each stage has a verdict before the next begins.

1. **Oracle labeler** — through `ceiling.simulate` + `observer`. Output: labels keyed
   `(trade_id, bar_ts)`. Gate: `assert_no_lookahead` passes; label distribution matches
   the known 57% unwinnable rate.
2. **Feature store** — separate pass, same key. Gate: mirror test (§3.5); CI
   column-name check; every feature computable from `truncate_at`.
3. **v1 GBM, two heads** — sample weights frozen first (§2.5). Gate: permutation
   importances reported; per-class results.
4. **Calibration** — isotonic, reliability curves, thresholds frozen.
5. **Backtest harness** — walk-forward with purge and embargo built into
   `backtester/walk_forward.py`. Gate: §7 table against all five benchmarks.
6. **Paper live, shadow mode** — decisions logged, never emitted. Gate: shadow
   decisions reproduce backtest decisions on the same bars.
7. **Live with hard rails** — rails carry the position; the model narrows only.

**Stages 1–2 are the entire honest cost of finding out.** If the labeler and feature
store are clean and the v1 result matches spec 025's `oof_advantage_corr = 0.081`,
that is the answer, and §10 applies.

---

## 10. Kill criteria — pre-registered

Declared before any model is fitted, because three attempts precede this one.

**The attempt is dead if any of these holds at stage 3–5:**

1. **OOF correlation of `head_r` against realized remaining R ≤ 0.15.** Spec 025 got
   0.081. A result in that neighbourhood is the same null, and "but the architecture
   is different" is not a result.
2. **The derived policy fails to beat the shipped frozen policy by ≥ +0.05 R/trade in
   ≥ 2 of 3 asset classes**, walk-forward and entry-grouped. This is spec 025's own
   registered bar, restated so the two are comparable.
3. **The improvement does not clear the detection floor** at n = entries
   (`gate/discovery.py`).
4. **Gains vanish when split winnable / unwinnable** (§7.3) — i.e. the engine only
   cuts dead trades faster.

A kill is recorded in `MECHANISMS.json` alongside MECH-004 with its numbers, and the
frozen incumbent stands. **A null result is a result**, and the fourth one is worth
exactly as much as the first three provided it is registered in advance.

---

## Appendix — figures quoted, and how to check them

```bash
python3 -c "import json;print(json.load(open('data/daytrade/exit_evaluator_report.json'))['verdict'])"
python3 -c "import json;d=json.load(open('data/daytrade/exit_quality.json'));print(d['n_sessions'],d['n_unwinnable_entries'],d['mean_oracle_r'])"
python3 -c "import pandas as pd;print(list(pd.read_parquet('data/daytrade/bars_premarket/SPY_5m.parquet').columns))"
python3 -c "import json;print([m['status'] for m in json.load(open('MECHANISMS.json'))['mechanisms'] if m['id']=='MECH-004'])"
```
