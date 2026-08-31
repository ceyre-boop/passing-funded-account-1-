#!/usr/bin/env python3
"""az/prereg.py — spec 049 enforced as code, not consulted as prose.

Three pieces, none of which had a precedent to reuse:
  adjudicate()      the pair rule — SPY+QQQ are ONE grading unit
  sub_periods()     P1-P4, fixed in advance; splits.py is two-way only
  max_stat_null()   circular day-shift, correlation-preserving

`mechanisms.permutation_p` is a donor for the day-blocked shuffle mechanic, not a
dependency: its statistic is a fixed contrast, not a max over cells.
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daytrade"))
from mechanisms import mde  # noqa: E402  — the repo's own MDE, not a second one


class PreregError(RuntimeError):
    pass


# ---------------------------------------------------------------- pair rule

@dataclass(frozen=True)
class ArmResult:
    symbol: str
    value: float
    threshold: float

    @property
    def clears(self) -> bool:
        return abs(self.value) >= self.threshold

    @property
    def sign(self) -> int:
        return 0 if self.value == 0 else (1 if self.value > 0 else -1)


@dataclass(frozen=True)
class PairVerdict:
    verdict: str                 # HOLDS | NULL
    reason: str
    arms: tuple

    def header(self) -> str:
        a = "  ".join(f"{r.symbol} {r.value:+.5f} (thr {r.threshold:.5f}, "
                      f"{'clears' if r.clears else 'below'})" for r in self.arms)
        return f"{self.verdict}: {self.reason}  |  {a}"


def adjudicate(*arms: ArmResult) -> PairVerdict:
    """SPY and QQQ are ONE grading unit (spec 049 §4).

    They are the same beta with different weights, so counting them as two
    confirmations turns one coin flip into a fabricated replication. A result
    must hold on BOTH. One clearing while the other fails is NULL — never
    'held on SPY'.
    """
    if len(arms) < 2:
        raise PreregError(f"the pair rule needs >=2 arms, got {len(arms)}")
    signs = {r.sign for r in arms}
    if len(signs) > 1:
        return PairVerdict("NULL", "signs disagree across the pair", arms)
    if 0 in signs:
        return PairVerdict("NULL", "an arm is exactly zero", arms)
    failed = [r.symbol for r in arms if not r.clears]
    if failed:
        return PairVerdict("NULL", f"{','.join(failed)} did not clear — one arm "
                                   "clearing is NOT a partial result", arms)
    return PairVerdict("HOLDS", "sign agrees and every arm clears independently", arms)


# ------------------------------------------------------------- sub-periods

SUB_PERIODS = (("P1", dt.date(2016, 1, 1), dt.date(2017, 12, 31)),
               ("P2", dt.date(2018, 1, 1), dt.date(2019, 12, 31)),
               ("P3", dt.date(2020, 1, 1), dt.date(2021, 12, 31)),
               ("P4", dt.date(2022, 1, 1), dt.date(2026, 12, 31)))


@dataclass(frozen=True)
class PeriodResult:
    name: str
    n_days: int
    value: float
    threshold: float
    contribution: float          # signed share of the total effect

    @property
    def clears(self) -> bool:
        return abs(self.value) >= self.threshold


def sub_periods(day_values: dict, sigma_day: float) -> tuple:
    """`day_values` = {date: mean R that day}. Threshold per period is
    mde(sigma_day, N_days_p) — the repo's own formula, honest about the fact that
    a short period needs a bigger effect to be detectable. sigma_day is computed
    once on the FULL window so the bar does not move with a period's own noise."""
    if not day_values:
        raise PreregError("no days supplied")
    total = float(np.sum(list(day_values.values())))
    out = []
    for name, lo, hi in SUB_PERIODS:
        vals = [v for d, v in day_values.items() if lo <= d <= hi]
        if not vals:
            out.append(PeriodResult(name, 0, float("nan"), float("inf"), 0.0)); continue
        s = float(np.sum(vals))
        out.append(PeriodResult(name=name, n_days=len(vals), value=float(np.mean(vals)),
                                threshold=float(mde(sigma_day, len(vals))),
                                contribution=(s / total) if total else float("nan")))
    return tuple(out)


