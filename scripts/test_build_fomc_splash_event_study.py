"""Fault-injection tests for the FOMC double-splash event study.

Invariants that must be provably enforced, not just documented:
  1. the 14:00 statement bar and 14:30 presser bar are extracted as
     SEPARATE single 5-minute windows, never blurred into one wide window.
  2. a day missing the 14:00 bar is excluded from stmt-window comparisons
     with a reason, never interpolated; same independently for 14:30.
  3. the control pool never includes a day that is itself a scheduled event
     day (FOMC of any kind, or a tracked FRED release).
  4. the PRIMARY test runs on SCHEDULED meetings only — an unscheduled
     (`unscheduled: true`) meeting must never leak into the primary
     population, by construction of the caller, not by luck.
  5. the primary test picks up a real, constructed separation and reports a
     one-sided p in the pre-registered direction; it is never BH-corrected.
  6. `load_fomc_events` refuses (raises) rather than silently building a
     study on a calendar that fails its own fabrication guard.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_fomc_splash_event_study as fs   # noqa: E402
from bars import BarDataError                # noqa: E402
from build_macro_event_study import EventStudyError  # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------- fixtures

def _synthetic_cache(tmp_path: Path, symbol: str, days: list[date],
                      *, stmt_event_days: set[date] | None = None,
                      presser_event_days: set[date] | None = None,
                      stmt_bump: float = 0.03, presser_bump: float = 0.03,
                      drop_stmt_on: set[date] | None = None,
                      drop_presser_on: set[date] | None = None) -> Path:
    """RTH bars (09:30-16:00). Each bar's OWN Open->Close fractional return
    is constructed directly (`close = open * (1 + bar_return)`), so an event
    day's bump is a real, bar-local return regardless of the cumulative
    price level carried into that bar -- avoiding the trap where adding a
    fixed price offset onto an already-drifted running price silently
    shrinks its effect on `ret = Close/Open - 1`. `stmt_bump`/`presser_bump`
    are FRACTIONAL returns (0.03 = 3%), deliberately large so the
    constructed separation `test_primary_test_detects_constructed_separation`
    checks for is unambiguous against small baseline noise."""
    stmt_event_days = stmt_event_days or set()
    presser_event_days = presser_event_days or set()
    drop_stmt_on = drop_stmt_on or set()
    drop_presser_on = drop_presser_on or set()
    rows = []
    for n, day in enumerate(days):
        start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
        price = 100.0
        n_bars = int((6.5 * 60) / 5) + 1
        for i in range(n_bars):
            ts = start + timedelta(minutes=5 * i)
            hhmm = ts.strftime("%H:%M")
            if hhmm == fs.STMT_BAR and day in drop_stmt_on:
                continue
            if hhmm == fs.PRESSER_BAR and day in drop_presser_on:
                continue
            bar_ret = 0.0002 * ((i + n) % 3 - 1)   # small baseline noise
            if hhmm == fs.STMT_BAR and day in stmt_event_days:
                bar_ret = stmt_bump
            if hhmm == fs.PRESSER_BAR and day in presser_event_days:
                bar_ret = presser_bump
            o = price
            c = o * (1 + bar_ret)
            rows.append({"ts": ts, "Open": o, "High": max(o, c) + 0.01,
                         "Low": min(o, c) - 0.01, "Close": c,
                         "Volume": 1000 + i})
            price = c
    df = pd.DataFrame(rows).set_index("ts")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache / f"{symbol}_5m.parquet")
    return cache


@pytest.fixture
def business_days():
    """40 consecutive weekday sessions starting Monday 2024-01-01."""
    d = date(2024, 1, 1)
    out = []
    while len(out) < 40:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _use_cache(monkeypatch, cache: Path):
    monkeypatch.setattr(fs, "CACHE", cache)


# --------------------------------------------------- 1/2: window extraction & gating

def test_stmt_and_presser_windows_extracted_separately(tmp_path, monkeypatch, business_days):
    event_days = {business_days[3], business_days[10]}
    cache = _synthetic_cache(tmp_path, "TST", business_days,
                              stmt_event_days=event_days, presser_event_days=set())
    _use_cache(monkeypatch, cache)
    days, stmt_excl, presser_excl = fs.rth_day_stats("TST")

    assert not stmt_excl and not presser_excl
    ev_rows = days[days["date"].isin({d.isoformat() for d in event_days})]
    other_rows = days[~days["date"].isin({d.isoformat() for d in event_days})]
    # stmt window shows the constructed bump; presser window does not.
    assert ev_rows["stmt_abs_ret"].mean() > other_rows["stmt_abs_ret"].mean()
    assert ev_rows["presser_abs_ret"].mean() == pytest.approx(
        other_rows["presser_abs_ret"].mean(), abs=0.02)


def test_missing_stmt_bar_excludes_only_stmt_window(tmp_path, monkeypatch, business_days):
    target = business_days[2]
    cache = _synthetic_cache(tmp_path, "TST", business_days[:5], drop_stmt_on={target})
    _use_cache(monkeypatch, cache)
    days, stmt_excl, presser_excl = fs.rth_day_stats("TST")

    assert len(stmt_excl) == 1 and stmt_excl[0]["date"] == target.isoformat()
    assert presser_excl == []
    row = days[days["date"] == target.isoformat()].iloc[0]
    assert pd.isna(row["stmt_abs_ret"])
    assert not pd.isna(row["presser_abs_ret"])


def test_missing_presser_bar_excludes_only_presser_window(tmp_path, monkeypatch, business_days):
    target = business_days[2]
    cache = _synthetic_cache(tmp_path, "TST", business_days[:5], drop_presser_on={target})
    _use_cache(monkeypatch, cache)
    days, stmt_excl, presser_excl = fs.rth_day_stats("TST")

    assert stmt_excl == []
    assert len(presser_excl) == 1 and presser_excl[0]["date"] == target.isoformat()
    row = days[days["date"] == target.isoformat()].iloc[0]
    assert not pd.isna(row["stmt_abs_ret"])
    assert pd.isna(row["presser_abs_ret"])


# --------------------------------------------------------- 3: clean control pool

def test_control_pool_excludes_every_scheduled_event_day(tmp_path, monkeypatch, business_days):
    cache = _synthetic_cache(tmp_path, "TST", business_days)
    _use_cache(monkeypatch, cache)
    days, _, _ = fs.rth_day_stats("TST")

    event_dates = {business_days[2].isoformat(), business_days[5].isoformat()}
    ctrl = fs.control_pool(days, event_dates)

    assert event_dates.isdisjoint(set(ctrl["date"]))
    assert len(ctrl) == len(days) - len(event_dates)


# ---------------------------------------------- 4: unscheduled never leaks into primary

def test_unscheduled_meeting_excluded_from_primary_population(tmp_path, monkeypatch, business_days):
    scheduled_day = business_days[5]
    unscheduled_day = business_days[8]
    cache = _synthetic_cache(
        tmp_path, "TST", business_days,
        stmt_event_days={scheduled_day, unscheduled_day}, stmt_bump=0.03)
    _use_cache(monkeypatch, cache)
    days, _, _ = fs.rth_day_stats("TST")

    all_event_dates = {scheduled_day.isoformat(), unscheduled_day.isoformat()}
    rec = fs.primary_test(days, [scheduled_day.isoformat()], all_event_dates)

    assert rec["scheduled_meetings_in_bar_coverage"] == 1
    assert rec["event_n"] == 1   # the unscheduled day never entered the event population


# ----------------------------------------- 5: primary detects real separation, no BH field

def test_primary_test_detects_constructed_separation_and_has_no_bh_field(
        tmp_path, monkeypatch, business_days):
    event_days = set(business_days[::4])
    cache = _synthetic_cache(tmp_path, "TST", business_days,
                              stmt_event_days=event_days, stmt_bump=0.03)
    _use_cache(monkeypatch, cache)
    days, _, _ = fs.rth_day_stats("TST")

    all_event_dates = {d.isoformat() for d in event_days}
    rec = fs.primary_test(days, sorted(all_event_dates), all_event_dates)

    assert rec["diff_event_minus_control"] is not None
    assert rec["diff_event_minus_control"] > 0
    assert rec["welch_p_one_sided_event_gt_control"] is not None
    assert rec["welch_p_one_sided_event_gt_control"] < 0.05
    assert "welch_p_bh" not in rec


def test_secondary_tests_all_carry_bh_field(tmp_path, monkeypatch, business_days):
    event_days = set(business_days[::4])
    cache = _synthetic_cache(tmp_path, "TST", business_days,
                              stmt_event_days=event_days, presser_event_days=event_days,
                              stmt_bump=0.03, presser_bump=0.03)
    _use_cache(monkeypatch, cache)
    days, _, _ = fs.rth_day_stats("TST")

    fomc_events = [{"date": d.isoformat(), "press_conference": True} for d in event_days]
    secondary = fs.secondary_tests(days, fomc_events, fred_event_dates=set())
    assert all("welch_p_bh" in t for t in secondary["tests"])
    assert "SCHEDULED_VS_UNSCHEDULED_NATURAL_EXPERIMENT" in secondary


# ---------------------------------------------- 6: calendar fabrication guard refusal

def test_load_fomc_events_refuses_on_fabricated_calendar(tmp_path, monkeypatch):
    bad = tmp_path / "fomc_calendar.json"
    bad.write_text(json.dumps({
        "events": [{"date": f"2026-01-{d:02d}"} for d in range(1, 15)],
    }))
    monkeypatch.setattr(fs.mc, "FOMC_CALENDAR_JSON", bad)
    with pytest.raises(EventStudyError):
        fs.load_fomc_events()


def test_load_fomc_events_accepts_clean_calendar(tmp_path, monkeypatch):
    good = tmp_path / "fomc_calendar.json"
    good.write_text(json.dumps({
        "events": [{"date": "2026-01-28", "label": "x", "press_conference": True},
                   {"date": "2026-03-18", "label": "x", "press_conference": True}],
    }))
    monkeypatch.setattr(fs.mc, "FOMC_CALENDAR_JSON", good)
    events = fs.load_fomc_events()
    assert len(events) == 2
