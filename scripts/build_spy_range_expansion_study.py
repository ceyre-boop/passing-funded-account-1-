#!/usr/bin/env python3
"""SPY RANGE-EXPANSION STUDY — the pivot from DIRECTION to REGIME.

READ `specs/043_RANGE_EXPANSION.md` FIRST. That file is the pre-registration
and it was written to disk before this script existed. Every number this
script produces is defined there; nothing here may widen, soften or reinterpret
it after the fact.

WHY THIS EXISTS
----------------
Three pre-registered attempts in this family already tried to trade the
DIRECTION of the 08:30/FOMC splash and all three failed:

  * `spy_bracket_harvest_study_summary.json` -- both-sides bracket, killed by
    costs.
  * pre-FOMC drift -- underpowered.
  * `spy_splash_continuation_study_summary.json` -- second-wave continuation
    null, despite confirmed elevated volume.

The impulse itself is real, large and replicated (`spy_premarket_event_study
_summary.json`: 08:30-09:00 |return| 0.1783% event vs 0.0890% control,
p=1.16e-20). It is just directionless. So this study stops asking "which way"
and asks the only remaining question the calendar could answer: does a
scheduled event tell us the day's MAGNITUDE will be larger?

THE PRE-REGISTERED PRIMARY
---------------------------
    expansion = RTH_true_range / ATR20_prior

    RTH_true_range = (max High - min Low) over 09:30-16:00 ET RTH ONLY.
    ATR20_prior    = 20-day average true range from sessions STRICTLY BEFORE
                     the day being measured -- no bar of that day may leak in.

    PRIMARY COMPARISON: mean expansion on scheduled-macro-event days vs mean
    expansion on matched non-event control days. ONE-SIDED (event > control),
    the direction fixed in advance by the hypothesis.

ONE instrument (SPY), ONE statistic, ONE test. No multiple-testing penalty --
it is the only member of its family.

THE CENTRAL CONFOUND, HANDLED NOT HIDDEN
-------------------------------------------
Macro events cluster inside high-volatility regimes. A RAW range comparison
would find a big "effect" that is nothing but volatility autocorrelation: vol
was already elevated before the event and merely persisted. Dividing by
ATR20_prior asks the only question that matters -- is the day bigger than its
OWN recent baseline predicted. The raw range IS reported, explicitly labelled
`CONFOUNDED_...`, and is never the primary.

THE ECONOMIC FLOOR, REGISTERED IN ADVANCE
--------------------------------------------
An expansion ratio below 1.10 is NOT economically useful even if statistically
significant -- it cannot move a profit target or an ORB threshold beyond noise
and slippage. `clears_economic_floor` is written into the summary for every
arm and a significant-but-under-floor result is reported as SIGNIFICANT AND
USELESS, in those words.

RTH ONLY, IMPULSE EXCLUDED
----------------------------
The 08:30 impulse is outside 09:30 by construction. Deliberate: a window
containing the impulse would just re-measure the already-known result.

SECONDARY / EXPLORATORY -- BH-corrected within its OWN family, never promoted
------------------------------------------------------------------------------
  A. ORB payoff (gross) conditional on event vs control day.
  B. Range decomposition: 09:30-12:00 expansion vs 12:00-16:00 expansion.
  C. Event-type split: scheduled FOMC vs 08:30 releases-without-FOMC.

BAR SOURCE
-----------
`data/daytrade/bars_premarket/SPY_5m.parquet` (Alpaca SIP, raw, 2016-01-01
forward) read through `build_spy_premarket_event_study._load_raw_bars` -- the
same direct-parquet read every sibling in this family uses, never through
`bars.load_sessions`. `data/daytrade/bars_extended/` holds NVDA only and
cannot serve this study.

CALENDAR
---------
FRED historical release dates come from the artifact
`data/daytrade/event_study/fred_historical_release_dates.json` that
`scripts/build_macro_event_study.py` already wrote (a live re-fetch needs
FRED_API_KEY; `--refresh-calendar` does that when the key is present, and the
run REFUSES rather than proceeding on a guess if neither the key nor the
artifact is available). Scheduled FOMC dates come from
`data/daytrade/fomc_calendar.json` through `daytrade/macro_calendar.py`, run
past that module's own `validate_fomc_events` fabrication guard; entries
flagged `unscheduled: true` are excluded by construction, because the
pool/ripple thesis is about FOREKNOWLEDGE and a surprise meeting violates the
premise.

The study span is bounded by the span over which that calendar is COMPLETE.
Outside it, a day cannot be verified to be a NON-event day, and a contaminated
control pool is worse than a short one.

FAIL LOUD
----------
A day missing any of its 78 RTH bars is EXCLUDED with a reason, never
interpolated. A day without 20 strictly-prior complete sessions has NO
expansion value and is excluded, never computed on a short window. A missing
calendar source refuses the run. Nothing is defaulted to zero.

NO LOOK-AHEAD
--------------
`prior_window()` is the single chokepoint through which every ATR input
passes, and it REFUSES an `end_index` past the day being measured. That is the
load-bearing property of this whole design and
`scripts/test_build_spy_range_expansion_study.py` fails if it is removed.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics as st
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))
sys.path.insert(0, str(ROOT / "scripts"))
from bars import BarDataError                                       # noqa: E402
import macro_calendar as mc                                         # noqa: E402
import mechanisms                                                   # noqa: E402
import build_macro_event_study as mes                                # noqa: E402
from build_macro_event_study import EventStudyError                  # noqa: E402
import build_spy_premarket_event_study as bspes                      # noqa: E402

OUT_DIR = ROOT / "data" / "daytrade" / "event_study"
SUMMARY_JSON = OUT_DIR / "spy_range_expansion_study_summary.json"
DAYS_CSV = OUT_DIR / "spy_range_expansion_days.csv"
FRED_ARTIFACT = OUT_DIR / "fred_historical_release_dates.json"

PRIMARY_SYMBOL = "SPY"

# RTH, by 5-minute bar START timestamp. 09:30..15:55 inclusive = 78 bars,
# covering 09:30-16:00.
RTH_OPEN, RTH_LAST = "09:30", "15:55"
FULL_RTH_BARS = 78
AM_LAST = "11:55"          # 09:30..11:55 -> 09:30-12:00
PM_FIRST = "12:00"         # 12:00..15:55 -> 12:00-16:00
OR_LAST = "09:55"          # 09:30..09:55 -> the 09:30-10:00 opening range
ORB_SCAN_FIRST = "10:00"

ATR_WINDOW = 20
ECONOMIC_FLOOR = 1.10      # pre-registered in specs/043_RANGE_EXPANSION.md

N_PERMUTATIONS = 2000
PERMUTATION_SEED = 43

BUFFER_SESSIONS = 1        # +/- 1 trading day for the buffered-control arm


class RangeExpansionError(RuntimeError):
    """Refused to build. Never downgraded to a warning, never defaulted."""


# ---------------------------------------------------------------- bar tables

def _window_bars(chunk: pd.DataFrame, start_hhmm: str, end_hhmm: str) -> pd.DataFrame:
    return bspes._window_bars(chunk, start_hhmm, end_hhmm)


def rth_frames(symbol: str) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    """`{iso_date: RTH bar frame}` for every day whose full 78-bar
    09:30-15:55 window is present, plus the excluded days with reasons. A
    day with a hole is EXCLUDED, never interpolated -- half days drop out by
    construction (they cannot have 78 bars)."""
    raw = bspes._load_raw_bars(symbol)
    frames: dict[str, pd.DataFrame] = {}
    excluded: list[dict] = []
    for day, chunk in raw.groupby(raw.index.date):
        window = _window_bars(chunk, RTH_OPEN, RTH_LAST)
        missing = bspes._missing_bars(window, day, RTH_OPEN, RTH_LAST)
        if missing:
            excluded.append({"date": day.isoformat(),
                              "reason": f"{len(missing)} missing 5m RTH bar(s) in "
                                        f"{RTH_OPEN}-16:00 (first at {missing[0]})"})
            continue
        if len(window) != FULL_RTH_BARS:
            excluded.append({"date": day.isoformat(),
                              "reason": f"{len(window)} RTH bars, expected {FULL_RTH_BARS}"})
            continue
        frames[day.isoformat()] = window
    return frames, excluded


def _range_of(window: pd.DataFrame) -> float:
    return float(window["High"].max()) - float(window["Low"].min())


def session_rows(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per complete session, oldest first: the raw geometry only.
    No normalisation happens here -- ATR is attached separately so the
    anti-lookahead chokepoint is a single, testable function."""
    rows = []
    for iso in sorted(frames):
        w = frames[iso]
        rows.append({
            "date": iso,
            "weekday": pd.Timestamp(iso).strftime("%A"),
            "rth_high": float(w["High"].max()),
            "rth_low": float(w["Low"].min()),
            "rth_close": float(w["Close"].iloc[-1]),
            "rth_range": _range_of(w),
            "am_range": _range_of(_window_bars(w, RTH_OPEN, AM_LAST)),
            "pm_range": _range_of(_window_bars(w, PM_FIRST, RTH_LAST)),
        })
    return pd.DataFrame(rows).reset_index(drop=True)


