# Session report — 2026-08-29 — carry exit tablebase vs CARRY-FROZEN-001

Brief: "finish the engine tonight" — exits as a solved endgame, gated by SPRT against a frozen
incumbent, with a bench, a red team, and this report. Plan: `Plans/cheeky-mixing-locket.md`.
Pre-registration: `specs/045_CARRY_EXIT_TABLEBASE.md` (+ Amendment 1). HEAD at report time: see git.

## 1. carry-exit-v1 — NOT frozen. The incumbent stays.

**Stopping decisions (spec 045 §6, run once, `artifacts/sprt_result.json`):**

| arm | SPRT | stop | LLR at stop | bounds A / B | mean unit ΔR | Σ trade ΔR | deviation |
|---|---|---|---|---|---|---|---|
| base fill | **ACCEPT_H0** | 68 of 97 units | −1.579 | +2.773 / −1.558 | −0.0274 | −16.43 R (301 trades) | 52% of units |
| candidate-pessimistic | **ACCEPT_H0** | 46 of 97 | −2.250 | same | −0.0575 | −23.91 R | 81% |
| sign-flip permutation (base) | p = **0.726** | — | — | null 5–95% [−0.070, +0.071] | −0.0274 | — | 50/97 nonzero |

Decision rule (pre-registered): replace only if both arms ACCEPT_H1 and p < 0.05. **None held.**
Per block (base, **descriptive — the sequential test stopped at unit 68, inside block 4, and never
consumed block 5**; red-team finding 3): B2 ΔR ≈ 0 (coverage 8% — the table barely deviates with 24
training units); B3 +0.040 mean; B4 +0.028 mean but −6.9 R summed; B5 −0.200 mean, −14.2 R summed,
16 of 22 units negative (worst unit −4.46 R over 5 trades — not one trade). The table learned "hold
through the trail" in-sample and it lost out of sample. The pre-registered prediction
(ACCEPT_H0) held. Empirical σ_ΔR 0.42 (base) against the declared σ 0.758 — the declared σ was
conservative, as intended.

**What "carry-exit-v1" means on disk was corrected in Phase 0:** spec 034's `daytrade/carry_exit.py`
reproduces 44.8% of sealed exits and was never a candidate. The incumbent is
`sovereign/forex/exit_machine.py`, content-hashed tonight for the first time.

**This was HYP-071's reopen condition** (`~/quant`, killed 2026-06-30 as METRIC_ARTIFACT): pure
E[R], no λ penalty, realized OOF R against a frozen opponent under SPRT. The result goes back to
`~/quant`'s ledger as a fresh adjudication: not a side door, and not a pass.

## 2. Content hashes

| artifact | sha256 |
|---|---|
| SF-FROZEN-002 (intraday engine — untouched, verify rc 0) | engine `eb12b4a4f78acd3747e9ff64122ee2a7a4cef64a628947794960ef7e9b2d9991` |
| SF-FROZEN-001 (intraday, superseded — untouched) | engine `1c437fc4e49c9a60cf1c497feaa0269af61440423cf1b6c94c69e068588ff457` |
| **CARRY-FROZEN-001** — the carry incumbent (11 files + declared config + 3 datasets) | record `6fb1c5ee8b2c975c13d6d6ea78105ddecf9ad3eecc288356b8c58e7a4a7b446f`; config `84a1d430c96b…` |
| frozen paths `artifacts/carry_paths.parquet` | `f1708fecddbb5f23bd0a6fd9708eee97753aae57d183a722e378b8fe289a7a12` |
| frozen trades `artifacts/carry_trades.parquet` | `492a7c8d7130ca4bab342c3cce6b12f04e648061f4b08e1467d8b9af2f203988` |
| units `artifacts/carry_units.json` | `ea3447e4ae9a4250faf9dbd1609c1aa49ed8b69511e73015b967c70ead080456` |
| candidate discretization, per block (`tablebase_bins_block{2,3,4,5}.json`) | `615f06d6095706de…`, `0296d6ef35c33606…`, `f381ee08630ae1bc…`, `e601649fb09169e1…` |
| gate output `artifacts/sprt_result.json` | `2ed96e5b6c29e169…` |

There is no "winning candidate": the full candidate config is the spec's §7 table plus the four
bins files above. Every hash is in `artifacts/inventory.json`; the driver refuses to run on any
other bytes (it halted once tonight on a stale checkpoint hash — correctly — after the datasets
were attached; re-recorded in `1dfcc0d`).

## 3. Self-exclusion — asserted by raise, per measurement script

| script | assertion | line |
|---|---|---|
| `scripts/carry_exit_sprt.py` | `_assert_self_excluded` → `MeasurementError` on train∩test, double evaluation, or empty OOF | 85, 92, 95, 98 |
| `sovereign/forex/exit_tablebase.py` | `_assert_no_train_test_overlap` → `TablebaseError` on any train path window intersecting the test block | 491 |
| `scripts/carry_bench.py` | n/a — replay of a frozen artifact, no fitting; refuses unhashed inputs | 60 |
| `scripts/carry_tablebase_paths.py` | n/a — extraction, no fitting; halts unless the population reproduces (n, ΣR, parity) | — |

