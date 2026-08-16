# NEXT.md — carried into the next Cowork session
Written 2026-08-03 ~06:45 ET, end of tonight's session. Read this first next time — don't re-derive what's already below.

## Tonight, in one paragraph
Built the honest pass-probability case for a funded eval (EVAL_LAB.md: ~40% real edge OOS, 25-55% CI, technicals ruled out as luck), then pivoted to Colin's actual ask — a one-day/ladder campaign — and locked a mechanical entry rule (THE_SHOT.md, v2 amended for Tradeify $25K Select / MES after the MT5/cTrader US-residency block). Friction-modeled the ladder (friction_ladder.py: attempt count beats edge quality, ~93-98% funded by attempt 5-7 across p=31-45%, but only if the cooloff rule is honored). Built the day-by-day pass plan against Tradeify's real consistency/drawdown rules (select_pass_planner.py, MONDAY_OPEN.md). Everything committed and pushed — repo is clean, nothing local-only.

## The two things Colin should NOT let blur together (said plainly, worth restating)
1. **The ladder math proves structure, not skill.** p=31% (coin flip after costs) passes almost as often as p=45% by attempt 5-7. A green "PASSED" checkbox is ~weak evidence about which p is real — only ~50 logged shots in the ledger will tell.
2. **Passing the eval ≠ having a live edge.** Eval-passing is a Stockfish problem (fixed rule, bounded risk, solvable). Making money live is the AlphaZero problem (unbounded time, real capital, no free restarts) — and that's the entire business, still fully unsolved, deliberately deferred tonight in favor of getting the eval bought.

## OPEN — Colin's action items (not mine, but track them)
- [ ] Buy Tradeify $25K Select, confirm cart = Select (not Instant/Lightning), confirm BOGO status at checkout (52-70% vs 77-91% campaign odds — this is the single highest-leverage checkbox tonight).
- [ ] Connect + test the platform/data feed tonight, before sleep — first live look at the execution screen should not be 9:30am tomorrow.
- [ ] Dry-run THE_SHOT v2 rule against tomorrow's actual chart: what does a 5-min close outside the OR look like on the real platform, where do OCO stop/target get placed, confirm alerts replace screen-watching.
- [ ] Put the 2-consecutive-bust cooloff on the calendar as a literal event NOW, not as an in-the-moment decision. Rule: "if Colin reports 2 busts to Claude, the cooloff starts that day" — make it enforceable by a party other than 2am-adrenaline-Colin.
- [ ] Screenshot the BOGO/checkout confirmation into the repo once purchased.

