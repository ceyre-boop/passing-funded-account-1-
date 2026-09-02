#!/usr/bin/env python3
"""scripts/momentum_compare.py — five strategies, identical risk, one table."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alta.momentum import (daily_context, earnings_breakout,  # noqa: E402
                           gap_and_go, high_breakout, relative_strength)
from alta.setup import atr  # noqa: E402
from alta.simple import cost_r, resolve, signals as s1_signals  # noqa: E402

SYMS = ("SPY", "QQQ")


def load(sym):
    df = pd.read_parquet(ROOT / f"data/daytrade/bars_premarket/{sym}_5m.parquet")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    return df


def score(name, fn, data, needs_bench=False):
    rs, mfes = [], []
    for sym in SYMS:
        df, ctxs, other = data[sym]
        # Pre-group the benchmark once. Filtering it inside the session loop was
        # O(n^2) over 2,677 sessions x 500k rows and never finished.
        bench_by_day = {}
        if needs_bench:
            bdf = data[other][0]
            bh = bdf.index.strftime("%H:%M")
            brth = bdf[(bh >= "09:30") & (bh <= "15:55")]
            bench_by_day = {d: g["Close"].to_numpy(float)
                            for d, g in brth.groupby(brth.index.date)}
        hh = df.index.strftime("%H:%M")
        rth = df[(hh >= "09:30") & (hh <= "15:55")]
        pre = df[(hh >= "04:00") & (hh < "09:30")]
        pv = pre.groupby(pre.index.date)["Volume"].sum().to_dict()
        ph = pre.groupby(pre.index.date)["High"].max().to_dict()
        for day, ch in rth.groupby(rth.index.date):
            if len(ch) < 40:
                continue
            o, h, l, c, v = [ch[x].to_numpy(float) for x in
                             ("Open", "High", "Low", "Close", "Volume")]
            a = atr(h, l, 14)
            ctx = ctxs.get(day)
            bench = None
            if needs_bench:
                bc = bench_by_day.get(day)          # pre-grouped ONCE, not per session
                if bc is not None and len(bc) >= 10:
                    bo = float(bc[0])
                    bench = lambda i, bc=bc, bo=bo: (
                        (float(bc[min(i, len(bc) - 1)]) - bo) / bo)
            for s in fn(o, h, l, c, v, ctx, bench if needs_bench else pv.get(day),
                        ph.get(day)):
                # UNIFORM RISK. Every strategy is scored on a 1 ATR stop so the
                # comparison measures the entry condition, not the sizing. S1
                # natively uses a tighter swing stop; without this floor it
                # would be compared on a different risk unit and its worse
                # numbers would be an accounting artifact.
                s = dict(s)
                floor = a[s["i"]]
                if s["risk"] < floor:
                    s["stop"] = s["entry"] - s["d"] * floor
                    s["risk"] = floor
                r, _ = resolve(h, l, c, s, 1.0)
                rs.append(r - cost_r(s))
                d, e, risk, stop = s["d"], s["entry"], s["risk"], s["stop"]
                best = 0.0
                for t in range(s["i"] + 1, len(c)):
                    adv = h[t] if d < 0 else l[t]
                    if (adv >= stop) if d < 0 else (adv <= stop):
                        break
                    fav = l[t] if d < 0 else h[t]
                    best = max(best, (fav - e) * d / risk)
                mfes.append(best)
    if len(rs) < 20:
        return dict(name=name, n=len(rs), insufficient=True)
    r = np.array(rs)
    m = np.array(mfes)
    w = r > 0
    aw = r[w].mean() if w.any() else 0.0
    al = -r[~w].mean() if (~w).any() else 1.0
    W = float(w.mean())
    return dict(name=name, n=len(r), win=W, payoff=float(aw / al),
                need=float((1 - W) / W) if W > 0 else np.inf,
                gap=float(aw / al - (1 - W) / W) if W > 0 else -np.inf,
                mean_r=float(r.mean()), mfe=float(np.median(m)),
                insufficient=False)


def main() -> int:
    data = {}
    for i, sym in enumerate(SYMS):
        df = load(sym)
        data[sym] = (df, daily_context(df), SYMS[1 - i])

    rows = [score("S1 impulse fade", lambda o, h, l, c, v, *a: s1_signals(o, h, l, c, v), data),
            score("S2 gap-and-go", gap_and_go, data),
            score("S3 earnings breakout", earnings_breakout, data),
            score("S4 52-week breakout", high_breakout, data),
            score("S5 relative strength", relative_strength, data, needs_bench=True)]

    print("MOMENTUM STRATEGY COMPARISON — SPY+QQQ, 2016-2026")
    print("identical risk (1 ATR stop), identical target (1R), identical costs\n")
    print(f"  {'strategy':<24}{'n':>6}{'win%':>8}{'payoff':>8}{'need':>7}"
          f"{'GAP':>8}{'mean R':>9}{'medMFE':>8}")
    for r in sorted(rows, key=lambda x: (x.get("insufficient", True),
                                         -(x.get("mean_r") or -9))):
        if r["insufficient"]:
            print(f"  {r['name']:<24}{r['n']:>6}   too few signals to score")
            continue
        print(f"  {r['name']:<24}{r['n']:>6}{r['win']:>8.1%}{r['payoff']:>8.2f}"
              f"{r['need']:>7.2f}{r['gap']:>+8.2f}{r['mean_r']:>+9.3f}{r['mfe']:>8.2f}")
    print("\n  NOT TESTED — data absent, and not approximated:")
    print("    short squeeze : no short interest / float / days-to-cover / borrow")
    print("    HFT micro     : no tick, quote, Level 2, or book data")
    print("    A proxy built from price and volume would be a different")
    print("    hypothesis wearing the name.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
