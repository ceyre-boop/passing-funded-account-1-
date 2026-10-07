"""Spec 004 — scorecard grading, baselines, calibration, append-only ledger.

Every number asserted here was computed by hand and the arithmetic is written out
in the test's docstring. A test that only asserts "it returned something" cannot
tell a correct grader from a broken one, and the whole point of this module is to
be the one thing in the repo that cannot flatter itself.

ATR IS 1.00 IN EVERY FIXTURE so the thresholds read directly as prices:
    CONTINUATION   extend >= 0.75 with worst-adverse-first < 0.50
    CONSOLIDATION  total range < 1.00
    MANIPULATION   close back through the governing swept level, 2 bars running

Fixtures are deterministic in-memory pandas frames (no parquet, no file I/O, no
network), same shape as test_regime_compute.py. `FakeRead` is a local duck-typed
stand-in: regime.py (spec 001) is not built, and scorecard.py must be gradeable
and reviewable before it is.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import pytest

from scorecard import (CONFIDENCE_BUCKETS, GRADE_VERSION, ROW_FIELDS,
                       ScorecardError, TIME_PRIOR_ARGMAX, Ungradeable,
                       append_rows, grade, governing_swept_level, held_for,
                       max_adverse, report, reversed_through, signed_extension,
                       total_range)

DAY = "2026-08-07"
READ_TS = f"{DAY} 09:30"
FORWARD_START = f"{DAY} 09:35"
ATR = 1.00


@dataclass
class FakeRead:
    """Duck-typed RegimeRead (spec 001 field list). NOT an import of regime.py."""
    regime: str
    confidence: float = 0.7
    direction_of_flow: int = +1
    time_block: str = "OPEN_DRIVE"
    ts: str = READ_TS
    symbol: str = "NVDA"
    exit_policy: str = "RIDE"
    rule_version: str = "regime-v1"
    evidence: dict[str, Any] = field(default_factory=lambda: {"atr5": ATR})


def mk_bars(rows: list[tuple[float, float, float, float]], *,
            start: str = FORWARD_START, volume: float = 1000.0) -> pd.DataFrame:
    """rows are (Open, High, Low, Close), 5m apart, ascending."""
    idx = pd.date_range(start, periods=len(rows), freq="5min")
    return pd.DataFrame(
        {"Open": [r[0] for r in rows], "High": [r[1] for r in rows],
         "Low": [r[2] for r in rows], "Close": [r[3] for r in rows],
         "Volume": [volume] * len(rows)},
        index=idx)


# open0 = 100.00. b2 high 100.90 => favour 0.90 >= 0.75; worst adverse before it
# is b1's 100.00-99.80 = 0.20 < 0.50  => CONTINUATION HIT.
# full-window extension = 101.00 - 100.00 = 1.00 => forward_move_atr 1.00
# total range = 101.00 - 99.80 = 1.20, NOT < 1.00  => CONSOLIDATION miss
EXTENDS = mk_bars([(100.00, 100.40, 99.80, 100.30),
                   (100.30, 100.90, 100.20, 100.80),
                   (100.80, 101.00, 100.70, 100.95)])

# highest high 100.60 => favour 0.60 < 0.75  => CONTINUATION miss (short).
# total range = 100.60 - 99.90 = 0.70 < 1.00 => CONSOLIDATION hit
QUIET = mk_bars([(100.00, 100.30, 99.90, 100.20),
                 (100.20, 100.60, 100.10, 100.50),
                 (100.50, 100.55, 100.30, 100.40)])

# b1 low 99.40 => adverse 0.60 >= 0.50 BEFORE b2's favour 0.80 >= 0.75
# => CONTINUATION miss, for the adverse reason, not the extension reason.
SHAKEOUT = mk_bars([(100.00, 100.10, 99.40, 99.50),
                    (99.50, 100.80, 99.45, 100.70),
                    (100.70, 100.90, 100.60, 100.85)])

PDH = 100.50
# swept 100.50 upward, then closed 100.40 and 100.20 => 2 consecutive closes
# below the level => MANIPULATION hit.
RECLAIMED = mk_bars([(100.60, 100.80, 100.30, 100.40),
                     (100.40, 100.45, 100.10, 100.20),
                     (100.20, 100.35, 100.05, 100.30)])

# closed 100.40 below the level once (reversed_through True), then back above:
# run resets, never reaches 2 => MANIPULATION miss on the HOLD clause alone.
POKED = mk_bars([(100.60, 100.80, 100.30, 100.40),
                 (100.40, 100.70, 100.35, 100.60),
                 (100.60, 100.90, 100.55, 100.70)])

# every close sits between 100.50 and 101.20: through the EXTREME level only.
BETWEEN_POOLS = mk_bars([(101.30, 101.35, 100.85, 100.90),
                         (100.90, 101.00, 100.80, 100.95),
                         (100.95, 101.05, 100.75, 100.80)])


def manip_read(**kw: Any) -> FakeRead:
    ev = {"atr5": ATR, "swept": ["PDH"], "pools": {"PDH": PDH}}
    ev.update(kw.pop("evidence", {}))
    return FakeRead(regime="MANIPULATION", evidence=ev, **kw)


# --- helpers, in isolation ----------------------------------------------------

def test_helpers_arithmetic_is_exact():
    """EXTENDS: open0 100.00, max high 101.00, min low 99.80.
    signed_extension(+1) = 1.00; max_adverse(+1) = 0.20; total_range = 1.20.
    Short side on the same frame: extension = 100.00 - 99.80 = 0.20,
    adverse = 101.00 - 100.00 = 1.00."""
    assert signed_extension(EXTENDS, +1) == pytest.approx(1.00)
    assert max_adverse(EXTENDS, +1) == pytest.approx(0.20)
    assert total_range(EXTENDS) == pytest.approx(1.20)
    assert signed_extension(EXTENDS, -1) == pytest.approx(0.20)
    assert max_adverse(EXTENDS, -1) == pytest.approx(1.00)


def test_max_adverse_never_goes_negative_but_zero_is_measured():
    """A window that only traded up has adverse 0.0 — a measured zero, with the
    bars present to prove it, which is a different thing from a missing value."""
    up_only = mk_bars([(100.00, 100.50, 100.00, 100.40),
                       (100.40, 100.90, 100.35, 100.80),
                       (100.80, 101.20, 100.75, 101.10)])
    assert max_adverse(up_only, +1) == 0.0


def test_helpers_refuse_flat_direction():
    for fn in (signed_extension, max_adverse):
        with pytest.raises(ScorecardError, match="must be \\+1 or -1"):
            fn(EXTENDS, 0)


def test_reversed_through_and_held_for_are_about_closes_and_runs():
    """POKED closes 100.40 / 100.60 / 100.70 against level 100.50:
    one close below => reversed_through True, longest run below = 1 => not held."""
    assert reversed_through(PDH, POKED, +1) is True
    assert held_for(POKED, PDH, +1, bars=1) is True
    assert held_for(POKED, PDH, +1, bars=2) is False
    assert held_for(RECLAIMED, PDH, +1, bars=2) is True
    assert held_for(RECLAIMED, PDH, +1, bars=3) is True


# --- CONTINUATION -------------------------------------------------------------

def test_continuation_hit():
    row = grade(FakeRead(regime="CONTINUATION"), EXTENDS)
    assert row["hit"] is True
    assert row["gradeable"] is True
    assert row["reason"] is None
    assert row["forward_move_atr"] == pytest.approx(1.00)
    assert row["hit_consolidation"] is False          # range 1.20 is not < 1.00
    assert row["grade_version"] == GRADE_VERSION


def test_continuation_miss_because_extension_too_small():
    """QUIET's best favourable excursion is 0.60 ATR, short of 0.75."""
    row = grade(FakeRead(regime="CONTINUATION"), QUIET)
    assert row["hit"] is False
    assert row["forward_move_atr"] == pytest.approx(0.60)
    assert row["hit_consolidation"] is True           # range 0.70 < 1.00


