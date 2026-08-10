# specs/ — the written build, before the code
Everything here is a SPEC. Nothing in this directory is running code. Read
`000_RULINGS_AND_ORDER.md` first — it settles two open design questions and fixes
the build sequence.

| # | file | component | status | build order |
|---|---|---|---|---|
| 000 | RULINGS_AND_ORDER | rulings, sequence, status legend | — | read first |
| 008 | CEILING | `daytrade/ceiling.py`, `splits.py` | `[SPEC]` | **1st (with 005)** |
| 005 | BACKTEST | `daytrade/backtest.py` | `[SPEC]` | 1st |
| 001 | REGIME | `daytrade/regime.py` | `[SPEC]` | 2nd |
| 004 | SCORECARD | `daytrade/scorecard.py`, `streak.py` | `[SPEC]` | 3rd |
| 002 | SURVIVAL | `daytrade/survival.py` | `[SPEC]` | 4th |
| 003 | STOCKFISH_V2 | extend `stockfish_exit.py` | `[SPEC]` | 5th |
| 006 | ALPHAZERO_V2 | extend `alphazero_bias.py` | `[SPEC]` | 6th |
| 009 | CLAUDE_AS_NEWS | `daytrade/news_claude.py` + launchd | `[SPEC]` | with 006 |
| 007 | BRIEF (in 006 file) | `daytrade/brief.py` | `[SKETCH]` | last |
| 010 | LONG_TERM_VISION | backlog and architecture map | `[SKETCH]` | planning only |
| 011 | STOCKFISH_CONSTITUTION | `daytrade/stockfish_constitution.py` | `[BUILT]` — IMPLEMENTED, UNIT VERIFIED (self-test + `test_constitution_wiring.py`), WIRED (all 9 rules live-reachable since the 2026-08-09 wiring fix), not EXERCISED | done 2026-08-06 |
| 018 | CONTEXT_DIRECTIVE_CONTRACT | `daytrade/context_directive.py` | `[BUILT]` — IMPLEMENTED, self-test only, not WIRED (016 wires production; 020 specifies the test spine) | done 2026-08-06, with 011 |
| 019 | AZ_INVARIANT_TESTS | `daytrade/test_scenarios.py`, `test_thesis.py`, `test_regime_vector.py` | `[BUILT]` — scenarios/thesis/regime_vector IMPLEMENTED → UNIT VERIFIED, 33/33 mutation rows (`019_MUTATION_LOG.md`), certified 2026-08-09 | done 2026-08-09 |
| 020 | AZ_WIRING (gate-1 spine + gate map) | `daytrade/test_az_spine.py`, `test_regime_compute.py`, hardened `regime_vector.compute()`, `stockfish_exit.resolve_channels` | `[SPEC]` — IMPLEMENTED 2026-08-09: 20/20 mutation rows (`020_MUTATION_LOG.md`); audited 2026-08-10 (architect: implemented and mutation-verified at unit level; suite 154, 156/156 rows repo-wide). Note for the certifier: `resolve_channels` (channel arbitration, conservative-wins) was added to `stockfish_exit` on adversarial-review instruction (ruling 1 — arbitration is mechanics) and needs explicit ratification; `trend_strength` hardened beyond the ruled five (same species). | promoted 2026-08-09 |
| 012 | STOCKFISH_EVENT_MEMORY | `daytrade/trade_events.py` | `[SPEC]`+built 2026-08-09 on Colin's instruction — IMPLEMENTED → UNIT VERIFIED (21/21, `012_MUTATION_LOG.md`); C005 keys now persist WITH state; not WIRED (runner does not emit events yet); pending certification | Gate 2 |
| 013 | STOCKFISH_EXECUTION_AND_FILLS | `daytrade/execution_policy.py` | `[SPEC]`+built 2026-08-09 — IMPLEMENTED → UNIT VERIFIED (17/17, `013_MUTATION_LOG.md`); broker.py untouched; not WIRED; pending certification | Gate 3 |
| 014 | SHADOW_REGRET_PORTFOLIO | `daytrade/shadow.py`, `regret.py`, `portfolio_guard.py` | `[SPEC]`+built 2026-08-09 — IMPLEMENTED → UNIT VERIFIED (14/14 + 11/11, `014_MUTATION_LOG.md`, `014_GUARDS_MUTATION_LOG.md`); type-level shadow containment; guards not WIRED to any pre-trade path; pending certification | Gates 4+7 |
| 015 | AZ_EVIDENCE_LIFECYCLE | `daytrade/evidence.py` | `[SPEC]`+built 2026-08-09 — IMPLEMENTED → UNIT VERIFIED (with 016: 18/18, `015_016_MUTATION_LOG.md`); not WIRED (news_claude.py still emits headline-shaped output); pending certification | Gate 5 |
| 016 | AZ_DIRECTIVES_AUTHORITY | `context_directive.py` additions + runner wiring | `[SPEC]`+built 2026-08-09 — IMPLEMENTED → UNIT VERIFIED; **018 is now WIRED** (runner directive channel, tighten-ceiling authority, tested byte-parity without the file); not EXERCISED (no live session has carried a directive); pending certification | Gate 5 |
| 017 | AZ_FORECAST_PROMOTION | `daytrade/forecast.py` | `[SPEC]`+built 2026-08-09 — IMPLEMENTED → UNIT VERIFIED (17/17, `017_MUTATION_LOG.md`); promotion_ref interlocks with 016's registry; no producer wired; pending certification | Gate 6 |
| 021 | CARRY_BUY_GATE | `sovereign/propfirm/firm_contracts.py`, `scripts/carry_buy_gate.py`, `diagnose_repro_gap.py`, `paper_carry_log.py`, repointed verdict page | `[SPEC]` — pre-registered 2026-08-10 (plan: `Plans/soft-churning-pelican.md`); carry lane, not daytrade; supersedes eval_lab*/instant_pro_sim/colin_v2_campaign_sim/oos_campaign_test campaigns | independent of daytrade gates |

