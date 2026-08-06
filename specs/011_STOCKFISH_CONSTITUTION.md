# 011 — STOCKFISH TRADE CONSTITUTION `[SPEC]`

Promoted from `[PLAN]` 2026-08-06, paired with 018 per the handoff's starting
sequence. Coverage: long-term vision Stockfish item 4. Depends on 003.

**This card adds no behavior.** It adds a layer that can only ever REFUSE.

## The design question this card had to settle

> "The planning pass must decide whether validation happens before `decide_exit`,
> inside `apply_action`, or both; the answer must not create a second decision
> engine."

**Answer: inside `apply_action`, and nowhere else.**

The reasoning that makes this safe: validation is a **predicate, not a decision**.
`validate_action` returns violations or nothing — it never selects an action,
never proposes an alternative, and never mutates. A component that can only say
"no" is not a second decision engine, because every action it sees was already
chosen by `decide_exit`.

`apply_action` is already the single funnel through which state changes (the
replay, the runner, the harness and the ceiling all route through it — that is
why the DoD diff works at all). Putting the invariants there means:

- one place to enforce, one place to test;
- no path can bypass it without also bypassing the thing that makes logs
  diffable, so a bypass is loud rather than silent;
- a pre-`decide_exit` check would duplicate the enforcement and create exactly
  the second engine the card forbids.

`validate_state` runs at `TradeState.__post_init__`, which already performs
field validation today — it is extended, not duplicated.

## Interface

```python
@dataclass(frozen=True)
class ConstitutionViolation:
    rule_id: str          # "C001" — stable forever, referenced in logs and tests
    rule: str             # short human name
    detail: str           # observed values, direction-specific
    state_revision: int   # which revision was being mutated when it fired
    action_kind: str | None

class ConstitutionError(RuntimeError):
    violations: list[ConstitutionViolation]

def validate_state(state) -> list[ConstitutionViolation]
def validate_action(state, action, *, applied_keys: frozenset[str]) -> list[ConstitutionViolation]
def enforce(state, action=None, *, applied_keys=frozenset()) -> None   # raises
```

## Invariant table

| id | rule | long | short | fires when |
|---|---|---|---|---|
| C001 | no quantity increase | qty may only decrease | same | `action` would raise `state.qty` |
| C002 | no over-close | cumulative reduction ≤ 1.0 | same | `TAKE_PARTIAL` fraction pushes cumulative past 1.0, or qty would go negative |
| C003 | no stop loosening | `sl` may only rise | `sl` may only fall | `MOVE_SL` moves the stop away from price |
| C004 | no stale facts | `now_et` may not go backwards | same | supplied clock precedes the last one seen |
| C005 | no duplicate reduction | one reduction per idempotency key | same | the same `(kind, fraction, revision)` key was already applied |
| C006 | bounded fractions | fraction ∈ (0, 1] | same | outside that range, or NaN |
| C007 | emergency precedence | `EXIT_ALL` cannot be preceded in the same batch by a non-emergency action | same | a batch orders another action after an `EXIT_ALL` |
| C008 | closed is terminal | no action after `CLOSED` | same | any action while `stage is CLOSED` |
| C009 | reproducibility | state carries a monotonic `state_revision` | same | revision fails to advance on a mutating action |

Direction is handled by evaluating C003 against `state.direction`: for a long,
"loosening" is `new_sl < sl`; for a short, `new_sl > sl`. The existing
`apply_action` guard already implements exactly this and becomes C003.

## `state_revision` and "duplicate reduction"

`state_revision` starts at 0 and increments on every **mutating** action
(`MOVE_SL`, `TAKE_PARTIAL`, `EXIT_ALL`). `HOLD` does not advance it — nothing
changed, and advancing on a no-op would make the counter a call count rather
than a state identity.

**A reduction is a duplicate when its idempotency key has already been applied.**
The key is `f"{kind}:{fraction!r}:{state_revision}"` — the revision the reduction
was computed *against*. This is the correct definition after a partial fill:
re-sending the same reduction computed against the same state is a duplicate
(the classic retry-after-timeout case), while a genuinely new reduction is
necessarily computed against a later revision, because the first one advanced it.

The caller owns the applied-key set, because only the caller knows whether a
crash happened between "broker accepted" and "state persisted". `applied_keys`
is passed in rather than stored on the state so replay can reconstruct it
exactly.

## Error shape and log policy

```json
{"error": "ConstitutionViolation", "state_revision": 7, "action_kind": "MOVE_SL",
 "violations": [{"rule_id": "C003", "rule": "no stop loosening",
                 "detail": "long: refusing sl 205.00 -> 204.60"}]}
```

Violations **raise**. They are never downgraded to a warning and never dropped:
a constitution that can be ignored is a style guide. The runner catches
`ConstitutionError`, logs the JSON above into the session record, and abandons
the cycle without advancing the ladder — the same shape as a refused quote.

## Migration

`state_revision` is additive. Existing session JSONL has no such field; the
harness treats a missing revision as 0 and does not fail. The v3 replay log
gains **no** new lines — `state_revision` is state, not an emitted action — so
the byte-identical DoD holds unchanged.

## Fixtures and tests

- property: for random valid paths, no violation ever fires (2,000+ paths, both
  directions, every policy) — the constitution must not fire on legal play;
- one targeted fixture per rule id that *must* raise;
- permutation: for action batches, every ordering that violates C007 raises and
  every legal ordering does not;
- replay: `data/daytrade/replay_expected/v3.log` byte-identical.

Test command: `python3 daytrade/stockfish_constitution.py --self-test`

## Definition of done

1. Every rule id has a fixture that fires it and a property test that shows it
   does not fire on legal play.
2. v3 replay byte-identical.
3. DoD diff (runner vs harness) still empty.
4. Ceiling R values unchanged.

## Explicit non-goals

No new policy, stop formula, broker call, or AlphaZero authority. This layer
cannot choose anything.
