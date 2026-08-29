"""sovereign/forex/fill_model.py — the carry lane's fill/cost model as a first-class, swappable object.

`ForexBacktester._apply_costs` is inline arithmetic: round-trip spread + slippage
normalised by the entry price, and a signed swap fraction from
`swap_model.ratediff_financing_rate` (falls back to `SWAP_RATES_ANNUAL` when the
calibration anchor is absent — which it is; CARRY-FROZEN-001 pins that absence).

`BaseFill` reproduces that arithmetic EXACTLY (I68 — tested against `_apply_costs`
itself), so the incumbent's R is the incumbent's R. `PessimisticFill` is the
deliberately hostile execution model the session brief asks for: wider spread,
more slippage, and a one-bar delayed fill at the next open. It is applied to the
CANDIDATE ONLY (a handicap that can only tighten the gate — it never helps the
candidate). Every pessimistic parameter is a required argument; there is no
"default pessimism" because that would be an untested empirical claim.

Adverse selection on limit orders is N/A: this lane fills at close / next open,
never on resting limits. Partial fills are N/A in an exit-only replay (a residual
position is undefined). Both stated rather than fudged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from sovereign.forex.forex_backtester import (
    SLIPPAGE_PER_SIDE, SPREAD_COST, SWAP_RATES_ANNUAL, _DEFAULT_SPREAD, _DEFAULT_SWAP,
    _calibrated_slippage,
)
from sovereign.forex.swap_model import ratediff_financing_rate


class FillError(ValueError):
    pass


def _swap_days(hold_bars: int) -> int:
    """Mirror of `_apply_costs`: hold days + a Wed-triple/weekend uplift of 2 per 5."""
    hold_days = max(int(hold_bars), 0)
    return hold_days + (hold_days // 5) * 2


def _annual_swap_rate(pair: str, direction: int, entry_date) -> float:
    side = "LONG" if direction >= 0 else "SHORT"
    rate = ratediff_financing_rate(pair, side, entry_date)
    if rate is None:
        rate = SWAP_RATES_ANNUAL.get(pair, _DEFAULT_SWAP)[side]
    return float(rate)


@dataclass(frozen=True)
class BaseFill:
    """The incumbent's own execution assumptions — byte-for-byte `_apply_costs`."""
    name: str = "base"

    def exit_price(self, *, close: float, open_next: float) -> float:
        return float(close)

    def cost_fracs(self, *, pair: str, entry_price: float, direction: int, hold_bars: int,
                   entry_date) -> tuple[float, float]:
        """Returns (spread_frac, swap_frac). Net pnl_pct = gross − spread_frac + swap_frac."""
        spread = SPREAD_COST.get(pair, _DEFAULT_SPREAD)
        per_side = _calibrated_slippage(pair)
        slip = per_side if per_side is not None else SLIPPAGE_PER_SIDE
        cost_price = spread + 2 * slip
        entry = max(float(entry_price), 1e-9)
        spread_frac = cost_price / entry
        swap_frac = (_annual_swap_rate(pair, direction, entry_date) / 365.0) * _swap_days(hold_bars)
        return spread_frac, swap_frac


@dataclass(frozen=True)
class PessimisticFill:
    """Hostile execution for the candidate: spread × spread_mult, slippage × slip_mult,
    exit filled `delay_bars` later at the open. `delay_bars` ∈ {0, 1}; 1 requires a finite
    `open_next` (a missing next bar is a halt, not a silent fallback to the close)."""
    spread_mult: float
    slip_mult: float
    delay_bars: int
    name: str = "pessimistic"

    def __post_init__(self) -> None:
        for k in ("spread_mult", "slip_mult"):
            v = getattr(self, k)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v >= 1.0):
                raise FillError(f"{k}={v!r}: must be a finite multiplier >= 1 (pessimism cannot help the candidate)")
        if self.delay_bars not in (0, 1):
            raise FillError(f"delay_bars={self.delay_bars!r}: only 0 or 1 is defined")

    def exit_price(self, *, close: float, open_next: float) -> float:
        if self.delay_bars == 0:
            return float(close)
        if open_next is None or not math.isfinite(float(open_next)):
            raise FillError("delay_bars=1 but open_next is missing — cannot fill; halting rather than using the close")
        return float(open_next)

    def cost_fracs(self, *, pair: str, entry_price: float, direction: int, hold_bars: int,
                   entry_date) -> tuple[float, float]:
        spread = SPREAD_COST.get(pair, _DEFAULT_SPREAD) * self.spread_mult
        per_side = _calibrated_slippage(pair)
        slip = (per_side if per_side is not None else SLIPPAGE_PER_SIDE) * self.slip_mult
        cost_price = spread + 2 * slip
        entry = max(float(entry_price), 1e-9)
        spread_frac = cost_price / entry
        # Delay adds one bar of holding, and therefore one more day of financing.
        swap_frac = (_annual_swap_rate(pair, direction, entry_date) / 365.0) * _swap_days(hold_bars + self.delay_bars)
        return spread_frac, swap_frac


def net_r(*, gross_pnl_pct: float, spread_frac: float, swap_frac: float, risk_pct: float) -> float:
    """`_apply_costs` composition, expressed in R: (gross − spread + swap) / risk_pct."""
    if not (risk_pct > 0):
        raise FillError(f"risk_pct={risk_pct!r} must be > 0")
    return (gross_pnl_pct - spread_frac + swap_frac) / risk_pct


def make_fill(kind: str, *, spread_mult: Optional[float] = None, slip_mult: Optional[float] = None,
              delay_bars: Optional[int] = None):
    if kind == "base":
        return BaseFill()
    if kind == "pessimistic":
        if spread_mult is None or slip_mult is None or delay_bars is None:
            raise FillError("pessimistic fill needs spread_mult, slip_mult and delay_bars — all required")
        return PessimisticFill(spread_mult=spread_mult, slip_mult=slip_mult, delay_bars=delay_bars)
    raise FillError(f"unknown fill kind {kind!r}")
