# 045 — CARRY EXIT TABLEBASE vs CARRY-FROZEN-001  `[SPEC]` `[PRE-REGISTERED]`

**Component:** `sovereign/forex/exit_tablebase.py`, `sovereign/forex/sprt.py`,
`sovereign/forex/fill_model.py`, `scripts/carry_tablebase_paths.py`,
`scripts/carry_bench.py`, `scripts/carry_exit_sprt.py`.
**Opponent:** `CARRY-FROZEN-001` (`data/carry/CARRY_FROZEN_001.json`, config
sha `84a1d430c96b…`) — `sovereign/forex/exit_machine.decide_exit` under
`ForexBacktester`'s constants and `_apply_costs`.
**Written:** 2026-08-29, before any candidate was fit. Session brief of the same
date; plan `Plans/cheeky-mixing-locket.md`.

This document is the fresh pre-registration that **HYP-071**'s reopen condition
requires (`~/quant`, sealed METRIC_ARTIFACT 2026-06-30: a λ-penalized tabular
exit value mechanically favours EXIT_NOW because a locked current value has
zero forecast variance; reopen only with *pure E[R], no λ penalty*). Nothing
here optimizes variance. Variance is reported.

---

## 1. The question

Given the incumbent's own entries, does a policy computed by backward induction
over a discretized state of the realized path — deciding only HOLD or EXIT on
bars 1..H_max — realize more R per independent unit than the incumbent's own
exit rule, out of sample, by at least δ?

- **H0:** mean paired ΔR per unit = 0 (no improvement).
- **H1:** mean paired ΔR per unit = δ (the minimum improvement worth acting on).
- **Test:** Wald SPRT, normal model, σ declared a priori (§6), α = 0.05, β = 0.20.

**Prediction, written before the run:** `ACCEPT_H0` under the base fill. The
in-sample shape of the 350 (trailing exits avg −0.55R, time exits +0.43R) says
the table will learn "hold through the trail"; that story has been measured as
harmful on the intraday lane (MECH-001) and is expected to be smaller than δ
here. A `ACCEPT_H0` is a complete, successful outcome: the incumbent stays.

## 2. The population, and why not the 411

`data/cb_ab/cb_off_trades.csv` — **350 trades**, 2015-01-02→2024-12-09, the
incumbent engine with `CB_LAYER_DISABLED=True`, R = `pnl_pct/risk_pct`,
ΣR = 34.4062. The sealed 411 is NOT the population: 102 of its entries sit in
windows opened by the fabricated `cb_decisions.json` (commit `55ac5a6`). The
411 may be reported as a secondary with **no inferential weight**.

Known caveat, not fixed here: the 350 were generated under nominal-vintage
rates (spec 039 look-ahead in the *entry* signal). The comparison is paired on
identical entries, so entry-side contamination is common to both arms and
cancels in ΔR. Red-team item.

## 3. The frozen path dataset

`scripts/carry_tablebase_paths.py --h-max 10` replicates `run_cb_ab`'s cb_off
arm, captures the rig's per-pair arrays by wrapping
`fast_backtester.simulate_forex_trades_arrays`, and emits:

- `artifacts/carry_trades.parquet` — one row per trade (entry/stop/hold_limit/
  trailing_mult/cost fractions/incumbent exit/`unit_id`).
- `artifacts/carry_paths.parquet` — one row per (trade, decision bar) t = 1..,
  ending in exactly one **absorbing** row: `absorbed_by ∈ {STOP, REVERSAL,
  CB_REFRESH, HMAX}` with `terminal_r` (net of base cost).
- `artifacts/carry_units.json` — connected components of the interval graph
  `[entry_date, path_end_date]` across all pairs. **The unit is the SPRT
  observation.** Entry-date clusters are reported as secondary.

The extractor HALTS unless n = 350, `round(ΣR_net, 2) == 34.41`, every trade
matches exactly one captured trade, the cost replication matches gross−csv to
1e-12 on all 350, and replaying every path through `exit_machine.decide_exit`
reproduces the incumbent's exit bar and reason (G7 parity). The parquet's
sha256 is recorded in `artifacts/inventory.json` and attached to
CARRY-FROZEN-001; the driver refuses to run against any other bytes (I64).

Path semantics:
- Decision bars are t = 1..H_max (`H_max = 10`). The candidate may EXIT at any
  decision bar or HOLD.
- **Forced terminals** apply to both arms identically, on any bar: INITIAL_STOP
  (close through `stop_price`, as `decide_exit` evaluates it), REVERSAL,
  CB_REFRESH (signal-driven). TIME and TRAILING_ATR are the incumbent's
  *choices* and the candidate may hold through them; the path continues on
  the pair's real bars.
- **HMAX terminal** at t = H_max if still unabsorbed: if the incumbent was still
  in (`incumbent_hold > H_max`) the candidate follows the incumbent from there
  and inherits `incumbent_r_net`; otherwise the candidate is capped at
  `close[H_max]`. Long holds are inherited, not handicapped.

## 4. The candidate

