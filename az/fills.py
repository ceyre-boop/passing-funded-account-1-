#!/usr/bin/env python3
"""az/fills.py — execution cost for the entry lane. Amendment 4.

The tag's `sovereign/forex/fill_model.py` is NOT reusable here: its `cost_fracs`
imports SPREAD_COST, SWAP_RATES_ANNUAL and ratediff_financing_rate from the forex
backtester, and equity intraday has no swap/financing analogue — `ceiling.py`
charges a flat COST_PER_SHARE. Only the SHAPE ports: a delayed fill at the next
open plus multiplicative spread/slippage, applied to the candidate ONLY so it can
never help it.

Every pessimistic parameter is required. There is no default pessimism, because a
default would be an untested empirical claim.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daytrade"))
from ceiling import COST_PER_SHARE  # noqa: E402 — one cost constant, not a second


class FillError(ValueError):
    pass


@dataclass(frozen=True)
class BaseFill:
    """What ceiling.simulate already charges. Reproduces it exactly."""
    name: str = "base"

    def entry_price(self, *, next_open: float, close_at_t: float) -> float:
        if not math.isfinite(next_open):
            raise FillError("next_open is not finite — the candidate has no fillable bar")
        return float(next_open)

    def cost_per_share(self, *, price: float) -> float:
        return float(COST_PER_SHARE)


@dataclass(frozen=True)
class PessimisticFill:
    """Hostile execution, applied to the CANDIDATE ONLY — a handicap that can only
    tighten a gate. `slip_bps` is charged against the entry in the adverse
    direction; `delay_bars` pushes the fill later, which is where adverse
    selection actually bites on an entry."""
    cost_mult: float
    slip_bps: float
    delay_bars: int
    name: str = "pessimistic"

    def __post_init__(self):
        if not (isinstance(self.cost_mult, (int, float)) and math.isfinite(self.cost_mult)
                and self.cost_mult >= 1.0):
            raise FillError(f"cost_mult={self.cost_mult!r}: must be finite and >= 1 "
                            "(pessimism may not help the candidate)")
        if not (isinstance(self.slip_bps, (int, float)) and math.isfinite(self.slip_bps)
                and self.slip_bps >= 0.0):
            raise FillError(f"slip_bps={self.slip_bps!r}: must be finite and >= 0")
        if self.delay_bars not in (0, 1, 2):
            raise FillError(f"delay_bars={self.delay_bars!r}: only 0, 1 or 2 is defined")

    def entry_price(self, *, next_open: float, close_at_t: float, direction: int = 1) -> float:
        if not math.isfinite(next_open):
            raise FillError("next_open is not finite — the candidate has no fillable bar")
        # slippage always against the trade
        return float(next_open) * (1.0 + direction * self.slip_bps / 10_000.0)

    def cost_per_share(self, *, price: float) -> float:
        return float(COST_PER_SHARE) * self.cost_mult
