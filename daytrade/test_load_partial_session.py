"""Tests for bars.load_partial_session — the same-day live accessor added to
unblock write_baseline_plan.py (the writer was reading load_sessions, whose
70-bar completeness gate excludes today's session until ~15:20 ET even though
find_entry only ever needs bars through TRIGGER_END, 11:00).

Every fixture here is built by TRUNCATING a real cached session
(NVDA 2026-08-21, a genuine 78-bar complete day already in
data/daytrade/bars/NVDA_5m.parquet) — no price is fabricated anywhere in this
file, per CLAUDE.md rule 10 (never modify sealed evaluation data) and the
project-wide "never fabricate a bar" doctrine in bars.py itself.
"""
from __future__ import annotations

import pandas as pd
import pytest

import bars
import ceiling

REAL_CACHE = bars.ROOT / "data" / "daytrade" / "bars" / "NVDA_5m.parquet"
DAY = pd.Timestamp("2026-08-21").date()


def _real_day_bars() -> pd.DataFrame:
    """The genuine, unmodified 78-bar 2026-08-21 session from the real cache."""
    df = pd.read_parquet(REAL_CACHE)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(bars.ET)
    day_bars = df[df.index.date == DAY]
    assert len(day_bars) == 78, "fixture assumption: 2026-08-21 is a full session"
    return day_bars


def _write_cache(tmp_path, monkeypatch, frame: pd.DataFrame) -> None:
    """Point bars.CACHE at an isolated tmp dir holding only `frame`."""
    cache_dir = tmp_path / "bars_cache"
    cache_dir.mkdir()
    monkeypatch.setattr(bars, "CACHE", cache_dir)
    frame.to_parquet(cache_dir / "NVDA_5m.parquet")


def test_partial_session_through_trigger_end_matches_full_session_entry(
    tmp_path, monkeypatch
):
    """A partial day truncated to bars through 11:00 (19 bars: 09:30..11:00)
    returns a usable Session, and ceiling.find_entry on it produces the exact
    same Entry find_entry produces on the real, complete 78-bar session —
    because find_entry structurally never looks past TRIGGER_END anyway."""
    full = _real_day_bars()
    truncated = full[full.index.strftime("%H:%M") <= ceiling.TRIGGER_END]
    assert len(truncated) == 19, "09:30..11:00 at 5m should be 19 bars"
    _write_cache(tmp_path, monkeypatch, truncated)

    partial = bars.load_partial_session("NVDA", DAY, ceiling.TRIGGER_END, "5m")
    assert partial is not None
    assert len(partial) == 19
    assert partial.symbol == "NVDA"
    assert partial.day == DAY

    got = ceiling.find_entry(partial)
    assert got is not None

    expected = ceiling.find_entry(
        bars.Session("NVDA", DAY, full)  # the real, complete session
    )
    assert got == expected


def test_internal_gap_inside_window_still_raises(tmp_path, monkeypatch):
    """Deleting one bar inside the requested window must still raise, exactly
    as load_sessions does — a hole is corruption whether the session has
    finished or is still forming, and it is never silently patched over."""
    full = _real_day_bars()
    truncated = full[full.index.strftime("%H:%M") <= ceiling.TRIGGER_END]
    # drop an interior bar (10:15), keeping first and last so the window still
    # looks "fully present" by bar count alone if the gap check were skipped
    gapped = truncated.drop(truncated.index[9])  # 09:30 + 9*5min = 10:15
    _write_cache(tmp_path, monkeypatch, gapped)

    with pytest.raises(bars.BarDataError, match="missing bar"):
        bars.load_partial_session("NVDA", DAY, ceiling.TRIGGER_END, "5m")


def test_incomplete_window_returns_none(tmp_path, monkeypatch):
    """Only 10 bars on file (through 10:15) when the trigger window needs 19
    (through 11:00) — the window is not yet fully present, so this must
    return None, not a truncated partial-of-a-partial."""
    full = _real_day_bars()
    incomplete = full[full.index.strftime("%H:%M") <= "10:15"]
    assert 0 < len(incomplete) < 19
    _write_cache(tmp_path, monkeypatch, incomplete)

    assert bars.load_partial_session("NVDA", DAY, ceiling.TRIGGER_END, "5m") is None


def test_no_data_for_day_returns_none(tmp_path, monkeypatch):
    """Nothing at all for the requested day (e.g. before the open) is also
    just "not yet" — None, not an error."""
    full = _real_day_bars()
    _write_cache(tmp_path, monkeypatch, full)

    other_day = pd.Timestamp("2099-01-01").date()
    assert bars.load_partial_session("NVDA", other_day, ceiling.TRIGGER_END, "5m") is None


def test_load_sessions_unchanged_on_real_cache():
    """load_sessions' own completeness rule, exclusions, and session count on
    the real, untouched cache are unaffected by adding load_partial_session —
    this hits the real file directly, not a monkeypatched CACHE."""
    sessions = bars.load_sessions("NVDA", "5m", allow_fetch=False)
    assert len(sessions) == 74
    assert all(len(s) >= bars.MIN_SESSION_BARS_5M for s in sessions)
    # every session is either the 78-bar full day or a >=70-bar reduced day —
    # never a partial/forming day slipping into the population
    assert all(70 <= len(s) <= 78 for s in sessions)
