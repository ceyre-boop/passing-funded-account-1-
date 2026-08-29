# carry-exit tablebase vs the frozen incumbent — Phase 0 gap list + build plan

**Session brief 2026-08-29.** Finish the engine: exits as a solved endgame, gated
by SPRT against a frozen incumbent, with a bench, a red team, and a report.
This file is the Phase 0 deliverable ("show me the gap list before implementation
code") and the plan that follows from it. Revised once after an adversarial
design review (findings folded in; see "What the review changed").

## Context — what discovery changed about the brief

Discovery was programmatic (three parallel read-only audits + shell summaries;
no raw data entered context). Five premises of the brief do not survive contact
with the repo, and the plan is built on what is actually there:

1. **Two `decide_exit`s, two lanes.** `daytrade/stockfish_exit.py:486` is the
   intraday engine; **SF-FROZEN-001/002** (`data/daytrade/SF_FROZEN_00{1,2}.json`)
   pin *that* engine (`engine_sha256` current `eb12b4a4…d9991`). The carry
   incumbent is a different function — `sovereign/forex/exit_machine.py:73
   decide_exit(state, bar, cfg)`, the rule that produced the sealed 411 — and
   **it is content-hashed nowhere**; `SEALS.json` hashes output CSVs only.
   `frozen_policy.policy("FX_CARRY")` *raises* by design (spec 034 I59). The
   gate tonight is **candidate vs `exit_machine`**, pinned first as
   CARRY-FROZEN-001.
2. **"carry-exit-v1" already exists and cannot be the candidate.** Spec 034's
   `daytrade/carry_exit.py` reproduces **44.8%** of sealed exit reasons (no
   term for the entry signal → `reversal` 0%). It stays a vocabulary, not a
   policy.
3. **The 10,188-row vector is `data/carry/fx_state.jsonl`** — 28 columns, now
   4 × 2,552 rows (append-only; 10,188 was its 2026-08-20 size),
   2016-11→2026-08, two columns 100% null. It starts 22 months after the
   sealed span, so paths come from the rig's own arrays instead.
4. **The population is decided by contamination.** 102 of the 411 entered
   inside windows opened by the fabricated `cb_decisions.json` (`55ac5a6`).
   The honest population is `data/cb_ab/cb_off_trades.csv`: **350 trades,
   2015-01→2024-12, avg R +0.098, perm p 0.0525**, R = `pnl_pct/risk_pct`,
   Σ R = **34.4062**. The 411 is a secondary with no inferential weight.
5. **This is HYP-071's reopen condition, not a new idea.** `~/quant` killed a
   tabular exit value function on 2026-06-30 as **METRIC_ARTIFACT**: the
   λ-penalized value mechanically favours EXIT_NOW (a locked current value has
   zero forecast variance). Reopen condition on record: *a fresh prereg using
   pure E[R] without the λ penalty*. The brief's "explicit variance penalty"
   would rebuild the artifact. **Tonight: pure E[R]; variance reported, never
   optimized; the only success criterion is realized out-of-sample R vs the
   incumbent under SPRT.** Spec 045 declares itself that fresh prereg.

In-sample, **no inferential weight — the hypothesis, not a result**: on the
350, `time` exits n=204 avg **+0.43R**; `trailing_stop` n=90 avg **−0.55R**;
`stop` n=10 −1.74R; `reversal` n=45 +0.17R. Expect the table to learn "hold
through the trail". Pre-registered modal outcome: **ACCEPT_H0** (the candidate
does not clear δ), incumbent stays frozen. That is a complete success.

## Phase 0 inventory (what exists, where, hashed?)

