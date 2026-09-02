#!/usr/bin/env python3
"""scripts/strategy_scorecard.py — one metric set, every strategy, forever.

WHY THIS EXISTS
    A strategy described in prose cannot be compared to another strategy
    described in different prose. This produces the SAME numbers in the SAME
    order for anything that can emit (entry, stop, direction) signals, so
    strategy 2 is measurable against strategy 1 without an argument about
    which metrics matter.

THE METRIC SET, fixed
    n · win rate · payoff · breakeven payoff · gap · mean R · profit factor
    · median MFE · P(reach 1R/2R/3R) · median cost in R

    `gap` = payoff - (1-W)/W is the headline. Positive means the reward
    covers the hit rate; negative means it does not, and by how much.

    Median MFE is reported because it is the ceiling on everything else: no
    target above it can be hit more than half the time, whatever the exit does.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alta.setup import atr  # noqa: E402
from alta.search import sessions  # noqa: E402


def score(signal_fn, symbols=("SPY", "QQQ"), atr_floor=1.0, target_r=1.0):
    """`signal_fn(o,h,l,c,v) -> [{i, d, entry, stop, risk}]`."""
    from alta.simple import cost_r, resolve
    rs, mfes, costs = [], [], []
    for sym in symbols:
        for _day, ch in sessions(sym):
            o, h, l, c, v = [ch[x].to_numpy(float) for x in
                             ("Open", "High", "Low", "Close", "Volume")]
            a = atr(h, l, 14)
            for s0 in signal_fn(o, h, l, c, v):
                s = dict(s0)
                floor = atr_floor * a[s0["i"]]
                if atr_floor and s["risk"] < floor:
                    s["stop"] = s["entry"] - s["d"] * floor
                    s["risk"] = floor
                ck = cost_r(s)
                r, _ = resolve(h, l, c, s, target_r)
                rs.append(r - ck)
                costs.append(ck)
                d, e, risk, stop = s["d"], s["entry"], s["risk"], s["stop"]
                best = 0.0
                for t in range(s["i"] + 1, len(c)):
                    adv = h[t] if d < 0 else l[t]
                    if (adv >= stop) if d < 0 else (adv <= stop):
                        break
                    fav = l[t] if d < 0 else h[t]
                    best = max(best, (fav - e) * d / risk)
                mfes.append(best)
    r = np.array(rs)
    m = np.array(mfes)
    w = r > 0
    aw = float(r[w].mean()) if w.any() else 0.0
    al = float(-r[~w].mean()) if (~w).any() else 1.0
    W = float(w.mean())
    payoff = aw / al if al > 0 else float("inf")
    need = (1 - W) / W if W > 0 else float("inf")
    return dict(n=int(len(r)), win_rate=W, payoff=payoff, breakeven=need,
                gap=payoff - need, mean_r=float(r.mean()),
                pf=float(r[w].sum() / -r[~w].sum()) if (~w).any() else float("inf"),
                median_mfe=float(np.median(m)),
                p_reach_1r=float((m >= 1).mean()),
                p_reach_2r=float((m >= 2).mean()),
                p_reach_3r=float((m >= 3).mean()),
                median_cost_r=float(np.median(costs)))


def render(name, s):
    print(f"  {name}")
    print(f"    n {s['n']}   win {s['win_rate']:.1%}   payoff {s['payoff']:.2f}   "
          f"needed {s['breakeven']:.2f}   GAP {s['gap']:+.2f}")
    print(f"    mean R {s['mean_r']:+.3f}   PF {s['pf']:.3f}   "
          f"cost {s['median_cost_r']:.3f}R")
    print(f"    median MFE {s['median_mfe']:.2f}R   reaches 1R {s['p_reach_1r']:.1%} "
          f"| 2R {s['p_reach_2r']:.1%} | 3R {s['p_reach_3r']:.1%}")


if __name__ == "__main__":
    from alta.simple import signals
    s = score(signals)
    print("STRATEGY SCORECARD\n")
    render("S1 — impulse fade (RSI exhaustion at a lower high)", s)
    out = ROOT / "strategies" / "S1_impulse_fade.json"
    doc = json.loads(out.read_text()) if out.exists() else {}
    doc["scorecard"] = s
    out.write_text(json.dumps(doc, indent=1))
    print(f"\n  -> {out.relative_to(ROOT)}")
