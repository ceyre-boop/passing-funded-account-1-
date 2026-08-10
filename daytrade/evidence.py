#!/usr/bin/env python3
"""EVIDENCE OBJECTS + EVENT LIFECYCLE — spec 015.

Replaces headline-shaped inputs with a structured `Evidence` object and models
the life of a market event: RUMOR → REPORTED → CONFIRMED → MARKET_REACTING →
DIGESTED → STALE, forward-only, idempotent on the same state. Time alone never
silently mutates a stored state — decay is an explicit `advance_state` call.

THE CORE RULE: a duplicate article updates provenance (last seen, count,
sources) but has novelty 0, so it can NEVER raise a group's urgency. Only new
evidence — a new group, or a state advance — can. Ten recaps of the same story
are one fact, not ten.

Unknown or missing provenance is EXPLICIT: an unrated source has reliability
None and is labeled `unrated`, never a defaulted 0.5. Abstention-adjacent
honesty, same doctrine as the regime vector's `unavailable`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

STATES: tuple[str, ...] = ("RUMOR", "REPORTED", "CONFIRMED", "MARKET_REACTING",
                           "DIGESTED", "STALE")
_STATE_ORDER = {s: i for i, s in enumerate(STATES)}

EVIDENCE_TYPES: frozenset[str] = frozenset({
    "EARNINGS", "GUIDANCE", "PRODUCT", "REGULATORY", "MACRO", "ANALYST",
    "RUMOR_SOCIAL", "MARKET_STRUCTURE"})

SCOPES: tuple[str, ...] = ("market", "sector", "symbol", "trade")
_SCOPE_SPECIFICITY = {s: i for i, s in enumerate(SCOPES)}   # trade most specific

DIRECTIONS = ("bullish", "bearish", "mixed", "unknown")

# Freshness TTL minutes by type — how long this KIND of fact stays actionable.
TTL_MIN: dict[str, int] = {
    "EARNINGS": 24 * 60, "GUIDANCE": 24 * 60, "PRODUCT": 8 * 60,
    "REGULATORY": 24 * 60, "MACRO": 4 * 60, "ANALYST": 6 * 60,
    "RUMOR_SOCIAL": 60, "MARKET_STRUCTURE": 2 * 60,
}

# How much urgency each lifecycle state carries. DIGESTED and STALE carry none:
# a digested story steers nothing, no matter how severe it was.
_STATE_URGENCY = {"RUMOR": 0.2, "REPORTED": 0.6, "CONFIRMED": 1.0,
                  "MARKET_REACTING": 1.0, "DIGESTED": 0.0, "STALE": 0.0}


class EvidenceError(RuntimeError):
    """Malformed evidence or an impossible lifecycle move. Never repaired."""


def canonical_group(text: str) -> str:
    """Same story -> same group: lowercase, strip punctuation, collapse space."""
    norm = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()
    if not norm:
        raise EvidenceError("cannot form a duplicate group from empty text")
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def _aware(ts: str, field_: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        raise EvidenceError(f"{field_}={ts!r} is not ISO-8601") from e
    if dt.tzinfo is None:
        raise EvidenceError(f"{field_}={ts!r} is timezone-naive — refusing to guess")
    return dt


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    evidence_type: str
    scope: str                       # market | sector | symbol | trade
    symbols: tuple
    headline: str                    # canonicalized for grouping, kept for audit
    source: str
    source_time: str                 # ISO tz-aware
    first_seen: str                  # ISO tz-aware
    severity: float                  # 0..1
    direction: str
    reliability: Optional[float]     # None == unrated, EXPLICITLY
    reliability_basis: str           # 'table:<version>' | 'unrated' | 'override'
    reliability_overridden_by: Optional[str] = None
    dup_group: str = ""

    def __post_init__(self):
        if not self.evidence_id:
            raise EvidenceError("evidence_id is required")
        if self.evidence_type not in EVIDENCE_TYPES:
            raise EvidenceError(f"unknown evidence_type {self.evidence_type!r}")
        if self.scope not in SCOPES:
            raise EvidenceError(f"unknown scope {self.scope!r}; known: {SCOPES}")
        if self.direction not in DIRECTIONS:
            raise EvidenceError(f"unknown direction {self.direction!r}")
        if not (0.0 <= self.severity <= 1.0):
            raise EvidenceError(f"severity {self.severity} outside [0, 1]")
        if self.reliability is not None and not (0.0 <= self.reliability <= 1.0):
            raise EvidenceError(f"reliability {self.reliability} outside [0, 1]")
        if self.reliability is None and self.reliability_basis != "unrated":
            raise EvidenceError("a None reliability must say 'unrated' — silence "
                                "and measurement are different facts")
        if self.scope in ("symbol", "trade") and not self.symbols:
            raise EvidenceError(f"scope {self.scope!r} names no symbols")
        for f_ in ("source_time", "first_seen"):
            _aware(getattr(self, f_), f_)
        if not self.dup_group:
            object.__setattr__(self, "dup_group", canonical_group(self.headline))

    def to_dict(self) -> dict:
        return asdict(self)


def reliability_from(table: dict, table_version: str, source: str
                     ) -> tuple[Optional[float], str]:
    """Versioned lookup. Unknown source -> (None, 'unrated'); never 0.5."""
    if source in table:
        return float(table[source]), f"table:{table_version}"
    return None, "unrated"


# --------------------------------------------------------------------- store

@dataclass
class _Group:
    state: str
    members: list                    # Evidence, arrival order
    first_seen: str
    last_seen: str


@dataclass(frozen=True)
class GroupView:
    dup_group: str
    state: str
    count: int
    fresh: bool
    conflicted: bool
    urgency: float
    most_specific_scope: str
    direction: str                   # 'mixed' when conflicted
    symbols: tuple


class EvidenceStore:
    """Groups evidence by story, walks each story's lifecycle forward-only."""

    def __init__(self):
        self._groups: dict[str, _Group] = {}

    def add(self, ev: Evidence) -> str:
        g = self._groups.get(ev.dup_group)
        if g is None:
            self._groups[ev.dup_group] = _Group(
                state="RUMOR" if ev.evidence_type == "RUMOR_SOCIAL" else "REPORTED",
                members=[ev], first_seen=ev.first_seen, last_seen=ev.first_seen)
        else:
            g.members.append(ev)                       # provenance only
            g.last_seen = ev.first_seen
        return ev.dup_group

    def advance_state(self, dup_group: str, new_state: str) -> str:
        g = self._groups.get(dup_group)
        if g is None:
            raise EvidenceError(f"unknown group {dup_group!r}")
        if new_state not in _STATE_ORDER:
            raise EvidenceError(f"unknown state {new_state!r}; known: {STATES}")
        if _STATE_ORDER[new_state] < _STATE_ORDER[g.state]:
            raise EvidenceError(
                f"{dup_group}: {g.state} -> {new_state} runs the lifecycle "
                "backwards — a digested story does not become a rumor again")
        g.state = new_state                            # idempotent on equal
        return g.state

    def view(self, dup_group: str, *, now: datetime) -> GroupView:
        g = self._groups.get(dup_group)
        if g is None:
            raise EvidenceError(f"unknown group {dup_group!r}")
        lead = g.members[0]
        age_min = (now - _aware(g.first_seen, "first_seen")).total_seconds() / 60.0
        fresh = age_min <= TTL_MIN[lead.evidence_type]          # INCLUSIVE boundary
        directions = {m.direction for m in g.members} - {"unknown"}
        conflicted = "bullish" in directions and "bearish" in directions
        # novelty: only the FIRST member of a group is novel. Duplicates carry
        # zero novelty, so urgency is computed from the lead alone — adding a
        # recap can never raise it.
        urgency = (_STATE_URGENCY[g.state] * lead.severity) if fresh else 0.0
        scope = max((m.scope for m in g.members),
                    key=lambda s: _SCOPE_SPECIFICITY[s])
        return GroupView(
            dup_group=dup_group, state=g.state, count=len(g.members),
            fresh=fresh, conflicted=conflicted, urgency=urgency,
            most_specific_scope=scope,
            direction="mixed" if conflicted else lead.direction,
            symbols=tuple(sorted({s for m in g.members for s in m.symbols})))

    def relevant_to(self, symbol: str, *, now: datetime,
                    index_linked: bool = False) -> list:
        """Scoped delivery, mirroring 018's rule: a symbol only hears stories
        that name it — or market-wide stories only if it is index-linked. An
        NVDA story is not an unrelated symbol's problem, and a SPY story is not
        automatically anyone's emergency."""
        out = []
        for gid, g in sorted(self._groups.items()):
            v = self.view(gid, now=now)
            if symbol in v.symbols:
                out.append(v)
            elif v.most_specific_scope == "market" and index_linked:
                out.append(v)
        return out


if __name__ == "__main__":
    print(__doc__)
    print(f"lifecycle: {' -> '.join(STATES)}")
    print("run the suite: pytest daytrade/test_evidence.py -v")
