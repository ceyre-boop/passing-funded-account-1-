"""body/features.py — the board. What the agent can see, and nothing about what it means.

THE LINE THIS FILE HOLDS
    Framework, not tuition. Chess gives you the position and the legal moves;
    it does not tell you the centre is valuable. So this module computes state
    and attaches NO opinion to any of it.

    Concretely, what is deliberately absent:
      - no feature is named "signal", "trigger", "setup" or "edge"
      - no thresholds ("expansion > 1.3 is interesting")
      - no directional priors
      - no feature is included because a study liked it, and none is excluded
        because a study didn't

    `VetoEntryPolicy` broke this line: it hard-coded range expansion as a
    reason TO trade, at a weight I chose, because one study cleared a detection
    floor. That is opening theory. It stays in the repo as the hand-built
    baseline the learned policy has to beat, and nothing here inherits from it.

EVERY FEATURE IS LIVE-COMPUTABLE
    Computed from bars at or BEFORE t, only. Same discipline as
    `az/state.py::truncate_at`, and the lookahead corruption test applies: if a
    feature moves when future bars are scrambled, it has read the future.

SCALE-FREE ON PURPOSE
    Everything is normalized by ATR or by a session-relative quantity, so the
    agent cannot learn "SPY at 400 behaves differently from SPY at 600" — that
    is a fact about the price level, not about the market.
"""
from __future__ import annotations

import math

# The state vector, in a fixed order. Order is part of the contract: learned
# weights are meaningless if the columns move under them.
FEATURE_NAMES = (
    "ret_1_atr",          # last bar's close-to-close, in ATRs
    "ret_3_atr",          # 3-bar
    "ret_12_atr",         # 12-bar
    "range_atr",          # this bar's high-low, in ATRs
    "range_3_atr",        # 3-bar realized range, in ATRs
    "atr_frac",           # ATR / price — volatility level, scale-free
    "atr_ratio",          # short ATR / long ATR — is vol rising or falling
    "pos_in_session",     # where price sits in the session's range so far, 0..1
    "dist_vwap_atr",      # signed distance from session VWAP, in ATRs
    "vol_ratio",          # this bar's volume / trailing average
    "minutes_elapsed",    # fraction of the session elapsed, 0..1
    "bars_since_high",    # bars since the session high, normalized
    "bars_since_low",     # bars since the session low, normalized
)
N_FEATURES = len(FEATURE_NAMES)

SHORT_ATR = 6
LONG_ATR = 24
MIN_BARS = LONG_ATR          # below this there is no state, not a default one


class FeatureError(ValueError):
    """Refused. A feature that cannot be computed is absent, never zero."""


def _atr(bars, n: int) -> float:
    w = bars[-n:]
    return sum(float(b.high) - float(b.low) for b in w) / len(w) if w else 0.0


def observable(bars, *, session_len: int) -> list[float] | None:
    """The state vector at the last bar of `bars`, or None if there is not
    enough history to compute one.

    Returns None rather than zeros. A zero ATR once caused an 817x mis-size
    elsewhere in this repo; an absent state must be absent, not a quiet zero
    that the agent will happily learn a rule about.
    """
    if len(bars) < MIN_BARS:
        return None
    atr = _atr(bars, LONG_ATR)
    px = float(bars[-1].close)
    if not (atr > 0 and px > 0 and math.isfinite(atr)):
        return None

    closes = [float(b.close) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    vols = [float(b.volume) for b in bars]

    # A bar whose close sits outside its own high/low is not a bar. Caught by a
    # malformed fixture that produced pos_in_session = 2.05 on a feature
    # documented as 0..1 -- refuse rather than emit a nonsense number the agent
    # would go on to learn a rule about.
    for h, l, c in zip(highs, lows, closes):
        if not (l <= c <= h and l <= h):
            raise FeatureError(
                f"malformed bar: low={l} close={c} high={h} — refusing to build "
                "state on it")

    hi, lo = max(highs), min(lows)
    rng = hi - lo
    vwap_num = sum(c * v for c, v in zip(closes, vols))
    vwap_den = sum(vols)
    vwap = vwap_num / vwap_den if vwap_den > 0 else px
    avg_vol = sum(vols) / len(vols) if vols else 0.0
    short, long_ = _atr(bars, SHORT_ATR), atr

    def ret(n):
        return (closes[-1] - closes[-1 - n]) / atr if len(closes) > n else 0.0

    return [
        ret(1),
        ret(3),
        ret(12),
        (highs[-1] - lows[-1]) / atr,
        (max(highs[-3:]) - min(lows[-3:])) / atr,
        atr / px,
        (short / long_) if long_ > 0 else 1.0,
        ((px - lo) / rng) if rng > 0 else 0.5,
        (px - vwap) / atr,
        (vols[-1] / avg_vol) if avg_vol > 0 else 1.0,
        len(bars) / session_len if session_len else 0.0,
        (len(bars) - 1 - highs.index(hi)) / len(bars),
        (len(bars) - 1 - lows.index(lo)) / len(bars),
    ]
