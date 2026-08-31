#!/usr/bin/env python3
"""scripts/persist_macro_decay_split.py — close the spy_macro_decay reproducibility gap.

THE GAP
    artifacts/EVENT_STUDY_MDE.md ranked spy_macro_decay the STRONGEST of the ten
    event studies (0.22x of its detection floor) and simultaneously flagged it the
    LEAST verifiable: build_spy_macro_decay_study.py names DECAY_CSV and
    BREATH_CSV_TEMPLATE as outputs, but both belong to its EXPLORATORY blocks. The
    PRIMARY 08:30-08:35 event/control split was never written to disk at all.

    Worse, the only committed FRED artifact
    (data/daytrade/event_study/fred_historical_release_dates.json) spans
    2024-01-04..2026-08-20 with 290 events -- it was generated for a different
    study's window. The PRIMARY used 1,221 events over 2016-01-04..2026-08-26.
    So the event set that produced the headline number was not recoverable from
    committed data by any route.

WHAT REPRODUCES OFFLINE ALREADY, AND WHAT DID NOT
    The DAY UNIVERSE reproduces exactly from the committed 5m premarket cache:
    2,676 complete days, 2 excluded, same span. Only the event calendar was
    missing. This script refetches that calendar over the study's exact span,
    persists it as its OWN artifact, rebuilds the split, and verifies the recorded
    statistics reproduce.

    It does NOT overwrite fred_historical_release_dates.json -- that file is
    another study's window and clobbering it would trade one reproducibility gap
    for another.

VERDICT DISCIPLINE
    This script asserts nothing about whether the effect is real. It answers one
    question -- does the number on record reproduce from data now committed --
    and prints reported vs recomputed side by side either way. A mismatch is the
    finding, and the ruling in that case is that the row leaves the MDE table.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "daytrade"), str(ROOT)]

import build_macro_event_study as mes                    # noqa: E402
import build_spy_macro_decay_study as md                 # noqa: E402
import build_spy_premarket_event_study as bspes          # noqa: E402

OUT = ROOT / "data" / "daytrade" / "event_study"
SPLIT_CSV = OUT / "spy_macro_decay_primary_days.csv"
CAL_JSON = OUT / "spy_macro_decay_event_calendar.json"
SUMMARY = OUT / "spy_macro_decay_study_summary.json"

# Fields compared against the committed summary. Tolerance is tight: this is a
# deterministic recomputation over identical inputs, not a re-estimate.
TOL = 1e-9
FIELDS = ("event_n", "control_n", "event_mean", "control_mean",
          "diff_event_minus_control", "welch_t", "welch_p_two_sided", "cohens_d")


def main(argv=None) -> int:
    offline = "--offline" in (argv or sys.argv[1:])

    days, excluded = md.primary_bar_day_stats(md.PRIMARY_SYMBOL)
    if days.empty:
        print("REFUSED: no 08:30-08:35 SPY bars in the committed premarket cache")
        return 1
    start = date.fromisoformat(days["date"].iloc[0])
    end_d = date.fromisoformat(days["date"].iloc[-1])
    print(f"day universe: {len(days)} complete, {len(excluded)} excluded, "
          f"{start} .. {end_d}")

    # ---- event calendar over the study's EXACT span --------------------------
    if offline and CAL_JSON.exists():
        doc = json.loads(CAL_JSON.read_text())
        events = doc["events"]
        print(f"calendar: {len(events)} events from {CAL_JSON.name} (offline)")
    else:
        load_dotenv(ROOT / ".env")
        key = os.getenv("FRED_API_KEY")
        if not key:
            print("REFUSED: no FRED_API_KEY. Cannot rebuild the calendar, and the "
                  "committed one covers the wrong window.")
            return 1
        events = mes.build_event_calendar(key, start=start, end=end_d)
        CAL_JSON.write_text(json.dumps({
            "generated_at": pd.Timestamp.now("UTC").isoformat(),
            "purpose": "event calendar for spy_macro_decay PRIMARY, over the "
                       "study's exact day-universe span. Persisted so the PRIMARY "
                       "is reproducible from committed data without a FRED call.",
            "start": start.isoformat(), "end": end_d.isoformat(),
            "releases": mes.RELEASES, "n_events": len(events), "events": events,
        }, indent=1))
        print(f"calendar: {len(events)} events fetched and written to {CAL_JSON.name}")

    # ---- the split, replicating primary_test exactly -------------------------
    event_dates = {e["date"] for e in events}
    ev = days[days["date"].isin(event_dates)]
    weekdays = set(ev["weekday"])
    ctrl = bspes._matched_control(days, event_dates, weekdays)

    arm = pd.Series("unused_weekday_not_matched", index=days.index)
    arm[days["date"].isin(set(ev["date"]))] = "event"
    arm[days["date"].isin(set(ctrl["date"]))] = "control"
    out = days.assign(arm=arm, is_event=days["date"].isin(event_dates))
    out.to_csv(SPLIT_CSV, index=False)
    print(f"split written: {SPLIT_CSV.name}  "
          f"event={(out.arm=='event').sum()} control={(out.arm=='control').sum()} "
          f"unused={(out.arm=='unused_weekday_not_matched').sum()}")

    # ---- recompute and compare ----------------------------------------------
    rec = bspes._welch(ev["abs_ret"].tolist(), ctrl["abs_ret"].tolist())
    ref = json.loads(SUMMARY.read_text())["PRIMARY"]

    print(f"\n{'field':<26} {'on record':>16} {'recomputed':>16}  match")
    print("-" * 68)
    ok = True
    for f in FIELDS:
        a, b = ref.get(f), rec.get(f)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            m = abs(a - b) <= TOL * max(1.0, abs(a))
        else:
            m = a == b
        ok &= m
        fa = f"{a:.10g}" if isinstance(a, float) else str(a)
        fb = f"{b:.10g}" if isinstance(b, float) else str(b)
        print(f"{f:<26} {fa:>16} {fb:>16}  {'yes' if m else 'NO'}")

    print()
    if ok:
        print("REPRODUCES. Every recorded PRIMARY statistic is recovered from data "
              "now committed. The spy_macro_decay row stands in the MDE table.")
        print(f"  re-verify offline with: python3 {Path(__file__).name} --offline")
    else:
        print("DOES NOT REPRODUCE. Per the standing ruling, the spy_macro_decay row "
              "comes out of the MDE table until this is explained.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
