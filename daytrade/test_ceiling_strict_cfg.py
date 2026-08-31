"""ceiling.simulate must refuse an unrecognised cfg key rather than silently
ignore it.

Context: a k-sweep over cfg["vol_k"] once returned IDENTICAL total R for all
nine values of k, because simulate() read cfg only by explicit key
(cfg["partial_frac"], cfg["trail_mult"], ...) and nothing wired vol_k through.
That produced a confident WRONG number, not a crash — worse than a crash,
because it was caught only by eye. ceiling.ALLOWED_CFG_KEYS + the check at the
top of simulate() turn any future instance of the same mistake into a loud
ValueError instead of a silently-dropped key.
"""
from __future__ import annotations

import pandas as pd
import pytest

import ceiling
from bars import Session


def _fixture_session_and_entry():
    """A minimal synthetic session/entry, same shape as
    test_constitution_wiring.py's ceiling.simulate fixture — no real cached
    data required, nothing sealed touched."""
    closes = [201.0, 202.5, 203.0, 203.2]
    idx = pd.date_range("2026-08-07 09:40", periods=len(closes), freq="5min")
    df = pd.DataFrame({"Open": [c - 0.1 for c in closes],
                       "High": [c + 0.4 for c in closes],
                       "Low": [c - 0.4 for c in closes],
                       "Close": closes, "Volume": [1000.0] * len(closes)}, index=idx)
    session = Session(symbol="TEST", day="2026-08-07", df=df)
    entry = ceiling.Entry(day="2026-08-07", ts="2026-08-07T09:35",
                          time_block="OPEN_DRIVE", direction=1, entry=200.0,
                          stop=199.0, risk=1.0, tp1=201.0, tp2=202.14,
                          trail_dist=0.5)
    return session, entry


VALID_CFG = {"partial_frac": 0.5, "trail_mult": 1.5, "be_arm_frac": 0.25,
            "hold_past_tp2": True, "flatten_et": None}


def test_simulate_accepts_a_valid_cfg():
    session, entry = _fixture_session_and_entry()
    r = ceiling.simulate(session, entry, VALID_CFG)
    assert isinstance(r, float)


def test_simulate_accepts_valid_cfg_with_opted_in_volatility_layer():
    session, entry = _fixture_session_and_entry()
    cfg = dict(VALID_CFG, vol_k=1.5, atr=0.8)
    r = ceiling.simulate(session, entry, cfg)
    assert isinstance(r, float)


def test_simulate_rejects_unknown_cfg_key():
    """This is the exact bug class: a key the sweep script set but simulate()
    never read must raise, not silently vanish."""
    session, entry = _fixture_session_and_entry()
    cfg = dict(VALID_CFG, vol_k_typo=1.5)
    with pytest.raises(ValueError, match="vol_k_typo"):
        ceiling.simulate(session, entry, cfg)


def test_simulate_rejects_unknown_cfg_key_names_accepted_set():
    session, entry = _fixture_session_and_entry()
    cfg = dict(VALID_CFG, bogus="x")
    with pytest.raises(ValueError) as e:
        ceiling.simulate(session, entry, cfg)
    msg = str(e.value)
    assert "bogus" in msg
    for k in ceiling.ALLOWED_CFG_KEYS:
        assert k in msg, f"accepted key {k!r} should be named in the error"


def test_narrow_and_wide_space_configs_all_pass_the_strict_check():
    """narrow_space()/wide_space() generate every config used across the
    ceiling measurement; none of them may carry a key outside
    ALLOWED_CFG_KEYS, or the strict check would have to be loosened to admit
    them — which defeats the point of adding it."""
    session, entry = _fixture_session_and_entry()
    space = {**ceiling.narrow_space(), **ceiling.wide_space()}
    assert len(space) == 399
    for name, cfg in space.items():
        assert not (set(cfg) - ceiling.ALLOWED_CFG_KEYS), \
            f"config {name!r} carries a key outside ALLOWED_CFG_KEYS: {cfg}"
        ceiling.simulate(session, entry, cfg)   # must not raise
