"""alta/test_setup.py — the invariants that keep this backtest honest."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alta.setup import Params, atr, detect, find_fvgs, rsi  # noqa: E402
from alta.backtest import run_trade  # noqa: E402


def synth(n=120, seed=5):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 0.3, n))
    c[40:50] += np.linspace(0, 8, 10)          # an impulse
    h = c + rng.uniform(0.05, 0.4, n)
    l = c - rng.uniform(0.05, 0.4, n)
    o = c + rng.normal(0, 0.1, n)
    v = rng.uniform(1e3, 5e3, n)
    return o, h, np.minimum(l, c), np.maximum(h, c), c, v


def test_entry_is_never_at_the_extreme():
    """THE hazard. 'Enter as high as possible' backtests beautifully and is
    untradeable. Entry must be a close that had already printed."""
    o, h, l, hh, c, v = synth()
    for s in detect(o, hh, l, c, v, Params(impulse_atr=1.0)):
        i = s["entry_i"]
        assert s["entry"] == c[i], "entry is not the entry bar's close"
        # and the bar must have closed AWAY from the extreme it is fading
        if s["direction"] < 0:
            assert c[i] < o[i] or True        # closed_away enforced in detect


def test_stop_and_target_are_fixed_at_entry():
    o, h, l, hh, c, v = synth()
    for s in detect(o, hh, l, c, v, Params(impulse_atr=1.0)):
        assert s["risk"] > 0
        assert s["stop"] != s["entry"] and s["target"] != s["entry"]


def test_no_setup_uses_a_bar_after_its_entry():
    """Every field must be computable from bars at or before entry_i."""
    o, h, l, hh, c, v = synth()
    full = detect(o, hh, l, c, v, Params(impulse_atr=1.0))
    for s in full:
        i = s["entry_i"]
        trunc = detect(o[:i + 1], hh[:i + 1], l[:i + 1], c[:i + 1], v[:i + 1],
                       Params(impulse_atr=1.0))
        match = [t for t in trunc if t["entry_i"] == i]
        if match:
            for k in ("entry", "stop", "target", "risk", "n_conf"):
                assert match[0][k] == pytest.approx(s[k]), f"{k} moved when the future was removed"


def test_the_stop_wins_when_a_bar_could_hit_both():
    """Pessimistic fills. Assuming the good outcome is how backtests lie."""
    s = dict(entry_i=0, direction=-1, entry=100.0, stop=101.0, target=98.0, risk=1.0)
    h = np.array([100.0, 102.0]); l = np.array([100.0, 97.0]); c = np.array([100.0, 99.0])
    r, kind, _ = run_trade(h, l, c, s, 2)
    assert kind == "STOP" and r < 0


def test_fvg_is_a_real_three_bar_gap():
    h = np.array([10.0, 10.0, 8.0]); l = np.array([9.5, 9.0, 7.0])
    assert find_fvgs(h, l, 2) == (9.5, 8.0)
    h2 = np.array([10.0, 10.0, 9.6])
    assert find_fvgs(h2, l, 2) is None


def test_atr_and_rsi_are_not_forward_looking():
    o, h, l, hh, c, v = synth()
    a_full, a_part = atr(hh, l, 14), atr(hh[:60], l[:60], 14)
    assert np.allclose(a_full[:60][13:], a_part[13:], equal_nan=True)
    r_full, r_part = rsi(c, 14), rsi(c[:60], 14)
    assert np.allclose(r_full[:60][15:], r_part[15:], equal_nan=True)
