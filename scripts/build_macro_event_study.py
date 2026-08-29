#!/usr/bin/env python3
"""MACRO EVENT STUDY — the labeled splash-to-ripple dataset.

`daytrade/macro_calendar.py` now knows WHEN a splash is scheduled. It has no
idea what a splash actually DOES to the tape. This script builds that
labeled substrate: for every historical FRED-scheduled release date that
falls inside the bar coverage this repo actually holds, measure NVDA's
realized session-level response at several forward horizons, and compare it
against a matched non-event control. No model, no threshold, no signal is
fit here — this produces the dataset and its descriptive statistics only.

SOURCES, EXPLICITLY BOUNDED
----------------------------
Historical event dates come ONLY from FRED's `fred/release/dates` endpoint,
for the SAME six release types `scripts/build_macro_calendar.py` already
tracks (CPI, PPI, Employment Situation, GDP, Personal Income and Outlays,
Initial Claims). FOMC is deliberately EXCLUDED: `daytrade/macro_calendar.py`'s
own docstring establishes FRED release id 101 is not a real schedule (it
returns a date for every calendar day once no-data rows are included), and
this repo's only real FOMC source (`data/daytrade/fomc_calendar.json`) is
hand-transcribed and carries 2026 dates only — no verified historical FOMC
decision dates exist anywhere in this repo. Fabricating them from outside
memory would repeat exactly the mistake `daytrade/macro_calendar.py`'s
docstring says this repo has already been burned by twice. So: FOMC is out
of scope for this study, stated plainly rather than silently substituted.

BAR COVERAGE, EXPLICITLY BOUNDED
---------------------------------
`data/daytrade/bars/` is a yfinance/Alpaca rolling cache, ~60-90 days deep —
useless for a study reaching back into 2024. The only population with real
depth is `data/daytrade/bars_extended/NVDA_5m.parquet` (Alpaca SIP,
2024-01-02..2026-08-25, `scripts/prove_datasource.py`). This study is
therefore NVDA-only, exactly like `scripts/build_extended_features.py`, and
uses the identical `bars.CACHE` redirect / restore-in-`finally` pattern
copied from `daytrade/oracle_audit.py::_collect`.

NO LOOK-AHEAD
-------------
`fred/release/dates` returns PUBLICATION dates (when the release actually
came out), not a nominal reference-period date — this is the opposite
failure mode from specs/039's rate-vintage look-ahead, and it is the
correct axis to measure a "was this knowable in advance / what happened
after" study on. FRED does not expose a release TIME via this endpoint —
only a date. Session-level response at horizon 0 is therefore ANY response
inside the RTH session on the release DATE (09:30-15:55 ET); this
necessarily also captures the near-universal 8:30am ET BLS/BEA release
convention for these six release types, but that convention is NOT relied
upon or asserted anywhere in this file — only the date is used. Horizons 1
and 2 are the 1st and 2nd trading SESSION PRESENT IN THE EXTENDED CACHE
strictly after the event date's own session (index-based, not calendar-date
arithmetic, so this never manufactures a session that was not actually
observed).

FAIL LOUD
---------
An event date that FRED reports but that is not itself a session in the
extended cache (rare — most of these releases land on a weekday the market
is open) is dropped with a reason, never silently coerced to the nearest
trading day. A horizon that runs off the end of the cache (the 2 most
recent events near 2026-08-25) is dropped with a reason too.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import statistics as st
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from scipy import stats as sp_stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))
import bars as bars_mod                                            # noqa: E402
from bars import BarDataError                                      # noqa: E402

EXT_CACHE = ROOT / "data" / "daytrade" / "bars_extended"
OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
EVENTS_CSV = OUT_DIR / "nvda_macro_event_ripples.csv"
SUMMARY_JSON = OUT_DIR / "nvda_macro_event_study_summary.json"
RAW_EVENTS_JSON = OUT_DIR / "fred_historical_release_dates.json"

FRED_RELEASE_DATES = "https://api.stlouisfed.org/fred/release/dates"

# Identical set/ids to scripts/build_macro_calendar.py. FOMC (id 101) is
# deliberately excluded -- see module docstring.
RELEASES: dict[str, str] = {
    "10": "Consumer Price Index",
    "46": "Producer Price Index",
    "50": "Employment Situation",
    "53": "Gross Domestic Product",
    "54": "Personal Income and Outlays",
    "180": "Unemployment Insurance Weekly Claims Report",
}
RELEASE_IMPORTANCE: dict[str, float] = {
    "Employment Situation": 1.0,
    "Consumer Price Index": 1.0,
    "Personal Income and Outlays": 1.0,
    "Producer Price Index": 0.6,
    "Gross Domestic Product": 0.6,
    "Unemployment Insurance Weekly Claims Report": 0.3,
}
HORIZONS = (0, 1, 2)
METRICS = ("ret", "abs_ret", "range_pct", "realized_vol")
MIN_CONTROL_N = 3   # below this a matched-weekday control is reported, not trusted


class EventStudyError(RuntimeError):
    """Refused to build — never partial, never guessed."""


# --------------------------------------------------------- FRED, historical

def fetch_historical_release_dates(release_id: str, key: str, *,
                                    start: date, end: date) -> list[str]:
    """Every date FRED has EVER scheduled this release for, restricted to
    [start, end]. `realtime_start` set well before `start` (not to `start`
    itself) so FRED's own asc-sorted history is read in full rather than
    filtered server-side by a parameter whose exact semantics near the
    boundary are not being relied upon here -- filtering to [start, end] is
    done locally, explicitly, after the fetch."""
    r = requests.get(FRED_RELEASE_DATES, params={
        "api_key": key, "file_type": "json", "release_id": release_id,
        "include_release_dates_with_no_data": "true",
        "realtime_start": "2000-01-01",
        "sort_order": "asc", "limit": 1000,
    }, timeout=30)
    if r.status_code != 200:
        raise EventStudyError(f"release {release_id} ({RELEASES.get(release_id, '?')}): "
                               f"FRED returned {r.status_code}: {r.text[:300]}")
    data = r.json()
    dates = [row["date"] for row in data.get("release_dates", [])]
    if not dates:
        raise EventStudyError(f"release {release_id} ({RELEASES.get(release_id, '?')}): "
                               "FRED returned zero dates — refusing to write a study "
                               "missing a release entirely rather than assume none exist")
    return sorted(d for d in dates if start.isoformat() <= d <= end.isoformat())


def build_event_calendar(key: str, *, start: date, end: date) -> list[dict]:
    events: list[dict] = []
    for rid, name in RELEASES.items():
        for d in fetch_historical_release_dates(rid, key, start=start, end=end):
            events.append({"date": d, "release_id": int(rid), "release_name": name,
                           "importance": RELEASE_IMPORTANCE[name]})
    events.sort(key=lambda e: (e["date"], e["release_id"]))
    return events


# --------------------------------------------------------------- bar loading

@contextlib.contextmanager
def _redirected_cache(cache: Path):
    """SAVE/RESTORE-IN-`finally`, the exact pattern
    `daytrade/oracle_audit.py::_collect` and
    `scripts/build_extended_features.py::_redirected_cache` use. A faulted
    module global outliving this call would silently repoint every later
    reader in the process at the wrong parquet tree."""
    if not any(cache.glob("*_5m.parquet")):
        raise BarDataError(
            f"cache {cache} holds no *_5m.parquet. Refusing to fall back to "
            f"{bars_mod.CACHE} — a silent fallback would build the study on "
            f"one population's name and another's data.")
    orig = bars_mod.CACHE
    bars_mod.CACHE = cache
    try:
        yield
    finally:
        bars_mod.CACHE = orig


def session_stats(symbol: str, cache: Path = EXT_CACHE) -> pd.DataFrame:
    """One row per complete RTH session: date, weekday, ret, abs_ret,
    range_pct, realized_vol. Half days and gapped sessions are excluded by
    `bars.load_sessions` itself (never repaired, never interpolated) —
    that exclusion is inherited here, not reimplemented."""
    with _redirected_cache(cache):
        sessions = bars_mod.load_sessions(symbol, "5m")

    rows = []
    for s in sessions:
        df = s.df
        o = float(df["Open"].iloc[0])
        c = float(df["Close"].iloc[-1])
        hi = float(df["High"].max())
        lo = float(df["Low"].min())
        closes = df["Close"].astype(float).to_numpy()
        # intrasession realized vol: sqrt of sum of squared 5m log returns.
        log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        rv = math.sqrt(sum(r * r for r in log_rets)) if log_rets else float("nan")
        rows.append({
            "date": s.day.isoformat(),
            "weekday": s.day.strftime("%A"),
            "ret": c / o - 1.0,
            "abs_ret": abs(c / o - 1.0),
            "range_pct": (hi - lo) / o,
            "realized_vol": rv,
        })
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out


# --------------------------------------------------------- event <-> session

def build_ripple_dataset(sessions: pd.DataFrame, events: list[dict],
                          symbol: str) -> tuple[pd.DataFrame, list[dict]]:
    """One row per (event, symbol, horizon, metric). Horizons index INTO the
    actual session list (never a manufactured calendar date), so a horizon
    landing outside the cache, or an event date that is not itself a
    session in the cache, is dropped with a reason — never guessed."""
    session_dates = sessions["date"].tolist()
    idx_by_date = {d: i for i, d in enumerate(session_dates)}

    rows: list[dict] = []
    dropped: list[dict] = []
    for ev in events:
        d = ev["date"]
        if d not in idx_by_date:
            dropped.append({**ev, "reason": "event date is not an RTH session "
                                             "present in the extended NVDA cache "
                                             "(weekend/holiday, or outside cache span)"})
            continue
        i0 = idx_by_date[d]
        for h in HORIZONS:
            j = i0 + h
            if j >= len(sessions):
                dropped.append({**ev, "horizon": h,
                                 "reason": "horizon runs past the end of the "
                                           "cached session list"})
                continue
            row = sessions.iloc[j]
            for metric in METRICS:
                rows.append({
                    "event_date": d,
                    "release_name": ev["release_name"],
                    "importance": ev["importance"],
                    "symbol": symbol,
                    "horizon": h,
                    "session_date": row["date"],
                    "session_weekday": row["weekday"],
                    "metric": metric,
                    "value": row[metric],
                })
    return pd.DataFrame(rows), dropped


# ------------------------------------------------------------ control pool

def control_pool(sessions: pd.DataFrame, event_dates: set[str]) -> pd.DataFrame:
    """Sessions that are not themselves a scheduled event day for ANY of the
    six tracked release types. Matching is by weekday of the SESSION being
    compared (not the weekday of the triggering event), because horizon 1/2
    sessions shift weekday relative to the event itself and a Friday
    session's realized vol is not a fair comparison for a Tuesday session's.

    Deliberately NOT excluding a buffer window (D-1..D+2) around events: with
    weekly Initial Claims in the tracked set, nearly every Thursday and the
    days around it would be "near an event", which would shrink the control
    pool to a degenerate size. The trade-off is stated, not hidden: a control
    day one session away from an unrelated event may itself carry some
    residual event effect, biasing the event-vs-control gap toward zero
    (conservative, not favorable to finding a splash)."""
    return sessions[~sessions["date"].isin(event_dates)].reset_index(drop=True)


def _ci95(values: list[float]) -> tuple[float, float] | None:
    n = len(values)
    if n < 2:
        return None
    mean = st.mean(values)
    sd = st.stdev(values)
    if sd == 0:
        return (mean, mean)
    # normal approx (n too small in places for a defensible t-table lookup
    # without scipy; flagged in the summary via n so nobody reads this as
    # more precise than it is)
    margin = 1.96 * sd / math.sqrt(n)
    return (mean - margin, mean + margin)


def _bh_adjust(pvals: list[float | None]) -> list[float | None]:
    """Benjamini-Hochberg FDR correction, hand-rolled (statsmodels is not a
    declared dependency of this repo — requirements.txt pins scipy only).
    `None` entries (no test ran) pass through as `None`, not treated as 0
    or 1. Same discipline `CLAUDE.md`'s carry edge already holds itself to
    ("survives BH correction") is applied here rather than reporting raw
    p-values across 72 comparisons unadjusted."""
    indexed = [(i, p) for i, p in enumerate(pvals) if p is not None]
    if not indexed:
        return list(pvals)
    m = len(indexed)
    ranked = sorted(indexed, key=lambda t: t[1])
    adj: dict[int, float] = {}
    prev = 1.0
    for rank, (i, p) in reversed(list(enumerate(ranked, start=1))):
        val = min(prev, p * m / rank)
        adj[i] = val
        prev = val
    return [adj.get(i) for i in range(len(pvals))]


def summarize(ripples: pd.DataFrame, ctrl: pd.DataFrame) -> dict:
    out: dict = {"by_release_horizon_metric": []}
    for release_name in sorted(ripples["release_name"].unique()):
        for h in HORIZONS:
            for metric in METRICS:
                sub = ripples[(ripples["release_name"] == release_name)
                               & (ripples["horizon"] == h)
                               & (ripples["metric"] == metric)]
                if sub.empty:
                    continue
                event_vals = sub["value"].tolist()

                # matched-weekday control values, pooled across the weekdays
                # that actually occur among this (release, horizon)'s events
                weekdays = sub["session_weekday"].unique().tolist()
                ctrl_vals = ctrl[ctrl["weekday"].isin(weekdays)][metric].tolist()

                rec = {
                    "release_name": release_name,
                    "horizon_sessions": h,
                    "metric": metric,
                    "event_n": len(event_vals),
                    "event_mean": st.mean(event_vals) if event_vals else None,
                    "event_ci95": _ci95(event_vals),
                    "control_n": len(ctrl_vals),
                    "control_mean": st.mean(ctrl_vals) if ctrl_vals else None,
                    "control_ci95": _ci95(ctrl_vals),
                    "diff_event_minus_control": None,
                    "welch_t": None,
                    "welch_p": None,
                    "control_reliable": len(ctrl_vals) >= MIN_CONTROL_N,
                }
                if rec["event_mean"] is not None and rec["control_mean"] is not None:
                    rec["diff_event_minus_control"] = rec["event_mean"] - rec["control_mean"]
                if len(event_vals) >= 2 and len(ctrl_vals) >= 2:
                    t_stat, p_val = sp_stats.ttest_ind(event_vals, ctrl_vals,
                                                        equal_var=False)
                    rec["welch_t"] = float(t_stat)
                    rec["welch_p"] = float(p_val)
                out["by_release_horizon_metric"].append(rec)

    padj = _bh_adjust([r["welch_p"] for r in out["by_release_horizon_metric"]])
    n_tested = sum(1 for r in out["by_release_horizon_metric"] if r["welch_p"] is not None)
    n_sig_raw = sum(1 for r in out["by_release_horizon_metric"]
                     if r["welch_p"] is not None and r["welch_p"] < 0.05)
    n_sig_bh = 0
    for rec, p in zip(out["by_release_horizon_metric"], padj):
        rec["welch_p_bh"] = p
        if p is not None and p < 0.05:
            n_sig_bh += 1
    out["multiple_testing"] = {
        "tests_run": n_tested,
        "significant_raw_p_lt_0.05": n_sig_raw,
        "expected_by_chance_at_alpha_0.05": round(n_tested * 0.05, 1),
        "significant_after_bh_correction": n_sig_bh,
    }
    return out


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--cache", default=str(EXT_CACHE))
    ap.add_argument("--start", default=None,
                     help="YYYY-MM-DD, defaults to the extended cache's earliest session")
    ap.add_argument("--end", default=None,
                     help="YYYY-MM-DD, defaults to the extended cache's latest session")
    a = ap.parse_args(argv)

    load_dotenv()
    key = __import__("os").environ.get("FRED_API_KEY")
    if not key:
        print("  REFUSED: FRED_API_KEY missing — cannot build the event study")
        return 1

    cache = Path(a.cache).resolve()
    try:
        sessions = session_stats(a.symbol, cache)
    except BarDataError as e:
        print(f"  REFUSED: {e}")
        return 1
    if sessions.empty:
        print("  REFUSED: no complete RTH sessions in the extended cache")
        return 1

    start = date.fromisoformat(a.start) if a.start else date.fromisoformat(sessions["date"].iloc[0])
    end = date.fromisoformat(a.end) if a.end else date.fromisoformat(sessions["date"].iloc[-1])

    try:
        events = build_event_calendar(key, start=start, end=end)
    except EventStudyError as e:
        print(f"  REFUSED: {e}")
        return 1

    ripples, dropped = build_ripple_dataset(sessions, events, a.symbol)
    event_dates = {e["date"] for e in events}
    ctrl = control_pool(sessions, event_dates)
    summary = summarize(ripples, ctrl)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ripples.to_csv(EVENTS_CSV, index=False)
    RAW_EVENTS_JSON.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": start.isoformat(), "end": end.isoformat(),
        "releases": RELEASES, "fomc_excluded_reason":
            "no verified historical FOMC decision dates exist anywhere in this "
            "repo — data/daytrade/fomc_calendar.json is hand-transcribed and "
            "2026-only; FRED release id 101 is a known non-schedule per "
            "daytrade/macro_calendar.py's docstring",
        "events": events,
        "dropped": dropped,
    }, indent=1))
    SUMMARY_JSON.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": a.symbol,
        "bar_source": "data/daytrade/bars_extended (Alpaca SIP)",
        "sessions_in_cache": len(sessions),
        "session_span": [sessions["date"].iloc[0], sessions["date"].iloc[-1]],
        "events_found_in_window": len(events),
        "events_landing_on_a_cached_session": len(event_dates & set(sessions["date"])),
        "ripple_rows": len(ripples),
        "dropped_rows": len(dropped),
        "control_pool_n": len(ctrl),
        **summary,
    }, indent=1, default=str))

    print(f"  {len(events)} FRED-scheduled event(s) in [{start}, {end}] "
          f"across {len(RELEASES)} release types")
    print(f"  {len(event_dates & set(sessions['date']))} land inside the "
          f"{len(sessions)}-session NVDA extended cache "
          f"({sessions['date'].iloc[0]}..{sessions['date'].iloc[-1]})")
    print(f"  {len(ripples)} ripple row(s) written, {len(dropped)} dropped "
          f"(reasons in {RAW_EVENTS_JSON.name})")
    print(f"  control pool: {len(ctrl)} non-event session(s)")
    print(f"  wrote {EVENTS_CSV}")
    print(f"  wrote {SUMMARY_JSON}")
    print(f"  wrote {RAW_EVENTS_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
