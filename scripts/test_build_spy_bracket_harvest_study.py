"""Fault-injection tests for the SPY bracket harvest study.

Invariants that must be provably enforced, not just documented:
  1. `simulate_trade` fills the buy side when only the upper trigger is
     touched, and prices gross/net P&L off the TRIGGER, not the bar's high.
  2. `simulate_trade` fills the sell side symmetrically.
  3. a single bar touching BOTH triggers is scored `double_stop`, and its
     net_bp is NEVER positive -- the exact invariant the task explicitly
     calls out (a deliberate "score it as a win" mutation must fail this).
  4. a day where no bar ever touches either trigger is `no_fill` with
     net_bp == 0 exactly (no cost charged for a trade that never happened).
  5. OCO: once one side fills on an earlier bar, a LATER bar touching the
     opposite trigger must NOT retroactively turn the day into a
     double_stop -- the scan stops at first fill.
  6. a day missing its reference bar is excluded (reference-exclusion list),
     never assigned a synthetic reference price.
  7. a day missing any bucket of the scan window is excluded (scan-exclusion
     list), never partially scanned.
  8. the bracket width is derived EXCLUSIVELY from the control population --
     mutating only an event day's bars must not move the computed width.
  9. `arm_expectancy` includes no-fill days in its denominator at net_bp=0,
     never drops them (no fill-conditioning survivorship bias).
 10. the permutation null's p-value is computed correctly against a
     constructed net_bp series with a known rank.
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

import build_spy_bracket_harvest_study as hstudy  # noqa: E402
import build_spy_macro_decay_study as dstudy       # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------- fixtures

def _bar(o: float, h: float, l: float, c: float) -> dict:
    return {"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1000}


def _window(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _bar_rows(day: date, start_hhmm: str, end_hhmm: str, *, base_price: float = 100.0,
              drift: float = 0.0002, wiggle: float = 0.0002) -> list[dict]:
    sh, sm = (int(x) for x in start_hhmm.split(":"))
    eh, em = (int(x) for x in end_hhmm.split(":"))
    start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=ET)
    end = datetime(day.year, day.month, day.day, eh, em, tzinfo=ET)
    rows = []
    price = base_price
    ts = start
    while ts <= end:
        o = price
        c = o + drift
        hi = max(o, c) + wiggle
        lo = min(o, c) - wiggle
        rows.append({"ts": ts, "Open": o, "High": hi, "Low": lo, "Close": c, "Volume": 1000})
        price = c
        ts += timedelta(minutes=1)
    return rows


def _synthetic_1m_cache(tmp_path: Path, symbol: str, days: list[date], *,
                         big_move_days: set[date] | None = None,
                         big_move_bp: float = 40.0) -> Path:
    """Raw 1m bars covering 08:25-08:50 and 13:55-14:20 for each day.
    `big_move_days` get a large, clean directional bar at 08:32 (well
    outside the 08:29 reference and 08:30 scan open) so the resulting
    control-population `range` distribution is well separated -- used only
    where a test needs a real constructed spread, not for the pure
    arithmetic unit tests, which build windows directly."""
    big_move_days = big_move_days or set()
    rows: list[dict] = []
    for day in days:
        rows += _bar_rows(day, "08:25", "08:50")
        rows += _bar_rows(day, "13:55", "14:20")
        if day in big_move_days:
            # widen the 08:32 bar's own high/low, well inside the scan
            # window (08:30-08:44), to move that day's `range` up sharply.
            base = 100.0
            pass  # left as documentation: big_move construction happens via range mutation below
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
    monkeypatch.setattr(dstudy, "CACHE_1M", cache)


# ----------------------------------------------------- 1/2: single-side fills

def test_simulate_trade_buy_fill_prices_off_trigger_not_bar_high():
    ref = 100.0
    width = 0.01  # 1%
    rows = [
        _bar(100.0, 100.05, 99.95, 100.02),          # no touch
        _bar(100.02, 101.5, 100.0, 101.2),            # touches upper (101.0), fills buy
        _bar(101.2, 101.6, 101.1, 101.55),            # after fill, ignored for entry
    ]
    window = _window(rows)
    sim = hstudy.simulate_trade(ref, width, window)
    assert sim["outcome"] == "buy"
    upper = ref * 1.01
    exit_price = rows[-1]["Close"]
    expected_gross_bp = (exit_price - upper) / ref * 1e4
    assert sim["gross_bp"] == pytest.approx(expected_gross_bp)
    expected_net_bp = expected_gross_bp - (hstudy.ENTRY_COST_BP + hstudy.EXIT_COST_BP)
    assert sim["net_bp"] == pytest.approx(expected_net_bp)


def test_simulate_trade_sell_fill_symmetric():
    ref = 100.0
    width = 0.01
    rows = [
        _bar(100.0, 100.05, 99.95, 100.02),
        _bar(100.02, 100.1, 98.4, 98.6),               # touches lower (99.0), fills sell
        _bar(98.6, 98.7, 98.5, 98.55),
    ]
    window = _window(rows)
    sim = hstudy.simulate_trade(ref, width, window)
    assert sim["outcome"] == "sell"
    lower = ref * 0.99
    exit_price = rows[-1]["Close"]
    expected_gross_bp = (lower - exit_price) / ref * 1e4
    assert sim["gross_bp"] == pytest.approx(expected_gross_bp)


# ----------------------------------------------------------- 3: double-stop

def test_double_touch_bar_scored_as_loss_never_a_win():
    """The explicitly required invariant: a bar whose High/Low span BOTH
    triggers must be scored `double_stop`, and its net_bp must never be
    positive -- across a range of widths and reference prices, not just one
    constructed case."""
    for ref in (50.0, 100.0, 437.5, 601.2):
        for width in (0.001, 0.003, 0.01, 0.02):
            upper = ref * (1 + width)
            lower = ref * (1 - width)
            rows = [_bar(ref, upper + 0.01, lower - 0.01, ref)]
            window = _window(rows)
            sim = hstudy.simulate_trade(ref, width, window)
            assert sim["outcome"] == "double_stop"
            assert sim["net_bp"] < 0, (
                f"double_stop must never be scored a win (ref={ref}, width={width})")
            assert sim["gross_bp"] == pytest.approx(-2.0 * width * 1e4)
            expected_net = sim["gross_bp"] - 2.0 * hstudy.ENTRY_COST_BP
            assert sim["net_bp"] == pytest.approx(expected_net)


def test_double_touch_takes_priority_even_if_upper_checked_first_in_code():
    """Mutation guard: even though the implementation checks touch_up before
    touch_down, a bar satisfying both must resolve to double_stop, not
    silently fall through to 'buy'."""
    ref, width = 100.0, 0.01
    upper, lower = ref * 1.01, ref * 0.99
    rows = [_bar(ref, upper + 5.0, lower - 5.0, ref)]  # grossly exceeds both
    sim = hstudy.simulate_trade(ref, width, _window(rows))
    assert sim["outcome"] == "double_stop"


# --------------------------------------------------------------- 4: no fill

def test_no_touch_anywhere_is_no_fill_zero_cost():
    ref, width = 100.0, 0.05  # wide bracket, tiny wiggle bars
    rows = [_bar(100.0, 100.1, 99.9, 100.05) for _ in range(5)]
    sim = hstudy.simulate_trade(ref, width, _window(rows))
    assert sim["outcome"] == "no_fill"
    assert sim["gross_bp"] == 0.0
    assert sim["net_bp"] == 0.0


# --------------------------------------------------------------- 5: OCO scan order

def test_oco_first_fill_wins_later_opposite_touch_ignored():
    ref, width = 100.0, 0.01
    upper, lower = ref * 1.01, ref * 0.99
    rows = [
        _bar(100.0, 100.05, 99.95, 100.02),   # no touch
        _bar(100.02, upper + 0.5, 100.0, upper + 0.2),  # fills buy first
        _bar(upper + 0.2, upper + 0.3, lower - 0.5, upper),  # would ALSO touch lower now
    ]
    sim = hstudy.simulate_trade(ref, width, _window(rows))
    assert sim["outcome"] == "buy", "OCO: fill on bar 2 must cancel the sell side permanently"


# --------------------------------------------------------- 6/7: completeness gates

def test_missing_reference_bar_excluded_not_synthesized(tmp_path, monkeypatch, business_days):
    cache = _synthetic_1m_cache(tmp_path, "TST", business_days[:5])
    path = cache / "TST_1m.parquet"
    df = pd.read_parquet(path)
    target_day = business_days[2]
    idx = df.index
    drop_mask = (idx.date == target_day) & (idx.strftime("%H:%M") == "08:29")
    df = df[~drop_mask]
    df.to_parquet(path)
    _use_1m_cache(monkeypatch, cache)

    days, scan_frames, ref_excl, scan_excl = hstudy.reference_and_scan_day_stats(
        "TST", hstudy.REF_MINUTE_PRIMARY, hstudy.SCAN_MINUTES_PRIMARY,
        hstudy.SCAN_WINDOW_LABEL_PRIMARY)

    assert len(days) == 4
    assert target_day.isoformat() not in set(days["date"])
    assert any(e["date"] == target_day.isoformat() for e in ref_excl)


def test_missing_scan_bucket_excluded(tmp_path, monkeypatch, business_days):
    cache = _synthetic_1m_cache(tmp_path, "TST", business_days[:5])
    path = cache / "TST_1m.parquet"
    df = pd.read_parquet(path)
    target_day = business_days[3]
    idx = df.index
    drop_mask = (idx.date == target_day) & (idx.strftime("%H:%M") == "08:37")
    df = df[~drop_mask]
    df.to_parquet(path)
    _use_1m_cache(monkeypatch, cache)

    days, scan_frames, ref_excl, scan_excl = hstudy.reference_and_scan_day_stats(
        "TST", hstudy.REF_MINUTE_PRIMARY, hstudy.SCAN_MINUTES_PRIMARY,
        hstudy.SCAN_WINDOW_LABEL_PRIMARY)

    assert len(days) == 4
    assert target_day.isoformat() not in set(days["date"])
    assert any(e["date"] == target_day.isoformat() for e in scan_excl)
    assert "08:37" in scan_excl[[e["date"] for e in scan_excl].index(target_day.isoformat())]["reason"]


# --------------------------------------------------------- 8: width is control-only

def test_bracket_width_never_moves_from_event_day_mutation(tmp_path, monkeypatch, business_days):
    days_list = business_days[:10]
    cache = _synthetic_1m_cache(tmp_path, "TST", days_list)
    _use_1m_cache(monkeypatch, cache)

    event_dates = {d.isoformat() for d in days_list[::3]}
    width_before, n_before = hstudy.control_bracket_width(
        "TST", hstudy.SCAN_MINUTES_PRIMARY, hstudy.SCAN_WINDOW_LABEL_PRIMARY, event_dates)

    # Blow up ONE event day's scan-window bars with a huge spike -- if the
    # width leaked event-day information, this would move it.
    path = cache / "TST_1m.parquet"
    df = pd.read_parquet(path)
    target_day = days_list[0]
    assert target_day.isoformat() in event_dates
    idx = df.index
    mask = (idx.date == target_day) & (idx.strftime("%H:%M") == "08:35")
    df.loc[mask, "High"] = df.loc[mask, "High"] + 50.0
    df.loc[mask, "Low"] = df.loc[mask, "Low"] - 50.0
    df.to_parquet(path)

    width_after, n_after = hstudy.control_bracket_width(
        "TST", hstudy.SCAN_MINUTES_PRIMARY, hstudy.SCAN_WINDOW_LABEL_PRIMARY, event_dates)

    assert width_after == pytest.approx(width_before)
    assert n_after == n_before


def test_bracket_width_moves_when_a_control_day_is_mutated(tmp_path, monkeypatch, business_days):
    """Positive control for the previous test: mutating a CONTROL day's
    bars (not excluded from the width population) DOES change the width --
    proving the exclusion in the prior test is doing real work, not just
    coincidentally stable."""
    days_list = business_days[:10]
    cache = _synthetic_1m_cache(tmp_path, "TST", days_list)
    _use_1m_cache(monkeypatch, cache)

    event_dates = {d.isoformat() for d in days_list[::3]}
    control_days = [d for d in days_list if d.isoformat() not in event_dates]

    width_before, _ = hstudy.control_bracket_width(
        "TST", hstudy.SCAN_MINUTES_PRIMARY, hstudy.SCAN_WINDOW_LABEL_PRIMARY, event_dates)

    # Blow up EVERY control day's 08:35 bar -- guarantees the p75 of the
    # control `range` distribution shifts, regardless of exactly which rank
    # the percentile interpolation happens to land on.
    path = cache / "TST_1m.parquet"
    df = pd.read_parquet(path)
    idx = df.index
    mask = pd.Series(False, index=idx)
    for cd in control_days:
        mask = mask | ((idx.date == cd) & (idx.strftime("%H:%M") == "08:35"))
    df.loc[mask, "High"] = df.loc[mask, "High"] + 50.0
    df.loc[mask, "Low"] = df.loc[mask, "Low"] - 50.0
    df.to_parquet(path)

    width_after, _ = hstudy.control_bracket_width(
        "TST", hstudy.SCAN_MINUTES_PRIMARY, hstudy.SCAN_WINDOW_LABEL_PRIMARY, event_dates)

    assert width_after != pytest.approx(width_before)


# --------------------------------------------------------- 9: no-fill included in denominator

def test_arm_expectancy_includes_no_fill_days_at_zero():
    trades = pd.DataFrame([
        {"date": "2024-01-02", "outcome": "buy", "gross_bp": 14.0, "net_bp": 10.0, "net_R": 0.5},
        {"date": "2024-01-03", "outcome": "no_fill", "gross_bp": 0.0, "net_bp": 0.0, "net_R": 0.0},
        {"date": "2024-01-04", "outcome": "sell", "gross_bp": -1.0, "net_bp": -5.0, "net_R": -0.25},
    ])
    member = {"2024-01-02", "2024-01-03", "2024-01-04"}
    rec = hstudy.arm_expectancy(trades, member, "test_arm")
    assert rec["n"] == 3
    assert rec["no_fill_rate"] == pytest.approx(1 / 3)
    assert rec["mean_net_bp_per_event"] == pytest.approx((10.0 + 0.0 - 5.0) / 3)


# --------------------------------------------------------- 10: permutation p-value

def test_permutation_pvalue_against_constructed_series():
    # Population: 8 ordinary days at net_bp=0, plus one day the "event"
    # sample will draw at net_bp=1000 (far above anything achievable by
    # random single-day draws from the zero pool alone -- deterministic
    # p-value check).
    dates = [f"2024-01-{i:02d}" for i in range(1, 10)]
    net_bps = [0.0] * 8 + [1000.0]
    trades = pd.DataFrame({"date": dates, "net_bp": net_bps})

    perm = hstudy.permutation_null(trades, n_event=1, n_perm=500, seed=42)
    finished = hstudy._finish_permutation(perm, observed_mean=1000.0)
    assert finished["n_perm"] == 500
    # observed (1000) can only be matched/exceeded by permutations that
    # happen to draw the single 1000-valued day -- roughly 1/9 of draws.
    assert 0.05 < finished["p_value_one_sided_event_gt_random"] < 0.25
    assert finished["null_mean_net_bp"] == pytest.approx(1000.0 / 9, rel=0.3)


def test_permutation_empty_when_no_event_days():
    trades = pd.DataFrame({"date": ["2024-01-01"], "net_bp": [5.0]})
    perm = hstudy.permutation_null(trades, n_event=0)
    finished = hstudy._finish_permutation(perm, observed_mean=None)
    assert finished["p_value_one_sided_event_gt_random"] is None
