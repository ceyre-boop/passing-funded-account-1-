#!/usr/bin/env python3
"""Instant Pro $10k rules sim — 3% daily loss, 6% max loss, sweep risk/trade.

Runs the sealed 411-trade carry series (2015-2024, true historical order,
every possible start index) through the exact Instant Pro limits, under BOTH
interpretations of "Maximum Loss 6%":
  - STATIC: floor fixed at $9,400 forever
  - TRAILING: floor = (high-water balance) - 6% of initial, EOD-updated,
    capped at initial balance (standard trailing-until-breakeven variant)
and also an uncapped trailing variant (worst case).

Daily loss: 3% of prior-day balance, close-basis. Trades held multi-day book
PnL on exit day only (carry exits at EOD; intraday floating DD not in sealed
data — stated as a limitation, not hidden).

Success metric: since Instant accounts have no profit-target/eval phase,
we measure survival + payout: P(survive 1yr), P(hit -6% bust), median PnL
at 12 months, and P(reaching +6% before busting) as a proxy first-payout.
"""
import csv, sys
from collections import defaultdict
from datetime import datetime, timedelta

CSV = "/home/claude/passing-funded-account-1-/data/proof/backtest_trades_v015_2015_2024.csv"

trades = []
with open(CSV) as f:
    for row in csv.DictReader(f):
        r = float(row["pnl_pct"]) / float(row["risk_pct"])  # R multiple
        trades.append((datetime.fromisoformat(row["exit_date"]), r))
trades.sort(key=lambda t: t[0])

INIT = 10_000.0

def run(start_idx, risk, mode, horizon_days=365):
    bal = INIT
    hwm = INIT
    day_start_bal = INIT
    seq = trades[start_idx:]
    if not seq: return None
    t0 = seq[0][0]
    cur_day = t0.date()
    hit_target = False
    for dt, r in seq:
        if (dt - t0).days > horizon_days:
            break
        if dt.date() != cur_day:
            day_start_bal = bal
            cur_day = dt.date()
        pnl = bal * risk * r
        # daily loss check (close basis)
        if pnl < 0 and (day_start_bal - (bal + pnl)) > 0.03 * day_start_bal:
            return ("BUST_DAILY", bal + pnl, hit_target)
        bal += pnl
        hwm = max(hwm, bal)
        if mode == "static":
            floor = INIT * 0.94
        elif mode == "trail_cap":
            floor = min(hwm - INIT * 0.06, INIT)
        else:  # trail
            floor = hwm - INIT * 0.06
        if bal <= floor:
            return ("BUST_MAX", bal, hit_target)
        if bal >= INIT * 1.06:
            hit_target = True
    return ("ALIVE", bal, hit_target)

print(f"{'mode':10s} {'risk':>5s} {'survive1y':>9s} {'bust':>6s} {'P(+6% first)':>12s} {'median 1y PnL':>13s}")
for mode in ("static", "trail_cap", "trail"):
    for risk in (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02):
        outs = [run(i, risk, mode) for i in range(len(trades))]
        outs = [o for o in outs if o]
        n = len(outs)
        alive = sum(1 for s, _, _ in outs if s == "ALIVE")
        bust = n - alive
        tgt = sum(1 for _, _, h in outs if h)
        pnls = sorted(b - INIT for s, b, _ in outs if s == "ALIVE")
        med = pnls[len(pnls)//2] if pnls else float("nan")
        print(f"{mode:10s} {risk*100:4.2f}% {alive/n:8.1%} {bust/n:5.1%} {tgt/n:11.1%} {med:12.0f}$")
