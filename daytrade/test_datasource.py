"""Tests for the DataSource seam.

Every invariant here is stated as a deliberate violation: each test makes the
wrong behaviour happen and asserts it raises. Per CLAUDE.md's definition of
VERIFIED, an invariant with no test that fails on violation is not verified.
"""
from __future__ import annotations

import json
import urllib.error

import pandas as pd
import pytest

from daytrade import bars, datasource


class FakeSource(datasource.DataSource):
    name = "fake"

    def __init__(self, df=None):
        self.calls = []
        self._df = df if df is not None else _frame("2024-03-01 09:30", 5)

    def bars(self, symbol, tf="5m", start=None, end=None, period=None):
        self.calls.append({"symbol": symbol, "tf": tf, "start": start,
                           "end": end, "period": period})
        return self._df


def _frame(start, n):
    idx = pd.date_range(start, periods=n, freq="5min",
                        tz="America/New_York")
    return pd.DataFrame(
        {"Open": range(n), "High": range(n), "Low": range(n),
         "Close": range(n), "Volume": [100] * n}, index=idx)


# ---------------------------------------------------------- wrong instrument

@pytest.mark.parametrize("sym", ["ES=F", "EURUSD=X", "^VIX", "BTC-USD"])
def test_alpaca_refuses_non_equity_symbols(sym, monkeypatch):
    """The failure that matters: returning SOME series for a futures symbol.

    An empty result is survivable; a plausible-looking equity series standing in
    for ES=F is a silently wrong backtest.
    """
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    src = datasource.AlpacaSource(key="k", secret="s")
    assert src.supports(sym) is False
    with pytest.raises(datasource.DataSourceError, match="cannot serve"):
        src.bars(sym, "5m", start="2024-03-01")


def test_get_source_routes_futures_to_yfinance():
    assert datasource.get_source("ES=F").name == "yfinance"
    assert datasource.get_source("EURUSD=X").name == "yfinance"


def test_forced_source_that_cannot_serve_raises():
    """A forced source is never silently swapped for one that works."""
    with pytest.raises(datasource.DataSourceError, match="forced"):
        datasource.get_source("ES=F", prefer="alpaca")


# ---------------------------------------------------------- feed downgrade

def test_sip_denial_does_not_fall_back_to_iex(monkeypatch):
    """IEX is ~2% of consolidated volume. A silent downgrade would leave every
    volume feature wrong behind a full-looking DataFrame."""
    src = datasource.AlpacaSource(key="k", secret="s", feed="sip")

    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                     _Body(b'{"message":"not authorized"}'))

    monkeypatch.setattr(datasource.urllib.request, "urlopen", boom)
    with pytest.raises(datasource.DataSourceError) as ei:
        src.bars("NVDA", "5m", start="2024-03-01", end="2024-03-02")
    msg = str(ei.value)
    assert "NOT falling back" in msg and "iex" in msg


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self, *a):
        return self._b

    def close(self):
        pass


def test_unmapped_timeframe_raises():
    src = datasource.AlpacaSource(key="k", secret="s")
    with pytest.raises(datasource.DataSourceError, match="not mapped"):
        src.bars("NVDA", "3m", start="2024-03-01")


def test_missing_credentials_raise():
    with pytest.raises(datasource.DataSourceError, match="not found"):
        datasource.AlpacaSource(key="", secret="")


# ---------------------------------------------------------- the seam itself

def test_bars_fetch_uses_injected_source_and_passes_window():
    src = FakeSource()
    out = bars.fetch("NVDA", "5m", start="2024-01-01", end="2024-02-01",
                     source=src)
    assert list(out.columns) == datasource.OHLCV
    assert src.calls[0]["start"] == "2024-01-01"
    # period must not be sent alongside an explicit window
    assert src.calls[0]["period"] is None


def test_bars_fetch_translates_source_error():
    class Boom(datasource.DataSource):
        name = "boom"

        def bars(self, *a, **k):
            raise datasource.DataSourceError("vendor exploded")

    with pytest.raises(bars.BarDataError, match="vendor exploded"):
        bars.fetch("NVDA", "5m", source=Boom())


# ---------------------------------------------------------- provenance

def test_provenance_is_written_and_appends(tmp_path):
    cache = tmp_path / "NVDA_5m.parquet"
    df = _frame("2024-03-01 09:30", 3)
    src = FakeSource()
    p = datasource.write_provenance(cache, src, "NVDA", "5m", df, 3)
    datasource.write_provenance(cache, src, "NVDA", "5m", df, 4)
    rec = json.loads(p.read_text())
    assert len(rec["fetches"]) == 2, "provenance must append, never overwrite"
    assert rec["fetches"][0]["vendor"] == "fake"
    assert rec["fetches"][1]["added_bars"] == 4


def test_refresh_cache_keeps_frozen_history(tmp_path, monkeypatch, capsys):
    """The pre-existing invariant must survive the rewire: a vendor restatement
    of an already-cached bar is reported and IGNORED."""
    monkeypatch.setattr(bars, "CACHE", tmp_path)
    first = _frame("2024-03-01 09:30", 4)
    bars.refresh_cache("NVDA", "5m", source=FakeSource(first))

    restated = first.copy()
    restated.loc[restated.index[0], "Close"] = 999.0
    extended = pd.concat([restated, _frame("2024-03-01 10:00", 2)])
    res = bars.refresh_cache("NVDA", "5m", source=FakeSource(extended))

    assert res["restated_ignored"] == 1
    assert "KEEPING the cached values" in capsys.readouterr().out
    stored = pd.read_parquet(tmp_path / "NVDA_5m.parquet")
    assert stored["Close"].iloc[0] == 0.0, "frozen history was overwritten"