**State key** `s = (r_bin, t_bucket, weekend_next)`:
- `r_bin` — 5 bins of `unrealized_r_net` (net of base cost at that bar's close)
  with edges at the {0.2, 0.4, 0.6, 0.8} quantiles of **training decision rows
  only**, written to `artifacts/tablebase_bins_block{k}.json` and hashed
  before use (I65).
- `t_bucket` — {1, 2-3, 4-5, 6-8, 9-10}.
- `weekend_next` — 1 if the next bar is ≥ 3 calendar days away, else 0.

Deliberately small: 350 trades cannot populate a 1,620-cell table; `atr_ratio`,
`swap_r`, `rate_diff` are not policy keys in v1 (`swap_r`/`rate_diff` are null
for 2015-16 anyway). One declared discretization — not a menu (`null_leak`
grows with the number of alternatives).

**Value function (pure E[R]):** for a training set, over decision rows i with
key s_i and level t_i:

```
exit_value(i)   = unrealized_r_net(i)                       # exit at this close, base fill
next_value(i)   = terminal_r(i+1)  if row i+1 is absorbing
                = Vπ(s_{i+1})      otherwise                 # policy value of the next state
Q_exit(s) = mean_i∈s exit_value(i)
Q_hold(s) = mean_i∈s next_value(i)
```
Gauss-Seidel sweeps over cells in descending `t_bucket` until no cell changes
(> 1e-12); the graph is acyclic in t so this converges in ≤ H_max sweeps.

**Policy per cell:**
- `N(s) < n_min` → **FALLBACK**: follow the incumbent *on that path*
  (row-level): if the incumbent exits at this bar or later, the candidate
  inherits the incumbent's exit; if the incumbent already exited (extended
  row), exit now. `Vπ(s)` = mean of those row-level values. No `max` (I67).
- otherwise action = argmax(Q_exit, Q_hold) computed on the full training
  set; tie → FALLBACK. **`Vπ(s)` is cross-fit:** training units split A/B by
  seeded shuffle; the argmax chosen on A is valued on B and vice versa; `Vπ`
  is the mean of the two out-of-half values. The cross-fit value is what the
  recursion consumes upstream, so selection optimism does not compound.

**Applying the policy to a test trade:** walk decision rows t = 1..; absorbed →
terminal; EXIT → exit at this bar; HOLD → continue; FALLBACK → follow the
incumbent as above; unabsorbed at H_max → HMAX rule.

**Candidate R:** every exit the candidate *decides* (EXIT, or FALLBACK
following an incumbent exit) is priced by the arm's fill model —
`BaseFill` (close, `_apply_costs`) or `PessimisticFill` (fill at `open[t+1]`,
spread ×2, slippage ×2, one extra financing day). Forced terminals and
inherited HMAX exits are not decisions; they are priced base in both arms.
Per trade `ΔR = R_candidate − R_incumbent`; per unit ΔR = mean over its
trades (sum reported).

## 5. Out-of-sample protocol

Anchored walk-forward over **units**, ordered by earliest entry date:

| block | start | role |
|---|---|---|
| B1 | 2015-01-01 | train only |
| B2 | 2017-01-01 | test 1 (train B1) |
| B3 | 2018-10-01 | test 2 (train B1-B2) |
| B4 | 2021-04-01 | test 3 (train B1-B3) |
| B5 | 2023-03-01 | test 4 (train B1-B4), ends 2024-12-31 |

Purge: a training unit is dropped if any of its paths ends on or after the
test block's start. No post-test embargo is needed (anchored; nothing after
the test block is ever trained on). Bin edges, `n_min` counts, cross-fit
values and the policy are recomputed per test block from its own train set.
Self-exclusion is asserted by raise: no training path window overlaps the
test block (I66, `measure`-style `MeasurementError`, never a warning).

The OOF sequence is B2 units, then B3, B4, B5, each in chronological order —
the only ordering in which a sequential test is legitimate.

## 6. The gate

1. **SPRT, base fill.** `sprt(unit_deltas, delta=δ, sigma=σ, alpha=0.05, beta=0.20)`.
   `LLR_i = (δ/σ²)(d_i − δ/2)`; `A = ln((1−β)/α) = 2.7726`; `B = ln(β/(1−α)) = −1.5581`.
2. **SPRT, candidate-pessimistic.** Same δ, σ, bounds; the candidate's decided
   exits re-priced by `PessimisticFill(spread_mult=2.0, slip_mult=2.0, delay_bars=1)`.
3. **Sign-flip permutation** on unit ΔR (base): 10,000 draws, seed 20260829,
   one-sided p for mean ΔR > 0 — the distribution-free companion, because ΔR
   has a point mass at 0 and stop-driven tails.

**σ and δ come from the incumbent, never the candidate:**
- `σ` = standard deviation of the incumbent's per-unit R over all units of the
  350 (candidate-independent; overestimation only lengthens the test).
- `δ` = `daytrade.mechanisms.mde(σ, n_units_oof)` — the one-sided 95% / 80%
  minimum detectable effect over the units the SPRT can consume (test blocks
  B2-B5). The rule is pre-registered; the number is filled in §7 from
  `artifacts/carry_units.json` before the first candidate fit.

