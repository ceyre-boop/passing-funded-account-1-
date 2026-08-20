# 033 — SF-FROZEN-001 + THE SELF-EXCLUSION INVARIANT `[SPEC]`

**Component:** `daytrade/frozen_policy.py`, `daytrade/measure.py`,
`data/daytrade/SF_FROZEN_001.json`
**Status:** built 2026-08-20. Both items are prerequisites named by the stack
exam, not new capability.

## SF-FROZEN-001 — the pinned opponent

Stack exam **LL-2** recorded the gap: mechanisms were measured against "the
shipped policy", stable but not pinned, so nothing prevented the comparison
target from drifting under a future edit. Beating yesterday's self is drift;
beating a frozen opponent is progress.

The checkpoint carries the exact policy parameters **and a content hash of the
engine that executes them**. An edit to `stockfish_exit.py` now breaks
verification loudly rather than silently redefining what everything was
measured against. `frozen_policy.policy(cls)` is the single answer to "better
than what", and it REFUSES to hand out a target that has drifted.

It becomes load-bearing the moment a carry evaluator exists beside the
intraday one: "the shipped policy" is then ambiguous — shipped which — and a
pinned id is not. It also refuses a class it does not price:
`policy("FX_CARRY")` raises, quoting SF-3.

## The self-exclusion invariant

Four instances, one shape — an instrument including its own artifacts in what
it measures:

| # | instrument | the error |
|---|---|---|
| M9 | four-book fingerprint | compared a value with itself |
| M13 | mutation harness | "killed" a test that did not exist |
| M26 | mutation harness | the same, again |
| SF-1 | stack exam | counted its own source as a second `decide_exit` |

Left as a habit, the fifth instance looks different enough to slip through.
So it is a check: **no measurement process may include its own artifacts in
its input set**, and the exclusion is **asserted rather than assumed** —
`exclude_self` raises when the caller was not present to remove, because a
filter that silently removes nothing looks like protection and provides none.
`assert_disjoint` catches the higher-level version: a report that ingests its
own previous output.

Noted, not yet closed: the ontology audit is arguably the same error one
level up — a vocabulary auditing itself for completeness against the market
it was written from. The FX vocabulary starting empty (032 I45) is the
structural answer to that, and it is why no FX label is defined in code.

## Invariants

- I47: an edit to the engine or the pinned params fails `verify` and the suite.
- I48: `policy()` refuses to return a drifted comparison target.
- I49: re-pinning is refused; a new opponent needs a new checkpoint id.
- I50: the checkpoint refuses a position type it does not price.
- I51: `exclude_self` raises when the exclusion was a no-op.
- I52: an instrument may not read what it writes.

M46–M51 all killed (`033_MUTATION_LOG.md`).
