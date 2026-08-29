# Session report — 2026-08-29 — carry exit tablebase vs CARRY-FROZEN-001

_Living document; each section is filled only when its verification ran. Unfilled = not done._

## 1. carry-exit-v1 — frozen or not

**Not decided yet.** The gate has not run. Decision rule (spec 045 §6): the candidate
replaces nothing unless SPRT(base) = ACCEPT_H1 AND SPRT(candidate-pessimistic) = ACCEPT_H1
AND sign-flip permutation p < 0.05. Pre-registered prediction: ACCEPT_H0.

Correction to the brief recorded in Phase 0: the thing called "carry-exit-v1" on disk
(spec 034, `daytrade/carry_exit.py`) reproduces 44.8% of sealed exits and is not the
candidate; the incumbent is `sovereign/forex/exit_machine.py`, pinned tonight for the
first time.

## 2. Content hashes

| artifact | sha256 | where |
|---|---|---|
| SF-FROZEN-002 (intraday engine, untouched) | engine `eb12b4a4f78acd3747e9ff64122ee2a7a4cef64a628947794960ef7e9b2d9991` | `data/daytrade/SF_FROZEN_002.json` |
| SF-FROZEN-001 (intraday, superseded, untouched) | engine `1c437fc4e49c9a60cf1c497feaa0269af61440423cf1b6c94c69e068588ff457` | `data/daytrade/SF_FROZEN_001.json` |
| CARRY-FROZEN-001 (carry incumbent) | record `4d8986b5e2c5b9965375e06adf2b8018df9c96c31cdc567e8773c90ffcd93d7e`; config `84a1d430c96b…` | `data/carry/CARRY_FROZEN_001.json` |
| winning candidate config (discretization included) | — no winner; bins per block recorded when the run completes | `artifacts/tablebase_bins_block*.json` |

Full map: `artifacts/inventory.json` (`hashes`).

## 3. Self-exclusion invariant — per measurement script

| script | asserts by raise | line |
|---|---|---|
| `scripts/carry_exit_sprt.py` | pending | — |
| `sovereign/forex/exit_tablebase.py` | pending | — |
| `scripts/carry_bench.py` | n/a — replay of a frozen artifact, no fitting | — |

Legacy scripts WITHOUT it (not retrofitted tonight; `build_inventory.py` `self_exclusion`):
10× `scripts/build_*_study.py`, `ruin_engine.py`, `carry_buy_gate.py`, `drawdown_margin.py`,
`run_cb_ab.py`, `run_vintage_ab.py`, `eval_lab.py`, `tsmom_backtest.py`, `forex_backtester.py`.

## 4. Bench

pending

## 5. Red-team findings (verbatim)

pending

## 6. Null results logged

- (pending the run)

## 7. Gap list — annotated

| # | gap | status |
|---|---|---|
| G1 | carry incumbent unhashed | **CLOSED** — CARRY-FROZEN-001 (`a74f12f`) |
| G2 | no frozen path dataset | in progress — extractor |
| G3 | no SPRT | **CLOSED** — `sovereign/forex/sprt.py` (`c1f7683`) |
| G4 | no overlap-respecting OOS scheme | pending — Step 6 |
| G5 | no bench / pre-commit | pending — Step 3 |
| G6 | nothing halts on an unhashed dependency | **CLOSED** — `inventory.require_hashes` (`6c9c77e`); driver wiring pending |
| G7 | extraction parity unproven | pending — falls out of G2 |
| G8 | spec 034 carry_exit is not a candidate | recorded |
| G9 | no pessimistic fill | **CLOSED** — `fill_model.py` (`82cdf38`); 350-parity pending G2 |
| G10 | no as_of_computable registry | **CLOSED** — `feature_registry.py` (`b2377e2`) |
| G11 | 17 legacy scripts lack self-exclusion | listed above; not retrofitted |
| G12 | entry gate / HYP-027 | out of scope (CLAUDE.md #1); V(s_entry) artifact pending |
| G13 | vintage caches absent | recorded; red-team item |
| G14 | swap modeled | recorded; registry flags `modeled` |
| G15 | spec 034 I58 | deferred until a pass exists |

## Seats and models

Architect/verifier: Claude (this session). Builders: Sonnet. SPRT: Forge via codex —
`gpt-5.6-terra` (the account refuses `gpt-5.4`). Every self-report was re-run, not read.
