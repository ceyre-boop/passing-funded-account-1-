# 015 — ALPHAZERO EVIDENCE OBJECTS AND EVENT LIFECYCLE `[SPEC]` (promoted 2026-08-09, ratification pending)

Coverage: AlphaZero items 4–5. Depends on 006 and 009; feeds 016 and 017.

## Intended contract

Replace headline-shaped inputs with an `Evidence` object containing stable id,
type, scope (`market`, `sector`, `symbol`, or `trade`), symbols, source time,
first seen, freshness, novelty, severity, direction, source reliability, and a
duplicate group. Unknown or missing provenance is explicit, not zero-filled.

Model event state as `RUMOR → REPORTED → CONFIRMED → MARKET_REACTING → DIGESTED
→ STALE`, with event-type-specific decay and monotonic/idempotent transitions.
Duplicate articles may update provenance but cannot create new urgency without
new evidence.

## Planning decisions

- canonicalization and duplicate-group algorithm;
- source reliability versioning and human override audit;
- decay curves by event type;
- conflict representation when evidence points both ways;
- market/sector/symbol/trade aggregation precedence.

## DoD seed

Fixtures prove duplicate headlines, rumor/confirmation, stale recaps, scoped
NVDA news, and market-wide SPY news produce distinct outputs. A replay at the
same `as_of` time is byte-stable.


---

## `[SPEC]` promotion — 2026-08-09, on Colin's direct instruction. Architect
## ratification post-hoc.

### Planning decisions, answered

1. **Canonicalization / duplicate group:** lowercase, strip punctuation,
   collapse whitespace; `dup_group = sha256(canonical)[:16]`. Same group =
   same story.
2. **Source reliability:** supplied per source from a VERSIONED table
   (`table_version` recorded on every evidence). An unrated source yields
   reliability `None` + `unrated` — explicit, never a defaulted 0.5. A human
   override is an explicit constructor argument recorded as
   `reliability_overridden_by`.
3. **Decay:** per-type TTL table (minutes) drives `is_fresh(now)` with the
   house INCLUSIVE boundary. State decay (MARKET_REACTING → DIGESTED → STALE)
   is explicit via `advance_state`, monotonic forward-only, idempotent on the
   same state; time alone never silently mutates a stored state.
4. **Conflict:** a duplicate group whose members point opposite directions is
   `conflicted=True` on the group view — surfaced, never averaged to neutral.
5. **Aggregation precedence:** trade > symbol > sector > market (mirrors 018's
   `in_scope`); `GroupView.most_specific_scope` reports it.

### The urgency rule (the card's core)

`urgency` is derived from state + severity + novelty. A DUPLICATE arrival
updates provenance (last_seen, count, reliability sources) and has novelty 0 —
so adding a duplicate can NEVER raise a group's urgency. Only new evidence
(new group, or a state advance) can. Named test + fault row.

### DoD

Duplicate headlines, rumor→confirmation, stale recap, NVDA-scoped vs SPY
market-wide distinct outputs, byte-stable replay at the same `as_of`, and the
never-raises-urgency duplicate rule — each named and fault-injected
(`mutation_check_015_016.py` → `specs/015_016_MUTATION_LOG.md`).

### Implementation notes (adversarial review, 2026-08-09 — finding 8)

- `novelty` is implemented as BEHAVIOR (a duplicate cannot raise urgency; only
  the lead member's severity counts), not as a carried numeric field. If a
  future consumer needs the number, add it then — do not infer it exists.
- Consequence, deliberate but worth knowing: a group's urgency is anchored to
  its FIRST member's severity. A low-severity rumor later CONFIRMED at high
  severity keeps the low anchor; representing upgraded severity needs an
  explicit severity-revision path (candidate for the next AZ card, architect
  call — an implicit upgrade would reopen the exact recap-inflation hole this
  card closes).
- `canonical_group` dedups normalized-verbatim text: literal reprints are one
  fact; differently-worded tellings of one story are separate groups until a
  semantic grouper (with its own scorecard) earns its way in.
