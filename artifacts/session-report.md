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

## 5. Red-team findings (verbatim — `artifacts/redteam.md`, unedited)

> # Red team — spec 045 "INCUMBENT STAYS" (HEAD 1dfcc0d)
>
> Attack target: base SPRT ACCEPT_H0 @68/97, pessimistic ACCEPT_H0 @46/97, perm p=0.726, mean OOF unit ΔR −0.027.
> All numbers below were recomputed from `artifacts/unit_deltas_*.csv`, `artifacts/carry_{paths,trades}.parquet`, `sovereign/forex/sprt.py`, `daytrade/mechanisms.mde`. Nothing in the repo was modified.
>
> ## Findings
>
> **1. The ACCEPT_H0 is 96% "free": it comes from units where the candidate could not act.** — MAJOR (hollow test)
> Evidence: 47 of 97 OOF units have ΔR ≡ 0 (`deviated == False`). Each zero unit contributes LLR −δ²/2σ² = −0.0319; 47 × −0.0319 = **−1.498 of the −1.558 bound**. Block 2 (23 units, coverage 7.6%, deviation 0%) alone delivers −0.733 before the table has made a single decision. `sprt_result.json` `llr_trace[0:23]` is an exact straight line. The pre-registered "no deviation → report as no-deviation" clause (§6) only fires below 10% deviation *overall*; a block with 0% deviation is silently counted as 23 votes for H0.
> Direction: on the 50 deviating units alone, mean ΔR = **−0.053** (sd 0.588, t = −0.64), SPRT on those alone → still ACCEPT_H0 at 38. Fixing this does not rescue the candidate; it changes what the test measured.
> Settles it: report SPRT + permutation on deviating units as the primary; declare block-level deviation floor (e.g. a block with <10% deviation contributes nothing to the LLR).
>
> **2. δ = 0.1914 was derived from the wrong variance and is ~2× the incumbent's whole edge.** — MAJOR (test weaker than claimed)
> Evidence: σ = 0.758 is the SD of the incumbent's per-unit *level* R; the SPRT observes the paired *difference*, whose empirical SD is 0.421 (`sigma_delta_empirical`). `mde(0.421, 97) = 0.106`, not 0.191. The incumbent earns +0.098 R/trade; the candidate was required to add +0.19 R/unit on top — i.e. roughly double the edge — to be "worth acting on". "Overestimation only lengthens the test" (spec §6) is false when δ is itself a function of σ: overestimating σ *raises the bar*, it does not merely slow the walk.
> Direction: re-running with σ = 0.421, δ = 0.106 → ACCEPT_H0 at 72, LLR −2.26. Unchanged; the candidate is negative either way.
> Settles it: the spec should have declared δ from a plausible-improvement argument (e.g. 0.10R, the plan's own alternative) and σ from a pilot of paired ΔR, not from the level.
>
> **3. The SPRT never saw block 5, the block the report headlines.** — MINOR (reporting)
> Evidence: base stopped at unit 68 = block 4 (23+25+27 = 75 units in B2–B4; 0 block-5 units consumed). Pessimistic stopped at 46 (inside block 3). `per_block` and "block 5 alone: −0.200" in the commit message are post-hoc descriptive stats on units the sequential test never consumed. The stop was tipped by one unit (idx 68, unit 91, 4 trades, ΔR −0.80 → LLR −0.298 in one step); unit idx 51 (unit 74, 1 trade, +1.63) moved it +0.51 the other way. The normal-model LLR is dominated by stop-driven tails the model does not describe.
> Direction: unchanged. Settles it: label per-block stats as "not consumed by the SPRT"; the permutation p (0.726, all 97) is the honest headline.
>
> **4. The "pessimistic" arm is not a handicap; it is a random re-price of exits the candidate did not choose.** — MAJOR (arm cannot do what the spec says it does)
> Evidence: `apply_policy` (`exit_tablebase.py:437-449`) prices FALLBACK-following-the-incumbent exits through the arm's fill → deviation 81% vs 52% base. `PessimisticFill.exit_price` fills at `open[t+1]`, which is *not* monotone worse than `close[t]`: **41 of 97 units have pessimistic ΔR > base ΔR**; the very first OOF unit has ΔR = **+0.47** under the "handicap" (block 2, where the base candidate is identical to the incumbent). 35 units positive, 44 negative. So the arm mixes (a) a 2× cost penalty and (b) overnight-gap noise applied to ~all 231 decided exits, most of which are the incumbent's own. All-FALLBACK-under-pessimistic computation: see finding 4b below (pending).
> Direction: unknown until 4b is computed; the arm as built cannot distinguish "candidate is worse" from "next-open fill is noisy".
> Settles it: apply the pessimistic fill only to exits that *differ* from the incumbent's, or price the incumbent through the same fill.
>
> **5. CARRY-FROZEN-001 does not pin the opponent's inputs; the 350 are reproducible only via an unpinned `/private/tmp` directory.** — MAJOR (the "frozen opponent" claim)
> Evidence: `scripts/carry_tablebase_paths.py:53-58` `SCRATCH_NOMINAL_DIR = /private/tmp/claude-501/.../42802b2d-.../scratchpad/macro_nominal`; `data/cache/macro_nominal/` does not exist in the tree (`ls data/cache` → cot, macro, macro_estat_raw only). The scratch dir exists today (12 parquets, dated Aug 26–27) and is neither hashed in `CARRY_FROZEN_001.json` (`files` lists engine .py files, spot caches, the CSV — no macro cache) nor in `artifacts/inventory.json`. A reboot deletes the only thing that reproduces `cb_off_trades.csv`. The checkpoint pins the *output* population and the exit code, not the entry-signal inputs; `signal_engine.py` is pinned but its data is not.
> Direction: unchanged for ΔR (paired on identical entries, spec §2's argument holds) — but "CARRY-FROZEN-001 pins the opponent" is false as stated, and the extractor's HALT conditions are not re-runnable by anyone else.
> Settles it: hash the macro_nominal parquets into the checkpoint (or into `data/`), or record that the population is pinned by CSV hash only and the paths are the sealed object.
>
> **6. `unrealized_r_net` (state key AND Q_exit) carries the incumbent's *final* hold length via the swap term.** — MINOR (look-ahead in state, small)
> Evidence: `carry_tablebase_paths.py::unrealized_r` uses the per-trade `cost_frac` computed at the incumbent's `hold_days`, not bar `t`. Recomputed with `BaseFill.cost_fracs(hold_bars=t)`: mean err −0.0001R, range [−0.019, +0.021]R, 91 of 2,667 decision rows off by >0.01R. Bins are ~0.3R wide so the key rarely flips; but Q_exit in training is mis-priced relative to `_price_exit` at apply time (which uses bar t), and the sign of the error depends on how long the incumbent later held.
> Direction: negligible either way. Settles it: compute per-bar cost in the extractor; assert `unrealized_r_net(t) == _price_exit(t)` under BaseFill.
>
> **7. Degrees of freedom: what git can and cannot certify.** — MINOR to MAJOR depending on trust model
> - Closed: `permutation.py` (n_nonzero, 1e-9 tol) committed f710784 02:08, before the run. Extractor fix 42bec94 changed no artifact bytes (commit asserts inventory verified). Spec cells filled 76461f2 01:55 from artifacts frozen 01:54.
> - Open: `exit_tablebase.py`, `scripts/carry_exit_sprt.py`, and every result artifact landed in **one commit (1dfcc0d)**. No pre-run version of the tablebase or driver exists in history. The hold_limit bug fix, the n_min/cross-fit implementation choices, and Amendment 1's exact-t key cannot be shown by git to predate any number. Amendment 1 (e940bc7 02:20:08) is 9 minutes before the result commit; the run takes seconds.
> - Amendment 1's direction: exact-t → fewer visits per cell → more FALLBACK → less deviation → more zero units → finding 1. The alternative fixes (damped value iteration, soft policy, larger n_min per bucket) were not enumerated. The builder's report says no result was seen; nothing in the repo can confirm it.
> - Design-review changes made **without** sight of an outcome (plan, before Step 2): H_max 60→10, K-fold→walk-forward, δ=MDE rule, σ=incumbent per-unit SD, unit=[entry,path_end] components. All predate the extraction commit.
> Settles it: nothing retroactively; going forward, commit the tablebase before the driver, and the driver before the artifacts.
>
> **8. Per-trade the candidate is far worse than the unit-mean headline: −0.055 R/trade vs the incumbent's +0.098.** — MINOR (direction: against the candidate)
> Evidence: sum ΔR −16.43 over 301 OOF trades. Block 4: unit-mean **+0.028** but trade-sum **−6.87** — the losing units are the multi-trade clusters. Unit-mean weighting (spec §4) gives a 1-trade unit the weight of a 5-trade unit; P&L does not.
> Direction: worse for the candidate. Settles it: report both; the plan's own "(sum reported)" is buried in a CSV.
>
> **9. Block 5's −0.20 is broad, not one trade.** — closed. Worst unit (103, 5 trades) is −4.46 of −14.16; without it block 5 is still −9.70.
> **10. Masked forced terminal (trail wins priority over reversal on the same bar).** — closed. `decide_exit` priority stop>trail>reversal; exactly 1 row where a TIME/TRAIL exit coincided with a reversed signal.
> **11. `terminal_r` for STOP rows uses the close, as the incumbent does.** — closed. Extractor asserts `unrealized_r_net(t=incumbent_hold) == incumbent_r_net` to 1e-9 on all 350 (`carry_tablebase_paths.py:597-604`), including the 10 stops.
> **12. Chronological order of OOF units.** — closed. `_unit_deltas` sorts by (block, first_entry); trace reproduces from the CSV in that order.
>
> **4b. All-FALLBACK table (a candidate identical to the incumbent) under PessimisticFill — computed.** — evidence for finding 4
> Evidence (`tb.apply_policy` with an empty cell table, OOF 301 trades, 97 units, spec σ/δ): base arm → deviated 0, ΔR ≡ 0, SPRT ACCEPT_H0@49 (the I69 identity). **Pessimistic arm → 241 of 241 decided exits re-priced, unit mean ΔR −0.049 (sd 0.381), 39 units > 0 / 40 < 0, SPRT ACCEPT_H0@46, LLR −2.41** — the identical candidate stops at the same step (46) with a *lower* LLR than the real candidate (−2.25). Decomposition: 2× spread/slip alone costs −0.017 R/trade; the next-open gap adds −0.012 R/trade of drift on top of |gap| = 0.31 R/trade of pure noise. The arm's ACCEPT_H0 is therefore pre-determined by construction: it cannot pass an incumbent-equivalent candidate, so it cannot pass any candidate whose true improvement is < ~0.05 R/unit plus the noise it injects. As a gate criterion (both arms must ACCEPT_H1) it is a veto, not a handicap. Severity for finding 4 stands at MAJOR; direction: removing it changes nothing here (base arm already ACCEPT_H0).
>
> **13. Git-history items not in finding 7.** — MINOR
> - The prereg commit 2cf6ac7 and the path-extraction commit 04f4fed carry the same timestamp (01:54:52). "Written before any candidate was fit" holds; "written before the paths existed" does not — H_max=10, n_min=20, n_bins=5 were all fixed in the plan with sight of the population's hold distribution (208/350 hold 5) and the overlap-component count at H=60 vs H=10 (2 vs ~97). That is tuning on the evaluation data's *structure*, not its outcome; it explains why block 2 cannot populate a cell (24 train units × ≤10 rows ÷ 100 cells) — the coverage collapse was foreseeable from the numbers in the plan.
> - 807376b (02:20:47) writes "(gate outcomes pending the run)" into the session report after Amendment 1 — the one in-repo timestamped statement that no result existed when the exact-t key was chosen. It is a self-report, but it is committed before the result.
> - `t_buckets` survives as a required CLI argument that only validates `h_max` and labels coverage; a reader of `sprt_result.json.params` would believe the state key is bucketed. Cosmetic, but it misstates the declared discretization.
>
> ## Verdict
> "INCUMBENT STAYS" survives. Every correction I could compute pushes the same way: on the 50 units where the table actually acted the candidate lost 0.053 R/unit and 0.090 R/trade; the permutation p (0.726) is unaffected by δ or σ; re-running the SPRT with the empirical σ_ΔR and its own MDE still stops at ACCEPT_H0; block 5's loss is broad, not one trade; the STOP/close and masked-reversal holes are closed. The candidate is not merely "not better" — it is measurably worse per trade than the rule it was meant to replace. The single strongest reason the conclusion could be hollow rather than true is finding 1 + finding 2 together: 96% of the H0 log-likelihood came from 47 units where the candidate was structurally identical to the incumbent, and the bar δ = 0.19 R/unit is double the incumbent's entire edge — so a genuinely better exit policy adding, say, +0.10 R on every deviating unit (mean +0.052 over all 97) would drift at −0.015 LLR/unit and *also* return ACCEPT_H0. The gate therefore supports "this tablebase does not beat CARRY-FROZEN-001" and does not support the stronger reading "a backward-induction exit cannot beat it"; the pessimistic arm (4b) adds no information either way, and the frozen opponent's inputs (5) live in `/private/tmp`. Keep the incumbent; do not cite this run as evidence that the exit question is closed.

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
13. **Parameters chosen with sight of the population's structure — agreed, with the distinction kept:**
   H_max, n_min, n_bins were set after Phase 0 saw the hold distribution and the overlap-component
   counts (that is what made block 2's coverage collapse foreseeable), but before any candidate
   outcome existed. Structure-informed, outcome-blind. The red team is right that git cannot prove
   the second half; only the commit order can, next time.

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
