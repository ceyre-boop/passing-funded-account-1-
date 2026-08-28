"""Fault-injection tests for the SPY macro decay study (primary 08:30-08:35
bar, 1-minute decay curve, breath-holding expansion).

Invariants that must be provably enforced, not just documented:
  1. the primary reads the single 08:30 5m bar, not the whole 08:30-09:00
     window.
  2. a day missing that single bar is EXCLUDED with a reason, never
     interpolated.
  3. the primary test picks up a real, constructed separation between event
     and control days and reports a one-sided p in the pre-registered
     direction, with no BH field (it is not a member of any family).
  4. the 1-minute decay curve requires ALL 16 buckets present or the day is
     excluded, never partially filled.
  5. every decay-curve test carries a BH-adjusted p (it IS a member of a
     family).
  6. the breath-holding expansion compares event vs control WITHIN each
     block only, and reports a ratio, never mixing blocks together.
  7. a block missing bars for a day excludes that day from THAT block only.
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

import build_spy_macro_decay_study as dstudy    # noqa: E402
import build_spy_premarket_event_study as bspes  # noqa: E402
from bars import BarDataError                    # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------- fixtures

def _synthetic_5m_cache(tmp_path: Path, symbol: str, days: list[date],
                         *, event_days: set[date] | None = None,
                         event_bump: float = 2.0) -> Path:
    """Raw 5m bars covering 04:00-20:00 ET, with a deliberately larger
    08:30-08:35 move on `event_days`."""
    event_days = event_days or set()
    rows = []
    for n, day in enumerate(days):
        start = datetime(day.year, day.month, day.day, 4, 0, tzinfo=ET)
        price = 100.0
        n_bars = int((16 * 60) / 5) + 1
        for i in range(n_bars):
            ts = start + timedelta(minutes=5 * i)
            hhmm = ts.strftime("%H:%M")
            wiggle = 0.02 * ((i + n) % 3 - 1)
            price += wiggle
            # The single-bar 08:30-08:35 return is Close/Open-1 WITHIN this
            # bar -- bumping `price` between iterations only moves the
            # LEVEL, not this bar's own O-C spread. Bump the bar's own
            # close offset directly so the constructed separation actually
            # lands in the single-bar primary metric.
            close_offset = 0.01
            if hhmm == "08:30" and day in event_days:
                close_offset += event_bump
            rows.append({"ts": ts, "Open": price, "High": price + 0.05 + close_offset,
                         "Low": price - 0.05, "Close": price + close_offset,
                         "Volume": 1000 + i})
    df = pd.DataFrame(rows).set_index("ts")
    cache = tmp_path / "cache5m"
    cache.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache / f"{symbol}_5m.parquet")
    return cache


def _synthetic_1m_cache(tmp_path: Path, symbol: str, days: list[date]) -> Path:
    """Raw 1m bars covering 08:20-08:45 ET only (enough for the decay
    window)."""
    rows = []
    for n, day in enumerate(days):
        start = datetime(day.year, day.month, day.day, 8, 20, tzinfo=ET)
        price = 100.0
        for i in range(26):    # 08:20..08:45 inclusive
            ts = start + timedelta(minutes=i)
            wiggle = 0.01 * ((i + n) % 3 - 1)
            price += wiggle
            rows.append({"ts": ts, "Open": price, "High": price + 0.02,
                         "Low": price - 0.02, "Close": price + 0.005,
                         "Volume": 500 + i})
    df = pd.DataFrame(rows).set_index("ts")
    cache = tmp_path / "cache1m"
    cache.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache / f"{symbol}_1m.parquet")
    return cache


@pytest.fixture
def business_days():
    d = date(2024, 1, 1)
    out = []
    while len(out) < 40:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _use_5m_cache(monkeypatch, cache: Path):
    monkeypatch.setattr(bspes, "CACHE", cache)


def _use_1m_cache(monkeypatch, cache: Path):
    monkeypatch.setattr(dstudy, "CACHE_1M", cache)


# --------------------------------------------------------- 1: primary window is the single bar

def test_primary_reads_only_the_single_0830_bar(tmp_path, monkeypatch, business_days):
    cache = _synthetic_5m_cache(tmp_path, "TST", business_days[:5])
    _use_5m_cache(monkeypatch, cache)
    days, excluded = dstudy.primary_bar_day_stats("TST")

    assert not excluded
    assert len(days) == 5
    assert (days["abs_ret"] >= 0).all()

    # Sanity: the primary abs_ret should be tiny relative to the parent
    # study's whole-window abs_ret for the same synthetic cache, since it
    # only spans one bar's move, not six.
    whole_days, _ = bspes.premarket_day_stats("TST")
    assert (days["abs_ret"] <= whole_days["abs_ret"] + 1e-9).all()


# --------------------------------------------------------- 2: completeness gate

def test_day_missing_the_0830_bar_is_excluded_not_interpolated(tmp_path, monkeypatch, business_days):
    cache = _synthetic_5m_cache(tmp_path, "TST", business_days[:5])
    path = cache / "TST_5m.parquet"
    df = pd.read_parquet(path)
    target_day = business_days[2]
    idx = df.index
    drop_mask = (idx.date == target_day) & (idx.strftime("%H:%M") == "08:30")
    df = df[~drop_mask]
    df.to_parquet(path)

    _use_5m_cache(monkeypatch, cache)
    days, excluded = dstudy.primary_bar_day_stats("TST")

    assert len(days) == 4
    assert len(excluded) == 1
    assert excluded[0]["date"] == target_day.isoformat()
    assert "08:30" in excluded[0]["reason"]


# --------------------------------------------------------- 3: primary detects a real separation, no BH

def test_primary_test_detects_constructed_separation_and_has_no_bh_field(
        tmp_path, monkeypatch, business_days):
    event_days = set(business_days[::3])
    cache = _synthetic_5m_cache(tmp_path, "TST", business_days,
                                 event_days=event_days, event_bump=3.0)
    _use_5m_cache(monkeypatch, cache)
    days, _ = dstudy.primary_bar_day_stats("TST")

    events = [{"date": d.isoformat(), "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}
              for d in event_days]

    rec = dstudy.primary_test(days, events)
    assert rec["diff_event_minus_control"] is not None
    assert rec["diff_event_minus_control"] > 0
    assert rec["welch_p_one_sided_event_gt_control"] is not None
    assert rec["welch_p_one_sided_event_gt_control"] < 0.05
    assert rec["window_et"] == "08:30-08:35"
    assert "welch_p_bh" not in rec


# --------------------------------------------------------- 4: decay curve completeness gate

def test_decay_curve_requires_all_16_minutes_present(tmp_path, monkeypatch, business_days):
    cache = _synthetic_1m_cache(tmp_path, "TST", business_days[:5])
    path = cache / "TST_1m.parquet"
    df = pd.read_parquet(path)
    target_day = business_days[1]
    idx = df.index
    drop_mask = (idx.date == target_day) & (idx.strftime("%H:%M") == "08:33")
    df = df[~drop_mask]
    df.to_parquet(path)

    _use_1m_cache(monkeypatch, cache)
    days, excluded = dstudy.decay_minute_day_stats("TST")

    assert len(days) == 4
    assert len(excluded) == 1
    assert excluded[0]["date"] == target_day.isoformat()
    for hhmm in dstudy.DECAY_MINUTES:
        assert f"m_{hhmm}" in days.columns


# --------------------------------------------------------- 5: decay curve is BH-corrected

def test_decay_curve_every_test_carries_bh_field(tmp_path, monkeypatch, business_days):
    cache = _synthetic_1m_cache(tmp_path, "TST", business_days)
    _use_1m_cache(monkeypatch, cache)
    days, _ = dstudy.decay_minute_day_stats("TST")

    event_days = set(business_days[::4])
    events = [{"date": d.isoformat(), "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}
              for d in event_days]

    result = dstudy.decay_curve(days, events)
    assert len(result["tests"]) == len(dstudy.DECAY_MINUTES)
    assert all("welch_p_bh" in t for t in result["tests"])


# --------------------------------------------------------- 6/7: breath-holding within-block only, ratio reported

def test_breath_holding_compares_within_block_only_and_reports_ratio(
        tmp_path, monkeypatch, business_days):
    cache = _synthetic_5m_cache(tmp_path, "TST", business_days)
    # Corrupt one bar inside the 07:30-08:00 block on one day only -- must
    # only exclude that day from THAT block, not from the others.
    path = cache / "TST_5m.parquet"
    df = pd.read_parquet(path)
    target_day = business_days[5]
    idx = df.index
    drop_mask = (idx.date == target_day) & (idx.strftime("%H:%M") == "07:45")
    df = df[~drop_mask]
    df.to_parquet(path)

    _use_5m_cache(monkeypatch, cache)

    event_days = set(business_days[::3])
    events = [{"date": d.isoformat(), "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}
              for d in event_days]

    result, per_block_days = dstudy.breath_holding_expansion("TST", events)

    assert len(result["tests"]) == len(dstudy.BREATH_BLOCKS)
    assert all("welch_p_bh" in t for t in result["tests"])
    assert all("event_control_ratio" in t for t in result["tests"])

    block_days_0800 = per_block_days["08:00-08:30"][0]
    block_days_0730 = per_block_days["07:30-08:00"][0]
    assert target_day.isoformat() not in set(block_days_0730["date"])
    assert target_day.isoformat() in set(block_days_0800["date"])
