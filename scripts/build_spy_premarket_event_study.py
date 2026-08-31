#!/usr/bin/env python3
"""SPY PRE-MARKET MACRO REACTION STUDY — the rerun after the NVDA null.

WHY THIS RERUN EXISTS
----------------------
`scripts/build_macro_event_study.py` (the NVDA study,
`data/daytrade/event_study/nvda_macro_event_study_summary.json`) ran 72
comparisons and came back with 0 surviving Benjamini-Hochberg — 9 raw
p<0.05 against ~3.6 expected by chance, exactly what multiple testing on
noise looks like. Two design choices could each hide a real splash effect
behind that null:

  1. WRONG INSTRUMENT. NVDA's variance is dominated by its own flow
     (earnings, AI-cycle momentum). A macro release moves the INDEX, not one
     single-name's order book. This study measures SPY (and, exploratorily,
     QQQ) instead.
  2. WRONG RESOLUTION. The NVDA study's horizon 0 was the entire 09:30-15:55
     RTH session — 390 minutes. CPI/NFP/PPI/GDP/PCE/Initial Claims all
     release at 08:30 ET (fixed, publicly documented BLS/BEA/Census
     convention — not a guess, not fetched from FRED, which exposes only a
     release DATE, never a time; FOMC is excluded from this entire study for
     the same reason `build_macro_event_study.py` excludes it: FRED release
     id 101 is a known non-schedule per `daytrade/macro_calendar.py`'s
     docstring, and this repo holds no verified historical FOMC dates). A
     five-minute impulse diluted across 390 minutes is most of why the NVDA
     study saw nothing. This study measures the 08:30-09:00 ET pre-market
     half hour directly.

08:30 ET IS PRE-MARKET — THE OBSTACLE
--------------------------------------
`daytrade/bars.py::load_sessions` filters every cache to
RTH_OPEN='09:30'..RTH_CLOSE='15:55', so the existing NVDA extended cache
(`data/daytrade/bars_extended/`) — even though its underlying parquet
happens to carry raw pre-market ticks — is reached ONLY through
`load_sessions` by every other consumer in this repo, and mixing a
pre-market-window study into that path would risk a future caller trusting
`load_sessions` output as RTH-only when it silently was not. This study
therefore fetches into its OWN cache directory,
`data/daytrade/bars_premarket/`, read directly via `_load_raw_bars()`
(never through `bars.load_sessions`), so nothing downstream can mistake a
pre-market bar for a session bar.

`daytrade/datasource.AlpacaSource` (feed='sip') returns whatever the
consolidated tape has for the requested window with NO server-side RTH
filter — extended-hours bars come back exactly like RTH bars, just outside
09:30-15:55. This was verified live 2026-08-27 (`SPY` 5m
2026-08-20..2026-08-22 returned bars from 04:00 through 19:55 ET). So the
extended-hours fallback this module's docstring warns might be needed was
NOT needed — pre-market bars are directly obtainable, not a gap-day proxy.

HISTORY DEPTH, EMPIRICALLY BOUNDED
-----------------------------------
Verified live 2026-08-27: Alpaca SIP 5m for SPY returns data from
2016-01-01 forward; a 2015-01-01 request returned zero bars (also checked
2013, 2014). `FETCH_START = "2016-01-01"` is that empirical boundary, not a
guess.

ONE PRE-REGISTERED PRIMARY HYPOTHESIS
---------------------------------------
Everything the NVDA study got right about multiple-testing discipline is
kept. What it got wrong (72 equally-weighted comparisons, no primary/
secondary separation) is fixed here by declaring, before any number is
computed:

    PRIMARY: SPY absolute return over the 08:30-09:00 ET window is larger
    on scheduled-release days (CPI, PPI, Employment Situation, GDP, Personal
    Income and Outlays, Initial Claims — the same six FRED-tracked release
    types `build_macro_event_study.py` uses) than on matched non-event days
    (same 08:30-09:00 window, same weekday, excluding any day that is a
    scheduled release for ANY of the six types).

    ONE instrument (SPY), ONE window (08:30-09:00 ET), ONE metric
    (abs_ret), ONE test (Welch, one-sided: event > control). No multiple-
    testing penalty — it is the only test in its family.

Everything else below — per-release breakdown, signed (directional) return,
the 08:30-08:45 vs 08:45-09:00 sub-window decay check, and QQQ — is
SECONDARY / EXPLORATORY, labelled as such in the output, BH-corrected
within its own family, and carries no inferential weight on its own. It is
hypothesis-generating only.

CONTROLS — THE INITIAL CLAIMS DEGENERACY, STATED NOT HIDDEN
-------------------------------------------------------------
Initial Claims publishes every week on Thursday. A weekday-matched control
pool for a Thursday-only release type is close to empty by construction —
almost every Thursday in the sample IS an Initial Claims event day, so the
non-event Thursday population that would serve as its control nearly
vanishes. This is reported explicitly per release in the secondary
breakdown (`control_n`, `control_reliable` — same `MIN_CONTROL_N` discipline
`build_macro_event_study.py` already uses) rather than silently dropped or
silently treated as reliable. It does not degrade the PRIMARY test: the
primary test pools all six release types together, and every weekday of the
week is represented among SOME release type (CPI/PPI/NFP/GDP/PCE land on
varying weekdays), so the primary control pool is never Thursday-starved.

NO LOOK-AHEAD
-------------
The 08:30-09:00 window is measured strictly forward from the 08:30 release
time. A release's SCHEDULE is knowable in advance (that is the entire
"splash" thesis); the market's reaction to its CONTENT is not, and nothing
here reads a bar timestamped before 08:30 to decide anything about an event
at 08:30.

FAIL LOUD
---------
A trading day whose 08:30-09:00 window is missing even one of its six
expected 5-minute bars is EXCLUDED with a reason, never interpolated. An
event date that is a FRED-scheduled release but has no complete pre-market
window in the cache (rare) is dropped with a reason, same discipline
`build_macro_event_study.py::build_ripple_dataset` already uses.

REUSE
-----
The FRED event-calendar fetch (`fetch_historical_release_dates`,
`build_event_calendar`, the six release ids/importances, the
`EventStudyError` refusal-on-zero-dates behavior, and the hand-rolled BH
correction) is imported directly from `build_macro_event_study` rather than
re-implemented — one source of truth for "what a scheduled macro release
is" in this repo.
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
import datasource                                                  # noqa: E402
from bars import BarDataError                                      # noqa: E402
import build_macro_event_study as mes                               # noqa: E402
from build_macro_event_study import EventStudyError                 # noqa: E402

CACHE = ROOT / "data" / "daytrade" / "bars_premarket"
OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
SUMMARY_JSON = OUT_DIR / "spy_premarket_event_study_summary.json"
SPY_DAYS_CSV = OUT_DIR / "spy_premarket_days.csv"
QQQ_DAYS_CSV = OUT_DIR / "qqq_premarket_days.csv"

# Empirically verified live 2026-08-27 — see module docstring.
FETCH_START = "2016-01-01"

PRIMARY_SYMBOL = "SPY"
SECONDARY_SYMBOL = "QQQ"

# Fixed, publicly documented release-time convention (BLS/BEA/Census) for
# these six release types — not fetched, not guessed. FRED's own
# `fred/release/dates` endpoint exposes only a DATE, never a time.
RELEASE_TIME_ET = "08:30"
PRIMARY_WINDOW = ("08:30", "08:55")     # 6 x 5m bars covering 08:30-09:00
FIRST_HALF = ("08:30", "08:40")         # 3 x 5m bars, 08:30-08:45
SECOND_HALF = ("08:45", "08:55")        # 3 x 5m bars, 08:45-09:00
BAR_MINUTES = 5
MIN_CONTROL_N = 3


# --------------------------------------------------------------- bar loading

def _redirected_fetch(symbol: str, start: str, end: str) -> dict:
    """Fetch/merge into CACHE (data/daytrade/bars_premarket/), the same
    save/restore-in-`finally` `bars.CACHE` redirect pattern
    `build_macro_event_study.py::_redirected_cache` and
    `scripts/prove_datasource.py` use — a faulted module global outliving
    this call would silently repoint every later reader in the process at
    the wrong parquet tree."""
    CACHE.mkdir(parents=True, exist_ok=True)
    src = datasource.AlpacaSource(feed="sip")
    orig = bars_mod.CACHE
    bars_mod.CACHE = CACHE
    try:
        return bars_mod.refresh_cache(symbol, "5m", start=start, end=end, source=src)
    finally:
        bars_mod.CACHE = orig


def _load_raw_bars(symbol: str) -> pd.DataFrame:
    """Every bar in the pre-market cache for `symbol`, ET-indexed, RAW — NOT
    routed through `bars.load_sessions`, which would strip everything
    outside 09:30-15:55 and defeat the entire point of this cache."""
    path = CACHE / f"{symbol}_5m.parquet"
    if not path.exists():
        raise BarDataError(f"no pre-market cache at {path} — run with --refresh first")
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(bars_mod.ET)
    return df


def _window_bars(chunk: pd.DataFrame, start_hhmm: str, end_hhmm: str) -> pd.DataFrame:
    t = chunk.index.strftime("%H:%M")
    return chunk[(t >= start_hhmm) & (t <= end_hhmm)]


def _missing_bars(window: pd.DataFrame, day, start_hhmm: str, end_hhmm: str) -> list:
    """Expected 5-minute timestamps in [start,end] on `day` that are not
    present in `window`. Never interpolated — a hole is reported, not
    filled."""
    start_ts = pd.Timestamp.combine(day, pd.Timestamp(start_hhmm).time()).tz_localize(bars_mod.ET)
    end_ts = pd.Timestamp.combine(day, pd.Timestamp(end_hhmm).time()).tz_localize(bars_mod.ET)
    expected = pd.date_range(start_ts, end_ts, freq=pd.Timedelta(minutes=BAR_MINUTES))
    return [ts for ts in expected if ts not in window.index]


def _window_metrics(window: pd.DataFrame) -> dict:
    o = float(window["Open"].iloc[0])
    c = float(window["Close"].iloc[-1])
    hi = float(window["High"].max())
    lo = float(window["Low"].min())
    ret = c / o - 1.0
    return {"ret": ret, "abs_ret": abs(ret), "range_pct": (hi - lo) / o}


def premarket_day_stats(symbol: str) -> tuple[pd.DataFrame, list[dict]]:
    """One row per calendar day whose PRIMARY_WINDOW (08:30-08:55, 6 bars)
    is fully present with no gap. Days failing that completeness check are
    excluded and reported, never repaired — same discipline
    `bars.load_sessions` already holds itself to for RTH sessions."""
    raw = _load_raw_bars(symbol)
    rows: list[dict] = []
    excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        window = _window_bars(chunk, *PRIMARY_WINDOW)
        missing = _missing_bars(window, day, *PRIMARY_WINDOW)
        if missing:
            excluded.append({"date": day.isoformat(),
                              "reason": f"{len(missing)} missing 5m bar(s) in "
                                        f"{PRIMARY_WINDOW[0]}-09:00 window "
                                        f"(first at {missing[0]})"})
            continue
        m = _window_metrics(window)
        fh = _window_metrics(_window_bars(window, *FIRST_HALF))
        sh = _window_metrics(_window_bars(window, *SECOND_HALF))
        rows.append({
            "date": day.isoformat(),
            "weekday": pd.Timestamp(day).strftime("%A"),
            "ret": m["ret"], "abs_ret": m["abs_ret"], "range_pct": m["range_pct"],
            "fh_abs_ret": fh["abs_ret"], "sh_abs_ret": sh["abs_ret"],
        })
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, excluded


# ------------------------------------------------------------ control pool

def control_pool(days: pd.DataFrame, event_dates: set[str]) -> pd.DataFrame:
    """Days that are not themselves a scheduled event day for ANY of the six
    tracked release types. Same non-buffered trade-off
    `build_macro_event_study.py::control_pool` already documents and
    accepts (conservative, biases event-vs-control gap toward zero — not
    favorable to finding a splash)."""
    return days[~days["date"].isin(event_dates)].reset_index(drop=True)


def _matched_control(days: pd.DataFrame, all_event_dates: set[str],
                      weekdays: set[str]) -> pd.DataFrame:
    pool = control_pool(days, all_event_dates)
    return pool[pool["weekday"].isin(weekdays)]


# --------------------------------------------------------------- statistics

def _cohens_d(a: list[float], b: list[float]) -> float | None:
    """POOLED-sd Cohen's d.

    DENOMINATOR MISMATCH -- annotated 2026-08-31 by the repo-wide sweep.
    Every significance test in this module is Welch (`equal_var=False`), which
    does NOT assume equal variance. This d divides by a POOLED sd, which does.
    The two disagree whenever the arms have unequal variance, and event days
    are more volatile than control days by construction -- so the pooled sd
    sits below the true standard error and this d OVERSTATES the effect,
    worst exactly where the event is most violent (1.87x on the FOMC study,
    1.25x macro_decay, 1.20x premarket).

    The value is retained unchanged so published summaries stay comparable,
    and the Welch t/p values it sits beside were always correct. But it must
    not be used for power or detection-floor arithmetic. `_welch` emits
    `cohens_d_welch_equivalent` = t * sqrt(1/n1 + 1/n2) alongside it, which is
    consistent with the test actually run; prefer that, or |t| directly.
    See artifacts/EVENT_STUDY_MDE.md."""
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
        # Effect size consistent with the Welch test actually run above. The
        # pooled-sd `cohens_d` beside it is retained for continuity but is the
        # wrong denominator for this test -- see `_cohens_d`.
        n1, n2 = len(event_vals), len(ctrl_vals)
        rec["cohens_d_welch_equivalent"] = float(t_stat) * math.sqrt(1 / n1 + 1 / n2)
        rec["cohens_d_caveat"] = (
            "cohens_d uses a POOLED sd; the test is Welch. For power, MDE, or "
            "detection-floor arithmetic use cohens_d_welch_equivalent or |welch_t|.")
    return rec


# ----------------------------------------------------------------- primary

def primary_test(days: pd.DataFrame, events: list[dict]) -> dict:
    """SPY abs_ret, 08:30-09:00, event days (any of the six release types)
    vs weekday-matched non-event days. ONE test, one-sided (event > control,
    the pre-registered direction), no multiple-testing correction — it is
    the only member of its family."""
    all_event_dates = {e["date"] for e in events}
    day_dates = set(days["date"])
    dropped = sorted(all_event_dates - day_dates)

    ev = days[days["date"].isin(all_event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = _matched_control(days, all_event_dates, weekdays)

    rec = _welch(ev["abs_ret"].tolist(), ctrl["abs_ret"].tolist())
    rec["hypothesis"] = ("SPY absolute return over 08:30-09:00 ET is larger on "
                          "scheduled-release days than on matched non-event days")
    rec["instrument"] = PRIMARY_SYMBOL
    rec["window_et"] = "08:30-09:00"
    rec["metric"] = "abs_ret"
    rec["event_dates_matched"] = len(ev)
    rec["event_dates_dropped_no_premarket_window"] = dropped
    rec["control_weekdays_matched_against"] = sorted(weekdays)
    if rec["welch_t"] is not None:
        # one-sided p in the pre-registered direction (event > control)
        p_two = rec["welch_p_two_sided"]
        rec["welch_p_one_sided_event_gt_control"] = (
            p_two / 2 if rec["welch_t"] > 0 else 1 - p_two / 2)
    else:
        rec["welch_p_one_sided_event_gt_control"] = None
    return rec


# ---------------------------------------------------------- secondary/exploratory

def secondary_tests(spy_days: pd.DataFrame, qqq_days: pd.DataFrame,
                     events: list[dict]) -> dict:
    """Everything NOT the one pre-registered primary test. Labelled
    SECONDARY/EXPLORATORY throughout — hypothesis-generating only, BH-
    corrected within this family, no inferential weight standing alone."""
    all_event_dates = {e["date"] for e in events}
    tests: list[dict] = []

    # --- per-release breakdown, SPY abs_ret and signed ret --------------
    by_release: dict[str, set[str]] = {}
    for e in events:
        by_release.setdefault(e["release_name"], set()).add(e["date"])

    for release_name, rel_dates in sorted(by_release.items()):
        rel_dates_present = rel_dates & set(spy_days["date"])
        ev = spy_days[spy_days["date"].isin(rel_dates_present)]
        weekdays = set(ev["weekday"])
        ctrl = _matched_control(spy_days, all_event_dates, weekdays)
        for metric in ("abs_ret", "ret"):
            rec = _welch(ev[metric].tolist(), ctrl[metric].tolist())
            rec.update({"family": "per_release_breakdown", "instrument": PRIMARY_SYMBOL,
                        "release_name": release_name, "window_et": "08:30-09:00",
                        "metric": metric})
            tests.append(rec)

    # --- pooled signed return (directionality), SPY ----------------------
    ev_all = spy_days[spy_days["date"].isin(all_event_dates)]
    weekdays_all = set(ev_all["weekday"])
    ctrl_all = _matched_control(spy_days, all_event_dates, weekdays_all)
    rec = _welch(ev_all["ret"].tolist(), ctrl_all["ret"].tolist())
    rec.update({"family": "pooled_signed_return", "instrument": PRIMARY_SYMBOL,
                "release_name": "ALL_SIX_POOLED", "window_et": "08:30-09:00",
                "metric": "ret"})
    tests.append(rec)

    # --- sub-window decay: first half vs second half of the reaction -----
    for label, col in (("08:30-08:45", "fh_abs_ret"), ("08:45-09:00", "sh_abs_ret")):
        rec = _welch(ev_all[col].tolist(), ctrl_all[col].tolist())
        rec.update({"family": "subwindow_decay", "instrument": PRIMARY_SYMBOL,
                    "release_name": "ALL_SIX_POOLED", "window_et": label,
                    "metric": "abs_ret"})
        tests.append(rec)

    # --- QQQ, same primary construction, different instrument ------------
    if qqq_days is not None and not qqq_days.empty:
        ev_q = qqq_days[qqq_days["date"].isin(all_event_dates)]
        weekdays_q = set(ev_q["weekday"])
        ctrl_q = _matched_control(qqq_days, all_event_dates, weekdays_q)
        rec = _welch(ev_q["abs_ret"].tolist(), ctrl_q["abs_ret"].tolist())
        rec.update({"family": "cross_instrument", "instrument": SECONDARY_SYMBOL,
                    "release_name": "ALL_SIX_POOLED", "window_et": "08:30-09:00",
                    "metric": "abs_ret"})
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
    }


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=FETCH_START)
    ap.add_argument("--end", default=None, help="YYYY-MM-DD, defaults to today")
    ap.add_argument("--refresh", action="store_true",
                     help="pull/merge SPY and QQQ pre-market bars before building the study")
    ap.add_argument("--skip-qqq", action="store_true",
                     help="skip the secondary cross-instrument (QQQ) test")
    a = ap.parse_args(argv)

    load_dotenv()
    key = __import__("os").environ.get("FRED_API_KEY")
    if not key:
        print("  REFUSED: FRED_API_KEY missing — cannot build the event calendar")
        return 1

    end = a.end or date.today().isoformat()

    if a.refresh:
        for sym in ([PRIMARY_SYMBOL] if a.skip_qqq else [PRIMARY_SYMBOL, SECONDARY_SYMBOL]):
            print(f"  fetching {sym} 5m pre-market bars {a.start}..{end} (Alpaca SIP)")
            res = _redirected_fetch(sym, a.start, end)
            print(f"    +{res['added']} bars, {res['total_bars']} total -> {res['path']}")

    try:
        spy_days, spy_excluded = premarket_day_stats(PRIMARY_SYMBOL)
    except BarDataError as e:
        print(f"  REFUSED: {e}")
        return 1
    if spy_days.empty:
        print("  REFUSED: no complete SPY pre-market windows in the cache")
        return 1

    qqq_days, qqq_excluded = pd.DataFrame(), []
    if not a.skip_qqq:
        try:
            qqq_days, qqq_excluded = premarket_day_stats(SECONDARY_SYMBOL)
        except BarDataError as e:
            print(f"  QQQ secondary test skipped — {e}")
            qqq_days, qqq_excluded = pd.DataFrame(), []

    start = date.fromisoformat(spy_days["date"].iloc[0])
    end_d = date.fromisoformat(spy_days["date"].iloc[-1])
    try:
        events = mes.build_event_calendar(key, start=start, end=end_d)
    except EventStudyError as e:
        print(f"  REFUSED: {e}")
        return 1

    primary = primary_test(spy_days, events)
    secondary = secondary_tests(spy_days, qqq_days, events)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    spy_days.to_csv(SPY_DAYS_CSV, index=False)
    if not qqq_days.empty:
        qqq_days.to_csv(QQQ_DAYS_CSV, index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rerun_of": "data/daytrade/event_study/nvda_macro_event_study_summary.json "
                     "(0/72 survived BH correction — wrong instrument, wrong resolution)",
        "primary_symbol": PRIMARY_SYMBOL,
        "secondary_symbol": None if a.skip_qqq else SECONDARY_SYMBOL,
        "bar_source": "data/daytrade/bars_premarket (Alpaca SIP, extended hours)",
        "fetch_start": a.start,
        "release_time_et_convention": RELEASE_TIME_ET,
        "release_time_convention_note": "fixed publicly documented BLS/BEA/Census "
            "convention for CPI/PPI/Employment Situation/GDP/Personal Income and "
            "Outlays/Initial Claims. FRED's release-dates endpoint exposes only a "
            "date, never a time -- this is not fetched, not guessed. FOMC excluded "
            "(FRED release id 101 is a known non-schedule; no verified historical "
            "FOMC dates exist in this repo).",
        "spy_complete_premarket_days": len(spy_days),
        "spy_excluded_days": len(spy_excluded),
        "spy_excluded_reasons_sample": spy_excluded[:5],
        "qqq_complete_premarket_days": len(qqq_days),
        "qqq_excluded_days": len(qqq_excluded),
        "events_found_in_window": len(events),
        "PRIMARY": primary,
        "SECONDARY_EXPLORATORY_NO_INFERENTIAL_WEIGHT": secondary,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=1, default=str))

    print("=" * 72)
    p_one = primary.get("welch_p_one_sided_event_gt_control")
    null_flag = "NULL" if (p_one is None or p_one >= 0.05) else "SIGNIFICANT"
    print(f"  PRIMARY RESULT [{null_flag}]: diff={primary['diff_event_minus_control']}, "
          f"event_n={primary['event_n']}, control_n={primary['control_n']}, "
          f"p(one-sided)={p_one}")
    print(f"  wrote {SUMMARY_JSON}")
    print(f"  wrote {SPY_DAYS_CSV}")
    if not qqq_days.empty:
        print(f"  wrote {QQQ_DAYS_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
