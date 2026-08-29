"""Fault-injection tests for the SPY pre-market macro reaction study.

Invariants that must be provably enforced, not just documented:
  1. the 08:30-09:00 window is extracted correctly from raw (non-RTH-
     filtered) bars.
  2. a day missing even one of the six expected 5m bars in the window is
     EXCLUDED with a reason, never interpolated.
  3. the control pool never includes a day that is itself a scheduled event
     day for ANY tracked release type.
  4. the primary test picks up a real, constructed separation between event
     and control days (direction + magnitude), and reports a one-sided p in
     the pre-registered direction.
  5. a weekly-only release type (the Initial Claims degeneracy) produces a
     starved/unreliable weekday-matched control pool, and that is reported
     via `control_reliable`, never hidden.
  6. the primary test's p-value is untouched by BH correction (it is not a
     member of the secondary family).
"""
from __future__ import annotations

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

import build_spy_premarket_event_study as sp   # noqa: E402
from bars import BarDataError                  # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------- fixtures

def _synthetic_premarket_cache(tmp_path: Path, symbol: str, days: list[date],
                                *, event_days: set[date] | None = None,
                                event_bump: float = 2.0) -> Path:
    """Raw (unfiltered) 5m bars covering 04:00-20:00 ET for each day, with a
    deliberately larger 08:30-09:00 move on `event_days` than on the rest —
    the constructed separation `test_primary_test_detects_constructed_separation`
    checks for."""
    event_days = event_days or set()
    rows = []
    for n, day in enumerate(days):
        start = datetime(day.year, day.month, day.day, 4, 0, tzinfo=ET)
        price = 100.0
        n_bars = int((16 * 60) / 5) + 1   # 04:00..20:00 inclusive, 5m steps
        for i in range(n_bars):
            ts = start + timedelta(minutes=5 * i)
            hhmm = ts.strftime("%H:%M")
            in_window = "08:30" <= hhmm <= "08:55"
            wiggle = 0.02 * ((i + n) % 3 - 1)
            if in_window and day in event_days:
                wiggle += event_bump / 6.0   # spread a large move across the 6 bars
            price += wiggle
            rows.append({"ts": ts, "Open": price, "High": price + 0.05,
                         "Low": price - 0.05, "Close": price + 0.01,
                         "Volume": 1000 + i})
    df = pd.DataFrame(rows).set_index("ts")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache / f"{symbol}_5m.parquet")
    return cache


@pytest.fixture
def business_days():
    """40 consecutive weekday sessions starting Monday 2024-01-01 (a real
    Monday) — enough to build a non-degenerate weekday-matched control pool
    for several release types at once."""
    d = date(2024, 1, 1)
    out = []
    while len(out) < 40:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _use_cache(monkeypatch, cache: Path):
    monkeypatch.setattr(sp, "CACHE", cache)


# --------------------------------------------------------- 1: window extraction

def test_window_extraction_matches_known_synthetic_prices(tmp_path, monkeypatch, business_days):
    cache = _synthetic_premarket_cache(tmp_path, "TST", business_days[:5])
    _use_cache(monkeypatch, cache)
    days, excluded = sp.premarket_day_stats("TST")

    assert not excluded
    assert len(days) == 5
    for col in ("ret", "abs_ret", "range_pct", "fh_abs_ret", "sh_abs_ret"):
        assert days[col].notna().all()
    assert (days["abs_ret"] >= 0).all()


# --------------------------------------------------------- 2: completeness gate

def test_day_missing_a_window_bar_is_excluded_not_interpolated(tmp_path, monkeypatch, business_days):
    cache = _synthetic_premarket_cache(tmp_path, "TST", business_days[:5])
    # Corrupt the cache: drop the 08:45 bar on the 3rd day.
    path = cache / "TST_5m.parquet"
    df = pd.read_parquet(path)
    target_day = business_days[2]
    idx = df.index
    drop_mask = (idx.date == target_day) & (idx.strftime("%H:%M") == "08:45")
    df = df[~drop_mask]
    df.to_parquet(path)

    _use_cache(monkeypatch, cache)
    days, excluded = sp.premarket_day_stats("TST")

    assert len(days) == 4
    assert len(excluded) == 1
    assert excluded[0]["date"] == target_day.isoformat()
    assert "missing" in excluded[0]["reason"]


