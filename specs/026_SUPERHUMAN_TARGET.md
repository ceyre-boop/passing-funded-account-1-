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

---

# RESULT — 2026-10-06. TRAIN-side verdict: **NO-GO. SEALED-002 NOT SPENT.**

The loop ran to a verdict. The verdict is a null, and the null is the result.

## What was measured

`entry_selection.py`, TRAIN split, **n = 5,126 entries, 19 symbols, ~650
session-days** (the pre-deepening population was 336, and before that 24):

| policy | gross R | **net R** | date-clustered CI95 (net) | win |
|---|---|---|---|---|
| r_STATIC — best hand-executable | +0.0287 | **−0.0713** | [−0.1307, −0.0075] | 25% |
| r_TRAIL_WIDE | +0.0151 | −0.0849 | [−0.1362, −0.0306] | 25% |
| r_TRAIL_TIGHT | −0.0118 | −0.1118 | [−0.1519, −0.0696] | 18% |
| **r_regime** (spec 001 conditioned) | **+0.0384** | **−0.0616** | [−0.1108, −0.0099] | 23% |

Measured embedded cost: **median 0.0267R** per trade (mean 0.0339R). So the 0.10R
all-in assumption is not already covered by `simulate`'s $0.02/share; the two are
different quantities and both are carried.

## The finding that matters most

**The +0.1538R gross in `exit_quality.json` was a small-sample artefact.** On the
same entry rule and the same engine, with 15x more data, gross collapses from
**+0.1538R (n=336) to +0.0287R (n=5,126)** — a 5.4x shrinkage. Every policy is
net-NEGATIVE after costs, and every confidence interval lies entirely below zero.

So the honest statement about this system, measured on 5,126 trades rather than
336 or 24: **the OR-breakout entry rule has no edge that survives costs.** That is
a far stronger claim than anything previously in this repo, and it points at the
entry, exactly where `exit_quality`'s 57% unwinnable rate said to look.

## Gate status

- **G-S1 — FAIL on TRAIN.** Net −0.0616R against a +0.10R target, CI entirely
  below zero. Not evaluated out of sample, deliberately: see below.
- **G-S2 — FAIL.** Margin over the best hand-executable policy is **+0.0097R**
  against the pre-registered **+0.05R**. The regime read does help — it produces
  the highest gross of any policy — but by a fifth of what is required.
- **G-S3/G-S4/G-S5 — NOT EVALUATED.** They are holdout gates and the holdout was
  not spent.
- **G-S6 — SPLIT, and the detail is the useful part.** 3,100 scored calls (needs
  300: **MET**). Brier skill **−1.52** (needs > 0: **FAIL**) — the classifier is
  badly overconfident, mean confidence 0.70 against 0.20 accuracy, so the
  confidence field is actively misleading and must not be read by anything.
  But the per-regime breakdown is not a null:
  - `CONTINUATION` n=1,260, accuracy **0.348** vs time-prior baseline 0.198 →
    **lift +0.149**; vs always-consolidation 0.113 → lift +0.235. Real
    discrimination.
  - `CONSOLIDATION` n=1,840, lift over always-consolidation **exactly 0.000**. It
    *is* the baseline. No skill.
  - `MANIPULATION` n=0 gradeable — the sweep-and-reclaim condition never fired
    gradeably, partly because ONH/ONL are unavailable (RTH-clipped source), so
    only PDH/PDL/ORH/ORL pools exist.

## Why SEALED-002 was NOT read

`gate_check_026.py` returns NO-GO, and this is the spec's fourth stop condition
made explicit. On TRAIN the regime policy has every advantage available to it: it
was built here, its thresholds were chosen here, and it has been looked at
freely. A margin it cannot produce under those conditions cannot appear out of
sample. Reading SEALED-002 now would burn a 461-entry holdout to confirm a
failure that is already visible for free — which is precisely how SEALED-001 was
spent on 2026-08-17 (tune promised +0.1135R, holdout returned −0.1066R,
NOT_VALIDATED).

**SEALED-002 remains sealed: 461 entries, >= 2026-08-18, never read.** That is an
asset, and preserving it is the correct outcome of this run, not a shortfall.

## What the next cycle should attack, in order

1. **The entry rule, not the exit.** Three independent measurements now agree:
   57% of entries unwinnable, gross +0.029R on n=5,126, and the exit-side prize
   needs perfect foresight. The OR-breakout trigger is the binding constraint.
2. **Delete or recalibrate `confidence`.** A Brier skill of −1.52 means the number
   is worse than useless. Either calibrate it against the 3,100 scored calls now
   on disk, or stop emitting it. Nothing downstream should read it meanwhile.
3. **Keep the CONTINUATION arm.** +0.149 lift over the time prior on n=1,260 is
   the one piece of genuine signal the classifier produced. The CONSOLIDATION arm
   adds nothing and MANIPULATION never fires — a v2 should be the CONTINUATION
   detector alone, which is a smaller and more honest object.
4. **Re-fit TIME_PRIORS from the scorecard**, which spec 001 said would be the
   first thing to do once ~200 scored blocks existed. There are now 3,100.
