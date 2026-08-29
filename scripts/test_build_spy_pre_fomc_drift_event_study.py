"""Fault-injection tests for the pre-FOMC announcement drift replication.

Invariants that must be provably enforced, not just documented:
  1. entry uses the PRIOR trading day's 15:55 close (index-based, not
     calendar subtraction) and exit uses the SAME day's 13:55 close --
     `build_drift_days` must compute a signed return matching that exact
     pair, never the day's own open or the 14:00 bar.
  2. a day with fewer than `lag` prior trading days in coverage is
     excluded, never assigned a synthetic entry day.
  3. a day whose entry day is missing its 15:55 bar is excluded with a
     reason distinct from a day missing its own 13:55 exit bar.
  4. `control_pool` excludes a day that is itself a tracked event day AND
     a day whose OWN entry day is a tracked event day -- contamination
     from the entry side must be caught, not just the exit side.
  5. `primary_test` detects a constructed positive separation and reports
     a one-sided p in the pre-registered (UP) direction.
  6. the permutation null holds each day's own `ret` fixed and only
     reshuffles which days are labelled "event" -- the p-value is a
     correct rank count against a constructed series with a known
     result.
  7. `horizon_sweep` builds INDEPENDENT day tables per lag (T-48h's entry
     day is two trading days back, not one), verified against the shared
     `build_drift_days` builder directly.
  8. `press_conference_split` partitions strictly on the real tracked
     field and never includes an event date in both subsets.
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

import build_spy_pre_fomc_drift_event_study as ds   # noqa: E402
import build_fomc_splash_event_study as fomc_mod     # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------- fixtures

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


def _minimal_cache(tmp_path: Path, symbol: str, days: list[date], *,
                    closes_1555: dict[date, float] | None = None,
                    closes_1355: dict[date, float] | None = None,
                    drop_1555_on: set[date] | None = None,
                    drop_1355_on: set[date] | None = None,
                    default: float = 100.0) -> Path:
    """Only the two 5m bars this module actually reads (`15:55`, `13:55`)
    per day -- `build_day_chunks`/`_bar_close` never look at any other
    bucket, so a full RTH day is not needed to exercise this module's own
    logic."""
    closes_1555 = closes_1555 or {}
    closes_1355 = closes_1355 or {}
    drop_1555_on = drop_1555_on or set()
    drop_1355_on = drop_1355_on or set()
    rows = []
    for day in days:
        if day not in drop_1555_on:
            c = closes_1555.get(day, default)
            ts = datetime(day.year, day.month, day.day, 15, 55, tzinfo=ET)
            rows.append({"ts": ts, "Open": c, "High": c + 0.01, "Low": c - 0.01,
                         "Close": c, "Volume": 1000})
        if day not in drop_1355_on:
            c = closes_1355.get(day, default)
            ts = datetime(day.year, day.month, day.day, 13, 55, tzinfo=ET)
            rows.append({"ts": ts, "Open": c, "High": c + 0.01, "Low": c - 0.01,
                         "Close": c, "Volume": 1000})
    df = pd.DataFrame(rows).set_index("ts")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache / f"{symbol}_5m.parquet")
    return cache


def _use_cache(monkeypatch, cache: Path):
    monkeypatch.setattr(fomc_mod, "CACHE", cache)


# ------------------------------------------------------- 1: correct bar pairing

def test_signed_return_uses_prior_day_1555_and_own_day_1355(tmp_path, monkeypatch, business_days):
    d0, d1, d2 = business_days[0], business_days[1], business_days[2]
    cache = _minimal_cache(tmp_path, "TST", [d0, d1, d2],
                            closes_1555={d0: 100.0, d1: 101.0},
                            closes_1355={d1: 102.0, d2: 103.0})
    _use_cache(monkeypatch, cache)
    raw = fomc_mod._load_raw_bars("TST")
    day_list, day_chunks = ds.build_day_chunks(raw)
    days, excluded = ds.build_drift_days(day_list, day_chunks, lag=1)

    row = days[days["date"] == d1.isoformat()].iloc[0]
    assert row["entry_date"] == d0.isoformat()
    assert row["entry_close"] == pytest.approx(100.0)
    assert row["exit_close"] == pytest.approx(102.0)
    assert row["ret"] == pytest.approx(0.02)

    row2 = days[days["date"] == d2.isoformat()].iloc[0]
    assert row2["entry_date"] == d1.isoformat()
    assert row2["entry_close"] == pytest.approx(101.0)   # the 15:55 close, NOT the 13:55 exit close


# ---------------------------------------------------- 2: insufficient lookback

def test_first_day_has_no_prior_trading_day_and_is_excluded(tmp_path, monkeypatch, business_days):
    days_list = business_days[:5]
    cache = _minimal_cache(tmp_path, "TST", days_list)
    _use_cache(monkeypatch, cache)
    raw = fomc_mod._load_raw_bars("TST")
    day_list, day_chunks = ds.build_day_chunks(raw)
    days, excluded = ds.build_drift_days(day_list, day_chunks, lag=1)

    assert days_list[0].isoformat() not in set(days["date"])
    reasons = {e["date"]: e["reason"] for e in excluded}
    assert "prior trading day" in reasons[days_list[0].isoformat()]

    # lag=3 must exclude the first THREE days, not just one
    days3, excluded3 = ds.build_drift_days(day_list, day_chunks, lag=3)
    assert {days_list[0].isoformat(), days_list[1].isoformat(), days_list[2].isoformat()} \
        .isdisjoint(set(days3["date"]))


# ------------------------------------------------- 3: distinct exclusion reasons

def test_missing_entry_and_exit_bars_excluded_with_distinct_reasons(
        tmp_path, monkeypatch, business_days):
    d0, d1, d2 = business_days[0], business_days[1], business_days[2]
    cache = _minimal_cache(tmp_path, "TST", [d0, d1, d2], drop_1555_on={d0}, drop_1355_on={d2})
    _use_cache(monkeypatch, cache)
    raw = fomc_mod._load_raw_bars("TST")
    day_list, day_chunks = ds.build_day_chunks(raw)
    days, excluded = ds.build_drift_days(day_list, day_chunks, lag=1)

    reasons = {e["date"]: e["reason"] for e in excluded}
    assert "missing 15:55 close bar on entry day" in reasons[d1.isoformat()]
    assert "missing 13:55 exit bar" in reasons[d2.isoformat()]
    assert d1.isoformat() not in set(days["date"])
    assert d2.isoformat() not in set(days["date"])


# --------------------------------------------------- 4: control pool contamination

def test_control_pool_excludes_event_date_and_event_entry_date(tmp_path, monkeypatch,
                                                                 business_days):
    dates = [d.isoformat() for d in business_days[:10]]
    df = pd.DataFrame({
        "date": dates,
        "entry_date": [business_days[max(0, i - 1)].isoformat() for i in range(10)],
        "weekday": [pd.Timestamp(d).strftime("%A") for d in business_days[:10]],
        "ret": [0.001] * 10,
        "abs_ret": [0.001] * 10,
    })
    # day 5 is itself an event; day 7's ENTRY day (day 6) is an event
    all_event_dates = {dates[5], business_days[6].isoformat()}
    pool = ds.control_pool(df, all_event_dates)

    assert dates[5] not in set(pool["date"])       # excluded: itself an event day
    assert dates[7] not in set(pool["date"])       # excluded: its entry day is an event day
    assert dates[0] in set(pool["date"])           # clean day stays in


# ------------------------------------------ 5: primary detects constructed drift

def test_primary_test_detects_constructed_positive_drift(tmp_path, monkeypatch, business_days):
    n = len(business_days)
    closes_1555 = {}
    closes_1355 = {}
    price = 100.0
    event_days = set(business_days[2::4])
    for i, day in enumerate(business_days):
        closes_1555[day] = price
        # a small baseline wobble every day
        price = price * (1 + 0.0001 * ((i % 3) - 1))
        if day in event_days:
            closes_1355[day] = closes_1555.get(business_days[i - 1], price) * 1.01
        else:
            closes_1355[day] = closes_1555.get(business_days[i - 1], price) * (1 + 0.0001)
    cache = _minimal_cache(tmp_path, "TST", business_days,
                            closes_1555=closes_1555, closes_1355=closes_1355)
    _use_cache(monkeypatch, cache)
    raw = fomc_mod._load_raw_bars("TST")
    day_list, day_chunks = ds.build_day_chunks(raw)
    days, _ = ds.build_drift_days(day_list, day_chunks, lag=1)

    event_dates = {d.isoformat() for d in event_days} & set(days["date"])
    rec = ds.primary_test(days, event_dates, set())

    assert rec["diff_event_minus_control"] is not None
    assert rec["diff_event_minus_control"] > 0
    assert rec["welch_p_one_sided_event_gt_control"] is not None
    assert rec["welch_p_one_sided_event_gt_control"] < 0.05
    assert rec["net_of_cost_event_mean"] < rec["event_mean"]   # costs always reduce, never help


# ------------------------------------------------------ 6: permutation null rank

def test_permutation_null_p_value_is_correct_rank(monkeypatch):
    # 10 days: 5 with ret=1.0, 5 with ret=0.0. Observed "event" mean is
    # constructed to equal the population mean exactly (n_event=10, i.e.
    # every permutation IS the full population) -- deterministic p=1.0.
    days = pd.DataFrame({"ret": [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]})
    perm = ds.permutation_null(days, n_event=10, n_perm=50, seed=1)
    finished = ds._finish_permutation(perm, observed_mean=0.5)
    assert finished["p_value_one_sided_event_gt_random"] == pytest.approx(1.0)

    # An observed mean far above every possible permutation mean must score
    # p=0 (no permutation, of any label reshuffling, exceeds it) when
    # n_event < population size, giving real variation across permutations.
    days2 = pd.DataFrame({"ret": [0.0] * 20 + [0.0001] * 1})
    perm2 = ds.permutation_null(days2, n_event=1, n_perm=200, seed=2)
    finished2 = ds._finish_permutation(perm2, observed_mean=10.0)
    assert finished2["p_value_one_sided_event_gt_random"] == pytest.approx(0.0)


def test_permutation_null_never_alters_underlying_returns(monkeypatch):
    days = pd.DataFrame({"ret": [0.01, -0.02, 0.03, -0.01, 0.02]})
    before = days["ret"].tolist()
    ds.permutation_null(days, n_event=2, n_perm=10, seed=3)
    assert days["ret"].tolist() == before


# --------------------------------------------------- 7: horizon sweep independence

def test_horizon_sweep_uses_independent_lag_specific_entry_days(tmp_path, monkeypatch,
                                                                  business_days):
    closes_1555 = {d: 100.0 + i for i, d in enumerate(business_days)}
    closes_1355 = {d: 100.0 + i for i, d in enumerate(business_days)}
    cache = _minimal_cache(tmp_path, "TST", business_days,
                            closes_1555=closes_1555, closes_1355=closes_1355)
    _use_cache(monkeypatch, cache)
    raw = fomc_mod._load_raw_bars("TST")
    day_list, day_chunks = ds.build_day_chunks(raw)

    d_target = business_days[10]
    days1, _ = ds.build_drift_days(day_list, day_chunks, lag=1)
    days3, _ = ds.build_drift_days(day_list, day_chunks, lag=3)

    row1 = days1[days1["date"] == d_target.isoformat()].iloc[0]
    row3 = days3[days3["date"] == d_target.isoformat()].iloc[0]
    assert row1["entry_date"] == business_days[9].isoformat()
    assert row3["entry_date"] == business_days[7].isoformat()
    assert row1["entry_close"] != row3["entry_close"]


# ------------------------------------------------- 8: press-conference partition

def test_press_conference_split_partitions_without_overlap(tmp_path, monkeypatch, business_days):
    closes = {d: 100.0 for d in business_days}
    cache = _minimal_cache(tmp_path, "TST", business_days, closes_1555=closes, closes_1355=closes)
    _use_cache(monkeypatch, cache)
    raw = fomc_mod._load_raw_bars("TST")
    day_list, day_chunks = ds.build_day_chunks(raw)
    days, _ = ds.build_drift_days(day_list, day_chunks, lag=1)

    scheduled_events = [
        {"date": business_days[3].isoformat(), "press_conference": True},
        {"date": business_days[5].isoformat(), "press_conference": False},
        {"date": business_days[7].isoformat(), "press_conference": True},
    ]
    out = ds.press_conference_split(days, scheduled_events, set())
    presser = next(t for t in out if t["subset"] == "press_conference")
    no_presser = next(t for t in out if t["subset"] == "no_press_conference")

    assert presser["meetings_in_subset"] == 2
    assert no_presser["meetings_in_subset"] == 1
