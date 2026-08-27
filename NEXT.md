# NEXT.md — carried into the next session

Rewritten 2026-08-26. The previous version described 2026-08-03: Tradeify, MES,
THE_SHOT v2, a ladder campaign. That firm selection is superseded — the live
contracts are `cti_1step` / `alpha_swing` / `ftmo_swing` in
`data/propfirm/firm_contracts.yaml`, evaluated by `scripts/carry_buy_gate.py`.

**Read `Plans/THE_BIG_PLAN.md` first.** It is the full-repo map from four
verified audits and it supersedes any architectural summary elsewhere.

---

## The one-paragraph state of things

This repo is a research instrument of unusual quality that **has never placed an
order**. Of 57 loaded `com.alta.*`/`com.sovereign.*` LaunchAgents on this
machine, 56 run a *different* checkout (`~/quant`, branch `sovereign-v2`, which
has no `daytrade/`). This repo runs two jobs: a 5-minute operator tick that
seals a SHADOW judgment, and a 16:40 dashboard publish. The daily terminal
action is a `git push`. Both of the daytrade lane's edge questions have now been
answered by pre-registered verifiers, and both answers are negative. The carry
lane still holds the only sealed proof. The distance to a funded account is not
edge discovery — it is four wires that were built, tested, and never connected.

## What closed on 2026-08-26 (do not re-open without new information)

**Exit-policy selection — closed, structurally.** `oracle_audit` re-run on 402
tune days (vs 39 effective before). `null_leak` 1.5838 against a 0.15 gate.
Critically, `null_leak` does NOT shrink with n — it converges *upward*
(1.4496 → 1.5813 as n goes 39 → 402) because it is `E[max of K columns] −
best_fixed`, a property of the marginal distribution, not an estimation error.
**More data cannot fix this.** The levers are fewer families or tighter
dispersion. Sealed: `data/daytrade/oracle_audit_nvda_extended.json`.

**Entry-side trade/skip — closed.** `specs/037` pre-registered before any fit,
then run: T1 PASS, T2 PASS (44.8% trade rate), T3/T4/T5 FAIL. The model's OOF
mean R (−0.0112) is worse than always-trade (−0.0061) and worse than a
rate-matched coin flip (−0.0036). Selection carries negative information.
Per 037's own terms this closes the entry question on this population — pending
a *different entry rule*, not a different exit and not more data.

**Consequence:** the NVDA opening-range-break cockpit is finished as an edge
source for now. That is a result, not a failure. The architecture (AlphaZero
meaning / Stockfish mechanics) is sound and genuinely enforced; what failed is
one specific strategy hypothesis inside it.

## Sizing: two independent arguments now converge on SMALL

1. **Survival.** `python3 scripts/drawdown_margin.py --firm cti_1step --risk 0.01`
   → the realized sealed curve BREACHES the 5% trailing floor on 2018-02-08.
   `max_safe_risk = 0.328%`. Every risk in the pre-registered sweep
   (1.00–3.00%) sits above the survival ceiling. The buy gate now prints this
   line beside whatever risk it quotes.
2. **Evidence.** Separation from the zero-edge control is maximised at the
   LOWEST risk and vanishes at high risk:

   | risk | 365d sep | 730d sep |
   |---|---|---|
   | 1.00% | +26.8pp | +40.6pp |
   | 1.50% | +2.4pp | +2.5pp |
   | 2.00% | +4.6pp | **−0.0pp** |
   | 2.50% | +8.1pp | **−0.4pp** |
   | 3.00% | +8.0pp | **−0.4pp** |

   At 730d above 2% risk a coin flip passes as often as the real edge — G3's
   shape fails outright. That is SANITY_AUDIT's mechanism appearing in live
   output: with enough retries P(pass) measures retry structure, not edge.

Sizing up does not merely risk more. **It destroys the evidence that there is an
edge at all.**

## G5 — I had this backwards, twice. Read `specs/036`.

