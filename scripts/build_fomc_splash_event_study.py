#!/usr/bin/env python3
"""FOMC DOUBLE-SPLASH EVENT STUDY — the splash this repo has never measured.

WHY THIS EXISTS
----------------
`scripts/build_spy_premarket_event_study.py` (the 08:30 pre-market study)
deliberately excludes FOMC from every comparison: FRED's own "FOMC Press
Release" release (id 101) returns a release date for EVERY calendar day once
`include_release_dates_with_no_data=true` is set (verified live, 30
consecutive daily rows) — not a real schedule, and this repo has already
spent two days removing a fabricated CPI line and a fabricated central-bank
decision file, so that source was never used. The gap that leaves: the
single heaviest scheduled anchor in the market has never been measured here.

`data/daytrade/fomc_calendar.json` closes that gap — a static, hand-verified
calendar built from the Federal Reserve's own published calendar pages
(federalreserve.gov/monetarypolicy/fomccalendars.htm and its historical
archive, fomchistorical2016..2020.htm), spanning 2016-2026 to match this
repo's SPY/QQQ bar coverage (`data/daytrade/bars_premarket`, 2016-01-01
forward). Every date traces to a cited fetch; a year that could not be
verified from a primary source was left out entirely rather than guessed —
see that file's own `_comment`/`source_url`/`source_urls_historical` fields.

THE PRE-REGISTERED PRIMARY — SCHEDULED MEETINGS ONLY
-------------------------------------------------------
FOMC is not one event, it is two, and the two are structurally different:

    14:00 ET -- statement + dot plot. Mechanical repricing, should look
    like CPI: instantaneous and violent.
    14:30 ET -- the press conference. Narrative, should look messier and
    potentially directional in a way the statement is not.

The single pre-registered PRIMARY test, fixed before any number was
computed:

    SPY |return| over 14:00-14:05 ET on SCHEDULED FOMC decision days vs
    matched non-event days (same window, same weekday, excluding every
    tracked scheduled-release day of any kind).

"Scheduled" is load-bearing and is why this study restructures around a
methodological point raised after the first draft: an unscheduled emergency
meeting (2020-03-03, 2020-03-15 — the COVID actions, each individually
flagged `unscheduled: true` with its own `source_url` in
`fomc_calendar.json`) is, by construction, a splash nobody saw coming. The
entire "splash in a pool" thesis (`daytrade/macro_calendar.py`'s module
docstring) rests on foreknowledge turning an otherwise-impossible inference
into a tractable one — you can only "read the ripple" because you knew when
the rock would land. A surprise meeting violates that premise outright, so
mixing it into the PRIMARY population would silently blend two different
phenomena and answer neither question cleanly. The PRIMARY therefore runs
on SCHEDULED meetings only; the excluded unscheduled dates are counted and
reported, not silently dropped.

ONE instrument (SPY), ONE window (14:00-14:05), ONE metric (abs_ret), ONE
test (Welch, one-sided: event > control). No multiple-testing penalty — it
is the only member of its family.

Everything else — the 14:30-14:35 press-conference window (restricted to
meetings that actually had a presser), signed (directional) return in both
windows, and the scheduled-vs-unscheduled natural experiment below — is
SECONDARY/EXPLORATORY, labelled as such, BH-corrected within its own
family, and carries no inferential weight standing alone.

THE DOUBLE SPLASH, NOT BLURRED
---------------------------------
14:00-14:05 and 14:30-14:35 are measured and reported as SEPARATE windows,
never pooled into one wide 14:00-15:00 window — that blurring is exactly
the dilution error `build_spy_premarket_event_study.py`'s module docstring
already diagnosed in the NVDA study (390-minute horizon 0 hid a 5-minute
impulse). The 14:30 window is measured ONLY on meetings that historically
had a press conference (`press_conference: true` in fomc_calendar.json,
itself verified against each year's `fomcpresconf<date>.htm` page existing
on federalreserve.gov: quarterly-only 2016-2018, every meeting from 2019
onward) — a meeting with no presser has no 14:30 splash to measure, and
counting a non-event as a null observation would bias the comparison
toward "no effect" for the wrong reason.

THE SCHEDULED VS UNSCHEDULED NATURAL EXPERIMENT — EXPLORATORY, NOT POWERED
------------------------------------------------------------------------------
Same event type (an FOMC rate decision), same instrument, same mechanics —
the only difference between the two March 2020 dates and every other row is
whether the market could see it coming. If unscheduled |return| is larger,
that is the value of foreknowledge stated as a number, and it is a more
interesting finding than the primary. If it's the same size, the
anticipation channel does less work than the pool/ripple thesis assumes.

This is reported as a DESCRIPTIVE comparison only. `fomc_calendar.json`
carries exactly 2 `unscheduled: true` entries (2020-03-03, 2020-03-15). Of
those, only 2020-03-03 (a Tuesday, the statement released the day after the
unscheduled meeting) has a real 14:00 RTH bar to measure — 2020-03-15 was a
Sunday announcement (the Fed acted ahead of the Asian market open, not
during an RTH session), so no 5-minute SPY bar exists for that date at all
and it is correctly dropped by the same "missing bar, never interpolated"
rule every other window uses, not silently coerced to the nearest trading
day. So the descriptive comparison actually runs on n=1, smaller than even
the already-tiny n=2 this section's design anticipated. No Welch test, no
p-value, is run against it. Reporting a p-value there would be exactly the
kind of statistical theatre this repo's BH-correction discipline elsewhere
exists to prevent — this is one data point, stated as one data point.

BAR SOURCE
-----------
Reuses `data/daytrade/bars_premarket/SPY_5m.parquet` (Alpaca SIP, raw,
2016-01-01 forward) directly via the same `_load_raw_bars`-style read
`build_spy_premarket_event_study.py` uses — NOT through `bars.load_sessions`
(which strips outside 09:30-15:55; irrelevant here since 14:00/14:30 are
both inside RTH, but reading the raw parquet directly keeps this script
independent of that filter's behavior). This is RTH, unlike the 08:30 study,
so no pre-market fetch is required; the existing cache already covers the
14:00-14:35 window for the entire 2016-2026 span.

CONTROLS
---------
Non-FOMC, non-tracked-release days, same clock window, same weekday.
Matching by weekday of the day itself (not a triggering event's weekday,
since there is no forward horizon here — the comparison day IS the day).
`control_n` and `control_reliable` (>=MIN_CONTROL_N) are reported per test,
same discipline `build_spy_premarket_event_study.py` already holds itself
to for the weekly-Initial-Claims degeneracy — not applicable in the same
way here (FOMC's own weekday mix is broad), but the same reporting
discipline is kept rather than assumed clean.

NO LOOK-AHEAD
--------------
Both windows are measured strictly forward from their own start time
(14:00, 14:30) — nothing here reads a bar timestamped before the window it
labels.

FAIL LOUD
----------
A day missing its 14:00 bar is excluded from every stmt-window comparison,
with a reason, never interpolated. A day missing its 14:30 bar is excluded
from every presser-window comparison the same way — independently, since a
day can have one without the other. An FOMC calendar date that is not
itself a trading day present in the cache is dropped with a reason.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from scipy import stats as sp_stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))
sys.path.insert(0, str(ROOT / "scripts"))
import bars as bars_mod                                            # noqa: E402
from bars import BarDataError                                      # noqa: E402
import build_macro_event_study as mes                               # noqa: E402
from build_macro_event_study import EventStudyError                 # noqa: E402
import macro_calendar as mc                                         # noqa: E402

CACHE = ROOT / "data" / "daytrade" / "bars_premarket"
OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
SUMMARY_JSON = OUT_DIR / "spy_fomc_double_splash_event_study_summary.json"
DAYS_CSV = OUT_DIR / "spy_fomc_days.csv"

PRIMARY_SYMBOL = "SPY"

# Fixed publicly documented convention: statement/dot-plot at 14:00 ET,
# press conference (when one follows) at 14:30 ET. Each measured window is
# ONE 5-minute bar, covering [start, start+5m) — never blurred together.
STMT_BAR = "14:00"       # covers 14:00-14:05
PRESSER_BAR = "14:30"    # covers 14:30-14:35
MIN_CONTROL_N = 3


# --------------------------------------------------------------- bar loading

def _load_raw_bars(symbol: str) -> pd.DataFrame:
    """Every bar in the pre-market/RTH cache for `symbol`, ET-indexed, RAW.
    Same read pattern `build_spy_premarket_event_study.py::_load_raw_bars`
    uses — never routed through `bars.load_sessions`."""
    path = CACHE / f"{symbol}_5m.parquet"
    if not path.exists():
        raise BarDataError(f"no bar cache at {path}")
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(bars_mod.ET)
    return df


def _single_bar_metrics(chunk: pd.DataFrame, hhmm: str) -> dict | None:
    """The one 5m bar at `hhmm` on this day, or None if it's missing —
    never interpolated."""
    t = chunk.index.strftime("%H:%M")
    bar = chunk[t == hhmm]
    if len(bar) != 1:
        return None
    o = float(bar["Open"].iloc[0])
    c = float(bar["Close"].iloc[0])
    ret = c / o - 1.0
    return {"ret": ret, "abs_ret": abs(ret)}


def rth_day_stats(symbol: str) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """One row per calendar day with whatever of {stmt, presser} bars it
    actually has. `stmt_ret`/`stmt_abs_ret` are NaN (and the day recorded in
    `stmt_excluded`) if the 14:00 bar is missing; `presser_ret`/
    `presser_abs_ret` independently NaN (and recorded in `presser_excluded`)
    if the 14:30 bar is missing. A day can be usable for one window and not
    the other."""
    raw = _load_raw_bars(symbol)
    rows: list[dict] = []
    stmt_excluded: list[dict] = []
    presser_excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        stmt = _single_bar_metrics(chunk, STMT_BAR)
        presser = _single_bar_metrics(chunk, PRESSER_BAR)
        if stmt is None:
            stmt_excluded.append({"date": day.isoformat(),
                                   "reason": f"missing {STMT_BAR} 5m bar"})
        if presser is None:
            presser_excluded.append({"date": day.isoformat(),
                                      "reason": f"missing {PRESSER_BAR} 5m bar"})
        rows.append({
            "date": day.isoformat(),
            "weekday": pd.Timestamp(day).strftime("%A"),
            "stmt_ret": stmt["ret"] if stmt else float("nan"),
            "stmt_abs_ret": stmt["abs_ret"] if stmt else float("nan"),
            "presser_ret": presser["ret"] if presser else float("nan"),
            "presser_abs_ret": presser["abs_ret"] if presser else float("nan"),
        })
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, stmt_excluded, presser_excluded


# ------------------------------------------------------------ FOMC calendar

def load_fomc_events() -> list[dict]:
    """Every event in `data/daytrade/fomc_calendar.json`, validated against
    the fabrication guard before anything is computed from it — refuses
    rather than silently building a study on a corrupted calendar."""
    doc = json.loads(mc.FOMC_CALENDAR_JSON.read_text())
    violations = mc.validate_fomc_events(doc["events"])
    if violations:
        raise EventStudyError(
            "REFUSED: fomc_calendar.json fails its own fabrication guard: "
            + "; ".join(violations))
    return doc["events"]


# ------------------------------------------------------------ control pool

def control_pool(days: pd.DataFrame, event_dates: set[str]) -> pd.DataFrame:
    """Days that are not themselves a scheduled event day for ANY tracked
    source (FOMC of any kind, or the six FRED release types)."""
    return days[~days["date"].isin(event_dates)].reset_index(drop=True)


def _matched_control(days: pd.DataFrame, all_event_dates: set[str],
                      weekdays: set[str]) -> pd.DataFrame:
    pool = control_pool(days, all_event_dates)
    return pool[pool["weekday"].isin(weekdays)]


# --------------------------------------------------------------- statistics

def _cohens_d(a: list[float], b: list[float]) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    na, nb = len(a), len(b)
    va, vb = st.variance(a), st.variance(b)
    pooled = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if pooled == 0:
        return None
    return (st.mean(a) - st.mean(b)) / pooled


def _welch(event_vals: list[float], ctrl_vals: list[float]) -> dict:
    rec: dict = {
        "event_n": len(event_vals), "event_mean": st.mean(event_vals) if event_vals else None,
        "event_ci95": mes._ci95(event_vals),
        "control_n": len(ctrl_vals), "control_mean": st.mean(ctrl_vals) if ctrl_vals else None,
        "control_ci95": mes._ci95(ctrl_vals),
        "diff_event_minus_control": None, "welch_t": None, "welch_p_two_sided": None,
        "cohens_d": None, "control_reliable": len(ctrl_vals) >= MIN_CONTROL_N,
    }
    if rec["event_mean"] is not None and rec["control_mean"] is not None:
        rec["diff_event_minus_control"] = rec["event_mean"] - rec["control_mean"]
    if len(event_vals) >= 2 and len(ctrl_vals) >= 2:
        t_stat, p_val = sp_stats.ttest_ind(event_vals, ctrl_vals, equal_var=False)
        rec["welch_t"] = float(t_stat)
        rec["welch_p_two_sided"] = float(p_val)
        rec["cohens_d"] = _cohens_d(event_vals, ctrl_vals)
    return rec


# ----------------------------------------------------------------- primary

def primary_test(days: pd.DataFrame, scheduled_dates_in_coverage: list[str],
                  all_event_dates: set[str]) -> dict:
    """SPY abs_ret, 14:00-14:05, SCHEDULED FOMC decision days vs
    weekday-matched non-event days (any tracked source). ONE test,
    one-sided (event > control, pre-registered direction), no
    multiple-testing correction."""
    ev = days[days["date"].isin(scheduled_dates_in_coverage) & days["stmt_abs_ret"].notna()]
    weekdays = set(ev["weekday"])
    ctrl_pool = _matched_control(days, all_event_dates, weekdays)
    ctrl = ctrl_pool[ctrl_pool["stmt_abs_ret"].notna()]

    rec = _welch(ev["stmt_abs_ret"].tolist(), ctrl["stmt_abs_ret"].tolist())
    rec["hypothesis"] = ("SPY absolute return over 14:00-14:05 ET is larger on "
                          "SCHEDULED FOMC decision days than on matched non-event days")
    rec["instrument"] = PRIMARY_SYMBOL
    rec["window_et"] = "14:00-14:05"
    rec["metric"] = "abs_ret"
    rec["scheduled_meetings_in_bar_coverage"] = len(ev)
    rec["control_weekdays_matched_against"] = sorted(weekdays)
    if rec["welch_t"] is not None:
        p_two = rec["welch_p_two_sided"]
        rec["welch_p_one_sided_event_gt_control"] = (
            p_two / 2 if rec["welch_t"] > 0 else 1 - p_two / 2)
    else:
        rec["welch_p_one_sided_event_gt_control"] = None
    return rec


# ---------------------------------------------------------- secondary/exploratory

def secondary_tests(days: pd.DataFrame, fomc_events: list[dict],
                     fred_event_dates: set[str]) -> dict:
    """Everything NOT the primary. Labelled SECONDARY/EXPLORATORY, BH-
    corrected within this family, no standalone inferential weight."""
    scheduled = [e for e in fomc_events if not e.get("unscheduled")]
    unscheduled = [e for e in fomc_events if e.get("unscheduled")]
    scheduled_with_presser = [e for e in scheduled if e.get("press_conference")]

    all_fomc_dates = {e["date"] for e in fomc_events}
    all_event_dates = all_fomc_dates | fred_event_dates

    tests: list[dict] = []

    # --- presser window (14:30), scheduled meetings with a presser only --
    presser_dates = {e["date"] for e in scheduled_with_presser}
    ev_p = days[days["date"].isin(presser_dates) & days["presser_abs_ret"].notna()]
    weekdays_p = set(ev_p["weekday"])
    ctrl_pool_p = _matched_control(days, all_event_dates, weekdays_p)
    ctrl_p = ctrl_pool_p[ctrl_pool_p["presser_abs_ret"].notna()]
    rec = _welch(ev_p["presser_abs_ret"].tolist(), ctrl_p["presser_abs_ret"].tolist())
    rec.update({"family": "presser_window", "instrument": PRIMARY_SYMBOL,
                "window_et": "14:30-14:35", "metric": "abs_ret",
                "scheduled_meetings_with_presser_in_coverage": len(ev_p)})
    tests.append(rec)

    # --- signed return, both windows, scheduled only ----------------------
    sched_dates = {e["date"] for e in scheduled}
    for label, col in (("stmt_signed_return", "stmt_ret"),
                        ("presser_signed_return", "presser_ret")):
        target_dates = sched_dates if "stmt" in col else presser_dates
        ev_s = days[days["date"].isin(target_dates) & days[col].notna()]
        weekdays_s = set(ev_s["weekday"])
        ctrl_pool_s = _matched_control(days, all_event_dates, weekdays_s)
        ctrl_s = ctrl_pool_s[ctrl_pool_s[col].notna()]
        rec = _welch(ev_s[col].tolist(), ctrl_s[col].tolist())
        rec.update({"family": "signed_return_directionality", "instrument": PRIMARY_SYMBOL,
                    "window_et": "14:00-14:05" if "stmt" in col else "14:30-14:35",
                    "metric": label})
        tests.append(rec)

    padj = mes._bh_adjust([t["welch_p_two_sided"] for t in tests])
    n_tested = sum(1 for t in tests if t["welch_p_two_sided"] is not None)
    n_sig_raw = sum(1 for t in tests
                     if t["welch_p_two_sided"] is not None and t["welch_p_two_sided"] < 0.05)
    n_sig_bh = 0
    for t, p in zip(tests, padj):
        t["welch_p_bh"] = p
        if p is not None and p < 0.05:
            n_sig_bh += 1

    # --- scheduled vs unscheduled natural experiment -- DESCRIPTIVE ONLY --
    # n on the unscheduled side is tiny by construction (2 across the whole
    # calendar). No Welch test, no p-value -- reported as means/values only.
    unsched_dates = {e["date"] for e in unscheduled}
    unsched_rows = days[days["date"].isin(unsched_dates) & days["stmt_abs_ret"].notna()]
    sched_rows = days[days["date"].isin(sched_dates) & days["stmt_abs_ret"].notna()]
    natural_experiment = {
        "note": "DESCRIPTIVE ONLY -- no significance test run against n="
                f"{len(unsched_rows)} unscheduled observations. Same event "
                "type/instrument/mechanics; the only difference is whether "
                "the market could anticipate it.",
        "scheduled_n": len(sched_rows),
        "scheduled_stmt_abs_ret_mean": st.mean(sched_rows["stmt_abs_ret"]) if len(sched_rows) else None,
        "unscheduled_n": len(unsched_rows),
        "unscheduled_dates": sorted(unsched_rows["date"].tolist()),
        "unscheduled_stmt_abs_ret_values": sorted(unsched_rows["stmt_abs_ret"].tolist()),
        "unscheduled_stmt_abs_ret_mean": (
            st.mean(unsched_rows["stmt_abs_ret"]) if len(unsched_rows) else None),
    }
    if natural_experiment["scheduled_stmt_abs_ret_mean"] is not None \
            and natural_experiment["unscheduled_stmt_abs_ret_mean"] is not None:
        natural_experiment["unscheduled_minus_scheduled"] = (
            natural_experiment["unscheduled_stmt_abs_ret_mean"]
            - natural_experiment["scheduled_stmt_abs_ret_mean"])

    return {
        "tests": tests,
        "multiple_testing": {
            "tests_run": n_tested,
            "significant_raw_p_lt_0.05": n_sig_raw,
            "expected_by_chance_at_alpha_0.05": round(n_tested * 0.05, 1),
            "significant_after_bh_correction": n_sig_bh,
        },
        "SCHEDULED_VS_UNSCHEDULED_NATURAL_EXPERIMENT": natural_experiment,
    }


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    a = ap.parse_args(argv)

    load_dotenv()
    key = __import__("os").environ.get("FRED_API_KEY")
    if not key:
        print("  REFUSED: FRED_API_KEY missing — cannot build the FRED "
              "exclusion set for the control pool")
        return 1

    try:
        fomc_events = load_fomc_events()
    except EventStudyError as e:
        print(f"  {e}")
        return 1

    try:
        days, stmt_excluded, presser_excluded = rth_day_stats(PRIMARY_SYMBOL)
    except BarDataError as e:
        print(f"  REFUSED: {e}")
        return 1
    if days.empty:
        print("  REFUSED: no bars in the cache")
        return 1

    start = date.fromisoformat(days["date"].iloc[0])
    end_d = date.fromisoformat(days["date"].iloc[-1])
    try:
        fred_events = mes.build_event_calendar(key, start=start, end=end_d)
    except EventStudyError as e:
        print(f"  REFUSED: {e}")
        return 1
    fred_event_dates = {e["date"] for e in fred_events}

    all_fomc_dates = {e["date"] for e in fomc_events}
    day_dates = set(days["date"])
    fomc_dropped = sorted(all_fomc_dates - day_dates)

    scheduled_events = [e for e in fomc_events if not e.get("unscheduled")]
    unscheduled_events = [e for e in fomc_events if e.get("unscheduled")]
    scheduled_dates_in_coverage = sorted(
        {e["date"] for e in scheduled_events} & day_dates)

    all_event_dates = all_fomc_dates | fred_event_dates

    primary = primary_test(days, scheduled_dates_in_coverage, all_event_dates)
    secondary = secondary_tests(days, fomc_events, fred_event_dates)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    days.to_csv(DAYS_CSV, index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "primary_symbol": PRIMARY_SYMBOL,
        "bar_source": "data/daytrade/bars_premarket (Alpaca SIP, RTH bars read raw)",
        "fomc_calendar_total_meetings": len(fomc_events),
        "fomc_calendar_scheduled_meetings": len(scheduled_events),
        "fomc_calendar_unscheduled_meetings": len(unscheduled_events),
        "fomc_meetings_in_bar_coverage": len(all_fomc_dates & day_dates),
        "fomc_meetings_dropped_not_in_bar_coverage": fomc_dropped,
        "scheduled_meetings_in_bar_coverage": len(scheduled_dates_in_coverage),
        "stmt_window_excluded_days_count": len(stmt_excluded),
        "presser_window_excluded_days_count": len(presser_excluded),
        "PRIMARY": primary,
        "SECONDARY_EXPLORATORY_NO_INFERENTIAL_WEIGHT": secondary,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=1, default=str))

    print("=" * 72)
    p_one = primary.get("welch_p_one_sided_event_gt_control")
    null_flag = "NULL" if (p_one is None or p_one >= 0.05) else "SIGNIFICANT"
    print(f"  PRIMARY RESULT [{null_flag}] (scheduled FOMC meetings only): "
          f"diff={primary['diff_event_minus_control']}, "
          f"event_n={primary['event_n']}, control_n={primary['control_n']}, "
          f"p(one-sided)={p_one}")
    print(f"  scheduled meetings in bar coverage: {len(scheduled_dates_in_coverage)} "
          f"of {len(scheduled_events)} in the calendar")
    ne = secondary["SCHEDULED_VS_UNSCHEDULED_NATURAL_EXPERIMENT"]
    print(f"  scheduled vs unscheduled (descriptive, n={ne['unscheduled_n']}): "
          f"unscheduled_mean={ne['unscheduled_stmt_abs_ret_mean']}, "
          f"scheduled_mean={ne['scheduled_stmt_abs_ret_mean']}")
    print(f"  wrote {SUMMARY_JSON}")
    print(f"  wrote {DAYS_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
