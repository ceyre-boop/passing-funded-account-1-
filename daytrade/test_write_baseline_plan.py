#!/usr/bin/env python3
"""Invariant test for write_baseline_plan.py's trade_id — the join key that
threads plan.json through to alpha_operator.py's sealed records and
runner.py's execution-ledger rows (the observed-predicted roadmap's Stage 0).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import write_baseline_plan as wbp
import streak as streak_mod
from ceiling import Entry


class _FixedDatetime(datetime):
    """`main()` calls `datetime.now(ET)` directly — pin it past 10:00 ET on a
    known date so the test is deterministic regardless of wall-clock time."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 8, 12, 15, 0, tzinfo=tz)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    monkeypatch.setattr(wbp, "PLAN", plan_path)
    monkeypatch.setattr(wbp, "CAMPAIGN_CONFIG",
                        wbp.ROOT / "data" / "daytrade" / "__no_such_campaign_config.json")
    monkeypatch.setattr(wbp, "datetime", _FixedDatetime)
    monkeypatch.setattr(wbp.streak_mod, "load_state",
                        lambda path=None: streak_mod.StreakState())
    monkeypatch.setattr(wbp, "load_partial_session", lambda *a, **kw: object())
    monkeypatch.setattr(wbp, "find_entry", lambda sess: Entry(
        day="2026-08-12", ts="2026-08-12T14:35:00+00:00", time_block="OPEN_DRIVE",
        direction=1, entry=204.0, stop=202.0, risk=2.0, tp1=204.9, tp2=205.8,
        trail_dist=0.5))
    return plan_path


def test_written_plan_carries_the_derivable_trade_id(sandbox):
    """Same scheme as runner.py's `trade_id = f"{symbol}-{session_date}"` and
    alpha_operator.py's `_session_trade_id()` — one id, threaded."""
    assert wbp.main("NVDA") == 0
    plan = json.loads(sandbox.read_text())
    assert plan["trade_id"] == "NVDA-2026-08-12"
    assert plan["trade_id"] == f"{plan['symbol']}-{plan['_session']}"
