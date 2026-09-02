#!/usr/bin/env python3
"""Pull 2016-> daily bars for the whole NASDAQ universe from Alpaca SIP.

Delisted names included — verified entitled (SIVB 2023-03-09, ATVI 2023).
Writes one gzipped JSONL per symbol batch so a rerun resumes from disk.
"""
import gzip, json, os, sys, time, urllib.parse, urllib.request, urllib.error

KEY, SEC = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
OUT = "data/nasdaq/daily"
os.makedirs(OUT, exist_ok=True)
START, END = "2016-01-01", "2026-08-31"
BATCH = 100

uni = json.load(open("data/nasdaq/universe.json"))
syms = [r["ticker"] for r in uni if r["ticker"].isalpha() and len(r["ticker"]) <= 5]
print(f"universe {len(uni)} -> {len(syms)} clean alpha tickers", flush=True)

def req(url):
    r = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1)); continue
            if e.code == 422:
                return None
            raise
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None

batches = [syms[i:i+BATCH] for i in range(0, len(syms), BATCH)]
for bi, batch in enumerate(batches):
    path = f"{OUT}/batch_{bi:03d}.jsonl.gz"
    if os.path.exists(path):
        continue
    got, token, pages = {}, None, 0
    while True:
        q = {"symbols": ",".join(batch), "timeframe": "1Day",
             "start": START, "end": END, "feed": "sip", "limit": "10000",
             "adjustment": "raw"}
        if token:
            q["page_token"] = token
        d = req("https://data.alpaca.markets/v2/stocks/bars?" + urllib.parse.urlencode(q))
        if d is None:
            break
        for s, bars in (d.get("bars") or {}).items():
            got.setdefault(s, []).extend(bars)
        pages += 1
        token = d.get("next_page_token")
        if not token:
            break
    with gzip.open(path, "wt") as f:
        for s, bars in got.items():
            f.write(json.dumps({"symbol": s, "bars": bars}) + "\n")
    n = sum(len(v) for v in got.values())
    print(f"  batch {bi+1}/{len(batches)}: {len(got)} symbols, {n:,} bars, {pages} pages", flush=True)
print("done", flush=True)
