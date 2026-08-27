"""
Forex macro data fetcher.

Primary: FRED API (if FRED_API_KEY in env)
Fallback: yfinance proxies + hardcoded known rates

Cached to data/cache/macro/{country}.parquet — updates monthly.
"""
from __future__ import annotations

import os
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class FredFetchError(Exception):
    """Raised when a FRED series is unconfigured, unreachable, or too short
    to compute the requested value. Callers MUST catch this and record the
    fallback honestly in source_map — never label a failed FRED call as
    'fred'."""

from sovereign.forex import rate_vintage

CACHE_DIR = Path(__file__).parents[2] / 'data' / 'cache' / 'macro'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Raw multi-generation JP CPI provenance sidecar — see
# ForexDataFetcher._fetch_estat_jp_cpi_yoy. Module-level constant (not
# inlined) so tests can redirect it away from the real cache tree.
ESTAT_RAW_DIR = Path(__file__).parents[2] / 'data' / 'cache' / 'macro_estat_raw'

# FRED series IDs per country/metric
FRED_RATES: Dict[str, str] = {
    'US': 'FEDFUNDS',
    'EU': 'ECBDFR',
    'UK': 'IUDSOIA',
    'JP': 'IRSTCI01JPM156N',
    'CH': 'IR3TIB01CHM156N',
    'AU': 'IR3TIB01AUM156N',   # Australia 3-month interbank rate (RBA proxy)
    'CA': 'IR3TIB01CAM156N',   # Canada 3-month T-bill rate (BOC proxy)
    'NZ': 'IR3TIB01NZM156N',   # New Zealand 3-month rate (RBNZ proxy)
}

FRED_CPI: Dict[str, str] = {
    'US': 'CPIAUCSL',
    'EU': 'CP0000EZ19M086NEST',
    'UK': 'GBRCPIALLMINMEI',
    'JP': '',   # No reliable current FRED series — see NON_FRED_CPI_SOURCES
                # ('estat'), which replaces the flat 3.2% fallback with a
                # live e-Stat fetch. Kept '' so this dict stays a total,
                # honest map of "what FRED itself has" — do not backfill.
    'CH': 'CHECPIALLMINMEI',
    'AU': 'AUSCPIALLQINMEI',
    'CA': 'CANCPIALLMINMEI',
    'NZ': 'NZLCPIALLQINMEI',
}

# CPI series that are quarterly (4 periods = 1 year, not 12)
QUARTERLY_CPI = {'AU', 'NZ'}

# UK's FRED-mirrored CPI (GBRCPIALLMINMEI, OECD MEI feed) stopped publishing
# 2025-03-01 (verified live 2026-08-26) — FRED's whole OECD MEI CPI feed for
# UK/AU/JP died in that window, not just this one series (every COICOP-family
# UK/AU/JP series on FRED shares that same last_updated cluster). Replaced by
# a direct fetch from ONS (Office for National Statistics) series D7G7,
# dataset MM23 — "CPI ANNUAL RATE 00: ALL ITEMS 2015=100" — which is already
# a YoY % figure, so it goes straight into DIRECT_YOY_CPI rather than through
# the index-level pct_change(12) path.
#
# JP's e-Stat replacement (see NON_FRED_CPI_SOURCES / ESTAT_JP_CPI_TABLES
# below) is also DIRECT_YOY: cdTab=3 ("前年同月比" — year-over-year %) is
# requested straight from the API, same reasoning as UK's D7G7.
DIRECT_YOY_CPI: set = {'UK', 'JP'}

# Countries whose CPI now comes from a non-FRED live source because FRED's
# copy is SOURCE_DEAD (see scripts/data_health.py) or never existed (JP:
# FRED_CPI['JP'] == ''). JP was synthetic (flat FALLBACK_CPI 3.2%) until
# Colin supplied an e-Stat appId (2026-08-27) — see
# _fetch_estat_jp_cpi_yoy for the live replacement.
NON_FRED_CPI_SOURCES = {'UK': 'ons', 'AU': 'abs', 'JP': 'estat'}

# ONS "Consumer Price Index Annual Rate" — d7g7/mm23 — already a YoY % figure.
ONS_UK_CPI_YOY_URL = (
    "https://api.beta.ons.gov.uk/v1/data"
    "?uri=/economy/inflationandpriceindices/timeseries/d7g7/mm23"
)

