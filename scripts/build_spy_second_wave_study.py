#!/usr/bin/env python3
"""SPY SECOND WAVE STUDY -- does a slower, VWAP-confirmed institutional
footprint exist AFTER the impulse decays, on a horizon a retail clock can
actually act on.

WHAT IS ALREADY ESTABLISHED, NOT RE-DERIVED HERE
--------------------------------------------------
`scripts/build_spy_macro_decay_study.py`: the impulse is real and causal but
decays fast -- 4.8x control at 08:30, ~2x at 08:31, ~1.5x still at 08:40.
`scripts/build_spy_splash_continuation_study.py`: SHORT-horizon conditional
continuation from the raw 08:30 impulse SIGN into 08:31-08:35 is NULL
(p=0.932), and null again restricted to large impulses (p=0.972). Seven
independent nulls total. NONE of that is re-run here.

THE IDEA, AND WHY THE EXISTING NULL DOES NOT ALREADY ANSWER IT
------------------------------------------------------------------
A retail stack cannot win the 08:30:00 tick -- headline to LLM to parse to
order is 500ms-2s, and HFTs have already consumed the liquidity at that
horizon. But large institutions cannot dump size in one minute without
destroying the book; TWAP/VWAP execution algorithms slice it over the
following HOURS. If real, that produces a second, slower, directional
footprint -- detectable on an horizon of hours, not milliseconds, which a
retail stack CAN act on.

The splash-continuation study's null is the nearest prior and does NOT
settle this: it conditioned on the raw impulse SIGN (08:30-08:31, noisy,
possibly bid-ask bounce dominated per that study's own caveat) and measured
a FOUR-MINUTE horizon (08:31-08:35). This study conditions on a DIFFERENT
signal -- whether price is above or below the volume-weighted average price
of the impulse window, a level that only a sustained one-directional flow
can hold -- and measures a horizon two orders of magnitude longer (roughly
3.5 hours, to midday). Different signal, different horizon. The prior null
is cited, not dismissed, and not treated as already having closed this
question either way.

THE PRE-REGISTERED PRIMARY -- ONE TEST, FIXED BEFORE ANY NUMBER IS COMPUTED
--------------------------------------------------------------------------
On SPY scheduled-release days (the same six FRED-tracked release types
`build_macro_event_study.py` / `build_spy_premarket_event_study.py` use):

    1. Compute VWAP over the 08:30-08:35 ET window from the five 1-minute
       bars 08:30 through 08:34 (bar-starts), volume-weighted on each bar's
       typical price (High+Low+Close)/3 -- the standard intraday VWAP
       approximation from OHLCV bars (no tick/trade-level data exists in
       this cache to compute a truer VWAP).
    2. At 08:36 ET, classify the day ABOVE (price at the 08:36 bar's Open
       is greater than that VWAP) or BELOW (less than). A day landing
       exactly on the VWAP (float equality) is excluded, not silently
       assigned a side.
    3. PRIMARY STATISTIC, per day: sign(above_vwap) * return from the
       08:36 bar's Open to the 12:00 bar's Open (i.e. the Close of the
       11:59 bar) -- the same "window Open of first bar to Close of last
       bar" convention every other study in this repo's event-study family
       already uses.
    4. Compare that per-day statistic, event days vs matched non-event
       days (same weekday, excluding any day that is a scheduled release
       for ANY of the six types) -- Welch, ONE-SIDED (event > control; the
       hypothesis is specifically that an incremental, calendar-knowable
       directional footprint exists on event days, not merely that it
       differs in some unspecified direction). No multiple-testing penalty
       -- it is the only member of its family.
    5. A required second control: the per-day statistic is fixed and does
       not depend on which days are LABELLED "event" -- event-day labels
       are reshuffled 2000 times (without replacement, same event count,
       drawn from the full day population) and the mean recomputed each
       time. The observed event-arm mean is reported beside that null
       distribution with a one-sided p-value, the same discipline
       `build_spy_bracket_harvest_study.py` already uses for its own
       permutation control.

Positive and exceeding BOTH controls => an institutional second wave exists
and is detectable on a retail clock. Null on either control => it does not,
at least not detectably this way. A null is a real, reportable result.

12:00 ET is PRE-REGISTERED as roughly the midpoint of the 2-4 hour window
the hypothesis itself names -- NOT chosen after looking at any number, and
NOT swept. Other horizons are EXPLORATORY, labelled as such, BH-corrected
within their own family, and may never be promoted to primary after the
fact -- this repo already measured a K=396 selection premium of +4.89
R/trade from exactly that kind of after-the-fact horizon search
(`build_spy_bracket_harvest_study.py`'s docstring), and this study does not
repeat it.

COSTS -- REUSES THE BRACKET STUDY'S MODEL, STATES WHAT DIFFERS
--------------------------------------------------------------
`build_spy_bracket_harvest_study.py`'s cost model charges a STOP order's
entry (spread + stop-through-fill, 3.0bp) because that study's entry is a
resting stop order triggered by price touching a trigger level on a fast
tape. This study's entry is NOT a stop order -- it is a scheduled decision,
made and executed at a known clock time (08:36) using only information
already fully observed by that time (the 08:30-08:35 VWAP and the 08:36
price), the same way a scheduled market order is placed. There is no
"fills through its trigger on a fast tape" component to charge, because
there is no trigger; the stop-through-bp term is REUSED conceptually (it is
this study's justification for excluding it, not a silently different
number) and set to zero here.
`SPREAD_BP = 1.0` is reused UNCHANGED from the bracket study
(`build_spy_bracket_harvest_study.SPREAD_BP`) -- same instrument, same
pessimistic-end widened-spread assumption for the thin-liquidity premarket
window. Both entry (08:36) and exit (12:00) are scheduled market orders, so
each pays `SPREAD_BP` only: `ENTRY_COST_BP = EXIT_COST_BP = 1.0bp`, round
trip `2.0bp` (vs the bracket study's 4.0bp round trip for a stop entry).
Both GROSS and NET are reported; NET is the real number.

NO LOOK-AHEAD
-------------
The VWAP uses only the five 1-minute bars strictly inside 08:30-08:35. The
classification at 08:36 uses only the 08:36 bar's OWN Open (the first tick
of that minute) compared against a VWAP already fully computed from bars
that closed before it. The continuation return starts at that same 08:36
Open. Nothing here reads a bar timestamped after a decision to make that
decision.

FAIL LOUD
---------
A day missing any of the five 08:30-08:34 VWAP bars, the 08:36 entry bar,
or ANY 1-minute bar in the 08:36-11:59 continuation span (204 minutes) is
EXCLUDED with a reason, never interpolated -- the same discipline every
other bar-loading path in this repo already holds itself to. Each of the
three completeness checks is tracked independently.

REUSE
-----
The 1-minute pre-market/RTH cache and its loader
(`build_spy_macro_decay_study._load_raw_bars_1m`, `CACHE_1M` -- already
verified to span the full 04:00-19:59 ET day, not premarket-only, so this
study needs no new fetch), the 5-minute cache's window/control/statistics
helpers (`build_spy_premarket_event_study._window_bars`, `_matched_control`,
`_welch`), the FRED event calendar and BH correction
(`build_macro_event_study.build_event_calendar`, `_bh_adjust`, `_ci95`), the
FOMC scheduled calendar (`build_fomc_splash_event_study.load_fomc_events`),
and the permutation-null machinery
(`build_spy_bracket_harvest_study.permutation_null`, `_finish_permutation`,
its `SPREAD_BP` constant) are all imported and used as-is -- none of that
machinery is reimplemented here.

MEASUREMENT / SIMULATION ONLY
------------------------------
No order is placed, nothing is wired to a broker, no live signal is built.
`data/proof/` and `SEALS.json` are never touched by this module.
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
import build_macro_event_study as mes                                # noqa: E402
from build_macro_event_study import EventStudyError                  # noqa: E402
import build_spy_premarket_event_study as bspes                      # noqa: E402
import build_spy_macro_decay_study as decay_mod                      # noqa: E402
import build_spy_bracket_harvest_study as bracket_mod                # noqa: E402
import build_fomc_splash_event_study as fomc_mod                     # noqa: E402
from bars import BarDataError                                        # noqa: E402

OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
SUMMARY_JSON = OUT_DIR / "spy_second_wave_study_summary.json"
PRIMARY_DAYS_CSV = OUT_DIR / "spy_second_wave_primary_days.csv"

PRIMARY_SYMBOL = "SPY"

VWAP_MINUTES = [f"08:{m:02d}" for m in range(30, 35)]     # 08:30..08:34
ENTRY_MINUTE = "08:36"
HOLD_CHECK_MINUTE = "08:40"                                 # exploratory
PRIMARY_EXIT_MINUTE = "11:59"                                # -> 12:00 open

# EXPLORATORY horizon sweep -- last bar-start whose Close is "at" the label.
HORIZON_SWEEP = [
    ("10:00", "09:59"),
    ("12:00", "11:59"),   # same as primary, included for the sweep table
    ("14:00", "13:59"),
    ("RTH_close_15:55", "15:55"),
]

VOLUME_WINDOW_MINUTES = [f"08:{m:02d}" for m in range(36, 60)] + \
                         [f"09:{m:02d}" for m in range(0, 30)]  # 08:36-09:30

# Costs -- reused from the bracket study; see module docstring for what
# differs (no stop-through component: this is a scheduled entry, not a
# resting stop order).
SPREAD_BP = bracket_mod.SPREAD_BP
ENTRY_COST_BP = SPREAD_BP
EXIT_COST_BP = SPREAD_BP
ROUND_TRIP_COST_BP = ENTRY_COST_BP + EXIT_COST_BP

N_PERMUTATIONS = bracket_mod.N_PERMUTATIONS
PERMUTATION_SEED = bracket_mod.PERMUTATION_SEED


# --------------------------------------------------------------- statistics

def _typical_price(row: pd.Series) -> float:
    return (float(row["High"]) + float(row["Low"]) + float(row["Close"])) / 3.0


def _vwap(window: pd.DataFrame) -> float | None:
    vol = window["Volume"].astype(float)
    if vol.sum() <= 0:
        return None
    tp = window.apply(_typical_price, axis=1)
    return float((tp * vol).sum() / vol.sum())


def _sign(x: float) -> float:
    if x > 0:
        return 1.0
    if x < 0:
        return -1.0
    return 0.0


# ------------------------------------------------------------- day builder

def second_wave_day_stats(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[dict]]]:
    """One row per calendar day whose VWAP bars, entry bar, and the full
    08:36-11:59 continuation span are ALL present in the 1-minute cache.
    Also carries the 08:40 hold-check price and the 08:36-09:30 volume sum
    (both exploratory, independently gated -- a day can be usable for the
    primary and NaN for either exploratory field).

    Returns the day table and a dict of three INDEPENDENT exclusion lists
    (`vwap`, `entry`, `continuation`) -- never merged, never interpolated.
    """
    rows: list[dict] = []
    excluded: dict[str, list[dict]] = {"vwap": [], "entry": [], "continuation": []}

    for day, chunk in raw.groupby(raw.index.date):
        d = day.isoformat()
        present = set(chunk.index.strftime("%H:%M"))
        row: dict = {"date": d, "weekday": pd.Timestamp(day).strftime("%A")}

        vwap_ok = all(m in present for m in VWAP_MINUTES)
        if not vwap_ok:
            missing = [m for m in VWAP_MINUTES if m not in present]
            excluded["vwap"].append({"date": d,
                                      "reason": f"missing 1m bar(s) in 08:30-08:34 VWAP "
                                                f"window (first at {missing[0]})"})

        entry_ok = ENTRY_MINUTE in present
        if not entry_ok:
            excluded["entry"].append({"date": d, "reason": f"missing {ENTRY_MINUTE} 1m entry bar"})

        cont_minutes = _minutes_between(ENTRY_MINUTE, PRIMARY_EXIT_MINUTE)
        missing_cont = [m for m in cont_minutes if m not in present]
        cont_ok = not missing_cont
        if not cont_ok:
            excluded["continuation"].append({
                "date": d,
                "reason": f"{len(missing_cont)} missing 1m bar(s) in "
                          f"{ENTRY_MINUTE}-{PRIMARY_EXIT_MINUTE} continuation span "
                          f"(first at {missing_cont[0]})"})

        row["vwap_0830_0834"] = float("nan")
        row["entry_price"] = float("nan")
        row["exit_price_1200"] = float("nan")
        row["above_vwap"] = float("nan")
        row["hold_price_0840"] = float("nan")
        row["volume_0836_0930"] = float("nan")

        if vwap_ok:
            vwap_window = bspes._window_bars(chunk, VWAP_MINUTES[0], VWAP_MINUTES[-1])
            row["vwap_0830_0834"] = _vwap(vwap_window)

        if entry_ok:
            entry_bar = bspes._window_bars(chunk, ENTRY_MINUTE, ENTRY_MINUTE)
            row["entry_price"] = float(entry_bar["Open"].iloc[0])

        if vwap_ok and entry_ok and row["vwap_0830_0834"] is not None:
            diff = row["entry_price"] - row["vwap_0830_0834"]
            row["above_vwap"] = _sign(diff)

        if entry_ok and cont_ok:
            cont_window = bspes._window_bars(chunk, ENTRY_MINUTE, PRIMARY_EXIT_MINUTE)
            row["exit_price_1200"] = float(cont_window["Close"].iloc[-1])

        if HOLD_CHECK_MINUTE in present:
            hold_bar = bspes._window_bars(chunk, HOLD_CHECK_MINUTE, HOLD_CHECK_MINUTE)
            row["hold_price_0840"] = float(hold_bar["Open"].iloc[0])

        vol_minutes_present = [m for m in VOLUME_WINDOW_MINUTES if m in present]
        if vol_minutes_present:
            vol_window = bspes._window_bars(chunk, VOLUME_WINDOW_MINUTES[0],
                                             VOLUME_WINDOW_MINUTES[-1])
            row["volume_0836_0930"] = float(vol_window["Volume"].sum())

        rows.append(row)

    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, excluded


def _minutes_between(start_hhmm: str, end_hhmm_inclusive: str) -> list[str]:
    sh, sm = (int(x) for x in start_hhmm.split(":"))
    eh, em = (int(x) for x in end_hhmm_inclusive.split(":"))
    start_total = sh * 60 + sm
    end_total = eh * 60 + em
    return [f"{t // 60:02d}:{t % 60:02d}" for t in range(start_total, end_total + 1)]


# ------------------------------------------------------------------ primary

def _complete_primary(days: pd.DataFrame) -> pd.DataFrame:
    ok = days["above_vwap"].isin([1.0, -1.0]) & days["exit_price_1200"].notna() \
        & days["entry_price"].notna()
    return days[ok].copy()


def primary_stat_series(days: pd.DataFrame) -> pd.DataFrame:
    complete = _complete_primary(days)
    complete = complete.copy()
    complete["gross_ret"] = complete["above_vwap"] * \
        (complete["exit_price_1200"] / complete["entry_price"] - 1.0)
    complete["net_ret"] = complete["gross_ret"] - (ROUND_TRIP_COST_BP / 1e4)
    return complete


def primary_test(days: pd.DataFrame, events: list[dict]) -> dict:
    """The one pre-registered primary: Welch (one-sided, event > control) on
    the NET signed-return statistic, plus the permutation-null second
    control reused from the bracket study."""
    all_event_dates = {e["date"] for e in events}
    complete = primary_stat_series(days)
    day_dates = set(complete["date"])
    dropped = sorted(all_event_dates - day_dates)

    ev = complete[complete["date"].isin(all_event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = bspes._matched_control(complete, all_event_dates, weekdays)

    rec = bspes._welch(ev["net_ret"].tolist(), ctrl["net_ret"].tolist())
    rec["hypothesis"] = (
        "sign(price_08:36 vs VWAP_08:30-08:35) * net_return(08:36 -> 12:00) is larger "
        "on SPY scheduled-release days than on matched non-event days")
    rec["instrument"] = PRIMARY_SYMBOL
    rec["window_et"] = "classify 08:36, hold to 12:00"
    rec["metric"] = "sign(above_vwap) * net_return, cost-adjusted"
    rec["cost_model_bp"] = {"entry_cost_bp": ENTRY_COST_BP, "exit_cost_bp": EXIT_COST_BP,
                             "round_trip_cost_bp": ROUND_TRIP_COST_BP}
    rec["gross_event_mean"] = float(ev["gross_ret"].mean()) if len(ev) else None
    rec["gross_control_mean"] = float(ctrl["gross_ret"].mean()) if len(ctrl) else None
    rec["event_dates_matched"] = len(ev)
    rec["event_dates_dropped_incomplete_window"] = dropped
    rec["control_weekdays_matched_against"] = sorted(weekdays)
    if rec["welch_t"] is not None:
        p_two = rec["welch_p_two_sided"]
        rec["welch_p_one_sided_event_gt_control"] = (
            p_two / 2 if rec["welch_t"] > 0 else 1 - p_two / 2)
    else:
        rec["welch_p_one_sided_event_gt_control"] = None

    perm = bracket_mod.permutation_null(
        complete.rename(columns={"net_ret": "net_bp"}), len(ev),
        n_perm=N_PERMUTATIONS, seed=PERMUTATION_SEED)
    perm = bracket_mod._finish_permutation(perm, rec["event_mean"])
    rec["permutation_null"] = perm

    p_one = rec["welch_p_one_sided_event_gt_control"]
    p_perm = perm.get("p_value_one_sided_event_gt_random")
    if rec["diff_event_minus_control"] is None or p_one is None or p_perm is None:
        rec["interpretation"] = "INSUFFICIENT_DATA"
    elif rec["diff_event_minus_control"] > 0 and p_one < 0.05 and p_perm < 0.05:
        rec["interpretation"] = ("SECOND WAVE DETECTED -- event days exceed both the "
                                  "weekday-matched control and the permutation null")
    else:
        rec["interpretation"] = ("NULL -- no detectable second wave by this construction "
                                  "(does not clear both controls)")
    return rec


# --------------------------------------------------------------- exploratory

def horizon_sweep_from_raw(raw: pd.DataFrame, base_days: pd.DataFrame,
                            events: list[dict]) -> dict:
    """EXPLORATORY -- same classification (08:36 VWAP break), swept exit
    horizons. Never promoted to primary. Each horizon has its OWN
    completeness gate, independent of the primary's. BH-corrected within
    this family."""
    all_event_dates = {e["date"] for e in events}
    base = base_days[base_days["above_vwap"].isin([1.0, -1.0]) &
                      base_days["entry_price"].notna()].copy()
    sign_by_date = base.set_index("date")["above_vwap"]
    entry_by_date = base.set_index("date")["entry_price"]

    tests: list[dict] = []
    for label, last_minute in HORIZON_SWEEP:
        cont_minutes = _minutes_between(ENTRY_MINUTE, last_minute)
        rows = []
        excluded = 0
        for day, chunk in raw.groupby(raw.index.date):
            d = day.isoformat()
            if d not in sign_by_date.index or sign_by_date[d] not in (1.0, -1.0):
                continue
            present = set(chunk.index.strftime("%H:%M"))
            if any(m not in present for m in cont_minutes):
                excluded += 1
                continue
            window = bspes._window_bars(chunk, ENTRY_MINUTE, last_minute)
            exit_price = float(window["Close"].iloc[-1])
            gross = sign_by_date[d] * (exit_price / entry_by_date[d] - 1.0)
            net = gross - (ROUND_TRIP_COST_BP / 1e4)
            rows.append({"date": d, "weekday": pd.Timestamp(day).strftime("%A"), "net_ret": net})
        df = pd.DataFrame(rows)
        if df.empty:
            tests.append({"family": "horizon_sweep", "label": label, "n": 0,
                          "excluded_days": excluded})
            continue
        ev = df[df["date"].isin(all_event_dates)]
        weekdays = set(ev["weekday"])
        ctrl = bspes._matched_control(df, all_event_dates, weekdays)
        rec = bspes._welch(ev["net_ret"].tolist(), ctrl["net_ret"].tolist())
        rec.update({"family": "horizon_sweep", "label": label, "instrument": PRIMARY_SYMBOL,
                    "excluded_days_this_horizon": excluded})
        tests.append(rec)

    padj = mes._bh_adjust([t.get("welch_p_two_sided") for t in tests])
    n_tested = sum(1 for t in tests if t.get("welch_p_two_sided") is not None)
    n_sig_raw = sum(1 for t in tests
                     if t.get("welch_p_two_sided") is not None and t["welch_p_two_sided"] < 0.05)
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


def hold_requirement(days: pd.DataFrame, events: list[dict]) -> dict:
    """EXPLORATORY -- does requiring the VWAP break to HOLD (same sign at
    both 08:36 and 08:40) sharpen or weaken the primary statistic."""
    all_event_dates = {e["date"] for e in events}
    complete = primary_stat_series(days)
    complete = complete[complete["hold_price_0840"].notna() &
                         complete["vwap_0830_0834"].notna()].copy()
    complete["sign_0840"] = (complete["hold_price_0840"] - complete["vwap_0830_0834"]).apply(_sign)
    held = complete[complete["sign_0840"] == complete["above_vwap"]]
    not_held = complete[complete["sign_0840"] != complete["above_vwap"]]

    def _arm(df: pd.DataFrame, label: str) -> dict:
        ev = df[df["date"].isin(all_event_dates)]
        weekdays = set(ev["weekday"])
        ctrl = bspes._matched_control(df, all_event_dates, weekdays)
        rec = bspes._welch(ev["net_ret"].tolist(), ctrl["net_ret"].tolist())
        rec.update({"family": "hold_requirement", "label": label})
        return rec

    return {
        "held_0836_and_0840_same_side": _arm(held, "held"),
        "did_not_hold": _arm(not_held, "did_not_hold"),
        "held_n": len(held), "not_held_n": len(not_held),
    }


def volume_confirmation(days: pd.DataFrame, events: list[dict]) -> dict:
    """EXPLORATORY -- is 08:36-09:30 volume elevated on event days vs
    control, and does the primary effect concentrate on high-volume event
    days. Volume is the direct observable for the institutional-routing
    claim; if the return effect exists WITHOUT elevated volume, that claim
    is wrong even if the return number is positive."""
    all_event_dates = {e["date"] for e in events}
    complete = primary_stat_series(days)
    complete = complete[complete["volume_0836_0930"].notna()].copy()

    ev_all = complete[complete["date"].isin(all_event_dates)]
    weekdays_all = set(ev_all["weekday"])
    ctrl_all = bspes._matched_control(complete, all_event_dates, weekdays_all)
    vol_test = bspes._welch(ev_all["volume_0836_0930"].tolist(),
                             ctrl_all["volume_0836_0930"].tolist())
    vol_test.update({"family": "volume_confirmation",
                      "metric": "sum(Volume), 08:36-09:30"})

    split: dict = {"n_event_days": len(ev_all)}
    if len(ev_all) >= 8:
        median_vol = ev_all["volume_0836_0930"].median()
        high = ev_all[ev_all["volume_0836_0930"] >= median_vol]
        low = ev_all[ev_all["volume_0836_0930"] < median_vol]
        high_vs_low = bspes._welch(high["net_ret"].tolist(), low["net_ret"].tolist())
        high_vs_low.update({"family": "volume_confirmation",
                             "comparison": "return stat, high-volume half of event days "
                                           "vs low-volume half of event days"})
        split["high_vs_low_volume_event_days_return"] = high_vs_low
        split["median_volume_0836_0930"] = float(median_vol)
        split["high_n"] = len(high)
        split["low_n"] = len(low)

    return {"event_vs_control_volume": vol_test, "return_by_volume_split": split}


def fomc_arm(raw: pd.DataFrame, fred_event_dates: set[str]) -> dict:
    """EXPLORATORY, thin -- same construction, 14:00 FOMC statement instead
    of 08:30, held to the RTH close (15:55) since the 2-4h primary horizon
    does not fit inside the trading day starting from 14:00."""
    try:
        fomc_events = fomc_mod.load_fomc_events()
    except EventStudyError as e:
        return {"skipped": str(e)}
    scheduled_dates = {e["date"] for e in fomc_events if not e.get("unscheduled")}
    all_fomc_dates = {e["date"] for e in fomc_events}
    exclusion_dates = all_fomc_dates | fred_event_dates

    vwap_minutes = [f"14:{m:02d}" for m in range(0, 5)]     # 14:00..14:04
    entry_minute = "14:05"
    exit_minute = "15:55"

    rows = []
    for day, chunk in raw.groupby(raw.index.date):
        d = day.isoformat()
        present = set(chunk.index.strftime("%H:%M"))
        if not all(m in present for m in vwap_minutes) or entry_minute not in present:
            continue
        cont_minutes = _minutes_between(entry_minute, exit_minute)
        if any(m not in present for m in cont_minutes):
            continue
        vwap_window = bspes._window_bars(chunk, vwap_minutes[0], vwap_minutes[-1])
        vwap = _vwap(vwap_window)
        if vwap is None:
            continue
        entry_bar = bspes._window_bars(chunk, entry_minute, entry_minute)
        entry_price = float(entry_bar["Open"].iloc[0])
        sign = _sign(entry_price - vwap)
        if sign == 0.0:
            continue
        cont_window = bspes._window_bars(chunk, entry_minute, exit_minute)
        exit_price = float(cont_window["Close"].iloc[-1])
        gross = sign * (exit_price / entry_price - 1.0)
        net = gross - (ROUND_TRIP_COST_BP / 1e4)
        rows.append({"date": d, "weekday": pd.Timestamp(day).strftime("%A"), "net_ret": net})

    df = pd.DataFrame(rows)
    if df.empty:
        return {"skipped": "no complete FOMC-arm days in the 1m cache"}

    ev = df[df["date"].isin(scheduled_dates)]
    ctrl = df[~df["date"].isin(exclusion_dates)]
    weekdays = set(ev["weekday"])
    ctrl = ctrl[ctrl["weekday"].isin(weekdays)]
    rec = bspes._welch(ev["net_ret"].tolist(), ctrl["net_ret"].tolist())
    rec.update({"family": "fomc_arm_thin", "instrument": PRIMARY_SYMBOL,
                "window_et": "classify 14:05 vs 14:00-14:04 VWAP, hold to RTH close 15:55",
                "note": f"thin sample -- {len(ev)} scheduled FOMC days with a complete window"})
    return rec


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    a = ap.parse_args(argv)

    load_dotenv()
    import os
    key = os.environ.get("FRED_API_KEY")
    if not key:
        print("  REFUSED: FRED_API_KEY missing -- cannot build the FRED event calendar")
        return 1

    try:
        raw1m = decay_mod._load_raw_bars_1m(PRIMARY_SYMBOL)
    except BarDataError as e:
        print(f"  REFUSED: {e} -- run build_spy_macro_decay_study.py --refresh first")
        return 1
    if raw1m.empty:
        print("  REFUSED: empty 1m cache")
        return 1

    days, excluded = second_wave_day_stats(raw1m)
    complete = _complete_primary(days)
    if complete.empty:
        print("  REFUSED: no complete SPY 08:36-12:00 second-wave days")
        return 1

    start = date.fromisoformat(complete["date"].iloc[0])
    end_d = date.fromisoformat(complete["date"].iloc[-1])
    try:
        events = mes.build_event_calendar(key, start=start, end=end_d)
    except EventStudyError as e:
        print(f"  REFUSED: {e}")
        return 1
    fred_event_dates = {e["date"] for e in events}

    primary = primary_test(days, events)

    horizon = horizon_sweep_from_raw(raw1m, days, events)
    hold = hold_requirement(days, events)
    volume = volume_confirmation(days, events)
    fomc = fomc_arm(raw1m, fred_event_dates)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    primary_stat_series(days).to_csv(PRIMARY_DAYS_CSV, index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "follows_up_on": [
            "data/daytrade/event_study/spy_macro_decay_study_summary.json "
            "(impulse real+causal, decays to ~1.5x control by 08:40)",
            "data/daytrade/event_study/spy_splash_continuation_study_summary.json "
            "(short-horizon raw-sign continuation is NULL, p=0.932; nearest prior, "
            "different signal and horizon, not re-run and not treated as already "
            "answering this)",
        ],
        "primary_symbol": PRIMARY_SYMBOL,
        "bar_source_1m": "data/daytrade/bars_premarket_1m (Alpaca SIP, full-day OHLCV, "
                          "verified to span 04:00-19:59 ET -- no new fetch required)",
        "cost_model_bp": {
            "spread_bp": SPREAD_BP, "entry_cost_bp": ENTRY_COST_BP,
            "exit_cost_bp": EXIT_COST_BP, "round_trip_cost_bp": ROUND_TRIP_COST_BP,
            "note": "reused from build_spy_bracket_harvest_study.SPREAD_BP; no "
                    "stop-through component -- entry and exit are both scheduled "
                    "market decisions at known clock times, not resting stop orders",
        },
        "events_found_in_window": len(events),
        "primary_complete_days": len(complete),
        "vwap_window_excluded_days": len(excluded["vwap"]),
        "vwap_window_excluded_reasons_sample": excluded["vwap"][:5],
        "entry_bar_excluded_days": len(excluded["entry"]),
        "entry_bar_excluded_reasons_sample": excluded["entry"][:5],
        "continuation_excluded_days": len(excluded["continuation"]),
        "continuation_excluded_reasons_sample": excluded["continuation"][:5],
        "PRIMARY": primary,
        "EXPLORATORY_HORIZON_SWEEP_NO_INFERENTIAL_WEIGHT": horizon,
        "EXPLORATORY_HOLD_REQUIREMENT_NO_INFERENTIAL_WEIGHT": hold,
        "EXPLORATORY_VOLUME_CONFIRMATION_NO_INFERENTIAL_WEIGHT": volume,
        "EXPLORATORY_FOMC_ARM_THIN_NO_INFERENTIAL_WEIGHT": fomc,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=1, default=str))

    print("=" * 72)
    print(f"  PRIMARY [{primary.get('interpretation')}]")
    print(f"    event_n={primary.get('event_n')}, control_n={primary.get('control_n')}, "
          f"diff(net)={primary.get('diff_event_minus_control')}, "
          f"welch_p(one-sided)={primary.get('welch_p_one_sided_event_gt_control')}, "
          f"perm_p={primary.get('permutation_null', {}).get('p_value_one_sided_event_gt_random')}")
    print(f"  wrote {SUMMARY_JSON}")
    print(f"  wrote {PRIMARY_DAYS_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
