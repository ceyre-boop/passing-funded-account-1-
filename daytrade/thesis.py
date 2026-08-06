#!/usr/bin/env python3
"""TRADE-THESIS MONITOR — why does this position exist, and does that still hold?

A stop tells you where you are wrong about PRICE. It says nothing about being
wrong about the REASON. A trade taken because "NVDA held while AMD broke" is
already over when NVDA stops holding — whether or not price has reached the stop
yet. This module stores the reason, watches reality against it, and emits a
state:

    THESIS_PENDING      taken, the expected confirmation has not appeared yet
    THESIS_CONFIRMED    the thing the trade was betting on actually happened
    THESIS_WEAKENING    supporting conditions are eroding
    THESIS_INVALIDATED  the reason the trade exists is gone          (terminal)
    THESIS_EXPIRED      the thesis had a shelf life and it ran out   (terminal)

IT DOES NOT TOUCH THE STOP. That is the whole discipline (ruling 1): this file
produces meaning, and `stockfish_exit.urgency_for_thesis` translates a state
into the engine's EXISTING bounded vocabulary — None / 'tighten' / 'exit'. No
new mechanical authority is created anywhere, which is what keeps the exit
engine small enough to hold in your head and keeps every path through it already
covered by the parity tests.

TERMINAL STATES ARE STICKY. A dead thesis stays dead: INVALIDATED and EXPIRED
never revert, the same forward-only guarantee the position lifecycle has. A
thesis that can quietly come back to life is one you will end up arguing with at
10:47 with money on the line, which is exactly the argument this system exists
to have already settled.

CONDITIONS ARE NAMED AND CHECKABLE. Not "sentiment sours" — "AMD trades down
another 3%". A condition nobody can mark true or false cannot move the state
machine and is refused at construction.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal, Optional

ThesisState = Literal[
    "THESIS_PENDING", "THESIS_CONFIRMED", "THESIS_WEAKENING",
    "THESIS_INVALIDATED", "THESIS_EXPIRED",
]
TERMINAL: frozenset[str] = frozenset({"THESIS_INVALIDATED", "THESIS_EXPIRED"})


class ThesisError(RuntimeError):
    """Malformed thesis or an illegal transition. Never silently repaired."""


@dataclass(frozen=True)
class Condition:
    """One named, checkable fact. `key` is what an observer marks true."""
    key: str
    description: str

    def __post_init__(self):
        if not self.key or " " in self.key:
            raise ThesisError(f"condition key {self.key!r} must be a single token — it is "
                              "what an observer marks true, so it has to be unambiguous")
        if len(self.description) < 8:
            raise ThesisError(
                f"{self.key}: description too vague to check. Write the observable fact "
                "('AMD trades down another 3%'), not a mood ('sentiment sours').")


@dataclass
class Thesis:
    """Why a position exists. Written at entry, before it can be rationalised."""
    symbol: str
    opened_ts: str
    statement: str                          # one sentence: why this trade exists
    confirmations: list[Condition]          # ALL must fire to be CONFIRMED
    weakeners: list[Condition]              # ANY firing means WEAKENING
    invalidators: list[Condition]           # ANY firing means INVALIDATED (terminal)
    expires_at_et: Optional[str] = None     # 'HH:MM' — a shelf life, if it has one
    state: str = "THESIS_PENDING"
    history: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if len(self.statement) < 12:
            raise ThesisError("statement too short — say why the trade exists in a sentence")
        if not self.invalidators:
            raise ThesisError(
                "a thesis with no invalidators cannot be wrong, so it cannot be scored "
                "and it cannot protect you. Name what would kill it.")
        if not self.confirmations:
            raise ThesisError(
                "a thesis with no confirmations can never leave PENDING. Name what would "
                "prove it right.")
        keys = [c.key for group in (self.confirmations, self.weakeners, self.invalidators)
                for c in group]
        if len(keys) != len(set(keys)):
            raise ThesisError(f"duplicate condition keys across groups: {keys}")
        if self.expires_at_et:
            from stockfish_exit import _et_minutes
            _et_minutes(self.expires_at_et, "expires_at_et")

    # -- the state machine --------------------------------------------------
    def evaluate(self, observed: set[str], *, now_et: Optional[str] = None,
                 note: str = "") -> str:
        """Fold observations into a state. Deterministic and order-independent:
        the same observation set always yields the same state, which is what
        makes this repeatable across sessions and replayable in a harness.

        PRECEDENCE — invalidation first, because it is the most informative and
        the most protective. A thesis that is both expired and invalidated is
        invalidated: 'the reason died' tells you more than 'the clock ran out'.
        """
        if self.state in TERMINAL:
            return self.state                       # sticky. a dead thesis stays dead.

        unknown = observed - {c.key for g in (self.confirmations, self.weakeners,
                                              self.invalidators) for c in g}
        if unknown:
            raise ThesisError(
                f"observed unknown condition(s) {sorted(unknown)} — every observation must "
                "name a condition this thesis actually declared, or the state machine is "
                "reacting to something nobody wrote down.")

        fired_inv = [c.key for c in self.invalidators if c.key in observed]
        fired_weak = [c.key for c in self.weakeners if c.key in observed]
        got_all = all(c.key in observed for c in self.confirmations)

        if fired_inv:
            new, why = "THESIS_INVALIDATED", f"invalidator(s) fired: {', '.join(fired_inv)}"
        elif self._expired(now_et):
            new, why = "THESIS_EXPIRED", f"past {self.expires_at_et} ET"
        elif fired_weak:
            new, why = "THESIS_WEAKENING", f"weakener(s) fired: {', '.join(fired_weak)}"
        elif got_all:
            new, why = "THESIS_CONFIRMED", "all confirmations observed"
        else:
            missing = [c.key for c in self.confirmations if c.key not in observed]
            new, why = "THESIS_PENDING", f"awaiting: {', '.join(missing)}"

        if new != self.state:
            self.history.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "from": self.state, "to": new, "why": why, "note": note,
                "observed": sorted(observed),
            })
            self.state = new
        return self.state

    def _expired(self, now_et: Optional[str]) -> bool:
        if not (self.expires_at_et and now_et):
            return False                            # no clock given == rule not armed
        from stockfish_exit import _et_minutes
        return _et_minutes(now_et, "now_et") >= _et_minutes(self.expires_at_et, "expires_at_et")

    # -- persistence --------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("confirmations", "weakeners", "invalidators"):
            d[k] = [asdict(c) if not isinstance(c, dict) else c for c in getattr(self, k)]
        return d

    @staticmethod
    def from_dict(d: dict) -> "Thesis":
        kw = dict(d)
        for k in ("confirmations", "weakeners", "invalidators"):
            kw[k] = [Condition(**c) for c in kw.get(k, [])]
        return Thesis(**kw)

    def describe(self) -> str:
        out = [f"  {self.symbol}  {self.state}",
               f"    \"{self.statement}\"",
               f"    opened {self.opened_ts[:16]}"
               + (f"   expires {self.expires_at_et} ET" if self.expires_at_et else "")]
        for label, group in (("confirms", self.confirmations), ("weakens", self.weakeners),
                             ("kills", self.invalidators)):
            for c in group:
                out.append(f"    {label:9s} [{c.key}] {c.description}")
        for h in self.history:
            out.append(f"    -> {h['from']} to {h['to']}: {h['why']}")
        return "\n".join(out)


def from_morning_plan(plan: dict, symbol: str, opened_ts: str,
                      expires_at_et: Optional[str] = None) -> Thesis:
    """Build a thesis from the morning plan, so the position inherits the day's
    reasoning instead of a separately-invented one.

    The morning read already produces exactly the right raw material: a thesis
    sentence and a list of specific, checkable invalidators. Reusing them means
    the thing monitored intraday is provably the same thing that was decided
    pre-open — no drift between the plan and what the machine is watching.
    """
    inv = plan.get("invalidators") or []
    if not inv:
        raise ThesisError(
            "morning plan has no invalidators — cannot build a thesis that could be "
            "proven wrong. Re-run the morning read.")
    return Thesis(
        symbol=symbol, opened_ts=opened_ts,
        statement=plan.get("thesis") or "(morning plan carried no thesis sentence)",
        confirmations=[Condition("plan_holds", "the morning day-plan behaviour actually "
                                               "shows up in the tape")],
        weakeners=[Condition(f"level_{i}", d[:120])
                   for i, d in enumerate(plan.get("key_levels") or [])[:3]],
        invalidators=[Condition(f"inv_{i}", d[:160]) for i, d in enumerate(inv)],
        expires_at_et=expires_at_et,
    )


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    t = Thesis(
        symbol="NVDA", opened_ts=datetime.now(timezone.utc).isoformat(),
        statement="NVDA held while AMD broke — relative strength into the Aug 26 print",
        confirmations=[Condition("holds_above_206", "NVDA holds above the 206.69 prior close"),
                       Condition("outperforms_smh", "NVDA outperforms SMH on the session")],
        weakeners=[Condition("loses_vwap", "price closes back below session VWAP")],
        invalidators=[Condition("below_203", "trades below 203 and fails to reclaim in an hour"),
                      Condition("spacex_inhouse", "SpaceX announcement confirmed as in-house silicon")],
        expires_at_et="15:45")

    print(t.describe())
    print("\n  --- walking it through a session ---")
    for obs, clock, label in [
        ({"holds_above_206"}, "10:15", "one confirmation in"),
        ({"holds_above_206", "outperforms_smh"}, "10:45", "both confirmations"),
        ({"holds_above_206", "loses_vwap"}, "11:30", "a weakener fires"),
        ({"holds_above_206", "below_203"}, "13:10", "an invalidator fires"),
        (set(), "13:20", "everything recovers — must NOT resurrect"),
    ]:
        print(f"    {clock}  {t.evaluate(obs, now_et=clock, note=label):20s} {label}")

    print("\n  --- guards ---")
    for label, f in [
        ("no invalidators", lambda: Thesis(symbol="X", opened_ts="t", statement="a statement here",
                                           confirmations=[Condition("a", "something checkable")],
                                           weakeners=[], invalidators=[])),
        ("vague condition", lambda: Condition("x", "sours")),
        ("unknown observation", lambda: t.__class__.evaluate(
            Thesis(symbol="X", opened_ts="t", statement="a statement here",
                   confirmations=[Condition("a", "something checkable")], weakeners=[],
                   invalidators=[Condition("b", "something else checkable")]), {"nope"})),
    ]:
        try:
            f(); print(f"    ❌ ACCEPTED: {label}")
        except ThesisError as e:
            print(f"    ✅ {label:22s} -> {str(e)[:62]}")
