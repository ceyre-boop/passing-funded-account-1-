#!/usr/bin/env python3
"""DRAWDOWN MARGIN — the realized curve against the real contract.

WHY THIS EXISTS, AND WHY IT IS NOT quantstats
---------------------------------------------
The buy gate (spec 021) already simulates firm-rule breach across ladder paths
and returns PASS/BUST/UNRESOLVED. What it never reports is the MARGIN on the one
curve that actually happened: how close did the sealed series come to the daily
limit and the max-drawdown floor, and at what risk does it stop clearing them.

That question is the one a prop firm is actually decided on, and a returns-based
tearsheet library cannot answer it. quantstats/pyfolio compute drawdown from a
returns series with no notion of:
  - a floor trailing a balance HIGH-WATER mark rather than a fixed start,
  - close-marking vs intraday-marking of that floor,
  - a daily budget measured from each day's OPEN balance,
  - phase targets and min-trading-days.
Every one of those is in the contract and changes the answer. So this reuses
carry_buy_gate.build_series and run_phase's exact marking conventions rather
than adding a library that would have to be corrected back into agreement.

G4 in spec 021 is named FIT but only checks PERMISSIONS (weekend/overnight/news).
This is the other half of fit, and it is deliberately a separate report rather
than a new gate — adding a gate changes the pre-registered BUY verdict, which is
a ruling, not a refactor.

    python3 scripts/drawdown_margin.py --firm cti_1step --risk 0.01
    python3 scripts/drawdown_margin.py --firm alpha_swing --series oos
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.carry_buy_gate import build_series, load_oos, load_sealed  # noqa: E402
from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402

OUT = ROOT / "data" / "agent" / "drawdown_margin.json"


def walk(vi, vw, vopen, contract, risk: float) -> dict:
    """One pass over the REALIZED curve. Marking mirrors run_phase exactly.

    Returns the worst observed daily loss and drawdown as fractions of the
    account, plus where they happened and whether a floor was breached.
    """
    daily = contract.daily_dd
    dd = contract.max_dd
    bal = peak = 1.0
    worst_daily = 0.0
    worst_daily_i = -1
    worst_floor = 0.0            # contract-relative: proximity to the real floor
    worst_floor_i = -1
    worst_ptt = 0.0              # strategy property: peak-to-trough, always
    worst_ptt_i = -1
    breach = None

    for i in range(len(vi)):
        day_start = bal
        intraday = bal * (1.0 + risk * vw[i])
        close = bal * (1.0 + risk * vi[i])

        if daily is not None:
            marked = intraday if daily.mark == "intraday" else close
            loss = day_start - marked            # fraction of account, as run_phase
            if loss > worst_daily:
                worst_daily, worst_daily_i = loss, i
            if breach is None and marked <= day_start - daily.pct:
                breach = {"kind": "daily", "i": i, "loss": float(loss)}

        dd_base = peak if dd.type == "trailing" else 1.0
        marked = intraday if dd.mark == "intraday" else close

        # (a) CONTRACT-RELATIVE. Drives breach. For a STATIC rule the base is the
        # starting balance, so once the account grows this measures distance
        # below the start, NOT a drawdown — which is exactly right for a fixed
        # floor and exactly wrong as a drawdown statistic. Hence (b).
        floor_depth = (dd_base - marked) / dd_base
        if floor_depth > worst_floor:
            worst_floor, worst_floor_i = floor_depth, i
        if breach is None and marked <= dd_base * (1.0 - dd.pct):
            breach = {"kind": "max_dd", "i": i, "depth": float(floor_depth)}

        # (b) PEAK-TO-TROUGH, always measured from the running high-water mark
        # regardless of contract type. This is the strategy's own drawdown
        # profile and the quantity the half-allowance heuristic is about.
        ptt = (peak - marked) / peak
        if ptt > worst_ptt:
            worst_ptt, worst_ptt_i = ptt, i

        bal = close
        peak = max(peak, bal)

    return {
        "risk": risk,
        "final_balance": float(bal),
        "peak_balance": float(peak),
        "worst_daily_loss": float(worst_daily),
        "worst_daily_i": int(worst_daily_i),
        "worst_floor_depth": float(worst_floor),
        "worst_floor_depth_i": int(worst_floor_i),
        "worst_peak_to_trough": float(worst_ptt),
        "worst_peak_to_trough_i": int(worst_ptt_i),
        "breach": breach,
    }


def max_safe_risk(vi, vw, vopen, contract, lo=0.0001, hi=0.10, tol=1e-5) -> float:
    """Largest risk fraction at which the realized curve never breaches.

    Monotone in risk for a fixed curve: scaling risk scales every excursion, so
    a bisection is valid. Returns 0.0 if even `lo` breaches.
    """
    if walk(vi, vw, vopen, contract, lo)["breach"] is not None:
        return 0.0
    if walk(vi, vw, vopen, contract, hi)["breach"] is None:
        return hi
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if walk(vi, vw, vopen, contract, mid)["breach"] is None:
            lo = mid
        else:
            hi = mid
    return lo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--firm", default="cti_1step")
    ap.add_argument("--series", choices=("sealed", "oos"), default="sealed")
    ap.add_argument("--risk", type=float, default=0.01)
    a = ap.parse_args(argv)

    contract = load_contract(a.firm)
    trades = load_sealed() if a.series == "sealed" else load_oos()
    idx, vi, vw, vopen = build_series(
        trades, contract.costs.swap_haircut_r_per_day, center=False)
    r = walk(vi, vw, vopen, contract, a.risk)

    dd, daily = contract.max_dd, contract.daily_dd
    print("=" * 70)
    print(f"DRAWDOWN MARGIN — {contract.display_name}")
    print("=" * 70)
    print(f"  series      : {a.series}  n={len(trades)} trades, "
          f"{len(idx)} business days  {idx[0].date()} .. {idx[-1].date()}")
    print(f"  rules_asof  : {contract.rules_asof}")
    print(f"  risk        : {a.risk:.2%} of account per R")
    print()

    # ---- daily limit -----------------------------------------------------
    if daily is None:
        print(f"  DAILY LOSS LIMIT : none in this contract.")
        print(f"    {contract.key} has no daily rule, so 'worst daily vs limit'")
        print(f"    has no denominator here. Not reported as a pass — reported")
        print(f"    as absent, because a margin against a rule that does not")
        print(f"    exist is not evidence of anything.")
        daily_row = {"present": False}
    else:
        wd, lim = r["worst_daily_loss"], daily.pct
        used = wd / lim
        print(f"  DAILY LOSS LIMIT : {lim:.2%} ({daily.mark}-marked, "
              f"basis {daily.basis})")
        print(f"    worst realized : {wd:.3%} on {idx[r['worst_daily_i']].date()}")
        print(f"    margin used    : {used:.1%} of the daily allowance")
        print(f"    verdict        : {'WELL UNDER' if used < 0.5 else 'NEAR THE LIMIT' if used < 1 else 'BREACHED'}")
        daily_row = {"present": True, "limit": lim, "worst": wd,
                     "fraction_used": used}

    # ---- max drawdown ----------------------------------------------------
    wfl, lim = r["worst_floor_depth"], dd.pct
    used = wfl / lim
    wptt = r["worst_peak_to_trough"]
    ptt_used = wptt / lim
    print()
    print(f"  MAX DRAWDOWN     : {lim:.2%} {dd.type} ({dd.mark}-marked)")
    print(f"    closest to floor : {wfl:.3%} on "
          f"{idx[r['worst_floor_depth_i']].date()}  "
          f"({used:.1%} of the allowance)")
    if dd.type == "static":
        print(f"      ^ measured from the FIXED start balance, not a high-water")
        print(f"        mark. Once the account grows this stops being a drawdown")
        print(f"        number and becomes floor distance. Correct for breach,")
        print(f"        not comparable to the line below.")
    print(f"    peak-to-trough : {wptt:.3%} on "
          f"{idx[r['worst_peak_to_trough_i']].date()}  "
          f"({ptt_used:.1%} of the allowance)")
    print(f"    half-allowance : {'PASS' if ptt_used < 0.5 else 'FAIL'}  "
          f"(peak-to-trough under HALF the max — the strategy-profile test)")

    # ---- breach ----------------------------------------------------------
    print()
    if r["breach"] is None:
        print(f"  BREACH           : none at {a.risk:.2%}")
    else:
        b = r["breach"]
        print(f"  BREACH           : {b['kind'].upper()} on "
              f"{idx[b['i']].date()} — the curve does NOT survive "
              f"{a.risk:.2%} under this contract")

    # ---- the sizing answer ----------------------------------------------
    safe = max_safe_risk(vi, vw, vopen, contract)
    print()
    print(f"  MAX SAFE RISK    : {safe:.3%} per R")
    print(f"    Largest risk at which this realized curve never trips a floor.")
    print(f"    NOT a sizing recommendation — one path's survival is a ceiling,")
    print(f"    not an estimate. Ratification still runs through spec 021.")

    print()
    print(f"  final balance    : {r['final_balance']:.4f}  "
          f"(peak {r['peak_balance']:.4f})")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "firm": contract.key, "display_name": contract.display_name,
        "rules_asof": contract.rules_asof, "series": a.series,
        "n_trades": len(trades), "n_days": len(idx),
        "span": [str(idx[0].date()), str(idx[-1].date())],
        "risk": a.risk, "daily": daily_row,
        "max_dd": {"limit": dd.pct, "type": dd.type, "mark": dd.mark,
                   "worst_floor_depth": r["worst_floor_depth"],
                   "floor_fraction_used": used,
                   "worst_peak_to_trough": r["worst_peak_to_trough"],
                   "ptt_fraction_used": ptt_used,
                   "under_half": bool(ptt_used < 0.5)},
        "breach": r["breach"], "max_safe_risk": safe,
        "final_balance": r["final_balance"],
    }, indent=1))
    print(f"\n  written: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