| Thing | Where | Hashed? |
|---|---|---|
| Intraday exit engine | `daytrade/stockfish_exit.py` (`decide_exit` :486, 8 callers, no bypass on its lane) | Y — SF-FROZEN-002 |
| Carry exit incumbent | `sovereign/forex/exit_machine.py` (:73; callers `fast_backtester.py:92`, `forex_exit_manager.py:194`) | **N** |
| Incumbent config + costs | `forex_backtester.py` `STOP_ATR_MULT`=2.0 :116, `TRAILING_ATR_MULT`=1.25 :117, `PAIR_TRAILING_OVERRIDES` :132, `HOLD_DAYS` :114, `_apply_costs` :524 (reads `swap_model` + `data/execution/calibrated_costs.json` — **both calibration files ABSENT → static tables**) | **N** |
| Parity test for the extraction | docstring cites `tests/test_exit_machine.py` | **ABSENT here** |
| Bypasses of `decide_exit` | `paper_carry_runner.py:386` (paper ledger — out of scope), `execution/funderpro_executor.py:307`, `execution/harness.py:306` (ICT lane, inert) | n/a |
| Honest population | `data/cb_ab/cb_off_trades.csv` (350) + `stats.json`; produced by `run_cb_ab._run_arm` (`CARRY_RATE_VINTAGE=nominal` + `CB_LAYER_DISABLED` patch) | N |
| Path data | `data/research/spot_cache/{PAIR}_ohlc.parquet` 3,147 rows/pair, no gaps >5d | N |
| Self-exclusion | `daytrade/measure.py:43 exclude_self` (raises), `:81 assert_disjoint` | — |
| Scripts lacking it | 10× `scripts/build_*_study.py`, `ruin_engine`, `carry_buy_gate`, `drawdown_margin`, `run_cb_ab`, `run_vintage_ab`, `forex_backtester` | — |
| CV / holdout | `exit_evaluator.day_grouped_oof` (intraday), `backtester/holdout_guard.py`, `daytrade/splits.py`; **no purge/embargo/walk-forward on carry** | — |
| SPRT | **ABSENT** | — |
| Reusable stats | `vintage_ab_stats.perm_null`, `eval_lab.bootstrap_ci`, `_bh_adjust`, `daytrade/mechanisms.mde` | — |
| Fill model | `_apply_costs` inline, no pessimistic mode (`backtester/realistic_fills.SCENARIOS` is ICT-lane only) | — |
| Feature provenance | `synthetic_fields`/`source_map` label, never raise; `as_of_computable` **ABSENT**; LLM/news features imported by live modules only — **no backtest reads them** (verified) | — |
| HYP-027 | **ABSENT** repo-wide | — |
| Bench / `artifacts/` / pre-commit | **ABSENT** (only `.githooks/commit-msg`) | — |
| Sealed register | 6 SEALS.json + 2 frozen_policy hashes: **8/8 match** | Y |

Suite 1149 / 8 skipped / 1 deselected at `a564683`. Dirty tree is operator
ticks + `paper_tsmom_*` (TSMOM lane) — untouched.

## GAP LIST — ordered by whether it blocks the freeze decision

| # | Gap | Blocks freeze? | Tonight |
|---|---|---|---|
| G1 | Carry incumbent has no content hash (engine, config, cost tables, spot cache, absence of calibration files) | **YES** | close |
| G2 | No per-bar path dataset for the 350; the rig drifts across days (`anchor_check` 288→397 on 08-27) — extract once, freeze, hash | **YES** | close |
| G3 | No SPRT | **YES** | close |
| G4 | No out-of-sample scheme that respects 4-pair overlap: at H=60 the overlap graph has **2** components over the decade; at H=10, ~97 | **YES** | close (anchored walk-forward, overlap-component units) |
| G5 | No bench; no pre-commit | **YES** | close |
| G6 | Nothing halts on an unhashed dependency | **YES** | close |
| G7 | `exit_machine` ≡ `_simulate_forex_core` parity unproven here | **YES** | close (falls out of G2) |
| G8 | `carry_exit.py` is not a candidate (44.8%) | no | record |
| G9 | No pessimistic fill on the carry path | no (brief: yes) | close, candidate-only |
| G10 | No `as_of_computable` registry | no (brief: yes) | minimal: dict + raising loader, last |
| G11 | Self-exclusion missing from ~17 legacy scripts | no | new scripts assert it; legacy list in report |
| G12 | Entry gate: HYP-027 absent; CLAUDE.md #1 forbids new hypotheses here | no | **out of scope** — `V(s_entry)` artifact for `~/quant`, labelled in-sample-optimistic with OOF-realized beside it |
| G13 | Vintage caches absent; the 350 were produced under nominal (look-ahead) rates | no | paired design cancels entry effects in ΔR; red-team item |
| G14 | Swap is modeled (static fallback) | no | not a v1 feature; recorded |
| G15 | Spec 034 I58 refuses a fifth exit reason | only if SPRT passes | ruling deferred |

