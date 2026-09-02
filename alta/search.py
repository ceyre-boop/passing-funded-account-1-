#!/usr/bin/env python3
"""alta/search.py — find "the gradual slow and shift", honestly.

THE PROTOCOL, WHICH IS THE WHOLE POINT
    The parameter hunt runs on TRAIN ONLY (2016-2022). Every variant tried is
    COUNTED -- that count IS the trial number a deflated Sharpe must be
    penalised by. The single train winner is then evaluated on HOLDOUT
    (2023-2026), which the search never saw.

    Searching is legitimate. Reporting the maximum of a search as if it were a
    single test is not. "The refinement is the overfitting" -- so the refinement
    is allowed and the count is carried.

WHAT IS SEARCHED
    Only the entry trigger and the geometry it needs. The structure -- impulse,
    fair value gap, lower high, confirmed rejection -- is frozen and not
    searched, because that is the method, not a parameter.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alta.backtest import backtest, stats  # noqa: E402
from alta.setup import Params, detect  # noqa: E402

# 192 variants, not 2,592. A smaller grid is BETTER here, not a compromise:
# the grid size IS the multiple-testing penalty a deflated Sharpe pays, so
# every variant added makes the survivor harder to believe. Coarse and honest
# beats fine and deflated into nothing.
GRID = dict(
    impulse_atr=[2.0, 2.5],
    rsi_extreme=[60.0, 70.0],
    slow_bars=[2, 3],
    no_new_extreme=[2, 3],
    stop_atr=[0.5, 1.0],
    target_frac=[1.0, 1.5, 2.0],
    max_wait=[12, 26],
)
MIN_TRADES = 60          # a variant with fewer is not evaluated, declared here


def sessions(sym):
    df = pd.read_parquet(ROOT / f"data/daytrade/bars_premarket/{sym}_5m.parquet")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    hh = df.index.strftime("%H:%M")
    rth = df[(hh >= "09:30") & (hh <= "15:55")]
    return [(d, c) for d, c in rth.groupby(rth.index.date) if len(c) >= 40]


def main() -> int:
    s = sessions("SPY")
    tr = [x for x in s if x[0].year < 2023]
    ho = [x for x in s if x[0].year >= 2023]
    keys = list(GRID)
    combos = list(itertools.product(*[GRID[k] for k in keys]))
    print(f"SEARCH — train {len(tr)} sessions, holdout {len(ho)} sessions (sealed)")
    print(f"  {len(combos)} variants. That number is N_eff and it is carried.\n")

    rows = []
    for n, vals in enumerate(combos, 1):
        p = Params(**dict(zip(keys, vals)))
        st = stats(backtest(tr, p, detect))
        if st and st["n"] >= MIN_TRADES:
            rows.append({**dict(zip(keys, vals)), **st})
        if n % 500 == 0:
            print(f"  {n}/{len(combos)}")

    rows.sort(key=lambda r: -r["mean_r"])
    print(f"\n  {len(rows)} variants cleared the {MIN_TRADES}-trade minimum")
    print(f"\n  TOP 5 ON TRAIN (selection happens here and nowhere else)")
    print(f"  {'mean R':>8}{'n':>6}{'win':>7}{'payoff':>8}{'need':>7}{'PF':>7}  params")
    for r in rows[:5]:
        prm = {k: r[k] for k in keys}
        print(f"  {r['mean_r']:>+8.4f}{r['n']:>6}{r['win_rate']:>7.1%}"
              f"{r['payoff']:>8.2f}{r['breakeven']:>7.2f}{r['pf']:>7.3f}  {prm}")

    best = rows[0]
    bp = Params(**{k: best[k] for k in keys})
    out = ROOT / "data" / "alta_search.json"
    out.write_text(json.dumps(
        {"n_variants_tried": len(combos), "n_evaluated": len(rows),
         "best_params": {k: best[k] for k in keys},
         "best_train": {k: best[k] for k in ("n", "mean_r", "win_rate", "payoff",
                                             "breakeven", "pf", "sharpe")},
         "train_mean_r_distribution": {
             "max": rows[0]["mean_r"], "p90": float(np.percentile([r["mean_r"] for r in rows], 90)),
             "median": float(np.median([r["mean_r"] for r in rows])),
             "min": rows[-1]["mean_r"]}}, indent=1))
    print(f"\n  best params sealed to {out.name}. Holdout NOT touched by this script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
