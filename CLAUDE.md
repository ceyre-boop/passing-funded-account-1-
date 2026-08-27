# CLAUDE.md — passing-funded-account-1
**One repo, one job: pass the first funded evaluation. Nothing else lives here.**

> **READ FIRST: `ARCHITECTURE.md`** — the definitive spec as of 2026-08-03
> (day-trading cockpit: ALPHAZERO bias layer + STOCKFISH exit engine + Colin's
> discretionary doctrine + ladder campaign math + shot ledger). It supersedes
> the carry-eval framing below where the two conflict; the carry stack remains
> valid as the swing lane. Doctrine: `I_AM_A_GOOD_TRADER.md`. Today's ops:
> `MONDAY_OPEN.md`. Carry-forward tasks: `NEXT.md`.
>
> **Building anything? Read `specs/README.md` then `specs/000_RULINGS_AND_ORDER.md`.**
> Written specs for all remaining components, labeled `[SPEC]` (safe to build
> from) or `[SKETCH]` (needs a planning pass first — do not build). Build order
> is fixed there: backtest bench → regime → scorecard → survival → stockfish v2
> → alphazero v2 → brief.

---

## What this repo is

The proven slice of `ceyre-boop/quant`, repurposed for a single purpose. Full
research, provenance, and history stays in the general repo — see
`FUNDED_ACCOUNT_REPO_MANIFEST.md` there for what was copied and why.

