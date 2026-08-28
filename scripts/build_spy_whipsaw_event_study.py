#!/usr/bin/env python3
"""SPY WHIPSAW (PATH) EVENT STUDY -- does the splash thrash or reprice clean.

WHY THIS EXISTS
----------------
Every splash measurement run so far in this repo
(`build_spy_premarket_event_study.py`, `build_spy_macro_decay_study.py`,
`build_fomc_splash_event_study.py`) uses `|return|` -- endpoint-to-endpoint.
That metric is BLIND to path. A market that rips up 40bp, reverses through
the open, and closes 2bp down registers as ~zero movement, identical to a
market that never moved at all. Those are completely different worlds to
trade.

This is also the question STOCKFISH (the exit engine -- it places and moves
stops, see repo root `CLAUDE.md`'s daytrade-cockpit boundary section) needs
answered. Max adverse excursion IS the stop-placement question. If a splash
thrashes far against you before resolving, a stop anywhere near entry gets
taken out even when the eventual direction is right. That is the difference
between "tradeable" and "stops you out before it works," and no `|return|`
number can tell you which.

DEFINITIONS -- per day, over a stated window, measured from the window's
own open
------------------------------------------------------------------------
    up_exc   = (max high in window - open) / open
    down_exc = (open - min low in window) / open
    range    = up_exc + down_exc            <- total travel, direction-agnostic
    endpoint = |close - open| / open        <- net movement
    ratio    = range / endpoint             <- the whipsaw ratio

Higher ratio on event days => the splash THRASHES: lots of travel per unit
of net move, and a tight stop dies. Lower => it REPRICES CLEANLY: a
straight-line move where a stop survives. Indistinguishable from control
=> the splash changes magnitude but not path character.

THE PRE-REGISTERED PRIMARY -- ONE TEST, NO CORRECTION
---------------------------------------------------------
    PRIMARY: the whipsaw ratio (range / endpoint), SPY, 08:30-08:45 ET,
    on scheduled-release days (the same six FRED-tracked release types
    `build_macro_event_study.py` / `build_spy_premarket_event_study.py`
    use) vs matched non-event days (same weekday, excluding any day that
    is a scheduled release for ANY of the six types).

    ONE instrument (SPY), ONE window (08:30-08:45 ET), ONE metric (the
    ratio), ONE test (Welch, two-sided -- direction is NOT pre-registered;
    a thrashier OR a cleaner event-day path are both live hypotheses, so
    calling this one-sided the way the |return| studies do would be
    unjustified). No multiple-testing penalty -- it is the only member of
    its family.

    `up_exc` and `down_exc` are reported SEPARATELY, event vs control, so a
    reader can see whether event days travel further in both directions or
    just one -- the ratio alone cannot show that.

THE DIVISION-BLOWUP PROBLEM, HANDLED NOT HIDDEN
----------------------------------------------------
`endpoint` near zero makes the ratio explode -- a day that travelled 20bp
but closed back within a tick of the open has a ratio near infinity despite
being an unremarkable amount of total travel. Pre-stated handling, decided
BEFORE any ratio was computed:

  1. FLOOR. `ENDPOINT_FLOOR = 0.0001` (1bp of the window's own 08:30 open --
     for SPY in this sample's price range, roughly a handful of ticks, the
     scale below which `endpoint` is closer to tick-rounding noise than a
     meaningful "net move"). The ratio used in the PRIMARY Welch test is
     computed against `max(endpoint, ENDPOINT_FLOOR)`, never the raw
     (possibly near-zero) endpoint. The count of days where the RAW endpoint
     fell below this floor is reported per group (event, control) --
     `endpoint_floor_hits` -- so a reader can see exactly how many days the
     floor touched, not just trust that it did something reasonable.
  2. MEDIAN AS A CROSS-CHECK. Because even a floored mean can still be
     dragged by the extreme tail of the ratio's distribution (it is a
     ratio of two positive quantities, right-skewed by construction), the
     MEDIAN ratio (computed on the RAW, unfloored ratio -- a robust
     statistic does not need the floor's help) is reported alongside the
     Welch mean-based result for both groups. If the floored-mean test and
     the raw-median comparison disagree in sign, that disagreement is the
     more honest headline than either number alone.

1-MINUTE BARS, THE RESOLUTION LIMIT
----------------------------------------
Excursion is measured from `data/daytrade/bars_premarket_1m/SPY_1m.parquet`
(1-minute bars) -- a 5-minute bar's high/low understates true excursion
within it, so 1-minute resolution is used everywhere in this module, never
the 5-minute premarket cache the `|return|`-only studies use. Even so, a
1-minute bar's own high/low still understates true tick-level excursion
within that minute -- every excursion number in this module's output is
therefore a LOWER BOUND on true intraday travel, not an exact figure.

EXPLORATORY A -- IMPULSE VS TAIL
-------------------------------------
Splits the primary window in two, each independently open-anchored, each
run through its OWN event-vs-control test:
  IMPULSE window: 08:30-08:35 (5 x 1m bars) -- opens at 08:30.
  TAIL window:    08:35-08:45 (10 x 1m bars) -- opens at 08:35, its OWN
                  open, not the 08:30 print -- this isolates whether
                  thrashing is a first-minute phenomenon that settles, or
                  continues once the initial impulse has already landed.
BH-corrected within this family of 2. Hypothesis-generating only.

EXPLORATORY B -- FOMC ARM
------------------------------
Same ratio, same window construction (14:00-14:15 ET, 15 x 1m bars,
anchored at the 14:00 statement release), on SCHEDULED FOMC decision days
only (reusing `data/daytrade/fomc_calendar.json` /
`build_fomc_splash_event_study.py::load_fomc_events`, the same "scheduled
only in the primary population" discipline that module's docstring already
argues for). n is far thinner here (~84-87 scheduled meetings across
2016-2026) than the ~1000+ six-release days the primary window draws on --
stated explicitly in the output, not left for a reader to infer from n
alone. One test, its own single-member family (BH is still run for
interface consistency with the other exploratory families; with one member
it leaves the p-value unchanged).

EXPLORATORY C -- ASYMMETRY
-------------------------------
Is `up_exc` vs `down_exc` balanced on event days (primary window), or does
the market travel further one way before settling back? Paired (one-sample
against zero) test on `up_exc - down_exc`, run independently for event days
and for control days so a reader can see the base rate the event-day number
sits against. No direction is pre-registered here either -- this would be
the first hint of anything DIRECTIONAL in this whole programme, so it is
reported carefully, two-sided, and explicitly flagged as not to be
overclaimed on. BH-corrected within this family of 2 (event test, control
test).

REUSE
-----
Bar loading (`_load_raw_bars_1m`, `CACHE_1M`, the redirected-fetch pattern)
is imported directly from `build_spy_macro_decay_study.py` -- not
reimplemented. Window slicing (`_window_bars`, string-HH:MM filtering, is
resolution-agnostic and already used at both 5m and 1m granularity
elsewhere in this repo) and the Welch/`_matched_control`/`control_pool`
machinery are imported directly from `build_spy_premarket_event_study.py`.
The FRED six-release event calendar and hand-rolled BH correction are
imported from `build_macro_event_study`. The FOMC scheduled calendar loader
is imported from `build_fomc_splash_event_study`. None of those four
modules is edited by this one.

FAIL LOUD
---------
A day missing even one of a window's expected 1-minute buckets is EXCLUDED
with a reason, never interpolated -- same discipline every other
bar-loading path in this repo already holds itself to.

NO LOOK-AHEAD
-------------
Every window here is measured strictly forward from its own stated open;
nothing reads a bar timestamped before a window's own opening tick to
decide anything about that window. The TAIL window in Exploratory A opens
at 08:35, not 08:30, for exactly this reason -- it does not borrow the
impulse window's open.

MEASUREMENT ONLY
-----------------
No model is fit, no trading threshold is picked, no signal is built here.
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
from bars import BarDataError                                        # noqa: E402
import build_macro_event_study as mes                                 # noqa: E402
from build_macro_event_study import EventStudyError                   # noqa: E402
import build_spy_macro_decay_study as dstudy                          # noqa: E402
import build_spy_premarket_event_study as bspes                       # noqa: E402
import build_fomc_splash_event_study as fstudy                        # noqa: E402

OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
SUMMARY_JSON = OUT_DIR / "spy_whipsaw_event_study_summary.json"
PRIMARY_DAYS_CSV = OUT_DIR / "spy_whipsaw_primary_days.csv"

PRIMARY_SYMBOL = "SPY"

# Pre-stated floor for the division-blowup problem -- see module docstring.
ENDPOINT_FLOOR = 0.0001    # 1bp of the window's own open

MIN_CONTROL_N = 3


def _minute_range(start_hhmm: str, end_hhmm: str) -> list[str]:
    """Inclusive HH:MM 1-minute bar-start labels from `start_hhmm` through
    `end_hhmm`."""
    start = pd.Timestamp(f"2000-01-01 {start_hhmm}")
    end = pd.Timestamp(f"2000-01-01 {end_hhmm}")
    return [ts.strftime("%H:%M") for ts in pd.date_range(start, end, freq="1min")]


# Primary window: 08:30-08:45 ET, 15 x 1m bars (08:30..08:44 bar-starts).
PRIMARY_MINUTES = _minute_range("08:30", "08:44")
PRIMARY_WINDOW_LABEL = "08:30-08:45"

# Exploratory A: impulse (own open at 08:30) vs tail (own open at 08:35).
IMPULSE_MINUTES = _minute_range("08:30", "08:34")
IMPULSE_WINDOW_LABEL = "08:30-08:35"
TAIL_MINUTES = _minute_range("08:35", "08:44")
TAIL_WINDOW_LABEL = "08:35-08:45"

# Exploratory B: FOMC statement window, own open at 14:00, same 15-bar span
# as the primary.
FOMC_MINUTES = _minute_range("14:00", "14:14")
FOMC_WINDOW_LABEL = "14:00-14:15"


# --------------------------------------------------------------- bar loading

def _load_raw_bars_1m(symbol: str) -> pd.DataFrame:
    """Delegates to `build_spy_macro_decay_study._load_raw_bars_1m` -- same
    cache (`data/daytrade/bars_premarket_1m/`), never reimplemented here."""
    return dstudy._load_raw_bars_1m(symbol)


# ------------------------------------------------------------ excursion math

def _window_excursion_metrics(window: pd.DataFrame) -> dict:
    """`up_exc`, `down_exc`, `range`, `endpoint` for one already-sliced,
    already-complete window. `window` must be sorted by index -- callers
    are responsible for that (bar loads in this repo are stored sorted)."""
    o = float(window["Open"].iloc[0])
    c = float(window["Close"].iloc[-1])
    hi = float(window["High"].max())
    lo = float(window["Low"].min())
    up_exc = (hi - o) / o
    down_exc = (o - lo) / o
    return {
        "up_exc": up_exc,
        "down_exc": down_exc,
        "range": up_exc + down_exc,
        "endpoint": abs(c - o) / o,
    }


def _day_window_stats(symbol: str, minutes: list[str],
                       window_label: str) -> tuple[pd.DataFrame, list[dict]]:
    """One row per calendar day whose full `minutes` span is completely
    present in the 1-minute cache (every bucket, not merely a matching bar
    count). A day missing even one bucket is EXCLUDED with a reason, never
    interpolated."""
    raw = _load_raw_bars_1m(symbol)
    rows: list[dict] = []
    excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        window = bspes._window_bars(chunk, minutes[0], minutes[-1])
        present = set(window.index.strftime("%H:%M"))
        missing = [m for m in minutes if m not in present]
        if missing:
            excluded.append({"date": day.isoformat(),
                              "reason": f"{len(missing)} missing 1m bar(s) in "
                                        f"{window_label} window "
                                        f"(first at {missing[0]})"})
            continue
        m = _window_excursion_metrics(window.sort_index())
        raw_ratio = m["range"] / m["endpoint"] if m["endpoint"] > 0 else None
        floored_endpoint = max(m["endpoint"], ENDPOINT_FLOOR)
        rows.append({
            "date": day.isoformat(),
            "weekday": pd.Timestamp(day).strftime("%A"),
            "up_exc": m["up_exc"], "down_exc": m["down_exc"], "range": m["range"],
            "endpoint": m["endpoint"], "endpoint_floor_hit": m["endpoint"] < ENDPOINT_FLOOR,
            "ratio_raw": raw_ratio,
            "ratio_floored": m["range"] / floored_endpoint,
        })
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, excluded


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
    """Same construction as `bspes._welch` -- reimplemented locally only
    because this module's records carry an extra `endpoint_floor_hits`
    field the shared helper doesn't know about; the statistics themselves
    (mean, CI95, Welch t/p, Cohen's d, `control_reliable`) follow the exact
    same formulas as `bspes._welch` / `fstudy._welch`."""
    rec: dict = {
        "event_n": len(event_vals), "event_mean": st.mean(event_vals) if event_vals else None,
        "event_median": st.median(event_vals) if event_vals else None,
        "event_ci95": mes._ci95(event_vals),
        "control_n": len(ctrl_vals), "control_mean": st.mean(ctrl_vals) if ctrl_vals else None,
        "control_median": st.median(ctrl_vals) if ctrl_vals else None,
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


def _paired_one_sample(diffs: list[float]) -> dict:
    """One-sample, two-sided t-test of `diffs` against 0 -- used for the
    up_exc-vs-down_exc asymmetry check. No direction is pre-registered."""
    rec: dict = {
        "n": len(diffs), "mean_diff": st.mean(diffs) if diffs else None,
        "ci95": mes._ci95(diffs), "t": None, "p_two_sided": None,
    }
    if len(diffs) >= 2 and st.stdev(diffs) > 0:
        t_stat, p_val = sp_stats.ttest_1samp(diffs, 0.0)
        rec["t"] = float(t_stat)
        rec["p_two_sided"] = float(p_val)
    return rec


# ----------------------------------------------------------------- primary

def primary_test(days: pd.DataFrame, events: list[dict]) -> dict:
    """The whipsaw ratio, SPY, 08:30-08:45, event days (any of the six FRED
    release types) vs weekday-matched non-event days. ONE test, two-sided
    (no pre-registered direction -- see module docstring). Uses the FLOORED
    ratio for the Welch mean-based comparison; the RAW-ratio median is
    reported alongside as a cross-check the floor cannot influence."""
    all_event_dates = {e["date"] for e in events}
    day_dates = set(days["date"])
    dropped = sorted(all_event_dates - day_dates)

    ev = days[days["date"].isin(all_event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = bspes._matched_control(days, all_event_dates, weekdays)

    rec = _welch(ev["ratio_floored"].tolist(), ctrl["ratio_floored"].tolist())
    rec["hypothesis"] = ("SPY whipsaw ratio (range/endpoint) over 08:30-08:45 ET "
                          "differs on scheduled-release days vs matched non-event "
                          "days -- direction not pre-registered")
    rec["instrument"] = PRIMARY_SYMBOL
    rec["window_et"] = PRIMARY_WINDOW_LABEL
    rec["metric"] = "whipsaw_ratio_floored"
    rec["endpoint_floor"] = ENDPOINT_FLOOR
    rec["event_dates_matched"] = len(ev)
    rec["event_dates_dropped_no_window"] = dropped
    rec["control_weekdays_matched_against"] = sorted(weekdays)
    rec["event_endpoint_floor_hits"] = int(ev["endpoint_floor_hit"].sum())
    rec["control_endpoint_floor_hits"] = int(ctrl["endpoint_floor_hit"].sum())
    rec["event_median_ratio_raw"] = (
        st.median(ev["ratio_raw"].dropna().tolist()) if not ev["ratio_raw"].dropna().empty else None)
    rec["control_median_ratio_raw"] = (
        st.median(ctrl["ratio_raw"].dropna().tolist()) if not ctrl["ratio_raw"].dropna().empty else None)
    # levels, not only the ratio
    rec["event_mean_up_exc"] = st.mean(ev["up_exc"].tolist()) if len(ev) else None
    rec["event_mean_down_exc"] = st.mean(ev["down_exc"].tolist()) if len(ev) else None
    rec["control_mean_up_exc"] = st.mean(ctrl["up_exc"].tolist()) if len(ctrl) else None
    rec["control_mean_down_exc"] = st.mean(ctrl["down_exc"].tolist()) if len(ctrl) else None
    return rec


# --------------------------------------------------------- exploratory A: impulse vs tail

def impulse_vs_tail(events: list[dict]) -> dict:
    """Two independent event-vs-control tests, each window open-anchored to
    its own start (impulse at 08:30, tail at 08:35 -- the tail never
    borrows the impulse's open). BH-corrected within this family of 2."""
    all_event_dates = {e["date"] for e in events}
    tests: list[dict] = []
    per_window_days: dict[str, tuple[pd.DataFrame, list[dict]]] = {}

    for label, minutes in ((IMPULSE_WINDOW_LABEL, IMPULSE_MINUTES),
                            (TAIL_WINDOW_LABEL, TAIL_MINUTES)):
        days, excluded = _day_window_stats(PRIMARY_SYMBOL, minutes, label)
        per_window_days[label] = (days, excluded)

        ev = days[days["date"].isin(all_event_dates)]
        weekdays = set(ev["weekday"])
        ctrl = bspes._matched_control(days, all_event_dates, weekdays)

        rec = _welch(ev["ratio_floored"].tolist(), ctrl["ratio_floored"].tolist())
        rec.update({"family": "impulse_vs_tail", "instrument": PRIMARY_SYMBOL,
                    "window_et": label, "metric": "whipsaw_ratio_floored",
                    "complete_days": len(days), "excluded_days": len(excluded)})
        tests.append(rec)

    padj = mes._bh_adjust([t["welch_p_two_sided"] for t in tests])
    for t, p in zip(tests, padj):
        t["welch_p_bh"] = p

    return {"tests": tests}, per_window_days


# -------------------------------------------------------------- exploratory B: FOMC arm

def fomc_arm() -> dict:
    """The whipsaw ratio, 14:00-14:15 ET, SCHEDULED FOMC decision days only
    (`fstudy.load_fomc_events`, same scheduled-only discipline that module
    already argues for) vs matched non-FOMC, non-tracked-release days. n is
    far thinner than the primary's -- stated in the output, not left
    implicit."""
    days, excluded = _day_window_stats(PRIMARY_SYMBOL, FOMC_MINUTES, FOMC_WINDOW_LABEL)
    if days.empty:
        return {"tests": [], "note": "no complete 14:00-14:15 SPY windows in the cache"}, days, excluded

    fomc_events = fstudy.load_fomc_events()
    scheduled_dates = {e["date"] for e in fomc_events if not e.get("unscheduled")}
    all_fomc_dates = {e["date"] for e in fomc_events}

    ev = days[days["date"].isin(scheduled_dates)]
    weekdays = set(ev["weekday"])
    ctrl = fstudy._matched_control(days, all_fomc_dates, weekdays)

    rec = _welch(ev["ratio_floored"].tolist(), ctrl["ratio_floored"].tolist())
    rec.update({"family": "fomc_arm", "instrument": PRIMARY_SYMBOL,
                "window_et": FOMC_WINDOW_LABEL, "metric": "whipsaw_ratio_floored",
                "scheduled_meetings_in_bar_coverage": len(ev),
                "n_note": "far thinner than the primary's ~1000+ six-release day "
                          "population -- ~84-87 scheduled FOMC meetings 2016-2026"})
    tests = [rec]
    padj = mes._bh_adjust([t["welch_p_two_sided"] for t in tests])
    for t, p in zip(tests, padj):
        t["welch_p_bh"] = p
    return {"tests": tests}, days, excluded


# -------------------------------------------------------------- exploratory C: asymmetry

def asymmetry_check(days: pd.DataFrame, events: list[dict]) -> dict:
    """up_exc vs down_exc, primary (08:30-08:45) window, paired against
    zero, run independently for event days and control days. Two-sided, no
    pre-registered direction -- the first potential hint of directionality
    in this whole programme, reported carefully. BH-corrected within this
    family of 2."""
    all_event_dates = {e["date"] for e in events}
    ev = days[days["date"].isin(all_event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = bspes._matched_control(days, all_event_dates, weekdays)

    tests: list[dict] = []
    for label, group in (("event", ev), ("control", ctrl)):
        diffs = (group["up_exc"] - group["down_exc"]).tolist()
        rec = _paired_one_sample(diffs)
        rec.update({"family": "asymmetry_up_vs_down", "instrument": PRIMARY_SYMBOL,
                    "window_et": PRIMARY_WINDOW_LABEL, "group": label,
                    "mean_up_exc": st.mean(group["up_exc"].tolist()) if len(group) else None,
                    "mean_down_exc": st.mean(group["down_exc"].tolist()) if len(group) else None})
        tests.append(rec)

    padj = mes._bh_adjust([t["p_two_sided"] for t in tests])
    for t, p in zip(tests, padj):
        t["p_bh"] = p

    return {"tests": tests,
            "caveat": "no direction pre-registered -- do not overclaim on this"}


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true",
                     help="pull/merge SPY 1-minute pre-market bars before building "
                          "the study (delegates to build_spy_macro_decay_study's own "
                          "fetch, same cache)")
    ap.add_argument("--start", default=dstudy.FETCH_START)
    ap.add_argument("--end", default=None, help="YYYY-MM-DD, defaults to today")
    a = ap.parse_args(argv)

    load_dotenv()
    key = __import__("os").environ.get("FRED_API_KEY")
    if not key:
        print("  REFUSED: FRED_API_KEY missing -- cannot build the event calendar")
        return 1

    end = a.end or date.today().isoformat()
    if a.refresh:
        print(f"  fetching SPY 1m pre-market bars {a.start}..{end} (Alpaca SIP)")
        res = dstudy._redirected_fetch_1m(PRIMARY_SYMBOL, a.start, end)
        print(f"    +{res['added']} bars, {res['total_bars']} total -> {res['path']}")

    try:
        primary_days, primary_excluded = _day_window_stats(
            PRIMARY_SYMBOL, PRIMARY_MINUTES, PRIMARY_WINDOW_LABEL)
    except BarDataError as e:
        print(f"  REFUSED: {e}")
        return 1
    if primary_days.empty:
        print("  REFUSED: no complete 08:30-08:45 SPY 1m windows in the cache")
        return 1

    start = date.fromisoformat(primary_days["date"].iloc[0])
    end_d = date.fromisoformat(primary_days["date"].iloc[-1])
    try:
        events = mes.build_event_calendar(key, start=start, end=end_d)
    except EventStudyError as e:
        print(f"  REFUSED: {e}")
        return 1

    primary = primary_test(primary_days, events)
    exp_a, per_window_days = impulse_vs_tail(events)
    exp_b, fomc_days, fomc_excluded = fomc_arm()
    exp_c = asymmetry_check(primary_days, events)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    primary_days.to_csv(PRIMARY_DAYS_CSV, index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measures": "range/endpoint whipsaw ratio (path-aware), not |return| -- see "
                     "data/daytrade/event_study/spy_premarket_event_study_summary.json "
                     "and spy_macro_decay_study_summary.json for the endpoint-only "
                     "measurements this follows up on",
        "primary_symbol": PRIMARY_SYMBOL,
        "bar_source": "data/daytrade/bars_premarket_1m (Alpaca SIP, extended hours, 1m)",
        "endpoint_floor": ENDPOINT_FLOOR,
        "endpoint_floor_note": "raw endpoint < floor uses the floor for the ratio used "
                                "in the Welch mean test; endpoint_floor_hits reports how "
                                "many days that touched per group; the raw-ratio median "
                                "is also reported and is unaffected by the floor",
        "resolution_note": "1-minute bars -- every excursion figure here is a LOWER "
                            "BOUND on true tick-level travel",
        "events_found_in_window": len(events),
        "primary_complete_days": len(primary_days),
        "primary_excluded_days": len(primary_excluded),
        "primary_excluded_reasons_sample": primary_excluded[:5],
        "PRIMARY": primary,
        "EXPLORATORY_A_IMPULSE_VS_TAIL_NO_INFERENTIAL_WEIGHT": exp_a,
        "EXPLORATORY_B_FOMC_ARM_NO_INFERENTIAL_WEIGHT": exp_b,
        "fomc_complete_days": len(fomc_days) if fomc_days is not None else 0,
        "fomc_excluded_days": len(fomc_excluded) if fomc_excluded is not None else 0,
        "EXPLORATORY_C_ASYMMETRY_NO_INFERENTIAL_WEIGHT": exp_c,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=1, default=str))

    print("=" * 72)
    p_two = primary.get("welch_p_two_sided")
    null_flag = "NULL" if (p_two is None or p_two >= 0.05) else "SIGNIFICANT"
    print(f"  PRIMARY RESULT [{null_flag}]: diff={primary['diff_event_minus_control']}, "
          f"event_n={primary['event_n']}, control_n={primary['control_n']}, "
          f"p(two-sided)={p_two}")
    print(f"  wrote {SUMMARY_JSON}")
    print(f"  wrote {PRIMARY_DAYS_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
