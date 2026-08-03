#!/usr/bin/env python3
"""Friction-adjusted one-shot ladder — answers the critique's ask: are busts
actually 'cheap' once real-world friction is modeled?

Friction modeled (the clean chart assumed none of this):
  - Not every day produces a qualifying shot: P(setup) = 60% per trading day
    (the 'no setup by 11:30 = no trade' filter). No-trade days burn calendar, not fees.
  - Weekends: 5 trading days / 7 calendar days.
  - TILT: after each consecutive bust, per-shot p drops 5pp (floor: base-10pp),
    resets after a pass or a 5-trading-day forced cooloff (which we trigger
    after 2 consecutive busts — the discipline circuit-breaker).
  - Fee budget is finite (campaign dies if exhausted). BOGO = 2 accounts/fee.
  - 150% refund of ONE fee on pass (per the promo), netted out of cost.
Outputs per (p, budget): P(funded before budget dies), median calendar days,
expected fees spent, expected net cost in fee-units after refund.
Fee left as a unit; multiply by the checkout price.
"""
import numpy as np

RNG = np.random.default_rng(7)
N = 200_000
P_SETUP = 0.60
TILT_DROP, TILT_FLOOR_DELTA = 0.05, 0.10
COOLOFF_AFTER, COOLOFF_DAYS = 2, 5
REFUND = 1.5

def run(p_base, fee_budget, bogo=True, tilt=True):
    funded = np.zeros(N, bool); days = np.zeros(N); fees = np.zeros(N)
    for i in range(N):
        attempts_avail = fee_budget * (2 if bogo else 1)
        fee_spent = 0; consec = 0; cool = 0; td = 0; acct = 0
        while True:
            td += 1
            if td > 400 * 5 // 7: break  # ~400 calendar days cap
            if cool > 0: cool -= 1; continue
            if RNG.random() > P_SETUP: continue
            if acct == 0:
                if attempts_avail == 0: break
                attempts_avail -= 1
                if bogo:
                    if (fee_budget * 2 - attempts_avail) % 2 == 1: fee_spent += 1
                else: fee_spent += 1
                acct = 1
            p_eff = p_base - (TILT_DROP * consec if tilt else 0)
            p_eff = max(p_eff, p_base - TILT_FLOOR_DELTA)
            if RNG.random() < p_eff:
                funded[i] = True; break
            acct = 0; consec += 1
            if tilt and consec >= COOLOFF_AFTER: cool = COOLOFF_DAYS; consec = 0
        days[i] = td * 7 / 5; fees[i] = fee_spent
    pf = funded.mean()
    med_days = np.median(days[funded]) if pf > 0 else float("nan")
    net = fees.mean() - REFUND * pf
    return pf, med_days, fees.mean(), net

print(f"{'p/shot':>7} {'budget':>7} {'tilt':>5} | {'P(funded)':>9} {'med days':>9} {'E[fees]':>8} {'net cost (fee units)':>20}")
for p in (0.31, 0.40, 0.45):
    for budget in (2, 4):
        for tilt in (True, False):
            pf, md, ef, net = run(p, budget, tilt=tilt)
            print(f"{p:7.0%} {budget:7d} {str(tilt):>5} | {pf:9.1%} {md:9.0f} {ef:8.2f} {net:20.2f}")
