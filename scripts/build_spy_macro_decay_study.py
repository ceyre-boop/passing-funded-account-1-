#!/usr/bin/env python3
"""SPY MACRO DECAY STUDY — the pre-registered primary at 5-minute resolution,
plus two exploratory follow-ons to the 08:30-09:00 splash finding in
`scripts/build_spy_premarket_event_study.py`
(`data/daytrade/event_study/spy_premarket_event_study_summary.json`).

WHAT THAT STUDY ESTABLISHED, NOT RE-DERIVED HERE
--------------------------------------------------
SPY |return| over 08:30-09:00 ET is roughly twice as large on scheduled-
release days as on matched controls (event 0.1783%, n=1008, vs control
0.0890%, n=1667; Welch t=9.50, p=1.16e-20, d=0.456; QQQ replicates). The
sub-window decay check already run there (08:30-08:45 vs 08:45-09:00) found
the diff concentrated in the FIRST half: +0.0139pp (p=0.010) vs -0.0002pp
(p=0.96) in the second. Half the 30-minute window is dilution.

ONE PRE-REGISTERED PRIMARY, DECLARED BEFORE ANY NUMBER IS COMPUTED
---------------------------------------------------------------------
    PRIMARY: SPY absolute return over the SINGLE 08:30-08:35 ET 5-minute bar
    is larger on scheduled-release days (the same six FRED-tracked release
    types as the parent study) than on matched non-event days (same weekday,
    excluding any day that is a scheduled release for ANY of the six types).

    ONE instrument (SPY), ONE window (08:30-08:35, one 5m bar), ONE metric
    (abs_ret), ONE test (Welch, one-sided: event > control). No multiple-
    testing penalty. This bar is already present in the existing
    `data/daytrade/bars_premarket/SPY_5m.parquet` cache -- no new fetch is
    required to answer it.

Everything else here -- the 1-minute decay curve and the breath-holding
expansion -- is SECONDARY / EXPLORATORY, labelled as such, BH-corrected
within its own family, carries no inferential weight standing alone, and is
hypothesis-generating only.

EXPLORATORY A -- THE DECAY CURVE
-----------------------------------
Needs 1-minute resolution the 5m cache cannot give. Pulled fresh via
`AlpacaSource(feed="sip")` into its OWN cache,
`data/daytrade/bars_premarket_1m/`, never mixed into the 5m premarket cache
or routed through `bars.load_sessions` (same isolation rationale
`build_spy_premarket_event_study.py`'s docstring already gives for its own
cache). Verified live 2026-08-28: Alpaca SIP 1m for SPY returns data from
2016-01-01 forward (a 2015-01-01 request returned zero bars) -- the SAME
empirical floor as the 5m cache, not shorter, so `FETCH_START` matches. The
question this answers is the half-life of a macro print's imprint on the
tape: does it clear inside a minute or take most of the window.

EXPLORATORY B -- THE BREATH-HOLDING EXPANSION
--------------------------------------------------
The parent study's placebo windows found event days quieter than control
BEFORE the release. This walks that back in 30-minute blocks:
08:00-08:30, 07:30-08:00, 07:00-07:30 (30m granularity down to 07:00, where
the parent study's placebo already ran), then 06:00-07:00, 05:00-06:00,
04:00-05:00 (60m granularity further back, where pre-market liquidity itself
is thin enough that finer resolution would mostly measure noise).

THE CONFOUND, HANDLED NOT HIDDEN
-----------------------------------
Overnight volatility is structurally lower at 04:00 than at 08:00 for EVERY
day, event or not -- a naive "abs_ret shrinks as you walk back" finding
would be pure time-of-day, not a calendar-knowable signal. Every comparison
here is event-vs-control WITHIN the same clock block, never across blocks,
and the reported quantity is the event/control RATIO per block (a ratio
near 1.0 means that block carries no incremental information, however small
its absolute level is) alongside the raw means so the ratio's own base rate
is visible.

REUSE
-----
The 08:30-08:35 primary and the breath-holding blocks both read the SAME
raw (non-RTH-filtered) `data/daytrade/bars_premarket/SPY_5m.parquet` cache
`build_spy_premarket_event_study.py` already built and populated, through
that module's own `_load_raw_bars`, `_window_bars`, `_window_metrics`,
`_matched_control`, and `_welch` -- none of that is re-implemented here. The
FRED event-calendar fetch and hand-rolled BH correction are imported from
`build_macro_event_study` the same way the parent study imports them.

FAIL LOUD
---------
A day missing its 08:30 5m bar, or missing any minute in the 08:25-08:40
decay window, or missing any 5m bar inside a breath-holding block, is
EXCLUDED with a reason, never interpolated -- the same discipline every
other bar-loading path in this repo already holds itself to.

NO LOOK-AHEAD
-------------
Every window here is measured strictly forward from its own start; nothing
reads a bar timestamped before a window's own opening tick to decide
anything about that window.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))
sys.path.insert(0, str(ROOT / "scripts"))
import bars as bars_mod                                             # noqa: E402
import datasource                                                   # noqa: E402
from bars import BarDataError                                       # noqa: E402
import build_macro_event_study as mes                                # noqa: E402
from build_macro_event_study import EventStudyError                  # noqa: E402
import build_spy_premarket_event_study as bspes                      # noqa: E402

CACHE_1M = ROOT / "data" / "daytrade" / "bars_premarket_1m"
OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
SUMMARY_JSON = OUT_DIR / "spy_macro_decay_study_summary.json"
DECAY_CSV = OUT_DIR / "spy_decay_minute_days.csv"
BREATH_CSV_TEMPLATE = "spy_breath_block_{slug}_days.csv"

PRIMARY_SYMBOL = "SPY"

# Empirically verified live 2026-08-28 -- see module docstring. Same floor as
# the 5m premarket cache.
FETCH_START = bspes.FETCH_START

# Single 5m bar spanning 08:30-08:35 ET.
PRIMARY_BAR = ("08:30", "08:30")

# 1-minute buckets 08:25..08:40 inclusive.
DECAY_MINUTES = [f"{h:02d}:{m:02d}" for h, m in
                 [(8, mm) for mm in range(25, 40 + 1)]]

# label -> (start_hhmm, last_bar_start_hhmm) for the 5m raw cache. The window
# actually measured is [start, last_bar_start + 5m), i.e. `_window_bars`'
# inclusive-both-ends semantics over 5-minute bar START timestamps.
BREATH_BLOCKS = [
    ("08:00-08:30", "08:00", "08:25"),
    ("07:30-08:00", "07:30", "07:55"),
    ("07:00-07:30", "07:00", "07:25"),
    ("06:00-07:00", "06:00", "06:55"),
    ("05:00-06:00", "05:00", "05:55"),
    ("04:00-05:00", "04:00", "04:55"),
]


# --------------------------------------------------------------- bar loading

def _redirected_fetch_1m(symbol: str, start: str, end: str) -> dict:
    """Fetch/merge into CACHE_1M, the same save/restore-in-`finally`
    `bars.CACHE` redirect `build_spy_premarket_event_study._redirected_fetch`
    already uses for its own (5m) cache."""
    CACHE_1M.mkdir(parents=True, exist_ok=True)
    src = datasource.AlpacaSource(feed="sip")
    orig = bars_mod.CACHE
    bars_mod.CACHE = CACHE_1M
    try:
        return bars_mod.refresh_cache(symbol, "1m", start=start, end=end, source=src)
    finally:
        bars_mod.CACHE = orig


def _load_raw_bars_1m(symbol: str) -> pd.DataFrame:
    """Every bar in the 1-minute pre-market cache for `symbol`, ET-indexed,
    RAW -- same discipline as `bspes._load_raw_bars`, own cache directory."""
    path = CACHE_1M / f"{symbol}_1m.parquet"
    if not path.exists():
        raise BarDataError(f"no 1m pre-market cache at {path} -- run with --refresh first")
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(bars_mod.ET)
    return df


# ----------------------------------------------------------------- primary

def primary_bar_day_stats(symbol: str) -> tuple[pd.DataFrame, list[dict]]:
    """One row per calendar day whose single 08:30-08:35 5m bar is present
    in the raw premarket cache. Days without that bar are excluded and
    reported, never repaired."""
    raw = bspes._load_raw_bars(symbol)
    rows: list[dict] = []
    excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        window = bspes._window_bars(chunk, *PRIMARY_BAR)
        if window.empty:
            excluded.append({"date": day.isoformat(),
                              "reason": "no 08:30 5m bar in the premarket cache"})
            continue
        m = bspes._window_metrics(window)
        rows.append({"date": day.isoformat(),
                      "weekday": pd.Timestamp(day).strftime("%A"),
                      "ret": m["ret"], "abs_ret": m["abs_ret"]})
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, excluded


def primary_test(days: pd.DataFrame, events: list[dict]) -> dict:
    """SPY abs_ret, the single 08:30-08:35 bar, event days vs weekday-
    matched non-event days. ONE test, one-sided (event > control), no
    multiple-testing correction -- it is the only member of its family."""
    all_event_dates = {e["date"] for e in events}
    day_dates = set(days["date"])
    dropped = sorted(all_event_dates - day_dates)

    ev = days[days["date"].isin(all_event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = bspes._matched_control(days, all_event_dates, weekdays)

    rec = bspes._welch(ev["abs_ret"].tolist(), ctrl["abs_ret"].tolist())
    rec["hypothesis"] = ("SPY absolute return over the single 08:30-08:35 ET "
                          "5-minute bar is larger on scheduled-release days "
                          "than on matched non-event days")
    rec["instrument"] = PRIMARY_SYMBOL
    rec["window_et"] = "08:30-08:35"
    rec["metric"] = "abs_ret"
    rec["event_dates_matched"] = len(ev)
    rec["event_dates_dropped_no_premarket_window"] = dropped
    rec["control_weekdays_matched_against"] = sorted(weekdays)
    if rec["welch_t"] is not None:
        p_two = rec["welch_p_two_sided"]
        rec["welch_p_one_sided_event_gt_control"] = (
            p_two / 2 if rec["welch_t"] > 0 else 1 - p_two / 2)
    else:
        rec["welch_p_one_sided_event_gt_control"] = None
    return rec


# ------------------------------------------------------- exploratory A: decay

def decay_minute_day_stats(symbol: str) -> tuple[pd.DataFrame, list[dict]]:
    """One row per calendar day whose full 08:25-08:40 1-minute span (16
    buckets) is completely present in the 1m cache. A day missing even one
    of those 16 bars is excluded and reported, never interpolated."""
    raw = _load_raw_bars_1m(symbol)
    rows: list[dict] = []
    excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        per_minute: dict[str, float] = {}
        missing: list[str] = []
        for hhmm in DECAY_MINUTES:
            window = bspes._window_bars(chunk, hhmm, hhmm)
            if window.empty:
                missing.append(hhmm)
                continue
            per_minute[hhmm] = bspes._window_metrics(window)["abs_ret"]
        if missing:
            excluded.append({"date": day.isoformat(),
                              "reason": f"{len(missing)} missing 1m bar(s) in "
                                        f"08:25-08:40 window (first at {missing[0]})"})
            continue
        row = {"date": day.isoformat(), "weekday": pd.Timestamp(day).strftime("%A")}
        row.update({f"m_{hhmm}": v for hhmm, v in per_minute.items()})
        rows.append(row)
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, excluded


def decay_curve(days: pd.DataFrame, events: list[dict]) -> dict:
    """Per-minute event-vs-control Welch test, 08:25..08:40. Exploratory
    family, BH-corrected within itself -- 16 tests, no standalone weight."""
    all_event_dates = {e["date"] for e in events}
    ev = days[days["date"].isin(all_event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = bspes._matched_control(days, all_event_dates, weekdays)

    tests: list[dict] = []
    for hhmm in DECAY_MINUTES:
        col = f"m_{hhmm}"
        rec = bspes._welch(ev[col].tolist(), ctrl[col].tolist())
        rec.update({"family": "decay_curve_1m", "instrument": PRIMARY_SYMBOL,
                    "minute_et": hhmm, "metric": "abs_ret"})
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

    return {
        "tests": tests,
        "event_n": len(ev), "control_n": len(ctrl),
        "control_weekdays_matched_against": sorted(weekdays),
        "multiple_testing": {
            "tests_run": n_tested,
            "significant_raw_p_lt_0.05": n_sig_raw,
            "expected_by_chance_at_alpha_0.05": round(n_tested * 0.05, 1),
            "significant_after_bh_correction": n_sig_bh,
        },
    }


# --------------------------------------------- exploratory B: breath-holding

def breath_block_day_stats(symbol: str, start_hhmm: str,
                            last_bar_hhmm: str) -> tuple[pd.DataFrame, list[dict]]:
    """One row per calendar day whose 5m bars from `start_hhmm` through
    `last_bar_hhmm` (inclusive, bar-START timestamps) are all present in the
    raw premarket cache."""
    raw = bspes._load_raw_bars(symbol)
    rows: list[dict] = []
    excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        window = bspes._window_bars(chunk, start_hhmm, last_bar_hhmm)
        missing = bspes._missing_bars(window, day, start_hhmm, last_bar_hhmm)
        if missing:
            excluded.append({"date": day.isoformat(),
                              "reason": f"{len(missing)} missing 5m bar(s) in "
                                        f"{start_hhmm}-{last_bar_hhmm} block "
                                        f"(first at {missing[0]})"})
            continue
        m = bspes._window_metrics(window)
        rows.append({"date": day.isoformat(),
                      "weekday": pd.Timestamp(day).strftime("%A"),
                      "abs_ret": m["abs_ret"]})
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, excluded


def breath_holding_expansion(symbol: str, events: list[dict]) -> dict:
    """Event-vs-control abs_ret, WITHIN each clock block, walking backward
    from 08:00 to 04:00. Reports the event/control RATIO per block (never a
    cross-block absolute-level comparison -- that would be confounded by
    structurally lower overnight volatility at every hour, event or not).
    Exploratory family, BH-corrected within itself."""
    all_event_dates = {e["date"] for e in events}
    tests: list[dict] = []
    per_block_days: dict[str, tuple[pd.DataFrame, list[dict]]] = {}

    for label, start_hhmm, last_bar_hhmm in BREATH_BLOCKS:
        days, excluded = breath_block_day_stats(symbol, start_hhmm, last_bar_hhmm)
        per_block_days[label] = (days, excluded)

        ev = days[days["date"].isin(all_event_dates)]
        weekdays = set(ev["weekday"])
        ctrl = bspes._matched_control(days, all_event_dates, weekdays)

        rec = bspes._welch(ev["abs_ret"].tolist(), ctrl["abs_ret"].tolist())
        ratio = None
        if rec["event_mean"] is not None and rec["control_mean"] not in (None, 0):
            ratio = rec["event_mean"] / rec["control_mean"]
        rec.update({"family": "breath_holding_expansion", "instrument": symbol,
                    "block_et": label, "metric": "abs_ret",
                    "event_control_ratio": ratio,
                    "complete_days": len(days), "excluded_days": len(excluded),
                    "control_weekdays_matched_against": sorted(weekdays)})
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

    return {
        "tests": tests,
        "multiple_testing": {
            "tests_run": n_tested,
            "significant_raw_p_lt_0.05": n_sig_raw,
            "expected_by_chance_at_alpha_0.05": round(n_tested * 0.05, 1),
            "significant_after_bh_correction": n_sig_bh,
        },
    }, per_block_days


# --------------------------------------------------------------------- main

def _slug(label: str) -> str:
    return label.replace(":", "").replace("-", "_")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=FETCH_START)
    ap.add_argument("--end", default=None, help="YYYY-MM-DD, defaults to today")
    ap.add_argument("--refresh", action="store_true",
                     help="pull/merge SPY 1-minute pre-market bars before building "
                          "the decay curve (the primary and breath-holding blocks "
                          "reuse the existing 5m premarket cache and never fetch)")
    a = ap.parse_args(argv)

    load_dotenv()
    key = __import__("os").environ.get("FRED_API_KEY")
    if not key:
        print("  REFUSED: FRED_API_KEY missing -- cannot build the event calendar")
        return 1

    end = a.end or date.today().isoformat()

    if a.refresh:
        print(f"  fetching SPY 1m pre-market bars {a.start}..{end} (Alpaca SIP)")
        res = _redirected_fetch_1m(PRIMARY_SYMBOL, a.start, end)
        print(f"    +{res['added']} bars, {res['total_bars']} total -> {res['path']}")

    # ---------------------------------------------------------- PRIMARY
    try:
        primary_days, primary_excluded = primary_bar_day_stats(PRIMARY_SYMBOL)
    except BarDataError as e:
        print(f"  REFUSED: {e}")
        return 1
    if primary_days.empty:
        print("  REFUSED: no 08:30-08:35 SPY bars in the premarket cache")
        return 1

    start = date.fromisoformat(primary_days["date"].iloc[0])
    end_d = date.fromisoformat(primary_days["date"].iloc[-1])
    try:
        events = mes.build_event_calendar(key, start=start, end=end_d)
    except EventStudyError as e:
        print(f"  REFUSED: {e}")
        return 1

    primary = primary_test(primary_days, events)

    # ---------------------------------------------------- EXPLORATORY A
    decay = None
    decay_excluded: list[dict] = []
    decay_history_note = None
    try:
        decay_days, decay_excluded = decay_minute_day_stats(PRIMARY_SYMBOL)
        if not decay_days.empty:
            decay = decay_curve(decay_days, events)
            first_1m_day = decay_days["date"].iloc[0]
            decay_history_note = (
                f"1m decay curve n starts {first_1m_day}; primary (5m) cache "
                f"starts {primary_days['date'].iloc[0]}. "
                + ("SAME depth -- not fewer events."
                   if first_1m_day <= primary_days["date"].iloc[0]
                   else "SHORTER than the 5m cache -- fewer events in the decay curve."))
            decay_days.to_csv(DECAY_CSV, index=False)
    except BarDataError as e:
        print(f"  EXPLORATORY A (decay curve) skipped -- {e}")

    # ---------------------------------------------------- EXPLORATORY B
    breath, per_block_days = breath_holding_expansion(PRIMARY_SYMBOL, events)
    for label, (days_df, _excl) in per_block_days.items():
        if not days_df.empty:
            days_df.to_csv(OUT_DIR / BREATH_CSV_TEMPLATE.format(slug=_slug(label)), index=False)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "follows_up_on": "data/daytrade/event_study/spy_premarket_event_study_summary.json "
                          "(08:30-09:00 event 0.1783% n=1008 vs control 0.0890% n=1667, "
                          "Welch t=9.50, p=1.16e-20, d=0.456)",
        "primary_symbol": PRIMARY_SYMBOL,
        "primary_bar_source": "data/daytrade/bars_premarket (Alpaca SIP, extended hours, 5m)",
        "decay_curve_bar_source": "data/daytrade/bars_premarket_1m (Alpaca SIP, extended hours, 1m)",
        "fetch_start_1m": a.start,
        "events_found_in_window": len(events),
        "primary_complete_days": len(primary_days),
        "primary_excluded_days": len(primary_excluded),
        "primary_excluded_reasons_sample": primary_excluded[:5],
        "PRIMARY": primary,
        "EXPLORATORY_A_DECAY_CURVE_1M_NO_INFERENTIAL_WEIGHT": decay,
        "decay_curve_history_note": decay_history_note,
        "decay_curve_excluded_days": len(decay_excluded),
        "decay_curve_excluded_reasons_sample": decay_excluded[:5],
        "EXPLORATORY_B_BREATH_HOLDING_EXPANSION_NO_INFERENTIAL_WEIGHT": breath,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=1, default=str))

    print("=" * 72)
    p_one = primary.get("welch_p_one_sided_event_gt_control")
    null_flag = "NULL" if (p_one is None or p_one >= 0.05) else "SIGNIFICANT"
    print(f"  PRIMARY RESULT [{null_flag}]: diff={primary['diff_event_minus_control']}, "
          f"event_n={primary['event_n']}, control_n={primary['control_n']}, "
          f"p(one-sided)={p_one}")
    print(f"  wrote {SUMMARY_JSON}")
    if decay is not None:
        print(f"  wrote {DECAY_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