def test_continuation_miss_because_it_retraced_first():
    """SHAKEOUT does reach 0.90 ATR of extension — the full-window number looks
    like a win — but it first gave up 0.60 ATR, over the 0.50 limit. The
    ordering clause is the whole test, so this must be a miss."""
    row = grade(FakeRead(regime="CONTINUATION"), SHAKEOUT)
    assert row["forward_move_atr"] == pytest.approx(0.90)
    assert row["hit"] is False


def test_continuation_with_no_flow_direction_is_ungradeable_not_a_miss():
    row = grade(FakeRead(regime="CONTINUATION", direction_of_flow=0), EXTENDS)
    assert row["hit"] is None
    assert row["gradeable"] is False
    assert row["reason"] == Ungradeable.NO_FLOW_DIRECTION
    assert row["forward_move_atr"] is None            # not 0.0
    assert row["hit_consolidation"] is False          # still computable


# --- MANIPULATION -------------------------------------------------------------

def test_manipulation_hit():
    row = grade(manip_read(), RECLAIMED)
    assert row["hit"] is True
    assert row["hit_continuation"] is False           # favour 0.20 < 0.75
    assert row["hit_consolidation"] is True           # 100.80-100.05 = 0.75


def test_manipulation_miss_when_the_reclaim_did_not_hold():
    row = grade(manip_read(), POKED)
    assert row["hit"] is False
    assert row["gradeable"] is True


