# Sanity Audit — 2026-08-01
**Requested by Colin after external review flagged the V3 numbers. Verdict: the review was right, and the problem is bigger than curve-fitting.**

## RETRACTED
**The "75% @30d / 94% @90d" JJ-style numbers are withdrawn as evidence of anything.** Two independent reasons:

1. **The critique's charge, confirmed.** The weather model was calibrated on carry's known outcome, then pointed at a strategy with zero logged trades. That is a projection of an assumed edge, not a measurement. My earlier sentence "the thing you've been working toward is real and the numbers hold on rerun" was an overclaim — the RERUN was stable; the EDGE was never tested. The rerun of a projection is still a projection.

2. **Worse — the metric itself is soft (found in this audit, beyond the critique).** Zero-edge control test: mean-centered trade outcomes (same distribution shape, expectancy exactly 0) run through the same rebuy-on-bust campaign:

| Test | Zero edge | Real/assumed edge |
|---|---|---|
| 5 trades/wk, 30d, IID | **84.7% pass** | 95.1% |
| Carry block replay, 90d | **49.9% pass** | 68.1% |
| E[evals burned] (5/wk 30d) | 1.99 | 1.38 |

**A coin flip passes 85% of 30-day campaigns at 5 trades/week.** With unlimited rebuys, "P(pass)" mostly measures trade frequency and retry structure, not edge. High-frequency pass rates were always going to look spectacular — for any strategy, including a worthless one. This is the same mechanism as the prop-firm business model: they sell the retry.

## WHERE EDGE ACTUALLY LIVES (audit's real finding)
- **Campaign cost:** edge cuts evals burned ~30% (1.38 vs 1.99). Real but small money.
- **The funded phase: 100% edge, 0% structure.** A zero-edge strategy that passes pays $0 in payouts and dies. Carry's median ~$10k/yr funded payout is the edge. **Passing was never the product. The funded account's EV is the product.**

## WHAT SURVIVES (with honest error bars now attached)
| Claim | Status |
|---|---|
| Carry P(pass ≤90d) at 5% eval sizing | **67.5% ± 14.5pp** (95% CI, n=40 non-overlapping windows → true value between ~53% and ~82%) |
| Zero-edge baseline same test: 49.9% | Carry's pass-metric lift over noise is real but modest — its value is funded-phase EV, as designed |
| Split-sample (risk chosen on full data — selection check) | 2015-19: 71.6%, 2020-24: 64.9% — stable, no cherry-pick flag |
| 30d supply ceiling for carry (~27%) | Survives — physical (zero trades = zero pass) |
| Frequency law (≥3/wk to make 30-90d viable) | Survives, **reframed**: frequency buys structural pass probability; only edge makes it worth passing |
| Funded-phase payouts (median ~$10k/yr @1%) | Survives as backtest-conditional; live n=4 caveat unchanged |

## MECHANICS CHECKED FOR LOOK-AHEAD (all clean)
Risk fixed at entry using entry-time balance; PnL applied at exit; only trades exiting inside the window count (conservative); EOD-only trailing floor per Lucid semantics; worst historical trade (-3.22R gap) included; no re-ordering, no survivorship within the CSV. Remaining known optimisms, unchanged and documented: intratrade drawdown invisible in the CSV; frictionless same-day rebuy; linear R scaling at 5% risk; and the CSV is itself a sealed backtest, not live trades.

## THE STANDING ORDER THIS AUDIT PRODUCES
No number derived from an assumed edge gets quoted as a probability again. The chain is:
**real NQ intraday data → backtest with costs → sealed CSV → paper sprint n≥80 → then P(pass) gets computed, with CI, and it will be graded against the 49.9%/84.7% zero-edge baselines this audit established.**
JJ-SIM-001 stays open — it is the instrument for exactly this. Its dashboard now measures against the right bar: not "did it pass" but "is live WR ≥52% at 1:1.5 over n≥80."