# ABS SDMX data API. AUSCPIALLQINMEI (the FRED mirror) died 2025-01-01 because
# it was sourced from OECD MEI, which ABS itself does not maintain — this
# calls the Australian Bureau of Statistics directly. Key:
# MEASURE=1 (Index numbers) . INDEX=10001 (All groups CPI) . TSEST=10
# (Original) . REGION=50 (Australia, weighted avg of 8 capital cities) . FREQ=Q
# MEASURE=3 ("Percentage change from previous year") does not exist for this
# exact slice (verified live 2026-08-26 — only measures 1/2/4 are published
# for national all-groups original), so this fetches the index level and
# reuses the SAME pct_change(4)*100 YoY path AU already used with FRED's
# quarterly index, preserving that computation unchanged.
ABS_AU_CPI_INDEX_URL = (
    "https://data.api.abs.gov.au/rest/data/ABS,CPI,2.0.0/1.10001.10.50.Q"
    "?format=jsondata"
)

# e-Stat (Japan Statistics Bureau) getStatsData — statsCode 00200573 is the
# Consumer Price Index. Requires an appId (ESTAT_APPID in .env); the class
# codes below are: tab=3 "前年同月比" (year-over-year %, already YoY — same
# shape as UK's D7G7), cat01=0001 "総合" (all items), area=00000 "全国"
# (national, all Japan). Verified live 2026-08-27/28.
ESTAT_BASE_URL = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
ESTAT_JP_CPI_PARAMS = {"cdTab": "3", "cdCat01": "0001", "cdArea": "00000"}

# The sealed backtest (2015-2024) plus live crosses two CPI base-year
# revisions (2015-base -> 2020-base -> 2025-base — the 2020-base and
# 2025-base tables were BOTH first published by e-Stat this week: OPEN_DATE
# 2026-08-21 and 2026-08-28 respectively, verified live). Design decision
# (specs/039's "design problem", resolved here):
#
#   STITCH YoY per generation, prefer the NEWEST generation covering a given
#   month, rather than splice INDEX LEVELS with a linking factor. Reasons:
#     1. Each e-Stat table independently retropolates its OWN
#        consistently-weighted index back to ~1970/71 — there is no scale
#        discontinuity WITHIN one generation's table, only a basket-weight
#        revision AT a seam between generations.
#     2. YoY is already a normalized (%) figure, so stitching it introduces
#        no fabricated scale jump — a naive INDEX-LEVEL concatenation across
#        a rebase would (base swap moves the index's raw scale by ~5-10%
#        with no linking factor computed here, which this module refuses to
#        fabricate).
#     3. "Prefer newest generation" matches the exact same convention every
#        other FRED-driven country in this cache tree already uses
#        (`.get_series()` returns latest-revision values, not a
#        point-in-time read) — not a new look-ahead, the SAME documented
#        one spec 039 already logged for the legacy `data/cache/macro/`
#        tree.
#   Measured live 2026-08-26: 2015-base vs 2020-base disagree by up to
#   0.7pp in their 2021 overlap (a real basket-weight revision, not a bug);
#   2020-base vs 2025-base agree almost exactly in their 2025-2026 overlap.
#   ESTAT_JP_CPI_MAX_BOUNDARY_JUMP_PP below is the guard against a genuinely
#   bad splice (see _fetch_estat_jp_cpi_yoy).
ESTAT_JP_CPI_TABLES: list = [
    ("2015base", "0003143513"),
    ("2020base", "0003427113"),
    ("2025base", "0004052037"),
]

# When each generation's table first became queryable on e-Stat — the date a
# NEWER-generation revision of an already-published month became available.
# Recorded here (not re-derived live) because it is stable table metadata,
# same treatment as a hardcoded FRED series id. Used only to build the
# provenance sidecar (est_available_from), never the primary cache value.
ESTAT_JP_CPI_GENERATION_OPEN_DATE = {
    "2015base": "2022-01-21",
    "2020base": "2026-08-21",
    "2025base": "2026-08-28",
}

