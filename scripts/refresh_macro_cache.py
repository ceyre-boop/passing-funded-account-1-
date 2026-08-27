#!/usr/bin/env python3
"""Refresh the macro rate/CPI parquet caches — the single documented command
for fixing REFRESHABLE staleness (see scripts/data_health.py's CACHE_STALE
state). This does NOT fix SOURCE_DEAD or NO_SERIES states — those need a
different source wired in ForexDataFetcher, not a re-fetch.

Bypasses ForexDataFetcher's 30-day cache-age gate via ``refresh=True`` so a
cache that is stale-but-under-30-days-old still gets a fresh pull; that gate
exists to avoid hammering FRED/ONS/ABS on every backtest run, not to block a
deliberate refresh.

Usage:
  python3 scripts/refresh_macro_cache.py                 # all 8 countries
  python3 scripts/refresh_macro_cache.py --countries US EU
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sovereign.forex.data_fetcher import ForexDataFetcher  # noqa: E402

MACRO_CACHE = ROOT / "data" / "cache" / "macro"
ALL_COUNTRIES = ("US", "EU", "UK", "JP", "AU", "CH", "CA", "NZ")


def _last_date(country: str, kind: str):
    import pandas as pd
    f = MACRO_CACHE / f"{country}_{kind}.parquet"
    if not f.exists():
        return None
    try:
        return pd.read_parquet(f).index.max().date()
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--countries", nargs="+", default=list(ALL_COUNTRIES))
    ap.add_argument("--start", default="2015-01-01")
    a = ap.parse_args()

    fetcher = ForexDataFetcher()
    today = date.today()
    print(f"REFRESH MACRO CACHE — {today}\n")

    for c in a.countries:
        for kind, getter in (
            ("rates", fetcher.get_rate_history),
            ("cpi", fetcher.get_cpi_history),
        ):
            before = _last_date(c, kind)
            try:
                s = getter(c, start=a.start, refresh=True)
            except Exception as e:
                print(f"  ! {c}_{kind}: refresh raised {type(e).__name__}: {e}")
                continue
            after = _last_date(c, kind)
            n = len(s) if s is not None else 0
            moved = "" if before == after else "  (moved)"
            print(f"  {c}_{kind}: {before} -> {after}  ({n} rows){moved}")

    print("\nNote: this fixes CACHE_STALE only. Run scripts/data_health.py "
          "afterward — SOURCE_DEAD / NO_SERIES / SYNTHETIC states need a "
          "different source, not a refresh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
