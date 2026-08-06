# 014 — SHADOW POLICIES, REGRET, AND PORTFOLIO GUARDS `[PLAN]`

Coverage: long-term vision Stockfish items 8–10. Depends on 011–013 and 008.

## Three separate products

1. **Shadow policies** receive the same immutable facts and produce hypothetical
   actions; only the selected policy can produce an execution intent.
2. **Regret** grades completed trades against named counterfactuals without
   selecting the hindsight winner.
3. **Portfolio guards** enforce limits supplied by the upstream risk layer;
   they do not discover correlations or invent exposure judgments.

The planning pass must define shared input snapshots, policy version identity,
counterfactual fill assumptions, and a schema that distinguishes realized,
hypothetical, and rejected actions.

## Minimum measures

Capture realized return, MFE captured, MAE after entry, giveback, hold time,
slippage, drawdown, and exit efficiency. Portfolio limits should include total
open risk, per-symbol exposure, correlated exposure supplied by upstream,
unprotected count, daily loss lock, and emergency flatten.

## DoD seed

Shadow output cannot reach the broker; it is impossible to confuse it with the
authoritative action in logs. Counterfactual results are reproducible from the
same event stream and use no future data at decision time.