Tests that make them bite: `test_i63_train_test_overlap_raises`, `test_i63_trade_evaluated_twice_raises`,
`test_no_train_test_overlap` (deliberately un-purged unit).

**Legacy scripts still without it** (18, not retrofitted tonight — `build_inventory.py` `self_exclusion`):
10× `scripts/build_*_study.py`, `ruin_engine.py`, `carry_buy_gate.py`, `drawdown_margin.py`,
`run_cb_ab.py`, `run_vintage_ab.py`, `eval_lab.py`, `tsmom_backtest.py`, `forex_backtester.py`.

## 4. Bench

`python3 scripts/carry_bench.py` → **`34.4061881520`** — the incumbent's total net R over the 350
frozen paths replayed bar-by-bar through the pinned `decide_exit`; 1,727 decision rows parity-checked
on every run. Two runs identical. Halving every trail multiple changes it; a 0.01% change flips no
exit (recorded, not hidden). `.githooks/pre-commit` runs `--check` against `artifacts/bench.txt` and
blocked nothing tonight because nothing moved the engine — every commit after `4b3c0c4` passed it.

## 5. Red-team findings (verbatim)

_See `artifacts/redteam.md` — appended below unedited when the red team returns._

## 5b. Architect's response to the red team (their text above is unedited; this is mine)

The decision survives; the test's claimed strength does not. Taking the open findings in order:

1. **Free ACCEPT_H0 from non-deviating units — agreed, MAJOR.** 47 of 97 units contributed
   −1.498 of the −1.558 bound without the table making a decision. Recomputed independently: on the
   50 deviating units alone, mean ΔR −0.053 (sd 0.588), SPRT with the declared parameters →
   ACCEPT_H0 at 38; with a post-hoc σ_ΔR/δ=MDE pair (not pre-registered, shown only to answer the
   finding) → ACCEPT_H0 at 28. The candidate is negative on every reading; what the spec measured on
   the first 23 units was coverage, not skill. A block-level deviation floor belongs in spec 046.
2. **δ from the level variance — agreed.** σ = SD of unit-level incumbent R (0.758) was declared
   "conservative" on the argument that overestimating σ only lengthens the test; that is false when
   δ = mde(σ, n) — it raises the bar to 0.19 R against a +0.098 R/trade edge. Empirical σ_ΔR was 0.42
   (MDE 0.106). Re-running with those (post-hoc, reported by the red team) → ACCEPT_H0 at 72.
   Direction unchanged; the rule was wrong and is recorded as such.
3. **The SPRT never saw block 5 — agreed;** §1 now labels the per-block table descriptive.
4. **The pessimistic arm is not a handicap on decisions — agreed, MAJOR, and it is my design.**
   Independently computed: an all-FALLBACK (incumbent-identical) candidate scores **−0.049 R/unit,
   −8.6 R, 39 units up / 40 down** under the pessimistic fill — the arm re-prices the incumbent's own
   exits at next open with doubled costs. It cannot separate "worse decisions" from "next-open noise".
   The base arm is clean (identity → 0.0000 exactly). The pessimistic ACCEPT_H0 is therefore not
   independent evidence; the decision rests on the base arm and the permutation.
5. **CARRY-FROZEN-001 pins the exit side, not the entry inputs — agreed, MAJOR.** The 350 reproduce
   only through a scratchpad `macro_nominal` directory that a reboot deletes; it is hashed nowhere.
   What is sealed and re-runnable by anyone is the path parquet (hashed, attached) — the tablebase
   and the gate depend only on it. The checkpoint's `why` overstates what it pins; the fix is to move
   those parquets under `data/` and attach them, or rebuild them via `build_rate_vintages.py`.
6. **Per-trade `cost_frac` carries the incumbent's final hold — agreed, MINOR** (91 of 2,667 rows off
   by >0.01 R, bins ~0.3 R wide). Fix in the extractor: per-bar cost via `BaseFill.cost_fracs(hold_bars=t)`.
7. **One commit for tablebase + driver + results — agreed as process.** The builder reported the
   walk-forward numbers to me before the driver ran, and Amendment 1 was written before that report,
   but git cannot show it. Next time: commit the module, then the driver, then run.
8. **Per-trade −0.055 R vs unit-mean −0.027 — agreed;** the losing units are the multi-trade clusters.

**Net:** INCUMBENT STAYS stands, on the base-arm SPRT, the permutation (p = 0.726), and the
per-trade sum (−16.4 R). What does not stand is the claim that the gate as designed could have
detected a real 0.10 R improvement: with 47 free H0 votes and a 0.19 R bar it could not. That is
the finding to carry into spec 046, and it is why a passing result tonight would not have been
trustworthy either.

