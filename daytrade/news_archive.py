#!/usr/bin/env python3
"""NEWS ARCHIVE — permanent, append-only record of every headline the system
fetches. No model call, no API key required beyond Polygon's, zero cost. This
is what keeps working through the Anthropic API blackout (blocked until
2026-09-01): it needs nothing the blackout takes away.

Reuses polygon_news.fetch_headlines() — this file is deliberately NOT a
second Polygon client. It only durably records what that function already
returns; it adds nothing to the prompt surface.

Raw headlines are otherwise never persisted (see decision ledger / evidence
history): only headlines a model chose to cite survive today, inside
data/daytrade/operator/evidence.jsonl. This file is the record of everything
the system SAW, cited or not.

Preserves Polygon's per-article sentiment fields (`polygon_sentiment` for the
queried symbol, plus the full raw `insights` list covering every ticker
Polygon scored). polygon_news.py:6-9 documents why that sentiment is kept OUT
of any Claude prompt: it is an independent baseline the model's read gets
scored against, and feeding it in would contaminate the comparison. Archiving
it here is not the same as feeding it to a model — nothing in this file, and
nothing that reads this archive, may pass those fields into a prompt.

Dedup is on Polygon's own article `id`, never headline text, so repeated
fetches inside the 5-minute tick loop never duplicate a row. Each row records
`archived_at` — when THIS system first saw the article — kept distinct from
Polygon's own `published_utc`. The gap between the two is itself research
material: how far behind the wire this feed runs.

Fail soft on fetch: a Polygon outage (NewsUnavailable) must not abort the
caller — same non-fatal-but-visible pattern as REFRESH_RC / PLAN_RC in
operator_tick.sh. Fail LOUD on read: a corrupt archive line is refused, never
silently skipped (I10 doctrine — same shape as build_dashboard_data.py's
load_jsonl and chain.py's rows()).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polygon_news import fetch_headlines, NewsUnavailable  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "daytrade" / "news_archive.jsonl"


class ArchiveError(RuntimeError):
    """A corrupt archive line. Never repaired silently — I10 doctrine."""


def rows() -> list[dict]:
    """Every archived row. Fails loud (ArchiveError) on a corrupt line rather
    than skipping it — a silently-dropped row is silent data loss, exactly
    what this file exists to prevent."""
    if not ARCHIVE.exists():
        return []
    out = []
    for i, line in enumerate(ARCHIVE.open(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ArchiveError(f"{ARCHIVE.name}:{i} is not JSON ({e})") from e
    return out


def _seen_ids(symbol: str) -> set[str]:
    return {r["article_id"] for r in rows()
            if r.get("symbol") == symbol and r.get("article_id")}


def _append(row: dict) -> None:
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with ARCHIVE.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def archive(symbol: str, limit: int = 12) -> dict:
    """Fetch + append every not-yet-seen article for `symbol`. Never raises
    on a Polygon outage — that is the entire point of running this every
    tick through the blackout. Raises ArchiveError only if the archive
    ITSELF is already corrupt (I10) — that is a real problem, not a soft one.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        headlines = fetch_headlines(symbol, limit)
    except NewsUnavailable as e:
        return {"ok": False, "reason": str(e), "fetched": 0,
                "added": 0, "skipped_no_id": 0, "skipped_dup": 0}

    seen = _seen_ids(symbol)          # raises ArchiveError if corrupt
    added = skipped_no_id = skipped_dup = 0
    for h in headlines:
        article_id = h.get("id") or ""
        if not article_id:
            skipped_no_id += 1
            print(f"  !! archive: article missing Polygon id, skipped "
                  f"(title={h.get('title', '')[:60]!r}) — cannot dedup safely")
            continue
        if article_id in seen:
            skipped_dup += 1
            continue
        row = {
            "kind": "headline",
            "symbol": symbol,
            "article_id": article_id,
            "archived_at": now,
            "published_utc": h.get("published_utc", ""),
            "publisher": h.get("publisher", ""),
            "title": h.get("title", ""),
            "description": h.get("description", ""),
            "url": h.get("url", ""),
            # Independent baseline — polygon_news.py:6-9. Archived for the
            # scorecard comparison. NEVER pass into a prompt.
            "polygon_sentiment": h.get("polygon_sentiment"),
            "insights": h.get("insights", []),
        }
        _append(row)
        seen.add(article_id)
        added += 1
    return {"ok": True, "reason": None, "fetched": len(headlines),
            "added": added, "skipped_no_id": skipped_no_id,
            "skipped_dup": skipped_dup}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="permanent headline archive (no model, no cost)")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--limit", type=int, default=12)
    a = ap.parse_args(argv)
    try:
        result = archive(a.symbol, a.limit)
    except ArchiveError as e:
        print(f"  !! ARCHIVE CORRUPT: {e}")
        return 1
    if not result["ok"]:
        print(f"  !! news archive unavailable for {a.symbol}: "
              f"{result['reason']} (non-fatal)")
        return 0
    print(f"  {a.symbol}: +{result['added']} new headline(s) archived "
          f"({result['fetched']} fetched, {result['skipped_dup']} dup, "
          f"{result['skipped_no_id']} no-id)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