## OPEN — technical/architecture (Sovereign repo, quant.git — NOT this repo's scope, but the ask landed here)
Colin's brief for Molly (paraphrased, not yet built anywhere):
1. **Stockfish — exit engine.** One `decide_exit(state) -> action`, imported by both backtest and paper/live runners, zero second implementation. DoD: same tick stream through both harnesses, trade logs byte-identical. **Not started.**
2. **AlphaZero — edge layer, rescoped as walk-forward** (not literal self-play — markets aren't a closed simulator; named this explicitly to avoid chasing the wrong architecture). Rolling-window train, strict OOS eval, expanding/rolling retrain, log Sharpe/edge per iteration for an improvement curve. PowerShell as outer orchestrator, Python for the heavy lifting. **Not started.**
3. **Cursus Honorum — Elo arena.** 100 pre-generated question set, score Stockfish vs AlphaZero (or vs ground truth), Elo trajectory, eventual regime-timeline-style widget. **Not started, and blocked on 1+2 existing first.**
Safety framing to carry over (already in APEX principles, just apply here): paper/live isolation with explicit flag + manual confirm, fail loud, backend before frontend, discrete versioned commits not one big rewrite.
**This is real work but it is NOT tonight's or tomorrow's priority** — the eval account and the mechanical rule are. Don't let Stockfish/AlphaZero architecture eat into pre-market hours again.

## OPEN — loose ends from earlier in the week, still not closed
- `data/propfirm/jj_sim_account.json` — `"projection"` field still shows the RETRACTED 75%/94% JJ-style figures (SANITY_AUDIT.md killed these 2026-08-01). Never corrected in a follow-up commit. Low priority since JJ-style is parked, but it's a stale falsehood sitting in the repo — clean it up next session.
- `config/parameters.yml` — still shipped untrimmed (carry-only keys never isolated from the full general-repo config). Deferred as "needs real verification, not a 2-minute grep." Still true.
- ~~**CLAUDE.md vs FIRM_FIT.md/COLIN_V2.1 tension, never fully reconciled**~~ **RESOLVED 2026-08-10 by spec 021**: COLIN_V1's 0.5%/92% and COLIN_V2's 5% eval sizing are both banner-superseded; eval sizing is re-derived under the real CTI/Alpha/FTMO-swing contracts by `scripts/carry_buy_gate.py` (FIRM_FIT's 2% is the prior) and ratified via decision_logger before any purchase. CLAUDE.md's strategy section now says exactly this.
- NQ intraday data acquisition (Databento/Polygon) — still blocked, still needed if JJ-style/session-reversion validation ever resumes. Deprioritized behind the eval account, not abandoned.

## Market rumors mentioned tonight — flagged, not modeled, not actioned
Colin referenced (housing crash 80-90%, "Oct 5 Bitcoin move via elite manipulation," reseller game inventory as a spending tell, Gen Z drinking stats). Explicitly kept OUT of THE_SHOT.md and MONDAY_OPEN.md — unconfirmed/unfalsifiable claims are exactly what "trade what you see, not what you believe" rules out. If Colin brings these up again, they stay in the "watch for confirmation" bucket, never become model inputs.

## Session artifacts index (all on GitHub, main branch)
`EVAL_LAB.md` + `eval_lab.py`/`eval_lab_carry_fix.py` — honest 60-combo search, ~40% true OOS pass rate.
`ONE_DAY_PASS.md` + `dashboard/one_shot_ladder.png` — ladder math, ruled-out list.
`friction_ladder.py` — real-world friction (tilt, cooloff, setup frequency) applied to the ladder.
`THE_SHOT.md` (+ v2 amendment) — locked mechanical ORB entry rule, now MES-sized for Tradeify.
`select_pass_planner.py` + `MONDAY_OPEN.md` — day-by-day $300/day pass table against real Tradeify rules, MC realism check (median 2.5-4 weeks, not 1 week).
`data/shot_ledger.csv` — empty, waiting for row 1.

## First thing to do next session
Ask Colin: did you buy it, was BOGO available, did the dry-run go clean, is the cooloff calendar block set — before touching any new analysis. If a shot already happened, log it in `data/shot_ledger.csv` first thing.

---

# 2026-08-15 — spec 021 remediation, signal source, data integrity

Read this before touching the carry lane. Two of the items below correct
things earlier sessions reported as done.

## What is actually true right now

Gates: `G1 GREEN · G2 GREEN · G3 RED · G4 GREEN · G5 RED (0/80)` → **NOT READY**.

## Corrections to earlier claims (do not trust the old reports)

- **"Trade #1 opened" was fabricated and has been reverted.** Its entry/stop were
  copied from `paper_carry_log.py`'s usage docstring and `qty` was
  reverse-engineered until the 1% tolerance passed. G5 is 0/80; the sprint has
  never started.
- **A test was writing synthetic trades into the production decision log.** Two
  rows removed; recorded as a `DECISION_LOG`/`CORRECTION` entry.
- **`scripts/build_cb_decisions.py` DOES NOT EXIST.** `entry_engine.py:23`/`:99`
  and `diagnose_repro_gap.py:62` all name it as the builder for
  `cb_decisions.json`. It has never existed. That artifact is unreproducible.

## New tools

- **`scripts/carry_scan.py`** — the G5 signal source. Reuses
  `ForexBacktester._get_pair_signals` rather than reimplementing entry rules.
  Verified: at `--as-of 2024-12-02` it reproduces all four sealed signals with
  exact fills (1.05012 / 1.26581 / 149.508 / 0.64752). Pinned in
  `scripts/test_carry_scan.py`. Refuses (exit 2) when inputs are degraded.
  Note: macro entries fire ONLY on the first business day of the month; a
  signal on bar i fills at the OPEN of bar i+1.
- **`scripts/data_health.py`** — separates CACHE_STALE / SOURCE_DEAD /
  NO_SERIES / SYNTHETIC. Exit 1 when a traded pair's carry input is unsound.

## The blocker: 3 of 4 pairs cannot price carry

`real_rate_diff = (base_rate - base_cpi) - (quote_rate - quote_cpi)`.

| pair | state |
|---|---|
| EURUSD | sound |
| GBPUSD | UK CPI **dead at source** (FRED stopped 2025-03) |
| AUDUSD | AU CPI **dead at source** (FRED stopped 2025-01) |
| USDJPY | JP CPI is a **hardcoded 3.2 constant** |

FRED discontinued the OECD MEI family; searching for live monthly/quarterly
replacements returns only exchange-rate series. **A refresh cannot fix these.**
`data/cache/macro/JP_cpi.parquet` is 1978 rows of the literal 3.2 — a fallback
constant written to disk that looks healthy by date.

## G5 is a ~2-year gate as specified

The sealed edge trades **41.4 times/year across all four pairs**. 80 *closed*
trades is ~1.9 years of live paper. No scanner shortens that. This needs a
decision: run it forward anyway, replay the 2025-26 OOS window as a labelled
replay sprint, or re-derive G5's n with written justification.

## Next, in order

1. **Wire `synthetic_fields`/`source_map`** — `data_fetcher.py:263-283` already
   computes which values were fabricated and **no caller reads it**. Free win.
2. **Find real UK/AU/JP CPI** — DBnomics carries ONS/ABS/e-Stat, free, no key.
3. **Refresh the four fixable caches** (US_rates, US_cpi, EU_rates, UK_rates).
4. `config/` and `data/execution/` are MISSING — `rr_engine`'s R-targets
   (1.5/3.0/5.0, min_rr 2.0), `is_live()`, CAPE params and calibrated costs are
   all silently defaulted.
5. Mutation coverage is 15 rows of the 27 in `specs/021_CARRY_BUY_GATE.md`.

## Colin's action item

- [ ] Credential hygiene item outstanding — see the private session notes, not
      recorded here. (This repo is public; exposure details do not belong in it.)
