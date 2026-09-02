# Strategies

One folder, one metric set, so strategies are comparable rather than merely
described. Every entry is scored by `scripts/strategy_scorecard.py`, which
produces the same numbers in the same order for anything that emits
`(entry, stop, direction)` signals.

All scored on **identical risk** (1 ATR stop, 1R target, same costs), so the
comparison measures the entry condition rather than the sizing.

| id | name | status | gap | mean R | medMFE | n |
|---|---|---|---|---|---|---|
| S5 | relative strength vs benchmark | tested — negative, thin | −0.45 | −0.328 | **1.08** | 72 |
| [S1](S1_impulse_fade.md) | impulse fade — RSI exhaustion at a lower high | tested — negative | −0.76 | −0.392 | 0.76 | 634 |
| S2 | gap-and-go | **untestable here** | — | — | — | 0 |
| S3 | earnings / news breakout | **untestable here** | — | — | — | 0 |
| S4 | 52-week / ATH breakout | **untestable here** | — | — | — | 0 |
| S6 | short squeeze | **not tested — data absent** | — | — | — | — |
| S7 | HFT / micro momentum | **not tested — data absent** | — | — | — | — |

### Why three are "untestable" rather than "failed"

Their defining thresholds describe small-cap single-name behaviour that index
ETFs essentially never exhibit, measured across 2,677 sessions:

| filter | frequency on SPY/QQQ |
|---|---|
| gap ≥ +5% | 1 session (0.04%) |
| daily volume > 3× ADV20 | 4 sessions (0.15%) |
| consolidation ≥ 30 days | 11 SPY (0.4%), **0 QQQ** |

That is the same external-validity wall S1 hit. These scanners are built for
$50 single names with 20M floats; this repo holds two index ETFs.

## The metric set

`gap = payoff − (1−W)/W` is the headline: positive means the reward covers the
hit rate, negative means it does not and by how much.

`median MFE` is reported beside it because it is the ceiling on everything
else — no target above the median can be hit more than half the time, whatever
the exit does.

## House rules for adding one

- Thresholds come from the **indicator's** distribution, never from returns.
- Entry is a bar that has **already closed**. Never the extreme.
- Search is allowed; the variant count is carried as N_eff and the winner is
  validated on data the search never saw.
- Report the absolute numbers. If every arm is negative, the header says so.
