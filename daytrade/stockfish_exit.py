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

def _et_minutes(hhmm: str, field: str) -> int:
    """'HH:MM' -> minutes since midnight ET. Malformed input raises."""
    try:
        h, m = hhmm.strip().split(":")
        h, m = int(h), int(m)
    except Exception as e:
        raise ValueError(f"{field} must be 'HH:MM', got {hhmm!r}") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"{field} out of range: {hhmm!r}")
    return h * 60 + m

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
    goal_fraction: float = 0.5      # fraction banked at TP2 (spec 008: partial_frac)
    # Time-flatten (doctrine: flat before 11:00). BOTH must be supplied for the
    # rule to arm; either left None means no clock was given, so no time exit.
    flatten_at_et: Optional[str] = None     # 'HH:MM' ET, e.g. '11:00'
    now_et: Optional[str] = None            # 'HH:MM' ET, the caller's clock
    # --- exit policy (spec 003). Defaults reproduce pre-policy behaviour EXACTLY,
    #     which is what lets the original replay log stay byte-identical.
    trail_mult: Optional[float] = 1.0       # multiplies trail_dist; None = no trailing at all
    be_arm_frac: float = 1.0                # breakeven arms at this fraction of the TP1 distance
    hold_past_tp2: bool = True              # False = flatten fully at TP2 instead of partial+trail
    exit_policy: str = "DEFAULT"            # provenance only; params above are authoritative

    def __post_init__(self):
        if self.direction not in (+1, -1): raise ValueError("direction must be +1/-1")
        for f_ in ("entry", "qty", "price", "sl", "tp1", "tp2", "trail_dist"):
            v = getattr(self, f_)
            if v is None or (isinstance(v, (int, float)) and v != v):
                raise ValueError(f"bad field {f_}={v}")   # fail loud, never guess
        for f_ in ("flatten_at_et", "now_et"):            # validate iff supplied
            v = getattr(self, f_)
            if v is not None: _et_minutes(v, f_)
        if self.trail_mult is not None and self.trail_mult <= 0:
            raise ValueError(f"trail_mult must be positive or None, got {self.trail_mult}")
        if not (0 < self.be_arm_frac <= 1.0):
            raise ValueError(f"be_arm_frac must be in (0, 1], got {self.be_arm_frac}")
        if not (0 <= self.goal_fraction <= 1.0):
            raise ValueError(f"goal_fraction must be in [0, 1], got {self.goal_fraction}")
        if self.hwm is None: self.hwm = self.price

@dataclass
class Action:
    kind: str                   # HOLD | MOVE_SL | TAKE_PARTIAL | EXIT_ALL
    sl: Optional[float] = None
    fraction: Optional[float] = None
    reason: str = ""

# Named presets from spec 003. These are SUGAR — the authoritative values are the
# fields on TradeState. Keeping the params on the state (rather than resolving a
# string inside decide_exit) is what lets spec 008 sweep a wide grid of configs
# the shipping policy table does not contain.
POLICY_PARAMS = {
    #                trail_mult  be_arm_frac  hold_past_tp2
    "DEFAULT":       (1.0,       1.00,        True),
    "STATIC":        (None,      1.00,        True),   # consolidation: stop churning
    "TRAIL_WIDE":    (1.50,      1.00,        True),   # continuation: let it run
    "TRAIL_TIGHT":   (0.50,      0.50,        True),   # manipulation: riskless sooner
    "SCRATCH_FAST":  (0.25,      0.25,        True),   # [SKETCH] not emitted by regime v1
}

