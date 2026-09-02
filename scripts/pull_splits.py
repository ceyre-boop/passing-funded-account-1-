#!/usr/bin/env python3
"""All US splits 2016-> . Used to drop candidate-days corrupted by a split."""
import json, os, time, urllib.request, urllib.error
KEY = os.environ["POLYGON_API_KEY"]
url = ("https://api.polygon.io/v3/reference/splits?execution_date.gte=2016-01-01"
       f"&limit=1000&order=asc&sort=execution_date&apiKey={KEY}")
out, page = [], 0
while url:
    for attempt in range(8):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                d = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(13 * (attempt + 1)); continue
            raise
    out += d.get("results") or []
    page += 1
    print(f"  page {page}: {len(out)} splits", flush=True)
    nxt = d.get("next_url")
    url = f"{nxt}&apiKey={KEY}" if nxt else None
    if url:
        time.sleep(12.5)
json.dump(out, open("data/nasdaq/splits.json", "w"))
rev = sum(1 for s in out if s["split_from"] > s["split_to"])
print(f"\n{len(out):,} splits since 2016   reverse splits: {rev:,}")
