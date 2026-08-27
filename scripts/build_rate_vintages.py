#!/usr/bin/env python3
"""Build nominal-vintage and publication-vintage macro caches from FRED/ALFRED.

Spec 039. Two output trees, identical shape to data/cache/macro/:

  data/cache/macro_nominal/{CC}_{rates,cpi}.parquet
      Latest-revision values indexed by their NOMINAL observation date, business
      -day forward filled. This is what the sealed v015 generator saw: the value
      for month M is visible from the first day of month M, ~30-45 days before it
      was published.

  data/cache/macro_pub/{CC}_{rates,cpi}.parquet
      Same values indexed by the date they FIRST BECAME AVAILABLE (ALFRED
      realtime_start). On date d the series carries the most recent observation
      whose first-availability date is <= d, at the revision current on d. Dates
      before the first publication are NaN and stay NaN — never filled.

Nothing here touches data/proof/ or data/cache/macro/.

The two daily series (ECBDFR, IUDSOIA) get a fixed publication lag rather than
ALFRED realtime, because ALFRED only began tracking them in 2021 and stamps every
earlier observation with the series' ALFRED-add date. Post-add measured lags are
0 days (ECBDFR) and 2 days (IUDSOIA); those constants are applied to the whole
span. Documented in DAILY_SERIES_LAG_DAYS below.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sovereign.forex.data_fetcher import (  # noqa: E402
    FRED_CPI, FRED_RATES, QUARTERLY_CPI, DIRECT_YOY_CPI,
)

OBS_START = "2012-01-01"          # warm-up: YoY needs 12 months before 2014
INDEX_START = "2014-01-01"
INDEX_END = "2026-08-01"

# ALFRED does not carry honest pre-2021 realtime for these daily series.
# Measured lag on observations published after the ALFRED-add date.
DAILY_SERIES_LAG_DAYS = {"ECBDFR": 0, "IUDSOIA": 2}

RAW_DIR = ROOT / "data" / "cache" / "macro_vintage_raw"
NOMINAL_DIR = ROOT / "data" / "cache" / "macro_nominal"
PUB_DIR = ROOT / "data" / "cache" / "macro_pub"


def _api_key() -> str:
    import os
    for env_path in (ROOT / ".env", Path("/Users/taboost/passing-funded-account-1-/.env")):
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.strip().startswith("FRED_API_KEY="):
                    return line.split("=", 1)[1].strip()
    key = os.environ.get("FRED_API_KEY", "")
    if not key:
        raise SystemExit("FRED_API_KEY not found in .env or environment")
    return key


def fetch_realtime(series_id: str, key: str) -> pd.DataFrame:
    """Every (observation date, value, first-availability date) row ALFRED holds."""
    cache = RAW_DIR / f"{series_id}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    q = urllib.parse.urlencode({
        "series_id": series_id, "api_key": key, "file_type": "json",
        "observation_start": OBS_START,
        "realtime_start": "1776-07-04", "realtime_end": "9999-12-31",
    })
    with urllib.request.urlopen(
        "https://api.stlouisfed.org/fred/series/observations?" + q
    ) as resp:
        payload = json.load(resp)
    df = pd.DataFrame(payload["observations"])
    df = df[df["value"] != "."].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["realtime_start"] = pd.to_datetime(df["realtime_start"])
    df["value"] = df["value"].astype(float)
    df = df[["date", "realtime_start", "value"]].sort_values(["date", "realtime_start"])
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    return df


def availability(df: pd.DataFrame, series_id: str) -> pd.DataFrame:
    """Attach the honest first-availability date for each (obs, revision) row."""
    out = df.copy()
    lag = DAILY_SERIES_LAG_DAYS.get(series_id)
    if lag is not None:
        out["avail"] = out["date"] + pd.Timedelta(days=lag)
    else:
        out["avail"] = out["realtime_start"]
    return out


def _event_snapshots(rows: pd.DataFrame):
    """Yield (availability date, as-of-that-date observation series)."""
    rows = rows.sort_values("avail")
    snapshot: dict[pd.Timestamp, float] = {}
    for avail, chunk in rows.groupby("avail", sort=True):
        for _, r in chunk.iterrows():
            snapshot[r["date"]] = r["value"]
        yield avail, pd.Series(snapshot).sort_index()


def build_rate(series_id: str, key: str, mode: str) -> pd.Series:
    rows = availability(fetch_realtime(series_id, key), series_id)
    idx = pd.date_range(INDEX_START, INDEX_END, freq="B")
    if mode == "nominal":
        latest = rows.sort_values("realtime_start").groupby("date")["value"].last()
        return latest.reindex(latest.index.union(idx)).ffill().reindex(idx)
    points = {}
    for avail, snap in _event_snapshots(rows):
        points[avail] = float(snap.iloc[-1])
    s = pd.Series(points).sort_index()
    return s.reindex(s.index.union(idx)).ffill().reindex(idx)


def build_cpi(country: str, series_id: str, key: str, mode: str) -> pd.Series:
    periods = 4 if country in QUARTERLY_CPI else 12
    rows = availability(fetch_realtime(series_id, key), series_id)
    idx = pd.date_range(INDEX_START, INDEX_END, freq="B")

    def yoy(snap: pd.Series) -> float:
        if country in DIRECT_YOY_CPI:
            return float(snap.iloc[-1])
        chg = (snap.pct_change(periods) * 100).dropna()
        return float(chg.iloc[-1]) if len(chg) else float("nan")

    if mode == "nominal":
        latest = rows.sort_values("realtime_start").groupby("date")["value"].last()
        if country in DIRECT_YOY_CPI:
            s = latest
        else:
            s = (latest.pct_change(periods) * 100).dropna()
        return s.reindex(s.index.union(idx)).ffill().reindex(idx)

    points = {}
    for avail, snap in _event_snapshots(rows):
        v = yoy(snap)
        if v == v:  # not NaN
            points[avail] = v
    s = pd.Series(points).sort_index()
    return s.reindex(s.index.union(idx)).ffill().reindex(idx)


def main() -> int:
    key = _api_key()
    NOMINAL_DIR.mkdir(parents=True, exist_ok=True)
    PUB_DIR.mkdir(parents=True, exist_ok=True)
    countries = ["US", "EU", "UK", "JP", "AU"]
    for mode, out_dir in (("nominal", NOMINAL_DIR), ("publication", PUB_DIR)):
        for cc in countries:
            rid = FRED_RATES.get(cc, "")
            if rid:
                s = build_rate(rid, key, mode)
                s.to_frame("rate").to_parquet(out_dir / f"{cc}_rates.parquet")
                print(f"{mode:11} {cc}_rates  {rid:18} "
                      f"first={str(s.first_valid_index())[:10]} n={s.notna().sum()} "
                      f"last={s.dropna().iloc[-1]:.3f}")
            cid = FRED_CPI.get(cc, "")
            if cid:
                s = build_cpi(cc, cid, key, mode)
                s.to_frame("cpi").to_parquet(out_dir / f"{cc}_cpi.parquet")
                print(f"{mode:11} {cc}_cpi    {cid:18} "
                      f"first={str(s.first_valid_index())[:10]} n={s.notna().sum()} "
                      f"last={s.dropna().iloc[-1]:.3f}")
            else:
                print(f"{mode:11} {cc}_cpi    (no FRED series — engine uses FALLBACK_CPI)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
