"""Tests for the carry scan — spec 021 G5 signal source.

The invariant that matters: the scan must reproduce the SEALED record's
decisions. If it drifts, the paper sprint silently measures a different
strategy while reporting a verdict about v015.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import carry_scan as cs  # noqa: E402


def test_universe_comes_from_the_sealed_record():
    """Fault: hardcoding the pair list, so it can drift from what was proven."""
    assert cs.sealed_universe() == ["AUDUSD=X", "EURUSD=X", "GBPUSD=X", "USDJPY=X"]
    src = Path(cs.__file__).read_text()
    for p in ("AUDUSD=X", "EURUSD=X", "GBPUSD=X", "USDJPY=X"):
        assert f'"{p}"' not in src, f"{p} hardcoded; universe must be read from the CSV"


def test_preflight_flags_stale_macro_cache(monkeypatch, tmp_path):
    """Fault: scanning on stale rate/CPI data. A stale carry input produces an
    empty scan that is indistinguishable from a genuine 'no setup today'."""
    monkeypatch.setattr(cs, "MACRO_CACHE", tmp_path / "absent")
    monkeypatch.setattr(cs, "CB_DECISIONS", tmp_path / "nope.json")
    bad = cs.preflight(date(2026, 8, 15))
    assert any("cache missing" in b for b in bad)
    assert any("cb_decisions.json missing" in b for b in bad)


def test_preflight_flags_missing_fred_key(monkeypatch, tmp_path):
    """Without FRED_API_KEY the rate/CPI series become flat constants and the
    carry leg collapses — upstream logs a warning and continues."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(cs, "ROOT", tmp_path)          # no .env in tmp_path
    monkeypatch.setattr(cs, "MACRO_CACHE", tmp_path / "absent")
    monkeypatch.setattr(cs, "CB_DECISIONS", tmp_path / "nope.json")
    assert any("FRED_API_KEY absent" in b for b in cs.preflight(date(2026, 8, 15)))


def test_stale_limits_are_tight_enough_to_matter():
    """A limit loose enough to admit the SOURCE_DEAD cases this investigation
    found (AU_cpi 602d, UK_cpi 543d) would not be a guard at all."""
    for key, limit in cs.MACRO_STALE_LIMITS.items():
        assert limit < 300, f"{key} limit {limit}d is loose enough to admit a dead source"


def test_stale_limits_cover_every_series():
    """Fault: adding a country to COUNTRIES without a matching limit falls
    through to DEFAULT_MACRO_STALE_DAYS silently instead of failing loud."""
    for c in cs.COUNTRIES:
        for kind in ("rates", "cpi"):
            assert f"{c}_{kind}" in cs.MACRO_STALE_LIMITS


def test_monthly_oecd_lag_series_does_not_fail_by_construction(monkeypatch, tmp_path):
    """spec 039: a uniform 45-day limit fails JP/AU rates EVERY time, even
    healthy, because of their real ~45-90d OECD MEI publication lag. A
    just-refreshed cache sitting at that natural lag must NOT be flagged."""
    import pandas as pd
    cache = tmp_path / "macro"
    cache.mkdir()
    monkeypatch.setattr(cs, "MACRO_CACHE", cache)
    monkeypatch.setattr(cs, "CB_DECISIONS", tmp_path / "nope.json")
    as_of = date(2026, 8, 26)
    # JP_rates dated 86d before as_of — the real, live-verified worst case
    # for this series family, still short of MACRO_STALE_LIMITS["JP_rates"].
    idx = pd.date_range("2020-01-01", as_of - pd.Timedelta(days=86), freq="B")
    vals = 0.5 + 0.001 * np.arange(len(idx))   # varying, not a fabricated flatline
    pd.Series(vals, index=idx, name="rate").to_frame("rate").to_parquet(
        cache / "JP_rates.parquet")
    bad = cs.preflight(as_of)
    assert not any(b.startswith("JP_rates:") for b in bad), \
        f"JP_rates flagged at its own real publication lag: {bad}"


def test_preflight_flags_flatline_fabricated_cache(monkeypatch, tmp_path):
    """Fault: a hardcoded FALLBACK_CPI constant persisted to disk (e.g.
    JP_cpi's flat 3.2) is date-fresh every day because it gets ffilled — only
    the value spread reveals it is fabricated, not observed. This is the gap
    that let JP_cpi pass preflight silently before this fix."""
    import pandas as pd
    cache = tmp_path / "macro"
    cache.mkdir()
    monkeypatch.setattr(cs, "MACRO_CACHE", cache)
    monkeypatch.setattr(cs, "CB_DECISIONS", tmp_path / "nope.json")
    as_of = date(2026, 8, 26)
    idx = pd.date_range("2020-01-01", as_of, freq="B")
    pd.Series(3.2, index=idx, name="cpi").to_frame("cpi").to_parquet(
        cache / "JP_cpi.parquet")
    bad = cs.preflight(as_of)
    assert any("FLAT LINE" in b and "JP_cpi" in b for b in bad), bad


@pytest.mark.network
def test_reproduces_the_sealed_2024_12_02_signals():
    """The fidelity test. On 2024-12-02 the sealed record entered all four pairs
    at the 12-03 open. Directions and fills must match exactly."""
    hits, _, _ = cs.scan(date(2024, 12, 2))
    if not hits:
        pytest.skip("no market data available (offline)")
    got = {h["pair"]: h for h in hits}
    expected = {                       # from backtest_trades_v015_2015_2024.csv
        "EURUSD=X": ("LONG", 1.050122),
        "GBPUSD=X": ("SHORT", 1.265806),
        "USDJPY=X": ("LONG", 149.5079),
        "AUDUSD=X": ("LONG", 0.647521),
    }
    assert set(got) == set(expected), "scan must fire on all four sealed pairs"
    for pair, (direction, entry) in expected.items():
        assert got[pair]["direction"] == direction, f"{pair} direction drifted"
        assert got[pair]["fill"] == pytest.approx(entry, rel=1e-4), \
            f"{pair} fill drifted from the sealed record"
        assert got[pair]["signal_bar"] == date(2024, 12, 2)
