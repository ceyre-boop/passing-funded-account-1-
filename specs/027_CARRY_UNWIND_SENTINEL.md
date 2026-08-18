# 027 — CARRY UNWIND SENTINEL `[SPEC]`

**Component:** `daytrade/carry_sentinel.py` (to be built), reusing the
operator's packet/seal machinery and the carry lane's sealed trade history.
**Status:** `[SPEC]` — written 2026-08-17, before any code and before any
window was shown to any model.
**Origin:** cross-chat strategic review. Four measurements say the OR-break
entry is a dead end; the proven edge is v015 carry (OOS Sharpe 1.25,
p<0.001, 411 sealed trades). Carry's one catastrophic failure mode — regime
breaks / carry unwinds — is narrative-driven, absent from feature columns,
rare, and asymmetric: on a Sharpe-1.25 book, avoiding two bad unwinds a
decade outearns any daytrade overlay, while a false alarm costs a little
skipped carry. This is the best-matched problem in the repo for a language
model reading news, positioning, and calendars.

## The experiment

Reconstruct point-in-time information packets for carry drawdown windows and
ask the operator COLD: "does this packet show elevated unwind risk?" Sealed
verdict (`UNWIND_RISK: none|elevated|severe` + confidence + evidence +
invalidators), scored against the realized forward drawdown label.

## Pre-registered disciplines (non-negotiable)

1. **Pretraining-leak wall.** The model may know historical outcomes from
   its weights regardless of packet hygiene. Therefore:
   - Windows BEFORE the model's knowledge cutoff: PROMPT DEVELOPMENT ONLY.
     No number from them is ever quoted as evidence, anywhere.
   - Evidence-grade numbers come ONLY from post-cutoff windows (and the
     live-forward sentinel once running). The cutoff date used for the wall
     is recorded per model version in the report.
2. **Balanced design.** ~20 worst drawdown windows AND ~20 matched calm
   windows, shuffled, unlabeled — the model must not be able to infer that
   "every packet I'm shown is a crisis."
3. **Pre-registered verdict rule** (before first scored run): sentinel has
   signal iff elevated/severe-rate on true-unwind windows exceeds its rate
   on calm windows with a one-sided Fisher exact p < 0.10 on post-cutoff
   windows only. Anything else: NO_SIGNAL, recorded, and the sentinel runs
   live-forward only (where leak is impossible) before re-judging.
4. **Same containment as everything else.** The sentinel outputs sealed
   records; it steers nothing. If it ever earns authority, that goes
   through a 017-style gate on live-forward calls only.
5. **Counterfactual accounting.** Score against the FIXED window set — every
   window, flagged or not — never only the windows that "survived" a flag
   (the circularity the knee repo's fixed-truth scoring solved).

## Amendment 2026-08-17 (pre-implementation review — BINDING)

**Pinned model & cutoff:** `claude-sonnet-5`, published training-data cutoff
**January 2026**. The sealed carry history ends 2024-12. Therefore **every
historical window is development-only under rule 1** — there is no
post-cutoff historical evidence cohort, and the sentinel cannot honestly
produce a Fisher result "in days." What it CAN produce now: a frozen
manifest, a packet pipeline, prompt-development outputs; evidence accrues
live-forward only.

**NO_EVIDENCE_COHORT assertion (hard):** an evidence cohort requires ≥1
post-cutoff unwind window AND ≥1 post-cutoff calm window. Otherwise the tool
reports `NO_EVIDENCE_COHORT`, makes ZERO scored model calls, and computes no
Fisher p. This assertion runs before any model call, every run.

**Exact labels (frozen):** equity curve = cumulative `risk_adjusted_pnl_pct`
of `backtest_trades_v015_2015_2024.csv` ordered by `exit_date` (the sealed
series itself — no external prices enter labeling). Window = 60 calendar
days starting at window_start. UNWIND label: forward 60-day equity drawdown
from window_start ≥ the 85th percentile of all rolling 60-day drawdowns in
the series. CALM label: forward 60-day drawdown ≤ the 50th percentile.
Windows may NOT overlap (min gap 60 days between selected window starts,
greedy from worst/calmest). Pair universe: the four sealed pairs only.

**Calm matching (frozen):** each unwind window matched to the calm window
nearest in (a) trailing 90-day realized equity volatility (tolerance ±50%
relative, else nearest available with the miss recorded) and (b) calendar
year ±2, without replacement, ties broken by earlier date, seed
deterministic (sorted iteration, no RNG).

**Frozen manifest:** `data/daytrade/sentinel/manifest.json` written by
`build-manifest` BEFORE any model call ever happens: every window ID, label,
window_start, packet_cutoff_ts (= window_start), cohort
(`development|evidence`), and a sha256 over the manifest body. Any later
edit to a manifest with scored calls against it is a protocol violation.

**Phase gate:** Phase 1 = `build-manifest` + `reconstruct-packets` only —
no Claude calls, no scoring, no authority. Packets are reconstructed from
the sealed trade series itself (point-in-time equity, drawdown state,
per-pair recent R, hold-time drift) with every input timestamped
≤ packet_cutoff_ts; external news/macro enrichment is a later, separately
reviewed addition. Phase 2 (scored calls) requires a non-empty evidence
cohort or runs live-forward only.

## Data sources

`data/proof/backtest_trades_v015_2015_2024.csv` (411 sealed trades) for
window selection and labels; carry gate state for the live-forward leg;
packet reconstruction from cached/fetchable macro + news history with every
item timestamped <= window start (I14 discipline, reused).

## Out of scope

Any change to the carry gate itself; any authority for the sentinel; any
quoting of pre-cutoff results as evidence; building before this spec's
verdict rule is committed.
