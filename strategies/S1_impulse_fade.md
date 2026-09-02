# S1 — Impulse fade (RSI exhaustion at a lower high)

**Status: TESTED — NEGATIVE at every configuration.**
Origin: narrated from a live GTLB trade, 2026-09-02.
Code: `alta/simple.py::signals` · Score: `python3 scripts/strategy_scorecard.py`

---

## The rule, complete and replicable

```
IMPULSE      |close[i] − close[i−8]|  ≥  2.0 × ATR14[i]
EXHAUSTION   RSI(14) ≥ 75 on an up-impulse   (≤ 25 on a down-impulse)
STRUCTURE    the post-impulse swing must NOT exceed the impulse extreme
             — i.e. a LOWER high (or higher low)
ENTRY        first bar that closes against the impulse
             → ENTER AT THAT CLOSE.  Never at the extreme.
STOP         the swing extreme, floored at 1.0 × ATR14.  This defines 1R.
DIRECTION    fade the impulse
```

**Where 75 and 25 come from:** the p90 and p10 of RSI measured at 73,255 impulse
moments across SPY and QQQ. They are facts about the RSI distribution, chosen
before any outcome was examined. Had they been picked because they paid best,
this would be a search reported as a rule.

**The hazard this refuses:** the narrated intent was "get as high as possible."
An entry *at* the extreme backtests beautifully and is untradeable — it picks
the top with hindsight. Entry is always a close that had already printed.
`alta/test_setup.py` re-runs detection with all future bars removed and requires
every field to be unchanged.

---

## Scorecard

634 signals · SPY + QQQ · 2016-01-04 → 2026-08-26

| metric | value |
|---|---|
| win rate | 42.0% |
| payoff | 0.62 |
| breakeven payoff needed | 1.38 |
| **gap** | **−0.76** |
| mean R | −0.392 |
| profit factor | 0.449 |
| median cost | 0.212 R |
| median MFE | 0.76 R |
| reaches 1R / 2R / 3R | 41.5% / 22.2% / 14.0% |

## Reward multiple — every target loses

| R:R | win | needed | gap | mean R |
|---|---|---|---|---|
| 1:1 | 37.5% | 50.0% | −12.5% | −0.764 |
| 2:1 | 26.8% | 33.3% | −6.5% | −0.758 |
| 3:1 | 20.5% | 25.0% | −4.5% | −0.796 |
| 4:1 | 17.0% | 20.0% | −3.0% | −0.834 |
| 5:1 | 14.7% | 16.7% | −2.0% | −0.882 |

*(tight-stop variant; costs were 0.393R before the 1 ATR floor was added)*

## Exit mechanism — and the finding that matters

| exit | win% | payoff | needed | gap | mean R |
|---|---|---|---|---|---|
| half at 1R, trail | 40.4% | 0.54 | 1.48 | −0.94 | −0.452 |
| fixed target 1R | 42.0% | 0.62 | 1.38 | −0.76 | −0.392 |
| fixed target 2R | 24.9% | 1.35 | 3.02 | −1.67 | −0.507 |
| 1R → BE, trail 1R | 9.5% | **3.36** | 9.53 | −6.17 | −0.512 |

**The exchange rate is the result.** Payoff rises 0.54 → 3.36 (6.2×); the win
rate required to support it rises 1.48 → 9.53 (6.4×). Payoff is bought with win
rate at slightly worse than fair, so every step toward a bigger reward *widens*
the shortfall. Payoff and win rate are one knob seen from two ends: where the
target sits decides **how** you lose, not **whether**.

## Two levers, both closed

**Filtering entries — closed.** MFE is fat-tailed (median 0.73R, p90 3.47R), so
the big moves genuinely exist. If they were identifiable at entry, filtering
would raise the payoff. Every entry-time feature, scanned on train only:

```
rsi_entry .058 · atr_frac .065 · risk_atr .065 · bar_of_day .072
rng_atr .081 · vol_ratio .093 · dist_to_stop .082
```

Strongest 0.0928 against a detection floor of 0.128 at n=377 — **below noise.**
Nothing known at entry predicts how far it goes, so the tail cannot be selected.

**Direction — a real asymmetry, not a rescue.** Fading down-impulses (long)
−0.282 vs fading up-impulses (short) −0.473. Longs are consistently less bad.
Both negative.

---

## Why it fails, in one line

The median move after the signal is **0.76 R**. To win more than half, the
target must sit below that — which pays less than 1×. To be paid 2×, the target
is 2R, reached 22.2% of the time. **Both halves of "wins more often than not,
AND pays 2–4×" cannot be satisfied at once.**

## Limitation, load-bearing

**SPY and QQQ only.** They are the sole instruments in this repo with real
history; everything else has 60–74 days. A single-name post-earnings gap — the
instrument this method was built for, where GTLB moved 22% and left a gap
spanning five ATRs — is a materially different distribution. This tests the
*structure* on the only data available, not the *method* on its intended
instrument.

## What would change the verdict

1. **Single-name intraday history.** Same machinery, ~10 minutes, different
   distribution. This is the real open question.
2. **Any entry-time feature that predicts MFE above its floor.** None of seven
   did here.
3. Nothing else. Retuning the target, the stop, or the exit moves along the
   exchange-rate curve and cannot cross zero.