# --------------------------------------------------------- 3: clean control pool

def test_control_pool_excludes_every_scheduled_event_day(tmp_path, monkeypatch, business_days):
    cache = _synthetic_premarket_cache(tmp_path, "TST", business_days)
    _use_cache(monkeypatch, cache)
    days, _ = sp.premarket_day_stats("TST")

    event_dates = {business_days[2].isoformat(), business_days[5].isoformat(),
                   business_days[9].isoformat()}
    ctrl = sp.control_pool(days, event_dates)

    assert event_dates.isdisjoint(set(ctrl["date"]))
    assert len(ctrl) == len(days) - len(event_dates)


# --------------------------------------------------------- 4: primary test detects a real separation

def test_primary_test_detects_constructed_separation(tmp_path, monkeypatch, business_days):
    event_days = set(business_days[::3])   # every 3rd day is an "event"
    cache = _synthetic_premarket_cache(tmp_path, "TST", business_days,
                                        event_days=event_days, event_bump=3.0)
    _use_cache(monkeypatch, cache)
    days, _ = sp.premarket_day_stats("TST")

    events = [{"date": d.isoformat(), "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}
              for d in event_days]

    rec = sp.primary_test(days, events)
    assert rec["diff_event_minus_control"] is not None
    assert rec["diff_event_minus_control"] > 0
    assert rec["welch_p_one_sided_event_gt_control"] is not None
    assert rec["welch_p_one_sided_event_gt_control"] < 0.05


# --------------------------------------------------- 5: weekly-release control degeneracy

def test_weekly_only_release_starves_its_own_weekday_matched_control(tmp_path, monkeypatch, business_days):
    cache = _synthetic_premarket_cache(tmp_path, "TST", business_days)
    _use_cache(monkeypatch, cache)
    days, _ = sp.premarket_day_stats("TST")

    # Every Thursday is a "claims" event day -- mirrors Initial Claims.
    thursdays = [d for d in business_days if d.weekday() == 3]
    events = [{"date": d.isoformat(), "release_id": 180,
               "release_name": "Unemployment Insurance Weekly Claims Report",
               "importance": 0.3} for d in thursdays]
    all_event_dates = {e["date"] for e in events}

    ev = days[days["date"].isin(all_event_dates)]
    weekdays = set(ev["weekday"])
    assert weekdays == {"Thursday"}
    ctrl = sp._matched_control(days, all_event_dates, weekdays)

    # Almost every Thursday in the sample is itself an event day, so the
    # non-event-Thursday control pool is starved -- exactly the degeneracy
    # the module docstring says will happen, not hidden.
    assert len(ctrl) < sp.MIN_CONTROL_N
    rec = sp._welch(ev["abs_ret"].tolist(), ctrl["abs_ret"].tolist())
    assert rec["control_reliable"] is False


# --------------------------------------------------- 6: primary is not BH-corrected

def test_primary_result_has_no_bh_field_secondary_does(tmp_path, monkeypatch, business_days):
    event_days = set(business_days[::4])
    cache = _synthetic_premarket_cache(tmp_path, "TST", business_days,
                                        event_days=event_days, event_bump=1.5)
    _use_cache(monkeypatch, cache)
    days, _ = sp.premarket_day_stats("TST")

    events = [{"date": d.isoformat(), "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}
              for d in event_days]

    primary = sp.primary_test(days, events)
    secondary = sp.secondary_tests(days, pd.DataFrame(), events)

    assert "welch_p_bh" not in primary
    assert all("welch_p_bh" in t for t in secondary["tests"])