def test_manipulation_governing_level_is_the_extreme_pool():
    """swept PDH 100.50 and PML 101.20 with +1 flow. Judged against 100.50 the
    window never closes back through (all closes >= 100.80) and would score a
    MISS; judged against the extreme 101.20 every close is back through and two
    run consecutively => HIT. The extreme is the ruling, so this is a hit."""
    ev = {"swept": ["PDH", "PML"], "pools": {"PDH": PDH, "PML": 101.20}}
    level, why = governing_swept_level({"atr5": ATR, **ev}, +1)
    assert (level, why) == (101.20, None)
    assert governing_swept_level({"atr5": ATR, **ev}, -1) == (PDH, None)
    assert reversed_through(PDH, BETWEEN_POOLS, +1) is False
    assert grade(manip_read(evidence=ev), BETWEEN_POOLS)["hit"] is True


def test_manipulation_with_no_sweep_is_ungradeable_not_a_miss():
    """The classifier can emit MANIPULATION from wick dominance with swept=[].
    Scoring that as a miss is a fabricated label (spec defect 1)."""
    row = grade(manip_read(evidence={"swept": [], "pools": {}}), POKED)
    assert row["hit"] is None
    assert row["gradeable"] is False
    assert row["reason"] == Ungradeable.NO_SWEPT_LEVEL


def test_manipulation_with_unavailable_pool_level_is_ungradeable_not_zero():
    """pools['PDH'] is None because the pool is genuinely unavailable. Reading
    that as 0.0 would put every close 'back through' the level and hand the
    classifier a free hit."""
    row = grade(manip_read(evidence={"swept": ["PDH"], "pools": {"PDH": None}}),
                POKED)
    assert row["hit"] is None
    assert row["reason"] == Ungradeable.SWEPT_LEVEL_UNAVAILABLE

    missing_name = grade(manip_read(evidence={"swept": ["PDH"], "pools": {}}), POKED)
    assert missing_name["reason"] == Ungradeable.SWEPT_LEVEL_UNAVAILABLE


def test_counterfactual_manipulation_is_unavailable_not_a_raise():
    """A CONTINUATION read carries no sweep bookkeeping, and it is not malformed
    for that. Its MANIPULATION counterfactual (needed only by report()'s
    baselines) is None with a reason; the call that WAS made still grades."""
    row = grade(FakeRead(regime="CONTINUATION"), EXTENDS)
    assert row["hit"] is True
    assert row["hit_manipulation"] is None
    assert governing_swept_level({"atr5": ATR}, +1, strict=False) == (
        None, Ungradeable.SWEPT_LEVEL_UNAVAILABLE)


def test_manipulation_without_the_swept_key_at_all_raises():
    read = FakeRead(regime="MANIPULATION", evidence={"atr5": ATR, "pools": {}})
    with pytest.raises(ScorecardError, match="swept"):
        grade(read, POKED)


# --- CONSOLIDATION ------------------------------------------------------------

def test_consolidation_hit_and_miss():
    """QUIET range = 100.60 - 99.90 = 0.70 < 1.00 ATR => hit.
    EXTENDS range = 101.00 - 99.80 = 1.20, not < 1.00 => miss."""
    hit = grade(FakeRead(regime="CONSOLIDATION", direction_of_flow=0), QUIET)
    assert hit["hit"] is True
    miss = grade(FakeRead(regime="CONSOLIDATION", direction_of_flow=0), EXTENDS)
    assert miss["hit"] is False


