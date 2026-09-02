"""alta/momentum.py — five scanner strategies, one risk unit, one scorecard.

WHAT MAKES THEM COMPARABLE
    Each strategy defines only its ENTRY CONDITION. The stop is uniformly
    1 x ATR14 and the target uniformly 1R, so the scorecard measures the one
    thing that differs: does this condition precede a favourable move.

    A strategy that "works" only because it uses a wider stop is not better,
    it is differently sized. Holding risk constant is what makes "which one
    worked best" a real question rather than an accounting artifact.

NOT IMPLEMENTED, AND WHY
    SHORT SQUEEZE  needs short interest, days-to-cover, float, cost-to-borrow.
                   None exist in this repo. Not approximated -- a proxy built
                   from price and volume would be a different hypothesis
                   wearing the name.
    HFT / MICRO    needs tape speed, bid-ask, Level 2, book imbalance. The data
                   is OHLCV only. Same refusal.
"""
from __future__ import annotations

import numpy as np

from alta.setup import atr, rsi


def daily_context(df):
    """Per-session facts derived from DAILY aggregation of the 5m tape.
    Every value is from sessions STRICTLY BEFORE the session it labels."""
    d = df.resample("1D").agg({"Open": "first", "High": "max", "Low": "min",
                               "Close": "last", "Volume": "sum"}).dropna()
    out = {}
    hi = d["High"].to_numpy()
    lo = d["Low"].to_numpy()
    cl = d["Close"].to_numpy()
    vol = d["Volume"].to_numpy()
    days = [x.date() for x in d.index]
    for i in range(1, len(days)):
        j0 = max(0, i - 252)
        b30 = max(0, i - 30)
        b40 = max(0, i - 40)
        out[days[i]] = dict(
            prev_close=float(cl[i - 1]),
            adv20=float(vol[max(0, i - 20):i].mean()),
            high_52w=float(hi[j0:i].max()),
            box_high=float(hi[b40:i].max()),
            box_low=float(lo[b40:i].min()),
            sma10=float(cl[max(0, i - 10):i].mean()),
            sma10_prev=float(cl[max(0, i - 11):i - 1].mean()),
            rsi_daily=float(_rsi_last(cl[:i], 14)),
            daily_atr=float((hi[max(0, i - 14):i] - lo[max(0, i - 14):i]).mean()),
            consolidation_days=int(_consolidation(hi[b30:i], lo[b30:i])),
        )
    return out


def _rsi_last(c, n):
    r = rsi(c, n)
    return r[-1] if len(r) and not np.isnan(r[-1]) else 50.0


def _consolidation(h, l):
    """Days the range has stayed inside 1.5x its own median — a sideways proxy."""
    if len(h) < 5:
        return 0
    rng = h - l
    med = np.median(rng)
    return int((rng <= 1.5 * med).sum())


def _long(i, c, a, direction=1):
    risk = a[i]
    return dict(i=i, d=direction, entry=float(c[i]),
                stop=float(c[i] - direction * risk), risk=float(risk))


def _rth(idx):
    hh = idx.strftime("%H:%M")
    return (hh >= "09:30") & (hh <= "15:55")


# ---------------------------------------------------------------- S2

def gap_and_go(o, h, l, c, v, ctx, pre_vol, pre_high):
    """RVOL >= 3, gap >= +5%, pre-market volume >= 100k, price holds VWAP in
    the first 30 minutes. FLOAT IS NOT AVAILABLE and is not approximated."""
    if ctx is None or pre_vol is None:
        return []
    a = atr(h, l, 14)
    gap = (o[0] - ctx["prev_close"]) / ctx["prev_close"]
    if gap < 0.05 or pre_vol < 100_000:
        return []
    cum_v = np.cumsum(v)
    vwap = np.cumsum(c * v) / np.maximum(cum_v, 1)
    for i in range(3, min(7, len(c) - 2)):          # first ~30 min on 5m bars
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        rvol = cum_v[i] / max(1.0, ctx["adv20"] * (i + 1) / 78)
        if rvol >= 3.0 and c[i] > vwap[i] and c[i] > o[0]:
            return [_long(i, c, a)]
    return []


# ---------------------------------------------------------------- S3

def earnings_breakout(o, h, l, c, v, ctx, *_):
    """Daily volume > 3x ADV20, breaking a 4-8 week box, daily RSI 60-65 and
    not exhausted (<80). Trigger: first 15-min bar closing above the box."""
    if ctx is None:
        return []
    a = atr(h, l, 14)
    if not (60 <= ctx["rsi_daily"] <= 80):
        return []
    if v.sum() < 3.0 * ctx["adv20"]:
        return []
    for i in range(2, len(c) - 2):
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        if c[i] > ctx["box_high"]:
            return [_long(i, c, a)]
    return []


# ---------------------------------------------------------------- S4

def high_breakout(o, h, l, c, v, ctx, *_):
    """Within 2% of the 52-week high, >=30 days consolidation, RVOL >= 2,
    daily ATR >= $1.50, 10-SMA sloped up and below price."""
    if ctx is None:
        return []
    a = atr(h, l, 14)
    if ctx["daily_atr"] < 1.50 or ctx["consolidation_days"] < 30:
        return []
    if not (ctx["sma10"] > ctx["sma10_prev"]):
        return []
    cum_v = np.cumsum(v)
    for i in range(2, len(c) - 2):
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        rvol = cum_v[i] / max(1.0, ctx["adv20"] * (i + 1) / 78)
        near = c[i] >= 0.98 * ctx["high_52w"]
        if near and rvol >= 2.0 and c[i] > ctx["sma10"] and c[i] > ctx["high_52w"]:
            return [_long(i, c, a)]
    return []


# ---------------------------------------------------------------- S5

def relative_strength(o, h, l, c, v, ctx, bench_ret, _unused=None):
    """RS vs the benchmark > 1.5 on the session, price > 20EMA > 50SMA proxy,
    and a fresh high of day while the benchmark is flat or pulling back."""
    if ctx is None or bench_ret is None:
        return []
    a = atr(h, l, 14)
    for i in range(6, len(c) - 2):
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        own = (c[i] - o[0]) / o[0]
        bench = bench_ret(i)
        if bench is None:
            continue
        if own <= 0 or bench > 0.001:              # benchmark must be flat/down
            continue
        rs = own / abs(bench) if abs(bench) > 1e-6 else (own / 1e-6)
        hod = c[i] >= h[:i + 1].max() - 1e-9
        if rs > 1.5 and hod and c[i] > c[:i].mean():
            return [_long(i, c, a)]
    return []
