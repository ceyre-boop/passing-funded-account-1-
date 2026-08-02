#!/usr/bin/env python3
"""Corrections to eval_lab.py family-D (carry) cells, per protocol honesty rules:
1. Sealed-CSV haircut was accidentally applied twice -> single -0.004R/day swap haircut.
2. Carry TRUE OOS = 2025-01..2026-07 rig trades (data/oos_trades_2025_2026.json),
   NOT 2022-24 (inside carry's original development window). Starts 2025-01..2025-07.
3. Zero-edge arm for carry = mean-centered R series (price-centering never touched it).
A/B/C cells from eval_lab.py stand unchanged.
"""
import json, csv, numpy as np, pandas as pd

RNG = np.random.default_rng(42)
BASE = "/home/claude/passing-funded-account-1-"
HORIZON_TD = 365 * 5 // 7 + 1  # trading days
RISKS = [0.0025, 0.005, 0.0075, 0.010]
HAIRCUT_R_PER_DAY = 0.004  # -0.003%/day swap haircut / 0.75% risk unit

idx = pd.read_parquet(f"{BASE}/data/research/spot_cache/EURUSD_ohlc.parquet").sort_index().index
idx = idx[~idx.duplicated()]

def series_from(trades, center=False):
    inc = pd.Series(0.0, index=idx)
    rs = [t["R"] - HAIRCUT_R_PER_DAY * max(t["hold"], 1) for t in trades]
    if center:
        m = np.mean(rs); rs = [r - m for r in rs]
    for t, r in zip(trades, rs):
        xd = pd.Timestamp(t["exit"])
        if xd in inc.index: inc[xd] += r
        else:
            pos = inc.index.searchsorted(xd)
            if pos < len(inc): inc.iloc[pos] += r
    worst = inc.clip(upper=0.0)
    return inc, worst

sealed = []
with open(f"{BASE}/data/proof/backtest_trades_v015_2015_2024.csv") as f:
    for row in csv.DictReader(f):
        sealed.append(dict(exit=row["exit_date"][:10], R=float(row["pnl_pct"]) / float(row["risk_pct"]), hold=int(row["hold_days"])))
oos = [dict(exit=t["exit_date"], R=t["R"], hold=t["hold_days"]) for t in json.load(open(f"{BASE}/data/oos_trades_2025_2026.json"))]

def eval_run(vi, vw, risk, i0):
    bal = hwm = 1.0
    for t, i in enumerate(range(i0, len(vi))):
        if t >= HORIZON_TD: return "TIMEOUT"
        w = bal * (1 + risk * vw[i]); c = bal * (1 + risk * vi[i])
        if w < bal * 0.97: return "BUST_DAILY"
        if w <= max(hwm, c) - 0.06: return "BUST_MAX"
        bal = c; hwm = max(hwm, bal)
        if bal >= 1.06: return "PASS"
    return "TIMEOUT"

def sweep(inc, worst, d0, d1, risk):
    lo = inc.index.searchsorted(pd.Timestamp(d0)); hi = inc.index.searchsorted(pd.Timestamp(d1))
    starts = list(range(lo, hi, 5))
    res = [eval_run(inc.values, worst.values, risk, i) for i in starts]
    n = len(res)
    return dict(n=n, PASS=res.count("PASS")/n, DAILY=res.count("BUST_DAILY")/n, MAX=res.count("BUST_MAX")/n, TO=res.count("TIMEOUT")/n)

def boot(inc, worst, d0, d1, risk, npaths=2000, block=20):
    lo = inc.index.searchsorted(pd.Timestamp(d0)); hi = inc.index.searchsorted(pd.Timestamp(d1))
    vi, vw = inc.values[lo:hi], worst.values[lo:hi]
    hits = 0
    for _ in range(npaths):
        pi, pw = [], []
        while len(pi) < HORIZON_TD:
            s = RNG.integers(0, len(vi) - block)
            pi.extend(vi[s:s+block]); pw.extend(vw[s:s+block])
        hits += eval_run(np.array(pi[:HORIZON_TD]), np.array(pw[:HORIZON_TD]), risk, 0) == "PASS"
    return hits / npaths

for label, center in (("REAL", False), ("ZERO-EDGE (mean-centered R)", True)):
    si, sw = series_from(sealed, center)
    oi, ow = series_from(oos, center)
    print(f"\n=== CARRY, {label} ===")
    print(f"{'risk':>6} | train15-21 p(pass) (n) | TRUE OOS 2025-26 p(pass) (n) [daily/max/timeout] | OOS bootstrap")
    for r in RISKS:
        tr = sweep(si, sw, "2015-01-01", "2021-12-31", r)
        oo = sweep(oi, ow, "2025-01-01", "2025-07-15", r)
        bs = boot(oi, ow, "2025-01-01", "2026-07-03", r)
        print(f"{r*100:5.2f}% | {tr['PASS']:6.1%} ({tr['n']}) | {oo['PASS']:6.1%} ({oo['n']}) [{oo['DAILY']:.0%}/{oo['MAX']:.0%}/{oo['TO']:.0%}] | {bs:.1%}")
print("\nNOTE: OOS start-sweep windows overlap heavily; 17.5 months of OOS data ")
print("contains ~1.4 independent 12-month windows. Treat OOS p(pass) accordingly.")
