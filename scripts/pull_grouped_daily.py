#!/usr/bin/env python3
"""Cache Polygon grouped-daily (whole US market, one call per session).

This is the DENOMINATOR ENGINE. Every "top mover" claim needs the count of
names that had the same setup and did NOT move. One call returns ~12.5k
tickers for one session, so N sessions == N calls.

Free tier is 5 req/min; we sleep accordingly and cache to disk so a rerun
costs nothing. Cached files are never re-fetched.
"""
import json, os, sys, time, urllib.request, urllib.error
from datetime import date, timedelta

KEY = os.environ.get("POLYGON_API_KEY")
if not KEY:
    sys.exit("POLYGON_API_KEY not set")
OUT = "data/nasdaq/grouped"
os.makedirs(OUT, exist_ok=True)

def sessions(end: date, n: int):
    """Weekdays back from `end`. Holidays return empty payloads; we keep them
    cached as empty so we never re-request a known-dead date."""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out

def fetch(d: date) -> bool:
    p = f"{OUT}/{d}.json"
    if os.path.exists(p):
        return False
    url = (f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{d}"
           f"?adjusted=true&apiKey={KEY}")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                payload = json.load(r)
            with open(p, "w") as f:
                json.dump(payload, f)
            print(f"  {d}  tickers={payload.get('resultsCount', 0)}", flush=True)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 13 * (attempt + 1)
                print(f"  {d}  429 -> sleep {wait}s", flush=True)
                time.sleep(wait)
                continue
            print(f"  {d}  HTTP {e.code}", flush=True)
            return False
        except Exception as e:                      # noqa: BLE001
            print(f"  {d}  {type(e).__name__}: {e}", flush=True)
            time.sleep(5)
    return False

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    end = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 8, 21)
    todo = sessions(end, n)
    have = sum(os.path.exists(f"{OUT}/{d}.json") for d in todo)
    print(f"pulling {n} sessions ending {end}  ({have} already cached)", flush=True)
    for d in todo:
        if fetch(d):
            time.sleep(12.5)          # 5 req/min ceiling
    print("done", flush=True)