# --- the boundary invariant ---------------------------------------------------

def test_lookahead_is_refused_at_the_boundary():
    """A forward window whose first bar IS the classified block must raise. This
    is the named test for the no-lookahead invariant: delete the check in
    scorecard._assert_no_lookahead and this test fails."""
    overlapping = mk_bars([(100.00, 100.40, 99.80, 100.30),
                           (100.30, 100.90, 100.20, 100.80),
                           (100.80, 101.00, 100.70, 100.95)],
                          start=READ_TS)
    with pytest.raises(ScorecardError, match="LOOKAHEAD"):
        grade(FakeRead(regime="CONTINUATION"), overlapping)

    # one bar earlier than the read is just as forbidden
    before = mk_bars([(100.00, 100.40, 99.80, 100.30),
                      (100.30, 100.90, 100.20, 100.80),
                      (100.80, 101.00, 100.70, 100.95)],
                     start=f"{DAY} 09:20")
    with pytest.raises(ScorecardError, match="LOOKAHEAD"):
        grade(FakeRead(regime="CONTINUATION"), before)

    # strictly after is fine, and records the window it used
    row = grade(FakeRead(regime="CONTINUATION"), EXTENDS)
    assert row["forward_first_ts"] == "2026-08-07 09:35:00"
    assert row["forward_bars"] == 3


# --- atr5: a raise, never a zero ---------------------------------------------

@pytest.mark.parametrize("bad", [{}, {"atr5": None}, {"atr5": 0.0},
                                 {"atr5": -1.0}, {"atr5": float("nan")},
                                 {"atr5": "wide"}])
def test_missing_or_degenerate_atr_raises_instead_of_scoring_zero(bad):
    """atr5 is the unit every threshold is written in. With atr=0 the
    CONTINUATION test would pass automatically and CONSOLIDATION's would fail
    automatically — the grader would be inventing labels."""
    with pytest.raises(ScorecardError, match="atr5"):
        grade(FakeRead(regime="CONTINUATION", evidence=dict(bad)), EXTENDS)


# --- malformed bars are a raise, never an assumption -------------------------

def test_missing_column_raises():
    broken = EXTENDS.drop(columns=["Low"])
    with pytest.raises(ScorecardError, match="missing column"):
        grade(FakeRead(regime="CONTINUATION"), broken)


def test_nan_in_close_raises():
    broken = EXTENDS.copy()
    broken.iloc[1, broken.columns.get_loc("Close")] = float("nan")
    with pytest.raises(ScorecardError, match="non-finite"):
        grade(FakeRead(regime="CONTINUATION"), broken)


def test_duplicate_and_unsorted_timestamps_raise():
    dupes = pd.concat([EXTENDS.iloc[[0]], EXTENDS])
    with pytest.raises(ScorecardError, match="duplicate"):
        grade(FakeRead(regime="CONTINUATION"), dupes)

    unsorted = EXTENDS.iloc[[2, 0, 1]]
    with pytest.raises(ScorecardError, match="not sorted"):
        grade(FakeRead(regime="CONTINUATION"), unsorted)


def test_empty_and_short_windows_are_ungradeable():
    empty = EXTENDS.iloc[0:0]
    row = grade(FakeRead(regime="CONTINUATION"), empty)
    assert (row["hit"], row["reason"]) == (None, Ungradeable.EMPTY_FORWARD_WINDOW)
    assert row["forward_bars"] == 0

    short = grade(FakeRead(regime="CONTINUATION"), EXTENDS.iloc[:2])
    assert (short["hit"], short["reason"]) == (None, Ungradeable.SHORT_FORWARD_WINDOW)


def test_unknown_regime_raises():
    with pytest.raises(ScorecardError, match="unknown regime"):
        grade(FakeRead(regime="TRENDING"), EXTENDS)


# --- report(): the arithmetic, pinned ----------------------------------------

