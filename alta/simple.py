"""alta/simple.py — the rule, stated in numbers, defined BEFORE any outcome.

THE ENTRY, in full. Short shown; long is the mirror.

  1. IMPULSE      |close[i] - close[i-8]| >= 2.0 * ATR14[i]
  2. EXHAUSTION   RSI(14) >= 75 at the swing high after the impulse
                  (75 is the p90 of RSI at up-impulses across 73,255 moments;
                   read off the indicator's own distribution, not from returns)
  3. LOWER HIGH   that swing high is BELOW the impulse extreme
  4. REJECTION    a bar closes below its open -> ENTER AT THAT CLOSE

  STOP    the swing high itself. Nothing fancy. This defines 1R.
  TARGET  N * R below entry, N in {1,2,3,4,5}.

WHY THIS ORDER MATTERS
    Thresholds come from the DISTRIBUTION OF THE INDICATOR, never from the
    distribution of returns. RSI 75 is where the top decile of up-impulses sits
    -- a fact about RSI. Had it been chosen because 75 paid best, this would be
    a search reported as a rule.

    So the only thing tested afterwards is the reward multiple: five numbers,
    N_eff = 5, and the win rate each one needs is arithmetic, not opinion:

        needed win rate = 1 / (1 + N)
        1:1 -> 50.0%   2:1 -> 33.3%   3:1 -> 25.0%
        4:1 -> 20.0%   5:1 -> 16.7%
"""
from __future__ import annotations

import numpy as np

from alta.setup import atr, rsi

IMPULSE_ATR = 2.0
IMPULSE_BARS = 8
RSI_HIGH = 75.0          # p90 of RSI at up-impulses
RSI_LOW = 25.0           # p10 at down-impulses
MAX_WAIT = 26            # bars after the impulse to find the rejection
SPREAD_BP = 1.0
COMMISSION_PER_SHARE = 0.0035


def signals(o, h, l, c, v):
    """Every valid entry in one session. Fields are all known at entry."""
    n = len(c)
    a, r = atr(h, l, 14), rsi(c, 14)
    out, i = [], 20
    while i < n - 2:
        j = i - IMPULSE_BARS
        if j < 0 or np.isnan(a[i]) or a[i] <= 0:
            i += 1
            continue
        move = c[i] - c[j]
        if abs(move) < IMPULSE_ATR * a[i]:
            i += 1
            continue
        up = move > 0
        d = -1 if up else 1                       # fade it
        imp_ext = h[j:i + 1].max() if up else l[j:i + 1].min()

        entry = None
        for t in range(i + 1, min(i + 1 + MAX_WAIT, n - 1)):
            swing = h[i + 1:t + 1].max() if up else l[i + 1:t + 1].min()
            if (swing >= imp_ext) if up else (swing <= imp_ext):
                break                              # new extreme: not a lower high
            if np.isnan(r[t]):
                continue
            hot = (r[i + 1:t + 1].max() >= RSI_HIGH) if up else \
                  (r[i + 1:t + 1].min() <= RSI_LOW)
            rejected = (c[t] < o[t]) if up else (c[t] > o[t])
            if hot and rejected:
                entry = t
                break
        if entry is None:
            i += 1
            continue

        px = c[entry]
        stop = h[i + 1:entry + 1].max() if up else l[i + 1:entry + 1].min()
        risk = abs(stop - px)
        if risk <= 0:
            i += 1
            continue
        out.append(dict(i=entry, d=d, entry=px, stop=stop, risk=risk))
        i = entry + MAX_WAIT
    return out


def resolve(h, l, c, s, rr):
    """One trade at reward multiple `rr`. Stop wins ties. Flat at the close."""
    d, e, risk = s["d"], s["entry"], s["risk"]
    stop, tgt = s["stop"], e + d * rr * risk
    for t in range(s["i"] + 1, len(c)):
        adverse = h[t] if d < 0 else l[t]
        favour = l[t] if d < 0 else h[t]
        if (adverse >= stop) if d < 0 else (adverse <= stop):
            return -1.0, "STOP"
        if (favour <= tgt) if d < 0 else (favour >= tgt):
            return float(rr), "TARGET"
    return float((c[-1] - e) * d / risk), "CLOSE"


def cost_r(s):
    return (2 * (s["entry"] * SPREAD_BP / 10_000.0 + COMMISSION_PER_SHARE)) / s["risk"]
