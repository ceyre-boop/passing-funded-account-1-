# 018 — STOCKFISH / ALPHAZERO CONTEXT DIRECTIVE CONTRACT `[SPEC]`

Promoted from `[PLAN]` 2026-08-06, paired with 011. The narrow bridge between
the constitution and 016. Not a shared strategy module; grants no mechanical
authority.

## What a directive is

A directive is **an expiring, scoped statement of meaning with a requested
authority level.** It is data. It cannot act. Everything it asks for is
translated into Stockfish's *existing* bounded vocabulary or refused with a
reason — there is no path by which a new mechanical capability enters the system
through this envelope.

## Envelope

```python
@dataclass(frozen=True)
class ContextDirective:
    schema_version: str          # "1"
    directive_id: str
    scope: Scope                 # market / sector / symbols / trade_id
    regime: dict[str, float]     # regime_vector dimension subset, advisory only
    thesis_state: str | None     # a thesis.ThesisState
    recommendation: str | None   # a stockfish_exit.INTENT name
    interrupt: str | None        # None | TIGHTEN | REDUCE_RISK | INVALIDATE | EMERGENCY
    confidence: float            # 0..1
    evidence_ids: list[str]
    issued_at: str               # ISO-8601, UTC, tz-aware — naive is REJECTED
    expires_at: str              # ISO-8601, UTC, tz-aware, strictly > issued_at
    model_version: str
    authority_level: int         # 0..4, see table
```

## Authority levels

Requested by the directive, **granted** by the receiver. `granted_level` is
configuration on the Stockfish side; a directive asking for more than the
receiver grants is refused, not clamped — silently downgrading an emergency to
a tighten is exactly the failure this table exists to prevent.

| level | may express | maps to |
|---|---|---|
| 0 | observation only | nothing mechanical |
| 1 | TIGHTEN | `urgent="tighten"` |
| 2 | + recommend a policy | policy candidate, still subject to 011 |
| 3 | + INVALIDATE | `urgent="tighten"`, or `"exit"` when the thesis channel is armed |
| 4 | + EMERGENCY | `urgent="exit"` |

Default `granted_level` is **1**. Everything above tightening is armed
deliberately, matching how `--broker off`, `--allow-urgency`, and the thesis
channel already work here.

## Interrupt precedence — deterministic, total

`EMERGENCY > INVALIDATE > REDUCE_RISK > TIGHTEN > None`

When several directives are in scope, the accepted set is reduced by taking the
**highest-precedence interrupt among accepted directives**. Ties break on
`issued_at`, then `directive_id` — a total order, so the result never depends on
input ordering. `REDUCE_RISK` maps to `"tighten"`: it is a request to carry less
risk, and tightening is the bounded expression of that. It does **not** get to
size positions.

## Scope matching

A directive is in scope for `(symbol, trade_id)` when **any** of:
- `trade_id` is set and equals the open trade's id;
- `symbol` ∈ `scope.symbols`;
- `symbol` ∈ `scope.sector` members (sector membership supplied by the caller);
- `scope.market` is non-empty and the caller declares the symbol index-linked.

An empty scope matches **nothing**. A directive that names no target is a
configuration error, not a broadcast — the failure mode of an accidental
market-wide directive is far worse than a dropped one.

## Rejection reasons — all auditable, all non-deleting

`STALE` · `NOT_YET_VALID` · `OUT_OF_SCOPE` · `OVER_AUTHORIZED` ·
`UNKNOWN_SCHEMA` · `MALFORMED` · `UNKNOWN_ENUM` · `SUPERSEDED`

Expired directives are **ignored, never deleted** — they stay in history so a
scorecard can grade what they said against what happened. `evaluate()` returns
an audit record for every directive it saw, accepted or not.

## Non-negotiable acceptance cases (from the `[PLAN]` card, now testable)

| requirement | how it is enforced |
|---|---|
| recommendation changes policy only through Stockfish validation | adapter returns a policy *candidate*; `policy_from_scenarios`/`policy_params` still validate, and 011 still governs the resulting actions |
| AlphaZero cannot supply order type, quantity, stop price, or fill claim | those fields do not exist on the envelope; a dict carrying them fails `MALFORMED` |
| emergency precedence deterministic and tested | total order above, property-tested under input shuffling |
| expired directives ignored without deleting them | `Rejection(STALE)` recorded in the audit list; the input list is never mutated |
| identical envelope + identical state → identical result | `evaluate()` is a pure function of `(directives, context, now)`; property-tested |

## Timestamp rules

Both timestamps must be ISO-8601 and **timezone-aware**. A naive timestamp is
`MALFORMED`, not assumed-UTC: an assumed timezone is how a directive quietly
lives an hour longer than intended. `expires_at` must be strictly after
`issued_at`. A directive whose `issued_at` is in the future is `NOT_YET_VALID`
rather than accepted early.

## Schema evolution

`schema_version` is checked exactly. Unknown versions are `UNKNOWN_SCHEMA` and
refused — never best-effort parsed. Adding a field is a new version; the adapter
keeps accepting the old one until a migration removes it.

Signing is **out of scope for v1** and recorded here as the reason: every
producer and consumer is in-process in this repository, so a signature would
authenticate nothing that the import boundary does not already. If a directive
ever crosses a process or network boundary, this decision must be revisited
before that happens — noted in the card so it is not silently inherited.

## Fixtures and tests

- one fixture per rejection reason;
- precedence property test under shuffled input;
- purity test: same inputs, 100 evaluations, identical output;
- scope matrix: symbol / sector / market / trade_id / empty;
- authority matrix: requested × granted.

Test command: `python3 daytrade/context_directive.py --self-test`

## Definition of done

Every rejection reason and every acceptance case above has a passing test, and
`evaluate()` is shown pure under shuffling. No production caller is wired in
this card — 016 does that.