def mk_row(regime: str, conf: float, block: str, hit: bool | None, *,
           cont: bool | None, manip: bool | None, consol: bool | None,
           ts: str = READ_TS) -> dict[str, Any]:
    return {"ts": ts, "symbol": "NVDA", "regime": regime, "confidence": conf,
            "time_block": block, "direction_of_flow": +1, "exit_policy": "RIDE",
            "rule_version": "regime-v1", "grade_version": GRADE_VERSION,
            "gradeable": hit is not None, "reason": None, "hit": hit,
            "hit_continuation": cont, "hit_manipulation": manip,
            "hit_consolidation": consol, "forward_move_atr": 0.5, "atr5": ATR,
            "forward_bars": 3, "forward_first_ts": FORWARD_START,
            "forward_last_ts": f"{DAY} 09:45", "evidence": {"atr5": ATR}}


# hand fixture: 4 gradeable rows + 1 ungradeable
FIXTURE = [
    mk_row("CONTINUATION", 0.8, "OPEN_DRIVE", True, cont=True, manip=False, consol=False),
    mk_row("CONTINUATION", 0.6, "OPEN_DRIVE", False, cont=False, manip=False, consol=True),
    mk_row("CONSOLIDATION", 0.4, "MIDDAY", True, cont=False, manip=False, consol=True),
    mk_row("MANIPULATION", 0.2, "PREOPEN", False, cont=False, manip=False, consol=True),
    mk_row("CONTINUATION", 0.5, "MORNING", None, cont=None, manip=None, consol=True),
]


def test_report_pins_accuracy_baselines_lift_and_brier():
    """n = 4 gradeable (the 5th has hit None).
    overall_accuracy = 2/4 = 0.50
    baseline_always_consolidation = hit_consolidation over the SAME 4 rows
        (False, True, True, True) = 3/4 = 0.75
    baseline_time_prior_only: OPEN_DRIVE argmax = CONTINUATION (1.3),
        MIDDAY argmax = CONSOLIDATION (1.3), PREOPEN has no published prior so
        that row is EXCLUDED and counted. Scored: hit_continuation True,
        hit_continuation False, hit_consolidation True = 2/3 = 0.6667, n = 3.
    lift = 0.50 - max(0.75, 0.6667) = -0.25, versus always_consolidation.
    brier = ((0.8-1)^2 + (0.6-0)^2 + (0.4-1)^2 + (0.2-0)^2)/4
          = (0.04 + 0.36 + 0.36 + 0.04)/4 = 0.20
    base rate 0.50 => brier_base = 4 * 0.25 / 4 = 0.25
    brier_skill_score = 1 - 0.20/0.25 = 0.20
    """
    rep = report(FIXTURE)
    assert rep["n"] == 4
    assert rep["rows_in"] == 5
    assert rep["ungradeable"] == 1
    assert rep["overall_accuracy"] == pytest.approx(0.50)
    assert rep["overall_accuracy_is_decoration"] is True

    assert rep["baseline_always_consolidation"] == {"n": 4, "accuracy": pytest.approx(0.75)}
    prior = rep["baseline_time_prior_only"]
    assert prior["n"] == 3
    assert prior["accuracy"] == pytest.approx(2 / 3)
    assert prior["excluded_unknown_block"] == 1
    assert prior["excluded_prediction_ungradeable"] == 0
    assert TIME_PRIOR_ARGMAX["PREOPEN"] is None
    assert TIME_PRIOR_ARGMAX["MIDDAY"] == "CONSOLIDATION"

    lift = rep["lift_over_baseline"]
    assert lift["value"] == pytest.approx(-0.25)
    assert lift["versus"] == "always_consolidation"
    assert lift["baseline"] == pytest.approx(0.75)

    assert rep["brier"] == pytest.approx(0.20)
    assert rep["brier_baseline"] == pytest.approx(0.25)
    assert rep["brier_skill_score"] == pytest.approx(0.20)