# ------------------------------------------------- the anti-lookahead chokepoint

def prior_window(values: list[float], day_index: int, window: int = ATR_WINDOW,
                  *, end_index: int | None = None) -> list[float]:
    """The `window` values STRICTLY BEFORE `day_index`, or `[]` if fewer than
    `window` of them exist.

    `end_index` exists for exactly one reason: so a fault-injection test can
    ATTEMPT a same-day leak and be refused. Any `end_index` greater than
    `day_index` would place the measured day's own bar inside its own
    baseline, which is the single property this whole study rests on, so it
    raises rather than returning a quietly wrong number.
    """
    end = day_index if end_index is None else end_index
    if end > day_index:
        raise RangeExpansionError(
            f"LOOK-AHEAD REFUSED: ATR window for index {day_index} would end at "
            f"{end}, which includes the day being measured (or later). "
            "ATR20_prior must read sessions strictly before the measured day.")
    lo = end - window
    if lo < 0:
        return []
    return list(values[lo:end])


def attach_expansion(sessions: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Adds `prev_close`, `true_range`, `atr20_prior`, `atr20_prior_rth`,
    `expansion`, `expansion_rth`, `expansion_am`, `expansion_pm`.

    Wilder TR against the PRIOR session's RTH close:
        TR_i = max(H_i - L_i, |H_i - C_{i-1}|, |L_i - C_{i-1}|)
    so TR is undefined for the first cached session, and ATR20_prior needs 20
    DEFINED prior TRs -- index >= 21. A day short of that is dropped with a
    reason and never computed on a partial window.
    """
    s = sessions.sort_values("date").reset_index(drop=True)
    closes = s["rth_close"].tolist()
    highs = s["rth_high"].tolist()
    lows = s["rth_low"].tolist()
    rth_ranges = s["rth_range"].tolist()

    trs: list[float | None] = [None]
    for i in range(1, len(s)):
        pc = closes[i - 1]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))

    out_rows: list[dict] = []
    dropped: list[dict] = []
    for i in range(len(s)):
        row = s.iloc[i].to_dict()
        # TR list index 0 is None by construction; a prior window that would
        # reach it is short by definition and handled by the None check below.
        tr_prior = prior_window(trs, i, ATR_WINDOW)
        rng_prior = prior_window(rth_ranges, i, ATR_WINDOW)
        if len(tr_prior) < ATR_WINDOW or any(v is None for v in tr_prior):
            dropped.append({"date": row["date"],
                             "reason": f"fewer than {ATR_WINDOW} strictly-prior complete "
                                       "sessions with a defined true range -- no "
                                       "ATR20_prior, day excluded rather than "
                                       "computed on a short window"})
            continue
        atr = sum(tr_prior) / len(tr_prior)
        atr_rth = sum(rng_prior) / len(rng_prior)
        if atr <= 0 or atr_rth <= 0:
            dropped.append({"date": row["date"],
                             "reason": "ATR20_prior is zero or negative -- refusing to "
                                       "divide; never defaulted"})
            continue
        row["prev_close"] = closes[i - 1] if i >= 1 else None
        row["true_range"] = trs[i]
        row["atr20_prior"] = atr
        row["atr20_prior_rth"] = atr_rth
        row["expansion"] = row["rth_range"] / atr
        row["expansion_rth"] = row["rth_range"] / atr_rth
        row["expansion_am"] = row["am_range"] / atr
        row["expansion_pm"] = row["pm_range"] / atr
        out_rows.append(row)
    return pd.DataFrame(out_rows).reset_index(drop=True), dropped


# ----------------------------------------------------------- secondary A: ORB

def orb_outcome(window: pd.DataFrame) -> dict:
    """Pre-registered ORB, GROSS of costs (spec 043 secondary A).

    Opening range = High/Low of 09:30-10:00. From the 10:00 bar onward the
    FIRST bar to trade beyond a boundary enters at that boundary; a bar that
    breaks BOTH sides is `ambiguous` and takes NO trade -- intrabar order is
    unknowable from 5m OHLC and inventing one would be fabricating a fill.
    Stop = the opposite boundary, checked from the bar AFTER entry (the entry
    bar itself cannot have reached it -- if it had, the bar would have been
    the ambiguous case). No target; otherwise exit at the 15:55 Close.
    1R = the opening-range width.
    """
    orw = _window_bars(window, RTH_OPEN, OR_LAST)
    if len(orw) != 6:
        return {"outcome": "incomplete_opening_range", "R": None}
    hi = float(orw["High"].max())
    lo = float(orw["Low"].min())
    width = hi - lo
    if width <= 0:
        return {"outcome": "degenerate_zero_width_opening_range", "R": None}

    scan = _window_bars(window, ORB_SCAN_FIRST, RTH_LAST)
    entry_pos = None
    side = entry = stop = None
    for pos in range(len(scan)):
        bar = scan.iloc[pos]
        up = float(bar["High"]) > hi
        dn = float(bar["Low"]) < lo
        if up and dn:
            return {"outcome": "ambiguous_both_sides_same_bar", "R": None,
                     "orb_width": width}
        if up:
            side, entry, stop, entry_pos = "long", hi, lo, pos
            break
        if dn:
            side, entry, stop, entry_pos = "short", lo, hi, pos
            break
    if entry_pos is None:
        return {"outcome": "no_breakout", "R": 0.0, "orb_width": width}

    for pos in range(entry_pos + 1, len(scan)):
        bar = scan.iloc[pos]
        if side == "long" and float(bar["Low"]) <= stop:
            return {"outcome": "stopped", "R": -1.0, "side": side, "orb_width": width}
        if side == "short" and float(bar["High"]) >= stop:
            return {"outcome": "stopped", "R": -1.0, "side": side, "orb_width": width}

    close = float(scan["Close"].iloc[-1])
    r = (close - entry) / width if side == "long" else (entry - close) / width
    return {"outcome": "held_to_close", "R": r, "side": side, "orb_width": width}


def attach_orb(days: pd.DataFrame, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = days.copy()
    outcomes, rs = [], []
    for iso in out["date"]:
        res = orb_outcome(frames[iso])
        outcomes.append(res["outcome"])
        rs.append(res["R"])
    out["orb_outcome"] = outcomes
    out["orb_R"] = rs
    return out


# ------------------------------------------------------------------ calendar

def _describe_path(p: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise (a test
    fixture in a tmp dir is not under ROOT, and provenance must still record
    WHICH file was read rather than raising)."""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def load_fred_event_dates(*, refresh: bool = False,
                           artifact: Path = FRED_ARTIFACT) -> tuple[set[str], dict]:
    """Historical scheduled-release dates for the six FRED-tracked releases,
    plus provenance. Refuses rather than guessing when neither a live key nor
    the cached artifact is available."""
    if refresh:
        key = os.environ.get("FRED_API_KEY")
        if not key:
            raise RangeExpansionError(
                "--refresh-calendar requested but FRED_API_KEY is missing -- "
                "refusing to silently fall back to the cached artifact")
        events = mes.build_event_calendar(key, start=date(2016, 1, 1),
                                           end=date.today())
        prov = {"source": "live FRED fetch via build_macro_event_study."
                           "build_event_calendar", "n_events": len(events)}
        return {e["date"] for e in events}, prov

    if not artifact.exists():
        raise RangeExpansionError(
            f"no FRED historical calendar at {artifact} and no --refresh-calendar "
            "(which needs FRED_API_KEY) -- refusing to run a study whose control "
            "pool cannot be verified to exclude release days")
    data = json.loads(artifact.read_text())
    events = data.get("events")
    if not events:
        raise RangeExpansionError(
            f"{artifact} holds no events -- refusing to treat an empty calendar "
            "as 'no releases happened'")
    prov = {"source": _describe_path(artifact),
            "generated_at": data.get("generated_at"),
            "calendar_start": data.get("start"), "calendar_end": data.get("end"),
            "releases": data.get("releases"), "n_events": len(events)}
    return {e["date"] for e in events}, prov


def load_scheduled_fomc_dates(*, fomc_json: Path = mc.FOMC_CALENDAR_JSON
                               ) -> tuple[set[str], dict]:
    """SCHEDULED FOMC decision dates only. Runs the calendar past
    `macro_calendar.validate_fomc_events` -- the fabrication guard that exists
    because FRED release id 101 returns a date for every calendar day -- and
    REFUSES on any violation rather than measuring against a suspect calendar.
    `unscheduled: true` entries are excluded by construction."""
    raw = mc._load_json(fomc_json)
    if raw is None:
        raise RangeExpansionError(
            f"no FOMC calendar at {fomc_json} -- refusing to run with FOMC days "
            "silently sitting in the control pool")
    events = raw.get("events", [])
    violations = mc.validate_fomc_events(events)
    if violations:
        raise RangeExpansionError(
            "FOMC calendar failed macro_calendar.validate_fomc_events: "
            + "; ".join(violations))
    scheduled = [e for e in events if e.get("unscheduled") is not True]
    excluded = [e["date"] for e in events if e.get("unscheduled") is True]
    prov = {"source": _describe_path(fomc_json),
            "verified_as_of": raw.get("verified_as_of"),
            "n_scheduled": len(scheduled),
            "unscheduled_excluded": excluded}
    return {e["date"] for e in scheduled}, prov


# -------------------------------------------------------------- control pools

def control_pool(days: pd.DataFrame, event_dates: set[str]) -> pd.DataFrame:
    """Sessions that are not themselves an event day for ANY tracked source.
    Same non-buffered convention `build_spy_premarket_event_study.control_pool`
    and `build_macro_event_study.control_pool` already document and accept
    (with weekly Initial Claims in the tracked set, buffering would collapse
    the pool); the trade-off biases the event-vs-control gap TOWARD zero and
    is therefore conservative."""
    return days[~days["date"].isin(event_dates)].reset_index(drop=True)


def buffered_control_pool(days: pd.DataFrame, event_dates: set[str],
                           buffer: int = BUFFER_SESSIONS) -> pd.DataFrame:
    """The registered robustness pool: additionally drops any session within
    `buffer` TRADING sessions of an event day (neighbourhood measured in the
    session list, not calendar days -- a Monday is adjacent to the prior
    Friday)."""
    ordered = days.sort_values("date").reset_index(drop=True)
    dates = ordered["date"].tolist()
    blocked: set[str] = set()
    for i, d in enumerate(dates):
        if d in event_dates:
            for j in range(max(0, i - buffer), min(len(dates), i + buffer + 1)):
                blocked.add(dates[j])
    return ordered[~ordered["date"].isin(blocked)].reset_index(drop=True)


def matched(pool: pd.DataFrame, weekdays: set[str]) -> pd.DataFrame:
    return pool[pool["weekday"].isin(weekdays)].reset_index(drop=True)


# ------------------------------------------------------------------ statistics

def _one_sided(rec: dict) -> float | None:
    if rec.get("welch_t") is None:
        return None
    p_two = rec["welch_p_two_sided"]
    return p_two / 2 if rec["welch_t"] > 0 else 1 - p_two / 2


def permutation_null(event_vals: list[float], control_vals: list[float], *,
                      n_perm: int = N_PERMUTATIONS,
                      seed: int = PERMUTATION_SEED) -> dict:
    """Reshuffle WHICH days carry the event label, without replacement, over
    the pooled population. Each day's own expansion never changes -- only the
    labelling does. `p` is the fraction of reshuffles whose event-arm mean is
    >= the observed event-arm mean (one-sided, the pre-registered direction).

    The inputs are never mutated: the pool is a fresh list and `random.sample`
    does not touch it."""
    n_ev = len(event_vals)
    pool = list(event_vals) + list(control_vals)
    if n_ev == 0 or n_ev > len(pool):
        return {"n_perm": 0, "null_mean": None, "null_sd": None,
                 "observed_event_mean": None,
                 "p_value_one_sided_event_gt_random": None}
    observed = sum(event_vals) / n_ev
    rng = random.Random(seed)
    means = []
    for _ in range(n_perm):
        sample = rng.sample(pool, n_ev)
        means.append(sum(sample) / n_ev)
    null_mean = sum(means) / n_perm
    var = sum((m - null_mean) ** 2 for m in means) / (n_perm - 1) if n_perm > 1 else 0.0
    return {
        "n_perm": n_perm,
        "null_mean": null_mean,
        "null_sd": var ** 0.5,
        "observed_event_mean": observed,
        "p_value_one_sided_event_gt_random": sum(1 for m in means if m >= observed) / n_perm,
    }


def two_sample_mde(event_vals: list[float], control_vals: list[float]) -> dict:
    """Minimum detectable difference in MEANS at one-sided 95% / 80% power.

    Reuses `daytrade/mechanisms.mde` rather than re-deriving the constant:
    that function computes (Z_a + Z_b) * sigma / sqrt(n), and the two-sample
    form (Z_a + Z_b) * sigma_pooled * sqrt(1/n1 + 1/n2) is exactly the same
    expression at an effective n of n1*n2/(n1+n2)."""
    n1, n2 = len(event_vals), len(control_vals)
    if n1 < 2 or n2 < 2:
        return {"mde_expansion_units": None,
                 "note": "fewer than 2 observations in an arm -- MDE undefined"}
    v1, v2 = st.variance(event_vals), st.variance(control_vals)
    pooled = (((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)) ** 0.5
    n_eff = (n1 * n2) / (n1 + n2)
    detectable = mechanisms.mde(pooled, n_eff)
    ctrl_mean = sum(control_vals) / n2
    return {
        "mde_expansion_units": detectable,
        "pooled_sd": pooled,
        "n_effective": n_eff,
        "z_alpha_one_sided_95": mechanisms.Z_ALPHA,
        "z_power_80": mechanisms.Z_POWER,
        "control_mean": ctrl_mean,
        "smallest_detectable_event_mean": ctrl_mean + detectable,
        "smallest_detectable_ratio_event_over_control": (
            (ctrl_mean + detectable) / ctrl_mean if ctrl_mean else None),
    }


def _floor_verdict(event_mean: float | None, p_one: float | None) -> dict:
    """The pre-registered economic floor, stated without softening."""
    if event_mean is None:
        return {"clears_economic_floor": None, "verdict": "INSUFFICIENT_DATA"}
    clears = event_mean >= ECONOMIC_FLOOR
    sig = p_one is not None and p_one < 0.05
    if sig and clears:
        verdict = "SIGNIFICANT AND ABOVE THE 1.10 FLOOR"
    elif sig and not clears:
        verdict = ("SIGNIFICANT AND USELESS -- the expansion ratio is below the "
                    "pre-registered 1.10 economic floor and cannot move a profit "
                    "target or an ORB threshold beyond noise and slippage")
    elif not sig and clears:
        verdict = "NOT SIGNIFICANT (ratio above the floor, but not distinguishable from control)"
    else:
        verdict = "NOT SIGNIFICANT AND BELOW THE 1.10 FLOOR"
    return {"economic_floor": ECONOMIC_FLOOR,
            "event_mean_expansion": event_mean,
            "clears_economic_floor": clears,
            "verdict": verdict}


def arm(event_vals: list[float], control_vals: list[float], *,
         label: str, metric: str, permute: bool = False) -> dict:
    rec = bspes._welch(event_vals, control_vals)
    rec["label"] = label
    rec["metric"] = metric
    # The LIFT (event mean / control mean) is a different quantity from the
    # expansion statistic itself, and the pre-registered 1.10 economic floor
    # is stated over the EXPANSION STATISTIC, not over this lift. Both are
    # reported so neither can be quietly substituted for the other.
    rec["event_over_control_ratio_NOT_the_floor_statistic"] = (
        rec["event_mean"] / rec["control_mean"]
        if rec["event_mean"] is not None and rec["control_mean"] else None)
    rec["welch_p_one_sided_event_gt_control"] = _one_sided(rec)
    rec["mde_one_sided_95_power_80"] = two_sample_mde(event_vals, control_vals)
    if permute:
        rec["permutation_null"] = permutation_null(event_vals, control_vals)
    return rec


# -------------------------------------------------------------------- primary

def primary_test(days: pd.DataFrame, event_dates: set[str]) -> dict:
    ev = days[days["date"].isin(event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = matched(control_pool(days, event_dates), weekdays)

    rec = arm(ev["expansion"].tolist(), ctrl["expansion"].tolist(),
               label="PRIMARY: expansion, event vs matched control",
               metric="expansion = RTH_true_range / ATR20_prior", permute=True)
    rec["hypothesis"] = ("SPY RTH range normalised by its own strictly-prior "
                          "ATR20 is LARGER on scheduled-macro-event days than on "
                          "matched non-event days")
    rec["one_sided_direction"] = "event > control (pre-registered)"
    rec["control_weekdays_matched_against"] = sorted(weekdays)
    rec.update(_floor_verdict(rec["event_mean"],
                               rec["welch_p_one_sided_event_gt_control"]))

    # Registered robustness arms -- neither may be promoted to primary.
    buf = matched(buffered_control_pool(days, event_dates), weekdays)
    rec["ROBUSTNESS_buffered_control_pm1_session"] = arm(
        ev["expansion"].tolist(), buf["expansion"].tolist(),
        label="expansion, event vs +/-1-session-buffered control",
        metric="expansion", permute=True)
    rec["ROBUSTNESS_rth_denominator"] = arm(
        ev["expansion_rth"].tolist(), ctrl["expansion_rth"].tolist(),
        label="expansion_rth (denominator = mean prior-20 RTH range, no gap)",
        metric="expansion_rth")
    rec["ROBUSTNESS_rth_denominator"].update(
        _floor_verdict(rec["ROBUSTNESS_rth_denominator"]["event_mean"],
                        rec["ROBUSTNESS_rth_denominator"]
                        ["welch_p_one_sided_event_gt_control"]))

    # Raw, unnormalised range. CONFOUNDED by volatility autocorrelation --
    # reported only so the size of that confound is visible, never as primary.
    rec["CONFOUNDED_raw_range_never_primary"] = arm(
        ev["rth_range"].tolist(), ctrl["rth_range"].tolist(),
        label="RAW RTH range in points -- CONFOUNDED by vol clustering",
        metric="rth_range_points")
    rec["CONFOUNDED_raw_range_never_primary"]["why_not_primary"] = (
        "macro events cluster inside high-volatility regimes; a raw-range gap "
        "can be pure volatility autocorrelation (vol was already elevated and "
        "merely persisted) and answers a different question than the primary")
    return rec


# ------------------------------------------------------------------ secondary

def secondary_family(days: pd.DataFrame, event_dates: set[str],
                      fomc_dates: set[str], fred_dates: set[str]) -> dict:
    ev = days[days["date"].isin(event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = matched(control_pool(days, event_dates), weekdays)

    tests: list[dict] = []
    skipped: list[dict] = []

    # --- A. ORB payoff, gross
    ev_orb = [r for r in ev["orb_R"].tolist() if r is not None and r == r]
    ct_orb = [r for r in ctrl["orb_R"].tolist() if r is not None and r == r]
    a = arm(ev_orb, ct_orb, label="A. ORB payoff (GROSS of costs), event vs control",
             metric="orb_R")
    a["gross_of_costs"] = True
    a["cost_note"] = ("GROSS. The bracket-harvest study already showed costs are "
                       "what kills this family -- a gross number here is a "
                       "diagnostic, not a tradeable claim.")
    a["event_days_without_a_usable_orb_R"] = int(len(ev) - len(ev_orb))
    a["control_days_without_a_usable_orb_R"] = int(len(ctrl) - len(ct_orb))
    tests.append(a)

    # --- B. Range decomposition
    tests.append(arm(ev["expansion_am"].tolist(), ctrl["expansion_am"].tolist(),
                      label="B1. 09:30-12:00 expansion, event vs control",
                      metric="expansion_am"))
    tests.append(arm(ev["expansion_pm"].tolist(), ctrl["expansion_pm"].tolist(),
                      label="B2. 12:00-16:00 expansion, event vs control",
                      metric="expansion_pm"))

    # --- C. Event-type split
    fomc_in_span = fomc_dates & set(days["date"])
    releases_only = (fred_dates & set(days["date"])) - fomc_dates
    if not fomc_in_span:
        skipped.append({"arm": "C1 FOMC", "reason": "no scheduled FOMC date falls "
                                                     "inside the study span"})
    else:
        fdays = days[days["date"].isin(fomc_in_span)]
        fctrl = matched(control_pool(days, event_dates), set(fdays["weekday"]))
        c1 = arm(fdays["expansion"].tolist(), fctrl["expansion"].tolist(),
                  label="C1. expansion on SCHEDULED FOMC days vs control",
                  metric="expansion")
        c1.update(_floor_verdict(c1["event_mean"],
                                  c1["welch_p_one_sided_event_gt_control"]))
        tests.append(c1)
    if not releases_only:
        skipped.append({"arm": "C2 releases-without-FOMC",
                         "reason": "no non-FOMC release day inside the study span"})
    else:
        rdays = days[days["date"].isin(releases_only)]
        rctrl = matched(control_pool(days, event_dates), set(rdays["weekday"]))
        c2 = arm(rdays["expansion"].tolist(), rctrl["expansion"].tolist(),
                  label="C2. expansion on 08:30 release days WITHOUT FOMC vs control",
                  metric="expansion")
        c2.update(_floor_verdict(c2["event_mean"],
                                  c2["welch_p_one_sided_event_gt_control"]))
        tests.append(c2)

    # BH within THIS family only. The primary is not a member and its p-value
    # is never touched by this correction.
    padj = mes._bh_adjust([t["welch_p_two_sided"] for t in tests])
    n_tested = sum(1 for t in tests if t["welch_p_two_sided"] is not None)
    n_sig_raw = sum(1 for t in tests if t["welch_p_two_sided"] is not None
                     and t["welch_p_two_sided"] < 0.05)
    n_sig_bh = 0
    for t, p in zip(tests, padj):
        t["welch_p_bh"] = p
        if p is not None and p < 0.05:
            n_sig_bh += 1

    return {
        "NO_INFERENTIAL_WEIGHT": True,
        "may_never_be_promoted_to_primary": True,
        "tests": tests,
        "skipped_arms": skipped,
        "multiple_testing": {
            "family": "spec 043 secondary/exploratory ONLY -- the primary is "
                       "not a member of this family",
            "tests_run": n_tested,
            "significant_raw_p_lt_0.05": n_sig_raw,
            "expected_by_chance_at_alpha_0.05": round(n_tested * 0.05, 1),
            "significant_after_bh_correction": n_sig_bh,
        },
    }


# ------------------------------------------------------- prediction scorecard

def prediction_scorecard(primary: dict, secondary: dict) -> list[dict]:
    """The three predictions recorded in specs/043_RANGE_EXPANSION.md before
    the run, scored MECHANICALLY against the result. A failed prediction is a
    finding and is written down as failed — this function has no branch that
    can soften one."""
    by_label = {t["label"][:2]: t for t in secondary["tests"]}
    out: list[dict] = []

    ev_mean = primary["event_mean"]
    out.append({
        "prediction": "expansion is elevated, likely in the 1.05-1.25 band",
        "measured": ev_mean,
        "met": ev_mean is not None and 1.05 <= ev_mean <= 1.25,
    })

    c1, c2 = by_label.get("C1"), by_label.get("C2")
    out.append({
        "prediction": "clears the 1.10 floor for FOMC and possibly not for "
                       "routine 08:30 releases",
        "measured": {"fomc_event_mean": c1["event_mean"] if c1 else None,
                      "releases_event_mean": c2["event_mean"] if c2 else None},
        "met": bool(c1 and c2 and c1["event_mean"] >= ECONOMIC_FLOOR),
        "note": "arm skipped" if not (c1 and c2) else None,
    })

    b1, b2 = by_label.get("B1"), by_label.get("B2")
    am_lift = (b1["diff_event_minus_control"] if b1 else None)
    pm_lift = (b2["diff_event_minus_control"] if b2 else None)
    out.append({
        "prediction": "the elevation decays through the day, so the 09:30-12:00 "
                       "half is larger than the afternoon half",
        "measured": {"am_event_minus_control": am_lift,
                      "pm_event_minus_control": pm_lift,
                      "am_event_mean": b1["event_mean"] if b1 else None,
                      "pm_event_mean": b2["event_mean"] if b2 else None},
        "met": bool(am_lift is not None and pm_lift is not None and am_lift > pm_lift),
    })
    return out


# ---------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SPY range-expansion study (spec 043)")
    ap.add_argument("--refresh-calendar", action="store_true",
                     help="re-fetch the FRED historical release calendar live "
                          "(needs FRED_API_KEY); default reads the cached artifact")
    a = ap.parse_args(argv)

    load_dotenv()

    try:
        frames, bar_excluded = rth_frames(PRIMARY_SYMBOL)
    except BarDataError as e:
        print(f"  REFUSED: {e}")
        return 1
    if not frames:
        print("  REFUSED: no complete SPY RTH sessions in the cache")
        return 1

    sessions = session_rows(frames)
    days, atr_dropped = attach_expansion(sessions)
    if days.empty:
        print("  REFUSED: no day has 20 strictly-prior complete sessions")
        return 1
    days = attach_orb(days, frames)

    try:
        fred_dates, fred_prov = load_fred_event_dates(refresh=a.refresh_calendar)
        fomc_dates, fomc_prov = load_scheduled_fomc_dates()
    except (RangeExpansionError, EventStudyError) as e:
        print(f"  REFUSED: {e}")
        return 1

    span_start = fred_prov.get("calendar_start") or min(fred_dates)
    span_end = fred_prov.get("calendar_end") or max(fred_dates)
    in_span = days[(days["date"] >= span_start) & (days["date"] <= span_end)]
    in_span = in_span.reset_index(drop=True)
    if in_span.empty:
        print("  REFUSED: no complete session inside the verified calendar span")
        return 1

    event_dates = (fred_dates | fomc_dates) & set(in_span["date"])

    primary = primary_test(in_span, event_dates)
    secondary = secondary_family(in_span, event_dates, fomc_dates, fred_dates)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    in_span.to_csv(DAYS_CSV, index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec": "specs/043_RANGE_EXPANSION.md (pre-registered before this script existed)",
        "question": ("the splash is real but directionless -- is a scheduled macro "
                      "event a REGIME CONDITIONER (larger day MAGNITUDE) rather than "
                      "a directional trigger?"),
        "primary_symbol": PRIMARY_SYMBOL,
        "bar_source": "data/daytrade/bars_premarket/SPY_5m.parquet (Alpaca SIP, raw)",
        "rth_definition": f"{RTH_OPEN}-16:00 ET, {FULL_RTH_BARS} x 5m bars, "
                           "impulse window (08:30) excluded by construction",
        "atr": {"window": ATR_WINDOW,
                 "definition": "Wilder TR vs the PRIOR session's RTH close, averaged "
                                "over the 20 sessions STRICTLY BEFORE the measured day",
                 "lookahead_chokepoint": "build_spy_range_expansion_study.prior_window "
                                          "refuses any end_index past the measured day"},
        "economic_floor": ECONOMIC_FLOOR,
        "calendar": {"fred": fred_prov, "fomc_scheduled_only": fomc_prov,
                      "span_start": span_start, "span_end": span_end,
                      "span_rationale": "outside the verified calendar span a day "
                                        "cannot be shown to be a NON-event day; a "
                                        "contaminated control pool is worse than a "
                                        "short one"},
        "population": {
            "complete_rth_sessions_in_cache": len(sessions),
            "sessions_excluded_incomplete_bars": len(bar_excluded),
            "sessions_excluded_no_atr20_prior": len(atr_dropped),
            "sessions_in_study_span": len(in_span),
            "event_days_in_span": len(event_dates),
            "control_days_in_span": int(len(control_pool(in_span, event_dates))),
            "excluded_bar_reasons_sample": bar_excluded[:5],
            "excluded_atr_reasons_sample": atr_dropped[:3],
        },
        "PRIMARY": primary,
        "SECONDARY_EXPLORATORY_BH_CORRECTED_WITHIN_ITS_OWN_FAMILY": secondary,
        "PREDICTION_SCORECARD": prediction_scorecard(primary, secondary),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=1, default=str))

    print("=" * 72)
    p_one = primary["welch_p_one_sided_event_gt_control"]
    perm_p = primary["permutation_null"]["p_value_one_sided_event_gt_random"]
    print(f"  PRIMARY expansion: event {primary['event_mean']:.4f} "
          f"(n={primary['event_n']}) vs control {primary['control_mean']:.4f} "
          f"(n={primary['control_n']})")
    print(f"  one-sided p={p_one}  permutation p={perm_p}  "
          f"MDE={primary['mde_one_sided_95_power_80']['mde_expansion_units']}")
    print(f"  {primary['verdict']}")
    print(f"  wrote {SUMMARY_JSON}")
    print(f"  wrote {DAYS_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