def policy_params(name: str) -> dict:
    """Expand a preset into TradeState kwargs. Unknown name raises — a typo'd
    policy must stop the session, never silently pick a different risk profile.
    """
    if name not in POLICY_PARAMS:
        raise ValueError(f"unknown exit_policy {name!r}; known: {sorted(POLICY_PARAMS)}")
    tm, be, hold = POLICY_PARAMS[name]
    return {"trail_mult": tm, "be_arm_frac": be, "hold_past_tp2": hold,
            "exit_policy": name}


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
    # 'tighten' MULTIPLIES with the policy, it does not replace it (spec 003):
    # TRAIL_WIDE + tighten = 1.5 * 0.5 = 0.75x, not 0.5x.
    trail = (None if state.trail_mult is None else
             state.trail_dist * state.trail_mult * (0.5 if state.urgent == "tighten" else 1.0))

    # 1. hard stop
    if (state.price <= state.sl if state.direction > 0 else state.price >= state.sl):
        return [Action("EXIT_ALL", reason="stop hit")]

    # 1b. time-flatten — always live once a clock is supplied (doctrine: flat
    #     before 11:00). No clock given == rule not armed, never a guessed time.
    if state.flatten_at_et is not None and state.now_et is not None:
        if _et_minutes(state.now_et, "now_et") >= _et_minutes(state.flatten_at_et, "flatten_at_et"):
            return [Action("EXIT_ALL", reason=f"time flatten {state.flatten_at_et} ET")]

    # 2. TP1: arm breakeven — rule #1, do not lose on the day.
    #    be_arm_frac < 1 arms it EARLIER (manipulation days steal stops; get to
    #    riskless sooner). The == 1.0 branch uses state.tp1 verbatim rather than
    #    recomputing it, because entry + (tp1-entry)*1.0 is not bit-identical to
    #    tp1 in floating point and would silently shift the trigger.
    arm = (state.tp1 if state.be_arm_frac == 1.0 else
           state.entry + (state.tp1 - state.entry) * state.be_arm_frac)
    if not state.tp1_done and _crossed(state, arm):
        state.tp1_done = True
        acts.append(Action("MOVE_SL", sl=state.entry,
                           reason="TP1: breakeven armed, day cannot go red"
                                  + ("" if state.be_arm_frac == 1.0
                                     else f" (early, {state.be_arm_frac:g} of TP1)")))

    # 3. TP2: bank the set goal, stop rides to TP1
    if not state.tp2_done and _crossed(state, state.tp2):
        state.tp2_done = True
        if not state.hold_past_tp2:
            # No runner is kept. The day goal IS the trade — take it and be done.
            return acts + [Action("EXIT_ALL", reason="TP2: day goal hit, full exit (hold_past_tp2=False)")]
        acts.append(Action("TAKE_PARTIAL", fraction=state.goal_fraction, reason="TP2: day goal banked"))
        acts.append(Action("MOVE_SL", sl=state.tp1, reason="TP2: stop rides to TP1"))

    # 4. TP3: trail the runner. trail_mult=None means STATIC — after TP2 the
    #    levels stay exactly where the plan put them, no churn on noise.
    if state.tp2_done and trail is not None:
        trail_sl = state.hwm - state.direction * trail
        cur_ok = trail_sl > state.sl if state.direction > 0 else trail_sl < state.sl
        if cur_ok:
            acts.append(Action("MOVE_SL", sl=round(trail_sl, 4),
                               reason=f"TP3 trail ({'tightened ' if state.urgent=='tighten' else ''}{trail:g} pts off HWM)"))

    return acts or [Action("HOLD", reason="set it and forget it")]

def apply_action(state: TradeState, action: Action) -> None:
    """Fold one Action back into the state. ONE implementation, same reason
    decide_exit is one implementation: the replay, the live runner and any
    backtest harness must evolve state identically or their logs can't be
    diffed (APEX DoD). Unknown or malformed actions raise.
    """
    if action.kind == "MOVE_SL":
        if action.sl is None: raise ValueError("MOVE_SL carries no sl")
        state.sl = action.sl
    elif action.kind == "TAKE_PARTIAL":
        if action.fraction is None: raise ValueError("TAKE_PARTIAL carries no fraction")
        state.qty = state.qty * (1.0 - action.fraction)   # what's still held
    elif action.kind not in ("HOLD", "EXIT_ALL"):
        raise ValueError(f"unknown action kind {action.kind!r}")

if __name__ == "__main__":
    # deterministic replay — the same log this must produce in any harness
    s = TradeState(direction=+1, entry=204.0, qty=163, price=204.0,
                   sl=202.9, tp1=204.9, tp2=205.8, trail_dist=0.8)
    path = [204.2, 204.9, 205.1, 205.8, 206.4, 207.1, 206.9, 206.2]
    for i, px in enumerate(path):
        s.price = px
        for a in decide_exit(s):
            apply_action(s, a)
            print(f"t{i} px={px:7.2f} -> {a.kind:12s} sl={a.sl} frac={a.fraction} | {a.reason}")
    s.price, s.urgent = 206.5, "exit"
    for a in decide_exit(s):
        print(f"urgent px=206.50 -> {a.kind:12s} | {a.reason}")
