#!/usr/bin/env python3
"""SCENARIO ENGINE — a distribution, not a number.

A single bias value in [-1, +1] is false precision. "+0.35" cannot distinguish
"probably grinds up all day" from "70% chance of a hard rally, 30% chance of a
failed breakout that guts you", and those two states call for opposite exit
behaviour. So ALPHAZERO carries a DISTRIBUTION:

    bull_continuation   42%
    range_consolidation 31%
    failed_breakout     17%
    risk_event          10%

Each scenario states its own probability, confidence, expected duration,
evidence, invalidation conditions, affected symbols and a RECOMMENDED Stockfish
policy. That last word is doing real work: AlphaZero recommends, it does not
select. Turning a distribution into the policy actually in force is a mechanical
decision and lives in stockfish_exit.policy_from_scenarios (ruling 1 —
AlphaZero communicates meaning, Stockfish controls mechanics).

This is the direct answer to alphazero_bias.py's own honesty label. A placeholder
that emits one confident number has no way to say "I don't know"; a distribution
says it natively — a flat spread across four scenarios IS the statement that the
day is unreadable, and it is actionable rather than embarrassing.

FRESHNESS IS PART OF THE CONTRACT. Every set carries the time it was formed and
how long it may be trusted. A stale distribution must never steer a live
position, and the staleness is structural rather than a convention someone has
to remember: `is_fresh()` is checked at the point of use and a stale set is
refused, not silently used.

FAIL LOUD: probabilities that do not sum to 1 raise. An unknown policy name
raises. A scenario without invalidation conditions raises — a scenario that
cannot be proven wrong is a mood, not a forecast, and it can never be scored.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Literal, Optional

# The named day-shapes. Deliberately few: this is a vocabulary a human argues
# with, not a taxonomy. Adding one is a real decision — it changes what every
# historical scorecard row means.
ScenarioName = Literal[
    "bull_continuation",     # trend up, extends
    "bear_continuation",     # trend down, extends
    "range_consolidation",   # two-sided, mean-reverting, no follow-through
    "failed_breakout",       # extends then reverses through the break level
    "risk_event",            # market-wide repricing that ignores the symbol's own story
]
ALL_SCENARIOS: tuple[str, ...] = (
    "bull_continuation", "bear_continuation", "range_consolidation",
    "failed_breakout", "risk_event",
)

PROB_TOLERANCE = 1e-6
DEFAULT_MAX_AGE_MIN = 30


class ScenarioError(RuntimeError):
    """Malformed distribution. Never repaired silently — a normalised-behind-
    your-back probability set is a different forecast than the one written."""


@dataclass(frozen=True)
class Scenario:
    name: str
    probability: float                     # 0..1
    confidence: float                      # 0..1 — how much to trust THIS estimate
    expected_duration_min: int             # how long this shape should persist
    evidence: list[str]                    # why this scenario, specifically
    invalidation: list[str]                # what would prove it wrong — REQUIRED
    affected_symbols: list[str]
    recommended_policy: str                # a name from stockfish_exit.INTENT

    def __post_init__(self):
        if self.name not in ALL_SCENARIOS:
            raise ScenarioError(f"unknown scenario {self.name!r}; known: {list(ALL_SCENARIOS)}")
        for f_ in ("probability", "confidence"):
            v = getattr(self, f_)
            if not (0.0 <= v <= 1.0):
                raise ScenarioError(f"{self.name}.{f_}={v} outside [0, 1]")
        if self.expected_duration_min <= 0:
            raise ScenarioError(f"{self.name}: expected_duration_min must be positive")
        if not self.invalidation:
            raise ScenarioError(
                f"{self.name}: no invalidation conditions. A scenario that cannot be "
                "proven wrong is a mood, not a forecast — it can never be scored, so it "
                "is not allowed to exist here.")
        if not self.evidence:
            raise ScenarioError(f"{self.name}: no evidence — say why, or do not claim it")
        # Validated against the engine's own vocabulary so a typo cannot select a
        # different risk profile downstream.
        from stockfish_exit import INTENT
        if self.recommended_policy not in INTENT:
            raise ScenarioError(
                f"{self.name}: recommended_policy {self.recommended_policy!r} is not a "
                f"known exit policy; known: {sorted(INTENT)}")


@dataclass(frozen=True)
class ScenarioSet:
    ts: str                                # ISO, when this view was formed
    symbol: str
    scenarios: list[Scenario]
    source: str                            # what produced it (model id, or 'computed')
    max_age_min: int = DEFAULT_MAX_AGE_MIN

    def __post_init__(self):
        if not self.scenarios:
            raise ScenarioError("empty scenario set — say something, even if it is 'unreadable'")
        names = [s.name for s in self.scenarios]
        if len(names) != len(set(names)):
            raise ScenarioError(f"duplicate scenarios: {names}")
        total = sum(s.probability for s in self.scenarios)
        if abs(total - 1.0) > PROB_TOLERANCE:
            raise ScenarioError(
                f"probabilities sum to {total:.6f}, not 1.0. Not normalising for you: a "
                "set that does not sum to one means the model did not produce a "
                "distribution, and quietly rescaling it would hide that.")
        if self.max_age_min <= 0:
            raise ScenarioError("max_age_min must be positive")

    # -- the distribution's own opinions ------------------------------------
    @property
    def top(self) -> Scenario:
        return max(self.scenarios, key=lambda s: s.probability)

    @property
    def entropy(self) -> float:
        """0 = certain, 1 = maximally unsure across the scenarios present.

        This is the number that lets the system SAY it does not know, instead of
        emitting a confident-looking average of contradictory views."""
        import math
        n = len(self.scenarios)
        if n < 2:
            return 0.0
        h = -sum(s.probability * math.log(s.probability)
                 for s in self.scenarios if s.probability > 0)
        return h / math.log(n)

    def is_decisive(self, floor: float = 0.50) -> bool:
        """Does one scenario actually dominate? A 42/31/17/10 spread does not."""
        return self.top.probability >= floor

    def age_min(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        formed = datetime.fromisoformat(self.ts.replace("Z", "+00:00"))
        return (now - formed).total_seconds() / 60.0

    def is_fresh(self, now: Optional[datetime] = None) -> bool:
        return self.age_min(now) <= self.max_age_min

    def get(self, name: str) -> Optional[Scenario]:
        return next((s for s in self.scenarios if s.name == name), None)

    def to_dict(self) -> dict:
        return {"ts": self.ts, "symbol": self.symbol, "source": self.source,
                "max_age_min": self.max_age_min,
                "entropy": round(self.entropy, 4),
                "scenarios": [asdict(s) for s in self.scenarios]}

    @staticmethod
    def from_dict(d: dict) -> "ScenarioSet":
        return ScenarioSet(
            ts=d["ts"], symbol=d["symbol"], source=d["source"],
            max_age_min=d.get("max_age_min", DEFAULT_MAX_AGE_MIN),
            scenarios=[Scenario(**s) for s in d["scenarios"]])

    def describe(self, now: Optional[datetime] = None) -> str:
        fresh = "FRESH" if self.is_fresh(now) else "STALE"
        out = [f"  {self.symbol}  {fresh} ({self.age_min(now):.0f}m old, max {self.max_age_min}m)"
               f"   entropy {self.entropy:.2f}"
               f"   {'decisive' if self.is_decisive() else 'NO DOMINANT SCENARIO'}"]
        for s in sorted(self.scenarios, key=lambda x: -x.probability):
            bar = "#" * int(round(s.probability * 30))
            out.append(f"    {s.name:22s} {s.probability:5.1%} {bar:<30s} "
                       f"conf {s.confidence:.2f}  ~{s.expected_duration_min}m  "
                       f"-> {s.recommended_policy}")
        return "\n".join(out)


def unreadable(symbol: str, why: str, *, source: str, ts: Optional[str] = None) -> ScenarioSet:
    """The honest output when the tape genuinely does not say anything.

    A flat spread is a real statement, not a failure — and it is far more useful
    downstream than a fabricated lean, because `is_decisive()` returns False and
    the exit policy stays conservative on its own.
    """
    p = round(1.0 / 3, 6)
    probs = [p, p, round(1.0 - 2 * p, 6)]
    names = ("bull_continuation", "bear_continuation", "range_consolidation")
    return ScenarioSet(
        ts=ts or datetime.now(timezone.utc).isoformat(), symbol=symbol, source=source,
        scenarios=[Scenario(n, pr, 0.2, 60, [why], ["any decisive break with volume"],
                            [symbol], "DEFEND")
                   for n, pr in zip(names, probs)])


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    demo = ScenarioSet(
        ts=datetime.now(timezone.utc).isoformat(), symbol="NVDA", source="demo",
        scenarios=[
            Scenario("bull_continuation", 0.42, 0.6, 120,
                     ["held while AMD broke", "above opening VWAP on rising volume"],
                     ["loses 203 and fails to reclaim within an hour"], ["NVDA"], "RIDE"),
            Scenario("range_consolidation", 0.31, 0.7, 180,
                     ["no stock-specific catalyst", "three weeks to earnings"],
                     ["clean break of 210 or 200 on expanding volume"], ["NVDA"], "DEFEND"),
            Scenario("failed_breakout", 0.17, 0.5, 60,
                     ["stretched 2.1 ATR from VWAP", "prior highs overhead"],
                     ["reclaims and holds above the breakout level"], ["NVDA"], "HARVEST"),
            Scenario("risk_event", 0.10, 0.4, 45,
                     ["13:00 30-year auction", "payrolls tomorrow"],
                     ["auction stops through, yields fall"], ["NVDA", "SPY"], "EVENT"),
        ])
    print(demo.describe())
    print(f"\n  top: {demo.top.name}   decisive at 50%: {demo.is_decisive()}")
    print(f"\n  round-trip stable: "
          f"{ScenarioSet.from_dict(demo.to_dict()).to_dict() == demo.to_dict()}")
    print("\n  --- the honest 'I don't know' ---")
    print(unreadable("NVDA", "no catalyst, no directional evidence", source="demo").describe())