# Statistics Bureau of Japan's published release calendar puts the national
# CPI ~19-20 days after the reference month ends. This is a fixed-lag
# APPROXIMATION (JP has no ALFRED-equivalent public revision-vintage API),
# same treatment as spec 039's DAILY_SERIES_LAG_DAYS constants for
# ECBDFR/IUDSOIA. Used only for the provenance sidecar, not the primary
# get_cpi_history() path (which stays nominal-date indexed, matching every
# other country in this tree).
ESTAT_JP_CPI_PUBLICATION_LAG_DAYS = 20

# Above this month-over-month jump (percentage points) AT a generation
# boundary, refuse rather than silently splice. JP CPI YoY has never moved
# more than a few pp month-over-month even in the 2022-2023 energy shock;
# this sits far below a fabricated index-level-treated-as-YoY jump and far
# above any real single-month move.
ESTAT_JP_CPI_MAX_BOUNDARY_JUMP_PP = 10.0

FRED_GDP: Dict[str, str] = {
    'US': 'GDP',
    'EU': 'EUNNGDP',
    'UK': 'UKNGDP',
    'JP': 'JPNNGDP',
}

# yfinance rate proxies (short-term gov yields as central bank rate proxies)
# These are close enough for signal generation
YF_RATE_PROXIES: Dict[str, Tuple[str, float]] = {
    'US': ('^IRX', 0.01),     # 13-week T-bill, already in %
    'EU': ('^TNX', 0.01),     # fallback: use US 10Y as proxy if EU unavailable
    'UK': ('^TNX', 0.01),
    'JP': ('^TNX', 0.01),
    'AU': ('^TNX', 0.01),
    'CA': ('^TNX', 0.01),
    'CH': ('^TNX', 0.01),
    'NZ': ('^TNX', 0.01),
}

# Known current approximate rates (2026-04) as last-resort fallback
# These change slowly — update quarterly
FALLBACK_RATES: Dict[str, float] = {
    'US': 4.33, 'EU': 2.50, 'UK': 4.50, 'JP': 0.50,
    'CH': 0.50, 'AU': 4.10, 'CA': 2.75, 'NZ': 3.50,
}

FALLBACK_CPI: Dict[str, float] = {
    'US': 2.4, 'EU': 2.2, 'UK': 2.8, 'JP': 3.2,
    'CH': 0.3, 'AU': 2.4, 'CA': 2.3, 'NZ': 2.2,
}

FALLBACK_GDP_GROWTH: Dict[str, float] = {
    'US': 2.5, 'EU': 1.2, 'UK': 0.8, 'JP': -0.2,
    'CH': 1.0, 'AU': 1.5, 'CA': 1.8, 'NZ': 0.5,
}

# Rate trajectory from last 3 decisions (1=hike, 0=hold, -1=cut)
# Update monthly from central bank announcements
RATE_TRAJECTORY: Dict[str, list] = {
    'US': [-1, -1, 0],   # FED cut twice, now holding
    'EU': [-1, -1, -1],  # ECB cutting cycle
    'UK': [-1, -1, 0],
    'JP': [1, 1, 0],     # BOJ hiking from ZIRP
    'CH': [-1, -1, -1],  # SNB cutting
    'AU': [-1, 0, 0],
    'CA': [-1, -1, -1],
    'NZ': [-1, -1, -1],
}


