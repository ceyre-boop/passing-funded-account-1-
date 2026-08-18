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

## Data sources

`data/proof/backtest_trades_v015_2015_2024.csv` (411 sealed trades) for
window selection and labels; carry gate state for the live-forward leg;
packet reconstruction from cached/fetchable macro + news history with every
item timestamped <= window start (I14 discipline, reused).

## Out of scope

Any change to the carry gate itself; any authority for the sentinel; any
quoting of pre-cutoff results as evidence; building before this spec's
verdict rule is committed.