## What the review changed (design defects caught before build)

- **H_max 60 → 10 bars.** 208/350 trades are hold 5; at 60 the SPRT would step over one continuous window. After bar 10 the candidate follows the incumbent (absorbing `HMAX` terminal = the incumbent's realized R), so long holds are inherited, not handicapped.
- **Terminals are absorbing states.** STOP / REVERSAL / HMAX rows carry `terminal_r`; they are visits and transitions, or HOLD wins by survivorship.
- **Fallback is per-row.** A cell with `< n_min` visits follows the incumbent *on that path*; its value is the mean of the incumbent's own outcomes there, no `max`.
- **Cross-fit inside train** (A/B split: argmax on A, value on B, swap, average) against Jensen optimism.
- **σ is the incumbent's**, declared a priori (SD of per-unit R on the 350) — candidate-independent; overestimation only lengthens the test. `sprt()` raises on σ ≤ 0 or NaN.
- **δ is a rule, not a guess**: δ = `mechanisms.mde(σ_inc, n_units)` (one-sided 95/80 — matches α .05 / β .20). ≈0.25R at n≈97. Expected stopping ≈81 units under H0, ≈95 under H1 — the test will usually terminate.
- **Anchored walk-forward, not K-fold** — OOF ΔR has a real chronological order; block starts 2015-01, 2017-01, 2018-10, 2021-04, 2023-03; test blocks 2..5; purge train trades whose window crosses the test start. Plus a sign-flip permutation on mean paired ΔR as the distribution-free companion (ΔR has a point mass at 0). Both required.
- **Pessimistic fill is a handicap**: candidate pessimistic, incumbent base; `partial_fraction` cut (undefined in exit-only replay); `delay_bars=1` needs `open[t+1]`, so one extra bar per path.
- **Capture the rig's arrays, don't recompute**: wrap `fast_backtester.simulate_forex_trades_arrays`; store per-trade `stop_price, hold_limit, trailing_mult, cost fractions`; parquet only (CSV float round-trip breaks `price <= trail_stop`).
- Dropped from v1: `swap_r`, `rate_diff`, `atr_ratio` (null for 2015-16; not policy keys), `purged_cv.py` embargo machinery, stratified diagnostics.

## Governance that binds every step

- **Trade-evidence freeze (CLAUDE.md #6).** Library in `sovereign/forex/`, drivers in `scripts/`, outputs in `artifacts/`. Exactly one new file under `specs/`, committed with `FREEZE OVERRIDE: session brief 2026-08-29 — prereg must precede the run`. No new `daytrade/` modules.
- **Untouched:** `data/proof/`, `SEALS.json`, `paper_carry_runner.py`, paper/TSMOM ledgers, `forex_exit_manager.py` (SHADOW), `MECHANISMS.json`, `SF_FROZEN_*`.
- **Parameter firewall.** Every number is declared in spec 045 and passed as a required argument. **Never loosened after the first run** — a change is a null result.
- **Seats.** Architect (this seat): spec 045, checkpoint, re-verification of every self-report by re-running it. Sonnet builders, one write stream at a time, each in `isolation: "worktree"`. Forge (GPT-5.4, CLAUDE.md E3+ rule) implements `sprt.py` from the spec in its own worktree with a disjoint footprint so the gate is cross-vendor; if `codex` is unavailable, Sonnet builds and an Opus reviewer recomputes the fixture LLR by hand.

## Build order (each step ends in a verified checkpoint commit with artifact hashes in the message; the report is complete after any step)

**Step 0 — `artifacts/` + inventory.** `scripts/build_inventory.py` → `artifacts/inventory.json` with a `hashes` map; `inventory.require_hashes([...])` **halts** on missing or mismatched — every driver calls it first.

**Step 1 — CARRY-FROZEN-001.** `sovereign/forex/carry_checkpoint.py` (sibling of `frozen_policy`, never merged — spec 034 separation). Pins sha256 of `exit_machine.py`, `fast_backtester.py`, the `ExitConfig` values and cost tables actually used, the 4 spot-cache parquets, `swap_model.py`'s static table, the population CSV, and asserts both calibration files are absent. `pin(why=)`, `verify()`, refuses re-pin. `data/carry/CARRY_FROZEN_001.json`. Test: byte-flip any input → `verify() != 0`.

**Step 2 — Path extractor.** `scripts/carry_tablebase_paths.py`: replicates `run_cb_ab._run_arm` (env + patch), wraps `simulate_forex_trades_arrays` to capture `opens, closes, signals, hold_days, atr_pcts, index` per pair, and emits `artifacts/carry_paths.parquet`: one row per (trade, bar) from the entry bar (decision bar 1, `hold_count=1` at its close) to `max(entry+H_max, incumbent exit)` plus `open[t+1]`, with `unrealized_r`, `bars_held`, `weekend_next`, the incumbent's action, and one absorbing terminal row (`absorbed_by ∈ {STOP, REVERSAL, HMAX}`, `terminal_r` net of base cost). Also emits `artifacts/carry_units.json`: overlap components of `[entry, path end]` across pairs (the SPRT unit), with entry-date clusters as secondary. **Halts unless** n = 350 and `round(ΣR, 2) == 34.41`. Parity test (G7): replaying the parquet through `exit_machine.decide_exit` reproduces every incumbent exit bar and reason. Hashes into inventory; checkpoint amended to include the parquet.

**Step 3 — Bench + hook.** `scripts/carry_bench.py`: parquet → pinned incumbent → prints **one number** (ΣR, full precision) — no rig import. `.githooks/pre-commit` runs it and fails on any diff from `artifacts/bench.txt`; installed at the *end* of this step. Fault injection: a one-parameter mutation changes the number; two clean runs are identical.

**Step 4 — `specs/045_CARRY_EXIT_TABLEBASE.md` `[SPEC]` (architect seat), FREEZE OVERRIDE commit.** Written after Step 2 because `n_units`, σ_inc and therefore δ come from the *incumbent's* data — never from the candidate. Declares everything in "Declared parameters", the prediction (ACCEPT_H0), the falsified register (HYP-071 METRIC_ARTIFACT, MECH-001, MECH-004, spec 025 `NO_SUPERSEDE`), and I63–I70. Forge starts `sprt.py` from this spec in parallel (disjoint worktree).

**Step 5 — SPRT.** `sovereign/forex/sprt.py`: `sprt(deltas, *, delta, sigma, alpha, beta)` → `LLR_i = (δ/σ²)(d_i − δ/2)`, `A = ln((1−β)/α)`, `B = ln(β/(1−α))`, outcomes `ACCEPT_H1 | ACCEPT_H0 | INCONCLUSIVE`, raises on σ ≤ 0 / NaN. Fixtures: a hand-computed LLR trace; incumbent-vs-itself (ΔR ≡ 0 ⇒ every `LLR_i = −δ²/(2σ²)`) → `ACCEPT_H0` at exactly step `⌈|B|·2σ²/δ²⌉` (|B| = 1.558 at α .05, β .20), never H1 (I69).

**Step 6 — Walk-forward + tablebase.** `sovereign/forex/exit_tablebase.py`: anchored walk-forward over units; per test block: bin edges from train-row quantiles → `artifacts/tablebase_bins_block{k}.json` (hashed before use); backward induction over `(r_bin, t, weekend_next)` with absorbing terminals, cross-fit A/B, pure E[R], per-row incumbent fallback below `n_min`; emits per-block policy, deviation rate, coverage, `V(s)`; `artifacts/entry_value_function.json` (in-sample-optimistic, with OOF-realized beside it). Self-exclusion via `measure.assert_disjoint` on train/test path windows, by raise.

**Step 7 — Fill model.** `sovereign/forex/fill_model.py`: `BaseFill` reproduces `_apply_costs` on the 350 to the cent (I68); `PessimisticFill(spread_mult, slip_mult, delay_bars)` fills at `open[t+1]`, applied to the **candidate only**.

**Step 8 — Driver.** `scripts/carry_exit_sprt.py`: `require_hashes` → walk-forward → OOF per-unit ΔR (mean over trades in a unit; sum reported) → SPRT (base) → SPRT (candidate-pessimistic) → sign-flip permutation → `artifacts/sprt_result.json` (LLR trace, stopping decision, n_units, deviation rate, empirical σ_ΔR, MDE, the 411 as unweighted secondary). Freeze rule, pre-registered: the candidate replaces nothing unless **both** SPRTs `ACCEPT_H1` **and** the permutation p < α; otherwise the incumbent stays frozen and the result is logged as a null. An `ACCEPT_H0` driven by near-zero deviation is reported as "no deviation", not "deviation failed".

**Step 9 — Feature registry (minimal).** `sovereign/forex/feature_registry.py`: `{name: Feature(as_of_computable, source, modeled)}`; the path loader raises `ProvenanceError` on any `False` feature; LLM/news fields registered `False`. Mutation: flip one → raise.

**Step 10 — Red team.** Opus subagent, "break this result", write access to `artifacts/redteam.md` only (`git status` afterwards proves it). Verbatim into the report.

**Step 11 — `artifacts/session-report.md`**: the seven DoD items; this gap list annotated closed/not; null results; final commit.

## Declared parameters (spec 045; required args, no defaults)

`H_max` 10 bars · `t_bucket` {1, 2-3, 4-5, 6-8, 9-10} · `r_bin` 5 train-row quantiles · `weekend_next` {0,1} · `n_min` 20 visits · walk-forward block starts 2015-01, 2017-01, 2018-10, 2021-04, 2023-03; purge = train trades whose path window crosses the test start · α 0.05, β 0.20 · σ = SD of the incumbent's per-unit R on the 350 · **δ = `mechanisms.mde(σ, n_units)`** (≈0.25R at n≈97) · pessimistic `spread_mult` 2.0, `slip_mult` 2.0, `delay_bars` 1 · unit ΔR = mean over its trades · permutation draws 10,000, seed 20260829.

**Approving this plan ratifies the δ rule.** A smaller fixed δ (e.g. 0.10R — "worth acting on" but undetectable at this n → inconclusive by construction) can be substituted before spec 045 is written, never after the first run.

## Invariants added (each with a named test + a mutation that makes it fail)

I63 every new measurement script asserts self-exclusion by raise · I64 the driver halts on an unhashed dependency · I65 bin edges derive from train rows only (mutation perturbs a test *row*) · I66 no train/test path-window overlap · I67 an under-visited cell follows the incumbent per row, never 0 · I68 `BaseFill` ≡ `_apply_costs` on the 350 · I69 incumbent-vs-itself → `ACCEPT_H0` at the computed step, never H1 · I70 bench reproducible across two runs and changed by a one-parameter mutation · I71 terminal rows are absorbing (synthetic set stopping at t=3 → `V(s,1)` equals the stop R, not the pre-stop mean).

## Out of scope tonight (deliberate)

Entry gate / HYP-027 (CLAUDE.md #1 — `~/quant`; the `V(s_entry)` artifact is the handoff) · retrofitting self-exclusion into 17 legacy scripts (listed in the report) · rebuilding ALFRED vintage caches · opening spec 034's I58 (only on a pass, by ruling) · Phase B ATR layer · `MECHANISMS.json` edits · `swap_r` / `rate_diff` / `atr_ratio` as policy keys.

## Verification

```bash
python3 scripts/build_inventory.py
python3 -m sovereign.forex.carry_checkpoint verify              # exit 0
python3 scripts/carry_tablebase_paths.py                        # halts unless n=350, round(ΣR,2)=34.41
python3 scripts/carry_bench.py; python3 scripts/carry_bench.py  # identical single number
python3 scripts/carry_exit_sprt.py --fill base && python3 scripts/carry_exit_sprt.py --fill pessimistic
python3 -m pytest sovereign/forex/ scripts/ daytrade/ execution/ -q --deselect daytrade/test_alpha_operator.py::test_i14_packet_point_in_time
python3 scripts/wiring_audit.py                                 # 0 new/unexplained
python3 -m daytrade.frozen_policy verify                        # SF-FROZEN-002 untouched
```

Baseline to match or exceed: 1149 / 8 / 1. Every new invariant fault-injected
before it is called verified. `git status` after the red team shows only
`artifacts/redteam.md`.
