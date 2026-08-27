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


# ---------------------------------------------------------------------------
# Durability + completeness of record_spend / _call, with fault injection.
#
# Two prior confirmed defects, both silent-data-loss of the CLAUDE.md rule-3
# kind: (1) record_spend() wrote with a plain buffered append, no flush/fsync,
# so a crash after a paid call could lose the row and let spent_today()
# undercount past the true cap; (2) the messages.parse() fallback path
# validated the response INSIDE the SDK before returning, so a validation
# failure on that path was billed but never reached record_spend() at all.
# ---------------------------------------------------------------------------

class _FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.usage = _FakeUsage()
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, text: str):
        self._text = text

    def create(self, **kw):
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str):
        self.messages = _FakeMessages(text)


def test_record_spend_flushes_and_fsyncs_every_row(sandbox_ledger, monkeypatch):
    """Fault injection: if the fsync call is ever removed from record_spend,
    this test must fail. It does not merely check the row landed on disk (a
    plain buffered write would also satisfy that from within the same
    process) — it asserts os.fsync is actually invoked."""
    calls = []
    real_fsync = nc.os.fsync

    def spy_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(nc.os, "fsync", spy_fsync)

    nc.record_spend("claude-sonnet-5", _FakeUsage(), 0.001, "delta")

    assert len(calls) == 1, "record_spend() must call os.fsync exactly once per row"
    rows = [json.loads(l) for l in sandbox_ledger.open() if l.strip()]
    assert len(rows) == 1
    assert rows[0]["cost_usd"] == pytest.approx(0.001)


def test_billed_call_with_unparseable_response_still_gets_ledgered(sandbox_ledger, monkeypatch):
    """Fault injection for defect 2: a response that fails schema validation
    AFTER being billed must still leave a ledger row. Before the fix, the
    messages.parse() fallback validated inside the SDK and raised before our
    code ever saw `usage`, so this exact scenario silently lost the row."""
    monkeypatch.setattr(nc, "_client", lambda: _FakeClient("this is not valid json at all"))
    monkeypatch.setattr(nc, "cost_of", lambda usage, model: 0.01234)

    with pytest.raises(Exception):
        nc._call([], [], nc.DeltaRead, model="claude-sonnet-5", cap=1.0,
                  effort="low", kind="delta")

    rows = [json.loads(l) for l in sandbox_ledger.open() if l.strip()]
    assert len(rows) == 1, "a billed call must be ledgered even when validation fails after it"
    assert rows[0]["cost_usd"] == pytest.approx(0.01234)
    assert rows[0]["model"] == "claude-sonnet-5"
    assert rows[0]["kind"] == "delta"


def test_billed_call_with_unparseable_response_ledgered_via_public_fallback_too(
    sandbox_ledger, monkeypatch
):
    """Same fault injection, but forcing the except-ImportError branch (the
    private anthropic.lib._parse helpers unavailable) — the branch the
    original defect actually lived in. Must still ledger before validating."""
    import builtins

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith("anthropic.lib._parse"):
            raise ImportError(f"simulated: {name} moved")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    monkeypatch.setattr(nc, "_client", lambda: _FakeClient("this is not valid json at all"))
    monkeypatch.setattr(nc, "cost_of", lambda usage, model: 0.02468)

    with pytest.raises(Exception):
        nc._call([], [], nc.DeltaRead, model="claude-sonnet-5", cap=1.0,
                  effort="low", kind="delta")

    rows = [json.loads(l) for l in sandbox_ledger.open() if l.strip()]
    assert len(rows) == 1, "the public-API fallback path must ledger before validating too"
    assert rows[0]["cost_usd"] == pytest.approx(0.02468)


def test_check_budget_runs_before_the_api_call(monkeypatch):
    """Regression guard: check_budget() must still run BEFORE the client is
    constructed / messages.create() is invoked. If that ordering is ever
    flipped, an over-cap call would reach the API before being refused."""
    def refuse(cap, now=None):
        raise nc.SpendCapReached("cap hit — simulated for ordering test")

    monkeypatch.setattr(nc, "check_budget", refuse)

    def fail_client():
        raise AssertionError(
            "API client was constructed — check_budget() must block the call "
            "before _client() is ever reached")

    monkeypatch.setattr(nc, "_client", fail_client)

    with pytest.raises(nc.SpendCapReached):
        nc._call([], [], nc.DeltaRead, model="claude-sonnet-5", cap=0.01,
                  effort="low", kind="delta")


def test_exactly_one_billed_call_site():
    """Two billed paths mean two chances to get the ledger wrong, and the second
    is the one that gets forgotten — `read_news` was exactly that, a
    messages.parse() call with record_spend() after it. It had no callers, so
    nothing was losing money, but it was one wiring away from doing so."""
    import re
    from pathlib import Path
    src = Path(__file__).with_name("news_claude.py").read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    # strip docstrings so prose mentioning the old API does not count
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    assert code.count("client.messages.create(") == 1, "more than one billed path"
    assert "client.messages.parse(" not in code, (
        "messages.parse() runs its validator inside the SDK, so a schema failure "
        "raises before usage is visible — billed, never ledgered")