def test_report_per_regime_and_per_block_carry_n_and_their_baseline():
    """CONTINUATION arm: 2 rows, 1 hit => 0.50. Its own always-consolidation
    score on those same 2 rows is (False, True) = 0.50, so lift = 0.00 — the
    classifier added nothing on that arm, which is exactly the thing overall
    accuracy would have hidden. mean_confidence = (0.8+0.6)/2 = 0.70."""
    rep = report(FIXTURE)
    cont = rep["by_regime"]["CONTINUATION"]
    assert cont["n"] == 2
    assert cont["accuracy"] == pytest.approx(0.50)
    assert cont["baseline_always_consolidation"]["accuracy"] == pytest.approx(0.50)
    assert cont["lift_over_always_consolidation"] == pytest.approx(0.00)
    assert cont["mean_confidence"] == pytest.approx(0.70)

    assert rep["by_regime"]["CONSOLIDATION"]["n"] == 1
    assert rep["by_regime"]["CONSOLIDATION"]["accuracy"] == pytest.approx(1.0)
    manip = rep["by_regime"]["MANIPULATION"]
    assert manip["n"] == 1 and manip["accuracy"] == pytest.approx(0.0)
    # PREOPEN has no prior, so this arm's prior baseline is n=0 and None, never 0.0
    assert manip["baseline_time_prior_only"] == {"n": 0, "accuracy": None,
                                                "reason": "NO_GRADEABLE_ROWS"}

    preopen = rep["by_time_block"]["PREOPEN"]
    assert preopen["n"] == 1
    assert preopen["time_prior_prediction"] is None
    assert preopen["baseline_time_prior_only"]["accuracy"] is None
    assert rep["by_time_block"]["OPEN_DRIVE"]["n"] == 2

    # every rate in the report ships with its n
    for section in ("by_regime", "by_time_block", "by_confidence_bucket"):
        for key, stats in rep[section].items():
            assert "n" in stats, f"{section}.{key} has no n"


def test_report_confidence_buckets_are_calibration_not_accuracy():
    """0.8 lands in the closed top bucket and was right => gap 0.8-1.0 = -0.2
    (underconfident). 0.6 lands in [0.6,0.8) and was wrong => gap +0.6
    (overconfident). The empty bottom bucket reports None, not 0.0."""
    buckets = report(FIXTURE)["by_confidence_bucket"]
    assert buckets["0.8-1.0"]["n"] == 1
    assert buckets["0.8-1.0"]["calibration_gap"] == pytest.approx(-0.2)
    assert buckets["0.6-0.8"]["calibration_gap"] == pytest.approx(0.6)
    assert buckets["0.4-0.6"]["calibration_gap"] == pytest.approx(-0.6)
    assert buckets["0.2-0.4"]["calibration_gap"] == pytest.approx(0.2)
    assert buckets["0.0-0.2"] == {"n": 0, "accuracy": None,
                                 "reason": "NO_GRADEABLE_ROWS"}
    assert len(buckets) == len(CONFIDENCE_BUCKETS)


def test_report_confidence_of_1_0_lands_in_the_top_bucket():
    rows = [mk_row("CONSOLIDATION", 1.0, "MIDDAY", True, cont=False, manip=False,
                   consol=True)]
    assert report(rows)["by_confidence_bucket"]["0.8-1.0"]["n"] == 1


def test_report_with_no_gradeable_rows_returns_none_not_zero():
    """A 0.0 accuracy on zero samples is indistinguishable from a 0.0 on a
    hundred misses. The empty case says None and names the reason."""
    for rows in ([], [FIXTURE[4]]):
        rep = report(rows)
        assert rep["n"] == 0
        assert rep["overall_accuracy"] is None
        assert rep["brier"] is None
        assert rep["brier_skill_score"] is None
        assert rep["lift_over_baseline"] is None
        assert rep["baseline_always_consolidation"]["accuracy"] is None
        assert rep["baseline_time_prior_only"]["accuracy"] is None
        assert rep["reason"] == "NO_GRADEABLE_ROWS"


def test_report_brier_skill_is_undefined_when_every_row_hit():
    """All hits => base rate 1.0 => brier_base 0 => 0/0. Said out loud, not 1.0."""
    rows = [mk_row("CONSOLIDATION", 0.9, "MIDDAY", True, cont=False, manip=False,
                   consol=True)]
    rep = report(rows)
    assert rep["brier"] == pytest.approx(0.01)
    assert rep["brier_baseline"] == 0.0
    assert rep["brier_skill_score"] is None
    assert rep["brier_skill_score_reason"] == "BASE_RATE_BRIER_IS_ZERO"


