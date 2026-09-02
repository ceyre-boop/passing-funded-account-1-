#!/usr/bin/env python3
"""Pull every NASDAQ common stock Polygon knows about — active AND delisted.

Delisted names are the whole point: a low-float screen's real risk lives in the
tail that stopped existing. A universe of today's listings measures survivors.
"""
import json, os, sys, time, urllib.request, urllib.error

KEY = os.environ["POLYGON_API_KEY"]
OUT = "data/nasdaq/universe.json"
os.makedirs("data/nasdaq", exist_ok=True)

def get(url):
    for attempt in range(8):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(13 * (attempt + 1)); continue
            raise
    raise RuntimeError("exhausted retries")

rows = {}
for active in ("true", "false"):
    url = (f"https://api.polygon.io/v3/reference/tickers?market=stocks"
           f"&exchange=XNAS&type=CS&active={active}&limit=1000&apiKey={KEY}")
    page = 0
    while url:
        d = get(url)
        for r in d.get("results") or []:
            rows[r["ticker"]] = {
                "ticker": r["ticker"],
                "name": r.get("name"),
                "active": r.get("active"),
                "delisted_utc": r.get("delisted_utc"),
                "cik": r.get("cik"),
            }
        page += 1
        print(f"  active={active} page {page}: total {len(rows)}", flush=True)
        nxt = d.get("next_url")
        url = f"{nxt}&apiKey={KEY}" if nxt else None
        if url:
            time.sleep(12.5)

with open(OUT, "w") as f:
    json.dump(sorted(rows.values(), key=lambda r: r["ticker"]), f, indent=1)

dead = sum(1 for r in rows.values() if not r["active"])
print(f"\nNASDAQ common stock, all time: {len(rows)}")
print(f"  still listed : {len(rows)-dead}")
print(f"  delisted     : {dead}   <- the survivorship correction")
