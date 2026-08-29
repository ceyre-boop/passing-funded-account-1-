"""Fault-injection tests for the SPY range-expansion study (spec 043).

Every invariant below has a test that FAILS when the invariant is
deliberately violated — passing today is not the claim; the claim is that
removing the guard breaks the suite.

  1. ATR20_prior LOOK-AHEAD. `prior_window` refuses an `end_index` at or past
     the measured day, and the measured day's own bars provably cannot move
     its own denominator. This is the load-bearing property of the study.
  2. A day without 20 strictly-prior complete sessions is DROPPED with a
     reason, never computed on a short window, never back-filled.
  3. Control-pool contamination, unbuffered: no event date may appear in the
     control pool, on the `date` field or after weekday matching.
  4. Control-pool contamination, buffered: the +/-1-session robustness pool
     must exclude an event day's NEIGHBOURS as well as the event day.
  5. Permutation-null RANK correctness (`>=`, not `>`) and NON-MUTATION of
     the inputs it is handed.
  6. RTH window boundaries: the 08:30 impulse is outside the measured window
     by construction, a post-16:00 bar cannot widen the range, and a day with
     a hole anywhere in 09:30-15:55 is EXCLUDED with a reason.
  7. Secondary-family BH independence: the correction runs over the secondary
     family and only that family; the primary carries no BH-adjusted p and
     its own p is untouched.
  8. `unscheduled: true` FOMC dates never enter the event population, and a
     calendar that trips `macro_calendar.validate_fomc_events` REFUSES the
     run rather than being measured against.
  9. ORB never fabricates a fill: a bar breaking both sides of the opening
     range yields NO trade, not a guessed intrabar order.
 10. The pre-registered 1.10 economic floor cannot be softened — a
     significant result below it is reported in those words.
 11. MDE reuses `daytrade/mechanisms.mde` rather than re-deriving the
     constants.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))
sys.path.insert(0, str(ROOT / "scripts"))

import mechanisms                                          # noqa: E402
import build_macro_event_study as mes                       # noqa: E402
import build_spy_premarket_event_study as bspes             # noqa: E402
import build_spy_range_expansion_study as rex               # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------- fixtures

def day_bars(day: str, *, high: float, low: float, open_px: float = 100.0,
             close: float = 100.0, start: str = "09:30", end: str = "15:55",
             drop_times: tuple[str, ...] = ()) -> pd.DataFrame:
    """Flat 5-minute bars across [start, end] on `day`, every bar carrying the
    same O/H/L/C so the session's high/low are exactly `high`/`low`."""
    idx = pd.date_range(pd.Timestamp(f"{day} {start}", tz=ET),
                        pd.Timestamp(f"{day} {end}", tz=ET), freq="5min")
    df = pd.DataFrame({"Open": open_px, "High": high, "Low": low,
                        "Close": close, "Volume": 1_000}, index=idx)
    if drop_times:
        keep = [ts for ts in df.index if ts.strftime("%H:%M") not in drop_times]
        df = df.loc[keep]
    return df


def sessions_frame(n: int, *, rng_pts: float = 1.0, close: float = 100.0,
                   vary: bool = True) -> pd.DataFrame:
    """`n` synthetic sessions in the shape `attach_expansion` consumes. Ranges
    vary deterministically by default so the resulting expansion series has
    non-zero variance (a constant series makes Welch's t undefined and would
    make a test pass for the wrong reason)."""
    rows = []
    for i in range(n):
        d = (pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)).date().isoformat()
        r = rng_pts * (1.0 + 0.15 * ((i * 7) % 5)) if vary else rng_pts
        rows.append({"date": d, "weekday": pd.Timestamp(d).strftime("%A"),
                      "rth_high": close + r / 2, "rth_low": close - r / 2,
                      "rth_close": close, "rth_range": r,
                      "am_range": r / 2, "pm_range": r / 2})
    return pd.DataFrame(rows)


# ------------------------------------------------------- 1. ATR20 look-ahead

def test_prior_window_refuses_an_end_index_that_includes_the_measured_day():
    values = [float(i) for i in range(60)]
    assert rex.prior_window(values, 30, 20) == values[10:30]
    # fewer than `window` prior values -> EMPTY, never a silently short window
    assert rex.prior_window(values, 10, 20) == []
    assert rex.prior_window(values, 19, 20) == []
    assert len(rex.prior_window(values, 20, 20)) == 20
    with pytest.raises(rex.RangeExpansionError) as e:
        rex.prior_window(values, 30, 20, end_index=31)
    assert "LOOK-AHEAD REFUSED" in str(e.value)
    with pytest.raises(rex.RangeExpansionError):
        rex.prior_window(values, 30, 20, end_index=1000)


