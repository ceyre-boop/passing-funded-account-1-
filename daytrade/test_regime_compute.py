"""Card 020 — regime_vector.compute() silent-zero hardening.

The architect ruling at 020 promotion: the five fallbacks (ATR/price,
volatility expansion, VWAP stretch, zero-denominator autocorrelation,
index-correlation StatisticsError) must emit `unavailable` with a reason — or
raise on malformed input — never a real 0.0 labeled `computed`. A downstream
require() cannot repair a fabricated zero.

Fixtures are deterministic in-memory pandas frames (no parquet, no pyarrow
file I/O, no network) — 019 stays parquet-free; this file owns compute().
"""
from __future__ import annotations

import pandas as pd
import pytest

from bars import Session
from regime_vector import RegimeError, compute

DAY = "2026-08-07"


def mk_session(closes: list[float], *, day: str = DAY, spread: float = 0.2,
               volume: float = 1000.0) -> Session:
    """One session of 5m bars. spread=0 makes a perfectly flat (zero-ATR) tape
    when closes are constant."""
    idx = pd.date_range(f"{day} 09:30", periods=len(closes), freq="5min")
    df = pd.DataFrame({
        "Open":  [c for c in closes],
        "High":  [c + spread for c in closes],
        "Low":   [c - spread for c in closes],
        "Close": closes,
        "Volume": [volume] * len(closes),
    }, index=idx)
    return Session(symbol="TEST", day=day, df=df)


NORMAL = [200.0, 200.4, 200.1, 200.9, 201.3, 200.8, 201.6, 202.0]
HISTORY = [mk_session([199.0, 199.5, 199.2, 200.0, 200.6, 200.2, 201.0, 199.8]),
           mk_session([201.0, 200.4, 200.9, 200.2, 199.8, 200.5, 199.9, 200.7])]


def test_happy_path_emits_no_fabricated_zero():
    v = compute("TEST", mk_session(NORMAL), HISTORY, ts=DAY)
    for name in ("trend_strength", "realized_volatility", "volatility_expansion",
                 "liquidity_quality", "momentum_persistence",
                 "mean_reversion_pressure"):
        assert v.dims[name].source == "computed", name
        assert v.dims[name].value is not None, name


def test_flat_tape_yields_unavailable_not_computed_zero():
    """Constant closes with zero range: ATR is 0, expansion/stretch/
    autocorrelation are all undefined. Each must say so — none may quietly
    report a computed 0.0 (the exact number a genuinely calm-but-alive tape
    would legitimately produce)."""
    flat = mk_session([200.0] * 8, spread=0.0)
    v = compute("TEST", flat, HISTORY, ts=DAY)
    for name in ("trend_strength", "volatility_expansion",
                 "mean_reversion_pressure", "momentum_persistence"):
        d = v.dims[name]
        assert d.source == "unavailable", f"{name}: {d.source}={d.value!r}"
        assert d.value is None
        assert d.detail                     # the reason travels with the refusal


def test_zero_median_history_makes_expansion_unavailable():
    """Today is alive but every historical session was flat: log(today/median)
    has a zero denominator — expansion is undefined, not zero."""
    flat_history = [mk_session([200.0] * 8, spread=0.0) for _ in range(2)]
    v = compute("TEST", mk_session(NORMAL), flat_history, ts=DAY)
    assert v.dims["volatility_expansion"].source == "unavailable"
    assert v.dims["realized_volatility"].source == "computed"   # percentile still fine


def test_zero_mean_price_raises():
    """A tape whose mean price is zero is malformed input, not a regime — the
    ruling says raise, never score garbage."""
    with pytest.raises(RegimeError):
        compute("TEST", mk_session([0.0] * 8, spread=0.0), HISTORY, ts=DAY)


def test_constant_index_makes_correlation_unavailable_only():
    """A constant index series has no return variance: correlation is
    undefined -> unavailable. Alignment and relative strength remain computed —
    the refusal is per-dimension, not a blanket."""
    idx_flat = mk_session([500.0] * 8, spread=0.0)
    v = compute("TEST", mk_session(NORMAL), HISTORY, ts=DAY, index_session=idx_flat)
    assert v.dims["cross_asset_correlation"].source == "unavailable"
    assert v.dims["index_alignment"].source == "computed"
    assert v.dims["symbol_relative_strength"].source == "computed"


def test_require_refuses_the_hardened_dims():
    """The point of the hardening: a decision path that require()s one of these
    on a flat tape gets a RegimeError, not a fabricated zero."""
    from regime_vector import require
    flat = mk_session([200.0] * 8, spread=0.0)
    v = compute("TEST", flat, HISTORY, ts=DAY)
    with pytest.raises(RegimeError):
        require(v, "momentum_persistence")
