#!/usr/bin/env python3
"""STOCKFISH — the mechanical exit engine. ONE implementation, zero judgment.

decide_exit(state) -> Action. Pure function: same state in, same action out,
importable by a backtest harness and the live advisor alike (APEX DoD: run the
same stream through both, diff the logs, byte-identical).

Doctrine encoded (I_AM_A_GOOD_TRADER.md):
  TP1  = don't lose on the day  -> stop moves to breakeven the moment TP1 trades
  TP2  = the set goal ($300)    -> bank the goal (partial), stop rides to TP1
  TP3  = trail the rest         -> dynamic trail, "you never know when it rips"
  Urgent channel (from ALPHAZERO): 'exit' = flatten now; 'tighten' = halve trail.
  Exit doctrine: the entry doesn't matter, the exit is the profession.

SAFETY (APEX): this module never talks to a broker. It emits advice actions;
a human (or a separately-flagged, manually-confirmed runner) executes them.
Fail loud: malformed state raises, never guesses.
"""
from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class TradeState:
    direction: int              # +1 long, -1 short
    entry: float
    qty: float
    price: float                # current
    sl: float
    tp1: float                  # breakeven trigger
    tp2: float                  # day-goal level ($300 worth)
    trail_dist: float           # trail distance beyond TP2 (points)
    hwm: float = None           # best price seen since entry (favorable extreme)
    tp1_done: bool = False
    tp2_done: bool = False
    urgent: Optional[str] = None    # None | 'tighten' | 'exit'  (from ALPHAZERO)
    goal_fraction: float = 0.5      # fraction banked at TP2

    def __post_init__(self):
        if self.direction not in (+1, -1): raise ValueError("direction must be +1/-1")
        for f_ in ("entry", "qty", "price", "sl", "tp1", "tp2", "trail_dist"):
            v = getattr(self, f_)
            if v is None or (isinstance(v, (int, float)) and v != v):
                raise ValueError(f"bad field {f_}={v}")   # fail loud, never guess
        if self.hwm is None: self.hwm = self.price

@dataclass
class Action:
    kind: str                   # HOLD | MOVE_SL | TAKE_PARTIAL | EXIT_ALL
    sl: Optional[float] = None
    fraction: Optional[float] = None
    reason: str = ""

def _fav(state: TradeState, a: float, b: float) -> float:
    """More favorable of two prices for the trade's direction."""
    return max(a, b) if state.direction > 0 else min(a, b)

def _crossed(state: TradeState, level: float) -> bool:
    return state.price >= level if state.direction > 0 else state.price <= level

def decide_exit(state: TradeState) -> List[Action]:
    """The whole exit policy. Returns ordered actions (may be several)."""
    acts: List[Action] = []
    state.hwm = _fav(state, state.hwm, state.price)

    # 0. urgent channel outranks everything
    if state.urgent == "exit":
        return [Action("EXIT_ALL", reason="ALPHAZERO urgent: exit")]
    trail = state.trail_dist * (0.5 if state.urgent == "tighten" else 1.0)

    # 1. hard stop
    if (state.price <= state.sl if state.direction > 0 else state.price >= state.sl):
        return [Action("EXIT_ALL", reason="stop hit")]

    # 2. TP1: arm breakeven — rule #1, do not lose on the day
    if not state.tp1_done and _crossed(state, state.tp1):
        state.tp1_done = True
        acts.append(Action("MOVE_SL", sl=state.entry, reason="TP1: breakeven armed, day cannot go red"))

    # 3. TP2: bank the set goal, stop rides to TP1
    if not state.tp2_done and _crossed(state, state.tp2):
        state.tp2_done = True
        acts.append(Action("TAKE_PARTIAL", fraction=state.goal_fraction, reason="TP2: day goal banked"))
        acts.append(Action("MOVE_SL", sl=state.tp1, reason="TP2: stop rides to TP1"))

    # 4. TP3: trail the runner
    if state.tp2_done:
        trail_sl = state.hwm - state.direction * trail
        cur_ok = trail_sl > state.sl if state.direction > 0 else trail_sl < state.sl
        if cur_ok:
            acts.append(Action("MOVE_SL", sl=round(trail_sl, 4),
                               reason=f"TP3 trail ({'tightened ' if state.urgent=='tighten' else ''}{trail:g} pts off HWM)"))

    return acts or [Action("HOLD", reason="set it and forget it")]

if __name__ == "__main__":
    # deterministic replay — the same log this must produce in any harness
    s = TradeState(direction=+1, entry=204.0, qty=163, price=204.0,
                   sl=202.9, tp1=204.9, tp2=205.8, trail_dist=0.8)
    path = [204.2, 204.9, 205.1, 205.8, 206.4, 207.1, 206.9, 206.2]
    for i, px in enumerate(path):
        s.price = px
        for a in decide_exit(s):
            if a.kind == "MOVE_SL": s.sl = a.sl
            print(f"t{i} px={px:7.2f} -> {a.kind:12s} sl={a.sl} frac={a.fraction} | {a.reason}")
    s.price, s.urgent = 206.5, "exit"
    for a in decide_exit(s):
        print(f"urgent px=206.50 -> {a.kind:12s} | {a.reason}")
