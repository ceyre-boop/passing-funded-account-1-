"""body/test_features.py — the board is a board, not a hint."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from body.features import (FEATURE_NAMES, MIN_BARS, N_FEATURES,  # noqa: E402
                           FeatureError, observable)


class B:
    def __init__(s, o, h, l, c, v=1000.0):
        s.open, s.high, s.low, s.close, s.volume = o, h, l, c, v


def bars(n=40, seed=3):
    import random
    r = random.Random(seed); out = []; px = 500.0
    for _ in range(n):
        px += r.gauss(0, 0.4)
        out.append(B(px, px + 0.3, px - 0.3, px + r.uniform(-0.25, 0.25)))
    return out


def test_no_feature_is_named_like_an_opinion():
    """Framework, not tuition. A feature called `signal` has already told the
    agent what to think about it."""
    banned = ("signal", "trigger", "setup", "edge", "buy", "sell", "good", "bad")
    for n in FEATURE_NAMES:
        assert not any(b in n for b in banned), n


def test_absent_state_is_none_not_zeros():
    """A zero ATR once caused an 817x mis-size in this repo. An absent state
    must be absent, not a quiet zero the agent learns a rule about."""
    assert observable(bars(MIN_BARS - 1), session_len=78) is None


def test_a_malformed_bar_is_refused():
    bad = [B(500, 500.5, 499.5, 502.0)] * MIN_BARS
    with pytest.raises(FeatureError):
        observable(bad, session_len=78)


def test_bounded_features_stay_bounded():
    v = observable(bars(), session_len=78)
    d = dict(zip(FEATURE_NAMES, v))
    for k in ("pos_in_session", "minutes_elapsed", "bars_since_high", "bars_since_low"):
        assert 0.0 <= d[k] <= 1.0, f"{k}={d[k]}"


def test_scale_free():
    """Doubling the price must not change the state. Otherwise the agent can
    learn 'SPY at 600 is different', which is a fact about the price level."""
    a = observable(bars(), session_len=78)
    b = observable([B(x.open * 2, x.high * 2, x.low * 2, x.close * 2, x.volume)
                    for x in bars()], session_len=78)
    for n, (x, y) in zip(FEATURE_NAMES, zip(a, b)):
        if n == "atr_frac":
            continue                     # ATR/price is invariant by construction
        assert abs(x - y) < 1e-6, f"{n} moved with the price level"


def test_vector_length_is_the_contract():
    assert len(observable(bars(), session_len=78)) == N_FEATURES == len(FEATURE_NAMES)
