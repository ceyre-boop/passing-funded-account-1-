#!/usr/bin/env python3
"""MACRO CALENDAR — the splash schedule. Read this module's docstring before
`decision_ledger._event_day()` or `regime_vector.py`'s `event_risk` dimension;
both consume it.

THE THESIS THIS SERVES (owner's model): a scheduled macro event is a splash
in a pool. You cannot read the ripple pattern on the bottom while staring at
sun glare on the surface — but knowing WHEN the splash happens turns an
otherwise-impossible inference into a tractable one. AlphaZero's whole job in
this architecture depends on knowing when the market is about to be hit.

THREE SOURCES, merged, never conflated:

  1. FRED (`fred/release/dates`) — machine-fetched by
     `scripts/build_macro_calendar.py`, written to `data/daytrade/
     macro_calendar.json`. SCHEDULED, forward-looking, ~90-125 days deep
     (verified live 2026-08-27; see that script's docstring for the per-
     release horizon actually observed). Covers CPI, PPI, the Employment
     Situation (NFP), GDP, Personal Income and Outlays (core PCE), and the
     weekly Initial Claims report.

  2. FOMC — FRED's own "FOMC Press Release" release (id 101) returns a
     release date for EVERY CALENDAR DAY once `include_release_dates_with_no_
     data=true` is set (verified live 2026-08-27: 30 consecutive daily rows
     from `realtime_start`) — it is not a real schedule and must never be
     used. The Federal Reserve's own published calendar
     (federalreserve.gov/monetarypolicy/fomccalendars.htm) is the real
     source, but it has no API — so it is HAND-TRANSCRIBED into
     `data/daytrade/fomc_calendar.json`, carrying `verified_as_of` and
     `source_url` so staleness is checkable rather than assumed. The Fed
     publishes these roughly a year ahead and they rarely move; re-verify
     periodically, not automatically.

  3. The legacy hand-written `data/daytrade/macro_calendar.md` — ADDITIVE,
     for anything a human wants to record that the two machine sources do
     not carry (a single stock's earnings date, an unscheduled event once
     it's known, ad hoc notes). Kept working exactly as `_event_day()` used
     it before: a `YYYY-MM-DD` substring match against the file text.

NEVER FABRICATE. A wrong date here is worse than a missing one — this repo
spent two days removing a fabricated flat CPI line and a fabricated central-
bank decision file. Every date in `macro_calendar.json` and
`fomc_calendar.json` traces to a live, cited fetch (see each file's own
provenance fields). A source that cannot currently be verified is treated as
ABSENT, never guessed.

SCHEDULED vs UNSCHEDULED: everything this module reports is, by construction,
something that was knowable in advance (a published release calendar). It
answers "was a splash scheduled", not "did anything happen" — that
distinction is the whole point of the thesis and must not be blurred.

NO LOOK-AHEAD: `is_event_day` / `event_risk` answer only "is `day` on a
calendar that says an event is scheduled" — they do not consult whether the
event's DATA has since been observed. Labeling a historical day still only
uses what the schedule said, which is exactly what was knowable at the time
the schedule was published. Callers doing point-in-time backfill (spec 024
I14 discipline) must still pass the calendar as it stood, not the current
one, if that distinction ever matters for a specific study — today's callers
(decision_ledger) only use this for regime labeling, not for a return-
predicting feature, so a single current-calendar read is the correct and
sufficient granularity.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MACRO_CALENDAR_JSON = ROOT / "data" / "daytrade" / "macro_calendar.json"
FOMC_CALENDAR_JSON = ROOT / "data" / "daytrade" / "fomc_calendar.json"
MACRO_CALENDAR_MD = ROOT / "data" / "daytrade" / "macro_calendar.md"

# How impactful each scheduled release is, 0-1, for the event_risk dimension
# (regime_vector.SPEC: "a scheduled catalyst can reprice this today"). FOMC,
# CPI, NFP and core PCE (Personal Income and Outlays) are the releases that
# routinely move the tape hardest; PPI/GDP move it less; weekly claims least
# of the six, and it publishes every week so weighting it like NFP would make
# nearly every session read as high event risk, which is false.
RELEASE_IMPORTANCE: dict[str, float] = {
    "Employment Situation": 1.0,
    "Consumer Price Index": 1.0,
    "Personal Income and Outlays": 1.0,
    "Producer Price Index": 0.6,
    "Gross Domestic Product": 0.6,
    "Unemployment Insurance Weekly Claims Report": 0.3,
}
FOMC_IMPORTANCE = 1.0
MANUAL_IMPORTANCE = 0.8      # a human judged it worth writing down

# Staleness limits. FRED's own schedule almost never moves once published,
# but the window this module can see shrinks every day that
# build_macro_calendar.py isn't re-run (the query is always "from today
# forward"), so it is re-run well inside its own horizon. FOMC dates are
# published ~1yr out by the Fed and essentially never move; the wider limit
# reflects that lower change-rate, not laziness about checking it.
FRED_STALE_DAYS = 14
FOMC_STALE_DAYS = 150


class MacroCalendarError(RuntimeError):
    """A calendar source exists but could not be read. Distinct from a
    source being ABSENT (missing file = legitimately 'nothing known yet') —
    a present-but-corrupt file is a bug and must never be silently treated
    as an empty calendar."""


def _load_json(path: Path) -> dict | None:
    """None = source absent (legitimate, stated). Raises on a present file
    that cannot be parsed — that is corruption, not absence."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise MacroCalendarError(f"{path}: present but not valid JSON ({e})") from e


