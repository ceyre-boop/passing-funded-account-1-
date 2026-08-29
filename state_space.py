"""
state_space.py — Stockfish core, layer 0.

Everything downstream (tablebase, backward induction, NNUE target) keys off this
module. Two jobs:

  1. discretize()  — continuous position snapshot -> finite hashed state
  2. audit()       — does the discretization leave enough paths per cell to
                     support backward induction, or is the tablebase mostly air?

Job 2 is the gate. A chess tablebase is exhaustive; a market tablebase is a
sample. If the median cell holds 3 paths, the value function is noise wearing a
lookup table's clothes. Run audit() before writing a line of induction code.
"""

from __future__ import annotations

from dataclasses import dataclass, astuple
from typing import Iterable, Sequence
import bisect
import math

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Bucket edges. Deliberately exposed and coarse-first — audit() sweeps these.
# ---------------------------------------------------------------------------

R_EDGES = (-1.0, -0.5, -0.15, 0.15, 0.5, 1.0, 2.0)          # unrealized R
HOLD_EDGES = (1, 3, 8, 20, 60)                               # bars held
ATR_EDGES = (0.7, 0.9, 1.15, 1.5)                            # atr_now / atr_entry
CARRY_EDGES = (-0.5, 0.5)                                    # accrued swap in R

TIME_BLOCKS = ("PREOPEN", "OPEN_DRIVE", "MORNING", "MIDDAY", "AFTERNOON", "CLOSE")


def _bucket(x: float, edges: Sequence[float]) -> int:
    """Index of x within edges. NaN -> -1 so it is never silently merged."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return -1
    return bisect.bisect_right(edges, x)


@dataclass(frozen=True, slots=True)
class State:
    r_b: int
    hold_b: int
    atr_b: int
    carry_b: int
    time_block: str
    weekend: bool

    def key(self) -> tuple:
        return astuple(self)

    def __str__(self) -> str:
        return "|".join(str(v) for v in self.key())


def discretize(
    unrealized_r: float,
    bars_held: int,
    atr_ratio: float,
    carry_r: float,
    time_block: str,
    weekend_exposure: bool,
    *,
    r_edges: Sequence[float] = R_EDGES,
    hold_edges: Sequence[float] = HOLD_EDGES,
    atr_edges: Sequence[float] = ATR_EDGES,
    carry_edges: Sequence[float] = CARRY_EDGES,
) -> State:
    if time_block not in TIME_BLOCKS:
        raise ValueError(f"unknown time_block {time_block!r}; expected one of {TIME_BLOCKS}")
    return State(
        r_b=_bucket(unrealized_r, r_edges),
        hold_b=_bucket(bars_held, hold_edges),
        atr_b=_bucket(atr_ratio, atr_edges),
        carry_b=_bucket(carry_r, carry_edges),
        time_block=time_block,
        weekend=bool(weekend_exposure),
    )


def discretize_frame(df: pd.DataFrame, **edge_overrides) -> pd.Series:
    """Vectorized-ish wrapper. df needs the six snapshot columns."""
    required = {
        "unrealized_r", "bars_held", "atr_ratio",
        "carry_r", "time_block", "weekend_exposure",
    }
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"snapshot frame missing columns: {sorted(missing)}")

    return df.apply(
        lambda row: discretize(
            row.unrealized_r,
            row.bars_held,
            row.atr_ratio,
            row.carry_r,
            row.time_block,
            row.weekend_exposure,
            **edge_overrides,
        ),
        axis=1,
    )


# ---------------------------------------------------------------------------
# Occupancy audit
# ---------------------------------------------------------------------------

@dataclass
class Occupancy:
    granularity: str
    reachable_states: int          # cells with >=1 observation
    observations: int
    median_per_state: float
    p10_per_state: float
    frac_states_thin: float        # cells below min_paths
    frac_obs_in_thin: float        # share of real observations living in them
    independent_episodes: int      # distinct trades, not snapshots

    def verdict(self) -> str:
        if self.frac_obs_in_thin > 0.35:
            return "FAIL — most of the tablebase would be fitted on <min_paths samples"
        if self.frac_obs_in_thin > 0.15:
            return "MARGINAL — coarsen, or pool cells before induction"
        return "OK"


def audit(
    df: pd.DataFrame,
    *,
    episode_col: str = "trade_id",
    min_paths: int = 30,
    granularity: str = "full",
    **edge_overrides,
) -> Occupancy:
    """
    df: one row per open-position snapshot, plus `trade_id` linking snapshots
        belonging to the same trade.

    min_paths=30 is the point where a per-cell mean has any claim to stability.
    Tune it, but tune it before you look at the answer, not after.
    """
    if episode_col not in df.columns:
        raise KeyError(
            f"{episode_col!r} required — snapshot counts overstate sample size "
            "because snapshots within one trade are the same bet observed repeatedly"
        )

    states = discretize_frame(df, **edge_overrides)
    counts = states.value_counts()  # unit = snapshot, not episode/trade_id (known bug, Colin's call — left as-is)

    thin = counts[counts < min_paths]
    obs_in_thin = int(thin.sum())

    return Occupancy(
        granularity=granularity,
        reachable_states=int(counts.size),
        observations=int(counts.sum()),
        median_per_state=float(counts.median()),
        p10_per_state=float(np.percentile(counts.values, 10)),
        frac_states_thin=float(thin.size / counts.size) if counts.size else 1.0,
        frac_obs_in_thin=float(obs_in_thin / counts.sum()) if counts.sum() else 1.0,
        independent_episodes=int(df[episode_col].nunique()),
    )


COARSENINGS = {
    "full": {},
    "coarse_r": {"r_edges": (-0.5, 0.15, 1.0)},
    "coarse_atr": {"atr_edges": (0.9, 1.3)},
    "coarse_both": {"r_edges": (-0.5, 0.15, 1.0), "atr_edges": (0.9, 1.3)},
    "minimal": {
        "r_edges": (-0.5, 0.15, 1.0),
        "atr_edges": (0.9, 1.3),
        "hold_edges": (3, 20),
        "carry_edges": (0.0,),
    },
}


def sweep(df: pd.DataFrame, *, min_paths: int = 30, **kw) -> pd.DataFrame:
    """Run audit() at every coarsening. Pick the finest grid that is still OK."""
    rows = []
    for name, overrides in COARSENINGS.items():
        occ = audit(df, min_paths=min_paths, granularity=name, **overrides, **kw)
        rows.append({**occ.__dict__, "verdict": occ.verdict()})
    return pd.DataFrame(rows).set_index("granularity")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        sys.exit("usage: python state_space.py <snapshots.parquet|csv>")

    path = sys.argv[1]
    frame = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)

    print(f"\nsnapshots: {len(frame):,}   trades: {frame.trade_id.nunique():,}\n")
    with pd.option_context("display.width", 160, "display.max_columns", None):
        print(sweep(frame))
    print(
        "\nRead the frac_obs_in_thin column. That is the fraction of the "
        "tablebase you would be inventing.\n"
    )