def test_the_measured_days_own_bars_cannot_move_its_own_atr20_denominator():
    """THE anti-lookahead test. Blow up ONLY the last day's range; its
    ATR20_prior must be bit-identical, and only its expansion may change. If
    a same-day bar ever leaks into the denominator, this fails."""
    base = sessions_frame(30, rng_pts=1.0)
    days_a, _ = rex.attach_expansion(base)

    leaked = base.copy()
    last = len(leaked) - 1
    leaked.loc[last, "rth_high"] = 200.0
    leaked.loc[last, "rth_low"] = 0.0
    leaked.loc[last, "rth_range"] = 200.0
    days_b, _ = rex.attach_expansion(leaked)

    a = days_a[days_a["date"] == base["date"].iloc[last]].iloc[0]
    b = days_b[days_b["date"] == base["date"].iloc[last]].iloc[0]
    assert b["atr20_prior"] == a["atr20_prior"]
    assert b["atr20_prior_rth"] == a["atr20_prior_rth"]
    assert b["expansion"] > a["expansion"] * 100


# --------------------------------------- 2. short ATR window is dropped, not faked

def test_day_without_twenty_strictly_prior_sessions_is_dropped_with_a_reason():
    days, dropped = rex.attach_expansion(sessions_frame(25))
    # index 0 has no prior close (TR undefined) and indices 1..20 cannot see
    # 20 DEFINED prior true ranges -> 21 dropped, 4 measurable.
    assert len(days) == 4
    assert len(dropped) == 21
    assert all("fewer than 20 strictly-prior" in d["reason"] for d in dropped)
    assert days["atr20_prior"].notna().all()


def test_atr20_is_the_mean_of_exactly_twenty_strictly_prior_true_ranges():
    """Positive check on the window itself: recomputing TR independently for
    sessions 1..20 must reproduce the first measurable day's ATR20_prior
    exactly. A window that is short, long, or shifted fails here."""
    base = sessions_frame(25)
    days, _ = rex.attach_expansion(base)
    first = days.iloc[0]
    assert first["date"] == base["date"].iloc[21]

    highs, lows, closes = (base["rth_high"].tolist(), base["rth_low"].tolist(),
                            base["rth_close"].tolist())
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
               abs(lows[i] - closes[i - 1])) for i in range(1, 21)]
    assert len(trs) == rex.ATR_WINDOW
    assert first["atr20_prior"] == pytest.approx(sum(trs) / len(trs))
    assert first["atr20_prior_rth"] == pytest.approx(
        sum(base["rth_range"].tolist()[1:21]) / rex.ATR_WINDOW)


# ------------------------------------------ 3./4. control-pool contamination

def test_control_pool_never_contains_an_event_date():
    days = sessions_frame(10)
    events = {days["date"].iloc[3], days["date"].iloc[7]}
    pool = rex.control_pool(days, events)
    assert not (set(pool["date"]) & events)
    assert len(pool) == 8
    # and weekday matching cannot re-admit one
    m = rex.matched(pool, set(days["weekday"]))
    assert not (set(m["date"]) & events)


def test_buffered_control_pool_excludes_the_neighbours_of_an_event_day():
    days = sessions_frame(7)
    d = days["date"].tolist()
    pool = rex.buffered_control_pool(days, {d[3]})
    assert set(pool["date"]) == {d[0], d[1], d[5], d[6]}
    assert d[2] not in set(pool["date"])      # day BEFORE the event
    assert d[4] not in set(pool["date"])      # day AFTER the event