## Status legend
- `[BUILT]` exists, tested, in the repo
- `[SPEC]` fully specified, not written. **Safe to build from.**
- `[SKETCH]` direction right, details deliberately unfinished. **Do not build
  from a SKETCH without a planning pass** — building it now bakes in a guess
  we'd have to tear out.

`[BUILT]` only means the module exists. For 011 and 018, and every card after
them, also track where the module sits on `CLAUDE_LONG_TERM_HANDOFF.md`'s
maturity sequence — `IMPLEMENTED → UNIT VERIFIED → INTEGRATION VERIFIED →
WIRED → EXERCISED` — since `[BUILT]` alone doesn't say whether it's tested,
wired to a live caller, or actually run.

## Operational completeness (architect audit, 2026-08-10)

All seven gates are implemented and mutation-verified at module/unit level
(suite 154, 156/156 fault rows). The remaining distance to EXERCISED is
wiring, and it is the same list card by card — each item is a future wiring
card, none is silent debt:

- **Gate 2:** the runner does not emit 012 events (spec 012 carries the
  append-before-apply ordering obligation for that card)
- **Gate 3:** execution policy is not wired to the broker transport
- **Gate 4:** shadow/regret is isolated, not operationally integrated
- **Gate 5:** evidence/directive PRODUCTION wiring is partial (the runner's
  directive channel is WIRED; news_claude does not yet emit Evidence objects)
- **Gate 6:** no forecast producer is wired
- **Gate 7:** portfolio guards sit on no pre-trade path

## Already built (not specs — real code)
`daytrade/stockfish_exit.py` · `daytrade/runner.py` · `daytrade/broker.py` ·
`daytrade/alphazero_bias.py` (v1 placeholder brain) — see `daytrade/README.md`.

The post-v1 scaffold is in `010_LONG_TERM_VISION.md`. Its `[PLAN]` cards are
deliberately not safe to build from until they are promoted to `[SPEC]`. The
Claude implementation handoff is `CLAUDE_LONG_TERM_HANDOFF.md`.

## The five rules that govern every file here
1. **One implementation of every decision.** Runner and harness are I/O; they
   import engines, they never re-implement. If a rule can close, reduce, or move
   a stop, it lives in `decide_exit`.
2. **Fail loud.** Stale data, malformed state, unknown enum → raise. Never a
   silent fallback to a "safe" guess.
3. **Every prediction gets scored, against a dumb baseline.** An accuracy number
   without the score a brainless model gets on identical data is not evidence.
4. **Rule changes commit BEFORE the next session.** Never after a loss.
5. **Tune split only.** All tuning, eyeballing and ceiling measurement happens on
   the oldest 40 sessions. The sealed 20 produce ONE number, ONCE, after
   `rule_version` is frozen. Enforced in `daytrade/splits.py`, not in intentions.
6. **No look-ahead, ever.** `classify()` sees bars ≤ N; `grade()` sees N+1..N+K
   and never feeds back into a classification. Assert it at the boundary.

## Where the ideas came from
`I_AM_A_GOOD_TRADER.md` — Colin's doctrine, the entry read, irreplaceable.
`ARCHITECTURE.md` — layer map and the APEX safety spine.
`BUILD_PLAN.md` — the regime-first thesis these specs decompose.
`SANITY_AUDIT.md` — why every number here needs a baseline. Read it before
building anything that produces a percentage.
