"""Daily (not lifetime) spend-cap semantics for news_claude.py.

The cap used to sum the ENTIRE llm_spend.jsonl ledger forever, so a project
with $5 of lifetime headroom would silently stop making judgments partway
through the project's life with no daily reset. Colin's decision: one daily
cap, resetting at midnight America/New_York, refusing loudly at the boundary.
See NEXT.md / the dispatch note for the full rationale.

All fixtures write to a tmp-dir ledger — never data/daytrade/llm_spend.jsonl.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

import news_claude as nc

ET = ZoneInfo("America/New_York")


@pytest.fixture
def sandbox_ledger(tmp_path, monkeypatch):
    """Point news_claude.SPEND at a scratch file for the duration of the test."""
    fake = tmp_path / "llm_spend.jsonl"
    monkeypatch.setattr(nc, "SPEND", fake)
    return fake


def _write_row(path, ts: str, cost: float):
    with path.open("a") as fh:
        fh.write(json.dumps({
            "ts": ts, "model": "claude-sonnet-5", "kind": "delta",
            "input": 100, "output": 50, "cache_write": 0, "cache_read": 0,
            "cost_usd": cost,
        }) + "\n")


def test_previous_et_day_spend_does_not_count_against_today(sandbox_ledger):
    # "Now" is 2026-08-21 10:00 ET. Yesterday's spend was large.
    now = datetime(2026, 8, 21, 10, 0, tzinfo=ET)
    yesterday_ts = datetime(2026, 8, 20, 23, 59, tzinfo=ET).isoformat()
    _write_row(sandbox_ledger, yesterday_ts, 4.75)

    assert nc.spent_today(now) == pytest.approx(0.0)
    # And the budget check must not raise, even though the lifetime ledger
    # holds far more than a $0.50 cap.
    used = nc.check_budget(0.50, now=now)
    assert used == pytest.approx(0.0)


def test_todays_et_day_spend_does_count(sandbox_ledger):
    now = datetime(2026, 8, 21, 10, 0, tzinfo=ET)
    today_ts = datetime(2026, 8, 21, 9, 30, tzinfo=ET).isoformat()
    _write_row(sandbox_ledger, today_ts, 0.12)
    # A row logged in UTC that lands on the same ET calendar day should also count.
    utc_today_ts = datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc).isoformat()  # 11:00 ET
    _write_row(sandbox_ledger, utc_today_ts, 0.08)

    assert nc.spent_today(now) == pytest.approx(0.20)


def test_cap_raises_at_the_boundary(sandbox_ledger):
    now = datetime(2026, 8, 21, 12, 0, tzinfo=ET)
    _write_row(sandbox_ledger, datetime(2026, 8, 21, 9, 0, tzinfo=ET).isoformat(), 0.50)

    with pytest.raises(nc.SpendCapReached) as exc:
        nc.check_budget(0.50, now=now)
    msg = str(exc.value)
    assert "0.5000" in msg or "0.50" in msg
    assert "TODAY" in msg
    assert "Resets" in msg

    # Just under the cap must not raise.
    sandbox_ledger.write_text("")
    _write_row(sandbox_ledger, datetime(2026, 8, 21, 9, 0, tzinfo=ET).isoformat(), 0.49)
    used = nc.check_budget(0.50, now=now)
    assert used == pytest.approx(0.49)


def test_naive_timestamp_fails_loud_rather_than_silently_scoped(sandbox_ledger):
    now = datetime(2026, 8, 21, 10, 0, tzinfo=ET)
    # No tzinfo at all — this is a corrupt ledger row per CLAUDE.md rule 3.
    _write_row(sandbox_ledger, "2026-08-21T09:30:00", 0.05)

    with pytest.raises(nc.CorruptLedgerRow):
        nc.spent_today(now)

    with pytest.raises(nc.CorruptLedgerRow):
        nc.check_budget(0.50, now=now)


def test_unparseable_timestamp_also_fails_loud(sandbox_ledger):
    now = datetime(2026, 8, 21, 10, 0, tzinfo=ET)
    _write_row(sandbox_ledger, "not-a-timestamp", 0.05)

    with pytest.raises(nc.CorruptLedgerRow):
        nc.spent_today(now)


def test_deliberate_violation_removing_day_filter_breaks_isolation(sandbox_ledger):
    """CLAUDE.md's VERIFIED bar: deliberately reintroducing the old lifetime-sum
    bug (no day filter) must make the previous-day-isolation test fail. This
    proves test_previous_et_day_spend_does_not_count_against_today actually
    exercises the day-scoping logic rather than passing vacuously."""
    now = datetime(2026, 8, 21, 10, 0, tzinfo=ET)
    yesterday_ts = datetime(2026, 8, 20, 23, 59, tzinfo=ET).isoformat()
    _write_row(sandbox_ledger, yesterday_ts, 4.75)

    def lifetime_sum(_now=None):
        return sum(json.loads(l)["cost_usd"] for l in sandbox_ledger.open() if l.strip())

    # The old (buggy) behavior: yesterday's spend counts against today.
    assert lifetime_sum() == pytest.approx(4.75)
    # The fixed behavior must differ from the buggy one on this exact fixture.
    assert nc.spent_today(now) != lifetime_sum()
    assert nc.spent_today(now) == pytest.approx(0.0)


def test_spent_so_far_still_reports_lifetime_total(sandbox_ledger):
    """spent_so_far() is retained for --spend's lifetime-context line; it must
    keep summing everything regardless of date."""
    _write_row(sandbox_ledger, datetime(2026, 8, 20, 9, 0, tzinfo=ET).isoformat(), 0.30)
    _write_row(sandbox_ledger, datetime(2026, 8, 21, 9, 0, tzinfo=ET).isoformat(), 0.20)
    assert nc.spent_so_far() == pytest.approx(0.50)