def test_report_since_filters_and_is_echoed_back():
    late = mk_row("CONSOLIDATION", 0.5, "MIDDAY", True, cont=False, manip=False,
                  consol=True, ts=f"{DAY} 13:00")
    rep = report(FIXTURE + [late], since=f"{DAY} 12:00")
    assert rep["n"] == 1
    assert rep["since"] == f"{DAY} 12:00"
    assert rep["overall_accuracy"] == pytest.approx(1.0)


def test_report_refuses_to_average_two_grade_versions():
    mixed = dict(FIXTURE[0])
    mixed["grade_version"] = "grade-v2"
    with pytest.raises(ScorecardError, match="mix grade versions"):
        report([FIXTURE[0], mixed])


def test_report_refuses_a_confidence_outside_zero_one():
    bad = dict(FIXTURE[0])
    bad["confidence"] = 1.4
    with pytest.raises(ScorecardError, match="outside"):
        report([bad])


# --- append_rows(): the only I/O, append-only --------------------------------

def test_append_rows_creates_header_once_and_only_appends(tmp_path):
    path = tmp_path / "regime_scorecard.csv"
    first = grade(FakeRead(regime="CONTINUATION"), EXTENDS)
    second = grade(manip_read(), RECLAIMED)

    assert append_rows([first], path=path) == 1
    assert append_rows([second], path=path) == 1

    with path.open() as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == list(ROW_FIELDS)
    assert len(rows) == 3                            # header + 2, nothing truncated
    body = dict(zip(ROW_FIELDS, rows[1]))
    assert body["hit"] == "True"
    assert body["regime"] == "CONTINUATION"
    assert body["grade_version"] == GRADE_VERSION
    assert '"atr5": 1.0' in body["evidence"]


def test_append_rows_writes_an_empty_cell_for_an_ungradeable_hit(tmp_path):
    """Never a 0 and never a False on disk for 'we could not tell'."""
    path = tmp_path / "s.csv"
    ungradeable = grade(manip_read(evidence={"swept": [], "pools": {}}), POKED)
    append_rows([ungradeable], path=path)
    body = dict(zip(ROW_FIELDS, list(csv.reader(path.open()))[1]))
    assert body["hit"] == ""
    assert body["gradeable"] == "False"
    assert body["reason"] == Ungradeable.NO_SWEPT_LEVEL


def test_append_rows_refuses_a_foreign_header(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text("ts,hit\n2026-08-07 09:30,True\n")
    with pytest.raises(ScorecardError, match="header"):
        append_rows([grade(FakeRead(regime="CONTINUATION"), EXTENDS)], path=path)


def test_append_rows_refuses_a_second_grade_version(tmp_path):
    path = tmp_path / "s.csv"
    row = grade(FakeRead(regime="CONTINUATION"), EXTENDS)
    append_rows([row], path=path)
    bumped = dict(row)
    bumped["grade_version"] = "grade-v2"
    with pytest.raises(ScorecardError, match="refusing to append"):
        append_rows([bumped], path=path)
    # and the refusal left the file untouched
    assert len(list(csv.reader(path.open()))) == 2


def test_append_rows_refuses_mixed_versions_in_one_batch(tmp_path):
    row = grade(FakeRead(regime="CONTINUATION"), EXTENDS)
    bumped = dict(row)
    bumped["grade_version"] = "grade-v2"
    with pytest.raises(ScorecardError, match="mixed grade versions"):
        append_rows([row, bumped], path=tmp_path / "s.csv")


def test_append_rows_refuses_a_short_row(tmp_path):
    row = grade(FakeRead(regime="CONTINUATION"), EXTENDS)
    row.pop("evidence")
    with pytest.raises(ScorecardError, match="missing field"):
        append_rows([row], path=tmp_path / "s.csv")


def test_append_rows_on_an_empty_batch_writes_nothing(tmp_path):
    path = tmp_path / "s.csv"
    assert append_rows([], path=path) == 0
    assert not path.exists()


def test_grade_does_no_io(tmp_path, monkeypatch):
    """grade() must stay pure: any open() inside it is a build error, because a
    grader that touches a filesystem cannot be replayed over history."""
    import builtins

    def no_open(*a, **k):
        raise AssertionError("grade() opened a file")

    monkeypatch.setattr(builtins, "open", no_open)
    assert grade(FakeRead(regime="CONTINUATION"), EXTENDS)["hit"] is True