def test_buffered_pool_measures_adjacency_in_sessions_not_calendar_days():
    """A Monday is adjacent to the prior Friday even though three calendar
    days separate them — the pool must work in session space."""
    rows = []
    for iso in ["2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]:
        rows.append({"date": iso, "weekday": pd.Timestamp(iso).strftime("%A")})
    days = pd.DataFrame(rows)
    pool = rex.buffered_control_pool(days, {"2024-01-05"})   # a Friday
    assert set(pool["date"]) == {"2024-01-09"}               # Mon 01-08 dropped


# ----------------------------------------------------- 5. permutation null

def test_permutation_null_rank_uses_greater_or_equal_not_strictly_greater():
    # observed event mean is the MINIMUM possible -> every reshuffle ties or
    # beats it -> p must be exactly 1.0. A strict `>` would return 0.0.
    res = rex.permutation_null([0.0, 0.0], [10.0, 10.0, 10.0], n_perm=200)
    assert res["p_value_one_sided_event_gt_random"] == 1.0

    # observed event mean is the MAXIMUM possible; only the 1-in-10 reshuffle
    # that picks both tens can tie it.
    res2 = rex.permutation_null([10.0, 10.0], [0.0, 0.0, 0.0], n_perm=2000)
    assert 0.04 < res2["p_value_one_sided_event_gt_random"] < 0.17
    assert res2["n_perm"] == 2000
    assert res2["observed_event_mean"] == 10.0


def test_permutation_null_does_not_mutate_the_values_it_is_handed():
    ev = [1.0, 2.0, 3.0]
    ct = [4.0, 5.0, 6.0, 7.0]
    ev_before, ct_before = list(ev), list(ct)
    rex.permutation_null(ev, ct, n_perm=50)
    assert ev == ev_before
    assert ct == ct_before


def test_permutation_null_is_deterministic_under_its_fixed_seed():
    a = rex.permutation_null([1.0, 5.0, 9.0], [2.0, 3.0, 4.0], n_perm=300)
    b = rex.permutation_null([1.0, 5.0, 9.0], [2.0, 3.0, 4.0], n_perm=300)
    assert a["p_value_one_sided_event_gt_random"] == b["p_value_one_sided_event_gt_random"]


# --------------------------------------------------- 6. RTH window boundaries

def test_rth_window_excludes_the_0830_impulse_and_anything_after_1600(monkeypatch):
    """A monstrous 08:30 pre-market bar and a monstrous 16:05 bar must both be
    invisible to the measured range. If either leaked in, this study would be
    re-measuring the already-known impulse instead of the RTH day."""
    rth = day_bars("2024-01-03", high=101.0, low=99.0)
    pre = day_bars("2024-01-03", high=500.0, low=1.0, start="08:30", end="09:25")
    post = day_bars("2024-01-03", high=500.0, low=1.0, start="16:00", end="16:05")
    raw = pd.concat([pre, rth, post]).sort_index()
    monkeypatch.setattr(bspes, "_load_raw_bars", lambda symbol: raw)

    frames, excluded = rex.rth_frames("SPY")
    assert excluded == []
    s = rex.session_rows(frames)
    assert s["rth_high"].iloc[0] == 101.0
    assert s["rth_low"].iloc[0] == 99.0
    assert s["rth_range"].iloc[0] == pytest.approx(2.0)


def test_a_day_with_a_hole_in_rth_is_excluded_with_a_reason_never_interpolated(monkeypatch):
    good = day_bars("2024-01-03", high=101.0, low=99.0)
    holed = day_bars("2024-01-04", high=101.0, low=99.0, drop_times=("11:20",))
    raw = pd.concat([good, holed]).sort_index()
    monkeypatch.setattr(bspes, "_load_raw_bars", lambda symbol: raw)

    frames, excluded = rex.rth_frames("SPY")
    assert set(frames) == {"2024-01-03"}
    assert len(excluded) == 1
    assert excluded[0]["date"] == "2024-01-04"
    assert "missing 5m RTH bar" in excluded[0]["reason"]


def test_am_and_pm_halves_partition_the_session_at_noon(monkeypatch):
    morning = day_bars("2024-01-03", high=110.0, low=90.0, start="09:30", end="11:55")
    afternoon = day_bars("2024-01-03", high=101.0, low=99.0, start="12:00", end="15:55")
    raw = pd.concat([morning, afternoon]).sort_index()
    monkeypatch.setattr(bspes, "_load_raw_bars", lambda symbol: raw)
    frames, excluded = rex.rth_frames("SPY")
    assert excluded == []
    s = rex.session_rows(frames).iloc[0]
    assert s["am_range"] == pytest.approx(20.0)
    assert s["pm_range"] == pytest.approx(2.0)
    assert s["rth_range"] == pytest.approx(20.0)


# ------------------------------------------- 7. secondary-family BH independence

def _labelled_days(n_event: int = 25, n_control: int = 60) -> pd.DataFrame:
    """Days whose first `n_event` rows carry a REAL, constructed separation in
    every secondary metric, so the family's p-values are small enough that a
    change in the BH family size actually moves the adjusted values. A
    degenerate all-equal fixture would make the BH test pass for the wrong
    reason (every adjusted p clamped to 1.0 either way)."""
    days, _ = rex.attach_expansion(sessions_frame(n_event + n_control + 25))
    days = days.reset_index(drop=True)
    for i in range(len(days)):
        bump = 0.6 if i < n_event else 0.0
        noise = 0.05 * ((i * 13) % 7)
        days.loc[i, "expansion"] = 1.0 + bump + noise
        days.loc[i, "expansion_am"] = 0.6 + bump / 2 + noise
        days.loc[i, "expansion_pm"] = 0.6 + bump / 2 + noise
        days.loc[i, "orb_R"] = (0.5 if i % 2 else -0.5) + bump
    days["orb_outcome"] = "held_to_close"
    return days


def test_bh_correction_runs_over_the_secondary_family_only():
    days = _labelled_days()
    ev_dates = set(days["date"].iloc[:10])
    sec = rex.secondary_family(days, ev_dates, set(), ev_dates)
    tests = sec["tests"]
    assert all("welch_p_bh" in t for t in tests)
    # BH must use m = the number of SECONDARY tests, nothing else.
    expected = mes._bh_adjust([t["welch_p_two_sided"] for t in tests])
    assert [t["welch_p_bh"] for t in tests] == expected
    assert sec["multiple_testing"]["tests_run"] == sum(
        1 for t in tests if t["welch_p_two_sided"] is not None)
    assert sec["NO_INFERENTIAL_WEIGHT"] is True
    assert sec["may_never_be_promoted_to_primary"] is True


def test_primary_carries_no_bh_adjusted_p_and_is_not_in_the_secondary_family():
    days = _labelled_days()
    ev_dates = set(days["date"].iloc[:10])
    primary = rex.primary_test(days, ev_dates)
    assert "welch_p_bh" not in primary
    # the primary's one-sided p is derived from its own Welch t alone
    p_two = primary["welch_p_two_sided"]
    expected = p_two / 2 if primary["welch_t"] > 0 else 1 - p_two / 2
    assert primary["welch_p_one_sided_event_gt_control"] == expected


def test_event_type_arms_are_skipped_not_fabricated_when_the_calendar_lacks_them():
    days = _labelled_days()
    ev_dates = set(days["date"].iloc[:10])
    sec = rex.secondary_family(days, ev_dates, set(), set())
    reasons = {s["arm"] for s in sec["skipped_arms"]}
    assert "C1 FOMC" in reasons and "C2 releases-without-FOMC" in reasons
    assert not any(t["label"].startswith("C") for t in sec["tests"])


# ------------------------------------------------------------ 8. FOMC calendar

def _write_fomc(tmp_path: Path, events: list[dict]) -> Path:
    import json
    p = tmp_path / "fomc.json"
    p.write_text(json.dumps({"verified_as_of": "2026-08-01", "events": events}))
    return p


def test_unscheduled_fomc_dates_never_enter_the_event_population(tmp_path):
    p = _write_fomc(tmp_path, [
        {"date": "2024-01-31", "label": "FOMC decision"},
        {"date": "2024-02-10", "label": "emergency", "unscheduled": True,
         "source_url": "https://example.invalid/emergency"},
        {"date": "2024-03-20", "label": "FOMC decision"},
    ])
    dates, prov = rex.load_scheduled_fomc_dates(fomc_json=p)
    assert dates == {"2024-01-31", "2024-03-20"}
    assert prov["unscheduled_excluded"] == ["2024-02-10"]


def test_a_fabricated_looking_fomc_calendar_refuses_the_run(tmp_path):
    """The release-101 signature: back-to-back daily 'meetings'. The study
    must REFUSE, not measure against it."""
    p = _write_fomc(tmp_path, [
        {"date": "2024-01-31", "label": "FOMC decision"},
        {"date": "2024-02-01", "label": "FOMC decision"},
    ])
    with pytest.raises(rex.RangeExpansionError) as e:
        rex.load_scheduled_fomc_dates(fomc_json=p)
    assert "validate_fomc_events" in str(e.value)


def test_a_missing_fomc_calendar_refuses_rather_than_running_without_it(tmp_path):
    with pytest.raises(rex.RangeExpansionError):
        rex.load_scheduled_fomc_dates(fomc_json=tmp_path / "nope.json")


def test_a_missing_fred_artifact_refuses_rather_than_guessing(tmp_path):
    with pytest.raises(rex.RangeExpansionError) as e:
        rex.load_fred_event_dates(artifact=tmp_path / "nope.json")
    assert "refusing" in str(e.value)


# ----------------------------------------------------------------- 9. ORB

def _orb_day(or_high: float, or_low: float, scan: list[tuple[float, float, float]]
              ) -> pd.DataFrame:
    """6 opening bars pinned to [or_low, or_high], then one bar per
    (high, low, close) tuple from 10:00 onward, padded flat to 15:55."""
    day = "2024-01-03"
    idx = pd.date_range(pd.Timestamp(f"{day} 09:30", tz=ET),
                        pd.Timestamp(f"{day} 15:55", tz=ET), freq="5min")
    rows = []
    mid = (or_high + or_low) / 2
    for i, ts in enumerate(idx):
        if i < 6:
            rows.append({"Open": mid, "High": or_high, "Low": or_low, "Close": mid})
        elif i - 6 < len(scan):
            h, lo, c = scan[i - 6]
            rows.append({"Open": mid, "High": h, "Low": lo, "Close": c})
        else:
            rows.append({"Open": mid, "High": mid, "Low": mid, "Close": mid})
    df = pd.DataFrame(rows, index=idx)
    df["Volume"] = 1_000
    return df


def test_orb_refuses_to_fabricate_a_fill_when_one_bar_breaks_both_sides():
    df = _orb_day(101.0, 99.0, [(105.0, 95.0, 100.0)])
    res = rex.orb_outcome(df)
    assert res["outcome"] == "ambiguous_both_sides_same_bar"
    assert res["R"] is None            # never a guessed intrabar order


def test_orb_no_breakout_is_zero_R_and_a_stop_is_minus_one_R():
    flat = rex.orb_outcome(_orb_day(101.0, 99.0, []))
    assert flat["outcome"] == "no_breakout" and flat["R"] == 0.0

    stopped = rex.orb_outcome(_orb_day(101.0, 99.0,
                                        [(101.5, 100.0, 101.2), (101.0, 98.5, 98.6)]))
    assert stopped["outcome"] == "stopped" and stopped["R"] == -1.0


def test_orb_payoff_is_expressed_in_opening_range_R():
    # width 2.0; long entry at 101.0; day closes at 104.0 -> +1.5R
    df = _orb_day(101.0, 99.0, [(101.5, 100.0, 104.0)])
    df.loc[df.index[-1], ["Open", "High", "Low", "Close"]] = [104.0, 104.0, 104.0, 104.0]
    res = rex.orb_outcome(df)
    assert res["outcome"] == "held_to_close"
    assert res["R"] == pytest.approx(1.5)


# ------------------------------------------------- 10. the economic floor

def test_a_significant_result_below_the_floor_is_reported_as_useless():
    v = rex._floor_verdict(1.05, 0.001)
    assert v["clears_economic_floor"] is False
    assert v["verdict"].startswith("SIGNIFICANT AND USELESS")
    assert "1.10 economic floor" in v["verdict"]


def test_the_floor_is_the_pre_registered_1_10_and_is_applied_to_the_expansion_statistic():
    assert rex.ECONOMIC_FLOOR == 1.10
    assert rex._floor_verdict(1.20, 0.001)["clears_economic_floor"] is True
    assert rex._floor_verdict(1.0999, 0.001)["clears_economic_floor"] is False
    # a non-significant result above the floor is not dressed up as a finding
    assert "NOT SIGNIFICANT" in rex._floor_verdict(1.5, 0.4)["verdict"]


# ------------------------------------------------------------------- 11. MDE

def test_mde_reuses_mechanisms_and_matches_the_two_sample_closed_form():
    ev = [1.0, 2.0, 3.0, 4.0, 5.0]
    ct = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    res = rex.two_sample_mde(ev, ct)
    n1, n2 = len(ev), len(ct)
    import statistics as st
    pooled = (((n1 - 1) * st.variance(ev) + (n2 - 1) * st.variance(ct))
              / (n1 + n2 - 2)) ** 0.5
    expected = (mechanisms.Z_ALPHA + mechanisms.Z_POWER) * pooled * math.sqrt(
        1 / n1 + 1 / n2)
    assert res["mde_expansion_units"] == pytest.approx(expected)
    assert res["z_alpha_one_sided_95"] == mechanisms.Z_ALPHA
    assert res["z_power_80"] == mechanisms.Z_POWER


def test_mde_is_undefined_rather_than_zero_when_an_arm_is_too_small():
    res = rex.two_sample_mde([1.0], [1.0, 2.0, 3.0])
    assert res["mde_expansion_units"] is None
    assert "undefined" in res["note"]