**The edge:** v015 4-pair FX carry (EURUSD, GBPUSD, USDJPY, AUDUSD). OOS Sharpe
1.25, p<0.001, survives BH correction, decay ratio 2.17 ROBUST. See
`data/proof/backtest_trades_v015_2015_2024.csv` (411 sealed trades) and
`data/proof/carry_hypothesis_lineage.json` (HYP-045, HYP-059, HYP-108 — the
three hypotheses this specific edge's history actually rests on).

**The strategy:** the v015 carry edge, evaluated by `scripts/carry_buy_gate.py`
per `specs/021_CARRY_BUY_GATE.md`. Eval sizing is being re-derived under the real
firm contracts (`data/propfirm/firm_contracts.yaml`) and is NOT ratified yet —
`FIRM_FIT.md`'s 2% static-DD is the prior. `COLIN_V1.md`'s "92% pass at 0.5%" is
superseded (unsourced, Lucid-conditioned; see its banner). No P(pass) number is
quotable without the zero-edge control printed beside it (`SANITY_AUDIT.md`).
Own-capital sizing (1.00%) from COLIN_V1 still stands.

**The method:** `ALTA_METHOD.md`. Five steps, in order, every trade: pre-trade
gate check, two confirmations, size, hold, exit and log. Templates for all
four in `data/trade_logs/`.

## Non-negotiables

1. **No new hypotheses get generated here.** This repo runs one already-proven
   edge. If you find yourself wanting to test a variant, that work happens in
   the general repo, not here — bring it back only after it clears the same
   bar HYP-045/HYP-059 already cleared.
2. **`sovereign/execution/forex_exit_manager.py` ships in SHADOW MODE.**
   Flipping it live is a real decision, not a default — log the reason before
   changing it, same discipline as the general repo's execution-path freeze.
3. **Every decision goes through `sovereign/intelligence/decision_logger.py`.**
   Every close gets `update_outcome()`. No exceptions — an unlogged trade is
   silent data loss, and this repo's whole job depends on a clean, honest
   record for the funded account's own risk rules.
4. **Kelly sizing is bounded.** `sovereign/risk/kelly_engine.py` computes
   proper quarter-Kelly (`f* = (p.b - q) / b`, capped at 25% of full Kelly),
   then `sovereign/risk/layers/prop.py` applies the funded-account ceiling on
   top. Never bypass either layer to size up faster.
5. **`.env` is gitignored and stays that way.** Copy `.env.example`, fill in
   real values locally, never commit the real file.

6. **Trade-evidence freeze.** No new module, spec, or verification layer
   under `daytrade/` or `specs/` until `data/trade_logs/paper_carry_trades.jsonl`
   has 50 CLOSED records (`status == "closed"` and `R` is not null — an open
   paper trade is not evidence). Since 2026-07-31 this repo grew 180 commits
   and +166,722 lines while the paper ledger stayed at 0 closed trades; the
   freeze exists because that ratio is the failure mode, not a hypothetical
   one. Enforced by `scripts/check_trade_freeze.py`, wired as a git
   `commit-msg` hook at `.githooks/commit-msg` — run `sh scripts/install_hooks.sh`
   once per checkout to activate it (git doesn't track `.git/hooks` itself,
   so this step is required after every fresh clone/worktree). It blocks
   (exit 1) on the first offence — a hook that only warns gets ignored, and
   this repo's own commit history is the demonstration.

   Exempt: edits to existing files (bug fixes, data-integrity fixes normally
   land as edits); new `test_*.py` / `*_test.py` files; a short, named,
   expiring allowlist in `check_trade_freeze.py::GRANDFATHERED_PATHS` for
   work already in flight when the freeze landed (`daytrade/paper_carry_runner.py`,
   `scripts/paper_carry_log.py`, `scripts/ruin_engine.py` — expires
   2026-09-09 or when the freeze lifts, whichever comes first). Everything
   else needs a commit message with a line starting `FREEZE OVERRIDE: <reason>`
   — logged in history, not a silent bypass.

## Daily operation

`python3 scripts/carry_buy_gate.py --series sealed --update-state` refreshes the
buy-gate state (G1–G5), then
`python3 scripts/build_daily_verdict_page.py` regenerates `daily_verdict.html`
— one page, plain language, answers "should we trade live today and why"
from the real gate state in `data/agent/carry_buy_gate_state.json`. The page is
red-by-default: missing or >7-day-old state renders NOT READY. Check it before
every session. (`data/agent/prop_challenge_state.json` and
`sovereign/propfirm/deployment_checklist.py` are ICT-lane legacy — never read.)

## What's deliberately not here

ICT (unproven, permutation p=0.52 in the general repo, not confirmed).
Undertow/HYP-093 (still INSUFFICIENT_DATA as of last check). The autonomous
research factory, the hypothesis generator, the full 973-file general repo.
This repo is small on purpose. If something feels missing, that's very
possibly correct — check the general repo's manifest before assuming it
should be copied over.

## Daytrade cockpit (STOCKFISH + ALPHAZERO) — architectural boundary

This restates `specs/000_RULINGS_AND_ORDER.md`'s Ruling 1 in enforceable form.
Read that file before touching `daytrade/`. It is not new policy — it is the
same boundary made explicit enough that a violation is a checklist item, not
a judgment call.

AlphaZero communicates meaning. Stockfish controls mechanics.

AlphaZero MUST NOT:
- place orders
- calculate executable exit quantities
- move stops directly
- bypass Stockfish constitution rules

Stockfish MUST NOT:
- infer semantic meaning from news
- silently invent unavailable context
- accept stale directives

### Definition of VERIFIED

A component is VERIFIED only when every stated invariant has a named
automated test and deliberate violation of that invariant makes the suite
fail. Passing today does not stay VERIFIED tomorrow if a later edit removes
the invariant's test — re-run fault injection, don't assume.

This is stricter than, and supersedes, the plain `[BUILT]` label wherever the
two conflict. `specs/CLAUDE_LONG_TERM_HANDOFF.md`'s "Maturity sequence"
section is the full definition — VERIFIED here splits into that doc's UNIT
VERIFIED and INTEGRATION VERIFIED stages, sitting between a module existing
(IMPLEMENTED) and a module actually being on the live path (WIRED,
EXERCISED). That same doc's "Roles" section also governs who is allowed to
declare a stage cleared: the seat that wrote a card's `[SPEC]` does not also
get to certify its own tests pass.

### Development rules

1. Read the relevant spec before editing code.
2. Do not alter approved tests to make implementation pass.
3. Never silently default unavailable values to numeric zero.
4. Make invalid states unrepresentable where practical.
5. Prefer explicit failure over fallback for correctness-critical inputs.
6. Run unit + integration + replay suites before claiming completion.
7. Never claim WIRED based solely on imports.
8. Never claim EXERCISED without evidence from the real execution path.
9. No live brokerage credentials or live-order access in agent environments.
10. Do not modify sealed evaluation data.
