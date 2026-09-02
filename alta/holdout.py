#!/usr/bin/env python3
"""alta/holdout.py — the winner, evaluated once, plus a $1k leveraged account.

The train winner is applied to 2023-2026, which the search never saw. Then the
same trade list is run through a real account: whole shares, 4:1 day-trading
buying power, risk-based sizing, costs in dollars.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alta.backtest import (COMMISSION_PER_SHARE, SPREAD_BP, backtest,  # noqa: E402
                           stats)
from alta.search import sessions  # noqa: E402
from alta.setup import Params, detect  # noqa: E402

START_EQUITY = 1_000.0
LEVERAGE = 4.0            # standard pattern-day-trader buying power
RISK_FRAC = 0.02          # 2% of equity risked per trade


def account_sim(trades, start=START_EQUITY, leverage=LEVERAGE, risk_frac=RISK_FRAC):
    eq = start
    curve, capped, skipped = [start], 0, 0
    for t in trades:
        want = (eq * risk_frac) / t["risk"]                 # shares by risk budget
        allowed = (eq * leverage) / t["entry"]              # shares by buying power
        shares = int(min(want, allowed))
        if shares < 1:
            skipped += 1
            curve.append(eq)
            continue
        if want > allowed:
            capped += 1
        gross = t["r_gross"] * t["risk"] * shares
        cost = shares * (2 * (t["entry"] * SPREAD_BP / 10_000.0 + COMMISSION_PER_SHARE))
        eq += gross - cost
        curve.append(eq)
        if eq <= 0:
            break
    c = np.array(curve)
    peak = np.maximum.accumulate(c)
    dd = float(((c - peak) / peak).min()) if len(c) > 1 else 0.0
    return dict(final=float(c[-1]), curve=c, max_dd=dd, capped=capped,
                skipped=skipped, n=len(trades))


def main() -> int:
    cfg = json.loads((ROOT / "data" / "alta_search.json").read_text())
    p = Params(**cfg["best_params"])
    n_eff = cfg["n_variants_tried"]
    s = sessions("SPY")
    ho = [x for x in s if x[0].year >= 2023]

    print("HOLDOUT — the train winner, evaluated once on data the search never saw\n")
    print(f"  params: {cfg['best_params']}")
    print(f"  N_eff (variants tried on train): {n_eff}")
    print(f"  train result of this variant: mean R {cfg['best_train']['mean_r']:+.4f} "
          f"(the BEST of {n_eff})\n")

    tr_all = backtest(ho, p, detect)
    st = stats(tr_all)
    print(f"  {'':<10}{'n':>6}{'mean R':>10}{'win':>8}{'payoff':>8}{'need':>7}"
          f"{'gap':>8}{'PF':>7}")
    print(f"  {'HOLDOUT':<10}{st['n']:>6}{st['mean_r']:>+10.4f}{st['win_rate']:>8.1%}"
          f"{st['payoff']:>8.2f}{st['breakeven']:>7.2f}"
          f"{st['payoff'] - st['breakeven']:>+8.2f}{st['pf']:>7.3f}")

    # confluence monotonicity — the claim "more confluence is better"
    print(f"\n  CONFLUENCE MONOTONICITY  (the claim: more agreement, better outcome)")
    print(f"  {'n_conf':>7}{'trades':>8}{'mean R':>10}{'win':>8}")
    for k in sorted({t["n_conf"] for t in tr_all}):
        sub = [t for t in tr_all if t["n_conf"] == k]
        r = np.array([t["r"] for t in sub])
        print(f"  {k:>7}{len(sub):>8}{r.mean():>+10.4f}{(r > 0).mean():>8.1%}")

    # the account
    print(f"\n  $1,000 ACCOUNT — {LEVERAGE:g}:1 buying power, {RISK_FRAC:.0%} risk/trade, "
          f"whole shares")
    a = account_sim(tr_all)
    print(f"    final equity       ${a['final']:,.2f}   from ${START_EQUITY:,.0f}")
    print(f"    max drawdown       {a['max_dd']:.1%}")
    print(f"    trades taken       {a['n'] - a['skipped']} of {a['n']}"
          f"   ({a['skipped']} too small to size)")
    print(f"    size-capped by leverage: {a['capped']} trades")
    for eq0 in (10_000.0, 25_000.0):
        b = account_sim(tr_all, start=eq0)
        print(f"    ${eq0:>7,.0f} start -> ${b['final']:>10,.2f}   maxDD {b['max_dd']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
