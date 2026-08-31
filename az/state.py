#!/usr/bin/env python3
"""az/state.py — Gate 1: the entry state representation. Spec 048.

Six as-of-computable dimensions describing the market at a candidate entry
moment, plus a mechanical lookahead guard and an occupancy audit that counts
DAYS, not rows.

Why days: 44 candidates share a trading day and are one bet. Counting rows is the
error that made a Phase 1 grid read 4.4%-thin when it was 57.5%-thin by trade,
and it is the doc's own risk #2 ("day count, not row count").

No LLM-derived feature may enter this module (spec 048 I79). Everything here is
arithmetic over bars at or before the candidate timestamp.
"""
from __future__ import annotations

import bisect
import math
import sys
from collections import defaultdict
from dataclasses import astuple, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daytrade"))

from ceiling import time_block  # noqa: E402  — one implementation of the phase labels


class LookaheadError(RuntimeError):
    """A feature moved when data after the candidate timestamp changed.
    Never downgraded to a warning."""


class StateError(ValueError):
    pass


# Spec 048 §2 — the declared grid. Not floating.
GRID_HHMM = ("09:30", "10:00", "10:30", "11:00", "11:30", "12:00",
             "12:30", "13:00", "13:30", "14:00", "14:30")
MIN_FORWARD_MIN = 60          # a candidate needs this much path to be gradeable

# Spec 048 §4 — granularities declared BEFORE the run.
GRANULARITIES = {
    "coarse": dict(vol=(0.008,), expansion=(0.0,), trend=(0.0,), gap=(0.0,), vwap=(0.0,)),
    "medium": dict(vol=(0.006, 0.012), expansion=(-0.15, 0.15), trend=(-0.5, 0.5),
                   gap=(-0.5, 0.5), vwap=(-0.5, 0.5)),
    "fine":   dict(vol=(0.004, 0.008, 0.014), expansion=(-0.3, 0.0, 0.3),
                   trend=(-1.0, 0.0, 1.0), gap=(-1.0, 0.0, 1.0), vwap=(-1.0, 0.0, 1.0)),
}
MIN_DAYS = 30                 # a cell is VALUED at >= this many DISTINCT DAYS
VALUED_THRESHOLD = 0.85       # spec 048 §4, pre-declared


def _bucket(x: float, edges) -> int:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return -1              # never silently merged with a real bucket
    return bisect.bisect_right(edges, float(x))


@dataclass(frozen=True, slots=True)
class EntryState:
    vol_b: int
    expansion_b: int
    trend_b: int
    gap_b: int
    vwap_b: int
    time_block: str

    def key(self):
        return astuple(self)


# --------------------------------------------------------------------------
# Features. Every one reads bars at or before t, and nothing else.
# --------------------------------------------------------------------------

def _atr(df: pd.DataFrame, n: int) -> float:
    if len(df) < 2:
        return float("nan")
    w = df.tail(n)
    return float((w["High"].astype(float) - w["Low"].astype(float)).mean())


def raw_features(hist: pd.DataFrame, prior_close: float | None) -> dict:
    """`hist` is every bar at or BEFORE the candidate timestamp. Nothing else is
    in scope — that is what makes the lookahead guard able to prove anything."""
    if len(hist) < 3:
        raise StateError(f"need >=3 bars at or before t, got {len(hist)}")
    close = float(hist["Close"].iloc[-1])
    atr14 = _atr(hist, 14)
    if not (atr14 > 0) or not math.isfinite(atr14):
        raise StateError(f"ATR14 is {atr14!r} — refusing to build a state on it")
    short, long_ = _atr(hist, 12), _atr(hist, 78)
    expansion = math.log(short / long_) if short > 0 and long_ > 0 else float("nan")
    c = hist["Close"].astype(float).to_numpy()
    trend = ((c[-1] - c[max(0, len(c) - 24)]) / atr14) if len(c) >= 2 else float("nan")
    gap = ((float(hist["Open"].iloc[0]) / prior_close - 1.0) * close / atr14
           if prior_close else float("nan"))
    tp = (hist["High"].astype(float) + hist["Low"].astype(float) + c) / 3.0
    vol = hist["Volume"].astype(float)
    vwap = float((tp * vol).sum() / vol.sum()) if vol.sum() > 0 else close
    return {"vol": atr14 / close, "expansion": expansion, "trend": trend,
            "gap": gap, "vwap": (close - vwap) / atr14}


