#!/usr/bin/env python3
"""CARRY EXIT EVALUATOR — current-state evaluation for a multi-day FX position.

A SECOND evaluator, built beside `stockfish_exit`, never inside it. The stack
exam recorded the reason (SF-3): `TradeState` has 23 fields, every one
intraday, and NO term for swap accrual, days held, weekend exposure, rate
differential, or financing. That is not a mis-parameterisation of
futures-exit-v1 — the terms do not exist in the type. Extending TradeState
would produce one type complete for neither position and would break the one
property the exam passes at ceiling: a single implementation with byte-
identical replays across 39 callers.

So: `futures-exit-v1` stays frozen and correct for what it prices.
`carry-exit-v1` prices a carry position.

SAME DISCIPLINE, DIFFERENT DICTIONARY:
  - pure function of state; same inputs, same actions, every time
  - no learning inside the layer, ever — frozen and auditable
  - fails loud; a malformed state raises at construction
  - one implementation, called identically by backtest and live

THE EXIT VOCABULARY IS READ FROM THE SEALED RECORD, NOT INVENTED. The 411
sealed v015 trades exit for exactly four reasons — time (205), trailing_stop
(118), reversal (79), stop (9) — so this engine expresses those four and
nothing else. Parameters are DECLARED here and reconciled against the record
by `carry_reconcile.py`; they are not fitted, because fitting inside the
evaluation layer is the thing that makes it stop being the yardstick.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional


class CarryStage(str, Enum):
    OPEN = "OPEN"
    TRAILING = "TRAILING"      # trail has taken over from the initial stop
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


_ORDER = {CarryStage.OPEN: 0, CarryStage.TRAILING: 1,
          CarryStage.CLOSING: 2, CarryStage.CLOSED: 3}

# Exit reasons, verbatim from the sealed record's vocabulary. An exit this
# engine cannot name is an exit it must not take.
EXIT_REASONS = ("stop", "reversal", "time", "trailing_stop")


class CarryStateError(RuntimeError):
    """Malformed carry state. Never guessed around."""


@dataclass
class CarryState:
    """Everything that materially changes a carry position's value.

    Deliberately NOT a superset of TradeState — a carry position has no
    opening range, no intraday ladder, and no same-session flatten, and
    carrying those fields would invite an evaluator to read terms that do not
    apply to the position in front of it.
    """
    # --- the position
    pair: str
    direction: int                   # +1 long base, -1 short base
    entry: float
    price: float                     # current daily close
    sl: float                        # effective stop, as last applied

    # --- what holding COSTS (the terms futures-exit-v1 has no room for)
    swap_accrual_r_per_day: float    # from the firm contract, never re-declared
    days_held: int = 0
    weekends_crossed: int = 0
    weekend_next_session: bool = False

    # --- the carry itself
    rate_diff: Optional[float] = None        # base - quote, at entry-to-date
    rate_diff_at_entry: Optional[float] = None
    rate_diff_stale_days: Optional[int] = None

    # --- daily price state
    atr14: Optional[float] = None            # in price units
    hwm: Optional[float] = None              # best price seen since entry

    # --- declared policy parameters (carry-exit-v1)
    time_stop_days: Optional[int] = 5        # the record's dominant exit
    trail_atr_mult: Optional[float] = 2.0    # None disables trailing
    trail_arms_after_days: int = 1
    reversal_on_carry_flip: bool = True
    # PRECEDENCE, made explicit because the reconciliation showed it matters
    # more than any parameter: sealed trailing_stop exits hold a median 7
    # days, LONGER than the 5-day time stop, so a time stop that outranks the
    # trail can never reproduce them. Which order is correct is an empirical
    # question about the strategy, so it is a declared switch and both
    # settings are measured — not a judgement call made once in silence.
    trail_outranks_time: bool = False
    max_stale_rate_days: int = 95            # beyond this the carry read is void

    # --- lifecycle
    stage: CarryStage = CarryStage.OPEN
    catastrophic_sl: Optional[float] = None  # the original stop; never loosened

    def __post_init__(self):
        if self.direction not in (+1, -1):
            raise CarryStateError("direction must be +1/-1")
        for f_ in ("entry", "price", "sl", "swap_accrual_r_per_day"):
            v = getattr(self, f_)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                raise CarryStateError(f"bad field {f_}={v}")
        if self.swap_accrual_r_per_day < 0:
            raise CarryStateError("negative swap accrual — the contract's sign "
                                  "convention is a cost per day held")
        if self.days_held < 0 or self.weekends_crossed < 0:
            raise CarryStateError("negative days_held/weekends_crossed")
        if self.time_stop_days is not None and self.time_stop_days <= 0:
            raise CarryStateError("time_stop_days must be positive or None")
        if self.trail_atr_mult is not None and self.trail_atr_mult <= 0:
            raise CarryStateError("trail_atr_mult must be positive or None")
        risk = abs(self.entry - self.sl)
        if risk <= 0:
            raise CarryStateError(f"entry {self.entry} == stop {self.sl}: "
                                  "R is undefined")
        if self.hwm is None:
            self.hwm = self.price
        if self.catastrophic_sl is None:
            self.catastrophic_sl = self.sl

    # ---- the terms futures-exit-v1 cannot express -----------------------

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.catastrophic_sl)

    @property
    def swap_paid_r(self) -> float:
        """Financing paid so far, in R. Weekends bill three days, which is
        why 72% of the sealed trades crossing one is a first-order fact."""
        return (self.days_held + 2 * self.weekends_crossed) \
            * self.swap_accrual_r_per_day

    @property
    def gross_r(self) -> float:
        return (self.price - self.entry) * self.direction / self.risk_per_unit

    @property
    def net_r(self) -> float:
        """What the position is ACTUALLY worth: price move minus financing."""
        return self.gross_r - self.swap_paid_r

    @property
    def carry_flipped(self) -> bool:
        """The reason for the trade is gone: the rate differential no longer
        favours the direction held."""
        if self.rate_diff is None or self.rate_diff_at_entry is None:
            return False
        if (self.rate_diff_stale_days is not None
                and self.rate_diff_stale_days > self.max_stale_rate_days):
            return False          # a stale read is not evidence of a flip
        return (self.rate_diff * self.direction) < 0 <= \
               (self.rate_diff_at_entry * self.direction)


@dataclass
class CarryAction:
    kind: str                        # HOLD | MOVE_SL | EXIT_ALL
    sl: Optional[float] = None
    reason: str = ""
    exit_reason: Optional[str] = None      # one of EXIT_REASONS

    def to_dict(self) -> dict:
        return asdict(self)


def _advance(state: CarryState, target: CarryStage) -> None:
    if _ORDER[target] < _ORDER[state.stage]:
        raise CarryStateError(f"{state.stage} -> {target} runs the lifecycle "
                              "backwards")
    state.stage = target


def trail_level(state: CarryState) -> Optional[float]:
    """ATR trail off the high-water mark, on DAILY bars. None when the trail
    is disabled, unarmed, or ATR is unavailable — never a guessed width."""
    if state.trail_atr_mult is None or state.atr14 is None:
        return None
    if state.days_held < state.trail_arms_after_days:
        return None
    return state.hwm - state.direction * state.trail_atr_mult * state.atr14


def effective_stop(state: CarryState) -> float:
    """The most protective active level. The catastrophic stop is never
    loosened — same rule as the intraday engine, different inputs."""
    levels = [state.sl, state.catastrophic_sl]
    t = trail_level(state)
    if t is not None:
        levels.append(t)
    return max(levels) if state.direction > 0 else min(levels)


def decide_carry_exit(state: CarryState) -> List[CarryAction]:
    """The whole carry exit policy. Ordered actions.

    PRECEDENCE — never violate this order:
      1. stop hit          -> EXIT_ALL  ('stop')
      2. carry reversal    -> EXIT_ALL  ('reversal')   the reason is gone
      3. time stop         -> EXIT_ALL  ('time')       financing outlives edge
      4. trailing stop hit -> EXIT_ALL  ('trailing_stop')
      5. trail advance     -> MOVE_SL
    """
    if state.stage in (CarryStage.CLOSING, CarryStage.CLOSED):
        return [CarryAction("HOLD", reason="already closing")]

    state.hwm = (max(state.hwm, state.price) if state.direction > 0
                 else min(state.hwm, state.price))

    # 1. the catastrophic stop, tested before anything advances
    hit = (state.price <= state.catastrophic_sl if state.direction > 0
           else state.price >= state.catastrophic_sl)
    if hit:
        _advance(state, CarryStage.CLOSING)
        return [CarryAction("EXIT_ALL", reason="stop hit", exit_reason="stop")]

    # 2. the carry reason is gone — this is the term futures-exit-v1 lacks
    if state.reversal_on_carry_flip and state.carry_flipped:
        _advance(state, CarryStage.CLOSING)
        return [CarryAction("EXIT_ALL",
                            reason=f"rate differential flipped against the "
                                   f"position ({state.rate_diff_at_entry} -> "
                                   f"{state.rate_diff})",
                            exit_reason="reversal")]

    def _time_stop():
        if (state.time_stop_days is not None
                and state.days_held >= state.time_stop_days):
            _advance(state, CarryStage.CLOSING)
            return [CarryAction("EXIT_ALL",
                                reason=f"held {state.days_held}d >= time stop "
                                       f"{state.time_stop_days}d "
                                       f"(financing paid {state.swap_paid_r:.4f}R)",
                                exit_reason="time")]
        return None

    # 3. time stop — unless the trail outranks it (declared switch above)
    if not state.trail_outranks_time:
        acts = _time_stop()
        if acts:
            return acts

    # 4/5. the trail
    t = trail_level(state)
    if t is not None:
        crossed = (state.price <= t if state.direction > 0 else state.price >= t)
        if crossed:
            _advance(state, CarryStage.CLOSING)
            return [CarryAction("EXIT_ALL", reason="trail crossed",
                                exit_reason="trailing_stop")]
        eff = effective_stop(state)
        tighter = (eff > state.sl if state.direction > 0 else eff < state.sl)
        if tighter:
            _advance(state, CarryStage.TRAILING)
            return [CarryAction("MOVE_SL", sl=eff,
                                reason=f"ATR trail advanced ({state.sl:g} -> "
                                       f"{eff:g})")]

    if state.trail_outranks_time:
        acts = _time_stop()
        if acts:
            return acts

    return [CarryAction("HOLD", reason="carry intact, inside time stop")]


def apply_carry_action(state: CarryState, action: CarryAction) -> None:
    """Fold one action back into the state. ONE implementation, same reason
    the intraday engine has one: backtest and live must evolve state
    identically or their logs cannot be diffed."""
    if action.kind == "MOVE_SL":
        if action.sl is None:
            raise CarryStateError("MOVE_SL with no level")
        looser = (action.sl < state.sl if state.direction > 0
                  else action.sl > state.sl)
        if looser:
            raise CarryStateError(
                f"MOVE_SL would loosen the stop ({state.sl} -> {action.sl}) — "
                "refused, same rule as the intraday constitution")
        state.sl = action.sl
    elif action.kind == "EXIT_ALL":
        if action.exit_reason not in EXIT_REASONS:
            raise CarryStateError(
                f"exit_reason {action.exit_reason!r} is not in the sealed "
                f"record's vocabulary {EXIT_REASONS} — an exit this engine "
                "cannot name is an exit it must not take")
        state.stage = CarryStage.CLOSED
    elif action.kind != "HOLD":
        raise CarryStateError(f"unknown action kind {action.kind!r}")


if __name__ == "__main__":
    print(__doc__)
    print(f"exit vocabulary: {EXIT_REASONS}")
    print("run the suite: pytest daytrade/test_carry_exit.py -v")
