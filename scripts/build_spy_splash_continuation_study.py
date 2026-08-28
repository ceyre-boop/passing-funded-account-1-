#!/usr/bin/env python3
"""SPY SPLASH CONTINUATION STUDY — momentum vs reversion after the impulse.

WHAT IS ALREADY ESTABLISHED, NOT RE-DERIVED HERE
--------------------------------------------------
`scripts/build_spy_macro_decay_study.py`
(`data/daytrade/event_study/spy_macro_decay_study_summary.json`) already
proved the splash is real, causal, and its shape is measured: SPY |return|
in the 08:30-08:35 ET bar is ~3x control (d=0.571, p=6.2e-29), and the
1-minute decay curve shows 08:30 at 4.8x control collapsing to 1.97x by
08:31, then ~1.5-1.9x through 08:40. `scripts/build_fomc_splash_event_study.py`
found the same shape, bigger, at FOMC 14:00 (d=1.31). Five independent tests
already agree the splash moves MAGNITUDE, not DIRECTION -- signed return is
flat everywhere, unconditionally. NONE of that is re-run here.

THE QUESTION THIS STUDY ANSWERS
---------------------------------
"Can you predict the direction beforehand" is closed -- no. This is a
different, still-open question: conditional on the impulse having happened
and having a SIGN, what comes next? Does the market CONTINUE in the impulse
direction (momentum), FADE it (reversion), or neither?

THE PRE-REGISTERED PRIMARY -- ONE TEST
------------------------------------------
On SPY scheduled-release days (the same six FRED-tracked release types
`build_macro_event_study.py` / `build_spy_premarket_event_study.py` use):
the mean of sign(r_impulse) * r_continuation, where r_impulse is the signed
return of the SINGLE 08:30-08:31 ET 1-minute bar (the impulse minute --
decay curve showed this is where the splash actually lives, 4.8x control
vs 1.97x the very next minute) and r_continuation is the signed return of
the 08:31-08:35 ET window (4 x 1-minute bars, 08:31 through 08:34 bar-
starts) -- together spanning exactly the already-established 08:30-08:35
primary bar, split into its first minute and the four minutes after it.

That per-day statistic is compared, event days vs weekday-matched
non-event days (Welch, two-sided -- this is a direction-discovery test,
not a one-sided confirmation), NOT reported on its own. ALL equity series
carry some short-horizon autocorrelation; an event-day number alone proves
nothing. What is reported is whether event days DIFFER from ordinary days:
    diff > 0 and significant  -> MOMENTUM (the splash adds continuation)
    diff < 0 and significant  -> REVERSION (the splash adds fade)
    not significant           -> NULL, no incremental conditional structure
ONE instrument (SPY), ONE window pair, ONE metric, ONE test. No multiple-
testing penalty -- it is the only member of its family.

MICROSTRUCTURE CAVEAT, STATED NOT HIDDEN
--------------------------------------------
Conditioning on a large move and then measuring the next move is the
textbook setup for spurious mean reversion from bid-ask bounce: a print
that ticks up on the offer and back on the bid looks exactly like reversion
and is not. The bar cache used here
(`data/daytrade/bars_premarket_1m/SPY_1m.parquet`, Alpaca SIP) carries only
OHLCV -- Open, High, Low, Close, Volume -- verified by direct column
inspection. There is NO bid/ask or midpoint field in this cache, so no
midpoint- or VWAP-based alternative computation is possible here; this is
stated as a limitation, not silently worked around. Close-to-close is what
this study reports, using genuine trade prints (SIP consolidated tape), not
a synthetic midpoint. SPY is among the tightest-spread instruments in
existence, which mitigates but does not eliminate the concern. As a partial
check, EXPLORATORY_LARGE_IMPULSE below reruns the primary statistic
restricted to the largest quartile of impulse-minute moves on event days
(bounce dominates SMALL moves, not large ones) -- if the primary finding
and the large-impulse finding agree in sign, that is evidence against pure
bounce; if they disagree, the primary finding should not be trusted as
clean. See that section's own caveat about its control-side asymmetry.

NO LOOK-AHEAD
-------------
sign(r_impulse) is knowable at the close of the impulse bar (08:31 ET for
the 08:30 primary, 14:01 ET for the FOMC arm below); every continuation
window measured against it starts strictly after that close.

REUSE
-----
The 1-minute pre-market/RTH cache and its loader
(`build_spy_macro_decay_study._load_raw_bars_1m`, `CACHE_1M`), the 5-minute
premarket/RTH cache and its window/control/statistics helpers
(`build_spy_premarket_event_study._window_bars`, `_window_metrics`,
`_missing_bars`, `_matched_control`, `_welch`, `_load_raw_bars`), the FRED
event calendar and BH correction (`build_macro_event_study.
build_event_calendar`, `_bh_adjust`), and the FOMC calendar loader
(`build_fomc_splash_event_study.load_fomc_events`) are all imported and
used as-is -- none of that machinery is reimplemented here.

FAIL LOUD
---------
Every window (impulse minute, each continuation window, the MFE/MAE span)
has its OWN completeness check against the 1-minute or 5-minute cache. A
day missing any required bar is excluded from that specific window's
statistic (NaN, never interpolated) and independently reported -- a day can
be usable for the primary and unusable for the MFE/MAE exploratory, exactly
like the FOMC study's independent stmt/presser exclusion.
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
import build_macro_event_study as mes                                 # noqa: E402
from build_macro_event_study import EventStudyError                   # noqa: E402
import build_spy_premarket_event_study as bspes                       # noqa: E402
import build_spy_macro_decay_study as decay_mod                       # noqa: E402
import build_fomc_splash_event_study as fomc_mod                      # noqa: E402

OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
SUMMARY_JSON = OUT_DIR / "spy_splash_continuation_study_summary.json"
PRIMARY_DAYS_CSV = OUT_DIR / "spy_splash_continuation_primary_days.csv"
FOMC_DAYS_CSV = OUT_DIR / "spy_splash_continuation_fomc_days.csv"

PRIMARY_SYMBOL = "SPY"

# 08:30 primary -- impulse minute + the four minutes that complete the
# already-established 08:30-08:35 bar.
IMPULSE_0830 = "08:30"
CONTINUATION_0831_0834 = [f"08:{m:02d}" for m in range(31, 35)]
# EXPLORATORY -- next 5 minutes past the established primary bar.
HORIZON_0835_0839 = [f"08:{m:02d}" for m in range(35, 40)]
# EXPLORATORY -- MFE/MAE span, 08:30 through 08:44 (endpoint 08:45).
MFE_RANGE_0830_0844 = [f"08:{m:02d}" for m in range(30, 45)]

# FOMC arm -- same construction, 14:00 statement bar.
IMPULSE_1400 = "14:00"
CONTINUATION_1401_1404 = [f"14:{m:02d}" for m in range(1, 5)]

LARGE_IMPULSE_PERCENTILE = 0.75


# --------------------------------------------------------------- statistics

def _sign(x: float) -> float:
    if x > 0:
        return 1.0
    if x < 0:
        return -1.0
    return 0.0


def _stat_series(impulse: pd.Series, continuation: pd.Series) -> list[float]:
    return [_sign(r) * c for r, c in zip(impulse.tolist(), continuation.tolist())]


# ------------------------------------------------------- 1m day-level stats

def impulse_continuation_day_stats(raw: pd.DataFrame, impulse_hhmm: str,
                                    continuation_minutes: list[str]
                                    ) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """One row per calendar day, `r_impulse`/`abs_r_impulse` (NaN + reason
    logged in `impulse_excluded` if the impulse-minute 1m bar is missing)
    and `r_continuation` (NaN + reason logged in `continuation_excluded` if
    ANY 1m bar in `continuation_minutes` is missing) -- independent, a day
    can be usable for one and not the other."""
    rows: list[dict] = []
    impulse_excluded: list[dict] = []
    continuation_excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        present = set(chunk.index.strftime("%H:%M"))
        row = {"date": day.isoformat(), "weekday": pd.Timestamp(day).strftime("%A")}
        if impulse_hhmm in present:
            m = bspes._window_metrics(bspes._window_bars(chunk, impulse_hhmm, impulse_hhmm))
            row["r_impulse"] = m["ret"]
            row["abs_r_impulse"] = m["abs_ret"]
        else:
            row["r_impulse"] = float("nan")
            row["abs_r_impulse"] = float("nan")
            impulse_excluded.append({"date": day.isoformat(),
                                      "reason": f"missing {impulse_hhmm} 1m bar"})
        if all(hh in present for hh in continuation_minutes):
            m = bspes._window_metrics(
                bspes._window_bars(chunk, continuation_minutes[0], continuation_minutes[-1]))
            row["r_continuation"] = m["ret"]
        else:
            row["r_continuation"] = float("nan")
            missing = [hh for hh in continuation_minutes if hh not in present]
            continuation_excluded.append({
                "date": day.isoformat(),
                "reason": f"missing 1m bar(s) in {continuation_minutes[0]}-"
                          f"{continuation_minutes[-1]} (first at {missing[0]})"})
        rows.append(row)
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, impulse_excluded, continuation_excluded


def mfe_mae_day_stats(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """One row per day with the 08:30-08:44 span (endpoint 08:45) complete
    in the 1m cache: max upside excursion, max downside excursion, total
    excursion (whipsaw magnitude), and the endpoint-to-endpoint net move,
    all relative to the 08:30 open. Days missing any of the 15 required 1m
    bars are excluded and reported, never interpolated."""
    rows: list[dict] = []
    excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        present = set(chunk.index.strftime("%H:%M"))
        if not all(hh in present for hh in MFE_RANGE_0830_0844):
            missing = [hh for hh in MFE_RANGE_0830_0844 if hh not in present]
            excluded.append({"date": day.isoformat(),
                              "reason": f"missing 1m bar(s) in 08:30-08:44 "
                                        f"(first at {missing[0]})"})
            continue
        w = bspes._window_bars(chunk, MFE_RANGE_0830_0844[0], MFE_RANGE_0830_0844[-1])
        open_ = float(w["Open"].iloc[0])
        hi = float(w["High"].max())
        lo = float(w["Low"].min())
        close_ = float(w["Close"].iloc[-1])
        mfe_up = (hi - open_) / open_
        mfe_down = (lo - open_) / open_
        rows.append({
            "date": day.isoformat(), "weekday": pd.Timestamp(day).strftime("%A"),
            "mfe_up": mfe_up, "mfe_down": mfe_down,
            "total_excursion": mfe_up - mfe_down,
            "net_move": close_ / open_ - 1.0,
        })
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, excluded


# ---------------------------------------------------------------- 5m horizons

def horizon_5m_stat(sign_by_date: pd.Series, start_hhmm: str, end_hhmm: str,
                     symbol: str = "SPY") -> tuple[pd.DataFrame, list[dict]]:
    """Continuation statistic in a window read from the 5-minute premarket/
    RTH cache, joined against `sign_by_date` (sign of the 08:30 1-minute
    impulse, indexed by date-string). Same completeness discipline as
    `build_spy_premarket_event_study`'s own 5m windows."""
    raw = bspes._load_raw_bars(symbol)
    rows: list[dict] = []
    excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        d = day.isoformat()
        if d not in sign_by_date.index:
            continue
        window = bspes._window_bars(chunk, start_hhmm, end_hhmm)
        missing = bspes._missing_bars(window, day, start_hhmm, end_hhmm)
        if missing:
            excluded.append({"date": d,
                              "reason": f"missing 5m bar(s) in {start_hhmm}-{end_hhmm}"})
            continue
        m = bspes._window_metrics(window)
        rows.append({"date": d, "weekday": pd.Timestamp(day).strftime("%A"),
                      "r_window": m["ret"]})
    out = pd.DataFrame(rows)
    if out.empty:
        return out, excluded
    out["sign_impulse"] = out["date"].map(sign_by_date)
    out = out.dropna(subset=["sign_impulse"])
    out["stat"] = out["sign_impulse"] * out["r_window"]
    return out.reset_index(drop=True), excluded


