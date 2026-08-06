# 015 — ALPHAZERO EVIDENCE OBJECTS AND EVENT LIFECYCLE `[PLAN]`

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

