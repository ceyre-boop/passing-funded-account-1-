"""Mutation-oriented unit tests for ForexDataFetcher provenance labelling.

DEFECT (2026-08-26): ``_fred_latest``/``_fred_yoy``/``_fred_qoq`` each caught
every exception and silently returned the hardcoded fallback table, while
``_fetch_macro`` unconditionally set ``source_map['rate'] = 'fred'`` (etc.)
the moment the FRED branch was entered — regardless of whether the call
actually succeeded. A silently-failed FRED call was therefore mislabelled as
live data, and ``synthetic_fields`` (which filters on ``source_map``) never
flagged it. Those two fields feed ``rate_diff_momentum`` (30% weight) and
``irp_z`` (25% weight) in the macro signal engine.

Fix: the FRED helpers raise ``FredFetchError`` instead of swallowing it, and
``_fetch_macro`` only stamps ``source_map[field] = 'fred'`` when the call
actually succeeds; on failure it leaves the honest ``'fallback_static'``
label in place and calls ``flag_degraded``.

Each test states the fault it exists to catch.
"""
import pandas as pd
import pytest

from sovereign.forex.data_fetcher import (
    FALLBACK_CPI,
    FALLBACK_GDP_GROWTH,
    FALLBACK_RATES,
    FredFetchError,
    ForexDataFetcher,
)


class _FakeFred:
    """Stand-in for fredapi.Fred with a scriptable get_series."""

    def __init__(self, series_by_id=None, raise_for=None):
        self._series_by_id = series_by_id or {}
        self._raise_for = raise_for or set()

    def get_series(self, series_id):
        if series_id in self._raise_for:
            raise RuntimeError(f'FRED down for {series_id}')
        if series_id not in self._series_by_id:
            raise RuntimeError(f'unknown series {series_id}')
        return self._series_by_id[series_id]


def _fetcher(fred=None, fred_ok=True) -> ForexDataFetcher:
    """Build a ForexDataFetcher without hitting __init__'s env/network path."""
    f = ForexDataFetcher.__new__(ForexDataFetcher)
    f._fred = fred
    f._fred_ok = fred_ok
    return f


def _monthly_series(n=20, start_val=1.0, step=0.01):
    idx = pd.date_range('2024-01-01', periods=n, freq='MS')
    return pd.Series([start_val + i * step for i in range(n)], index=idx)


class TestFredHelpersRaiseInsteadOfSwallow:
    """Fault: _fred_latest/_fred_yoy/_fred_qoq returning fallback silently
    on failure instead of raising FredFetchError."""

    def test_fred_latest_raises_on_missing_series_id(self):
        f = _fetcher(fred=_FakeFred())
        with pytest.raises(FredFetchError):
            f._fred_latest('', 2.0)

    def test_fred_latest_raises_on_fred_exception(self):
        f = _fetcher(fred=_FakeFred(raise_for={'FEDFUNDS'}))
        with pytest.raises(FredFetchError):
            f._fred_latest('FEDFUNDS', 2.0)

    def test_fred_latest_returns_value_on_success(self):
        s = _monthly_series()
        f = _fetcher(fred=_FakeFred(series_by_id={'FEDFUNDS': s}))
        val = f._fred_latest('FEDFUNDS', 2.0)
        assert val == float(s.iloc[-1])

    def test_fred_yoy_raises_on_insufficient_history(self):
        # Only 3 monthly points; need 13 for a 12-period YoY.
        s = _monthly_series(n=3)
        f = _fetcher(fred=_FakeFred(series_by_id={'CPIAUCSL': s}))
        with pytest.raises(FredFetchError):
            f._fred_yoy('CPIAUCSL', 2.0, country='US')

    def test_fred_yoy_raises_on_missing_series_id(self):
        f = _fetcher(fred=_FakeFred())
        with pytest.raises(FredFetchError):
            f._fred_yoy('', 2.0, country='JP')  # JP has no FRED CPI series

    def test_fred_yoy_returns_value_on_success(self):
        s = _monthly_series(n=20)
        f = _fetcher(fred=_FakeFred(series_by_id={'CPIAUCSL': s}))
        val = f._fred_yoy('CPIAUCSL', 2.0, country='US')
        assert isinstance(val, float)

    def test_fred_qoq_raises_on_insufficient_history(self):
        s = _monthly_series(n=2)
        f = _fetcher(fred=_FakeFred(series_by_id={'GDP': s}))
        with pytest.raises(FredFetchError):
            f._fred_qoq('GDP', 1.0)

    def test_fred_qoq_returns_value_on_success(self):
        s = _monthly_series(n=6)
        f = _fetcher(fred=_FakeFred(series_by_id={'GDP': s}))
        val = f._fred_qoq('GDP', 1.0)
        assert isinstance(val, float)


