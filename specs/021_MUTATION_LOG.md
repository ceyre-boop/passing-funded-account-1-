# 021 mutation log — carry buy gate

Definition of done per repo doctrine: every stated invariant has a named automated
test, and deliberately violating the invariant makes the suite fail. Faults are
injected for real (file edited, suite run, file restored), not reasoned about.

## Phase 1 — firm contract layer (2026-08-10)

Suite: `sovereign/propfirm/test_firm_contracts.py` (9 tests). Baseline green before
and after every injection. Injection driver run inline; each row's fault was applied
to the named file, the suite run, and the file restored byte-identical.

| # | fault injected | file | caught by | result |
|---|---|---|---|---|
| M1 | CTI `max_dd.type` static→trailing | firm_contracts.yaml | test_contracts_load_and_validate + test_static_vs_trailing_floor_moves | CAUGHT |
| M2 | CTI gains a 5% daily limit | firm_contracts.yaml | test_no_daily_limit_means_daily_floor_never_binds | CAUGHT |
| M3 | Alpha Swing phase-2 target 5%→10% | firm_contracts.yaml | test_alpha_swing_two_phase_targets | CAUGHT |
| M4 | `NO_DAILY_LIMIT_PCT` 1.0→0.05 (no-limit silently becomes a real limit) | firm_contracts.py | test_no_daily_limit_means_daily_floor_never_binds | CAUGHT |
| M5 | drawdown-type enum validation deleted (fail-loud removed) | firm_contracts.py | test_malformed_contract_rejected | CAUGHT |
| M6 | Alpha Swing phase 1 gains a 90-day deadline | firm_contracts.yaml | test_contracts_load_and_validate (no-deadline invariant) | CAUGHT |
| M7 | adapter perturbs the emitted prop cfg (+0.1pt hidden buffer) | firm_contracts.py | test_static_vs_trailing_floor_moves (exact-ceiling assertions) | CAUGHT |

7/7 rows CAUGHT. Phase 1 status: IMPLEMENTED → UNIT VERIFIED. Certification by a
different seat still required per CLAUDE_LONG_TERM_HANDOFF roles.

## Phase 2 — campaign evaluator (2026-08-10)

Suite: `scripts/test_carry_buy_gate.py` (19 tests). Baseline green before and after
every injection; all faults applied to `scripts/carry_buy_gate.py` for real and
restored byte-identical.

| # | fault injected | caught by | result |
|---|---|---|---|
| E1 | golden avg_r tolerance 0.005→0.5 | test_golden_fails_on_constant_divisor | CAUGHT |
| E2 | sealed loader divides by constant 0.0075 (ignores the 0.007969 rows) | test_golden_green_on_real_data | CAUGHT |
| E3 | daily-DD bust check deleted | test_daily_floor_uses_day_start_not_open | CAUGHT |
| E4 | static max-DD silently evaluated as trailing | test_static/trailing pair | CAUGHT |
| E5 | min_trading_days enforcement dropped | test_min_trading_days_blocks_instant_pass | CAUGHT |
| E6 | TIMEOUT invented at observation horizon (UNRESOLVED→BUST) | test_no_deadline_never_times_out | CAUGHT |
| E7 | formatter accepts real without zero-edge control | test_formatter_refuses_missing_control | CAUGHT |
| E8 | funded declared after phase 1 of a two-phase contract | test_two_phase_campaign_requires_both | CAUGHT |
| E9 | G3 in-sample guard removed (sealed run could green G3) | test_g3_stays_red_on_sealed_run — SURVIVED on first pass with 50 bootstrap paths (CIs too wide to separate); test strengthened to 400 paths, then | CAUGHT |
| E10 | swap haircut dropped from the daily series | had NO test on first pass — SURVIVED; test_swap_haircut_applied_per_hold_day added, then | CAUGHT |

10/10 rows CAUGHT (two required test strengthening — recorded honestly above).
Phase 2 status: IMPLEMENTED → UNIT VERIFIED; certification by a different seat pending.

## Phase 3 — reproduction-gap diagnosis (2026-08-10)

`scripts/diagnose_repro_gap.py` carries its own runtime invariant: attribution
categories MUST sum to the total missing count (`assert` in the script — a run that
cannot account for every trade cannot print "diagnosed"). Result on this checkout:
sealed 411 / rig 296, matched 285 (±3d), missing 126 = cache-gap 0 +
macro-history-truncation 126 + residual 0 → status ATTRIBUTED. Evidence for the
attribution: per-pair/per-year table matches PERFECTLY 2021-2024 and the entire
deficit sits 2016-2020, exactly where data/cache/macro/{EU,UK,US,AU}_cpi (start
2020) and rates (start 2019) lack history; ^VIX3M/^TNX/COT were ruled out
statically (size-only multipliers, cannot remove entries). Confirmatory re-run
with restored macro history remains on the restore list.

## Phase 4 — verdict page repoint (2026-08-10)

Suite: `scripts/test_daily_verdict_page.py` (8 tests).

| # | fault injected | caught by | result |
|---|---|---|---|
| V1 | page reads legacy ICT prop_challenge_state.json again | test_no_state_file / test_stale_ict_state_is_ignored | CAUGHT |
| V2 | staleness check dropped | test_eight_day_old_green_is_not_ready | CAUGHT |
| V3 | gate-name/shape check dropped | SURVIVED on first pass (missing-key test passed for the wrong reason: 4 greens ≠ 5); test_wrong_gate_names_cannot_green_the_page added, then | CAUGHT |
| V4 | verdict word trusted without checking gates | test_verdict_word_alone_cannot_green_the_page | CAUGHT |
| V5 | stale window silently widened 7d→30d | test_eight_day_old_green_is_not_ready | CAUGHT |

5/5 rows CAUGHT (one required test strengthening — recorded honestly).

## Phase 5 — paper carry log (2026-08-10)

Suite: `scripts/test_paper_carry_log.py` (7 tests).

| # | fault injected | caught by | result |
|---|---|---|---|
| P1 | SHORT direction sign dropped from R | test_r_short_direction_sign | CAUGHT |
| P2 | swap haircut dropped from R | test_r_matches_hand_computation | CAUGHT |
| P3 | sprint requirement lowered 80→30 | test_min_n_matches_evaluator | CAUGHT |
| P4 | double-close allowed | test_double_close_rejected | CAUGHT |
| P5 | evaluator's G5 counts open trades | test_g5_counts_only_closed_trades | CAUGHT |

5/5 rows CAUGHT. Phases 4-5 status: IMPLEMENTED → UNIT VERIFIED; certification by
a different seat pending.
