# Council review — what reproduced, what didn't

**Date:** 2026-09-01 · A three-model review (Claude Opus 5, GPT 5.6 Sol, Gemini 3.7
Flash) of the Stockfish/AlphaZero/Nautilus stack. Every claim below was **tested
before being accepted or rejected**.

---

## The thesis is correct

> The rigour is concentrated in the decision layer, and invariants proved there are
> treated as properties of a live trading system.

Confirmed, and one finding proves it in a single line of arithmetic: the `max()`
never-loosen invariant is airtight in `effective_stop`, and the order that reached
the venue could still be **half a cent looser**, because `make_price` rounds to
nearest. The proof has no jurisdiction over quantization.

## Three claims did not reproduce

| Claim | Test | Result |
|---|---|---|
| **Short-side `max()` inverts the stop** — all three models, the review's "highest-confidence technical finding" | mirror-image long vs short, identical distances | **Exact mirror.** Stop-distance paths identical to 1e-9. `max(level*direction)` and `entry − direction·k·ATR` are both side-aware. |
| A persisted stop ratchet is needed when TIGHTEN lifts | drive RIDE → tighten → lift | **Stop held at 103.925.** `state.sl` plus the `tighter` emission gate already *is* the proposed ratchet. |
| 44 written / 13 resolved = a 70% file drawer | recount by row kind | **17 written, 13 resolved, 4 unresolvable, 0 open.** `forecasts.jsonl` is a mixed-kind ledger. This was **my own miscount**, retracted a day earlier, propagating into the review. |
| No model identity versioning | `forecast.py:224` | Scoring **does** partition on `model_version`. Only `prompt_version` was missing — the narrower claim was real and is fixed. |

The short-side claim matters most as a lesson: three independent models agreed on a
defect that a ten-line mirror test refutes. Convergence is not evidence.

---

## Four holes were real. All four are closed.

### 1 · Quantization loosened the stop at the venue

`make_price(103.925) → 103.92` on a long stop, and `make_price(96.075) → 96.08` on a
short. Both **looser**, at the exact boundary the `max()` proof cannot see.

`quantize_protective()` rounds a stop **always toward tighter** — long up, short down
— with a post-condition that raises if the quantized level is looser than computed.
Fixed at the venue boundary in `body/`, deliberately **not** in
`daytrade/stockfish_exit.py`: that file is pinned by `SF-FROZEN-004`, and
quantization is a venue property rather than a decision property.

> **The first version of this fix passed every test while being reverted.** Testing
> `quantize_protective` in isolation proves the function correct and proves nothing
> about the order that reaches the venue — reverting the call site to `make_price`
> left the suite green. There is now an AST guard on the call site itself. That gap,
> inside the fix for exactly that gap, is the most instructive thing in this session.

### 2 · T_SIM had one sensor, not two

`no_live_venue` was a hardcoded `Truth.TRUE` beside a comment claiming it was
"independently re-checked from the injected clock." It was not re-checked, and the
clock is what `authorize_entry` already reads — one sensor counted twice. **I wrote
that comment and repeated the claim in a published artifact; both were wrong.**

The second sensor now reads a genuinely different object: which `ExecutionEngine`
class the kernel constructed. A live `TradingNode` builds `LiveExecutionEngine`; a
`BacktestEngine` builds the plain one.

> **The naive version was vacuous and shipped for about thirty seconds.**
> `registered_clients` returns `ClientId`s, not client objects, and there is no
> `get_client` — so `isinstance(c, LiveExecutionClient)` is False for every element
> and the check "passes" while measuring nothing. A test now pins the distinction by
> asserting the sensor returns True for a live engine and False for a simulated one.

### 3 · The promotion gate had no discrimination requirement

`baseline_brier` was **uniform only**. A forecaster emitting unconditional base rates
beats uniform forever with zero information, is perfectly calibrated by construction,
scores constantly across regimes, and never says EXIT — passing four gates at once.

Added: a climatology baseline, a `skill_vs_climatology` score, and a **bootstrap CI
that must exclude zero** (deterministic seed — a gate whose verdict moves between
runs is not a gate). `prompt_version` now joins the scoring partition.

> **The repo's own "brilliant challenger" fixture has negative skill.** The 0.8
> hit-rate report used to represent *genuinely better* beats uniform 0.34 vs 0.50 —
> and **loses to climatology, 0.34 vs 0.32, skill −0.0625.** The council's argument,
> demonstrated on this project's own test data. It is now pinned by a test.

Also guarded, before it happens: when 49 of 50 decisions are in and the tail gate
still reads `REGRET_DATA_MISSING`, defining `policy_regret_r = 0.0` for capped
directives will be tempting and technically defensible — and silently converts a tail
gate into one that cannot fail. An all-zero regret distribution is now refused as
`REGRET_ALL_ZERO_SUSPECT_IMPUTATION`.

### 4 · The cost constant is 15.5× too high, and it sets the floors

`SLIP_BPS = 2.0` is **15.5× the SPY half-spread** (0.13 bps on a 1-cent quote), and
slippage is **66%** of the modelled `$0.1176`/share. Since costs — not the effect —
set both economic floors, the closed register carried an error bar it did not show.

**Ruling: sensitivity only. Every verdict stands.** `floor_test.py --sweep`:

