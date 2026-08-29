"""Fault-injection tests for the SPY second wave study (VWAP-break
classification at 08:36, 08:36->12:00 primary continuation, exploratory
horizon sweep / hold requirement / volume confirmation / FOMC arm).

Invariants that must be provably enforced, not just documented:
  1. `_vwap` is a volume-weighted typical-price average, not a plain mean --
     a volume-weighted mutation must move it in the expected direction.
  2. a day landing exactly on the VWAP (float equality at 08:36) is
     excluded from the primary population, never silently assigned a side.
  3. a day missing any of the five 08:30-08:34 VWAP bars is excluded via
     the `vwap` reason list ONLY -- not `entry` or `continuation`.
  4. a day missing the 08:36 entry bar is excluded via the `entry` reason
     list ONLY.
  5. a day missing any bar in the 08:36-11:59 continuation span is excluded
     via the `continuation` reason list ONLY.
  6. the primary statistic is sign(above_vwap) * net_return, net of the
     reused round-trip cost -- constructing a real separation must produce
     a positive diff, a one-sided p < 0.05, AND a permutation-null p field.
  7. the cost model has NO stop-through component and is derived from
     `build_spy_bracket_harvest_study.SPREAD_BP`, not a re-typed constant.
  8. every horizon-sweep test carries a BH-adjusted p field (it is a member
     of a family); the primary test does not.
  9. hold_requirement splits days into held/not-held using the SAME sign
     comparison at 08:36 and 08:40, and every event day lands in exactly
     one bucket.
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

import build_spy_second_wave_study as swstudy      # noqa: E402
import build_spy_macro_decay_study as decay_mod     # noqa: E402
import build_spy_bracket_harvest_study as bracket_mod  # noqa: E402

ET = ZoneInfo("America/New_York")

DAY_START, DAY_END = "08:20", "12:05"


# --------------------------------------------------------------------- fixtures

def _minutes(day: date, start_hhmm: str, end_hhmm: str) -> list[datetime]:
    sh, sm = (int(x) for x in start_hhmm.split(":"))
    eh, em = (int(x) for x in end_hhmm.split(":"))
    start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=ET)
    end = datetime(day.year, day.month, day.day, eh, em, tzinfo=ET)
    out = []
    ts = start
    while ts <= end:
        out.append(ts)
        ts += timedelta(minutes=1)
    return out


def _day_rows(day: date, *, base_price: float = 100.0, jump_at: str | None = "08:35",
              jump_amount: float = 0.0, per_minute_drift: float = 0.0,
              wiggle: float = 0.001, volume: float = 1000.0, seed: int = 0,
              noise: float = 0.001) -> list[dict]:
    """One full day of 1-minute OHLCV rows, 08:20-12:05. `jump_amount` is
    applied as an extra step on the bar CLOSING at `jump_at` -- i.e. it
    shifts the OPEN of the very next bar, which is what the entry-bar Open
    actually reads. `per_minute_drift` accrues on every bar after that,
    building a constructed continuation. `seed` drives a small deterministic
    +/- micro-noise on every bar's own close (same `(i+n)%3-1` pattern the
    macro-decay test fixtures already use) so a zero-jump/zero-drift day is
    NOT exactly flat -- real bars are never exactly flat either, and an
    exactly-flat synthetic day would land exactly on its own VWAP by
    construction, which is a fixture artifact, not something this test
    wants to exercise here."""
    rows = []
    price = base_price
    minutes = _minutes(day, DAY_START, DAY_END)
    for i, ts in enumerate(minutes):
        o = price
        hhmm = ts.strftime("%H:%M")
        step = noise * ((i + seed) % 3 - 1)
        if jump_at is not None and hhmm == jump_at:
            step += jump_amount
        if jump_at is not None and hhmm > jump_at:
            step += per_minute_drift
        c = o + step
        hi = max(o, c) + wiggle
        lo = min(o, c) - wiggle
        rows.append({"ts": ts, "Open": o, "High": hi, "Low": lo, "Close": c, "Volume": volume})
        price = c
    return rows


def _write_cache(tmp_path: Path, symbol: str, rows: list[dict]) -> Path:
    df = pd.DataFrame(rows).set_index("ts")
    cache = tmp_path / "cache1m"
    cache.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache / f"{symbol}_1m.parquet")
    return cache


@pytest.fixture
def business_days():
    d = date(2024, 1, 1)
    out = []
    while len(out) < 30:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _use_1m_cache(monkeypatch, cache: Path):
    monkeypatch.setattr(decay_mod, "CACHE_1M", cache)


def _load(symbol: str) -> pd.DataFrame:
    return decay_mod._load_raw_bars_1m(symbol)


# --------------------------------------------------------- 1: VWAP is volume-weighted

def test_vwap_is_volume_weighted_not_a_plain_mean():
    rows = [
        {"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 1.0},
        {"Open": 110.0, "High": 110.0, "Low": 110.0, "Close": 110.0, "Volume": 1000.0},
    ]
    window = pd.DataFrame(rows)
    vwap = swstudy._vwap(window)
    plain_mean = (100.0 + 110.0) / 2.0
    assert vwap is not None
    assert vwap > plain_mean
    assert abs(vwap - 110.0) < 0.1  # dominated by the 1000-volume bar


# --------------------------------------------------- 2: exact-VWAP day excluded, not assigned

def test_day_landing_exactly_on_vwap_is_excluded_from_primary(tmp_path, monkeypatch, business_days):
    day = business_days[0]
    # flat price, zero wiggle -> VWAP == entry price exactly.
    rows = _day_rows(day, jump_at=None, wiggle=0.0, noise=0.0)
    cache = _write_cache(tmp_path, "TST", rows)
    _use_1m_cache(monkeypatch, cache)

    raw = _load("TST")
    days, _excl = swstudy.second_wave_day_stats(raw)
    row = days[days["date"] == day.isoformat()].iloc[0]
    assert row["above_vwap"] == 0.0
    complete = swstudy._complete_primary(days)
    assert complete.empty


# --------------------------------------------------- 3/4/5: independent exclusion reasons

def test_missing_vwap_bar_excludes_via_vwap_reason_only(tmp_path, monkeypatch, business_days):
    days_list = business_days[:4]
    rows = []
    for n, d in enumerate(days_list):
        rows += _day_rows(d, jump_amount=0.5, per_minute_drift=0.001, seed=n)
    cache = _write_cache(tmp_path, "TST", rows)
    path = cache / "TST_1m.parquet"
    df = pd.read_parquet(path)
    target = days_list[1]
    idx = df.index
    drop_mask = (idx.date == target) & (idx.strftime("%H:%M") == "08:32")
    df = df[~drop_mask]
    df.to_parquet(path)

    _use_1m_cache(monkeypatch, cache)
    raw = _load("TST")
    days, excluded = swstudy.second_wave_day_stats(raw)

    assert any(e["date"] == target.isoformat() for e in excluded["vwap"])
    assert not any(e["date"] == target.isoformat() for e in excluded["entry"])
    assert not any(e["date"] == target.isoformat() for e in excluded["continuation"])
    row = days[days["date"] == target.isoformat()].iloc[0]
    assert pd.isna(row["vwap_0830_0834"])


def test_missing_entry_bar_excludes_via_entry_reason_only(tmp_path, monkeypatch, business_days):
    days_list = business_days[:4]
    rows = []
    for n, d in enumerate(days_list):
        rows += _day_rows(d, jump_amount=0.5, per_minute_drift=0.001, seed=n)
    cache = _write_cache(tmp_path, "TST", rows)
    path = cache / "TST_1m.parquet"
    df = pd.read_parquet(path)
    target = days_list[2]
    idx = df.index
    drop_mask = (idx.date == target) & (idx.strftime("%H:%M") == "08:36")
    df = df[~drop_mask]
    df.to_parquet(path)

    _use_1m_cache(monkeypatch, cache)
    raw = _load("TST")
    days, excluded = swstudy.second_wave_day_stats(raw)

    assert any(e["date"] == target.isoformat() for e in excluded["entry"])
    assert not any(e["date"] == target.isoformat() for e in excluded["vwap"])
    row = days[days["date"] == target.isoformat()].iloc[0]
    assert pd.isna(row["entry_price"])
    assert pd.isna(row["above_vwap"])


def test_missing_continuation_bar_excludes_via_continuation_reason_only(
        tmp_path, monkeypatch, business_days):
    days_list = business_days[:4]
    rows = []
    for n, d in enumerate(days_list):
        rows += _day_rows(d, jump_amount=0.5, per_minute_drift=0.001, seed=n)
    cache = _write_cache(tmp_path, "TST", rows)
    path = cache / "TST_1m.parquet"
    df = pd.read_parquet(path)
    target = days_list[3]
    idx = df.index
    drop_mask = (idx.date == target) & (idx.strftime("%H:%M") == "10:15")
    df = df[~drop_mask]
    df.to_parquet(path)

    _use_1m_cache(monkeypatch, cache)
    raw = _load("TST")
    days, excluded = swstudy.second_wave_day_stats(raw)

    assert any(e["date"] == target.isoformat() for e in excluded["continuation"])
    assert not any(e["date"] == target.isoformat() for e in excluded["vwap"])
    assert not any(e["date"] == target.isoformat() for e in excluded["entry"])
    row = days[days["date"] == target.isoformat()].iloc[0]
    assert pd.isna(row["exit_price_1200"])
    # entry/classification still computed independently of the continuation gate
    assert not pd.isna(row["entry_price"])


# --------------------------------------------------- 6: primary detects constructed separation

def test_primary_detects_constructed_separation_with_permutation_field(
        tmp_path, monkeypatch, business_days):
    event_days = set(business_days[::2])
    rows = []
    for n, d in enumerate(business_days):
        if d in event_days:
            rows += _day_rows(d, jump_amount=0.6, per_minute_drift=0.0015, seed=n)
        else:
            rows += _day_rows(d, jump_amount=0.0, per_minute_drift=0.0, seed=n)
    cache = _write_cache(tmp_path, "TST", rows)
    _use_1m_cache(monkeypatch, cache)

    raw = _load("TST")
    days, _excl = swstudy.second_wave_day_stats(raw)

    events = [{"date": d.isoformat(), "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}
              for d in event_days]

    rec = swstudy.primary_test(days, events)
    assert rec["diff_event_minus_control"] is not None
    assert rec["diff_event_minus_control"] > 0
    assert rec["welch_p_one_sided_event_gt_control"] is not None
    assert rec["welch_p_one_sided_event_gt_control"] < 0.05
    assert "permutation_null" in rec
    assert "p_value_one_sided_event_gt_random" in rec["permutation_null"]
    assert rec["interpretation"].startswith("SECOND WAVE DETECTED")


# --------------------------------------------------- 7: cost model reused, no stop-through

def test_cost_model_reused_from_bracket_study_no_stop_through():
    assert swstudy.SPREAD_BP == bracket_mod.SPREAD_BP
    assert swstudy.ENTRY_COST_BP == swstudy.SPREAD_BP
    assert swstudy.EXIT_COST_BP == swstudy.SPREAD_BP
    assert swstudy.ROUND_TRIP_COST_BP == 2.0 * swstudy.SPREAD_BP
    assert swstudy.ROUND_TRIP_COST_BP < bracket_mod.ENTRY_COST_BP + bracket_mod.EXIT_COST_BP


# --------------------------------------------------- 8: horizon sweep carries BH, primary does not

def test_horizon_sweep_carries_bh_field_and_primary_does_not(
        tmp_path, monkeypatch, business_days):
    event_days = set(business_days[::3])
    rows = []
    for n, d in enumerate(business_days):
        if d in event_days:
            rows += _day_rows(d, jump_amount=0.4, per_minute_drift=0.001, seed=n)
        else:
            rows += _day_rows(d, jump_amount=0.0, per_minute_drift=0.0, seed=n)
    cache = _write_cache(tmp_path, "TST", rows)
    _use_1m_cache(monkeypatch, cache)

    raw = _load("TST")
    days, _excl = swstudy.second_wave_day_stats(raw)

    events = [{"date": d.isoformat(), "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}
              for d in event_days]

    primary = swstudy.primary_test(days, events)
    assert "welch_p_bh" not in primary

    horizon = swstudy.horizon_sweep_from_raw(raw, days, events)
    real_tests = [t for t in horizon["tests"] if "welch_p_two_sided" in t]
    assert real_tests
    assert all("welch_p_bh" in t for t in real_tests)


# --------------------------------------------------- 9: hold requirement partitions cleanly

def test_hold_requirement_partitions_every_complete_day_exactly_once(
        tmp_path, monkeypatch, business_days):
    event_days = set(business_days[::2])
    rows = []
    for n, d in enumerate(business_days):
        if d in event_days:
            rows += _day_rows(d, jump_amount=0.5, per_minute_drift=0.002, seed=n)
        else:
            rows += _day_rows(d, jump_amount=0.0, per_minute_drift=0.0, seed=n)
    cache = _write_cache(tmp_path, "TST", rows)
    _use_1m_cache(monkeypatch, cache)

    raw = _load("TST")
    days, _excl = swstudy.second_wave_day_stats(raw)
    events = [{"date": d.isoformat(), "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}
              for d in event_days]

    result = swstudy.hold_requirement(days, events)
    complete = swstudy.primary_stat_series(days)
    complete = complete[complete["hold_price_0840"].notna() &
                         complete["vwap_0830_0834"].notna()]
    assert result["held_n"] + result["not_held_n"] == len(complete)

    # constructed event-day drift (0.002/min for 4 minutes = 0.008, an order
    # of magnitude past the 0.001 noise floor) keeps the same sign through
    # 08:40 -> every EVENT day must land in the held bucket. Control days
    # (noise only, no drift) are not asserted either way -- they are not
    # what this invariant is about.
    event_dates = {d.isoformat() for d in event_days}
    complete = complete.copy()
    complete["sign_0840"] = (complete["hold_price_0840"] -
                              complete["vwap_0830_0834"]).apply(swstudy._sign)
    held_event_days = complete[complete["date"].isin(event_dates) &
                                (complete["sign_0840"] == complete["above_vwap"])]
    assert len(held_event_days) == len(complete[complete["date"].isin(event_dates)])
    assert result["held_n"] > 0