## 6. Null results logged (none deleted)

- **The gate itself**: base ACCEPT_H0, pessimistic ACCEPT_H0, permutation p = 0.726. Logged in
  `artifacts/sprt_result.json` / `freeze_decision.json`; nothing re-run with a changed parameter.
- **Spec 045 §4 as written does not terminate.** Cells keyed by `t_bucket` pool bars 6–8; 16 of 28
  rows in one block-2 cell transition into their own cell and the hard argmax oscillates in an exact
  period-2 cycle (2.445 ↔ 1.778, traced to 40 sweeps), in all four blocks. Found by the builder, who
  refused to touch a parameter; Amendment 1 (`e940bc7`) moved to exact-level states before any result
  existed. Coverage fell as the amendment said it would (B2 8%, B3 41%, B4 72%, B5 79%).
- **The 411 secondary was not run** — no frozen paths exist for the contaminated population; it would
  need its own extraction. Recorded rather than fudged. NO_INFERENTIAL_WEIGHT either way.
- **A 0.01% trail change flips no exit** on the frozen workload (bench mutation test).
- **Four order-dependent test failures** came from running the rig capture in-process
  (`yfinance.download` patched globally, `sovereign.forex.*` reloaded); fixed structurally (`42bec94`).
- **The spec-fill edit failed silently once** (an assertion inside a shell heredoc did not abort the
  commit); the fill was redone as its own commit (`76461f2`) as the spec requires.

## 7. Gap list — annotated

| # | gap | outcome |
|---|---|---|
| G1 | carry incumbent unhashed | **CLOSED** — CARRY-FROZEN-001 (`a74f12f`, datasets `04f4fed`) |
| G2 | no frozen path dataset | **CLOSED** — `carry_paths.parquet`, parity 350/350 |
| G3 | no SPRT | **CLOSED** — `sovereign/forex/sprt.py` (`c1f7683`), cross-vendor seat |
| G4 | no overlap-respecting OOS scheme | **CLOSED** — anchored walk-forward over 121 interval-component units, purge, self-exclusion by raise |
| G5 | no bench / pre-commit | **CLOSED** — `carry_bench.py` + `.githooks/pre-commit` (`4b3c0c4`) |
| G6 | nothing halts on an unhashed dependency | **CLOSED** — `inventory.require_hashes`; exercised for real once |
| G7 | extraction parity unproven | **CLOSED** — 350/350; the builder's own hold_limit bug took it 125→350 |
| G8 | spec 034 `carry_exit.py` is not a candidate | recorded; unchanged |
| G9 | no pessimistic fill | **CLOSED** — `fill_model.py`; applied candidate-only |
| G10 | no `as_of_computable` registry | **CLOSED** — `feature_registry.py`; the driver's state keys pass through it |
| G11 | 18 legacy scripts lack self-exclusion | **NOT CLOSED** — listed in §3; every new script has it |
| G12 | entry gate / HYP-027 | **OUT OF SCOPE** by CLAUDE.md #1; `artifacts/entry_value_function.json` is the handoff (labelled in-sample-optimistic, OOF-realized means beside it) |
| G13 | vintage caches absent; the 350 produced under nominal rates via a scratchpad dir | **NOT CLOSED** — paired design cancels entry effects in ΔR; red-team item |
| G14 | swap modeled | recorded; registry flags `modeled` |
| G15 | spec 034 I58 fifth exit reason | moot — nothing passed |

## Seats, models, and what was re-verified

Architect/verifier: Claude Fable 5 (this session). Builders: Sonnet ×2 (extractor, tablebase).
SPRT: Forge via codex — `gpt-5.6-terra` (the account refuses `gpt-5.4`). Design review: Plan agent.
Red team: general-purpose agent, write access to `artifacts/redteam.md` only.

Every subagent self-report was re-run, not read: the extractor's hashes reproduced under an independent
rerun; Forge's hand-computed LLR trace was recomputed; the tablebase and driver tests were rerun; two
independent fault injections were made on the SPRT and one on BaseFill and the bench. Two subagent
stops were the right call and are recorded: the extractor refused a stale worktree base; the tablebase
builder refused to route around the non-terminating table.

Suite at `1dfcc0d`: **1249 passed / 8 skipped / 1 deselected** (baseline 1149). Wiring audit: 0 new /
174 allowlisted. `frozen_policy verify`: SF-FROZEN-002 intact. Trade-evidence freeze: one
`FREEZE OVERRIDE` (spec 045), logged in history.

## Not done tonight (deliberate)

The 411 secondary; retrofitting self-exclusion into 18 legacy scripts; rebuilding the ALFRED vintage
caches; the daytrade Phase B ATR layer; any `MECHANISMS.json` reconciliation (MECH-001 ruling still
owed); 12 stale `.claude/worktrees/agent-*` from earlier sessions (deletion blocked by the permission
classifier earlier; `git worktree remove --force` ×12 + `git worktree prune`).
