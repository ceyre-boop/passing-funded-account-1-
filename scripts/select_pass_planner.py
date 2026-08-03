#!/usr/bin/env python3
"""Tradeify $25K Select pass planner (SPEC FOR MOLLY, executed).

Confirmed rules (help.tradeify.co, 2026-08-03): target $1,500; $1,000 EOD
trailing DD, locks at $25,100 EOD; NO daily loss limit in eval; consistency
40% = biggest EOD PnL <= 40% of total profit AT PASS-REQUEST TIME (delays
pass, never breaches); >=3 days; max 1 mini / 10 micros.
NOTE: spec's day-by-day reading is wrong per TOS wording (day 1 would always
be 100% and nothing could ever pass). Solver uses the real rule.

Part 1 (spec 1+5): deterministic day tables, T in $300-600 step $50, 4-7 days.
Part 2 (spec 6): Monte Carlo realism — ORB trigger rate 60%/day, win rate
p in {45,50,55}%, green day = +T, red day = -T/2.14 (THE_SHOT geometry:
target 2.14R so R = T/2.14), flat days = $0. Tracks EOD trailing floor
(locks at +$100), days-to-pass distribution, bust probability.
Honest label: trigger/win rates are ASSUMPTIONS (no intraday data in this
sandbox); the deterministic tables are arithmetic, the MC is a sanity check,
neither is a backtest.
"""
import numpy as np
RNG = np.random.default_rng(11)
TARGET, DD, LOCK = 1500.0, 1000.0, 100.0
RR = 2.14  # THE_SHOT: +6%/2.8% => target = 2.14R

print("=" * 76)
print("PART 1 — deterministic equal-day tables (correct 40%-at-pass rule)")
print("=" * 76)
for nd in (4, 5, 6, 7):
    print(f"\n--- {nd}-day plan ---")
    print(f"{'T/day':>6} {'total':>7} {'maxday%':>8} {'pass?':>6} {'risk/trade':>10} {'worst-case cushion note'}")
    for T in range(300, 601, 50):
        tot = nd * T
        share = 1 / nd
        ok = tot >= TARGET and share <= 0.40
        R = T / RR
        print(f"${T:5d} ${tot:6d} {share:7.1%} {'PASS' if ok else 'no':>6} ${R:9.0f}  first red day leaves ${DD - R:.0f} of $1000")
    # day-by-day cushion table for the minimal passing T
    T = max(300, int(np.ceil(TARGET / nd / 50) * 50))
    print(f"  recommended T=${T}: day-by-day [day, target, cum, day% of cum, 40% check, cushion]")
    cum, hwm = 0.0, 0.0
    for d in range(1, nd + 1):
        cum += T
        hwm = max(hwm, cum)
        floor = min(hwm - DD, LOCK)  # floor relative to start; locks at +100
        cushion = cum - floor
        share_now = T / cum
        print(f"    day {d}: +${T}  cum ${cum:.0f}  {share_now:5.1%}  {'ok' if share_now <= 0.4 or d < 3 else 'ok(at-pass rule)'}  cushion ${cushion:.0f}")

print()
print("=" * 76)
print("PART 2 — Monte Carlo realism check (assumed trigger 60%, THE_SHOT sizing)")
print("=" * 76)
N = 100_000
print(f"{'T/day':>6} {'winrate':>8} | {'P(pass<=10td)':>13} {'P(pass<=20td)':>13} {'med days':>8} {'P(bust)':>8}")
for T in (400, 450, 500):
    R = T / RR
    for p in (0.45, 0.50, 0.55):
        passed10 = passed20 = bust = 0
        dtp = []
        for _ in range(N):
            cum, hwm, maxday, locked = 0.0, 0.0, 0.0, False
            for day in range(1, 41):
                pnl = 0.0
                if RNG.random() < 0.60:
                    pnl = T if RNG.random() < p else -R
                cum += pnl
                hwm = max(hwm, cum)
                if not locked and hwm >= LOCK: locked = True
                floor = 0.0 - DD + (hwm if not locked else LOCK) if not locked else LOCK - DD
                floor = (hwm - DD) if not locked else (LOCK - DD)
                if cum <= floor: bust += 1; break
                maxday = max(maxday, pnl)
                if cum >= TARGET and maxday <= 0.40 * cum and day >= 3:
                    dtp.append(day)
                    if day <= 10: passed10 += 1
                    if day <= 20: passed20 += 1
                    break
        med = int(np.median(dtp)) if dtp else -1
        print(f"${T:5d} {p:7.0%} | {passed10/N:12.1%} {passed20/N:13.1%} {med:8d} {bust/N:7.1%}")
print("\nNOTE: 'days' are TRADING days incl. flat no-trigger days. 4-7 GREEN days")
print("!= 4-7 calendar days: at 60% trigger x ~50% win, a green day arrives ~1 in 3.")
