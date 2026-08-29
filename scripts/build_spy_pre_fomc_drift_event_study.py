#!/usr/bin/env python3
"""PRE-FOMC ANNOUNCEMENT DRIFT — a REPLICATION, not a discovery.

WHY THIS TEST AND NOT ANOTHER SPLASH TEST
------------------------------------------
The splash programme is COMPLETE: `build_fomc_splash_event_study.py`
measured the 14:00 FOMC impulse as real and large (d=1.309, p=1.1e-07,
n=84 scheduled meetings) and `build_spy_splash_continuation_study.py`
showed it is directionless (seven independent nulls).
`build_spy_bracket_harvest_study.py` then tried to harvest the magnitude
with a both-sides bracket and found the toll exceeds the prize (net
-3.80bp/event). So stop trading the impulse.

This tests a DIFFERENT phenomenon: DRIFT INTO the announcement, not
reaction to it. It is directional by construction (a stated prior
direction, UP), requires no latency (position days early, flat well before
14:00), and never pays the announcement-window spread this repo's other
FOMC studies already showed does not clear its own cost.

THIS IS A REPLICATION, NOT A DISCOVERY
-----------------------------------------
Lucca & Moench, "The Pre-FOMC Announcement Drift" (Journal of Finance,
2015): US equities have historically drifted UP in the ~24 hours before
scheduled FOMC announcements. The direction tested here (UP) was FIXED by
that prior published literature, not chosen after looking at this repo's
own data — a failed replication is exactly as reportable as a successful
one, and is stated as such if that is what this sample shows.

THE PRE-REGISTERED PRIMARY — ONE TEST, FIXED BEFORE COMPUTING
-------------------------------------------------------------------
SPY SIGNED return from the RTH close on the trading day BEFORE a scheduled
FOMC announcement to 13:59 ET on announcement day, versus matched
non-FOMC windows of identical length and weekday.

  - SIGNED, not absolute — this is the directional test the impulse
    studies deliberately were not.
  - SCHEDULED meetings only. `fomc_calendar.json`'s `unscheduled: true`
    entries (the two March 2020 COVID meetings) are excluded by
    construction — a surprise meeting has no anticipation window, so
    mixing it in would answer neither question cleanly (same reasoning
    `build_fomc_splash_event_study.py`'s docstring already states for its
    own PRIMARY).
  - Exit at 13:59 ET, strictly before the 14:00 impulse: the Close of the
    13:55-14:00 5-minute bar — the last price the tape prints before the
    statement drops, never the 14:00 bar itself. Entry reference: the
    Close of the PRIOR trading day's 15:55-16:00 5-minute bar — this
    repo's own `bars.RTH_CLOSE = "15:55"` convention for "the session
    close", reused rather than redefined.
  - Matched controls: same weekday as the announcement day, same window
    shape (prior-day close to 13:55-bar close), excluding every day that
    is EITHER itself a tracked scheduled-release day (FOMC of any kind, or
    the six FRED release types `build_macro_event_study.py` tracks) OR
    whose OWN entry day (the prior trading day) is one — a control window
    that itself opens on the tail of a different event's reaction would
    silently blend two phenomena, exactly the contamination
    `build_fomc_splash_event_study.py`'s control pool already guards
    against for its own (single-day) window.

This is fixed before any number is computed. Everything else is
EXPLORATORY, labelled, BH-corrected within its own family.

EXPLORATORY, LABELLED
------------------------
  - Horizon sweep: T-24h (the primary, prior-day close), T-48h, T-72h
    (2 and 3 trading days back respectively) — where does drift begin?
    Reported as shape, not as a competing primary. "T-24h" etc. is an
    approximate label (a trading day is ~21.5-24.5h depending on weekday
    gaps, not exactly 24h) — stated, not hidden.
  - Net of costs, using this repo's existing cost-model CONVENTION
    (`build_spy_bracket_harvest_study.py`'s `SPREAD_BP = 1.0`) but NOT its
    stop-through assumption: both legs here are SCHEDULED market orders
    (enter at a known close, exit at a known close), never stop orders, so
    neither leg pays `STOP_THROUGH_BP` — only `SPREAD_BP` each way. This
    is stated explicitly because it is a real, deliberate difference from
    the bracket study's 4.0bp round trip, not an oversight.
  - Whether drift concentrates in meetings with a press conference
    (`press_conference: true` in `fomc_calendar.json`, a real tracked
    field) versus those without. A further SEP/dot-plot split was
    requested but `fomc_calendar.json` carries no SEP field, and
    fabricating one from outside memory is exactly the mistake this
    repo's calendars already document being burned by twice — that
    sub-split is SKIPPED, stated as skipped, not silently approximated.
  - Post-2016 vs pre-2016: `fomc_calendar.json`'s own `coverage` field is
    "2016-2026" — there are no pre-2016 FOMC dates in this repo's verified
    calendar to split against, even though the SPY bar cache reaches back
    to 2015-12-31. Reported as N/A with the reason, not silently skipped.

PERMUTATION NULL
-----------------
The per-day signed-return series is fixed (it depends only on that
calendar day's own two close prices, never on which days are LABELLED
FOMC-eve). Event-day labels are reshuffled at random, without replacement,
same event count, `N_PERMUTATIONS` times over the FULL population of days
with a complete T-24h window; the observed event-arm mean is reported
beside that null distribution with a one-sided p-value (fraction of
permutations >= observed) — the same discipline
`build_spy_bracket_harvest_study.py`'s `permutation_null` already uses.
n=84ish scheduled meetings is thin; stated, not hidden.

NO LOOK-AHEAD
-------------
Entry uses only the prior trading day's own close — information available
before the announcement day even opens. The FOMC date itself is knowable
in advance (that is the entire point of a pre-announcement drift test),
but nothing about the announcement's CONTENT or the 14:00-15:00 reaction
leaks into the 13:59 exit price.

FAIL LOUD
---------
A day missing its own 13:55 exit bar, or whose prior trading day is
missing its 15:55 close bar, or that has fewer than `lag` prior trading
days in coverage at all, is EXCLUDED with a reason, never interpolated.
Missing data excludes the observation and is counted, never guessed.

REUSE
-----
Bar loading (`build_fomc_splash_event_study._load_raw_bars`), the FOMC
calendar loader and its fabrication guard
(`build_fomc_splash_event_study.load_fomc_events`), the Welch/CI machinery
(`build_fomc_splash_event_study._welch`, which itself reuses
`build_macro_event_study._ci95`), BH correction
(`build_macro_event_study._bh_adjust`), and the six-release FRED calendar
(`build_macro_event_study.build_event_calendar`, `EventStudyError`) are
all imported and used as-is — none of that machinery is reimplemented
here.

MEASUREMENT / SIMULATION ONLY
------------------------------
No order is placed, nothing is wired to a broker, no live signal is built.
`data/proof/` and `SEALS.json` are never touched by this module.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))
sys.path.insert(0, str(ROOT / "scripts"))
import build_macro_event_study as mes                                # noqa: E402
from build_macro_event_study import EventStudyError                   # noqa: E402
import build_fomc_splash_event_study as fomc_mod                      # noqa: E402
from bars import BarDataError                                         # noqa: E402

OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
SUMMARY_JSON = OUT_DIR / "spy_pre_fomc_drift_event_study_summary.json"
PRIMARY_DAYS_CSV = OUT_DIR / "spy_pre_fomc_drift_primary_days.csv"

PRIMARY_SYMBOL = "SPY"

# Fixed, pre-registered. See module docstring -- never swept.
ENTRY_CLOSE_BAR = "15:55"   # prior trading day's session-close 5m bar
EXIT_BAR = "13:55"          # last 5m bar strictly before the 14:00 statement
MIN_CONTROL_N = 3

LAG_LABELS = {1: "T-24h (PRIMARY)", 2: "T-48h", 3: "T-72h"}
PRIMARY_LAG = 1

# Cost model, bp. SCHEDULED market orders both legs -- see module docstring
# for why this deliberately does NOT carry the bracket study's
# STOP_THROUGH_BP.
SPREAD_BP = 1.0
ROUND_TRIP_COST_BP = 2.0 * SPREAD_BP

N_PERMUTATIONS = 2000
PERMUTATION_SEED = 20260828


# ------------------------------------------------------------ day chunking

def build_day_chunks(raw: pd.DataFrame) -> tuple[list[date], dict[date, pd.DataFrame]]:
    """Sorted trading-day list and a date -> that day's bars map, built once
    and shared across every lag so a day's identity as "the Nth prior
    trading day" is INDEX-based (the actual prior session present in the
    cache), never calendar-date subtraction -- the same discipline
    `build_macro_event_study.py`'s horizon lookups already use."""
    chunks = {d: c for d, c in raw.groupby(raw.index.date)}
    return sorted(chunks), chunks


