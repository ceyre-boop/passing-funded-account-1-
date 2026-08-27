"""Unit tests for the JP CPI e-Stat replacement (2026-08-27).

CONTEXT: FRED never had a working JP CPI series (FRED_CPI['JP'] == ''), so
JP_cpi lived on the synthetic FALLBACK_CPI constant (a hardcoded flat 3.2
persisted to data/cache/macro/JP_cpi.parquet) until Colin supplied an e-Stat
appId. ``_fetch_estat_jp_cpi_yoy`` replaces it with a live fetch, stitched
across e-Stat's 2015/2020/2025 base-year generations (ESTAT_JP_CPI_TABLES).
Each test states the fault it exists to catch — the same discipline
test_data_fetcher_dead_source_replacement.py already applied to UK/AU.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from sovereign.forex.data_fetcher import (
    DIRECT_YOY_CPI,
    ESTAT_JP_CPI_MAX_BOUNDARY_JUMP_PP,
    ESTAT_JP_CPI_TABLES,
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


def _estat_payload(rows: list[tuple[str, str]], status=0):
    """rows: list of (time_code, value_str)."""
    return {
        "GET_STATS_DATA": {
            "RESULT": {"STATUS": status, "ERROR_MSG": "OK" if status == 0 else "err"},
            "STATISTICAL_DATA": {
                "DATA_INF": {
                    "VALUE": [
                        {"@tab": "3", "@cat01": "0001", "@area": "00000",
                         "@time": t, "@unit": "%", "$": v}
                        for t, v in rows
                    ]
                }
            },
        }
    }


def test_jp_registered_as_direct_yoy_and_non_fred_source():
    """Fault: e-Stat's tab=3 is already a YoY % figure. Routing it back
    through the index-level pct_change(12) path would corrupt it into a
    YoY-of-a-YoY nonsense figure, exactly the fault UK's D7G7 test guards."""
    assert "JP" in DIRECT_YOY_CPI
    assert NON_FRED_CPI_SOURCES.get("JP") == "estat"


def test_estat_raises_without_appid(monkeypatch):
    """Fault: a missing credential must surface as an exception, never a
    silent fallback disguised as live data."""
    monkeypatch.delenv("ESTAT_APPID", raising=False)
    with pytest.raises(RuntimeError, match="ESTAT_APPID"):
        _fetcher()._fetch_estat_jp_cpi_yoy("2015-01-01")


def test_estat_table_fetch_parses_monthly_rows_and_skips_annual(monkeypatch):
    """Fault: e-Stat's time codes mix annual/fiscal-year rollups into the
    same series as monthly observations — an unfiltered parse would double
    -count or corrupt the monthly series."""
    monkeypatch.setenv("ESTAT_APPID", "fake-key")
    payload = _estat_payload([
        ("2026000101", "1.5"),   # Jan 2026, monthly
        ("2026000202", "1.3"),   # Feb 2026, monthly
        ("2026000000", "9.9"),   # annual rollup — must be skipped
        ("2025100000", "8.8"),   # fiscal year rollup — must be skipped
    ])
    monkeypatch.setattr(
        "sovereign.forex.data_fetcher.requests.get",
        lambda *a, **k: _FakeResponse(payload),
    )
    s = _fetcher()._fetch_estat_jp_cpi_table("0004052037")
    assert len(s) == 2
    assert s.loc[pd.Timestamp("2026-01-01")] == pytest.approx(1.5)
    assert s.loc[pd.Timestamp("2026-02-01")] == pytest.approx(1.3)


def test_estat_table_fetch_raises_on_bad_status(monkeypatch):
    monkeypatch.setenv("ESTAT_APPID", "fake-key")
    payload = _estat_payload([("2026000101", "1.5")], status=1)
    monkeypatch.setattr(
        "sovereign.forex.data_fetcher.requests.get",
        lambda *a, **k: _FakeResponse(payload),
    )
    with pytest.raises(RuntimeError, match="STATUS"):
        _fetcher()._fetch_estat_jp_cpi_table("0004052037")


def test_estat_table_fetch_raises_on_empty_observations(monkeypatch):
    """Fault: an e-Stat schema change or empty result must surface as an
    exception — never a silent fallback to the hardcoded FALLBACK_CPI 3.2."""
    monkeypatch.setenv("ESTAT_APPID", "fake-key")
    payload = _estat_payload([])
    monkeypatch.setattr(
        "sovereign.forex.data_fetcher.requests.get",
        lambda *a, **k: _FakeResponse(payload),
    )
    with pytest.raises(RuntimeError):
        _fetcher()._fetch_estat_jp_cpi_table("0004052037")


def _mock_three_generations(monkeypatch, gen_rows: dict[str, list[tuple[str, str]]], tmp_path=None):
    """gen_rows keyed by table_id, matching ESTAT_JP_CPI_TABLES order."""
    def fake_get(url, params=None, timeout=None):
        table_id = params["statsDataId"]
        return _FakeResponse(_estat_payload(gen_rows.get(table_id, [])))

    monkeypatch.setenv("ESTAT_APPID", "fake-key")
    monkeypatch.setattr("sovereign.forex.data_fetcher.requests.get", fake_get)
    if tmp_path is not None:
        monkeypatch.setattr("sovereign.forex.data_fetcher.ESTAT_RAW_DIR", tmp_path)


