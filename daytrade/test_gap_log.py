#!/usr/bin/env python3
"""Spec 024 I18-I20: sim→live gap records, drift classification, tripwire."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gap_log


def _row(tid, r, mode, outcome="TP"):
    return {"trade_id": tid, "r_realized": r, "mode": mode, "outcome": outcome,
            "exit_timestamp": "2026-08-17T15:00:00+00:00"}


def _gaps(deltas):
    return [gap_log.GapRecord(trade_id=f"t{i}", sim_r=0.0, real_r=d, delta_r=d,
                              sim_basis="sim", real_basis="paper", ts="")
            for i, d in enumerate(deltas)]


# ------------------------------------------------------------------- I18

def test_i18_gap_requires_trade_in_both_lanes():
    sim = [_row("NVDA-1", 1.0, "sim"), _row("NVDA-2", 0.5, "sim")]
    real = [_row("NVDA-1", 0.9, "paper"), _row("NVDA-3", -1.0, "paper")]
    gaps, unmatched = gap_log.compute_gaps(sim, real)
    assert [g.trade_id for g in gaps] == ["NVDA-1"]
    assert gaps[0].delta_r == pytest.approx(-0.1)
    assert unmatched == {"sim_only": ["NVDA-2"], "real_only": ["NVDA-3"]}


def test_open_trades_never_gap_scored():
    sim = [_row("NVDA-1", 1.0, "sim")]
    real = [{**_row("NVDA-1", None, "paper"), "outcome": "OPEN", "r_realized": None}]
    gaps, unmatched = gap_log.compute_gaps(sim, real)
    assert gaps == []


def test_closed_trade_without_r_raises():
    with pytest.raises(gap_log.GapError, match="no r_realized"):
        gap_log.compute_gaps([{**_row("t", 1.0, "sim"), "r_realized": None}], [])


# ------------------------------------------------------------------- I19

def test_i19_under_ten_rows_reads_insufficient():
    assert gap_log.classify_drift(_gaps([0.01] * 9)) == "insufficient"


def test_i19_constant_drift():
    assert gap_log.classify_drift(_gaps([0.02] * 10)) == "constant"


def test_i19_widening_drift():
    assert gap_log.classify_drift(_gaps([0.02] * 5 + [0.10] * 5)) == "widening"


def test_i19_collapsing_drift():
    assert gap_log.classify_drift(_gaps([0.10] * 5 + [0.02] * 5)) == "collapsing"


# ------------------------------------------------------------------- I20

def test_i20_tripwire_fires_on_widening_beyond_band():
    reason = gap_log.promotion_tripwire(_gaps([0.02] * 5 + [0.10] * 5))
    assert reason is not None and "WIDENING" in reason


def test_tripwire_silent_when_widening_but_inside_band():
    assert gap_log.promotion_tripwire(_gaps([0.01] * 5 + [0.04] * 5)) is None


def test_tripwire_silent_on_insufficient_data():
    # an unmeasured gap cannot fire a tripwire — it is reported, not guessed
    assert gap_log.promotion_tripwire(_gaps([0.5] * 9)) is None


def test_tripwire_silent_on_constant_drift_even_if_large():
    # constant big offset = usable proxy with an offset (pre-committed reading)
    assert gap_log.promotion_tripwire(_gaps([0.2] * 10)) is None