def _bar_close(chunk: pd.DataFrame, hhmm: str) -> float | None:
    t = chunk.index.strftime("%H:%M")
    bar = chunk[t == hhmm]
    if len(bar) != 1:
        return None
    return float(bar["Close"].iloc[0])


# -------------------------------------------------------------- day tables

def build_drift_days(day_list: list[date], day_chunks: dict[date, pd.DataFrame],
                      lag: int) -> tuple[pd.DataFrame, list[dict]]:
    """One row per announcement-candidate day `d` with a complete T-`lag`
    window: entry = Close of `ENTRY_CLOSE_BAR` on the `lag`-th prior trading
    day, exit = Close of `EXIT_BAR` on `d` itself. A day failing any leg is
    EXCLUDED with a reason, never interpolated."""
    rows: list[dict] = []
    excluded: list[dict] = []
    for i, d in enumerate(day_list):
        if i - lag < 0:
            excluded.append({"date": d.isoformat(),
                              "reason": f"fewer than {lag} prior trading day(s) in coverage"})
            continue
        entry_day = day_list[i - lag]
        entry_close = _bar_close(day_chunks[entry_day], ENTRY_CLOSE_BAR)
        if entry_close is None:
            excluded.append({"date": d.isoformat(),
                              "reason": f"missing {ENTRY_CLOSE_BAR} close bar on entry day "
                                        f"{entry_day.isoformat()}"})
            continue
        exit_close = _bar_close(day_chunks[d], EXIT_BAR)
        if exit_close is None:
            excluded.append({"date": d.isoformat(), "reason": f"missing {EXIT_BAR} exit bar"})
            continue
        ret = exit_close / entry_close - 1.0
        rows.append({
            "date": d.isoformat(),
            "weekday": pd.Timestamp(d).strftime("%A"),
            "entry_date": entry_day.isoformat(),
            "entry_close": entry_close,
            "exit_close": exit_close,
            "ret": ret,
            "abs_ret": abs(ret),
        })
    columns = ["date", "weekday", "entry_date", "entry_close", "exit_close", "ret", "abs_ret"]
    out = pd.DataFrame(rows, columns=columns)
    if not out.empty:
        out = out.sort_values("date").reset_index(drop=True)
    return out, excluded


