#!/usr/bin/env python3
"""Shotgun-the-evaluations EV, with the mandatory zero-edge control.

CLAUDE.md standing rule: no P(pass) is quotable without the zero-edge control
printed beside it. This computes both from the real contracts in
data/propfirm/firm_contracts.yaml.

Model: trade-level random walk. Each trade risks `risk` of the CURRENT balance,
wins `b`R with prob W, loses 1R otherwise, minus `cost` R of spread/commission
on every trade. Edge is expressed as expectancy in R BEFORE costs.
"""
import random, statistics as st

random.seed(20260903)
N_SIM = 20000
SPLIT = 0.80          # ASSUMPTION — not in firm_contracts.yaml. Industry standard.

FIRMS = {
    "cti_1step":   dict(fee=382.0, refund=0.0, targets=[0.08],
                        dd=0.05, dd_type="trailing", daily=None),
    "alpha_swing": dict(fee=490.0, refund=0.0, targets=[0.10, 0.05],
                        dd=0.10, dd_type="static", daily=0.05),
    "ftmo_swing":  dict(fee=501.0, refund=1.0, targets=[0.10, 0.05],
                        dd=0.10, dd_type="static", daily=0.05),
}

def trade(edge_r, b, cost):
    """One trade in R. W solved so pre-cost expectancy == edge_r."""
    W = (1.0 + edge_r) / (1.0 + b)
    return (b if random.random() < W else -1.0) - cost

def phase(target, dd, dd_type, daily, risk, edge_r, b, cost, cap=6000):
    """Return (passed, trades_used). Equity in fractions of start balance."""
    eq, peak, day_start, tday = 0.0, 0.0, 0.0, 0
    for t in range(cap):
        eq += trade(edge_r, b, cost) * risk
        peak = max(peak, eq)
        floor = (peak - dd) if dd_type == "trailing" else -dd
        if eq <= floor:
            return False, t + 1
        if daily is not None:
            tday += 1
            if tday >= 5:                       # ~5 trades per session
                if eq - day_start <= -daily:
                    return False, t + 1
                day_start, tday = eq, 0
        if eq >= target:
            return True, t + 1
    return False, cap

def evaluate(f, risk, edge_r, b, cost):
    for tgt in f["targets"]:
        ok, _ = phase(tgt, f["dd"], f["dd_type"], f["daily"], risk, edge_r, b, cost)
        if not ok:
            return False
    return True

def funded_life(f, risk, edge_r, b, cost, cap=20000):
    """Total GROSS profit withdrawn before the account dies. Withdraw at +5%,
    which resets equity to 0 and (for static DD) restores the full buffer."""
    eq, peak, withdrawn = 0.0, 0.0, 0.0
    for _ in range(cap):
        eq += trade(edge_r, b, cost) * risk
        peak = max(peak, eq)
        floor = (peak - f["dd"]) if f["dd_type"] == "trailing" else -f["dd"]
        if eq <= floor:
            break
        if eq >= 0.05:
            withdrawn += eq
            eq, peak = 0.0, 0.0
    return withdrawn

def run(edge_r, risk, b=2.0, cost=0.0, acct=100_000.0):
    out = {}
    for name, f in FIRMS.items():
        pas = sum(evaluate(f, risk, edge_r, b, cost) for _ in range(N_SIM)) / N_SIM
        lives = [funded_life(f, risk, edge_r, b, cost) for _ in range(2000)]
        gross = st.fmean(lives) * acct
        net_payout = gross * SPLIT + (f["fee"] * f["refund"] if gross > 0 else 0)
        cost_to_fund = f["fee"] / pas if pas > 0 else float("inf")
        out[name] = dict(p=pas, cost_to_fund=cost_to_fund,
                         payout=net_payout, ev=net_payout - cost_to_fund)
    return out