def sub_period_verdict(results: tuple) -> tuple[str, str]:
    """Sign agrees 4/4, magnitude clears in >=3, no period over half the effect."""
    live = [r for r in results if r.n_days > 0]
    if len(live) < len(SUB_PERIODS):
        missing = [r.name for r in results if r.n_days == 0]
        return "NULL", f"no days in {','.join(missing)} — cannot check sign agreement"
    signs = {1 if r.value > 0 else (-1 if r.value < 0 else 0) for r in live}
    if len(signs) > 1 or 0 in signs:
        return "NULL", "sign does not agree in all four sub-periods"
    n_clear = sum(1 for r in live if r.clears)
    if n_clear < 3:
        return "NULL", f"magnitude cleared in only {n_clear} of 4 (need >=3)"
    worst = max(live, key=lambda r: abs(r.contribution))
    if abs(worst.contribution) > 0.5:
        return "NULL", (f"{worst.name} carries {worst.contribution:.0%} of the total effect "
                        "(>half) — that is a regime artifact, not an edge")
    return "HOLDS", f"sign 4/4, magnitude {n_clear}/4, max period share {abs(worst.contribution):.0%}"


# --------------------------------------------------- max-statistic null

def max_stat_null(cell_ids: np.ndarray, outcomes: np.ndarray, day_index: np.ndarray,
                  *, min_days: int, draws: int | None = None) -> dict:
    """Circular day-shift null for "the best cell by chance" (spec 049 §2.1).

    cell_ids / outcomes / day_index are parallel arrays over candidate slots.
    Slots are aligned across days (verified: every session carries every grid
    timestamp), so rotating the OUTCOME vector against the CELL-ASSIGNMENT vector
    by whole days destroys the cell->outcome link while preserving both
    within-day structure and cross-cell correlation. Bonferroni over hundreds of
    correlated cells respects neither.
    """
    days = np.unique(day_index)
    n_days = len(days)
    if n_days < 3:
        raise PreregError(f"need >=3 days to shift, got {n_days}")
    order = np.argsort(day_index, kind="stable")
    cells_o, out_o, day_o = cell_ids[order], outcomes[order], day_index[order]
    counts = np.array([np.sum(day_o == d) for d in days])
    if len(set(counts.tolist())) != 1:
        raise PreregError(
            f"slots per day are ragged ({sorted(set(counts.tolist()))[:5]}…) — the "
            "day-shift null requires aligned slots; mask illegal candidates BEFORE "
            "permuting, never after")
    per = int(counts[0])
    C = cells_o.reshape(n_days, per)
    O = out_o.reshape(n_days, per)

    def best(cells2d, outs2d):
        flat_c, flat_o = cells2d.ravel(), outs2d.ravel()
        uniq, inv = np.unique(flat_c, return_inverse=True)
        sums = np.bincount(inv, weights=flat_o, minlength=len(uniq))
        ns = np.bincount(inv, minlength=len(uniq))
        # a cell must have min_days DISTINCT DAYS, not min_days rows
        dcount = np.array([len(np.unique(day_o[flat_c == u])) for u in uniq])
        ok = dcount >= min_days
        if not ok.any():
            return float("nan"), 0
        return float(np.max(sums[ok] / ns[ok])), int(ok.sum())

    observed, n_valued = best(C, O)
    shifts = range(1, n_days) if draws is None else \
        np.random.default_rng(20260830).choice(np.arange(1, n_days), size=min(draws, n_days - 1), replace=False)
    null = [best(C, np.roll(O, s, axis=0))[0] for s in shifts]
    null = np.array([x for x in null if x == x])
    p = float((np.sum(null >= observed) + 1) / (len(null) + 1))
    return {"observed_max_cell_mean": observed, "n_cells_valued": n_valued,
            "n_days": n_days, "slots_per_day": per, "draws": len(null),
            "null_p95": float(np.percentile(null, 95)) if len(null) else float("nan"),
            "p_value": p, "survives": bool(p < 0.05)}
