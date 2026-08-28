#!/usr/bin/env python3
"""Spec 030 invariants (I36-I40). The chain check is always-on."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decision_ledger as dl
import bars as bars_mod

UTC = timezone.utc


def test_i36_real_ledger_chain_intact():
    """THE guard: the live ledger's hash chain verifies on every suite run."""
    assert dl.verify() == 0, "decision-ledger chain broken — history was rewritten"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "LEDGER", tmp_path / "decision_ledger.jsonl")
    monkeypatch.setattr(dl, "CALENDAR", tmp_path / "macro_calendar.md")
    # isolate from the real repo's machine-maintained calendars too, so a
    # snapshot test's outcome never depends on today's actual macro schedule
    monkeypatch.setattr(dl, "MACRO_CALENDAR_JSON", tmp_path / "macro_calendar.json")
    monkeypatch.setattr(dl, "FOMC_CALENDAR_JSON", tmp_path / "fomc_calendar.json")
    return tmp_path


def _bars(tmp_path, monkeypatch, symbol="NVDA", days=3):
    """Synthetic 5m RTH bars across `days` sessions."""
    import pandas as pd
    idx, vals = [], []
    for d in range(days):
        base = datetime(2026, 5, 4 + d, 13, 30, tzinfo=UTC)   # 09:30 ET
        for b in range(78):
            idx.append(base + timedelta(minutes=5 * b))
            vals.append(100.0 + d + b * 0.01)
    df = pd.DataFrame({"Open": vals, "High": [v * 1.002 for v in vals],
                       "Low": [v * 0.998 for v in vals], "Close": vals,
                       "Volume": [1000] * len(vals)},
                      index=pd.DatetimeIndex(idx, tz="UTC"))
    cache = tmp_path / "bars"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(bars_mod, "CACHE", cache)
    df.to_parquet(cache / f"{symbol}_5m.parquet")


# --------------------------------------------------------------------- I36

def test_i36_chain_detects_a_rewritten_row(sandbox, monkeypatch):
    _bars(sandbox, monkeypatch)
    dl.append({"kind": "decision_point", "source": "backfill", "a": 1})
    dl.append({"kind": "decision_point", "source": "backfill", "a": 2})
    assert dl.verify() == 0
    rows = dl._rows()
    rows[0]["a"] = 99                       # quietly edit the past
    dl.LEDGER.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    assert dl.verify() == 1


def test_i36_chain_detects_a_deleted_row(sandbox):
    for i in range(3):
        dl.append({"kind": "decision_point", "source": "backfill", "a": i})
    # the chain must be INTACT before the excision, or this test cannot tell
    # "a row was deleted" from "the chain was never linked" (mutation M34)
    assert dl.verify() == 0
    rows = dl._rows()
    del rows[1]                              # excise the middle
    dl.LEDGER.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    assert dl.verify() == 1


def test_corrupt_line_raises(sandbox):
    dl.append({"kind": "decision_point", "source": "backfill"})
    with dl.LEDGER.open("a") as fh:
        fh.write("{not json\n")
    with pytest.raises(dl.LedgerError, match="not JSON"):
        dl._rows()


# --------------------------------------------------------------------- I37

def test_i37_snapshot_uses_nothing_newer_than_as_of(sandbox, monkeypatch):
    _bars(sandbox, monkeypatch, days=3)
    t = datetime(2026, 5, 5, 14, 30, tzinfo=UTC)      # 10:30 ET, mid-session 2
    snap = dl.snapshot("NVDA", t, source="backfill")
    assert datetime.fromisoformat(snap["max_data_ts"]) <= t
    assert snap["session"] == "2026-05-05"


def test_i37_snapshot_is_reproducible(sandbox, monkeypatch):
    _bars(sandbox, monkeypatch, days=3)
    t = datetime(2026, 5, 5, 14, 30, tzinfo=UTC)
    a = dl.snapshot("NVDA", t, source="backfill")
    b = dl.snapshot("NVDA", t, source="backfill")
    for k in a:
        if k != "captured_at":
            assert a[k] == b[k]


def test_snapshot_refuses_without_history(sandbox, monkeypatch):
    _bars(sandbox, monkeypatch, days=1)
    with pytest.raises(dl.LedgerError, match="fewer than 6 prior bars"):
        dl.snapshot("NVDA", datetime(2026, 5, 4, 13, 35, tzinfo=UTC),
                    source="backfill")