| cost/share | macro_decay floor | E[R] | vs floor | fomc floor | E[R] | vs floor |
|---|---|---|---|---|---|---|
| $0.015 | 0.1871 | +0.7266 | **388%** | 0.1474 | +1.4422 | **978%** |
| $0.030 | 0.3743 | +0.6331 | **169%** | 0.2948 | +1.3685 | **464%** |
| $0.050 | 0.6238 | +0.5083 | 81% | 0.4913 | +1.2702 | **259%** |
| $0.080 | 0.9980 | +0.3212 | 32% | 0.7861 | +1.1228 | **143%** |
| **as frozen** | **1.2915** | **+0.1745** | **14%** | **1.0585** | **+0.9866** | **93%** |

Both families clear comfortably at realistic costs. **That does not reopen anything.**
`k_stop` and the fill model were frozen at `3b10e8b` *before* the computation,
precisely so they could not be retuned once FOMC landed at 93%. The 15.5× figure is
recorded as a defect **in the pre-registration**, not as a licence to rerun it.
Reopening requires a new pre-registration written before these numbers are looked at
again. The frozen row is computed through the real price-scaled path, not a flat
average, and an assertion fails the sweep if it does not reproduce the published
floor exactly.

---

## Not acted on

The review's argument that the entry-null is anomalous and the 0/396 exit sweep is
confounded by edgeless entries is **interesting and may well be right**. It is a new
hypothesis about existing results, which in this repo means a pre-registration, not a
patch. Recorded, not actioned.

## Verification

```bash
python3 -m pytest daytrade/ scripts/ gate/ az/ -q   # 793 passed, 1 skipped
.venv-v1/bin/python -m pytest body/ -q              # 100 passed
python3 scripts/floor_test.py --sweep
.venv-v1/bin/python scripts/sim_session_run.py --sessions 20   # 20/20, unchanged
```

**7 of 7 mutations caught** — including the one that survived the first attempt
(quantization reverted at the call site), which is why the call-site guard exists.

---

# Follow-up: templates 1a and 1b

## 1a · The null diagnostic — the misspecification runs the other way

The review's premise is right (a permutation test is exactly level-α only under
exchangeability) but the entry study did **not** use a naive permutation.
`az/prereg.py::max_stat_null` is a **circular day-shift**, which preserves
within-day structure *and* cross-cell correlation. What it does not preserve is
day-to-day dependence.

`scripts/null_diagnostic.py`, on synthetic data with a day-level AR(0.85):

| null | p95 of best-cell |
|---|---|
| naive permutation | −0.0324 |
| **circular day-shift** (what the study used) | −0.0406 |
| stationary block over days (preserves runs) | **+0.0855** |

Both the naive shuffle and the day-shift are **anti-conservative** — their nulls
sit ~0.13 *below* a dependence-preserving one. So a correctly-specified null
would be **higher** than the +0.4310 on record, and the observed best cell of
+0.0956 sits **even further below it**.

**Pre-specified threshold-crossing rule, declared before the numbers:** reconsider
the entry closure only if a correctly-specified null's p95 falls *below* +0.0956.
**Not crossed. The closure is strengthened, not weakened.**

> **Load-bearing caveat.** This is synthetic. The entry study's 104,680 candidate
> arrays were **never persisted**, so its null cannot be re-derived from committed
> data — the same reproducibility gap closed for `spy_macro_decay` and still open
> here. The direction is established; the magnitude on the real study is not.

## 1b · Edge preservation — the column that was missing

0/396 answered *"can exits create an edge"* (no — that is optional stopping, a
theorem). It was never evidence about *which exit keeps one*, because the sample
had none to keep. `scripts/edge_preservation.py` injects a known drift and measures
`mean R under policy ÷ mean R holding to horizon`, running the **real**
`decide_exit` over synthetic paths.

| policy | drift +0.0002 | drift +0.0005 |
|---|---|---|
| **RIDE** | **0.94×** | **0.94×** |
| HARVEST | 0.86× | 0.85× |
| DEFEND | 0.71× | 0.81× |
| SALVAGE | 0.71× | 0.77× |

**The tension worth naming:** the frozen defaults were fitted from an oracle on an
*edgeless* sample, where 22 of 24 best picks used **no trailing at all**. Under
injected drift the **trailing** policy preserves most. Those are answers to two
different questions, and the second one had never been asked — which is precisely
the confounding the review identified.

`EVENT` is excluded because the engine refuses it: `policy_params("EVENT")` raises
without `flatten_at_et`, since it means *"be out before a known catalyst"* and a
synthetic path has no catalyst. Supplying a default clock would invent the thing
the policy exists to respect.

### The control earned its place twice

Under drift = 0 no policy may beat hold. Two bugs were caught by that arm alone:

1. **`exp(cumsum(N(0,σ)))` is not a price martingale** — zero drift in *log* space
   is +σ²/2 in price space. The control showed every policy "beating" hold by
   ~0.16R on what was supposed to be a no-edge process.
2. **The R accounting double-counted partials** — crediting a fixed
   `1 − goal_fraction` remainder instead of tracking the surviving fraction, which
   mis-weighted the runner and broke policies that exit fully at TP2.

The control tolerance is now **derived** (3 × the paired standard error) rather
than a chosen constant — a fixed threshold makes the control's verdict a function
of how many paths happen to be run. The run **refuses to report** the drifted rows
if the control fails.

**Neither script reopens anything.** 1a confirms a closure by a pre-specified rule;
1b adds a forward-looking column and explicitly is not evidence that an edge exists.
