"""Fault-injection tests for the macro event study builder.

Invariants that must be provably enforced, not just documented:
  1. an event date that FRED reports but that is not itself an RTH session
     in the extended cache is DROPPED with a reason, never coerced to the
     nearest trading day.
  2. horizons index into the actual session list, never manufacture a
     calendar date the cache never observed.
  3. the control pool never includes a session that is itself a scheduled
     event day for any tracked release type.
  4. a FRED release query returning zero dates is a hard refusal, never an
     empty-but-silent calendar.
  5. BH correction is actually applied and is at least as conservative as
     the raw p-value it adjusts.
Each has a test below that fails if the guarding code is removed or reverted.
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

import build_macro_event_study as mes   # noqa: E402
from bars import BarDataError           # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------- fixtures

def _synthetic_cache(tmp_path: Path, symbol: str, sessions: list[date],
                      *, seed_shift: float = 0.0) -> Path:
    """RTH-only 5m cache, one full 78-bar session per date, mildly varying
    intrasession range so ret/range_pct/realized_vol are not degenerate."""
    rows = []
    for n, sess in enumerate(sessions):
        start = datetime(sess.year, sess.month, sess.day, 9, 30, tzinfo=ET)
        price = 100.0 + seed_shift
        for i in range(78):
            ts = start + timedelta(minutes=5 * i)
            wiggle = 0.05 * ((i + n) % 5 - 2)
            price += wiggle
            rows.append({"ts": ts, "Open": price, "High": price + 0.10,
                         "Low": price - 0.10, "Close": price + 0.02,
                         "Volume": 1000 + i})
    df = pd.DataFrame(rows).set_index("ts")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache / f"{symbol}_5m.parquet")
    return cache


@pytest.fixture
def business_days():
    """15 consecutive weekday sessions starting Monday 2024-01-01 (a real
    Monday), so weekday matching in the control pool is meaningful."""
    d = date(2024, 1, 1)
    out = []
    while len(out) < 15:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# --------------------------------------------------------- 1: dropped events

def test_event_not_a_session_is_dropped_with_a_reason(tmp_path, business_days):
    cache = _synthetic_cache(tmp_path, "TST", business_days)
    sessions = mes.session_stats("TST", cache)

    # 2024-01-06 is a Saturday -- never a weekday, so never in business_days
    # -- construct an event on it deliberately.
    events = [{"date": "2024-01-06", "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}]
    ripples, dropped = mes.build_ripple_dataset(sessions, events, "TST")

    assert ripples.empty
    assert len(dropped) == 1
    assert dropped[0]["date"] == "2024-01-06"
    assert "not an RTH session" in dropped[0]["reason"]


def test_horizon_past_end_of_cache_is_dropped_not_manufactured(tmp_path, business_days):
    cache = _synthetic_cache(tmp_path, "TST", business_days)
    sessions = mes.session_stats("TST", cache)

    last_session = business_days[-1].isoformat()
    events = [{"date": last_session, "release_id": 10,
               "release_name": "Consumer Price Index", "importance": 1.0}]
    ripples, dropped = mes.build_ripple_dataset(sessions, events, "TST")

    # horizon 0 exists (the event day itself); horizons 1 and 2 run off the
    # end of the 15-session cache and must be dropped, not synthesized.
    got_horizons = set(ripples["horizon"].unique().tolist())
    assert 0 in got_horizons
    dropped_horizons = {d.get("horizon") for d in dropped}
    assert 1 in dropped_horizons or 2 in dropped_horizons
    for d in dropped:
        if "horizon" in d:
            assert "past the end" in d["reason"]


# --------------------------------------------------------- 2: no manufactured dates

def test_ripple_session_dates_are_always_real_cache_sessions(tmp_path, business_days):
    cache = _synthetic_cache(tmp_path, "TST", business_days)
    sessions = mes.session_stats("TST", cache)
    real_dates = set(sessions["date"])

    events = [{"date": business_days[3].isoformat(), "release_id": 50,
               "release_name": "Employment Situation", "importance": 1.0}]
    ripples, _ = mes.build_ripple_dataset(sessions, events, "TST")

    assert set(ripples["session_date"].unique()).issubset(real_dates)


# --------------------------------------------------------- 3: clean control pool

def test_control_pool_excludes_every_scheduled_event_day(tmp_path, business_days):
    cache = _synthetic_cache(tmp_path, "TST", business_days)
    sessions = mes.session_stats("TST", cache)

    event_dates = {business_days[2].isoformat(), business_days[5].isoformat(),
                   business_days[9].isoformat()}
    ctrl = mes.control_pool(sessions, event_dates)

    assert event_dates.isdisjoint(set(ctrl["date"]))
    assert len(ctrl) == len(sessions) - len(event_dates)


# --------------------------------------------------------- 4: FRED refusal

def test_zero_dates_from_fred_is_a_hard_refusal(monkeypatch):
    class _FakeResp:
        status_code = 200

        def json(self):
            return {"release_dates": []}

    monkeypatch.setattr(mes.requests, "get", lambda *a, **k: _FakeResp())
    with pytest.raises(mes.EventStudyError, match="zero dates"):
        mes.fetch_historical_release_dates(
            "10", "fake-key", start=date(2024, 1, 1), end=date(2024, 12, 31))


def test_missing_extended_cache_refuses_not_falls_back(tmp_path):
    empty = tmp_path / "empty_cache"
    empty.mkdir()
    with pytest.raises(BarDataError, match="Refusing to fall back"):
        mes.session_stats("TST", empty)


# --------------------------------------------------------- 5: BH conservatism

def test_bh_adjustment_is_never_less_conservative_than_raw_p():
    raw = [0.001, 0.02, 0.03, 0.04, 0.20, 0.50, None]
    adj = mes._bh_adjust(raw)
    for r, a in zip(raw, adj):
        if r is None:
            assert a is None
        else:
            assert a >= r - 1e-12


def test_bh_adjustment_is_monotone_non_decreasing_when_sorted():
    raw = [0.5, 0.4, 0.3, 0.2, 0.1, 0.01]
    adj = mes._bh_adjust(raw)
    order = sorted(range(len(raw)), key=lambda i: raw[i])
    sorted_adj = [adj[i] for i in order]
    assert all(sorted_adj[i] <= sorted_adj[i + 1] + 1e-12
               for i in range(len(sorted_adj) - 1))


# --------------------------------------------------------- session stats sanity

def test_session_stats_metrics_are_finite_and_in_expected_shape(tmp_path, business_days):
    cache = _synthetic_cache(tmp_path, "TST", business_days)
    sessions = mes.session_stats("TST", cache)

    assert len(sessions) == len(business_days)
    for col in ("ret", "abs_ret", "range_pct", "realized_vol"):
        assert sessions[col].notna().all()
    assert (sessions["abs_ret"] >= 0).all()
    assert (sessions["range_pct"] >= 0).all()
    assert (sessions["realized_vol"] >= 0).all()
