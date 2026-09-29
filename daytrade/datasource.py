#!/usr/bin/env python3
"""DATASOURCE — the only file in this repo allowed to know a vendor's name.

WHY THIS EXISTS
---------------
`bars.fetch()` used to call `yfinance.download()` directly. That worked, and it
capped this repo at whatever yfinance is willing to serve: ~60 days of 5m bars.
Every regime/ceiling number in the daytrade lane is measured on ~74 sessions
because of that ceiling, not because 74 is a defensible sample size.

Rather than scatter a second vendor's API through the codebase, every vendor now
sits behind `DataSource.bars()`. `bars.py` calls the interface; it never imports
a vendor. If Alpaca changes its API, or yfinance goes unmaintained, the blast
radius is this file.

PROVENANCE IS NOT OPTIONAL
--------------------------
A cache holding 2024 bars from Alpaca and 2026 bars from yfinance, with no
record of which is which, is exactly the kind of quiet mixed-lineage artifact
SANITY_AUDIT.md exists to prevent. Every fetch stamps `_provenance.json` beside
the parquet: vendor, feed, span, bar count, UTC timestamp. Nothing here writes a
bar without writing where it came from.

FAIL LOUD (APEX #2)
-------------------
- A source asked for a symbol it cannot serve raises. It never returns a
  plausible-looking series for the wrong instrument.
- The Alpaca feed is explicit. `sip` (full consolidated tape) is NOT silently
  downgraded to `iex` when entitlement is missing — IEX carries roughly 2% of
  consolidated volume, so a silent fallback would leave every volume-derived
  feature quietly wrong while still producing a full-looking DataFrame.
- A source swap does not change the SESSION. yfinance serves US equities
  regular-hours only (09:30-16:00 ET); Alpaca serves 04:00-20:00. When
  `get_source()` auto-picked Alpaca for NVDA on 2026-09-28 the RTH cache took
  4,788 pre/post-market bars, and every reader keyed on "the day's first bar"
  (day_open, gap_pct, prior_close) silently read 04:00 and 19:55 prices.
  `get_source()` therefore hands out Alpaca clipped to regular hours; the
  premarket studies construct `AlpacaSource()` directly and keep the raw tape.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]

OHLCV = ["Open", "High", "Low", "Close", "Volume"]
REGULAR_OPEN, REGULAR_CLOSE = "09:30", "16:00"   # bar START in [open, close)


def is_us_equity(symbol: str) -> bool:
    """yfinance suffixes: =F futures, =X FX, ^ index, - crypto. Anything else
    is treated as a US-listed equity."""
    return not (symbol.endswith("=F") or symbol.endswith("=X")
                or "-" in symbol or "^" in symbol)


class DataSourceError(RuntimeError):
    """Vendor fetch failed or returned something unusable. Never a warning."""


# ---------------------------------------------------------------- interface

class DataSource(ABC):
    """One method. Repo code depends on this shape, never on a vendor."""

    name: str = "abstract"

    @abstractmethod
    def bars(self, symbol: str, tf: str, start: str | None = None,
             end: str | None = None, period: str | None = None):
        """ET-indexed DataFrame with exactly OHLCV columns, ascending, no gaps
        filled. Either (start, end) as YYYY-MM-DD, or `period` like '60d'."""

    def supports(self, symbol: str) -> bool:  # pragma: no cover - trivial
        return True

    def describe(self) -> dict:
        return {"vendor": self.name}


# ---------------------------------------------------------------- yfinance

class YFinanceSource(DataSource):
    """The incumbent. Preserved bit-for-bit so existing caches stay valid.

    Kept as the default for futures and FX (`ES=F`, `EURUSD=X`) which Alpaca's
    equity API does not serve at all.
    """

    name = "yfinance"

    def bars(self, symbol, tf="5m", start=None, end=None, period=None):
        import warnings

        import pandas as pd
        import yfinance as yf

        warnings.filterwarnings("ignore")
        kw = {"interval": tf, "progress": False, "auto_adjust": False}
        if start or end:
            kw.update(start=start, end=end)
        else:
            kw["period"] = period or "60d"

        df = yf.download(symbol, **kw)
        if df is None or df.empty:
            raise DataSourceError(f"yfinance returned no {tf} bars for {symbol}")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = df.index.tz_convert(ET)
        return df[OHLCV].sort_index()


# ---------------------------------------------------------------- alpaca

_ALPACA_TF = {"1m": "1Min", "5m": "5Min", "15m": "15Min",
              "1h": "1Hour", "1d": "1Day"}
# The plan's SIP entitlement stops 15 minutes short of now ("subscription does
# not permit querying recent SIP data", HTTP 403 — probed live 2026-09-29).
SIP_DELAY_MIN = 15


class AlpacaSource(DataSource):
    """US equities only, but with real history — 5m bars back to 2016.

    This is the unblock: yfinance caps 5m at ~60 days, Alpaca does not. Uses the
    REST endpoint directly (stdlib urllib) rather than the alpaca-py SDK — one
    documented GET with a page token, versus a dependency whose model classes
    would leak into call sites the moment anyone imported them for typing.
    """

    name = "alpaca"
    BASE = "https://data.alpaca.markets/v2/stocks"

    def __init__(self, key: str | None = None, secret: str | None = None,
                 feed: str = "sip", regular_hours_only: bool = False):
        if key is None or secret is None:
            env = _dotenv(ROOT / ".env")
            key = key or env.get("ALPACA_API_KEY") or os.environ.get("ALPACA_API_KEY")
            secret = (secret or env.get("ALPACA_SECRET_KEY")
                      or os.environ.get("ALPACA_SECRET_KEY"))
        if not key or not secret:
            raise DataSourceError(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY not found in .env or environment")
        self._hdr = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.feed = feed
        # False = the raw 04:00-20:00 tape (the premarket studies want it);
        # True = yfinance's equity session, for caches that assume RTH.
        self.regular_hours_only = regular_hours_only

    # Alpaca's equity API serves US-listed tickers. yfinance suffixes mean
    # futures (=F) and FX (=X); returning equity data for those would be wrong
    # rather than merely empty, so refuse them by name.
    def supports(self, symbol: str) -> bool:
        return is_us_equity(symbol)

    def describe(self) -> dict:
        return {"vendor": self.name, "feed": self.feed,
                "session": "regular" if self.regular_hours_only else "extended"}

    def bars(self, symbol, tf="5m", start=None, end=None, period=None):
        import pandas as pd

        if not self.supports(symbol):
            raise DataSourceError(
                f"AlpacaSource cannot serve {symbol!r} — its equity API does not "
                f"cover futures (=F), FX (=X) or index (^) symbols. Use "
                f"YFinanceSource for that instrument; do not substitute a "
                f"different instrument's bars.")
        if tf not in _ALPACA_TF:
            raise DataSourceError(
                f"timeframe {tf!r} not mapped for Alpaca; known: {sorted(_ALPACA_TF)}")

        end_param = f"{end}T00:00:00Z" if end else None
        if period and not start:
            # The live path. A date-only end is midnight UTC, which left the
            # operator a full session stale: 2026-09-28's bars first reached
            # the cache on 09-29 14:13 UTC. Instead, end on the newest bar
            # that is both complete and past the SIP delay — never a forming
            # bar, because refresh_cache freezes whatever it is first given.
            days = int(str(period).rstrip("dD"))
            now = pd.Timestamp.now(tz="UTC")
            start = (now - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
            delay = pd.Timedelta(minutes=SIP_DELAY_MIN if self.feed == "sip" else 0)
            edge = (now - delay).floor(pd.Timedelta(tf.replace("m", "min")))
            end_param = (edge - pd.Timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not start:
            raise DataSourceError("AlpacaSource needs start= or period=")

        rows, token, pages = [], None, 0
        while True:
            q = {"timeframe": _ALPACA_TF[tf], "start": f"{start}T00:00:00Z",
                 "limit": "10000", "feed": self.feed, "adjustment": "split"}
            if end_param:
                q["end"] = end_param
            if token:
                q["page_token"] = token
            url = f"{self.BASE}/{symbol}/bars?{urllib.parse.urlencode(q)}"
            payload = self._get(url)
            rows.extend(payload.get("bars") or [])
            token = payload.get("next_page_token")
            pages += 1
            if not token:
                break
            if pages > 500:
                raise DataSourceError(
                    f"Alpaca paging exceeded 500 pages for {symbol} — refusing to "
                    f"loop; narrow the date range.")
            time.sleep(0.05)

        if not rows:
            raise DataSourceError(
                f"Alpaca returned no {tf} bars for {symbol} {start}..{end} "
                f"(feed={self.feed})")

        df = pd.DataFrame(rows)
        df["t"] = pd.to_datetime(df["t"], utc=True, format="ISO8601")
        df = df.set_index("t").sort_index()
        df.index = df.index.tz_convert(ET)
        df = df.rename(columns={"o": "Open", "h": "High", "l": "Low",
                                "c": "Close", "v": "Volume"})
        if self.regular_hours_only and tf != "1d":
            t = df.index.strftime("%H:%M")
            df = df[(t >= REGULAR_OPEN) & (t < REGULAR_CLOSE)]
        return df[OHLCV]

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers=self._hdr)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read()[:400].decode(errors="replace")
            if e.code in (401, 403) and self.feed == "sip":
                raise DataSourceError(
                    f"Alpaca denied feed='sip' (HTTP {e.code}): {body}\n"
                    f"NOT falling back to feed='iex' — IEX carries ~2% of "
                    f"consolidated volume, so the bars would look complete while "
                    f"every volume-derived number was wrong. Pass feed='iex' "
                    f"explicitly if that is genuinely what you want.") from e
            raise DataSourceError(f"Alpaca HTTP {e.code}: {body}") from e


# ---------------------------------------------------------------- selection

def _dotenv(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def get_source(symbol: str, prefer: str | None = None) -> DataSource:
    """Pick a source for `symbol`.

    `prefer` (or DAYTRADE_SOURCE) forces one and is NOT silently overridden — a
    forced source that cannot serve the symbol raises rather than quietly
    handing back a different vendor's bars.

    Alpaca is handed out clipped to regular hours, so whichever vendor is
    picked, an equity series has yfinance's session shape.
    """
    def alpaca():
        return AlpacaSource(regular_hours_only=True)

    prefer = prefer or os.environ.get("DAYTRADE_SOURCE")
    if prefer:
        src = {"yfinance": YFinanceSource, "alpaca": alpaca}[prefer.lower()]()
        if not src.supports(symbol):
            raise DataSourceError(
                f"source {prefer!r} was forced but cannot serve {symbol!r}")
        return src
    alp = None
    try:
        alp = alpaca()
    except DataSourceError:
        return YFinanceSource()
    return alp if alp.supports(symbol) else YFinanceSource()


def write_provenance(cache_path: Path, source: DataSource, symbol: str, tf: str,
                     df, added: int) -> Path:
    """Stamp who supplied what, beside the parquet. Append-only history."""
    p = cache_path.with_name(cache_path.stem + "_provenance.json")
    rec = {
        "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": symbol, "tf": tf, "added_bars": int(added),
        "span": [str(df.index.min()), str(df.index.max())] if len(df) else None,
        **source.describe(),
    }
    hist = []
    if p.exists():
        try:
            hist = json.loads(p.read_text()).get("fetches", [])
        except json.JSONDecodeError:
            hist = []
    hist.append(rec)
    p.write_text(json.dumps({"cache": cache_path.name, "fetches": hist}, indent=1))
    return p
