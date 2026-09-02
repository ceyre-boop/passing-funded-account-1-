# Strategies

One folder, one metric set, so strategies are comparable rather than merely
described. Every entry is scored by `scripts/strategy_scorecard.py`, which
produces the same numbers in the same order for anything that emits
`(entry, stop, direction)` signals.

| id | name | status | gap | mean R | n |
|---|---|---|---|---|---|
| [S1](S1_impulse_fade.md) | impulse fade — RSI exhaustion at a lower high | **TESTED — NEGATIVE** | −0.76 | −0.392 | 634 |

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
