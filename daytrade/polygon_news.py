#!/usr/bin/env python3
"""Polygon news feed — the raw material news_claude.py reads.

Separated from news_claude.py so the brain can be tested against fixed headlines
without a network call, and so a feed swap never touches the scoring path.

Polygon's payload already carries per-article, per-ticker sentiment. That is NOT
passed to Claude — it is kept as an independent BASELINE to score the model
against. Feeding it in would contaminate the comparison, which is the whole
reason the scorecard exists.

Free tier throttles at ~5 requests/minute. One call per fetch, so the 5-minute
loop is comfortably inside it.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution.alpaca import load_env                       # noqa: E402

BASE = "https://api.polygon.io/v2/reference/news"


class NewsUnavailable(RuntimeError):
    """No headlines. Never silently downgraded to an empty list — an empty
    list and a broken feed are different facts, and a brain that can't tell
    them apart will confidently read nothing as 'quiet'."""


def fetch_headlines(symbol: str, limit: int = 12) -> list[dict]:
    import os
    load_env()
    key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not key:
        raise NewsUnavailable("POLYGON_API_KEY missing from .env")

    q = urllib.parse.urlencode({"ticker": symbol, "limit": limit, "apiKey": key})
    try:
        with urllib.request.urlopen(f"{BASE}?{q}", timeout=20) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise NewsUnavailable(
            f"polygon news HTTP {e.code}: {e.read().decode()[:160]}") from e
    except Exception as e:
        raise NewsUnavailable(f"polygon news failed: {e!r}") from e

    out = []
    for a in payload.get("results") or []:
        # Polygon's own sentiment for this ticker — the baseline, kept OUT of
        # the prompt so the model's read can be scored against it honestly.
        pol = next((i.get("sentiment") for i in (a.get("insights") or [])
                    if i.get("ticker") == symbol), None)
        out.append({
            "title": a.get("title", ""),
            "publisher": (a.get("publisher") or {}).get("name", ""),
            "published_utc": a.get("published_utc", ""),
            "description": (a.get("description") or "")[:400],
            "polygon_sentiment": pol,
            "url": a.get("article_url", ""),
        })
    return out


def baseline_bias(headlines: list[dict]) -> float | None:
    """Polygon's sentiment as a dumb baseline the model has to beat.
    +1 per positive article, -1 per negative, averaged. None if unlabelled."""
    vals = [{"positive": 1.0, "negative": -1.0, "neutral": 0.0}.get(h["polygon_sentiment"])
            for h in headlines if h.get("polygon_sentiment")]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="fetch headlines (no LLM, no cost)")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--limit", type=int, default=12)
    a = ap.parse_args()
    h = fetch_headlines(a.symbol, a.limit)
    print(f"{len(h)} headlines for {a.symbol}   "
          f"polygon baseline bias: {baseline_bias(h)}\n")
    for x in h:
        print(f"  [{x['published_utc'][:16]}] {x['polygon_sentiment'] or '-':8s} "
              f"{x['publisher'][:18]:18s} {x['title'][:72]}")
