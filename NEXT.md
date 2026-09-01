# NEXT.md — carried into the next session

**Rewritten 2026-08-31.** The 2026-08-03 head this replaced pointed at a Tradeify
eval purchase as the live plan. That is no longer the plan of record and had become
actively misleading to any session that read it first. Everything below the
`## OPEN — loose ends` heading is older but still true; the dated sections further
down (08-15, 08-22, 08-23) are unchanged and still apply.

## Plan of record

**Venue: Interactive Brokers, own capital, paper first. Nautilus Trader stays as the
execution body.**

Reached by elimination, not preference. The funded-account path is contingent on an
edge that does not exist, so choosing prop-firm infrastructure today would be
building for a hypothetical — and an expensive one, since eval fees are real money
spent against a system with no demonstrated positive expectancy.

Nautilus ships live execution adapters for IB (needs the optional `[ib]` extra, a
one-line install) plus eight crypto venues. It ships **no** Rithmic, CQG, Tradovate,
NinjaTrader, ProjectX or Ironbeam adapter, which is what a futures prop firm routes
through. That gap is not a blocker: it is a wall in front of a door there is no key
for. If an edge ever clears SPRT and a funded account becomes live again, that is the
moment to decide whether the exit core runs outside Nautilus for that one venue — a
much easier decision with an edge in hand.

**Tradeify $25K Select / MES: DEFERRED pending a validated edge. Not the plan of
record.** Do not buy an eval, do not size for one, do not treat `THE_SHOT.md`,
`MONDAY_OPEN.md` or `select_pass_planner.py` as current operating documents. They
describe a plan whose precondition is unmet.

## What is actually true right now

**Zero validated edges.** Stated plainly because every roadmap below assumes it.

| line of enquiry | state |
|---|---|
| Entry family (AlphaZero) | CLOSED — best cell +0.0956 vs null p95 +0.4310 |
| Exit configuration search | CLOSED — 0 of 396 profitable, 2,115 trades |
| Seven earlier leads | CLOSED — 7 of 7 below detection floor |
| Four detection-floor survivors | CLOSED on economics, 2026-08-31 |
| Carry exit tablebase | CLOSED — SPRT ACCEPT_H0 both arms |
| Carry lane G5 | 0/80, NOT READY (see the 08-15 section) |

The floor tests are in `artifacts/FLOOR_TEST_RESULT.md`; the detection-floor table is
`artifacts/EVENT_STUDY_MDE.md`. Costs, not the effect, set both floors — a 1×ATR14
stop puts a third to a half of the risk unit into costs before the trade does
anything.

## Do NOT do next session

- **No work in `body/`.** It is at a clean stopping point: WIRED and EXERCISED in a
  real `BacktestEngine`, boundary enforced by the type system, 59 tests. It is
  tractable work that produces green tests and feels like progress while the edge
  column still reads zero. Stop means stop.
- **No new hypotheses in this repo** (standing rule, `CLAUDE.md` non-negotiable 1).
- **No re-run of the floor tests at a different `k_stop`.** Cost drag scales as
  `1/k_stop`, so a wider stop mechanically lowers the floor and FOMC would cross it.
  `k_stop = 1.0` was frozen at `3b10e8b` before the computation precisely so that
  move was unavailable afterwards. Taking it needs a fresh pre-registration.

## Where the two engines actually stand

Colin's own estimate, 2026-08-31: **AlphaZero ~65% built, Stockfish early beta.**
Both now have a home to plug into, and the interface is two functions:

- AlphaZero → the `policy` passed to `AlphaZeroActor`, signature `bar -> EntryDirective | None`
- Stockfish → the body of `StockfishStrategy.execute()`, which currently raises

Not done: no data adapter (the parquet caches are not in `ParquetDataCatalog`
format), no venue configured, no order-placement code, and `daytrade/`/`az/` still
assume they own the loop.