def test_snapshot_refuses_without_any_cache(sandbox, monkeypatch):
    monkeypatch.setattr(bars_mod, "CACHE", sandbox / "nope")
    with pytest.raises(dl.LedgerError, match="nothing was knowable"):
        dl.snapshot("NVDA", datetime.now(UTC), source="backfill")


# --------------------------------------------------------------------- I38

def test_i38_source_is_always_present_and_distinguishing(sandbox, monkeypatch):
    _bars(sandbox, monkeypatch, days=3)
    t = datetime(2026, 5, 5, 14, 30, tzinfo=UTC)
    assert dl.snapshot("NVDA", t, source="backfill")["source"] == "backfill"
    assert dl.snapshot("NVDA", t, source="live")["source"] == "live"


def test_backfill_never_claims_headlines(sandbox, monkeypatch):
    """A backfilled row cannot honestly count headlines — it must be null,
    never a number reconstructed from a live feed. The stub below makes the
    feed SUCCEED, so a removed guard produces a real count (mutation M37)."""
    _bars(sandbox, monkeypatch, days=3)
    monkeypatch.setenv("POLYGON_API_KEY", "x")
    import polygon_news
    monkeypatch.setattr(polygon_news, "fetch_headlines", lambda *a, **k: [
        {"published_utc": "2026-05-05T14:00:00+00:00", "title": "t"}])
    t = datetime(2026, 5, 5, 14, 30, tzinfo=UTC)
    assert dl.snapshot("NVDA", t, source="backfill")["headlines_last_hour"] is None
    # ...and the live path DOES count it, proving the stub reaches the branch
    assert dl.snapshot("NVDA", t, source="live")["headlines_last_hour"] == 1


# --------------------------------------------------------------------- I40

def test_i40_backfill_is_idempotent(sandbox, monkeypatch):
    _bars(sandbox, monkeypatch, days=3)
    monkeypatch.setattr(dl, "DECISION_POINTS", ("10:00", "10:30"))
    dl.backfill("NVDA", days=3)
    first = len(dl._rows())
    assert first > 0
    dl.backfill("NVDA", days=3)
    assert len(dl._rows()) == first          # no duplicates on re-run


def test_regime_vocabulary_is_closed(sandbox, monkeypatch):
    _bars(sandbox, monkeypatch, days=3)
    snap = dl.snapshot("NVDA", datetime(2026, 5, 5, 14, 30, tzinfo=UTC),
                       source="backfill")
    assert snap["regimes"]
    for g in snap["regimes"]:
        assert g in dl.REGIMES


def test_event_day_absent_calendar_is_false(sandbox):
    """No sources present at all -> False, absence stated, never a guess."""
    assert dl._event_day("2026-05-05") is False


def test_event_day_fires_from_fred_json(sandbox):
    import json
    dl.MACRO_CALENDAR_JSON.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(),
        "events": [{"date": "2026-05-05", "release_id": 10,
                    "release_name": "Consumer Price Index"}],
    }))
    assert dl._event_day("2026-05-05") is True
    assert dl._event_day("2026-05-06") is False


def test_event_day_fires_from_fomc_json(sandbox):
    import json
    from datetime import date as _date
    dl.FOMC_CALENDAR_JSON.write_text(json.dumps({
        "verified_as_of": _date.today().isoformat(), "source_url": "x",
        "events": [{"date": "2026-05-05", "label": "FOMC decision"}],
    }))
    assert dl._event_day("2026-05-05") is True


def test_event_day_still_fires_from_legacy_md(sandbox):
    """Backward compatibility: the pre-existing substring-match behavior over
    macro_calendar.md keeps working exactly as before this change."""
    dl.CALENDAR.write_text("2026-05-05 (Tue)\n  some manual note\n")
    assert dl._event_day("2026-05-05") is True


def test_event_day_raises_loud_on_corrupt_json(sandbox):
    dl.MACRO_CALENDAR_JSON.write_text("{not valid json")
    with pytest.raises(dl.LedgerError):
        dl._event_day("2026-05-05")


def test_event_day_regime_label_reachable_end_to_end(sandbox, monkeypatch):
    """EVENT_DAY was permanently dead before this change — this is the
    regression guard that it is reachable now."""
    import json
    _bars(sandbox, monkeypatch, days=3)
    day = "2026-05-05"
    dl.MACRO_CALENDAR_JSON.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(),
        "events": [{"date": day, "release_id": 50,
                    "release_name": "Employment Situation"}],
    }))
    snap = dl.snapshot("NVDA", datetime(2026, 5, 5, 14, 30, tzinfo=UTC),
                       source="backfill")
    assert "EVENT_DAY" in snap["regimes"]
