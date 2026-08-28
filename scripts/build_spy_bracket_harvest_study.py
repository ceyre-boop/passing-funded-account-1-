#!/usr/bin/env python3
"""SPY BRACKET HARVEST STUDY -- can the splash's MAGNITUDE be harvested with
DIRECTIONAL instruments, given DIRECTION itself does not exist.

WHAT IS ALREADY ESTABLISHED, NOT RE-DERIVED HERE
--------------------------------------------------
`scripts/build_spy_macro_decay_study.py`: the splash is real and causal
(SPY |return| 08:30-08:35 vs control, d=0.571, p=6.2e-29; placebo windows
run the OPPOSITE direction) and its shape is measured (4.8x control at
08:30, ~2x at 08:31, ~1.5x by 08:40; FOMC 14:00 bigger, d=1.309).
`scripts/build_spy_whipsaw_event_study.py`: the path barely degrades
(whipsaw ratio +13%, d=0.10) while magnitude roughly doubles -- an event-day
move mostly reprices, it does not thrash. `scripts/
build_spy_splash_continuation_study.py`: DIRECTION does not exist -- seven
independent nulls, including conditional continuation (p=0.932) and
large-impulse-only (p=0.972). NONE of that is re-run here.

THE QUESTION THIS STUDY ANSWERS
---------------------------------
You know WHEN and HOW MUCH, never WHICH WAY. Can a both-sides bracket
placed before the release harvest the magnitude anyway -- one side fills on
the impulse and rides it, the other is cancelled? This is the natural
terminal measurement of the programme, not a new hypothesis.

ONE PRE-REGISTERED PARAMETERIZATION -- NOT SEARCHED
---------------------------------------------------------
`stockfish_tune` measured a K=396 selection premium of +4.89 R/trade from
sweeping configs and reporting the best. Sweeping bracket widths or hold
times here and reporting the winner would manufacture the same kind of
worthless apparent edge. Exactly ONE parameterization is used, derived from
already-measured quantities, fixed before any P&L is computed:

  - ENTRY REFERENCE: SPY price at the 08:29 1-minute bar's close (the last
    bar fully before the 08:30 release; FOMC arm uses the 13:59 close
    before the 14:00 statement).
  - BRACKET WIDTH: the 75th percentile of `range` (up_exc + down_exc, the
    SAME open-anchored metric `build_spy_whipsaw_event_study.py` already
    defines and computes) over the 08:30-08:45 window, on CONTROL days
    ONLY (non-FRED-event days for the primary; non-FRED AND non-FOMC days
    for the FOMC arm). Computed ONCE from the control population, stated in
    the output, then used unchanged for every event day. Note the anchor:
    `range` is anchored on the scan window's OWN open (the 08:30 bar's
    open), not the 08:29 reference price used for bracket placement --
    those two prices sit one minute apart and are used for two different
    purposes here (a level-setting reference vs. a control-population
    dispersion measure), not conflated.
  - ENTRY: buy-stop at reference * (1 + width), sell-stop at
    reference * (1 - width), both live from 08:30 through the last scan bar
    (08:44 bar-start, i.e. through 08:45). FIRST TOUCH FILLS, scanned bar by
    bar in time order using each bar's own High/Low.
  - EXIT: the scan window's own 08:45 close (Close of the 08:44 bar-start
    1-minute bar) -- the decay curve says elevation persists to ~08:40;
    08:45 is the measured boundary, not a chosen one.
  - OPPOSITE SIDE: cancelled on first fill (OCO). If a single bar's
    High/Low touches BOTH the upper and lower trigger (order-of-touch is
    not recoverable from an OHLC bar), it is scored as a DOUBLE-STOP LOSS,
    never silently resolved to whichever side would have been the winner:
    both stop orders are treated as having filled (bought high AND sold
    low, `2 * width` apart), netting to a flat position and a definite,
    unambiguous loss of `2 * width` before costs, plus TWO entries' worth
    of entry cost (no separate exit cost -- the position closes itself via
    the offsetting fill, it is never carried to 08:45). This is the only
    interpretation of "both would have filled" that does not require
    guessing which side went first.
  - NO FILL: if neither trigger is touched anywhere in the scan window, the
    day contributes a real trade with `net_bp = 0` (no signal, no cost) --
    included in the "per event" denominator, never dropped, so expectancy
    is never conditioned on a fill happening (that would be survivorship
    bias in the strategy's own favor).

COSTS -- PESSIMISTIC, PER-TRADE, STATED
--------------------------------------------
No cost-sweep is run; these are fixed, stated assumptions, not tuned:
  - `SPREAD_BP = 1.0` -- SPY's calm-market quoted NBBO spread is close to
    the $0.01 minimum tick on a ~$450-650 share price (roughly 0.15-0.22bp);
    1.0bp is used here as a pessimistic-end widened-spread assumption for
    the thin-liquidity 08:30/14:00 print window, in the same "pessimistic
    end" spirit `EVAL_LAB.md` states its own FX cost convention in (there,
    3.0 pips round trip against ~120 pip average EURUSD daily range).
  - `STOP_THROUGH_BP = 2.0` -- a stop order on a fast tape fills THROUGH,
    not AT, its trigger; this is the SPY-bp-scale analogue of `EVAL_LAB.md`'s
    1.2-pip FX stop-slippage assumption, order-of-magnitude reasoned, not
    fit to any observed fill data (none exists in this simulation-only
    context).
  - Entry (a stop order) pays `SPREAD_BP + STOP_THROUGH_BP = 3.0bp`. Exit
    (a scheduled market order at the 08:45 close, not a stop) pays
    `SPREAD_BP = 1.0bp` only. Round trip = `4.0bp` per single-sided trade;
    a double-stop pays `2 * 3.0bp = 6.0bp` (two entries, no separate exit).
  - Both GROSS and NET results are reported; NET is the real number.

THE TWO CONTROLS
---------------------
  1. SAME STRATEGY ON CONTROL DAYS -- the identical bracket (same fixed
     width, same reference construction) applied to every non-event day in
     the same population. If it makes money there too, it is not
     harvesting the splash.
  2. PERMUTATION NULL -- the per-day net P&L series is fixed (it does not
     depend on which days are LABELLED event days, only on that day's own
     bars); event-day labels are reshuffled at random (without replacement,
     same event count) 2000 times and the mean re-computed each time. The
     observed event-day mean is reported beside that null distribution
     with a one-sided p-value (fraction of permutations >= observed) --
     the same discipline the ruin engine and oracle audit already use in
     this repo.

A result that does not clearly exceed both controls is a null and is
reported as such in the console's first line and the summary JSON's
`headline` field.

NO LOOK-AHEAD
-------------
The bracket is placed using only the 08:29-and-earlier (13:59-and-earlier
for FOMC) reference bar. The width is computed exclusively from the
CONTROL population -- by construction it never includes the event day being
priced, and it is computed once, up front, never re-derived per event day.

FAIL LOUD
---------
A day missing its reference bar, or missing any bar in its scan window, is
EXCLUDED with a reason, never interpolated -- same discipline every other
bar-loading path in this repo already holds itself to. A "no fill" day is
NOT an exclusion; it is a real, counted trading outcome.

REUSE
-----
1-minute bar loading (`build_spy_macro_decay_study._load_raw_bars_1m`,
`CACHE_1M`), window slicing (`build_spy_premarket_event_study._window_bars`),
the control-pool and Welch machinery (`build_spy_premarket_event_study.
control_pool`, `_matched_control`, `_welch`), the whipsaw `range` metric and
its own completeness-gated day builder (`build_spy_whipsaw_event_study.
_day_window_stats`, `PRIMARY_MINUTES`, `FOMC_MINUTES`), the FRED six-release
event calendar (`build_macro_event_study.build_event_calendar`,
`EventStudyError`, `_ci95`), and the FOMC scheduled calendar
(`build_fomc_splash_event_study.load_fomc_events`) are all imported and used
as-is -- none of that machinery is reimplemented here.

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
import build_macro_event_study as mes                                  # noqa: E402
from build_macro_event_study import EventStudyError                     # noqa: E402
import build_spy_premarket_event_study as bspes                         # noqa: E402
import build_spy_macro_decay_study as decay_mod                         # noqa: E402
import build_spy_whipsaw_event_study as wstudy                          # noqa: E402
import build_fomc_splash_event_study as fomc_mod                        # noqa: E402
from bars import BarDataError                                           # noqa: E402

OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
SUMMARY_JSON = OUT_DIR / "spy_bracket_harvest_study_summary.json"
PRIMARY_DAYS_CSV = OUT_DIR / "spy_bracket_harvest_primary_days.csv"
FOMC_DAYS_CSV = OUT_DIR / "spy_bracket_harvest_fomc_days.csv"

PRIMARY_SYMBOL = "SPY"

# --------------------------------------------------------------- parameters
# Pre-registered, fixed. See module docstring -- never swept.
BRACKET_WIDTH_PERCENTILE = 0.75

REF_MINUTE_PRIMARY = "08:29"
SCAN_MINUTES_PRIMARY = wstudy.PRIMARY_MINUTES          # 08:30..08:44
SCAN_WINDOW_LABEL_PRIMARY = wstudy.PRIMARY_WINDOW_LABEL  # "08:30-08:45"

REF_MINUTE_FOMC = "13:59"
SCAN_MINUTES_FOMC = wstudy.FOMC_MINUTES                # 14:00..14:14
SCAN_WINDOW_LABEL_FOMC = wstudy.FOMC_WINDOW_LABEL       # "14:00-14:15"

# Costs, bp. See module docstring for the reasoning behind each number.
SPREAD_BP = 1.0
STOP_THROUGH_BP = 2.0
ENTRY_COST_BP = SPREAD_BP + STOP_THROUGH_BP     # stop order: spread + through-fill
EXIT_COST_BP = SPREAD_BP                        # scheduled market exit: spread only

N_PERMUTATIONS = 2000
PERMUTATION_SEED = 20260828


# --------------------------------------------------------- reference + scan

def reference_and_scan_day_stats(symbol: str, ref_hhmm: str, scan_minutes: list[str],
                                  window_label: str
                                  ) -> tuple[pd.DataFrame, dict[str, pd.DataFrame],
                                             list[dict], list[dict]]:
    """One row per calendar day whose reference bar (`ref_hhmm`) AND every
    bucket of `scan_minutes` are present in the 1-minute cache. Days failing
    either check are EXCLUDED with a reason, never interpolated; the two
    exclusion reasons are tracked independently. Returns the day-level
    reference-price table, a date -> sorted scan-window-bars map (for
    per-day simulation), and the two exclusion lists."""
    raw = decay_mod._load_raw_bars_1m(symbol)
    rows: list[dict] = []
    scan_frames: dict[str, pd.DataFrame] = {}
    ref_excluded: list[dict] = []
    scan_excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        d = day.isoformat()
        present = set(chunk.index.strftime("%H:%M"))
        ref_ok = ref_hhmm in present
        missing_scan = [m for m in scan_minutes if m not in present]
        if not ref_ok:
            ref_excluded.append({"date": d, "reason": f"missing {ref_hhmm} 1m reference bar"})
        if missing_scan:
            scan_excluded.append({"date": d,
                                   "reason": f"{len(missing_scan)} missing 1m bar(s) in "
                                             f"{window_label} scan window "
                                             f"(first at {missing_scan[0]})"})
        if not ref_ok or missing_scan:
            continue
        ref_bar = bspes._window_bars(chunk, ref_hhmm, ref_hhmm)
        ref_price = float(ref_bar["Close"].iloc[-1])
        scan_window = bspes._window_bars(chunk, scan_minutes[0], scan_minutes[-1]).sort_index()
        scan_frames[d] = scan_window
        rows.append({"date": d, "weekday": pd.Timestamp(day).strftime("%A"), "ref": ref_price})
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return out, scan_frames, ref_excluded, scan_excluded


def control_bracket_width(symbol: str, scan_minutes: list[str], window_label: str,
                           exclude_dates: set[str]) -> tuple[float, int]:
    """75th percentile of `range` (whipsaw metric, open-anchored on the scan
    window's own open) over `scan_minutes`, restricted to days NOT in
    `exclude_dates` -- CONTROL days only, computed once, before any event
    day's P&L is touched."""
    range_days, _excluded = wstudy._day_window_stats(symbol, scan_minutes, window_label)
    ctrl = bspes.control_pool(range_days, exclude_dates)
    if ctrl.empty:
        return float("nan"), 0
    width = float(ctrl["range"].quantile(BRACKET_WIDTH_PERCENTILE))
    return width, len(ctrl)


# --------------------------------------------------------------- simulation

def simulate_trade(ref: float, width: float, scan_window: pd.DataFrame) -> dict:
    """First-touch-fills bracket simulation for one day. `scan_window` must
    be sorted by index (callers are responsible -- bar loads in this repo
    are stored sorted, and `reference_and_scan_day_stats` sorts explicitly).
    See module docstring for the double-stop and no-fill conventions."""
    upper = ref * (1.0 + width)
    lower = ref * (1.0 - width)
    fill_side: str | None = None
    for _, row in scan_window.iterrows():
        hi, lo = float(row["High"]), float(row["Low"])
        touch_up, touch_down = hi >= upper, lo <= lower
        if touch_up and touch_down:
            fill_side = "double_stop"
            break
        if touch_up:
            fill_side = "buy"
            break
        if touch_down:
            fill_side = "sell"
            break

    if fill_side is None:
        return {"outcome": "no_fill", "gross_bp": 0.0, "net_bp": 0.0}

    exit_price = float(scan_window["Close"].iloc[-1])

    if fill_side == "double_stop":
        # Both stop orders treated as filled -- bought high, sold low,
        # 2*width apart, netting flat. Definite, unambiguous loss.
        gross_bp = (-2.0 * width) * 1e4
        net_bp = gross_bp - 2.0 * ENTRY_COST_BP
        return {"outcome": "double_stop", "gross_bp": gross_bp, "net_bp": net_bp}

    if fill_side == "buy":
        gross_frac = (exit_price - upper) / ref
    else:  # "sell"
        gross_frac = (lower - exit_price) / ref
    gross_bp = gross_frac * 1e4
    net_bp = gross_bp - (ENTRY_COST_BP + EXIT_COST_BP)
    return {"outcome": fill_side, "gross_bp": gross_bp, "net_bp": net_bp}


def build_trades(days: pd.DataFrame, scan_frames: dict[str, pd.DataFrame],
                  width: float) -> pd.DataFrame:
    """One row per day in `days`: the simulated bracket outcome, gross/net
    P&L in bp, and net P&L in R (R defined as the bracket width itself --
    the only risk unit this pre-registered parameterization defines)."""
    width_bp = width * 1e4
    rows: list[dict] = []
    for _, r in days.iterrows():
        sim = simulate_trade(r["ref"], width, scan_frames[r["date"]])
        rows.append({
            "date": r["date"], "weekday": r["weekday"], "ref": r["ref"],
            "outcome": sim["outcome"], "gross_bp": sim["gross_bp"], "net_bp": sim["net_bp"],
            "net_R": (sim["net_bp"] / width_bp) if width_bp else None,
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------- stats

def arm_expectancy(trades: pd.DataFrame, member_dates: set[str], label: str) -> dict:
    """Per-event expectancy over ALL matched days, including no-fill days
    at net_bp=0 -- never conditioned on a fill happening."""
    ev = trades[trades["date"].isin(member_dates)]
    n = len(ev)
    if n == 0:
        return {"label": label, "n": 0}
    hit = int((ev["net_bp"] > 0).sum())
    double = int((ev["outcome"] == "double_stop").sum())
    nofill = int((ev["outcome"] == "no_fill").sum())
    return {
        "label": label, "n": n,
        "hit_rate": hit / n,
        "double_stop_rate": double / n,
        "no_fill_rate": nofill / n,
        "mean_gross_bp_per_event": float(ev["gross_bp"].mean()),
        "mean_net_bp_per_event": float(ev["net_bp"].mean()),
        "mean_net_R_per_event": float(ev["net_R"].mean()),
        "net_ci95": mes._ci95(ev["net_bp"].tolist()),
    }


def permutation_null(trades: pd.DataFrame, n_event: int, *,
                      n_perm: int = N_PERMUTATIONS, seed: int = PERMUTATION_SEED) -> dict:
    """Reshuffle which `n_event` days (of the full day population `trades`
    covers) are labelled 'event', without replacement, `n_perm` times;
    each day's own simulated net_bp never changes -- only the labelling
    does. Reports the observed event-arm mean beside this null
    distribution."""
    pool = trades["net_bp"].tolist()
    if n_event <= 0 or n_event > len(pool):
        return {"n_perm": 0, "null_mean": None, "null_sd": None,
                "p_value_one_sided_event_gt_random": None}
    rng = random.Random(seed)
    means = []
    for _ in range(n_perm):
        sample = rng.sample(pool, n_event)
        means.append(sum(sample) / n_event)
    return {"means": means, "n_perm": n_perm}


def _finish_permutation(perm: dict, observed_mean: float | None) -> dict:
    if not perm.get("means") or observed_mean is None:
        return {"n_perm": perm.get("n_perm", 0), "null_mean": None, "null_sd": None,
                "p_value_one_sided_event_gt_random": None}
    means = perm["means"]
    n_perm = len(means)
    null_mean = sum(means) / n_perm
    var = sum((m - null_mean) ** 2 for m in means) / (n_perm - 1) if n_perm > 1 else 0.0
    null_sd = var ** 0.5
    p_val = sum(1 for m in means if m >= observed_mean) / n_perm
    return {
        "n_perm": n_perm, "null_mean_net_bp": null_mean, "null_sd_net_bp": null_sd,
        "observed_mean_net_bp": observed_mean,
        "p_value_one_sided_event_gt_random": p_val,
    }


def _classify(primary: dict, control_days: dict, perm: dict) -> str:
    if primary.get("n", 0) == 0:
        return "INSUFFICIENT_DATA"
    net_mean = primary.get("mean_net_bp_per_event")
    if net_mean is None:
        return "INSUFFICIENT_DATA"
    beats_control_days = (control_days.get("n", 0) > 0
                           and net_mean > control_days.get("mean_net_bp_per_event", float("inf")))
    p = perm.get("p_value_one_sided_event_gt_random")
    beats_permutation = p is not None and p < 0.05
    if net_mean > 0 and beats_control_days and beats_permutation:
        return "CLEARS_BOTH_CONTROLS"
    return "NULL -- does not clearly exceed both controls"


# --------------------------------------------------------------------- arm

def run_arm(symbol: str, ref_hhmm: str, scan_minutes: list[str], window_label: str,
            event_member_dates: set[str], width_exclude_dates: set[str],
            arm_name: str) -> dict:
    """One full arm: reference/scan day table, control-derived width,
    per-day trades over the WHOLE covered population, event-arm expectancy,
    control-days-same-strategy expectancy, and the permutation null."""
    days, scan_frames, ref_excl, scan_excl = reference_and_scan_day_stats(
        symbol, ref_hhmm, scan_minutes, window_label)
    if days.empty:
        return {"arm": arm_name, "skipped": "no complete reference+scan days in cache"}

    width, control_range_n = control_bracket_width(symbol, scan_minutes, window_label,
                                                     width_exclude_dates)
    if width != width:  # NaN check without importing math for one use
        return {"arm": arm_name, "skipped": "empty control population for width derivation"}

    trades = build_trades(days, scan_frames, width)

    event_dates_present = event_member_dates & set(trades["date"])
    event_dates_dropped = sorted(event_member_dates - set(trades["date"]))
    control_dates_present = set(trades["date"]) - width_exclude_dates

    primary = arm_expectancy(trades, event_dates_present, f"{arm_name}_event_days")
    control_days_arm = arm_expectancy(trades, control_dates_present,
                                       f"{arm_name}_control_days_same_strategy")

    welch = bspes._welch(
        trades[trades["date"].isin(event_dates_present)]["net_bp"].tolist(),
        trades[trades["date"].isin(control_dates_present)]["net_bp"].tolist())

    perm = permutation_null(trades, len(event_dates_present))
    perm = _finish_permutation(perm, primary.get("mean_net_bp_per_event"))

    return {
        "arm": arm_name,
        "window_et": window_label,
        "reference_minute_et": ref_hhmm,
        "bracket_width_pct": width,
        "bracket_width_bp": width * 1e4,
        "bracket_width_control_n": control_range_n,
        "bracket_width_percentile": BRACKET_WIDTH_PERCENTILE,
        "cost_model": {
            "entry_cost_bp": ENTRY_COST_BP, "exit_cost_bp": EXIT_COST_BP,
            "round_trip_cost_bp": ENTRY_COST_BP + EXIT_COST_BP,
            "double_stop_cost_bp": 2.0 * ENTRY_COST_BP,
        },
        "reference_scan_complete_days": len(days),
        "reference_excluded_days": len(ref_excl),
        "reference_excluded_reasons_sample": ref_excl[:5],
        "scan_excluded_days": len(scan_excl),
        "scan_excluded_reasons_sample": scan_excl[:5],
        "event_dates_dropped_no_complete_window": event_dates_dropped,
        "PRIMARY_EVENT_ARM": primary,
        "CONTROL_1_SAME_STRATEGY_ON_CONTROL_DAYS": control_days_arm,
        "event_vs_control_days_welch": welch,
        "CONTROL_2_PERMUTATION_NULL": perm,
        "interpretation": _classify(primary, control_days_arm, perm),
        "trades": trades,
    }


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
        print("  REFUSED: empty 1m pre-market cache")
        return 1

    start = raw1m.index.min().date()
    end_d = raw1m.index.max().date()
    try:
        fred_events = mes.build_event_calendar(key, start=start, end=end_d)
    except EventStudyError as e:
        print(f"  REFUSED: {e}")
        return 1
    fred_event_dates = {e["date"] for e in fred_events}

    try:
        fomc_events = fomc_mod.load_fomc_events()
    except Exception as e:  # noqa: BLE001 -- calendar file is static/local, any failure is fatal here
        print(f"  REFUSED: could not load FOMC calendar -- {e}")
        return 1
    scheduled_fomc_dates = {e["date"] for e in fomc_events if not e.get("unscheduled")}
    all_fomc_dates = {e["date"] for e in fomc_events}

    # ------------------------------------------------------------ PRIMARY ARM
    primary_arm = run_arm(
        PRIMARY_SYMBOL, REF_MINUTE_PRIMARY, SCAN_MINUTES_PRIMARY, SCAN_WINDOW_LABEL_PRIMARY,
        event_member_dates=fred_event_dates, width_exclude_dates=fred_event_dates,
        arm_name="primary_0830")

    # --------------------------------------------------------------- FOMC ARM
    fomc_arm = run_arm(
        PRIMARY_SYMBOL, REF_MINUTE_FOMC, SCAN_MINUTES_FOMC, SCAN_WINDOW_LABEL_FOMC,
        event_member_dates=scheduled_fomc_dates,
        width_exclude_dates=fred_event_dates | all_fomc_dates,
        arm_name="fomc_1400")
    fomc_arm["note"] = ("thin sample -- n~84 scheduled meetings across the covered "
                         "period, stated explicitly, not left for a reader to infer")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if "trades" in primary_arm:
        primary_arm["trades"].to_csv(PRIMARY_DAYS_CSV, index=False)
    if "trades" in fomc_arm:
        fomc_arm["trades"].to_csv(FOMC_DAYS_CSV, index=False)
    primary_arm_json = {k: v for k, v in primary_arm.items() if k != "trades"}
    fomc_arm_json = {k: v for k, v in fomc_arm.items() if k != "trades"}

    headline = primary_arm_json.get("interpretation", "INSUFFICIENT_DATA")
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "headline": headline,
        "follows_up_on": [
            "data/daytrade/event_study/spy_macro_decay_study_summary.json (splash real+causal)",
            "data/daytrade/event_study/spy_whipsaw_event_study_summary.json (path barely degrades)",
            "data/daytrade/event_study/spy_splash_continuation_study_summary.json "
            "(direction does not exist -- seven independent nulls)",
        ],
        "parameterization": ("pre-registered, single, not searched -- see module docstring: "
                              "08:29/13:59 reference close, control-only p75 range as bracket "
                              "width, first-touch OCO entry, 08:45/14:15 close exit"),
        "cost_model_bp": {"spread_bp": SPREAD_BP, "stop_through_bp": STOP_THROUGH_BP,
                           "entry_cost_bp": ENTRY_COST_BP, "exit_cost_bp": EXIT_COST_BP},
        "PRIMARY_ARM_0830": primary_arm_json,
        "FOMC_ARM_1400_SEPARATE_THIN_SAMPLE": fomc_arm_json,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=1, default=str))

    print("=" * 72)
    print(f"  FIRST LINE: {headline}")
    pe = primary_arm_json.get("PRIMARY_EVENT_ARM", {})
    cd = primary_arm_json.get("CONTROL_1_SAME_STRATEGY_ON_CONTROL_DAYS", {})
    pm = primary_arm_json.get("CONTROL_2_PERMUTATION_NULL", {})
    print(f"  PRIMARY (0830): n={pe.get('n')}, net_bp/event={pe.get('mean_net_bp_per_event')}, "
          f"net_R/event={pe.get('mean_net_R_per_event')}, hit_rate={pe.get('hit_rate')}, "
          f"double_stop_rate={pe.get('double_stop_rate')}")
    print(f"  CONTROL 1 (same strategy, control days): n={cd.get('n')}, "
          f"net_bp/event={cd.get('mean_net_bp_per_event')}")
    print(f"  CONTROL 2 (permutation null): p={pm.get('p_value_one_sided_event_gt_random')}, "
          f"null_mean_net_bp={pm.get('null_mean_net_bp')}")
    fe = fomc_arm_json.get("PRIMARY_EVENT_ARM", {})
    print(f"  FOMC ARM (1400, thin, n={fe.get('n')}): "
          f"net_bp/event={fe.get('mean_net_bp_per_event')}, "
          f"interpretation={fomc_arm_json.get('interpretation')}")
    print(f"  wrote {SUMMARY_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