class ForexDataFetcher:
    """
    Fetches and caches macro fundamentals for all 8 forex economies.
    """

    CACHE_DAYS = 30

    def __init__(self):
        self._fred = None
        self._fred_ok = False
        fred_key = os.getenv('FRED_API_KEY')
        if fred_key:
            try:
                from fredapi import Fred
                self._fred = Fred(api_key=fred_key)
                self._fred_ok = True
                logger.info("ForexDataFetcher: FRED API ready")
            except Exception as e:
                logger.warning(f"FRED init failed: {e}")

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def get_country_macro(self, country: str, refresh: bool = False) -> dict:
        """
        Returns current macro snapshot for a country.
        Keys: rate, cpi_yoy, gdp_growth, real_rate, rate_trajectory
        """
        cache_path = CACHE_DIR / f'{country}_macro.json'

        if not refresh and cache_path.exists():
            age_days = (datetime.now() - datetime.fromtimestamp(
                cache_path.stat().st_mtime
            )).days
            if age_days < self.CACHE_DAYS:
                with open(cache_path) as f:
                    return json.load(f)

        macro = self._fetch_macro(country)
        with open(cache_path, 'w') as f:
            json.dump(macro, f, indent=2, default=str)
        return macro

    def get_rate_history(
        self, country: str, start: str = '2015-01-01', refresh: bool = False
    ) -> pd.Series:
        """
        Returns historical rate series for backtesting.
        Cached as parquet. ``refresh=True`` bypasses the 30-day cache-age
        gate and re-fetches unconditionally — used by
        scripts/refresh_macro_cache.py so a stale-but-not-yet-30-day-old
        cache can still be forced fresh on demand.
        """
        if not rate_vintage.is_sealed():
            path = rate_vintage.require_cache(
                rate_vintage.macro_cache_dir() / f'{country}_rates.parquet')
            return pd.read_parquet(path).squeeze()
        cache_path = CACHE_DIR / f'{country}_rates.parquet'
        if not refresh and cache_path.exists():
            age = (datetime.now() - datetime.fromtimestamp(
                cache_path.stat().st_mtime
            )).days
            if age < self.CACHE_DAYS:
                return pd.read_parquet(cache_path).squeeze()

        series = self._fetch_rate_history(country, start)
        if series is not None and not series.empty:
            series.to_frame('rate').to_parquet(cache_path)
        return series if series is not None else pd.Series(dtype=float)

    def get_cpi_history(
        self, country: str, start: str = '2015-01-01', refresh: bool = False
    ) -> pd.Series:
        """``refresh=True`` bypasses the 30-day cache-age gate — see
        ``get_rate_history``."""
        if not rate_vintage.is_sealed():
            if not FRED_CPI.get(country):
                return pd.Series(dtype=float)
            path = rate_vintage.require_cache(
                rate_vintage.macro_cache_dir() / f'{country}_cpi.parquet')
            return pd.read_parquet(path).squeeze()
        cache_path = CACHE_DIR / f'{country}_cpi.parquet'
        if not refresh and cache_path.exists():
            age = (datetime.now() - datetime.fromtimestamp(
                cache_path.stat().st_mtime
            )).days
            if age < self.CACHE_DAYS:
                return pd.read_parquet(cache_path).squeeze()

        series = self._fetch_cpi_history(country, start)
        if series is not None and not series.empty:
            series.to_frame('cpi').to_parquet(cache_path)
        return series if series is not None else pd.Series(dtype=float)

    def get_pair_differentials(
        self, base_country: str, quote_country: str, start: str = '2015-01-01'
    ) -> pd.DataFrame:
        """
        Returns daily DataFrame with rate/cpi differentials for a pair.
        Used by the backtester for historical signal generation.
        """
        base_rates = self.get_rate_history(base_country, start)
        quote_rates = self.get_rate_history(quote_country, start)
        base_cpi = self.get_cpi_history(base_country, start)
        quote_cpi = self.get_cpi_history(quote_country, start)

        df = pd.DataFrame(index=pd.date_range(start, datetime.now(), freq='B'))
        df['base_rate'] = base_rates.reindex(df.index).ffill()
        df['quote_rate'] = quote_rates.reindex(df.index).ffill()
        df['base_cpi'] = base_cpi.reindex(df.index).ffill()
        df['quote_cpi'] = quote_cpi.reindex(df.index).ffill()

        df['rate_differential'] = df['base_rate'] - df['quote_rate']
        df['cpi_differential'] = df['base_cpi'] - df['quote_cpi']
        df['real_rate_base'] = df['base_rate'] - df['base_cpi']
        df['real_rate_quote'] = df['quote_rate'] - df['quote_cpi']
        df['real_rate_differential'] = df['real_rate_base'] - df['real_rate_quote']

        # Rate differential momentum: 1-month change
        df['rate_diff_momentum'] = df['rate_differential'].diff(21)

        return df.dropna(subset=['rate_differential'])

    # ------------------------------------------------------------------ #
    # Internal fetch                                                       #
    # ------------------------------------------------------------------ #

    def _fetch_macro(self, country: str) -> dict:
        rate = FALLBACK_RATES.get(country, 2.0)
        cpi = FALLBACK_CPI.get(country, 2.0)
        gdp = FALLBACK_GDP_GROWTH.get(country, 1.0)
        trajectory = RATE_TRAJECTORY.get(country, [0, 0, 0])
        synthetic_fields = ['rate', 'cpi_yoy', 'gdp_growth', 'rate_trajectory']
        source_map = {
            'rate': 'fallback_static',
            'cpi_yoy': 'fallback_static',
            'gdp_growth': 'fallback_static',
            'rate_trajectory': 'manual_prior',
        }

        if self._fred_ok:
            from sovereign.forex.degraded_sentinel import flag_degraded

            try:
                rate = self._fred_latest(FRED_RATES.get(country, ''), rate)
                source_map['rate'] = 'fred'
            except FredFetchError as e:
                flag_degraded(country, f"FRED rate fetch failed, using fallback_static: {e}", source="fred_rate")
            except Exception as e:
                logger.warning(f"FRED rate fetch for {country}: {e}")

            try:
                cpi = self._fred_yoy(FRED_CPI.get(country, ''), cpi, country)
                source_map['cpi_yoy'] = 'fred'
            except FredFetchError as e:
                flag_degraded(country, f"FRED CPI fetch failed, using fallback_static: {e}", source="fred_cpi")
            except Exception as e:
                logger.warning(f"FRED CPI fetch for {country}: {e}")

            try:
                if country in FRED_GDP:
                    gdp = self._fred_qoq(FRED_GDP[country], gdp)
                    source_map['gdp_growth'] = 'fred'
            except FredFetchError as e:
                flag_degraded(country, f"FRED GDP fetch failed, using fallback_static: {e}", source="fred_gdp")
            except Exception as e:
                logger.warning(f"FRED GDP fetch for {country}: {e}")
        else:
            # Try yfinance rate proxy for US only (most reliable)
            if country == 'US':
                try:
                    import yfinance as yf
                    data = yf.download('^IRX', period='5d', progress=False, auto_adjust=True)
                    if not data.empty:
                        close = data['Close'].iloc[-1]
                        if hasattr(close, 'item'):
                            close = close.item()
                        rate = float(close)
                        source_map['rate'] = 'yfinance_proxy'
                except Exception:
                    pass

        synthetic_fields = [
            field for field, source in source_map.items()
            if source in {'fallback_static', 'manual_prior'}
        ]
        if synthetic_fields:
            logger.warning(
                "ForexDataFetcher using synthetic macro state for %s: %s",
                country,
                ', '.join(synthetic_fields),
            )

        return {
            'country': country,
            'rate': rate,
            'cpi_yoy': cpi,
            'gdp_growth': gdp,
            'real_rate': rate - cpi,
            'rate_trajectory': trajectory,
            'source_map': source_map,
            'synthetic_fields': synthetic_fields,
            'as_of': datetime.now().strftime('%Y-%m-%d'),
        }

    def _fred_latest(self, series_id: str, fallback: float) -> float:
        """Returns the latest value from a FRED series. Raises FredFetchError
        (never silently returns ``fallback``) so the caller can label the
        result honestly in source_map instead of defaulting to 'fred'."""
        if not series_id or not self._fred:
            raise FredFetchError(f"no FRED series configured or client unavailable (series_id={series_id!r})")
        try:
            s = self._fred.get_series(series_id)
            val = s.dropna().iloc[-1]
            return float(val)
        except Exception as e:
            raise FredFetchError(f"FRED get_series({series_id!r}) failed: {e}") from e

    def _fred_yoy(self, series_id: str, fallback: float, country: str = '') -> float:
        """Year-over-year % change. Handles monthly, quarterly, and pre-computed
        YoY series. Raises FredFetchError (never silently returns ``fallback``)
        on missing config, fetch failure, or insufficient history."""
        if not series_id or not self._fred:
            raise FredFetchError(f"no FRED CPI series configured for {country!r} (series_id={series_id!r})")
        try:
            s = self._fred.get_series(series_id)
            s = s.dropna()
            if country in DIRECT_YOY_CPI:
                return float(s.iloc[-1])
            periods = 4 if country in QUARTERLY_CPI else 12
            if len(s) >= periods + 1:
                yoy = (s.iloc[-1] / s.iloc[-(periods + 1)] - 1) * 100
                return float(yoy)
            raise FredFetchError(
                f"insufficient FRED CPI history for {country!r}: {len(s)} points, need {periods + 1}"
            )
        except FredFetchError:
            raise
        except Exception as e:
            raise FredFetchError(f"FRED CPI fetch failed for {country!r}: {e}") from e

    def _fred_qoq(self, series_id: str, fallback: float) -> float:
        """Quarter-over-quarter annualized GDP growth. Raises FredFetchError
        (never silently returns ``fallback``) on missing config, fetch
        failure, or insufficient history."""
        if not series_id or not self._fred:
            raise FredFetchError(f"no FRED GDP series configured (series_id={series_id!r})")
        try:
            s = self._fred.get_series(series_id)
            s = s.dropna()
            if len(s) >= 5:
                qoq = (s.iloc[-1] / s.iloc[-5] - 1) * 100
                return float(qoq)
            raise FredFetchError(f"insufficient FRED GDP history: {len(s)} points, need 5")
        except FredFetchError:
            raise
        except Exception as e:
            raise FredFetchError(f"FRED GDP fetch failed: {e}") from e

    def _fetch_rate_history(
        self, country: str, start: str
    ) -> Optional[pd.Series]:
        if self._fred_ok and country in FRED_RATES:
            try:
                s = self._fred.get_series(
                    FRED_RATES[country], observation_start=start
                )
                s = s.dropna().asfreq('B').ffill()
                s.name = f'{country}_rate'
                return s
            except Exception as e:
                logger.warning(f"FRED rate history {country}: {e}")

        # Fallback: flat series at current rate
        logger.info(f"Using fallback flat rate history for {country}")
        idx = pd.date_range(start, datetime.now(), freq='B')
        current = FALLBACK_RATES.get(country, 2.0)
        return pd.Series(current, index=idx, name=f'{country}_rate')

    def _fetch_ons_uk_cpi_yoy(self, start: str) -> pd.Series:
        """UK CPI annual rate direct from ONS (series D7G7, dataset MM23).
        Raises on any failure — never returns a fabricated fallback. Live
        endpoint, no API key required."""
        r = requests.get(ONS_UK_CPI_YOY_URL, timeout=15)
        r.raise_for_status()
        d = r.json()
        months = d.get("months", [])
        if not months:
            raise RuntimeError("ONS response had no monthly observations")
        rows = []
        for m in months:
            try:
                ts = pd.Timestamp(f"{m['year']}-{m['month']}-01")
            except Exception:
                continue
            try:
                rows.append((ts, float(m["value"])))
            except (TypeError, ValueError):
                continue
        if not rows:
            raise RuntimeError("ONS response had no parseable observations")
        s = pd.Series({t: v for t, v in rows}).sort_index()
        s = s[s.index >= pd.Timestamp(start)]
        if s.empty:
            raise RuntimeError(f"ONS series has no observations on/after {start}")
        s.name = "UK_cpi_yoy"
        return s

    def _fetch_abs_au_cpi_index(self, start: str) -> pd.Series:
        """Australia CPI index numbers direct from the ABS SDMX data API
        (MEASURE=1, INDEX=10001 all-groups, REGION=50 national, quarterly).
        Raises on any failure — never returns a fabricated fallback. Live
        endpoint, no API key required."""
        start_q = f"{pd.Timestamp(start).year}-Q1"
        r = requests.get(
            ABS_AU_CPI_INDEX_URL, params={"startPeriod": start_q}, timeout=20
        )
        r.raise_for_status()
        d = r.json()
        datasets = d.get("data", {}).get("dataSets", [])
        if not datasets or "series" not in datasets[0]:
            raise RuntimeError(f"ABS response had no series: {d!r}"[:300])
        series_block = datasets[0]["series"]
        # single-slice key is always all-zero indices for a fully-keyed query
        obs = next(iter(series_block.values()))["observations"]
        structures = d["data"]["structures"][0]
        timedim = next(
            x for x in structures["dimensions"]["observation"]
            if x["id"] == "TIME_PERIOD"
        )
        values = timedim["values"]
        rows = []
        for k, v in obs.items():
            idx = int(k.split(":")[0])
            period = values[idx]["id"]           # e.g. "2026-Q2"
            ts = pd.Timestamp(values[idx]["start"])
            rows.append((ts, float(v[0])))
        if not rows:
            raise RuntimeError("ABS response had no observations")
        s = pd.Series({t: v for t, v in rows}).sort_index()
        s = s[s.index >= pd.Timestamp(start)]
        if s.empty:
            raise RuntimeError(f"ABS series has no observations on/after {start}")
        s.name = "AU_cpi_index"
        return s

    def _estat_get(self, table_id: str, extra_params: dict) -> dict:
        """Raw e-Stat getStatsData call. Raises on any failure — never
        returns a fabricated fallback."""
        app_id = os.getenv("ESTAT_APPID")
        if not app_id:
            raise RuntimeError(
                "ESTAT_APPID not set in environment/.env — cannot fetch "
                "live JP CPI from e-Stat"
            )
        params = {"appId": app_id, "statsDataId": table_id, "limit": 2000, **extra_params}
        r = requests.get(ESTAT_BASE_URL, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _fetch_estat_jp_cpi_table(self, table_id: str) -> pd.Series:
        """One e-Stat base-year generation's national all-items YoY CPI,
        monthly observations only (e-Stat time codes carry annual/fiscal-
        year rollups in the same series — a "@time" code's last two digits
        are '00' for those and are skipped). Raises on any failure — never
        returns a fabricated fallback."""
        d = self._estat_get(table_id, ESTAT_JP_CPI_PARAMS)
        try:
            result = d["GET_STATS_DATA"]["RESULT"]
            status = result.get("STATUS")
            if status not in (0, "0"):
                raise RuntimeError(
                    f"e-Stat {table_id} STATUS={status}: {result.get('ERROR_MSG')}"
                )
            vals = d["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"]
        except (KeyError, TypeError) as e:
            raise RuntimeError(
                f"e-Stat response for {table_id} missing expected shape: {e}"
            ) from e
        if isinstance(vals, dict):
            vals = [vals]
        rows = []
        for v in vals:
            t = v.get("@time", "")
            if len(t) != 10 or t[6:8] == "00":
                continue   # annual ("...0000") / fiscal-year rollup, not a month
            try:
                ts = pd.Timestamp(year=int(t[0:4]), month=int(t[8:10]), day=1)
                val = float(v["$"])
            except (TypeError, ValueError):
                continue
            rows.append((ts, val))
        if not rows:
            raise RuntimeError(
                f"e-Stat table {table_id} returned no parseable monthly JP "
                f"CPI YoY observations"
            )
        s = pd.Series({t: v for t, v in rows}).sort_index()
        s.name = f"JP_cpi_yoy_{table_id}"
        return s

    def _fetch_estat_jp_cpi_yoy(self, start: str) -> pd.Series:
        """Japan CPI YoY %, stitched across e-Stat's 2015/2020/2025 base-year
        generations (ESTAT_JP_CPI_TABLES — see the design-decision comment
        above it). Raises on any failure — never returns a fabricated
        fallback.

        Also persists the raw multi-generation pull, with an approximate
        per-observation publication date, to
        data/cache/macro_estat_raw/JP_cpi_provenance.parquet — the primary
        cache stays nominal-date indexed like every other country (spec
        039's already-documented legacy-tree limitation), but a future
        publication-vintage build (macro_pub) needs per-generation
        provenance this sidecar is the only place that survives."""
        per_gen: Dict[str, pd.Series] = {}
        for label, table_id in ESTAT_JP_CPI_TABLES:
            per_gen[label] = self._fetch_estat_jp_cpi_table(table_id)

        values: Dict[pd.Timestamp, float] = {}
        source_label: Dict[pd.Timestamp, str] = {}
        provenance_rows = []
        for label, table_id in ESTAT_JP_CPI_TABLES:   # oldest -> newest
            gen_open = pd.Timestamp(ESTAT_JP_CPI_GENERATION_OPEN_DATE[label])
            for ts, v in per_gen[label].items():
                values[ts] = v          # newer generation overwrites older
                source_label[ts] = label
                month_end = ts + pd.offsets.MonthEnd(0)
                first_pub = month_end + pd.Timedelta(days=ESTAT_JP_CPI_PUBLICATION_LAG_DAYS)
                est_available_from = max(first_pub, gen_open)
                provenance_rows.append({
                    "obs_month": ts,
                    "generation": label,
                    "table_id": table_id,
                    "value": v,
                    "generation_open_date": gen_open,
                    "est_available_from": est_available_from,
                })

        if not values:
            raise RuntimeError("e-Stat JP CPI: no generation returned any observations")

        merged = pd.Series(values).sort_index()
        idx = merged.index

        # Sanity guard: a fabricated splice at a generation seam would show
        # up as an implausible single-month jump. Refuse rather than accept.
        for i in range(1, len(idx)):
            prev_t, cur_t = idx[i - 1], idx[i]
            if source_label[cur_t] != source_label[prev_t]:
                jump = abs(merged.iloc[i] - merged.iloc[i - 1])
                if jump > ESTAT_JP_CPI_MAX_BOUNDARY_JUMP_PP:
                    raise RuntimeError(
                        f"e-Stat JP CPI generation boundary {prev_t.date()}->"
                        f"{cur_t.date()} ({source_label[prev_t]}->"
                        f"{source_label[cur_t]}) jumps {jump:.2f}pp, above the "
                        f"{ESTAT_JP_CPI_MAX_BOUNDARY_JUMP_PP}pp sanity limit — "
                        f"refusing to splice, not fabricating a value"
                    )

        merged = merged[merged.index >= pd.Timestamp(start)]
        if merged.empty:
            raise RuntimeError(f"e-Stat JP CPI has no observations on/after {start}")

        # Same flatline guard carry_scan.py's preflight applies to the cache
        # file — catch it here too, at the source, so a broken e-Stat
        # response can never masquerade as fresh live data.
        if len(merged) > 30 and merged.nunique() == 1:
            raise RuntimeError(
                "e-Stat JP CPI series came back flat — refusing to return a "
                "fabricated-looking series"
            )

        ESTAT_RAW_DIR.mkdir(parents=True, exist_ok=True)
        prov_df = pd.DataFrame(provenance_rows).sort_values(
            ["obs_month", "generation_open_date"]
        )
        prov_df.to_parquet(ESTAT_RAW_DIR / "JP_cpi_provenance.parquet")

        merged.name = "JP_cpi_yoy"
        return merged

    def _fetch_cpi_history(
        self, country: str, start: str
    ) -> Optional[pd.Series]:
        if country in NON_FRED_CPI_SOURCES:
            try:
                if country == "UK":
                    yoy = self._fetch_ons_uk_cpi_yoy(start)
                elif country == "AU":
                    idx = self._fetch_abs_au_cpi_index(start)
                    periods = 4 if country in QUARTERLY_CPI else 12
                    yoy = (idx.pct_change(periods) * 100).dropna()
                elif country == "JP":
                    yoy = self._fetch_estat_jp_cpi_yoy(start)
                else:
                    raise RuntimeError(f"no fetcher wired for {country!r}")
                yoy = yoy.asfreq("B").ffill()
                yoy.name = f"{country}_cpi_yoy"
                return yoy
            except Exception as e:
                logger.warning(
                    f"live {NON_FRED_CPI_SOURCES[country]} CPI fetch for "
                    f"{country}: {e} — falling back to FRED (likely SOURCE_DEAD)"
                )

        if self._fred_ok and country in FRED_CPI:
            try:
                s = self._fred.get_series(
                    FRED_CPI[country], observation_start=start
                )
                s = s.dropna()
                if country in DIRECT_YOY_CPI:
                    yoy = s  # already YoY %
                else:
                    periods = 4 if country in QUARTERLY_CPI else 12
                    yoy = s.pct_change(periods) * 100
                    yoy = yoy.dropna()
                yoy = yoy.asfreq('B').ffill()
                yoy.name = f'{country}_cpi_yoy'
                return yoy
            except Exception as e:
                logger.warning(f"FRED CPI history {country}: {e}")

        # NO FLAT FALLBACK. A constant CPI series written to disk is the exact
        # defect this pipeline spent 2026-08-26/27 removing: JP_cpi was a
        # hardcoded flat 3.2 that passed every staleness check for months,
        # because forward-fill made it look fresh. Returning None here makes the
        # absence visible to carry_scan's preflight instead of laundering a
        # constant into a signal input. (Restored 2026-08-27 — a later change
        # reintroduced the flat path and broke
        # test_fetch_cpi_history_returns_none_when_live_source_fails.)
        logger.warning(
            f"CPI history {country}: no live source succeeded — returning None "
            f"rather than a flat FALLBACK_CPI series")
        return None