Note the gate in front of "live": `StockfishStrategy` refuses everything because the
ODD gate cannot open in v0.1 — T0 unseal is absent and nine preconditions are
UNKNOWN. Opening it requires a pre-registered edge that clears SPRT. **Finishing the
engines does not by itself produce a live system.** That is the design working.

## Open defects and hygiene

- `data/propfirm/jj_sim_account.json` — `"projection"` still shows the RETRACTED
  75%/94% JJ-style figures (killed by `SANITY_AUDIT.md` 2026-08-01). A stale
  falsehood sitting in the repo.
- `config/parameters.yml` — still shipped untrimmed.
- `NEXT.md` staleness itself — this file was ~4 weeks out of date and misdirected.
  Rewrite the head whenever the plan of record changes, not at session end.
- `sentinel/` — 8 runtime `DEGRADED_*` markers, untracked and un-ignored.
- 12 stale `.claude/worktrees/agent-*`; `com.alta.paper-carry-daily` LaunchAgent is
  loaded but its target script no longer exists.
- Rulings owed by Colin: MECH-001 status; whether `trail` stays ACTIVE (three
  evidence lines against); `Tablebase` cell-key design (state alone vs (state, phase)).
- `az/odd.py` should become a translation layer onto Nautilus `TradingState`
  (ACTIVE/REDUCING/HALTED — `REDUCING` *is* ODD §4's T2) rather than a second ladder.
- Only `spy_macro_decay` has a committed event/control split; no other study's split
  is reproducible from committed artifacts.

**Fixed 2026-08-31:** `runner.py` silently ignored a typo'd plan key on the live path
— `trail_multiplier` was dropped by a whitelist comprehension and the engine used its
own default instead of the plan's intent. Now `KNOWN_PLAN_KEYS` +
`validate_plan_keys()` refuse it loudly and name the field you meant. 19 tests,
3 of 3 mutations caught.

## First thing to do next session

Read `artifacts/FLOOR_TEST_RESULT.md` and this head before touching anything. Then
pick from the open defects above, or from the dated sections below — **not** from
`body/`, and not from a Tradeify plan.

## OPEN — loose ends from earlier in the week, still not closed
- `data/propfirm/jj_sim_account.json` — `"projection"` field still shows the RETRACTED 75%/94% JJ-style figures (SANITY_AUDIT.md killed these 2026-08-01). Never corrected in a follow-up commit. Low priority since JJ-style is parked, but it's a stale falsehood sitting in the repo — clean it up next session.
- `config/parameters.yml` — still shipped untrimmed (carry-only keys never isolated from the full general-repo config). Deferred as "needs real verification, not a 2-minute grep." Still true.
- ~~**CLAUDE.md vs FIRM_FIT.md/COLIN_V2.1 tension, never fully reconciled**~~ **RESOLVED 2026-08-10 by spec 021**: COLIN_V1's 0.5%/92% and COLIN_V2's 5% eval sizing are both banner-superseded; eval sizing is re-derived under the real CTI/Alpha/FTMO-swing contracts by `scripts/carry_buy_gate.py` (FIRM_FIT's 2% is the prior) and ratified via decision_logger before any purchase. CLAUDE.md's strategy section now says exactly this.
- NQ intraday data acquisition (Databento/Polygon) — still blocked, still needed if JJ-style/session-reversion validation ever resumes. Deprioritized behind the eval account, not abandoned.

## OPEN — v015's headline Sharpe is not independent of in-sample tuning (logged 2026-08-22, NOT investigated)
`sovereign/forex/forex_backtester.py:154-159` — `PAIR_VIX_GATES` is five per-pair
VIX thresholds (15/15/18/18/20) **selected in-sample on 2015-2024**, with the
per-pair Sharpe table that justified them sitting in the comments at `:147-153`
(USDJPY 1.004 -> 1.770, n 57-120/pair). `SIGNAL_THRESHOLD` was separately lowered
0.20 -> 0.15 (`:118`) for sample size, with no OOS check noted.

2015-2024 is the same decade as the sealed 411-trade proof set. So the Sharpe 1.25
that CLAUDE.md cites as "the edge" was measured on a series produced by an engine
carrying five thresholds tuned on that same series. **The number is not wrong, but
it is not independent, and nobody has quantified by how much.**

Consequence to hold onto: no sizing decision should treat Sharpe 1.25 as a clean
out-of-sample estimate. It is an upper bound of unknown tightness.

Why it was not measured on 2026-08-22: `data/oos_trades_2025_2026.json` (60 trades)
has no committed generator, and the rig that plausibly produced it reproduces only
296 of the 411 sealed in-sample trades (`data/agent/repro_gap_report.json`). The
clean measurement is not currently available. Restoring that generator is
`scripts/diagnose_repro_gap.py:63` RESTORE_LIST item 5 and is the real prerequisite.

Precedent that this instinct pays: `PAIR_HOLD_OVERRIDES` was rolled back to `{}` on
2026-06-07 after exactly this kind of check failed (OOS delta +0.055,
regime-concentrated, AUDUSD negative).

## NEXT UP — the observed−predicted roadmap (written 2026-08-22, starts after Monday)
`Plans/lucky-forging-hoare.md` holds a four-stage roadmap toward studying this
system's own trading scientifically. Read it before adding ANY new predictive field.

Headline: the predicted world is already built and running — sealed `pre_registration`
bands, 5-way scenario probabilities graded by Brier, 6,893 point-in-time decision rows,
evidence sealed at judgment time. Three defects block the science, and none of them is
a missing field:

1. **The join does not exist.** `records.jsonl.trade_id` is hardcoded `None`
   (`alpha_operator.py:859,966`); `decisions_*.jsonl.evidence_ids` is always `[]`
   because `runner.py:646-670` never passes it. Both fields exist on both sides.
   Neither end is plugged in, so a belief cannot be joined to the trade it produced.
2. **`alpha_operator.py:1027` scores against a mutable file.** It reads `plan.json`
   from disk at resolution time for the risk denominator, though its docstring claims
   the plan risk is sealed at decision time. It is sealed nowhere, and the mechanical
   writer overwrites that file once per session by design.
3. **`policy_regret_r` is 0/13 since 2026-08-09** — a field with a consumer
   (`forecast.py:324`) and no supplier. Gates with no data fail closed by design, so
   AlphaZero currently cannot be promoted for a plumbing reason, not a merit one.

Stage 0 is those three plus adding `forecast_id` to the ledger row. Stages 1-3:
unbrick the gate, register `expected_mae`/`bars_to_tp1` as full spec-029 mechanisms
(expect the MDE gate to refuse them at n=39 days — a true answer), then build spec
030's step 3, the bounded exploration budget, which has never been started.

## ANSWERED — "AlphaZero rescoped as walk-forward" (item raised 2026-08-03, resolved 2026-08-22)
That item, open since 2026-08-03, was examined properly on 2026-08-22 and the answer
is **do not build it yet**. Full evidence in `Plans/lucky-forging-hoare.md`; the short
version: AlphaZero has no fitted parameters to walk forward (keyword table + prompt +
hand-set constants), the two components that WERE genuinely fitted both scored worse
than their baselines and are recorded `killed` in `MECHANISMS.json` (MECH-004,
`residual_model.json` NO_SKILL), a working rolling WFA already exists unused at
`backtester/walk_forward.py`, and the daytrade lane has 24 tune entries and 13
resolved forecasts against a gate needing 50. `specs/005_BACKTEST.md:75-77` already
gates this behind a pre-registered protocol for the stated reason that "the bench
makes it mechanically easy, which is exactly the danger."

Revive only when: something acquires real fitted parameters that are production
candidates, AND the lane has enough samples to split, AND the pre-registered protocol
is written and ratified. Then start from `backtester/walk_forward.py` and add the
purge gap — the one genuinely missing idea (zero purge/embargo implementations exist
repo-wide).

## Market rumors (raised 2026-08-03) — flagged, not modeled, not actioned. Standing rule.
Colin referenced (housing crash 80-90%, "Oct 5 Bitcoin move via elite manipulation," reseller game inventory as a spending tell, Gen Z drinking stats). Explicitly kept OUT of THE_SHOT.md and MONDAY_OPEN.md — unconfirmed/unfalsifiable claims are exactly what "trade what you see, not what you believe" rules out. If Colin brings these up again, they stay in the "watch for confirmation" bucket, never become model inputs.

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

---

# 2026-08-23 — the cost monitor has never worked (found while hunting a $40 bill)

Colin reported wasting ~$40 on API spend "that did nothing." The hunt found the
money is not recorded anywhere on this machine — and, more importantly, found
why it could disappear unnoticed.

## Tracked API spend, all of it

| source | real API spend |
|---|---|
| `data/daytrade/llm_spend.jsonl` (this repo) | **$0.95** — 27 calls, all-time |
| `~/quant/logs/oracle_cost.json` (morning briefings) | **$5.25** — 101 calls since 2026-06-02 |
| **total accounted for** | **$6.20** |

Note `~/quant`'s August figure is **$0.00 across 48 calls** — that lane switched
to local `ollama/qwen2.5`. Good call by whoever made it.

Do NOT be alarmed by `~/.claude/PAI/MEMORY/OBSERVABILITY/session-costs.jsonl`,
which totals ~$23,238 across 5,073 sessions since May. That is the API-EQUIVALENT
cost of Claude Code usage on a subscription, not money paid.

## The actual defect

`~/.claude/PAI/MEMORY/OBSERVABILITY/anthropic-cost.jsonl` is the cost monitor.
**412 samples since 2026-05-19. Every single one is empty:**

- `api_spend.month_used_usd` — `null` in 412/412, `source: "unavailable"` in
  412/412. It has never once read the real spend figure.
- `call_sites.total` — `0` in 412/412. Its call-site auditor has never detected
  a single API call site, while `daytrade/news_claude.py` and the quant oracle
  were both demonstrably calling the API throughout that period.
- `alerts` — empty, every sample.

**A watcher that reports nothing is indistinguishable from a healthy system.**
This is the same failure family as `write_baseline_plan.py` crashing into a
swallowed error 121 times (fixed 9f72a2a) and `policy_regret_r` having a
consumer and no supplier (logged 44fe744). Three months of "no alerts" was read
as "fine".

## What to do

1. **The authoritative record is `console.anthropic.com` → Usage**, filtered by
   key and date. Ten seconds there beats three months of local monitoring.
2. **Set an organization-level spend limit in the console** (Settings → Limits).
   That is the only control that covers ALL keys and ALL consumers regardless of
   whether the calling code knows a cap exists. The code cap below does not.
3. **Fix or delete the monitor.** Either make the probe actually read spend and
   detect call sites — with a test that fails when it returns null — or remove
   it. Leaving it running while it reports nothing is worse than not having it,
   because its silence reads as reassurance.
4. **One API key per consumer.** Shared keys make spend unattributable; that is
   why "where did $40 go" took a scavenger hunt instead of a lookup.

## What was fixed here (2026-08-23)

`daytrade/news_claude.py`'s cap was **lifetime, not daily** — `spent_so_far()`
summed the entire ledger and `DEFAULT_CAP_USD = 5.00`, so at $0.95 spent the
operator had ~$4 of headroom *for the life of the project* and would then have
gone silent mid-soak with no obvious cause. Now: **$0.50/day, resetting midnight
ET**, refusing loudly with the reset time in the message. A corrupt or
timezone-naive ledger timestamp raises rather than being silently counted or
skipped.

**This cap governs `news_claude.py` only.** It is not, and cannot be, the answer
to "limit all my keys."
