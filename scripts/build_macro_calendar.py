#!/usr/bin/env python3
"""Build `data/daytrade/macro_calendar.json` — the FRED half of the splash
schedule. See `daytrade/macro_calendar.py`'s module docstring for the full
picture (this is source 1 of 3; FOMC and the hand-written file are the
other two, and are NOT written by this script).

WHY THESE SIX RELEASES (FRED release IDs, verified live 2026-08-27 via
`fred/releases`):

  10  Consumer Price Index
  46  Producer Price Index
  50  Employment Situation           (nonfarm payrolls)
  53  Gross Domestic Product
  54  Personal Income and Outlays    (carries core PCE, the Fed's preferred gauge)
  180 Unemployment Insurance Weekly Claims Report

These are the US macro prints that routinely move an equity day-trading tape
(this repo's daytrade cockpit trades NVDA, not FX — the FX carry pairs live
in a separate, unrelated lane). FOMC is deliberately excluded from this list:
FRED's own "FOMC Press Release" release (id 101) returns a release date for
EVERY CALENDAR DAY once `include_release_dates_with_no_data=true` is set —
verified live 2026-08-27 (30 consecutive daily rows starting at
`realtime_start`) — so it is not a real schedule and using it would be
exactly the kind of fabricated-looking-real data this repo has already been
burned by twice. FOMC dates are hand-transcribed into fomc_calendar.json from
the Fed's own published calendar instead.

HORIZON (verified live 2026-08-27, `realtime_start` = today,
`sort_order=asc`, `include_release_dates_with_no_data=true`): the monthly
releases (CPI/PPI/NFP/GDP/PCE) each returned exactly 4 future dates, reaching
into mid-to-late December 2026 (~105-120 days out) before FRED's schedule
simply stopped supplying more; Initial Claims, being weekly, returned dates
out to the same December horizon. FRED does not publish further forward than
that — this is not a query-parameter limit, `limit=1000` was used and still
returned only what's shown. Re-running this script periodically is what keeps
the calendar's forward horizon from shrinking as today's date advances past
mid-2026 without new dates appearing.

FAIL LOUD: any release query that errors aborts the whole write — this never
overwrites a good file with a partial one. A missing FRED_API_KEY is a hard
stop, same discipline as `daytrade/macro_state.py::fetch()`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "macro_calendar.json"
FRED_RELEASE_DATES = "https://api.stlouisfed.org/fred/release/dates"

RELEASES: dict[str, str] = {
    "10": "Consumer Price Index",
    "46": "Producer Price Index",
    "50": "Employment Situation",
    "53": "Gross Domestic Product",
    "54": "Personal Income and Outlays",
    "180": "Unemployment Insurance Weekly Claims Report",
}


class BuildError(RuntimeError):
    """Refused to write — never partial, never guessed."""


def fetch_release_dates(release_id: str, key: str, *, as_of: date) -> list[str]:
    r = requests.get(FRED_RELEASE_DATES, params={
        "api_key": key, "file_type": "json", "release_id": release_id,
        "include_release_dates_with_no_data": "true",
        "realtime_start": as_of.isoformat(),
        "sort_order": "asc", "limit": 1000,
    }, timeout=30)
    if r.status_code != 200:
        raise BuildError(f"release {release_id} ({RELEASES.get(release_id, '?')}): "
                          f"FRED returned {r.status_code}: {r.text[:300]}")
    data = r.json()
    dates = [row["date"] for row in data.get("release_dates", [])]
    if not dates:
        raise BuildError(f"release {release_id} ({RELEASES.get(release_id, '?')}): "
                          "FRED returned zero future dates — refusing to write a "
                          "calendar missing a release entirely rather than assume "
                          "it means 'nothing scheduled'")
    return sorted(d for d in dates if d >= as_of.isoformat())


def build(as_of: date | None = None) -> dict:
    load_dotenv()
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise BuildError("FRED_API_KEY missing — cannot build the macro calendar")
    as_of = as_of or date.today()

    events = []
    for rid, name in RELEASES.items():
        for d in fetch_release_dates(rid, key, as_of=as_of):
            events.append({"date": d, "release_id": int(rid), "release_name": name})
    events.sort(key=lambda e: (e["date"], e["release_id"]))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "source": "FRED fred/release/dates API",
        "releases": RELEASES,
        "events": events,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build the FRED half of the macro calendar")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD, defaults to today")
    ap.add_argument("--print-only", action="store_true",
                     help="build and print, do not write")
    a = ap.parse_args(argv)
    try:
        as_of = date.fromisoformat(a.as_of) if a.as_of else None
        doc = build(as_of)
    except BuildError as e:
        print(f"  REFUSED: {e}")
        return 1

    print(f"  {len(doc['events'])} scheduled release(s) across "
          f"{len(RELEASES)} release types, {doc['as_of']} forward "
          f"through {doc['events'][-1]['date']}")
    if a.print_only:
        print(json.dumps(doc, indent=2))
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    tmp.replace(OUT)
    print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