**Decision rule, pre-registered:** the candidate replaces nothing unless
(1) = `ACCEPT_H1` **and** (2) = `ACCEPT_H1` **and** (3) p < 0.05. Any other
combination → CARRY-FROZEN-001 stays the exit rule, the outcome is logged as
a null with its specific stopping decision, and nothing is re-run with a
changed parameter. An `ACCEPT_H0` or `INCONCLUSIVE` reached with a deviation
rate below 10% is reported as **"no deviation"** — the table agreed with the
incumbent — not as "deviation failed".

## 7. Declared parameters (required arguments in code; no defaults)

| parameter | value | source |
|---|---|---|
| `H_max` | 10 bars | 2× the modal hold_limit (208/350 trades hold 5); at 60 the 4-pair overlap graph has 2 components over the decade |
| `t_bucket` | {1, 2-3, 4-5, 6-8, 9-10} | declared |
| `r_bin` | 5, edges at train-row quantiles {.2,.4,.6,.8} | declared |
| `n_min` | 20 visits | declared |
| cross-fit split seed | 20260829 | declared |
| walk-forward blocks | 2015-01-01, 2017-01-01, 2018-10-01, 2021-04-01, 2023-03-01 | declared (≈ equal unit counts) |
| α, β | 0.05, 0.20 | declared |
| pessimistic | spread_mult 2.0, slip_mult 2.0, delay_bars 1 | declared |
| permutation | 10,000 draws, seed 20260829 | declared |
| `n_units` (all) | <<FILL from carry_units.json>> | incumbent |
| `n_units_oof` (B2-B5) | <<FILL>> | incumbent |
| `σ` | <<FILL: SD of incumbent per-unit R>> | incumbent |
| `δ` | <<FILL: mde(σ, n_units_oof)>> | rule above |

The `<<FILL>>` cells are filled by the commit that follows the path extraction
and **precedes** the commit that adds `exit_tablebase.py`. After the first run
of `scripts/carry_exit_sprt.py`, no cell in this table changes; a different
value is a different spec (046), not an amendment.

## 8. Falsified register — do not re-propose without new evidence

- **HYP-071** (`~/quant`, 2026-06-30) — λ-penalized tabular exit value:
  METRIC_ARTIFACT. This spec is its reopen condition, not a side door.
- **MECH-001** — wider trailing captures more tail: indeterminate/harmful on
  the intraday lane; `trail_mult` removed.
- **MECH-004** — entry-time features pick the exit config per day: killed.
- **Spec 025** pooled exit evaluator: `NO_SUPERSEDE`. **Spec 034** carry-exit-v1:
  44.8% reconciliation — not a candidate.
- `oracle_audit` ×2: `NOTHING_QUOTABLE`; `null_leak` 1.58 vs a 0.15 gate — the
  reason this spec has exactly one candidate and one discretization.

## 9. Invariants (each has a named test and a mutation that makes it fail)

| id | invariant | test |
|---|---|---|
| I63 | every new measurement script asserts self-exclusion by raise | `test_driver_asserts_self_exclusion` |
| I64 | the driver halts on an unhashed or moved dependency | `sovereign/forex/test_inventory.py`, `test_driver_halts_on_unhashed_dependency` |
| I65 | bin edges derive from training rows only | `test_bins_from_train_rows_only` (perturb a test row → edges unchanged) |
| I66 | no training path window overlaps the test block | `test_no_train_test_overlap` |
| I67 | an under-visited cell follows the incumbent per row, never 0 | `test_undervisited_cell_follows_incumbent` |
| I68 | `BaseFill` ≡ `_apply_costs` on the 350 | `test_base_fill_equals_apply_costs_on_350` |
| I69 | incumbent-vs-itself → `ACCEPT_H0` at `⌈|B|·2σ²/δ²⌉`, never H1 | `sovereign/forex/test_sprt.py` |
| I70 | bench: two runs identical; a one-parameter mutation changes the number | `test_bench_reproducible`, `test_bench_mutation_changes_number` |
| I71 | terminal rows are absorbing: a synthetic set stopping at t=3 gives `V(s,1)` = the stop R, not the pre-stop mean | `test_terminal_rows_absorbing` |

## 10. What the report must contain

`artifacts/sprt_result.json`: per-arm LLR trace and stopping decision;
`n_units`, `n_units_oof`, `n_consumed`; σ, δ, MDE; deviation rate (units with
ΔR ≠ 0) and per-block coverage (cells above `n_min`); empirical σ_ΔR (reported,
not used); mean/sum ΔR per block; permutation p; the 411 secondary (labelled
NO_INFERENTIAL_WEIGHT); hashes of every input consumed. `artifacts/
entry_value_function.json`: `Vπ(s_entry)` per block, labelled
in-sample-optimistic, with the OOF-realized mean R per entry state beside it —
the handoff to `~/quant` for any entry-gate work (CLAUDE.md #1 keeps that
work out of this repo).

## 11. Out of scope

Trading anything; any change to `exit_machine.py`, `carry_exit.py`, spec 034's
exit vocabulary (I58 — opened only on a pass, by ruling); any `MECHANISMS.json`
edit; rebuilding vintage caches; an entry gate.