def _as_date(day) -> date:
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    return date.fromisoformat(str(day))


# --------------------------------------------------------------- the merge

def scheduled_events(day, *, calendar_json: Path = MACRO_CALENDAR_JSON,
                      fomc_json: Path = FOMC_CALENDAR_JSON,
                      calendar_md: Path = MACRO_CALENDAR_MD) -> list[dict]:
    """Every event any source has scheduled for `day`. Each entry carries its
    own source and importance so a caller can weight or filter."""
    d = _as_date(day)
    iso = d.isoformat()
    out: list[dict] = []

    fred = _load_json(calendar_json)
    if fred:
        for ev in fred.get("events", []):
            if ev.get("date") == iso:
                name = ev.get("release_name", "")
                out.append({
                    "date": iso, "source": "fred", "name": name,
                    "importance": RELEASE_IMPORTANCE.get(name, 0.5),
                })

    fomc = _load_json(fomc_json)
    if fomc:
        for ev in fomc.get("events", []):
            if ev.get("date") == iso:
                out.append({
                    "date": iso, "source": "fomc_manual",
                    "name": ev.get("label", "FOMC decision"),
                    "importance": FOMC_IMPORTANCE,
                })

    if calendar_md.exists() and iso in calendar_md.read_text():
        out.append({
            "date": iso, "source": "manual_md", "name": "hand-written entry",
            "importance": MANUAL_IMPORTANCE,
        })

    return out


def is_event_day(day, **kwargs) -> bool:
    """True iff any source has a scheduled event on `day`. Absent sources
    contribute nothing — never a guess."""
    return len(scheduled_events(day, **kwargs)) > 0


def event_risk(day, **kwargs) -> tuple[float, str]:
    """`(value, detail)` in `regime_vector`'s `judged` shape for the
    `event_risk` dimension: max importance among today's scheduled events, or
    (0.0, ...) with the absence stated if none. Usage:

        judged={"event_risk": macro_calendar.event_risk(day)}
        regime_vector.compute(..., judged=judged)
    """
    events = scheduled_events(day, **kwargs)
    if not events:
        return 0.0, f"no scheduled event found for {_as_date(day).isoformat()} " \
                     "across fred/fomc_manual/manual_md sources"
    best = max(events, key=lambda e: e["importance"])
    names = ", ".join(f"{e['name']} ({e['source']})" for e in events)
    return best["importance"], f"{_as_date(day).isoformat()}: {names}"


# ------------------------------------------------------------- freshness

def freshness(*, now: datetime | None = None,
               calendar_json: Path = MACRO_CALENDAR_JSON,
               fomc_json: Path = FOMC_CALENDAR_JSON) -> dict:
    """Age of each machine-checkable source and whether it has gone stale.
    Never silently used to hide a dead calendar — callers that need a hard
    gate use `assert_fresh()`."""
    now = now or datetime.now(timezone.utc)
    out: dict = {"stale": False, "reasons": []}

    fred = _load_json(calendar_json)
    if fred is None:
        out["fred_age_days"] = None
        out["stale"] = True
        out["reasons"].append("macro_calendar.json missing — no FRED-sourced events")
    else:
        gen = datetime.fromisoformat(fred["generated_at"])
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
        age = (now - gen).days
        out["fred_age_days"] = age
        if age > FRED_STALE_DAYS:
            out["stale"] = True
            out["reasons"].append(
                f"macro_calendar.json is {age}d old (limit {FRED_STALE_DAYS}d) — "
                "re-run scripts/build_macro_calendar.py")

    fomc = _load_json(fomc_json)
    if fomc is None:
        out["fomc_age_days"] = None
        out["stale"] = True
        out["reasons"].append("fomc_calendar.json missing — no FOMC dates")
    else:
        ver = date.fromisoformat(fomc["verified_as_of"])
        age = (now.date() - ver).days
        out["fomc_age_days"] = age
        if age > FOMC_STALE_DAYS:
            out["stale"] = True
            out["reasons"].append(
                f"fomc_calendar.json verified_as_of is {age}d old "
                f"(limit {FOMC_STALE_DAYS}d) — re-verify against "
                f"{fomc.get('source_url', 'the Fed calendar')}")

    return out


def assert_fresh(**kwargs) -> None:
    """Fail loud when the calendar has gone stale. Mirrors
    `scripts/carry_scan.py`'s preflight staleness discipline for the macro
    cache — an empty/quiet event_risk reading is only informative if the
    inputs behind it are current."""
    f = freshness(**kwargs)
    if f["stale"]:
        raise MacroCalendarError("macro calendar stale: " + "; ".join(f["reasons"]))
