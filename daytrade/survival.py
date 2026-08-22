#!/usr/bin/env python3
"""SURVIVAL PLANNER — spec 002. NOT BUILT. No blockers, fully specified.

Pure arithmetic, no market data, no dependencies. Buildable in an hour and
useful the morning it lands. Unbuilt only because 005/008 came first.

THE DELIVERABLE is one sentence printed before every entry:
    SURVIVAL: risk $140 -> worst case $24,860, cushion $860 left, 1 day back to
    on-track at $300/day goal. Still on track: YES. Verdict: GO (1.0x).

RULES IN PRIORITY ORDER (spec 002, first hit wins): cooloff absolute · a loss
must never break the eval · already at goal today means stop · after a loss
today bet 2 is SMALLER with wider room · else GO.

INVARIANTS, enforced with assertions:
  - size_multiplier <= 1.0 ALWAYS. No code path scales size up. Not after a
    loss, not after a win, not on high confidence. Bet 2 smaller than bet 1 is
    the single rule separating the doctrine from tilt.
  - daily_goal_pct read from config, never computed from recent P&L.
  - Regime confidence does NOT enter this calculation, by design.

ONE CONFLICT TO RESOLVE BEFORE BUILDING: spec 002 computes
`goal = account_size * daily_goal_pct / 100`, which drifts as the account grows.
The doctrine and THE_SHOT.md use a FIXED $300/day. They disagree. Pick one.
"""
from __future__ import annotations

raise NotImplementedError("survival.py is not built. No blockers — see spec 002.")
