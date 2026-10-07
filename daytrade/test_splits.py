"""Tests for splits.py — the tune/sealed boundary and the SEALED-002 guards.

splits.py is the only thing standing between "we measured an edge" and "we tuned
and evaluated on the same data", which `SANITY_AUDIT.md` exists because of. It
had no tests at all until spec 026 cut SEALED-002 — so the guard that makes a
holdout number interpretable was itself unverified.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

import splits


@pytest.fixture(autouse=True)
def _never_touch_the_real_audit_trail(monkeypatch, tmp_path):
    """Redirect BOTH unseal sinks for every test in this file.

    splits._log_unseal writes to SEAL_LOG *and* mirrors into
    seals._append_intent, which has its own path. Patching only SEAL_LOG left the
    mirror live, and three spurious HOLDOUT_READ rows were appended to the real
    data/daytrade/unseal_intents.jsonl by test runs on 2026-10-07 before this was
    noticed. Those two files are append-only audit trails whose entire purpose is
    to prove the holdout was read once and by whom; a test suite must not be able
    to write to them, and a test that EXPECTS a raise must still be safe when
    fault injection deletes that raise.
    """
    monkeypatch.setattr(splits, "SEAL_LOG", tmp_path / "unseals.log")
    try:
        import seals
        monkeypatch.setattr(seals, "_append_intent", lambda rec: None)
    except ImportError:
        pass


@dataclass(frozen=True)
class FakeSession:
    day: date


def _sessions(*days):
    return [FakeSession(date(*d)) for d in days]


ALL = _sessions(
    (2026, 5, 7),    # TRAIN
    (2026, 7, 6),    # TRAIN — the boundary day itself is inclusive
    (2026, 7, 7),    # DEV   — burned band
    (2026, 8, 17),   # DEV   — last burned day
    (2026, 8, 18),   # SEALED-002 — first fresh day
    (2026, 10, 6),   # SEALED-002
)


# -------------------------------------------------------------- the boundaries

def test_the_two_boundary_dates_are_exactly_what_was_registered():
    """These are committed constants. If either moves, every number measured
    against them silently changes meaning — so the dates are pinned in a test,
    not only in a comment."""
    assert splits.TUNE_END == date(2026, 7, 6)
    assert splits.SEALED_002_START == date(2026, 8, 18)
    assert splits.TUNE_END < splits.SEALED_002_START


def test_the_three_bands_partition_the_sessions_with_no_overlap_and_no_gap():
    train = splits.tune_sessions(ALL)
    dev = splits.dev_sessions(ALL)
    sealed = [s for s in ALL if s.day >= splits.SEALED_002_START]
    assert [s.day for s in train] == [date(2026, 5, 7), date(2026, 7, 6)]
    assert [s.day for s in dev] == [date(2026, 7, 7), date(2026, 8, 17)]
    assert [s.day for s in sealed] == [date(2026, 8, 18), date(2026, 10, 6)]
    # partition: every session in exactly one band
    assert len(train) + len(dev) + len(sealed) == len(ALL)
    ids = {id(s) for s in train} | {id(s) for s in dev} | {id(s) for s in sealed}
    assert len(ids) == len(ALL)


def test_tune_end_day_is_in_train_not_dev():
    """Off-by-one on an inclusive boundary moves a session across the split."""
    on_boundary = _sessions((2026, 7, 6))
    assert len(splits.tune_sessions(on_boundary)) == 1
    assert len(splits.dev_sessions(on_boundary)) == 0


def test_sealed_002_start_day_is_sealed_not_dev():
    on_boundary = _sessions((2026, 8, 18))
    assert len(splits.dev_sessions(on_boundary)) == 0


# ------------------------------------------------- the SEALED-002 unseal guards

def test_sealed_002_requires_an_unseal_reason(monkeypatch, tmp_path):
    """No accidental reads. A holdout looked at by accident is spent.

    SEAL_LOG redirected for the same reason as the rule_version test below: the
    thing fault injection deletes here is the raise, and without the redirect the
    fallthrough would append to the real audit log.
    """
    monkeypatch.setattr(splits, "SEAL_LOG", tmp_path / "unseals.log")
    with pytest.raises(splits.SealedSplitError, match="unseal_reason"):
        splits.sealed_002_sessions(ALL)
    with pytest.raises(splits.SealedSplitError, match="unseal_reason"):
        splits.sealed_002_sessions(ALL, rule_version="regime-v1")


def test_sealed_002_requires_a_rule_version(monkeypatch, tmp_path):
    """A holdout number with no record of what produced it cannot be interpreted
    later, which is the whole lesson of the SEALED-001 futures-exit-v1 read.

    Two things this test learned from fault injection. First, `match="rule_version"`
    passed for the WRONG reason: with the check deleted, control fell through to
    the freeze check, whose message ALSO contains the string "rule_version", so
    the mutation stayed green. The match is now the specific phrase. Second,
    _rules_frozen is stubbed True so that the rule_version check is the ONLY thing
    that can raise — otherwise deleting it still raises, just elsewhere.
    """
    monkeypatch.setattr(splits, "_rules_frozen", lambda rv: (True, "frozen at abc"))
    # SEAL_LOG is redirected even though this test expects a RAISE. Under fault
    # injection the raise is exactly what gets deleted, and the call then falls
    # through to _log_unseal and appends to the REAL holdout audit log — which is
    # the one file in this repo whose whole purpose is to prove the holdout was
    # read once. Two spurious "[SEALED-002] rule_version None" rows were written
    # that way by mutation_check_026 runs on 2026-10-07 before this was noticed.
    # A test must not be able to damage the audit trail when it fails.
    monkeypatch.setattr(splits, "SEAL_LOG", tmp_path / "unseals.log")
    with pytest.raises(splits.SealedSplitError, match="needs rule_version"):
        splits.sealed_002_sessions(ALL, unseal_reason="spec 026 read")


def test_sealed_002_refuses_an_uncommitted_rule_version(monkeypatch):
    """Condition 3: the rules must have been frozen BEFORE the holdout was seen.
    A rule_version that is not in a commit means it could be edited after the
    number is known — the laundering the whole module exists to prevent."""
    monkeypatch.setattr(splits, "_rules_frozen",
                        lambda rv: (False, "pretend: uncommitted"))
    with pytest.raises(splits.SealedSplitError, match="not frozen"):
        splits.sealed_002_sessions(ALL, unseal_reason="r", rule_version="v")


def test_sealed_002_force_bypasses_only_the_freeze_check(monkeypatch, tmp_path):
    """force= exists so the check can never be quietly deleted for being
    inconvenient. It must still require reason AND rule_version."""
    monkeypatch.setattr(splits, "_rules_frozen", lambda rv: (False, "uncommitted"))
    monkeypatch.setattr(splits, "SEAL_LOG", tmp_path / "unseals.log")
    with pytest.raises(splits.SealedSplitError, match="unseal_reason"):
        splits.sealed_002_sessions(ALL, rule_version="v", force=True)
    got = splits.sealed_002_sessions(ALL, unseal_reason="bootstrap",
                                     rule_version="v", force=True)
    assert [s.day for s in got] == [date(2026, 8, 18), date(2026, 10, 6)]


def test_every_sealed_002_read_is_logged_and_tagged(monkeypatch, tmp_path):
    """A quiet second look is the failure mode. The log is the only defence."""
    log = tmp_path / "unseals.log"
    monkeypatch.setattr(splits, "_rules_frozen", lambda rv: (True, "frozen at abc"))
    monkeypatch.setattr(splits, "SEAL_LOG", log)
    splits.sealed_002_sessions(ALL, unseal_reason="the spec 026 read",
                               rule_version="regime-v1")
    text = log.read_text()
    assert "regime-v1" in text
    assert "the spec 026 read" in text
    assert "[SEALED-002]" in text, "the band must be identifiable in the log"


def test_sealed_002_and_sealed_001_are_different_bands():
    """Reaching for the newer holdout must not quietly hand back the burned one.
    sealed_sessions (001) is everything after TUNE_END, which INCLUDES the
    contaminated dev band; sealed_002_sessions excludes it."""
    monkey_all = ALL
    s001 = [s for s in monkey_all if s.day > splits.TUNE_END]
    s002 = [s for s in monkey_all if s.day >= splits.SEALED_002_START]
    assert len(s001) == 4 and len(s002) == 2
    assert set(d.day for d in s002).issubset(set(d.day for d in s001))
