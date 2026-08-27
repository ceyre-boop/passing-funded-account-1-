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

## Phase 0 and Phase 1 — CLOSED 2026-08-26

All three disconnects from `Plans/THE_BIG_PLAN.md` are wired. 832 passing.

- **D1 execution** — `daytrade/paper_carry_runner.py`, guarded and shipped
  **DISARMED**. Guards on the path in order: streak cooloff → portfolio_guard
  Gate 7 → survival. Arming takes three coordinated actions. `paper_carry_trades
  .jsonl` is still 0 bytes and will stay so until someone arms it. (`53e2f9c`)
- **D2 risk** — non-negotiable #4 is now TRUE. `kelly_math.py` extracted the pure
  functions out of the unimportable `kelly_engine`, so the Layer 2 quarter-Kelly
  ceiling actually binds alongside `prop.py`. No threshold changed. The NaN
  hazard is closed: a NaN input returned 0.04 (the CEILING, max size) and now
  returns 0.005 (the floor). (`19a0f92`)
- **D3 learning loop** — all three links wired, authority unchanged. The model
  now states a policy and gets graded on it while `policy_candidate` stays gated
  behind `granted_level >= 2`; production still resolves to exactly `(1, '')`.
  Boundary proved by injection, not by reading. (`d60ca9c`, `24ee12b`)
- **Wiring detector** — `scripts/wiring_audit.py`, 128 findings, all allowlisted
  with written reasons; a new disconnect fails the suite. (`17f5a03`, `df4d5b4`)
- `survival.py` and `streak.py` built and wired (`a6c8ac6`, `a2df8c1`, `3ad7524`).

## THE GATE — three unratified numbers, and they are Colin's

Not code. The last thing between the built path and a first paper trade. An
agent guessing here is precisely the failure mode the whole discipline exists to
prevent, so all three were left as **required arguments** rather than defaults.

1. **`survival.py`'s daily-goal figure.** Spec 002 is written for the intraday
   cockpit's $-per-day doctrine. Carry holds a median 6 days across the 411
   sealed trades, so "already banked today's goal, stop" has no carry-lane
   analogue on record. Separately, spec 002's own formula
   (`account_size * daily_goal_pct / 100`) drifts as the account grows while
   THE_SHOT.md's doctrine says a fixed $300/day — they coincide exactly at $25k
   and 1.2%, which is why it has gone unnoticed.
2. **`portfolio_guard` G001–G004 exposure caps** for a 4-pair book: total open
   risk, per-symbol, correlated, and max unprotected. None ratified anywhere.
3. **Eval sizing.** `max_safe_risk` is 0.328% on `cti_1step` against a planned
   1.00%, and the zero-edge separation table above says the same thing from the
   other direction. Ratify through spec 021.

Note `resolve_r_limits()` already derives G005/G006 from the real contract plus
the measured `max_safe_risk` — `alpha_swing` yields 9.53R lock / 19.06R flatten.
`cti_1step` REFUSES, correctly: it has no `daily_dd`, and the existing fallback
convention would derive a daily lock WIDER than the emergency flatten.

## Also open

- `specs/036` G5 re-registration — `[UNRATIFIED]`, needs a ruling.
- `specs/038` — the second policy gate at `runner.py:763`. `[NOT A DEFECT]`,
  nothing to build; read it before "fixing" a promotion that appears inert.
- G3 needs one run-once OOS series.

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

## Method notes — parallel agents (learned the hard way 2026-08-26)

**Subagent worktree isolation is NOT automatic.** It happens only with an
explicit `isolation: "worktree"`. Four separate collisions tonight, every one of
which I first misread as harmless background noise:

- my own `git add` raced a builder's writes, so commit `20bb131` swept 3 files
  under a message describing a 4th — one change, two commits, and the second had
  to say so out loud
- an agent found an autostash named `concurrent-background-alpha-operator-test`
  it had not created
- one agent saw 44 transient failures from another's mid-flight edits to
  `alpha_operator.py`; another saw 8 from the Kelly workstream
- three agents independently reported "files changed that I did not touch"

Nothing was lost, but only because their file footprints happened to be nearly
disjoint. **Decide the footprint before spawning.** If overlap is not obviously
zero, pass `isolation: "worktree"`.

**"Stop and ask rather than guess" is a soft prompt instruction, not an enforced
gate.** It worked tonight — the AlphaZero agent genuinely halted with three real
questions and made zero changes — but that is behaviour, not a guarantee, and it
degrades over long runs. When it must be enforced rather than hoped for, make
forward progress structurally conditional on an answer instead of trusting the
model to remember to ask.

**A general goal does not invoke a different reasoning mode.** A subagent given
"make this claim true or make the doc honest" runs the same loop as one given a
micro-spec, with less scaffolding holding it on-path. General goals worked well
here because the constraints were sharp even where the method was open.

**Nothing is listening on localhost:31337** (verified: HTTP 000, connection
refused). Claude Code HTTP hooks can only block by returning 200 with
`"permissionDecision": "deny"` — every connection failure, timeout, or non-2xx
is non-blocking and the tool call proceeds. So any hook pointed there is
currently fail-open. Note CLAUDE.md's voice commands also target 31337 with
`curl -s`, which swallows the error. Decide deliberately whether those are meant
to enforce or merely log.

**The highest-leverage habit is Opus re-verifying subagent self-reports rather
than trusting them.** Tonight that caught: a false-positive detector finding, a
live-path import I had broken myself, a fault injection of mine that was a no-op
and briefly looked like a decorative test, and a commit whose staging had
silently drifted. Every one surfaced from re-running the check independently,
not from reading a report.

## Method note for whoever picks this up

Two of my predictions were wrong tonight, both the same way: I reasoned about
what a statistic *ought* to do instead of measuring what it does (the `null_leak`
1/√n error, and predicting T2 would fail when T3 did). The repo's guards caught
both. Fault-inject every new invariant test before trusting it — a decorative
T3 control passed 15 tests until a `k_skip = 0` mutation exposed it, which is the
fourth instance of that pattern after M9, M34, M37.
