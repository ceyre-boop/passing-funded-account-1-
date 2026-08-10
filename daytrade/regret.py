#!/usr/bin/env python3
"""REGRET GRADER — spec 014 (regret half).

Grades a COMPLETED trade against named counterfactuals. It never selects the
hindsight winner: a grader that crowns winners becomes a strategy selector,
and selection is 017's job under promotion discipline (minimum samples,
out-of-sample, calibration) — not one good-looking trade's.

All excursion metrics are computed from the recorded cycle stream — the same
facts the decision path saw — and are expressed in R (plan risk per share).
An OPEN trade refuses to be graded: grading a live position invites exactly
the mid-flight re-litigation this system exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional

from stockfish_exit import _et_minutes


class RegretError(RuntimeError):
    """Grading refused. Most commonly: the trade is still open."""


@dataclass(frozen=True)
class RegretReport:
    trade_id: str
    realized_r: float
    mfe_r: float                    # max favorable excursion, in R
    mae_r: float                    # max adverse excursion after entry, in R
    captured_frac: Optional[float]  # realized / MFE (None when MFE <= 0)
    giveback_r: float               # MFE - realized
    hold_time_min: Optional[float]
    slippage_r: float               # 0.0 until 013 wires live fills
    slippage_basis: str             # 'paper' — labeled, never silently zero
    max_drawdown_r: float           # worst peak-to-trough of open P&L
    exit_efficiency: Optional[float]  # realized vs best counterfactual (info only)
    counterfactual_delta_r: dict    # policy -> (policy realized_r - realized_r)

    def to_dict(self) -> dict:
        return asdict(self)


def grade(*, trade_id: str, realized_r: float, closed: bool, plan: dict,
          cycles: List[tuple], shadow_results: dict) -> RegretReport:
    """Pure. Same inputs, same report. `shadow_results` is run_shadow output —
    the named counterfactuals; the report DELTAS against them, it does not
    pick among them."""
    if not closed:
        raise RegretError(
            f"{trade_id}: refusing to grade an OPEN trade — regret on a live "
            "position is mid-flight re-litigation, and the number would change "
            "by the next bar anyway")
    if not cycles:
        raise RegretError(f"{trade_id}: no cycle stream — nothing to grade against")

    entry, sl = float(plan["entry"]), float(plan["sl"])
    direction = int(plan["direction"])
    risk = abs(entry - sl)
    if risk <= 0:
        raise RegretError("plan risk (|entry - sl|) must be positive")

    excursions = [(float(p) - entry) * direction / risk for p, _ in cycles]
    mfe_r = max(0.0, max(excursions))
    mae_r = min(0.0, min(excursions))

    peak = dd = 0.0
    for x in excursions:
        peak = max(peak, x)
        dd = min(dd, x - peak)

    t0, t1 = cycles[0][1], cycles[-1][1]
    hold = (None if not (t0 and t1) else
            float(_et_minutes(t1, "cycle_end") - _et_minutes(t0, "cycle_start")))

    deltas = {name: r.realized_r - realized_r
              for name, r in sorted(shadow_results.items())}
    best_cf = max((r.realized_r for r in shadow_results.values()), default=None)
    efficiency = (None if best_cf is None or best_cf <= 0
                  else realized_r / best_cf)

    return RegretReport(
        trade_id=trade_id, realized_r=realized_r, mfe_r=mfe_r, mae_r=mae_r,
        captured_frac=(None if mfe_r <= 0 else realized_r / mfe_r),
        giveback_r=mfe_r - realized_r, hold_time_min=hold,
        slippage_r=0.0, slippage_basis="paper",
        max_drawdown_r=dd, exit_efficiency=efficiency,
        counterfactual_delta_r=deltas)


if __name__ == "__main__":
    print(__doc__)
    print("run the suite: pytest daytrade/test_shadow_regret.py -v")