def test_stitch_prefers_newest_generation_for_overlapping_month(monkeypatch, tmp_path):
    """Fault: if the merge preferred the OLDEST generation (or averaged
    them), a rebase revision would never actually take effect."""
    table_ids = [tid for _, tid in ESTAT_JP_CPI_TABLES]
    gen_rows = {
        table_ids[0]: [("2021000404", "-0.4")],   # 2015base: Apr 2021 = -0.4
        table_ids[1]: [("2021000404", "-1.1")],   # 2020base: Apr 2021 = -1.1 (revised)
        table_ids[2]: [("2021000404", "-1.1")],   # 2025base: same as 2020base
    }
    _mock_three_generations(monkeypatch, gen_rows, tmp_path)
    s = _fetcher()._fetch_estat_jp_cpi_yoy("2015-01-01")
    assert s.loc[pd.Timestamp("2021-04-01")] == pytest.approx(-1.1)
    assert (tmp_path / "JP_cpi_provenance.parquet").exists()


def test_boundary_jump_guard_catches_a_fabricated_splice(monkeypatch):
    """Fault: the artifact this whole change replaces was a fabricated flat
    3.2 — the analogous risk in a multi-generation stitch is a fabricated
    (or badly-linked) jump at the generation seam, e.g. an accidental
    index-level value read as if it were already a YoY %. This must raise,
    not silently produce a chart-breaking spike."""
    table_ids = [tid for _, tid in ESTAT_JP_CPI_TABLES]
    gen_rows = {
        table_ids[0]: [("2021000404", "-0.4")],
        # A generation-2 value hugely out of line with generation-1's
        # neighbouring month — as if an index LEVEL (e.g. 105.2) leaked into
        # a YoY %-shaped field instead of the real ~-1pp reading.
        table_ids[1]: [("2021000505", "105.2")],
        table_ids[2]: [("2021000505", "105.2")],
    }
    _mock_three_generations(monkeypatch, gen_rows)
    with pytest.raises(RuntimeError, match="jumps"):
        _fetcher()._fetch_estat_jp_cpi_yoy("2015-01-01")


def test_flatline_guard_catches_a_regressed_fabrication(monkeypatch):
    """Fault: this is the exact defect being replaced (a flat 3.2 across
    3040 rows). If e-Stat ever degrades to returning a constant, this must
    raise rather than let a fabricated-looking series back onto disk."""
    table_ids = [tid for _, tid in ESTAT_JP_CPI_TABLES]
    flat_rows = [
        (f"{year}00{str(m).zfill(2)}{str(m).zfill(2)}", "3.2")
        for year in range(2018, 2022)
        for m in range(1, 13)
    ]
    assert len(flat_rows) > 30
    gen_rows = {tid: flat_rows for tid in table_ids}
    _mock_three_generations(monkeypatch, gen_rows)
    with pytest.raises(RuntimeError, match="flat"):
        _fetcher()._fetch_estat_jp_cpi_yoy("2015-01-01")


def test_fetch_cpi_history_wires_jp_through_estat(monkeypatch, tmp_path):
    monkeypatch.setenv("ESTAT_APPID", "fake-key")
    table_ids = [tid for _, tid in ESTAT_JP_CPI_TABLES]
    gen_rows = {
        table_ids[0]: [("2020000101", "0.5")],
        table_ids[1]: [("2021000101", "0.7")],
        table_ids[2]: [("2026000101", "1.5"), ("2026000202", "1.3")],
    }
    _mock_three_generations(monkeypatch, gen_rows, tmp_path)
    f = _fetcher()
    s = f._fetch_cpi_history("JP", "2015-01-01")
    assert s is not None
    assert s.name == "JP_cpi_yoy"
    assert s.nunique() > 1


def test_fetch_cpi_history_returns_none_when_estat_unreachable(monkeypatch):
    """NO FLAT FALLBACK — the opposite of what this test originally asserted.

    The first version of this test pinned `falls_back_to_flat` as correct
    behaviour. It is not. A constant CPI series written to disk is exactly the
    defect this whole pipeline was fixing: JP_cpi was a hardcoded flat 3.2 that
    passed every staleness check for months because forward-fill made it look
    fresh, and it only surfaced when a flatline detector was wired into
    preflight. Reintroducing a flat fallback for the unreachable case rebuilds
    that trap one layer down.

    A CPI source that cannot be reached must produce None so preflight SEES the
    absence, not a plausible-looking constant.
    """
    from sovereign.forex import data_fetcher as df

    def boom(*a, **k):
        raise RuntimeError("e-Stat unreachable")

    monkeypatch.setattr(df.ForexDataFetcher, "_fetch_estat_jp_cpi_yoy", boom,
                        raising=False)
    f = df.ForexDataFetcher()
    assert f._fetch_cpi_history("JP", start="2020-01-01") is None
