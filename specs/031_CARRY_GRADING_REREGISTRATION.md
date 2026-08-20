# 031 — CARRY GRADING: RE-REGISTRATION DRAFT `[UNRATIFIED]`

**Status: DRAFT. Not in force. Colin ratifies or rejects; an agent never
self-ratifies a gate change.**
**Written:** 2026-08-20, BEFORE any FX instrumentation work began and before
any new number was produced. That ordering is the point: a gate changed after
seeing what the change would yield is not a gate.

## Why this document exists at all

Spec 021 pre-registered G5: **≥80 closed paper trades** via
`paper_carry_log.py`, paper mean R within ±0.25R of the sealed +0.3556.
It stands at **0/80**.

A rate computation on the sealed record — 411 trades spanning 9.9 years,
**0.80 trades/week pooled across all four pairs** — implies that G5 as written
is a **1.9-year gate**. Nothing was peeked at to learn this: trade count and
date span are the same fields G1 GOLDEN already reads and reports, and no
holdout was unsealed.

That is a discovery about the *gate's cost*, not about the edge. It is
exactly the situation where the temptation to quietly move a goalpost is
strongest, so the reason is written here first, in the open, and hashed.

## What is NOT being questioned

- The edge itself. v015 carry: OOS Sharpe 1.25, p<0.001, 411 sealed trades,
  survives BH correction, decay ratio 2.17 ROBUST.
- G1 GOLDEN, G2 REPRO, G3 EDGE, G4 FIT. All stand exactly as written.
- The sealed 2015–2024 record. Untouched, unre-cut, unre-read.
- The `±0.25R` agreement band in G5. If G5 runs, that tolerance is unchanged.

## The question, stated neutrally

G5 asks paper trading to confirm what a sealed, corrected, out-of-sample
411-trade record already says. Two readings, both defensible:

**Reading A — G5 is measuring something the backtest cannot.**
Execution reality: real fills, real swap accrual, real weekend gaps, real
platform behavior, and the operator's own discipline under live conditions.
None of that is in a backtest. Under this reading 1.9 years is the honest
price of the only evidence that matters, and the correct response is to
start the sprint tonight and accept the calendar.

**Reading B — G5 is redundant confirmation at a 20-month cost.**
The sealed record IS the out-of-sample evidence; re-observing the same edge
forward at 0.8 trades/week adds ~80 draws from a distribution already
characterised by 411. Under this reading the binding risk is not "is the edge
real" but "does execution match assumption" — a *different* question that a
much smaller sample answers.

**These readings imply different gates, and the difference is not cosmetic.**

## Proposed re-registration (for ratification, not in force)

If Colin ratifies, G5 is **split into two gates that ask separate questions**,
neither of which relaxes the original standard:

| gate | question | requirement |
|---|---|---|
| **G5a EXECUTION** | does live execution match the backtest's assumptions? | **20 closed paper trades** (~6 months at 0.8/wk), scored on *execution fidelity*: realized swap accrual within ±20% of the contract's modeled haircut; realized slippage on entry/exit within a pre-committed band; zero unexplained fills. **Not** an edge test. |
| **G5b EDGE** | does the live edge match the sealed edge? | unchanged: **80 closed trades**, mean R within ±0.25R of +0.3556. Runs in the background and completes when it completes. |

**The consequence being requested:** a BUY verdict may be issued on
G1–G4 + **G5a**, with G5b remaining open as a standing monitor whose breach
revokes the verdict. Rationale: G1–G4 + the sealed record establish *the edge
exists*; G5a establishes *this operator can execute it as modeled*; G5b
establishes *the edge persists*, which is a monitoring question, not a
gating one, once the first two hold.

**Pre-committed failure conditions (registered now, before any G5a data):**
- Any single execution-fidelity check failing at n=20 → G5a RED, no verdict,
  and the failure's cause must be named before a re-run.
- G5b mean R drifting outside ±0.25R at any n≥40 → verdict revoked
  automatically, not reviewed.
- If G5a passes and G5b later fails, the record must state that the split
  gate let a bad verdict through for that interval. That admission is part
  of the price of this change and is registered here, in advance.

## Second re-registration: grading resolution

Proposed: grade on **per-day attribution** rather than per-closed-trade —
each day a position is held yields swap accrual, price change, and weekend
exposure as separately attributable components.

**Honest correction to the case for it.** This does NOT multiply the sample
5–6×. Days within one position are serially dependent; five days of one trade
are not five independent bets, and treating them as such would inflate
significance exactly the way same-day cross-symbol entries did in the daytrade
lane (specs/025:26-29). What daily attribution genuinely buys:

- **Attribution, not power**: how much of realized R came from carry accrual
  vs price vs weekend gap — currently unmeasurable.
- **Faster detection of within-trade mechanisms**: "the weekend costs what
  the contract says it costs" is answerable in weeks, not years.
- **Denser feedback on the model of a trade**, which is what the mechanism
  ledger consumes.

Registered accordingly: **daily attribution is an attribution instrument, and
its outputs may never be used as independent observations in a significance
test.** Any test over day-rows must cluster by trade_id.

## Ratification

Unratified until Colin records a decision below and the file is re-sealed.
Rejecting this document is a complete and legitimate outcome — it means G5
stands at 80 trades and the sprint takes as long as it takes.

```
DECISION: [ ] adopt as written   [ ] adopt with amendments   [ ] reject
BY:                        DATE:
REASON (required, written before any G5a number is read):
```
