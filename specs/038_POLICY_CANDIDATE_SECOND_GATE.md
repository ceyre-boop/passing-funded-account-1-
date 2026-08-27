# 038 — THE SECOND POLICY GATE  `daytrade/runner.py:763`  `[SPEC]`  `[NOT A DEFECT]`

**Read this before "fixing" a promotion that appears to do nothing.**

---

## The fact

`daytrade/runner.py:763` hardcodes `policy_candidate=None` into `resolve_channels`,
unconditionally, regardless of any authority level:

```python
_, merged_urgency, _ = resolve_channels(
    policy=s.exit_policy, policy_candidate=None,
    urgents=(urgency, directive_urgency))
```

So AlphaZero's recommended policy cannot reach the exit engine **even if it
clears every other gate.**

## Why this is a card and not a bug fix

There are now TWO independent gates on that channel, and they were built at
different times for different reasons:

1. **Authority.** `context_directive.py:249` surfaces `policy_candidate` only at
   `granted_level >= 2`. Production runs at 1 (`runner._granted_level()`
   returns `(1, '')`; no registry file exists). Wired through
   `AuthorityRegistry` as of `d60ca9c`.
2. **This line.** Even at level 2+, `resolve_channels` is handed `None`.

Gate 1 is *earned* — a model raises it by clearing the promotion gate against a
graded record. Gate 2 is *unconditional*. Removing gate 2 is a deliberate
decision that has never been made, and it must not be made by accident while
debugging why a promotion "did not work."

## The trap this card exists to prevent

The day someone grants a real promotion, the system will do **nothing
different**. Every symptom will look like a broken promotion path: authority
says 2, the directive carries a recommendation, `evaluate()` returns a
`policy_candidate`, and the exit engine ignores it.

The obvious move at that moment — delete the `None`, ship it — silently removes
a safety layer during the exact operation that most warrants care, under time
pressure, by someone who has just concluded they are fixing a bug. That is how
boundaries die.

## What must happen instead

When a promotion is genuinely earned:

1. Read `specs/016` and `specs/017` and confirm the promotion was earned against
   a graded record, not granted by an edit.
2. Decide, explicitly and on the record, whether AlphaZero's policy
   recommendation should reach `resolve_channels` at all. `CLAUDE.md`'s cockpit
   boundary permits it — a policy *name* is not an order, a quantity, or a stop
   — but permitted is not the same as decided.
3. If yes: route `policy_candidate` from `Decision.policy_candidate`, never from
   the directive directly, so gate 1 still governs it. `policy_params` and the
   constitution remain downstream and unchanged — `Decision.policy_candidate`'s
   own comment already says "still subject to policy_params + constitution".
4. Add a test that fails if a level-1 model's recommendation reaches
   `resolve_channels`. Gate 1 must keep working after gate 2 is opened.
5. Log the decision through `decision_logger` — `CLAUDE.md` non-negotiable #3.

## Invariants to hold

- **I53** — a `granted_level < 2` model's recommendation never reaches
  `resolve_channels`, before or after this card is actioned.
- **I54** — `policy_candidate` reaching the engine comes from
  `Decision.policy_candidate` (already authority-filtered), never from
  `ContextDirective.recommendation` directly.
- **I55** — opening gate 2 is a logged decision, not an incidental edit.

## Status

`[NOT A DEFECT]`. The line is correct as it stands and should be left alone
until a real promotion exists. This card is defence against a future
misdiagnosis, not a work item — there is nothing to build today.

Found 2026-08-26 while wiring D3 (`Plans/THE_BIG_PLAN.md`). Related:
`specs/016`, `specs/017`, `specs/018`, `daytrade/context_directive.py`,
`daytrade/runner.py`.