# ------------------------------------------------------------ control pool

def control_pool(days: pd.DataFrame, all_event_dates: set[str]) -> pd.DataFrame:
    """Days that are not themselves a tracked scheduled-event day AND whose
    own entry day is not one either -- a control window opening on the tail
    of a different event's reaction would blend two phenomena. See module
    docstring."""
    clean_date = ~days["date"].isin(all_event_dates)
    clean_entry = ~days["entry_date"].isin(all_event_dates)
    return days[clean_date & clean_entry].reset_index(drop=True)


def _matched_control(days: pd.DataFrame, all_event_dates: set[str],
                      weekdays: set[str]) -> pd.DataFrame:
    pool = control_pool(days, all_event_dates)
    return pool[pool["weekday"].isin(weekdays)]


# ----------------------------------------------------------------- primary

def primary_test(days: pd.DataFrame, event_dates: set[str],
                  all_event_dates: set[str]) -> dict:
    """SPY SIGNED return, prior-close to 13:59 ET, SCHEDULED FOMC
    announcement days vs weekday-matched clean control days. ONE test,
    one-sided in the pre-registered direction (event > control, i.e. UP),
    no multiple-testing correction -- it is the only member of its
    family."""
    ev = days[days["date"].isin(event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = _matched_control(days, all_event_dates, weekdays)

    rec = fomc_mod._welch(ev["ret"].tolist(), ctrl["ret"].tolist())
    rec["hypothesis"] = ("SPY signed return from the prior trading day's close to 13:59 ET "
                          "is larger (more positive) ahead of SCHEDULED FOMC announcements "
                          "than on matched clean non-event windows -- direction (UP) fixed "
                          "by Lucca & Moench prior to this replication")
    rec["instrument"] = PRIMARY_SYMBOL
    rec["window"] = f"prior day {ENTRY_CLOSE_BAR} close -> announcement day {EXIT_BAR} close"
    rec["metric"] = "signed_ret"
    rec["event_n_days_with_complete_window"] = len(ev)
    rec["control_weekdays_matched_against"] = sorted(weekdays)
    if rec["welch_t"] is not None:
        p_two = rec["welch_p_two_sided"]
        rec["welch_p_one_sided_event_gt_control"] = (
            p_two / 2 if rec["welch_t"] > 0 else 1 - p_two / 2)
    else:
        rec["welch_p_one_sided_event_gt_control"] = None
    if rec["event_mean"] is not None:
        rec["net_of_cost_event_mean"] = rec["event_mean"] - ROUND_TRIP_COST_BP * 1e-4
        rec["cost_model_bp"] = {"spread_bp_per_leg": SPREAD_BP,
                                 "round_trip_cost_bp": ROUND_TRIP_COST_BP,
                                 "note": "both legs are scheduled market orders, never stop "
                                         "orders -- no stop-through cost, unlike the bracket "
                                         "harvest study"}
    else:
        rec["net_of_cost_event_mean"] = None
    return rec


# ---------------------------------------------------------- secondary/exploratory

def horizon_sweep(day_list: list[date], day_chunks: dict[date, pd.DataFrame],
                   scheduled_dates: set[str], all_event_dates: set[str]) -> list[dict]:
    """T-24h/T-48h/T-72h, EXPLORATORY (T-24h duplicates the primary lag but
    is reported here too so the shape reads as one table; only T-48h/T-72h
    carry inferential weight for the "does the drift begin earlier" claim
    -- T-24h's number here must equal the primary's own, by construction of
    sharing the same day-table builder)."""
    out = []
    for lag, label in LAG_LABELS.items():
        days, excluded = build_drift_days(day_list, day_chunks, lag)
        ev = days[days["date"].isin(scheduled_dates)]
        weekdays = set(ev["weekday"])
        ctrl = _matched_control(days, all_event_dates, weekdays)
        rec = fomc_mod._welch(ev["ret"].tolist(), ctrl["ret"].tolist())
        rec.update({"family": "horizon_sweep", "lag_trading_days": lag, "horizon_label": label,
                    "excluded_days_count": len(excluded)})
        out.append(rec)
    return out


def press_conference_split(days: pd.DataFrame, scheduled_events: list[dict],
                            all_event_dates: set[str]) -> list[dict]:
    """Drift split by whether the meeting had a press conference -- a real
    tracked field. A further SEP/dot-plot split was requested but
    `fomc_calendar.json` has no such field; fabricating one is refused, see
    module docstring."""
    out = []
    for label, want in (("press_conference", True), ("no_press_conference", False)):
        dates = {e["date"] for e in scheduled_events if bool(e.get("press_conference")) is want}
        ev = days[days["date"].isin(dates)]
        weekdays = set(ev["weekday"])
        ctrl = _matched_control(days, all_event_dates, weekdays)
        rec = fomc_mod._welch(ev["ret"].tolist(), ctrl["ret"].tolist())
        rec.update({"family": "press_conference_split", "subset": label,
                    "meetings_in_subset": len(ev)})
        out.append(rec)
    return out


def permutation_null(days: pd.DataFrame, n_event: int, *, n_perm: int = N_PERMUTATIONS,
                      seed: int = PERMUTATION_SEED) -> dict:
    """Reshuffle which `n_event` days (of the FULL T-24h day population) are
    labelled 'event', without replacement, `n_perm` times -- each day's own
    signed return never changes, only the labelling does."""
    pool = days["ret"].tolist()
    if n_event <= 0 or n_event > len(pool):
        return {"n_perm": 0, "means": []}
    rng = random.Random(seed)
    means = [sum(rng.sample(pool, n_event)) / n_event for _ in range(n_perm)]
    return {"n_perm": n_perm, "means": means}


def _finish_permutation(perm: dict, observed_mean: float | None) -> dict:
    means = perm.get("means") or []
    if not means or observed_mean is None:
        return {"n_perm": perm.get("n_perm", 0), "null_mean": None, "null_sd": None,
                "observed_mean": observed_mean,
                "p_value_one_sided_event_gt_random": None}
    n_perm = len(means)
    null_mean = sum(means) / n_perm
    var = sum((m - null_mean) ** 2 for m in means) / (n_perm - 1) if n_perm > 1 else 0.0
    null_sd = var ** 0.5
    p_val = sum(1 for m in means if m >= observed_mean) / n_perm
    return {"n_perm": n_perm, "null_mean": null_mean, "null_sd": null_sd,
            "observed_mean": observed_mean, "p_value_one_sided_event_gt_random": p_val}


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    a = ap.parse_args(argv)

    load_dotenv()
    import os
    key = os.environ.get("FRED_API_KEY")
    if not key:
        print("  REFUSED: FRED_API_KEY missing -- cannot build the FRED "
              "exclusion set for the control pool")
        return 1

    try:
        fomc_events = fomc_mod.load_fomc_events()
    except EventStudyError as e:
        print(f"  {e}")
        return 1

    try:
        raw = fomc_mod._load_raw_bars(PRIMARY_SYMBOL)
    except BarDataError as e:
        print(f"  REFUSED: {e}")
        return 1
    if raw.empty:
        print("  REFUSED: no bars in the cache")
        return 1

    day_list, day_chunks = build_day_chunks(raw)
    start = day_list[0]
    end_d = day_list[-1]
    try:
        fred_events = mes.build_event_calendar(key, start=start, end=end_d)
    except EventStudyError as e:
        print(f"  REFUSED: {e}")
        return 1
    fred_event_dates = {e["date"] for e in fred_events}

    scheduled_events = [e for e in fomc_events if not e.get("unscheduled")]
    unscheduled_events = [e for e in fomc_events if e.get("unscheduled")]
    all_fomc_dates = {e["date"] for e in fomc_events}
    all_event_dates = all_fomc_dates | fred_event_dates
    scheduled_dates = {e["date"] for e in scheduled_events}

    primary_days, primary_excluded = build_drift_days(day_list, day_chunks, PRIMARY_LAG)
    event_dates_present = scheduled_dates & set(primary_days["date"])
    dropped_scheduled = sorted(scheduled_dates - event_dates_present)

    primary = primary_test(primary_days, event_dates_present, all_event_dates)
    primary["scheduled_meetings_with_complete_window"] = len(event_dates_present)
    primary["scheduled_meetings_dropped_incomplete_window"] = dropped_scheduled

    perm = permutation_null(primary_days, len(event_dates_present))
    perm = _finish_permutation(perm, primary.get("event_mean"))

    horizon = horizon_sweep(day_list, day_chunks, scheduled_dates, all_event_dates)
    presser = press_conference_split(primary_days, scheduled_events, all_event_dates)

    exploratory_tests = horizon[1:] + presser  # T-24h excluded from BH family: it IS the primary
    padj = mes._bh_adjust([t["welch_p_two_sided"] for t in exploratory_tests])
    for t, p in zip(exploratory_tests, padj):
        t["welch_p_bh"] = p

    # Natural-experiment context, descriptive only (mirrors
    # build_fomc_splash_event_study.py's own unscheduled section) -- not a
    # driver of this study's own primary/exploratory results.
    unsched_note = (f"{len(unscheduled_events)} unscheduled FOMC meeting(s) in the calendar; "
                     "excluded from every population above by construction -- a surprise "
                     "meeting has no anticipation window to measure drift over.")

    pre_2016_note = ("N/A -- fomc_calendar.json coverage is 2016-2026; no pre-2016 scheduled "
                      "FOMC dates exist in this repo's verified calendar to split against, "
                      "even though the SPY bar cache itself reaches back to 2015-12-31.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    primary_days.to_csv(PRIMARY_DAYS_CSV, index=False)

    replicates = (primary.get("welch_p_one_sided_event_gt_control") is not None
                  and primary.get("welch_p_one_sided_event_gt_control") < 0.05
                  and (primary.get("event_mean") or 0) > 0)
    headline = ("REPLICATES: pre-FOMC drift is UP and significant in this sample"
                if replicates else
                "DOES NOT REPLICATE: no significant positive drift found in this sample")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "headline": headline,
        "primary_symbol": PRIMARY_SYMBOL,
        "replication_of": "Lucca & Moench (2015), 'The Pre-FOMC Announcement Drift', "
                           "Journal of Finance -- direction (UP) fixed by that prior "
                           "literature, not chosen after seeing this sample",
        "bar_source": "data/daytrade/bars_premarket (Alpaca SIP, RTH bars read raw)",
        "fomc_calendar_total_meetings": len(fomc_events),
        "fomc_calendar_scheduled_meetings": len(scheduled_events),
        "fomc_calendar_unscheduled_meetings": len(unscheduled_events),
        "unscheduled_meetings_note": unsched_note,
        "primary_excluded_days_count": len(primary_excluded),
        "primary_excluded_reasons_sample": primary_excluded[:5],
        "PRIMARY": primary,
        "PERMUTATION_NULL": perm,
        "SECONDARY_EXPLORATORY_NO_INFERENTIAL_WEIGHT": {
            "horizon_sweep": horizon,
            "press_conference_split": presser,
            "sep_dot_plot_split": "SKIPPED -- fomc_calendar.json has no SEP field; "
                                   "fabricating one is refused, see module docstring",
            "post_2016_vs_pre_2016": pre_2016_note,
            "multiple_testing": {
                "tests_run": sum(1 for t in exploratory_tests
                                  if t["welch_p_two_sided"] is not None),
                "significant_after_bh_correction": sum(
                    1 for t in exploratory_tests
                    if t.get("welch_p_bh") is not None and t["welch_p_bh"] < 0.05),
            },
        },
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=1, default=str))

    print("=" * 72)
    p_one = primary.get("welch_p_one_sided_event_gt_control")
    print(f"  FIRST LINE: {headline}")
    print(f"  PRIMARY: mean_signed_ret={primary.get('event_mean')}, "
          f"ci95={primary.get('event_ci95')}, p(one-sided)={p_one}, "
          f"event_n={primary.get('event_n')}, control_n={primary.get('control_n')}, "
          f"net_of_cost_mean={primary.get('net_of_cost_event_mean')}")
    print(f"  PERMUTATION NULL: p={perm.get('p_value_one_sided_event_gt_random')}, "
          f"null_mean={perm.get('null_mean')}, n_perm={perm.get('n_perm')}")
    print(f"  wrote {SUMMARY_JSON}")
    print(f"  wrote {PRIMARY_DAYS_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
