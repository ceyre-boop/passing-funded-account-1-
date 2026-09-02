"""alta/setup.py — the mechanical form of the traded method.

THE METHOD, AS NARRATED (GTLB, 2026-09-02)
    A large move happens. You are too late for it. It leaves an imbalance --
    a fair value gap -- and a low-volume shelf that price will want to revisit.
    Price pushes back up, but to a LOWER high than the impulse extreme: the
    manipulation. It slows, cannot physically push higher, rolls over. You
    enter on that rejection, short, and target the gap below.

WHAT IS MECHANICAL HERE AND WHAT IS NOT
    Frozen, no discretion: impulse, FVG, low-volume node, lower high, prior
    shelf. Those are geometry.

    The judgement lives in ONE place -- "the gradual slow and shift" -- and it
    is the entry trigger. That is the thing being searched, and it is why the
    search is counted and validated out of sample rather than reported as
    found.

THE HAZARD THIS FILE REFUSES
    The stated goal is "get as high as possible." An entry AT the extreme is
    unbackestable: it picks the top with hindsight and produces a beautiful,
    untradeable curve. So entry is always on CONFIRMED REJECTION -- a bar that
    has already closed away from the extreme. Every fill is at a price that had
    already printed when the decision was made.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Params:
    """Everything searchable lives here. Nothing else is tuned."""
    impulse_atr: float = 3.0        # move >= k*ATR to count as the big move
    impulse_bars: int = 8           # over this many bars
    rsi_period: int = 14
    rsi_extreme: float = 70.0       # overbought/oversold gate at the manipulation
    slow_bars: int = 3              # consecutive bars of decay = "gradual slow"
    no_new_extreme: int = 3         # bars without extending the extreme
    max_wait: int = 26              # bars after impulse to find the entry
    stop_atr: float = 1.0           # stop beyond the manipulation extreme
    target_frac: float = 0.5        # fraction of the FVG to target


def atr(h, l, n):
    tr = h - l
    out = np.full(len(tr), np.nan)
    if len(tr) >= n:
        c = np.cumsum(tr, dtype=float)
        out[n - 1:] = np.concatenate([[c[n - 1]], c[n:] - c[:-n]]) / n
    return out


def rsi(c, n):
    d = np.diff(c, prepend=c[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    au, ad = up[1:n + 1].mean(), dn[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        au = (au * (n - 1) + up[i]) / n
        ad = (ad * (n - 1) + dn[i]) / n
        out[i] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def find_fvgs(h, l, i):
    """Bearish FVG at bar i: low[i-2] > high[i] — a gap left by an up-impulse
    that price must come back down through. Returns (top, bottom) or None."""
    if i < 2:
        return None
    if l[i - 2] > h[i]:
        return (l[i - 2], h[i])
    return None


def detect(o, h, l, c, v, p: Params):
    """Yield one dict per valid setup. Every field is computable at `entry_i`
    from bars at or before it."""
    n = len(c)
    a = atr(h, l, 14)
    r = rsi(c, p.rsi_period)
    out = []
    i = max(20, p.rsi_period + 2)
    while i < n - 2:
        # --- 1. the impulse: a large move over impulse_bars
        j = i - p.impulse_bars
        if j < 0 or np.isnan(a[i]) or a[i] <= 0:
            i += 1
            continue
        move = c[i] - c[j]
        if abs(move) < p.impulse_atr * a[i]:
            i += 1
            continue
        direction = -1 if move > 0 else 1     # fade the impulse
        imp_extreme = h[j:i + 1].max() if move > 0 else l[j:i + 1].min()

        # --- 2. the imbalance it left
        gap = None
        for k in range(j, i + 1):
            g = find_fvgs(h, l, k) if move > 0 else (
                (l[k], h[k - 2]) if k >= 2 and h[k - 2] < l[k] else None)
            if g:
                gap = g
        if gap is None:
            i += 1
            continue

        # --- 3. the manipulation: a LOWER high (or higher low) after the impulse
        entry = None
        for t in range(i + 1, min(i + 1 + p.max_wait, n - 1)):
            ext = h[i + 1:t + 1].max() if move > 0 else l[i + 1:t + 1].min()
            if move > 0 and ext >= imp_extreme:
                break                       # made a new high — not a lower high
            if move < 0 and ext <= imp_extreme:
                break
            if np.isnan(r[t]):
                continue
            # --- 4. THE GRADUAL SLOW AND SHIFT (the searched part)
            rng = h[t - p.slow_bars + 1:t + 1] - l[t - p.slow_bars + 1:t + 1]
            decaying = len(rng) == p.slow_bars and all(np.diff(rng) < 0)
            rsi_rolling = (r[t] < r[t - 1] < r[t - 2]) if t >= 2 else False
            rsi_gate = (r[t - 1] >= p.rsi_extreme if move > 0
                        else r[t - 1] <= 100 - p.rsi_extreme)
            look = h[max(0, t - p.no_new_extreme + 1):t + 1] if move > 0 else \
                l[max(0, t - p.no_new_extreme + 1):t + 1]
            stalled = (look.argmax() if move > 0 else look.argmin()) == 0
            closed_away = (c[t] < o[t]) if move > 0 else (c[t] > o[t])

            n_conf = sum([decaying, rsi_rolling, rsi_gate, stalled])
            if closed_away and n_conf >= 1:
                entry = t
                break
        if entry is None:
            i += 1
            continue

        # --- 5. geometry, all fixed at entry
        man_ext = h[i + 1:entry + 1].max() if move > 0 else l[i + 1:entry + 1].min()
        px = c[entry]
        stop = man_ext + direction * -1 * p.stop_atr * a[entry]
        gtop, gbot = max(gap), min(gap)
        target = px - direction * -1 * abs(px - (gbot if move > 0 else gtop)) * p.target_frac
        risk = abs(px - stop)
        if risk <= 0 or abs(target - px) <= 0:
            i += 1
            continue

        out.append(dict(entry_i=entry, direction=direction, entry=px, stop=stop,
                        target=target, risk=risk, n_conf=n_conf,
                        conf=dict(decaying=bool(decaying), rsi_rolling=bool(rsi_rolling),
                                  rsi_gate=bool(rsi_gate), stalled=bool(stalled)),
                        atr=float(a[entry])))
        i = entry + p.max_wait          # no overlapping setups
    return out
