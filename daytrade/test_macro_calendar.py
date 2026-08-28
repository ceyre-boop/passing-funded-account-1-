#!/usr/bin/env python3
"""macro_calendar.py — the splash-schedule merge. Fault injection for every
stated invariant: never fabricate, never silently treat corruption as
absence, event_risk weights match RELEASE_IMPORTANCE, staleness fails loud."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import macro_calendar as mc


def _write(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj))


@pytest.fixture
def sources(tmp_path):
    return {
        "calendar_json": tmp_path / "macro_calendar.json",
        "fomc_json": tmp_path / "fomc_calendar.json",
        "calendar_md": tmp_path / "macro_calendar.md",
    }


# --------------------------------------------------------------- absence

def test_all_sources_absent_is_not_an_event_day(sources):
    assert mc.is_event_day("2026-09-11", **sources) is False
    assert mc.scheduled_events("2026-09-11", **sources) == []


def test_absence_is_stated_in_event_risk_detail(sources):
    value, detail = mc.event_risk("2026-09-11", **sources)
    assert value == 0.0
    assert "no scheduled event" in detail
    assert "2026-09-11" in detail


# ---------------------------------------------------------------- fred

def test_fred_event_on_date_is_an_event_day(sources):
    _write(sources["calendar_json"], {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": [{"date": "2026-09-11", "release_id": 10,
                    "release_name": "Consumer Price Index"}],
    })
    assert mc.is_event_day("2026-09-11", **sources) is True
    assert mc.is_event_day("2026-09-12", **sources) is False


def test_fred_event_off_date_does_not_leak(sources):
    _write(sources["calendar_json"], {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": [{"date": "2026-09-11", "release_id": 10,
                    "release_name": "Consumer Price Index"}],
    })
    events = mc.scheduled_events("2026-10-01", **sources)
    assert events == []


# ---------------------------------------------------------------- fomc

def test_fomc_event_on_date_is_an_event_day(sources):
    _write(sources["fomc_json"], {
        "verified_as_of": date.today().isoformat(),
        "source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "events": [{"date": "2026-09-16", "label": "FOMC decision"}],
    })
    assert mc.is_event_day("2026-09-16", **sources) is True


# ---------------------------------------------------------------- manual md

def test_manual_md_dated_line_is_an_event_day(sources):
    sources["calendar_md"].write_text("2026-09-05 (Fri)\n  NVDA earnings\n")
    assert mc.is_event_day("2026-09-05", **sources) is True


# ---------------------------------------------------------------- corruption

def test_corrupt_fred_json_raises_not_silently_absent(sources):
    sources["calendar_json"].write_text("{not valid json")
    with pytest.raises(mc.MacroCalendarError):
        mc.is_event_day("2026-09-11", **sources)


def test_corrupt_fomc_json_raises_not_silently_absent(sources):
    sources["fomc_json"].write_text("{not valid json")
    with pytest.raises(mc.MacroCalendarError):
        mc.is_event_day("2026-09-16", **sources)


# -------------------------------------------------------- event_risk weights

def test_event_risk_high_impact_release(sources):
    _write(sources["calendar_json"], {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": [{"date": "2026-09-04", "release_id": 50,
                    "release_name": "Employment Situation"}],
    })
    value, detail = mc.event_risk("2026-09-04", **sources)
    assert value == 1.0
    assert "Employment Situation" in detail


def test_event_risk_lower_for_weekly_claims(sources):
    _write(sources["calendar_json"], {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": [{"date": "2026-09-03", "release_id": 180,
                    "release_name": "Unemployment Insurance Weekly Claims Report"}],
    })
    value, _ = mc.event_risk("2026-09-03", **sources)
    assert value == pytest.approx(0.3)


def test_event_risk_fomc_is_maximum_importance(sources):
    _write(sources["fomc_json"], {
        "verified_as_of": date.today().isoformat(),
        "source_url": "x",
        "events": [{"date": "2026-09-16", "label": "FOMC decision"}],
    })
    value, _ = mc.event_risk("2026-09-16", **sources)
    assert value == mc.FOMC_IMPORTANCE == 1.0


def test_event_risk_takes_max_when_multiple_sources_fire_same_day(sources):
    _write(sources["calendar_json"], {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": [{"date": "2026-09-16", "release_id": 46,
                    "release_name": "Producer Price Index"}],
    })
    _write(sources["fomc_json"], {
        "verified_as_of": date.today().isoformat(), "source_url": "x",
        "events": [{"date": "2026-09-16", "label": "FOMC decision"}],
    })
    value, detail = mc.event_risk("2026-09-16", **sources)
    assert value == 1.0                     # FOMC (1.0) beats PPI (0.6)
    assert "Producer Price Index" in detail  # but both are still reported
    assert "FOMC decision" in detail


# -------------------------------------------------------------- freshness

def test_freshness_flags_missing_sources_as_stale(sources):
    f = mc.freshness(calendar_json=sources["calendar_json"],
                      fomc_json=sources["fomc_json"])
    assert f["stale"] is True
    assert any("missing" in r for r in f["reasons"])


def test_freshness_passes_when_both_sources_current(sources):
    _write(sources["calendar_json"], {
        "generated_at": datetime.now(timezone.utc).isoformat(), "events": [],
    })
    _write(sources["fomc_json"], {
        "verified_as_of": date.today().isoformat(), "source_url": "x", "events": [],
    })
    f = mc.freshness(calendar_json=sources["calendar_json"],
                      fomc_json=sources["fomc_json"])
    assert f["stale"] is False
    assert f["reasons"] == []


def test_freshness_fails_loud_on_old_fred_json(sources):
    old = datetime.now(timezone.utc) - timedelta(days=mc.FRED_STALE_DAYS + 1)
    _write(sources["calendar_json"], {"generated_at": old.isoformat(), "events": []})
    _write(sources["fomc_json"], {
        "verified_as_of": date.today().isoformat(), "source_url": "x", "events": [],
    })
    f = mc.freshness(calendar_json=sources["calendar_json"],
                      fomc_json=sources["fomc_json"])
    assert f["stale"] is True
    assert any("macro_calendar.json" in r for r in f["reasons"])


def test_freshness_fails_loud_on_old_fomc_verification(sources):
    old = (date.today() - timedelta(days=mc.FOMC_STALE_DAYS + 1)).isoformat()
    _write(sources["calendar_json"], {
        "generated_at": datetime.now(timezone.utc).isoformat(), "events": [],
    })
    _write(sources["fomc_json"], {"verified_as_of": old, "source_url": "x", "events": []})
    f = mc.freshness(calendar_json=sources["calendar_json"],
                      fomc_json=sources["fomc_json"])
    assert f["stale"] is True
    assert any("fomc_calendar.json" in r for r in f["reasons"])


def test_assert_fresh_raises_when_stale(sources):
    with pytest.raises(mc.MacroCalendarError):
        mc.assert_fresh(calendar_json=sources["calendar_json"],
                         fomc_json=sources["fomc_json"])


def test_assert_fresh_passes_when_fresh(sources):
    _write(sources["calendar_json"], {
        "generated_at": datetime.now(timezone.utc).isoformat(), "events": [],
    })
    _write(sources["fomc_json"], {
        "verified_as_of": date.today().isoformat(), "source_url": "x", "events": [],
    })
    mc.assert_fresh(calendar_json=sources["calendar_json"],
                     fomc_json=sources["fomc_json"])   # must not raise


# -------------------------------------------------------- real repo files

def test_real_repo_calendars_are_currently_fresh():
    """The files this task actually shipped — catches drift going forward."""
    f = mc.freshness()
    assert f["stale"] is False, f["reasons"]


def test_real_fomc_calendar_has_no_fabricated_looking_pattern():
    """The FRED FOMC release (id 101) is known-bad: it returns a date for
    every single day (verified live: 30 consecutive daily rows). This test
    catches that exact signature regressing into fomc_calendar.json.

    Originally this was a flat >=20-day floor on every consecutive pair —
    correct in spirit (FOMC meets roughly every 6-8 weeks) but it produced a
    false positive on verified, sourced data: the March 2020 COVID emergency
    actions (2020-03-03, 2020-03-15) are real FOMC decisions, cited from
    federalreserve.gov/monetarypolicy/fomchistorical2020.htm, only 12 days
    apart. Dropping them to satisfy the heuristic would have meant deleting
    real data to please a test — the wrong fix.

    The corrected, STRICTER discipline: an unflagged pair must still be
    >=20 days apart. A pair narrower than that is allowed ONLY if the later
    event carries `unscheduled: true` AND its own `source_url` pointing at
    the specific Fed page documenting that meeting — so a dense calendar can
    never slip through by accident, only by someone deliberately marking and
    sourcing every single dense entry. On top of that, NO pair may ever be
    <2 days apart under any flag — that is the actual release-101 signature
    (a date for every calendar day) and no legitimate FOMC action, scheduled
    or emergency, has ever landed on back-to-back days. Finally, meetings per
    calendar year are capped at 12 (double the normal 8) as a sanity backstop
    against a dense fabrication being smuggled in one `unscheduled: true` flag
    at a time.
    """
    doc = json.loads(mc.FOMC_CALENDAR_JSON.read_text())
    violations = mc.validate_fomc_events(doc["events"])
    assert violations == []


# ------------------------------------- fabrication guard, deliberate violations

def test_fabrication_guard_catches_release_101_daily_signature():
    """The exact bug this guard exists for: a date for every calendar day."""
    events = [{"date": f"2026-01-{d:02d}"} for d in range(1, 15)]
    violations = mc.validate_fomc_events(events)
    assert any("absolute floor" in v for v in violations)


def test_fabrication_guard_rejects_unflagged_close_pair():
    events = [{"date": "2026-01-27"}, {"date": "2026-02-10"}]  # 14d, unflagged
    violations = mc.validate_fomc_events(events)
    assert any("not marked unscheduled" in v for v in violations)


def test_fabrication_guard_rejects_flagged_but_unsourced_pair():
    events = [{"date": "2026-01-27"},
              {"date": "2026-02-10", "unscheduled": True}]  # no source_url
    violations = mc.validate_fomc_events(events)
    assert any("no per-meeting source_url" in v for v in violations)


def test_fabrication_guard_accepts_flagged_and_sourced_pair():
    """The March 2020 shape: 12d apart, flagged, sourced — must pass clean."""
    events = [{"date": "2026-01-27"},
              {"date": "2026-02-10", "unscheduled": True, "source_url": "https://x"}]
    assert mc.validate_fomc_events(events) == []


def test_fabrication_guard_absolute_floor_cannot_be_flagged_away():
    """Even `unscheduled: true` + a source_url cannot excuse a <2-day gap —
    that density is never legitimate."""
    events = [{"date": "2026-01-27"},
              {"date": "2026-01-28", "unscheduled": True, "source_url": "https://x"}]
    violations = mc.validate_fomc_events(events)
    assert any("absolute floor" in v for v in violations)


def test_fabrication_guard_caps_meetings_per_year():
    # 13 events, all flagged/sourced and 25d apart pairwise (>=20d, so the
    # gap rule alone would pass) -- only the per-year cap catches this.
    base = date(2026, 1, 1)
    events = [{"date": (base + timedelta(days=25 * i)).isoformat(),
               "unscheduled": True, "source_url": "https://x"} for i in range(13)]
    violations = mc.validate_fomc_events(events)
    assert any("cap is" in v for v in violations)


def test_fabrication_guard_allows_real_march_2020_shape():
    """Positive control: the actual production case that forced this design."""
    events = [{"date": "2020-01-29"},
              {"date": "2020-03-03", "unscheduled": True,
               "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20200303a.htm"},
              {"date": "2020-03-15", "unscheduled": True,
               "source_url": "https://www.federalreserve.gov/monetarypolicy/fomchistorical2020.htm"},
              {"date": "2020-04-29"}]
    assert mc.validate_fomc_events(events) == []
