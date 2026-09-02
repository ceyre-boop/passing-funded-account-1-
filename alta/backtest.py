"""alta/backtest.py — pessimistic fills, session-flat, costs charged.

FILL CONVENTION
    Adverse extreme first, then favourable. When a bar could have hit both the
    stop and the target, the STOP wins. Intrabar path is unknowable at 5-minute
    resolution and assuming the good outcome is how backtests lie.

ENTRY
    At the CLOSE of the confirmed-rejection bar. Never at the extreme. The bar
    has already printed when the decision is made.

COSTS
    Round trip charged in R at entry: spread + commission, expressed against
    the trade's own risk. Stated up front, never used as a pass condition.
"""
from __future__ import annotations

import numpy as np

SPREAD_BP = 1.0        # SPY/QQQ one-way, generous for a 1c-wide quote
COMMISSION_PER_SHARE = 0.0035


def run_trade(h, l, c, s, i_end):
    """One setup to its resolution. Returns (R, exit_kind, bars_held)."""
    d, e, stop, tgt, risk = s["direction"], s["entry"], s["stop"], s["target"], s["risk"]
    for t in range(s["entry_i"] + 1, i_end):
        adverse = l[t] if d > 0 else h[t]
        favour = h[t] if d > 0 else l[t]
        if (adverse <= stop) if d > 0 else (adverse >= stop):
            return (stop - e) * d / risk, "STOP", t - s["entry_i"]
        if (favour >= tgt) if d > 0 else (favour <= tgt):
            return (tgt - e) * d / risk, "TARGET", t - s["entry_i"]
    return (c[i_end - 1] - e) * d / risk, "SESSION_CLOSE", i_end - 1 - s["entry_i"]


def cost_r(s):
    """Round-trip friction expressed in the trade's own R units."""
    px = s["entry"]
    dollars = 2 * (px * SPREAD_BP / 10_000.0 + COMMISSION_PER_SHARE)
    return dollars / s["risk"]


def backtest(sessions, params, detect_fn):
    """Every session, every setup. Returns a list of trade dicts."""
    out = []
    for day, ch in sessions:
        o, h, l, c, v = [ch[x].to_numpy(float) for x in
                         ("Open", "High", "Low", "Close", "Volume")]
        for s in detect_fn(o, h, l, c, v, params):
            r, kind, held = run_trade(h, l, c, s, len(c))
            out.append(dict(day=str(day), r_gross=r, r=r - cost_r(s), kind=kind,
                            bars=held, n_conf=s["n_conf"], conf=s["conf"],
                            direction=s["direction"], entry=s["entry"],
                            risk=s["risk"]))
    return out


def stats(trades):
    if not trades:
        return None
    r = np.array([t["r"] for t in trades])
    w = r > 0
    n_w, n_l = int(w.sum()), int((~w).sum())
    avg_w = r[w].mean() if n_w else 0.0
    avg_l = -r[~w].mean() if n_l else 0.0
    payoff = (avg_w / avg_l) if avg_l > 0 else float("inf")
    W = w.mean()
    return dict(n=len(r), mean_r=float(r.mean()), win_rate=float(W),
                payoff=float(payoff),
                breakeven=float((1 - W) / W) if W > 0 else float("inf"),
                pf=float(r[w].sum() / -r[~w].sum()) if n_l and r[~w].sum() < 0 else float("inf"),
                sharpe=float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if len(r) > 1 and r.std() > 0 else 0.0)
