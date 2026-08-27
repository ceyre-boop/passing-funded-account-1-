"""Unit tests for the UK/AU live CPI replacements (spec: rate-vintage /
data-integrity pass, 2026-08-26).

CONTEXT: FRED's mirror of UK/AU CPI (GBRCPIALLMINMEI, AUSCPIALLQINMEI) is
SOURCE_DEAD — both stopped publishing in the same 2025-01/2025-03 window
because FRED's whole OECD MEI CPI feed for those countries died then, not
just these two series (verified live against FRED 2026-08-26; see
scripts/data_health.py). ``_fetch_ons_uk_cpi_yoy`` / ``_fetch_abs_au_cpi_index``
replace them with direct calls to ONS / ABS. Each test states the fault it
exists to catch: a silent fallback here would relabel fabricated/stale data
as live, exactly the defect test_data_fetcher_provenance.py already caught
once for the FRED path.
"""
from __future__ import annotations

import pandas as pd
import pytest

from sovereign.forex.data_fetcher import (
    DIRECT_YOY_CPI,
    NON_FRED_CPI_SOURCES,
    ForexDataFetcher,
)


def _fetcher() -> ForexDataFetcher:
    f = ForexDataFetcher.__new__(ForexDataFetcher)
    f._fred = None
    f._fred_ok = False
    return f


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_uk_is_registered_as_direct_yoy():
    """Fault: ONS's D7G7 series is already a YoY %. Routing it back through
    the index-level pct_change(12) path would silently corrupt it into a
    YoY-of-a-YoY nonsense figure."""
    assert "UK" in DIRECT_YOY_CPI


def test_uk_and_au_registered_as_non_fred_sources():
    assert NON_FRED_CPI_SOURCES.get("UK") == "ons"
    assert NON_FRED_CPI_SOURCES.get("AU") == "abs"


def test_ons_fetch_parses_months_into_yoy_series(monkeypatch):
    payload = {
        "months": [
            {"date": "2026 JUN", "year": "2026", "month": "June", "value": "2.6"},
            {"date": "2026 JUL", "year": "2026", "month": "July", "value": "2.9"},
        ]
    }
    monkeypatch.setattr(
        "sovereign.forex.data_fetcher.requests.get",
        lambda *a, **k: _FakeResponse(payload),
    )
    s = _fetcher()._fetch_ons_uk_cpi_yoy("2020-01-01")
    assert s.iloc[-1] == pytest.approx(2.9)
    assert s.index.max() == pd.Timestamp("2026-07-01")


def test_ons_fetch_raises_on_empty_response_never_fabricates(monkeypatch):
    """Fault: a network hiccup or an ONS schema change must surface as an
    exception the caller can label honestly — never a silent fallback to the
    hardcoded FALLBACK_CPI constant disguised as live data."""
    monkeypatch.setattr(
        "sovereign.forex.data_fetcher.requests.get",
        lambda *a, **k: _FakeResponse({"months": []}),
    )
    with pytest.raises(RuntimeError):
        _fetcher()._fetch_ons_uk_cpi_yoy("2020-01-01")


def test_ons_fetch_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        "sovereign.forex.data_fetcher.requests.get",
        lambda *a, **k: _FakeResponse({}, status=404),
    )
    with pytest.raises(RuntimeError):
        _fetcher()._fetch_ons_uk_cpi_yoy("2020-01-01")


def _abs_payload(obs: dict[int, float], periods: list[str]):
    return {
        "data": {
            "dataSets": [{"series": {"0:0:0:0:0": {"observations": obs}}}],
            "structures": [{
                "dimensions": {
                    "observation": [{
                        "id": "TIME_PERIOD",
                        "values": [
                            {"id": p, "start": f"{p[:4]}-{'01' if p.endswith('Q1') else ('04' if p.endswith('Q2') else ('07' if p.endswith('Q3') else '10'))}-01"}
                            for p in periods
                        ],
                    }],
                },
            }],
        }
    }


def test_abs_fetch_parses_index_series(monkeypatch):
    payload = _abs_payload({"0": [100.0], "1": [102.31]}, ["2026-Q1", "2026-Q2"])
    monkeypatch.setattr(
        "sovereign.forex.data_fetcher.requests.get",
        lambda *a, **k: _FakeResponse(payload),
    )
    s = _fetcher()._fetch_abs_au_cpi_index("2015-01-01")
    assert s.index.max() == pd.Timestamp("2026-04-01")
    assert s.iloc[-1] == pytest.approx(102.31)


def test_abs_fetch_raises_on_no_series_never_fabricates(monkeypatch):
    monkeypatch.setattr(
        "sovereign.forex.data_fetcher.requests.get",
        lambda *a, **k: _FakeResponse({"data": {"dataSets": [{}]}}),
    )
    with pytest.raises(RuntimeError):
        _fetcher()._fetch_abs_au_cpi_index("2015-01-01")


def test_fetch_cpi_history_falls_back_to_fred_when_live_source_fails(monkeypatch):
    """Fault: if ONS/ABS is briefly unreachable, _fetch_cpi_history must not
    crash the whole signal path — it degrades to the existing FRED branch
    (which is itself SOURCE_DEAD for UK/AU, so this still ultimately reaches
    the honest FALLBACK_CPI constant, not a fabricated live-looking value)."""
    monkeypatch.setattr(
        "sovereign.forex.data_fetcher.requests.get",
        lambda *a, **k: _FakeResponse({}, status=500),
    )
    f = _fetcher()   # _fred_ok=False, so the FRED branch also can't run
    s = f._fetch_cpi_history("UK", "2020-01-01")
    # falls all the way through to the flat FALLBACK_CPI series, not a crash
    assert s is not None
    assert s.nunique() == 1
