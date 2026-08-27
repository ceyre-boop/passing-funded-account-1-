"""Tests for bars.load_partial_session — the same-day live accessor added to
unblock write_baseline_plan.py (the writer was reading load_sessions, whose
70-bar completeness gate excludes today's session until ~15:20 ET even though
find_entry only ever needs bars through TRIGGER_END, 11:00).

Every fixture here is built from FROZEN SNAPSHOTS of real cached sessions —
committed under daytrade/fixtures/ — never from the live, mutable
data/daytrade/bars/NVDA_5m.parquet, which the com.alta.alpha-operator
LaunchAgent rewrites every 5 minutes during market hours. Reading that file
directly made these tests intermittently fail depending on whether a live
tick happened to land mid-run (see NEXT.md / MEMORY for the incident).

No price is fabricated anywhere in this file, per CLAUDE.md rule 10 (never
modify sealed evaluation data) and the project-wide "never fabricate a bar"
doctrine in bars.py itself:

- daytrade/fixtures/NVDA_5m_2026-08-20.parquet holds the genuine, unmodified
  78-bar 2026-08-21... -1 day, i.e. 2026-08-20, RTH session for NVDA, snapshot
  taken 2026-08-27 from data/daytrade/bars/NVDA_5m.parquet while that session
  was still frozen history (refresh_cache never rewrites an existing
  session). Used by the load_partial_session truncation tests below.
- daytrade/fixtures/NVDA_5m_load_sessions_sample.parquet holds every RTH
  session strictly before 2026-08-21 that was present in the same live cache
  at snapshot time (73 sessions, 2026-05-07..2026-08-20, each a confirmed
  complete 78-bar day — verified before writing the fixture). Used by
  test_load_sessions_unchanged_on_real_cache so that test's session-count
  assertion is pinned to a frozen population instead of the ever-growing
  live cache (which was itself the same root cause of intermittent
  failure: the count changes as the operator appends new sessions, and the
  newest session in the live file is sometimes still forming).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import bars
import ceiling

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SINGLE_DAY_FIXTURE = FIXTURES / "NVDA_5m_2026-08-20.parquet"
LOAD_SESSIONS_FIXTURE = FIXTURES / "NVDA_5m_load_sessions_sample.parquet"
DAY = pd.Timestamp("2026-08-20").date()


def _real_day_bars() -> pd.DataFrame:
    """The genuine, unmodified 78-bar 2026-08-20 session, frozen into a
    committed fixture (see module docstring for provenance)."""
    df = pd.read_parquet(SINGLE_DAY_FIXTURE)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(bars.ET)
    day_bars = df[df.index.date == DAY]
    assert len(day_bars) == 78, "fixture assumption: 2026-08-20 is a full session"
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


def test_load_sessions_unchanged_on_real_cache(tmp_path, monkeypatch):
    """load_sessions' own completeness rule, exclusions, and session count on
    a frozen, previously-real cache are unaffected by adding
    load_partial_session — this hits a monkeypatched CACHE seeded with the
    frozen fixture (73 confirmed-complete real sessions), not the live,
    still-growing data/daytrade/bars/NVDA_5m.parquet, so the assertion below
    does not depend on when the test happens to run."""
    fixture = pd.read_parquet(LOAD_SESSIONS_FIXTURE)
    _write_cache(tmp_path, monkeypatch, fixture)

    sessions = bars.load_sessions("NVDA", "5m", allow_fetch=False)
    assert len(sessions) == 73
    assert all(len(s) >= bars.MIN_SESSION_BARS_5M for s in sessions)
    # every session in the frozen fixture is the full 78-bar day — never a
    # partial/forming day slipping into the population
    assert all(len(s) == 78 for s in sessions)