class TestFetchMacroProvenanceLabelling:
    """Fault: source_map['rate']/['cpi_yoy']/['gdp_growth'] stamped 'fred'
    unconditionally the instant the FRED branch runs, regardless of whether
    the underlying call raised."""

    def test_failed_fred_rate_is_labelled_fallback_not_fred(self, monkeypatch):
        f = _fetcher(fred=_FakeFred(raise_for={'FEDFUNDS'}))
        calls = []
        monkeypatch.setattr(
            'sovereign.forex.degraded_sentinel.flag_degraded',
            lambda pair, reason, source='yfinance': calls.append((pair, reason, source)),
        )
        macro = f._fetch_macro('US')
        assert macro['source_map']['rate'] == 'fallback_static'
        assert macro['source_map']['rate'] != 'fred'
        assert 'rate' in macro['synthetic_fields']
        assert macro['rate'] == FALLBACK_RATES['US']

    def test_failed_fred_rate_calls_flag_degraded(self, monkeypatch):
        """The FRED fallback path had zero degraded flagging before the fix
        — every failure was invisible."""
        f = _fetcher(fred=_FakeFred(raise_for={'FEDFUNDS'}))
        calls = []
        monkeypatch.setattr(
            'sovereign.forex.degraded_sentinel.flag_degraded',
            lambda pair, reason, source='yfinance': calls.append((pair, reason, source)),
        )
        f._fetch_macro('US')
        assert any(c[0] == 'US' for c in calls), 'flag_degraded must be called on FRED rate failure'

    def test_failed_fred_cpi_calls_flag_degraded_and_labels_honestly(self, monkeypatch):
        # Rate succeeds, CPI fails.
        rate_series = _monthly_series(n=5, start_val=5.0)
        f = _fetcher(fred=_FakeFred(
            series_by_id={'FEDFUNDS': rate_series},
            raise_for={'CPIAUCSL'},
        ))
        calls = []
        monkeypatch.setattr(
            'sovereign.forex.degraded_sentinel.flag_degraded',
            lambda pair, reason, source='yfinance': calls.append((pair, reason, source)),
        )
        macro = f._fetch_macro('US')
        assert macro['source_map']['rate'] == 'fred'
        assert macro['source_map']['cpi_yoy'] == 'fallback_static'
        assert 'cpi_yoy' in macro['synthetic_fields']
        assert 'rate' not in macro['synthetic_fields']
        assert any(c[0] == 'US' for c in calls)

    def test_successful_fred_calls_labelled_fred_and_not_synthetic(self, monkeypatch):
        rate_series = _monthly_series(n=5, start_val=5.0)
        cpi_series = _monthly_series(n=20, start_val=100.0, step=0.5)
        gdp_series = _monthly_series(n=6, start_val=1000.0, step=10.0)
        f = _fetcher(fred=_FakeFred(series_by_id={
            'FEDFUNDS': rate_series,
            'CPIAUCSL': cpi_series,
            'GDP': gdp_series,
        }))
        monkeypatch.setattr(
            'sovereign.forex.degraded_sentinel.flag_degraded',
            lambda *a, **k: (_ for _ in ()).throw(AssertionError('flag_degraded must not fire on success')),
        )
        macro = f._fetch_macro('US')
        assert macro['source_map']['rate'] == 'fred'
        assert macro['source_map']['cpi_yoy'] == 'fred'
        assert macro['source_map']['gdp_growth'] == 'fred'
        assert 'rate' not in macro['synthetic_fields']
        assert 'cpi_yoy' not in macro['synthetic_fields']
        assert 'gdp_growth' not in macro['synthetic_fields']

    def test_no_fred_gdp_series_for_country_stays_fallback(self, monkeypatch):
        """CH has no FRED_GDP entry — gdp_growth must remain fallback_static
        without ever attempting (or mislabelling) a FRED call."""
        f = _fetcher(fred=_FakeFred())
        monkeypatch.setattr(
            'sovereign.forex.degraded_sentinel.flag_degraded',
            lambda *a, **k: None,
        )
        macro = f._fetch_macro('CH')
        assert macro['source_map']['gdp_growth'] == 'fallback_static'
        assert macro['gdp_growth'] == FALLBACK_GDP_GROWTH['CH']