# --------------------------------------------------------------------- tests

def _interpret(diff: float | None, p: float | None) -> str:
    if diff is None or p is None:
        return "INSUFFICIENT_DATA"
    if p >= 0.05:
        return ("NULL -- indistinguishable from control, no incremental "
                "conditional directional structure beyond ordinary-day autocorrelation")
    return ("MOMENTUM -- event days continue the impulse direction more than control"
            if diff > 0 else
            "REVERSION -- event days fade the impulse direction more than control")


def continuation_test(days: pd.DataFrame, membership_dates: set[str],
                       exclusion_dates: set[str], hypothesis: str, window_et: str,
                       instrument: str = "SPY") -> dict:
    complete = days.dropna(subset=["r_impulse", "r_continuation"])
    ev = complete[complete["date"].isin(membership_dates)]
    weekdays = set(ev["weekday"])
    ctrl = bspes._matched_control(complete, exclusion_dates, weekdays)

    ev_stat = _stat_series(ev["r_impulse"], ev["r_continuation"])
    ctrl_stat = _stat_series(ctrl["r_impulse"], ctrl["r_continuation"])

    rec = bspes._welch(ev_stat, ctrl_stat)
    rec["hypothesis"] = hypothesis
    rec["instrument"] = instrument
    rec["window_et"] = window_et
    rec["metric"] = "sign(r_impulse) * r_continuation"
    rec["event_n_days"] = len(ev)
    rec["control_n_days"] = len(ctrl)
    rec["control_weekdays_matched_against"] = sorted(weekdays)
    rec["interpretation"] = _interpret(rec["diff_event_minus_control"], rec["welch_p_two_sided"])
    return rec


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    a = ap.parse_args(argv)

    load_dotenv()
    key = __import__("os").environ.get("FRED_API_KEY")
    if not key:
        print("  REFUSED: FRED_API_KEY missing -- cannot build the FRED event calendar")
        return 1

    try:
        raw1m = decay_mod._load_raw_bars_1m(PRIMARY_SYMBOL)
    except bspes.BarDataError as e:
        print(f"  REFUSED: {e} -- run build_spy_macro_decay_study.py --refresh first")
        return 1

    # ---------------------------------------------------------- PRIMARY
    primary_days, primary_impulse_excl, primary_cont_excl = \
        impulse_continuation_day_stats(raw1m, IMPULSE_0830, CONTINUATION_0831_0834)
    if primary_days.dropna(subset=["r_impulse", "r_continuation"]).empty:
        print("  REFUSED: no complete 08:30-08:34 SPY 1m days")
        return 1

    complete_primary = primary_days.dropna(subset=["r_impulse", "r_continuation"])
    start = date.fromisoformat(complete_primary["date"].iloc[0])
    end_d = date.fromisoformat(complete_primary["date"].iloc[-1])
    try:
        fred_events = mes.build_event_calendar(key, start=start, end=end_d)
    except EventStudyError as e:
        print(f"  REFUSED: {e}")
        return 1
    fred_event_dates = {e["date"] for e in fred_events}

    primary = continuation_test(
        primary_days, fred_event_dates, fred_event_dates,
        hypothesis=("sign(r_08:30-08:31) * r_08:31-08:35 is different on SPY "
                    "scheduled-release days than on matched non-event days "
                    "(momentum if positive, reversion if negative, null if not significant)"),
        window_et="impulse 08:30-08:31, continuation 08:31-08:35")

    # ------------------------------------------------- EXPLORATORY: large impulse
    ev_complete = complete_primary[complete_primary["date"].isin(fred_event_dates)]
    ctrl_complete = bspes._matched_control(complete_primary, fred_event_dates,
                                            set(ev_complete["weekday"]))
    ctrl_stat_full = _stat_series(ctrl_complete["r_impulse"], ctrl_complete["r_continuation"])

    large_impulse = None
    if len(ev_complete) >= 8:
        threshold = ev_complete["abs_r_impulse"].quantile(LARGE_IMPULSE_PERCENTILE)
        large_ev = ev_complete[ev_complete["abs_r_impulse"] >= threshold]
        small_ev = ev_complete[ev_complete["abs_r_impulse"] < threshold]
        large_stat = _stat_series(large_ev["r_impulse"], large_ev["r_continuation"])
        small_stat = _stat_series(small_ev["r_impulse"], small_ev["r_continuation"])

        rec_large_vs_small = bspes._welch(large_stat, small_stat)
        rec_large_vs_small.update({
            "family": "large_impulse_diagnostic", "instrument": PRIMARY_SYMBOL,
            "comparison": "top quartile |r_impulse| vs bottom 3 quartiles, event days only",
            "abs_r_impulse_threshold_p75": float(threshold),
            "interpretation": _interpret(rec_large_vs_small["diff_event_minus_control"],
                                          rec_large_vs_small["welch_p_two_sided"])})

        rec_large_vs_ctrl = bspes._welch(large_stat, ctrl_stat_full)
        rec_large_vs_ctrl.update({
            "family": "large_impulse_diagnostic", "instrument": PRIMARY_SYMBOL,
            "comparison": "top quartile |r_impulse| event days vs FULL (unfiltered) control",
            "caveat": "control is NOT filtered by move size -- this is not a clean "
                      "apples-to-apples on impulse magnitude, read as informative "
                      "context for whether the primary finding survives restricting "
                      "to moves too large for bid-ask bounce to plausibly dominate, "
                      "not as a standalone clean test",
            "abs_r_impulse_threshold_p75": float(threshold),
            "interpretation": _interpret(rec_large_vs_ctrl["diff_event_minus_control"],
                                          rec_large_vs_ctrl["welch_p_two_sided"])})
        large_impulse = {"large_vs_small_event_days": rec_large_vs_small,
                          "large_vs_full_control": rec_large_vs_ctrl}

    # ------------------------------------------------- EXPLORATORY: horizon sweep
    exploratory_tests: list[dict] = []
    if large_impulse is not None:
        exploratory_tests.append(large_impulse["large_vs_full_control"])

    horizon2_days, horizon2_impulse_excl, horizon2_cont_excl = \
        impulse_continuation_day_stats(raw1m, IMPULSE_0830, HORIZON_0835_0839)
    horizon2 = continuation_test(
        horizon2_days, fred_event_dates, fred_event_dates,
        hypothesis="sign(r_08:30-08:31) * r_08:35-08:40 -- does conditional structure "
                   "appear one horizon further out than the primary window",
        window_et="impulse 08:30-08:31, continuation 08:35-08:40")
    horizon2["family"] = "horizon_sweep"
    exploratory_tests.append(horizon2)

    sign_by_date = complete_primary.set_index("date")["r_impulse"].apply(_sign)

    h3_df, h3_excl = horizon_5m_stat(sign_by_date, "08:40", "08:55")
    h3_ev = h3_df[h3_df["date"].isin(fred_event_dates)]
    h3_ctrl = bspes._matched_control(h3_df, fred_event_dates, set(h3_ev["weekday"]))
    rec_h3 = bspes._welch(h3_ev["stat"].tolist(), h3_ctrl["stat"].tolist())
    rec_h3.update({"family": "horizon_sweep", "instrument": PRIMARY_SYMBOL,
                    "window_et": "impulse 08:30-08:31, continuation 08:40-09:00",
                    "metric": "sign(r_impulse) * r_window", "bar_source": "5m premarket cache",
                    "event_n_days": len(h3_ev), "control_n_days": len(h3_ctrl),
                    "interpretation": _interpret(rec_h3["diff_event_minus_control"],
                                                  rec_h3["welch_p_two_sided"])})
    exploratory_tests.append(rec_h3)

    h4_df, h4_excl = horizon_5m_stat(sign_by_date, "09:30", "09:30")
    h4_ev = h4_df[h4_df["date"].isin(fred_event_dates)]
    h4_ctrl = bspes._matched_control(h4_df, fred_event_dates, set(h4_ev["weekday"]))
    rec_h4 = bspes._welch(h4_ev["stat"].tolist(), h4_ctrl["stat"].tolist())
    rec_h4.update({"family": "horizon_sweep", "instrument": PRIMARY_SYMBOL,
                    "window_et": "impulse 08:30-08:31, continuation into RTH open 09:30-09:35",
                    "metric": "sign(r_impulse) * r_window", "bar_source": "5m premarket/RTH cache",
                    "event_n_days": len(h4_ev), "control_n_days": len(h4_ctrl),
                    "interpretation": _interpret(rec_h4["diff_event_minus_control"],
                                                  rec_h4["welch_p_two_sided"])})
    exploratory_tests.append(rec_h4)

    # ------------------------------------------------- EXPLORATORY: max excursion
    mfe_days, mfe_excl = mfe_mae_day_stats(raw1m)
    mfe_ev = mfe_days[mfe_days["date"].isin(fred_event_dates)]
    mfe_ctrl = bspes._matched_control(mfe_days, fred_event_dates, set(mfe_ev["weekday"]))
    for col, label in (("mfe_up", "max_upside_excursion"),
                        ("mfe_down", "max_downside_excursion"),
                        ("total_excursion", "total_excursion_whipsaw_magnitude")):
        vals_ev = mfe_ev[col].abs().tolist() if col == "mfe_down" else mfe_ev[col].tolist()
        vals_ctrl = mfe_ctrl[col].abs().tolist() if col == "mfe_down" else mfe_ctrl[col].tolist()
        rec = bspes._welch(vals_ev, vals_ctrl)
        rec.update({"family": "max_excursion_08_30_to_08_45", "instrument": PRIMARY_SYMBOL,
                    "metric": label, "window_et": "08:30-08:45"})
        exploratory_tests.append(rec)
    ev_ratio = (mfe_ev["total_excursion"].mean() / mfe_ev["net_move"].abs().mean()
                if len(mfe_ev) and mfe_ev["net_move"].abs().mean() != 0 else None)
    ctrl_ratio = (mfe_ctrl["total_excursion"].mean() / mfe_ctrl["net_move"].abs().mean()
                  if len(mfe_ctrl) and mfe_ctrl["net_move"].abs().mean() != 0 else None)
    whipsaw_summary = {
        "note": "ratio of MEAN total_excursion to MEAN |net_move| (population-level, "
                "not per-day -- per-day ratios blow up when net_move is near zero). "
                "Near 1.0 means the window mostly moves in a straight line; well above "
                "1.0 means it thrashes both ways before settling, information that "
                "|return| alone (endpoint-to-endpoint) cannot see.",
        "event_n_days": len(mfe_ev), "event_whipsaw_ratio": ev_ratio,
        "control_n_days": len(mfe_ctrl), "control_whipsaw_ratio": ctrl_ratio,
    }

    padj = mes._bh_adjust([t["welch_p_two_sided"] for t in exploratory_tests])
    n_tested = sum(1 for t in exploratory_tests if t["welch_p_two_sided"] is not None)
    n_sig_raw = sum(1 for t in exploratory_tests
                     if t["welch_p_two_sided"] is not None and t["welch_p_two_sided"] < 0.05)
    n_sig_bh = 0
    for t, p in zip(exploratory_tests, padj):
        t["welch_p_bh"] = p
        if p is not None and p < 0.05:
            n_sig_bh += 1

    # ---------------------------------------------------- FOMC ARM (separate)
    try:
        fomc_events = fomc_mod.load_fomc_events()
        fomc_days, fomc_impulse_excl, fomc_cont_excl = \
            impulse_continuation_day_stats(raw1m, IMPULSE_1400, CONTINUATION_1401_1404)
        all_fomc_dates = {e["date"] for e in fomc_events}
        scheduled_fomc_dates = {e["date"] for e in fomc_events if not e.get("unscheduled")}
        day_dates = set(fomc_days["date"])
        scheduled_in_coverage = scheduled_fomc_dates & day_dates
        fomc_exclusion_dates = all_fomc_dates | fred_event_dates
        fomc_arm = continuation_test(
            fomc_days, scheduled_in_coverage, fomc_exclusion_dates,
            hypothesis=("sign(r_14:00-14:01) * r_14:01-14:05 is different on SCHEDULED "
                        "FOMC decision days than on matched non-event days"),
            window_et="impulse 14:00-14:01, continuation 14:01-14:05")
        fomc_arm["note"] = (f"much thinner n than the 08:30 primary "
                             f"(scheduled meetings in bar coverage: {len(scheduled_in_coverage)})")
        fomc_days.to_csv(FOMC_DAYS_CSV, index=False)
    except (EventStudyError, bspes.BarDataError) as e:
        fomc_arm = {"skipped": str(e)}
        fomc_impulse_excl = fomc_cont_excl = []

    # --------------------------------------------------------------- write
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    primary_days.to_csv(PRIMARY_DAYS_CSV, index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "follows_up_on": "data/daytrade/event_study/spy_macro_decay_study_summary.json "
                          "(splash real+causal+magnitude-only, established, not re-derived)",
        "primary_symbol": PRIMARY_SYMBOL,
        "bar_source_1m": "data/daytrade/bars_premarket_1m (Alpaca SIP, OHLCV only -- "
                          "no bid/ask/midpoint field, see MICROSTRUCTURE_CAVEAT)",
        "bar_source_5m": "data/daytrade/bars_premarket (Alpaca SIP, OHLCV only)",
        "MICROSTRUCTURE_CAVEAT": (
            "Bars carry Open/High/Low/Close/Volume only -- no bid/ask or midpoint field "
            "exists in this cache, so no midpoint/VWAP alternative to close-to-close "
            "could be computed. Close-to-close on SIP consolidated-tape trade prints is "
            "reported. SPY's tight spread mitigates but does not eliminate bid-ask-bounce "
            "risk in a conditional-reversion test; see EXPLORATORY_LARGE_IMPULSE for the "
            "partial check (bounce dominates small moves, not large ones)."),
        "events_found_in_window": len(fred_events),
        "primary_complete_days": len(complete_primary),
        "primary_impulse_excluded_days": len(primary_impulse_excl),
        "primary_continuation_excluded_days": len(primary_cont_excl),
        "PRIMARY": primary,
        "EXPLORATORY_LARGE_IMPULSE_NO_INFERENTIAL_WEIGHT": large_impulse,
        "EXPLORATORY_HORIZON_SWEEP_AND_MAX_EXCURSION_NO_INFERENTIAL_WEIGHT": {
            "tests": exploratory_tests,
            "whipsaw_ratio_summary": whipsaw_summary,
            "multiple_testing": {
                "tests_run": n_tested,
                "significant_raw_p_lt_0.05": n_sig_raw,
                "expected_by_chance_at_alpha_0.05": round(n_tested * 0.05, 1),
                "significant_after_bh_correction": n_sig_bh,
            },
        },
        "FOMC_ARM_SEPARATE_NOT_POWERED_LIKE_PRIMARY": fomc_arm,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=1, default=str))

    print("=" * 72)
    diff = primary.get("diff_event_minus_control")
    p = primary.get("welch_p_two_sided")
    print(f"  PRIMARY [{primary.get('interpretation')}]")
    print(f"    event_n={primary.get('event_n')}, control_n={primary.get('control_n')}, "
          f"diff={diff}, welch_p_two_sided={p}")
    if isinstance(fomc_arm, dict) and "hypothesis" in fomc_arm:
        print(f"  FOMC ARM [{fomc_arm.get('interpretation')}] "
              f"event_n={fomc_arm.get('event_n')}, control_n={fomc_arm.get('control_n')}")
    print(f"  wrote {SUMMARY_JSON}")
    print(f"  wrote {PRIMARY_DAYS_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
