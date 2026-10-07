# 026 — SUPERHUMAN-001: the quantified target
**Status: [SPEC] — PRE-REGISTERED 2026-10-06, before any model was fitted or any
new bar was pulled. Gates below are derived from the account economics in
CLAUDE.md, NOT from looking at a result.**

## Why this file exists

Colin asked for Stockfish + AlphaZero to "trade better than any human could ever
without pure luck." That sentence is not measurable as written: markets have no
fixed rule set, no clean win condition, and no opponent to play 100 games
against. `specs/000` already says so — *"There is no ELO because there are no
rules and no clean win condition — that is the honest disanalogy with chess."*

So the superlative is translated into four measurable claims. "Better than a
human" becomes *beats the best hand-executable policy*. "Without pure luck"
becomes *survives a holdout, a placebo control, a multiple-comparison
correction, and a split-half check*. A number that clears all of them is the
strongest honest version of the request. A number that does not is reported as
a null, per rule 3 of `specs/README.md`.

## The arithmetic this is anchored to

From `CLAUDE.md` (the direction set 2026-09-30):
- cost per trade `C = 0.10R` (realistic, to be replaced by the measured value)
- break-even is `net = +0.05R`; bank-like income is `net = +0.10R`
- `net = gross - C`, so **break-even gross = +0.15R**, **bank-like gross = +0.20R**

If the measured cost per trade differs from 0.10R, C is replaced by the measured
value and the thresholds move with it. The thresholds are defined on NET; the
gross figures above are the translation at C = 0.10R.

## Where we stand at pre-registration (measured, not projected)

From `data/daytrade/exit_quality.json`, 336 entries, all classes:
- realized gross `+0.1538R` → net `+0.054R` at C = 0.10R — **break-even, not bank-like**
- perfect-hindsight exit ceiling `+0.8234R` (NOT achievable; the yardstick)
- **57.1% of entries are unwinnable** — no exit policy profits on them
- per class: SINGLE_NAME `+0.218R`, CASH_INDEX `+0.126R`, FUTURES `+0.0275R`

From `data/daytrade/sealed_read_futures_v1.json` (seal burned 2026-08-17):
- `futures-exit-v1` candidate `-0.1066R` vs shipped `-0.1366R`, verdict
  **NOT_VALIDATED**. The tune split had said `+0.1135R`. A full sign flip.

**The ruling this forces.** Exit tuning is the wrong lever. Total exit headroom
is `0.823 - 0.154 = 0.67R` and it requires perfect foresight, while 57% of
entries cannot be won at any exit. The binding constraint is ENTRY SELECTION.
SUPERHUMAN-001 is therefore a gate on the ENTRY layer — `specs/001_REGIME`,
build-order item #2, still `NotImplementedError` as of today.

## The splits — fixed here, before any fitting

`splits.py` `TUNE_END = 2026-07-06` stands and is not edited. Its own law says a
burned holdout is replaced by one "cut from sessions that did not exist at
tuning time." The Alpaca/SIP source removes yfinance's 60-day cap, so:

| band | dates | role |
|---|---|---|
| TRAIN | `<= 2026-07-06` (incl. deep pre-2026 history) | fit and tune, unlimited looks |
| DEV (contaminated) | `2026-07-07 .. 2026-08-17` | the burned band. Diagnostics only. **Never a headline number.** |
| **SEALED-002** | `>= 2026-08-18` | existed at no prior tuning time. ONE read, after freeze. |

`SEALED_002_START = 2026-08-18`, recorded in `splits.py` beside `TUNE_END` with
the reason the previous seal was burned.

**Power requirement.** Per-trade R has sd ≈ 0.8R, so detecting a `+0.10R`
difference at 80% power needs n ≈ 250 entries. SEALED-002 must therefore hold
**n >= 250 entries** before it is read; at pre-registration it holds 23 (NVDA
only), so the cache must be deepened across all symbols first. Deepening is
append-only (`refresh_cache` never rewrites a stored session) and provenance is
stamped per fetch.

## The gates — SUPERHUMAN-001

All six must pass. Any single failure means NOT ACHIEVED, reported as such.

**G-S1 — the edge clears bank-like, out of sample.**
On SEALED-002, mean net R/trade `>= +0.10R` (gross `>= +0.20R` at C = 0.10R),
with a date-clustered bootstrap 95% CI whose **lower bound > 0** (net
profitable). Clustering is by entry DATE, not by trade: the entry rule fires
across symbols the same morning, which is approximately one bet.

**G-S2 — it beats the best hand-executable policy.**
Margin over the best shipped baseline (STATIC / TRAIL_WIDE / TRAIL_TIGHT on
unfiltered entries, the policy a human can actually run) `>= +0.05R`, the
margin already pre-registered in `stockfish_tune.py`.

**G-S3 — the pipeline does not manufacture edge.**
A placebo control — labels/signals permuted within date, everything else
identical — run through the same pipeline must FAIL G-S1. Reported beside the
real number always, per `SANITY_AUDIT.md`.

**G-S4 — not carried by one name or one month.**
Net positive in BOTH halves of SEALED-002 by date, and on `>= 60%` of symbols
with `>= 10` entries each.

**G-S5 — multiple comparisons are paid for.**
The count of configurations/models evaluated is recorded before the read, and
the result survives a permutation test at `p < 0.05` on that count (or an
equivalent BH correction). An uncorrected margin is not evidence.

**G-S6 — the reward signal exists and is scored against a dumb baseline.**
`regime.classify` + `scorecard.grade` produce `>= 300` scored calls on TRAIN
with a **Brier skill score > 0** against a base-rate baseline. Rule 3 of
`specs/README.md`: an accuracy number without the brainless model's score on
identical data is not evidence.

## Stop conditions — when the loop ends

The loop ends when the gates have a VERDICT, not when they pass.

1. **ACHIEVED** — all six pass. Freeze `rule_version`, record, stop.
2. **NOT ACHIEVED — no edge** — G-S1 fails on SEALED-002. This is a real
   result and it terminates the loop. It does NOT license a re-read, a
   re-tune, or a third holdout. SEALED-002 is then burned, exactly as
   SEALED-001 was.
3. **BLOCKED — underpowered** — SEALED-002 cannot reach n >= 250. Report the
   achievable n and the effect it could detect; do not read it.

## What is explicitly NOT claimed

No claim of superhuman play in the AlphaZero sense is available from this
repo, now or later. There is no self-play, no search, no learned value
network, and no opponent. `specs/000` rescoped that to walk-forward learning
and this file keeps that rescoping. "Better than a human" here means and only
means: beats the best hand-executable baseline, out of sample, after costs,
with the luck explanations ruled out by G-S3 through G-S5.
