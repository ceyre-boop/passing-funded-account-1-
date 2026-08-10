# 014 — SHADOW POLICIES, REGRET, AND PORTFOLIO GUARDS `[SPEC]` (promoted 2026-08-09, ratification pending)

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


---

## `[SPEC]` promotion — 2026-08-09, on Colin's direct instruction. Architect
## ratification post-hoc. Shadow + regret build at Gate 4; portfolio guards at
## Gate 7 (same card, deliberately last, per the gate map in 020).

### Planning decisions, answered

1. **Shared input snapshots:** a shadow run consumes the SAME recorded cycle
   stream the authoritative path saw — (price, now_et) pairs from the session
   log / 012 FACTS_OBSERVED events — plus the resolved plan. Each policy folds
   the stream strictly forward on its own state copy; the prefix property
   (results over `cycles[:k]` equal the first k of results over `cycles`) is a
   named test, which is what "no future data at decision time" means in code.
2. **Policy version identity:** every shadow result records the policy name AND
   its resolved mechanical params (trail_mult / be_arm_frac / hold_past_tp2),
   so a renamed or retuned preset cannot silently claim an old result.
3. **Counterfactual fill assumption:** fills at the recorded cycle price — the
   same convention the authoritative runner itself experienced. No intrabar
   modeling, no favorable assumptions; documented, single, testable.
4. **Type-level execution containment:** shadow output is `ShadowAction`, a
   distinct type whose serialized form carries `"shadow": true` and
   `hypothetical_kind` — it has NO `kind` field, and *accessing* `.kind` raises
   `ShadowContainment`. Every authoritative consumer (apply_action, the
   constitution, broker intent translation) reads `.kind` first, so a shadow
   action physically cannot travel the execution path, and a log line cannot be
   mistaken for an authoritative action. Invalid states unrepresentable, not
   discouraged.
5. **Realized / hypothetical / rejected schema:** authoritative actions keep
   `kind`; shadow actions carry `hypothetical_kind` + `policy_name`; ledger
   rejections (013) carry their own Rejection type. Three shapes, no flag
   fields to forget.

### Regret (grading, never hindsight selection)

`grade()` runs only on a CLOSED trade (open trades raise) and produces per
trade: realized R, MFE R, MAE R, captured fraction (realized/MFE), giveback R
(MFE − realized), hold time, slippage (0.0 and labeled `paper` until 013 wires
live fills), max drawdown R, exit efficiency vs the best shadow counterfactual
— and a per-policy delta table. The report has NO winner field; a grader that
crowns the hindsight winner becomes a strategy selector, which is 017's job
under promotion discipline, not a grader's.

### DoD

Shadow determinism; prefix property; policy divergence non-vacuity (DEFEND and
RIDE genuinely differ on a fixture path); containment (`.kind` access raises,
apply_action refuses, serialized form unmistakable); regret metrics against a
hand-computed fixture; no-winner schema; open-trade refusal. Fault rows in
`mutation_check_014.py` → `specs/014_MUTATION_LOG.md`.
