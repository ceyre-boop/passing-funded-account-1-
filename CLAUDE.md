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

**The strategy:** `COLIN_V1.md`. Read it before touching sizing. Short version:
0.50% risk/trade for the funded eval (92% pass probability, no deadline,
~11-month median), 1.00% risk/trade once trading own capital.

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

## Daily operation

`python3 scripts/build_daily_verdict_page.py` regenerates `daily_verdict.html`
— one page, plain language, answers "should we trade live today and why"
from the real gate state in `data/agent/prop_challenge_state.json` and
`sovereign/propfirm/deployment_checklist.py`. Check it before every session.

## What's deliberately not here

ICT (unproven, permutation p=0.52 in the general repo, not confirmed).
Undertow/HYP-093 (still INSUFFICIENT_DATA as of last check). The autonomous
research factory, the hypothesis generator, the full 973-file general repo.
This repo is small on purpose. If something feels missing, that's very
possibly correct — check the general repo's manifest before assuming it
should be copied over.
