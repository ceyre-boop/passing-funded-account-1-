#!/usr/bin/env python3
"""News archive: dedup, fail-soft on Polygon outage, fail-loud on a corrupt line."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import news_archive as na
from polygon_news import NewsUnavailable


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(na, "ARCHIVE", tmp_path / "news_archive.jsonl")
    return tmp_path


def _headline(id_="a1", title="Nvidia thing happens"):
    return {"id": id_, "title": title, "publisher": "Reuters",
            "published_utc": "2026-08-21T10:00:00Z", "description": "desc",
            "polygon_sentiment": "positive", "insights": [{"ticker": "NVDA",
            "sentiment": "positive", "sentiment_reasoning": "why"}],
            "url": "http://x"}


def test_archive_writes_new_rows_and_preserves_sentiment(sandbox, monkeypatch):
    monkeypatch.setattr(na, "fetch_headlines", lambda symbol, limit=12: [_headline("a1")])
    result = na.archive("NVDA")
    assert result == {"ok": True, "reason": None, "fetched": 1, "added": 1,
                       "skipped_no_id": 0, "skipped_dup": 0}
    rows = na.rows()
    assert len(rows) == 1
    assert rows[0]["article_id"] == "a1"
    assert rows[0]["polygon_sentiment"] == "positive"
    assert rows[0]["insights"][0]["ticker"] == "NVDA"
    assert "archived_at" in rows[0] and "published_utc" in rows[0]
    assert rows[0]["archived_at"] != rows[0]["published_utc"]


def test_dedup_across_repeated_fetches(sandbox, monkeypatch):
    """Same article id fetched twice across ticks must not duplicate a row."""
    monkeypatch.setattr(na, "fetch_headlines", lambda symbol, limit=12: [_headline("a1")])
    first = na.archive("NVDA")
    second = na.archive("NVDA")          # simulates the next 5-minute tick
    assert first["added"] == 1
    assert second["added"] == 0
    assert second["skipped_dup"] == 1
    assert len(na.rows()) == 1


def test_dedup_scoped_per_symbol(sandbox, monkeypatch):
    """Same article id under a different symbol is not a duplicate."""
    monkeypatch.setattr(na, "fetch_headlines", lambda symbol, limit=12: [_headline("a1")])
    na.archive("NVDA")
    result = na.archive("AAPL")
    assert result["added"] == 1
    assert len(na.rows()) == 2


def test_article_missing_id_is_skipped_not_silently_dropped(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(na, "fetch_headlines",
                         lambda symbol, limit=12: [_headline(id_="")])
    result = na.archive("NVDA")
    assert result["added"] == 0
    assert result["skipped_no_id"] == 1
    assert "cannot dedup safely" in capsys.readouterr().out
    assert na.rows() == []


def test_polygon_outage_is_non_fatal(sandbox, monkeypatch):
    def boom(symbol, limit=12):
        raise NewsUnavailable("polygon news HTTP 500: boom")
    monkeypatch.setattr(na, "fetch_headlines", boom)
    result = na.archive("NVDA")
    assert result["ok"] is False
    assert "boom" in result["reason"]
    assert result["added"] == 0


def test_main_returns_0_on_polygon_outage(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(na, "fetch_headlines",
                         lambda symbol, limit=12: (_ for _ in ()).throw(
                             NewsUnavailable("no key")))
    assert na.main(["--symbol", "NVDA"]) == 0


def test_corrupt_line_fails_loud_on_read(sandbox):
    """I10 doctrine: a corrupt archive line is refused, not skipped."""
    sandbox_archive = sandbox / "news_archive.jsonl"
    sandbox_archive.write_text('{"kind": "headline", "symbol": "NVDA"}\nNOT JSON\n')
    with pytest.raises(na.ArchiveError):
        na.rows()


def test_main_exits_nonzero_on_corrupt_archive(sandbox, monkeypatch):
    sandbox_archive = sandbox / "news_archive.jsonl"
    sandbox_archive.write_text("not json at all\n")
    monkeypatch.setattr(na, "fetch_headlines", lambda symbol, limit=12: [_headline("a1")])
    assert na.main(["--symbol", "NVDA"]) == 1