I twice recommended DROPPING G5's n≥80 to something reachable. Wrong direction.
Sealed per-trade σ is **1.6922**, so the ±0.25R band needs **n≈283** (6.9 years
at 41.4 trades/yr). n=80 resolves only **±0.471R** — nearly double the band it
polices. G5 is UNDER-powered. And `specs/021:119`'s defence of the band
("σ/√n ≈ 0.13R") reconciles with no σ in the series (per-trade 0.189,
after-haircut 0.188, daily-nonzero 0.264, daily-all 0.087; you would need
σ=1.1628, which is nowhere). Options drafted, no recommendation, `[UNRATIFIED]`
— **Colin ratifies, an agent never self-ratifies a gate change.**

## Next work, in dependency order (Phase 1 of THE_BIG_PLAN)

Risk before execution, deliberately: an execution path without enforced risk is
worse than no execution path.

1. `daytrade/survival.py` — the pre-trade "risk $X → worst case $Y" sentence.
   Spec 002, no blockers. *(in flight 2026-08-26)*
2. `daytrade/streak.py` — the two-red-days cooloff, made mechanical. Spec 004
   second half; the `[SKETCH]` scorecard→classifier loop stays unbuilt.
   `friction_ladder.py` says the ladder's 93–98% pass probability depends on
   this being real; `TRAINING_DAY_1.md:138` currently says "cooloff is not
   enforced by code — you enforce it." *(in flight)*
3. **THE OPEN FORK — Colin's call.** `CLAUDE.md` non-negotiable #4 says Kelly
   sizing is bounded and "never bypass either layer." `rg kelly_engine|risk_engine`
   outside `sovereign/` returns nothing; those modules have zero callers and
   zero tests. Either wire them onto the sizing path, or delete the claim. A
   stated control that is not connected is worse than no stated control, because
   it reads as protection. Characterisation tests are *in flight* — do not wire
   before they land.
4. Give `portfolio_guard` a real Gate 7 that can block an order. Its docstring
   says "Gate 7 enforces last"; no Gate 7 exists.
5. Then, and only then, a PAPER execution path. That is the only thing that can
   ever fill `data/trade_logs/paper_carry_trades.jsonl` (0 bytes) and close G5.

## Still open, verified still true

- **v015's Sharpe is not independent of in-sample tuning.**
  `sovereign/forex/forex_backtester.py:154-159` — five per-pair VIX gates
  selected in-sample on 2015-2024, the same decade as the sealed proof set.
  Sharpe 1.25 is an upper bound of unknown tightness. Uninvestigated.
- **`carry_engine._get_rate`** has a third silent-fallback chain
  (`get_rate_history` → `FALLBACK_RATES` → a hardcoded 2024 table on any
  exception). Must be fixed before `CarryEngine` is ever wired.
- **`synthetic_fields`/`source_map` have zero consumers.** FRED provenance is now
  labelled honestly but nothing refuses to trade on synthetic macro.
- **`sovereign.data.adapter` does not exist**, referenced at
  `execution/harness.py:256` and `backtester/data.py:150`, both swallowing it
  into `[]`/`None`.
- **Futures intraday history is still capped** — Alpaca serves US equities back
  to 2016; `ES=F`/`NQ=F` etc. remain on yfinance's ~60 days.
- `config/parameters.yml` — RESOLVED, file no longer exists.
- `data/propfirm/jj_sim_account.json` — RESOLVED 2026-08-26, the retracted
  75%/94% projection is now explicitly marked RETRACTED rather than sitting
  there as a quotable number.

## Daily operation

```bash
python3 scripts/carry_buy_gate.py --series sealed --update-state
python3 scripts/build_daily_verdict_page.py
```
Gate state refreshed 2026-08-26. **VERDICT: NOT READY** (G1 GREEN, G2 GREEN,
G3 RED — needs a run-once OOS series, G4 GREEN, G5 RED at 0/80).
The page is red-by-default: state older than 7 days renders NOT READY on
staleness alone.

## Method note for whoever picks this up

Two of my predictions were wrong tonight, both the same way: I reasoned about
what a statistic *ought* to do instead of measuring what it does (the `null_leak`
1/√n error, and predicting T2 would fail when T3 did). The repo's guards caught
both. Fault-inject every new invariant test before trusting it — a decorative
T3 control passed 15 tests until a `k_skip = 0` mutation exposed it, which is the
fourth instance of that pattern after M9, M34, M37.
