#!/usr/bin/env python3
"""scripts/phase0_conditioning.py — spec 047. Does ANY conditioner make the entry positive?

Median config is the statistic. Best config is never the statistic (see
artifacts/CEILING_10Y_RECORD.md). Label-shuffle null, 1000 draws, seed 20260829.
Marginal conditioners only — no crossing.
"""
from __future__ import annotations

import datetime as dt, json, sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))
import bars                                                                      # noqa: E402
from bars import ET, MIN_SESSION_BARS_5M, RTH_CLOSE, RTH_OPEN, Session, _internal_gaps  # noqa: E402
from ceiling import find_entry, simulate, wide_space                             # noqa: E402
from splits import TUNE_END                                                      # noqa: E402

SEED, DRAWS, MIN_CELL = 20260829, 1000, 30


def load(sym, sub):
    df = pd.read_parquet(ROOT / f"data/daytrade/{sub}/{sym}_5m.parquet")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    t = df.index.strftime("%H:%M"); rth = df[(t >= RTH_OPEN) & (t <= RTH_CLOSE)]
    out = []
    for day, ch in rth.groupby(rth.index.date):
        if len(ch) < MIN_SESSION_BARS_5M or _internal_gaps(ch, "5m"): continue
        out.append(Session(sym, day, ch))
    cut = dt.date.fromisoformat(str(TUNE_END))
    return [s for s in out if s.day < cut]


def fomc_days():
    j = json.load(open(ROOT / "data/daytrade/fomc_calendar.json"))
    rows = j["events"] if isinstance(j, dict) and "events" in j else (list(j.values())[0] if isinstance(j, dict) else j)
    ds = set()
    for r in rows:
        d = r.get("date") or r.get("day") if isinstance(r, dict) else r
        if d: ds.add(dt.date.fromisoformat(str(d)[:10]))
    return ds


def build(sym, sub):
    sess = load(sym, sub); fomc = fomc_days(); space = wide_space(); names = list(space)
    prev_close, rows, R = None, [], []
    for s in sess:
        e = find_entry(s)
        if e is not None:
            d = s.day
            prox = "day_of" if d in fomc else ("pm1" if any((d + dt.timedelta(days=k)) in fomc for k in (-1, 1)) else "none")
            rows.append({"day": str(d), "or_width": e.risk / e.entry, "fomc": prox,
                         "tblock": e.time_block, "dirn": "long" if e.direction > 0 else "short",
                         "gap": (s.df.iloc[0]["Open"] / prev_close - 1.0) if prev_close else np.nan})
            R.append([simulate(s, e, dict(space[c])) for c in names])
        prev_close = float(s.df.iloc[-1]["Close"])
    return pd.DataFrame(rows), np.array(R, dtype=float), names


def terciles(v, labels=("low", "mid", "high")):
    q = np.nanquantile(v, [1/3, 2/3])
    return pd.cut(v, [-np.inf, q[0], q[1], np.inf], labels=list(labels)).astype(object)


def cell_stats(R, idx):
    sub = R[idx]                      # entries x configs
    tot = sub.sum(axis=0); n = len(idx)
    return dict(n=n, median_per_trade=float(np.median(tot))/n, best_per_trade=float(tot.max())/n,
                frac_cfg_profitable=float((tot > 0).mean()),
                span_per_trade=float(sub.max(axis=1).sum() - sub.min(axis=1).sum())/n)


def run(sym="SPY", sub="bars_premarket"):
    df, R, names = build(sym, sub)
    df["or_width_t"] = terciles(df.or_width.values)
    df["gap_t"] = terciles(df.gap.values)
    conds = {"C1_or_width": "or_width_t", "C2_fomc": "fomc", "C3_time_block": "tblock",
             "C4_direction": "dirn", "C5_gap": "gap_t"}
    rng = np.random.default_rng(SEED); out = {}
    print(f"{sym}: {len(df)} entries x {len(names)} configs\n")
    print(f"{'conditioner':<14} {'level':<11} {'n':>5} {'median/tr':>10} {'null 5-95%':>19} {'cnt':>4} {'cfg>0':>6} {'span/tr':>8} {'null span':>19} {'cnt':>4}")
    print("-" * 118)
    for cname, col in conds.items():
        labels = df[col].values
        levels = [l for l in pd.unique(labels) if l == l]
        obs = {l: cell_stats(R, np.where(labels == l)[0]) for l in levels}
        nulls = {l: {"m": [], "s": []} for l in levels}
        for _ in range(DRAWS):
            perm = rng.permutation(labels)
            for l in levels:
                i = np.where(perm == l)[0]
                if len(i) == 0: continue
                st = cell_stats(R, i); nulls[l]["m"].append(st["median_per_trade"]); nulls[l]["s"].append(st["span_per_trade"])
        for l in levels:
            o = obs[l]
            if o["n"] < MIN_CELL:
                print(f"{cname:<14} {str(l):<11} {o['n']:>5}   EXCLUDED (< {MIN_CELL} entries, spec 047)"); continue
            ml, mh = np.percentile(nulls[l]["m"], [5, 95]); sl, sh = np.percentile(nulls[l]["s"], [5, 95])
            mc = "OUT" if (o["median_per_trade"] < ml or o["median_per_trade"] > mh) else "-"
            sc = "OUT" if (o["span_per_trade"] < sl or o["span_per_trade"] > sh) else "-"
            print(f"{cname:<14} {str(l):<11} {o['n']:>5} {o['median_per_trade']:>+10.4f} "
                  f"[{ml:>+8.4f},{mh:>+8.4f}] {mc:>4} {o['frac_cfg_profitable']:>5.0%} "
                  f"{o['span_per_trade']:>8.3f} [{sl:>8.3f},{sh:>8.3f}] {sc:>4}")
            out[f"{cname}:{l}"] = {**o, "null_median_5_95": [ml, mh], "null_span_5_95": [sl, sh],
                                   "median_counts": mc == "OUT", "span_counts": sc == "OUT"}
        print()
    lives = [k for k, v in out.items() if v["median_counts"] and v["median_per_trade"] > 0]
    mag = [k for k, v in out.items() if v["span_counts"]]
    res = {"symbol": sym, "n_entries": int(len(df)), "n_configs": len(names), "seed": SEED, "draws": DRAWS,
           "cells": out, "direction_lives": lives, "magnitude_counts": mag,
           "verdict_direction": "DIRECTION LIVES" if lives else "DIRECTION DEAD",
           "verdict_magnitude": "MAGNITUDE CONDITIONABLE" if mag else "MAGNITUDE FLAT"}
    (ROOT / "artifacts" / f"phase0_{sym}.json").write_text(json.dumps(res, indent=2, default=str) + "\n")
    print(f"DIRECTION: {res['verdict_direction']}  {lives if lives else ''}")
    print(f"MAGNITUDE: {res['verdict_magnitude']}  ({len(mag)} cells outside the null band)")
    return res


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "SPY",
        sys.argv[2] if len(sys.argv) > 2 else "bars_premarket")
