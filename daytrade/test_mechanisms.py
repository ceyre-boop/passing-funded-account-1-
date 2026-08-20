#!/usr/bin/env python3
"""Spec 029 invariants (I28-I35). The ledger-integrity test is always-on,
same guard shape as 028's seal check."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mechanisms as mx


def test_i35_ledger_integrity_always_on():
    """THE guard: the real ledger satisfies every entry rule, every run."""
    assert mx.check(quiet=True) == 0, \
        "ledger violation — see `python3 daytrade/mechanisms.py check`"


class _Args:
    def __init__(self, **kw):
        self.claim = "x"; self.transfer = None; self.predicted_effect = None
        self.band = None; self.by = "tester"; self.metric = "R_per_trade"
        self.__dict__.update(kw)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "LEDGER", tmp_path / "MECHANISMS.json")
    (tmp_path / "MECHANISMS.json").write_text(json.dumps(
        {"spec": "029", "mechanisms": [], "calibration_seeds": []}))
    return tmp_path


# ------------------------------------------------------------------- I28/I29

def test_i28_no_transfer_prediction_refused(sandbox):
    with pytest.raises(mx.MechanismError, match="PARAMETER, not a mechanism"):
        mx.propose(_Args(predicted_effect=0.2))


def test_i28_bad_transfer_term_refused(sandbox):
    with pytest.raises(mx.MechanismError, match="bad transfer term"):
        mx.propose(_Args(transfer="CRYPTO:help", predicted_effect=0.2))
    with pytest.raises(mx.MechanismError, match="bad transfer term"):
        mx.propose(_Args(transfer="FUTURES:maybe", predicted_effect=0.2))


def test_i29_no_predicted_effect_refused(sandbox):
    with pytest.raises(mx.MechanismError, match="nothing to calibrate"):
        mx.propose(_Args(transfer="FUTURES:help"))


def test_help_everywhere_is_flagged_unfalsifiable(sandbox):
    mx.propose(_Args(transfer="FUTURES:help,SINGLE_NAME:help,CASH_INDEX:help",
                     predicted_effect=0.2))
    m = json.loads((sandbox / "MECHANISMS.json").read_text())["mechanisms"][0]
    assert any("unfalsifiable_shape" in f for f in m["flags"])


def test_a_hurt_prediction_is_not_flagged(sandbox):
    mx.propose(_Args(transfer="FUTURES:help,SINGLE_NAME:hurt", predicted_effect=0.2))
    m = json.loads((sandbox / "MECHANISMS.json").read_text())["mechanisms"][0]
    assert "flags" not in m


# ---------------------------------------------------------------------- I30

def _rows():
    """Two symbols per day, class perfectly confounded with day-half so an
    UNGROUPED permutation sees structure a day-blocked one correctly does not."""
    rows = []
    for d in range(20):
        day = f"2026-05-{d + 1:02d}"
        rows.append((day, "FUTURES", 1.0 if d < 10 else -1.0))
        rows.append((day, "SINGLE_NAME", 1.0 if d < 10 else -1.0))
    return rows


def test_i30_day_blocking_changes_the_null():
    """The ungrouped null is a different (wrong) test. When class membership
    carries no within-day information, day-blocking must NOT report
    significance — the ungrouped version can, which is the documented error
    from specs/026 that this design exists to avoid."""
    rows = _rows()
    pattern = {"FUTURES": "help", "SINGLE_NAME": "neutral", "CASH_INDEX": "neutral"}
    by_class = {"FUTURES": [d for _, c, d in rows if c == "FUTURES"],
                "SINGLE_NAME": [d for _, c, d in rows if c == "SINGLE_NAME"],
                "CASH_INDEX": []}
    obs = mx.contrast(by_class, pattern)
    p_blocked = mx.permutation_p(rows, pattern, obs, n_perm=300, day_blocked=True)
    # within every day the two deltas are identical, so no day-blocked
    # permutation can move the contrast: p must be maximal, never significant
    assert p_blocked > 0.9


def test_i30_permutation_is_deterministic():
    rows = _rows()
    pattern = {"FUTURES": "help", "SINGLE_NAME": "neutral", "CASH_INDEX": "neutral"}
    a = mx.permutation_p(rows, pattern, 0.1, n_perm=200)
    b = mx.permutation_p(rows, pattern, 0.1, n_perm=200)
    assert a == b


def test_contrast_uses_pattern_weights_only():
    by_class = {"FUTURES": [1.0], "SINGLE_NAME": [1.0], "CASH_INDEX": [1.0]}
    assert mx.contrast(by_class, {"FUTURES": "help", "SINGLE_NAME": "neutral",
                                  "CASH_INDEX": "hurt"}) == 0.0
    assert mx.contrast(by_class, {"FUTURES": "help", "SINGLE_NAME": "help",
                                  "CASH_INDEX": "neutral"}) == 2.0


# ---------------------------------------------------------------------- I33

def test_i33_shrinkage_pools_and_accumulates(sandbox):
    reg = {"spec": "029", "mechanisms": [], "calibration_seeds": [
        {"id": "C1", "predicted_by": "p", "predicted_effect_r": 0.2,
         "realized_effect_r": 0.0, "pre_registered": True}]}
    (sandbox / "MECHANISMS.json").write_text(json.dumps(reg))
    f1, n1 = mx.shrinkage(mx._load(), "p")
    assert n1 == 1 and 0.6 < f1 < 0.7            # one miss -> ~0.67, not 0
    for i in range(4):
        reg["calibration_seeds"].append(
            {"id": f"C{i+2}", "predicted_by": "p", "predicted_effect_r": 0.2,
             "realized_effect_r": 0.0, "pre_registered": True})
    (sandbox / "MECHANISMS.json").write_text(json.dumps(reg))
    f5, n5 = mx.shrinkage(mx._load(), "p")
    assert n5 == 5 and f5 < f1                    # repeated misses shrink harder


def test_i33_retrospective_and_unregistered_never_calibrate(sandbox):
    reg = {"spec": "029", "calibration_seeds": [
        {"id": "C1", "predicted_by": "p", "predicted_effect_r": 0.5,
         "realized_effect_r": 0.0, "pre_registered": False}],
        "mechanisms": [
        {"id": "M1", "predicted_by": "p", "predicted_effect_r": 0.5,
         "realized_effect_r": 0.0, "recorded_retrospectively": True,
         "transfer_prediction": {"FUTURES": "help"},
         "structural_vs_lever": {"structural": "x", "lever": "y"},
         "status": "killed"}]}
    (sandbox / "MECHANISMS.json").write_text(json.dumps(reg))
    assert mx.shrinkage(mx._load(), "p") == (1.0, 0)


def test_shrinkage_never_inflates_an_underpromiser(sandbox):
    reg = {"spec": "029", "mechanisms": [], "calibration_seeds": [
        {"id": "C1", "predicted_by": "p", "predicted_effect_r": 0.1,
         "realized_effect_r": 0.9, "pre_registered": True}]}
    (sandbox / "MECHANISMS.json").write_text(json.dumps(reg))
    f, _ = mx.shrinkage(mx._load(), "p")
    assert f <= 1.0


# ----------------------------------------------------------------- MDE / I34

def test_mde_falls_with_more_day_clusters():
    assert mx.mde(0.2, 90) < mx.mde(0.2, 39)
    with pytest.raises(mx.MechanismError):
        mx.mde(0.2, 0)


def test_i34_structural_and_lever_are_independent(sandbox):
    reg = {"spec": "029", "calibration_seeds": [], "mechanisms": [
        {"id": "M1", "transfer_prediction": {"FUTURES": "help"},
         "predicted_effect_r": 0.2, "status": "killed",
         "structural_vs_lever": {"structural": "TRUE", "lever": "REFUTED"}}]}
    (sandbox / "MECHANISMS.json").write_text(json.dumps(reg))
    assert mx.check(quiet=True) == 0           # true-and-refuted is legal
    reg["mechanisms"][0]["structural_vs_lever"] = "TRUE"
    (sandbox / "MECHANISMS.json").write_text(json.dumps(reg))
    assert mx.check(quiet=True) == 1           # collapsing the axes is not


def test_check_rejects_unknown_status(sandbox):
    reg = {"spec": "029", "calibration_seeds": [], "mechanisms": [
        {"id": "M1", "transfer_prediction": {"FUTURES": "help"},
         "predicted_effect_r": 0.2, "status": "probably_fine",
         "structural_vs_lever": {"structural": "x", "lever": "y"}}]}
    (sandbox / "MECHANISMS.json").write_text(json.dumps(reg))
    assert mx.check(quiet=True) == 1


def test_corrupt_ledger_raises(sandbox):
    (sandbox / "MECHANISMS.json").write_text("{not json")
    with pytest.raises(mx.MechanismError, match="not JSON"):
        mx._load()


# ------------------------------------------------- soak channel (MECH-006)

def test_soak_reports_empty_channel_when_books_never_diverge(monkeypatch, tmp_path):
    """Agreement between the veto book and its control is NOT weak evidence —
    it is NO evidence, and must be reported as EMPTY_CHANNEL rather than a
    0.0 edge that looks like a measurement."""
    import four_books as fb
    recs = tmp_path / "data" / "daytrade" / "operator"
    recs.mkdir(parents=True)
    (recs / "records.jsonl").write_text(json.dumps(
        {"symbol": "NVDA", "verdict": "TIGHTEN", "ts": "2026-08-17T17:23:00+00:00",
         "expires_at": "2026-08-17T18:08:00+00:00"}) + "\n")
    monkeypatch.setattr(mx, "ROOT", tmp_path)

    class _S:
        day = __import__("datetime").date(2026, 8, 17)
        df = None
    monkeypatch.setattr(mx, "_now", lambda: "t")
    import bars, ceiling
    monkeypatch.setattr(bars, "load_sessions", lambda *a, **k: [_S()])
    monkeypatch.setattr(ceiling, "find_entry", lambda s: None)   # no entry that day
    out = mx.run_soak_test({"predicted_effect": 0.1})
    assert out["verdict"] == "NO_SAMPLE"
    assert out["n_divergences"] == 0


def test_soak_refuses_with_no_records(monkeypatch, tmp_path):
    (tmp_path / "data" / "daytrade" / "operator").mkdir(parents=True)
    (tmp_path / "data" / "daytrade" / "operator" / "records.jsonl").write_text("")
    monkeypatch.setattr(mx, "ROOT", tmp_path)
    with pytest.raises(mx.MechanismError, match="soak has produced nothing"):
        mx.run_soak_test({"predicted_effect": 0.1})