def discretize(raw: dict, hhmm: str, edges: dict) -> EntryState:
    return EntryState(
        vol_b=_bucket(raw["vol"], edges["vol"]),
        expansion_b=_bucket(raw["expansion"], edges["expansion"]),
        trend_b=_bucket(raw["trend"], edges["trend"]),
        gap_b=_bucket(raw["gap"], edges["gap"]),
        vwap_b=_bucket(raw["vwap"], edges["vwap"]),
        time_block=time_block(hhmm),
    )


# --------------------------------------------------------------------------
# The lookahead guard (spec 048 §5, invariant I76)
# --------------------------------------------------------------------------

def truncate_at(df: pd.DataFrame, t_hhmm: str) -> pd.DataFrame:
    """Bars at or BEFORE t. The single seam through which every feature sees data.
    Named and separate on purpose: it is the thing the lookahead guard tests, and
    a bug here is precisely how the future gets in."""
    return df[df.index.strftime("%H:%M") <= t_hhmm]


def assert_no_lookahead(session_df: pd.DataFrame, t_hhmm: str,
                        prior_close: float | None, *, rng_seed: int = 20260830) -> dict:
    """Compute the features, then CORRUPT every bar strictly after t and compute
    again. Identical or it raises. A feature that moves has read the future."""
    base = raw_features(truncate_at(session_df, t_hhmm), prior_close)

    corrupt = session_df.copy()
    after = session_df.index.strftime("%H:%M") > t_hhmm
    if after.any():
        rng = np.random.default_rng(rng_seed)
        # ONE factor per bar, shared across O/H/L/C. Corrupting each column
        # independently can invert High/Low and make ATR negative, which raises
        # StateError before the guard can compare — the corruption must produce a
        # bar that is still well-formed, just wrong.
        factor = rng.uniform(3.0, 9.0, size=int(after.sum()))
        for col in ("Open", "High", "Low", "Close", "Volume"):
            vals = corrupt[col].astype(float).to_numpy().copy()
            vals[after] = vals[after] * factor
            corrupt[col] = vals
    after_feats = raw_features(truncate_at(corrupt, t_hhmm), prior_close)

    moved = {k: (base[k], after_feats[k]) for k in base
             if not (math.isnan(base[k]) and math.isnan(after_feats[k]))
             and base[k] != after_feats[k]}
    if moved:
        raise LookaheadError(
            f"feature(s) changed when post-{t_hhmm} bars were corrupted: {moved} "
            "— they are reading data that does not exist yet")
    return base


# --------------------------------------------------------------------------
# Occupancy — DAYS per cell (invariant I77)
# --------------------------------------------------------------------------

@dataclass
class Occupancy:
    granularity: str
    cells: int
    cells_valued: int
    days: int
    candidates: int
    frac_candidates_in_valued_cells: float
    min_days: int

    def verdict(self) -> str:
        f = self.frac_candidates_in_valued_cells
        return "OK" if f >= VALUED_THRESHOLD else ("MARGINAL" if f >= 0.65 else "FAIL")


def audit(rows: list[dict], granularity: str, edges: dict, *, min_days: int = MIN_DAYS) -> Occupancy:
    """`rows` = [{day, hhmm, raw}]. A cell is VALUED at >= min_days DISTINCT DAYS."""
    by_cell_days: dict[tuple, set] = defaultdict(set)
    by_cell_n: dict[tuple, int] = defaultdict(int)
    for r in rows:
        k = discretize(r["raw"], r["hhmm"], edges).key()
        by_cell_days[k].add(r["day"])
        by_cell_n[k] += 1
    valued = {k for k, d in by_cell_days.items() if len(d) >= min_days}
    in_valued = sum(n for k, n in by_cell_n.items() if k in valued)
    return Occupancy(granularity=granularity, cells=len(by_cell_days), cells_valued=len(valued),
                     days=len({r["day"] for r in rows}), candidates=len(rows),
                     frac_candidates_in_valued_cells=in_valued / max(len(rows), 1),
                     min_days=min_days)
