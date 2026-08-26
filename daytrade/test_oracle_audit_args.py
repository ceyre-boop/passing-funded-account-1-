"""Tests for the oracle audit's population arguments and its pre-registered gates.

Two jobs. The first is ordinary: --cache/--out must not silently do the wrong
thing. The second is cultural — test_gates_are_unchanged exists so that editing
a pre-registered gate FAILS THE SUITE rather than passing quietly. The audit
returned NOTHING_QUOTABLE twice; the temptation to move NULL_GATE is exactly
what specs/008 and ceiling._verdict() refuse, and a comment does not refuse it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from daytrade import oracle_audit as oa
from daytrade.splits import TUNE_END

# NOTE: use oa.BarDataError, not daytrade.bars.BarDataError. oracle_audit does
# `sys.path.insert(daytrade/)` then `import bars`, so the top-level module `bars`
# and the package module `daytrade.bars` are DISTINCT objects with DISTINCT
# exception classes. Catching the package one silently fails to catch what this
# module actually raises. Pinned by test_bar_data_error_identity below.
BarDataError = oa.BarDataError

EXTENDED = Path(__file__).resolve().parents[1] / "data" / "daytrade" / "bars_extended"


# ------------------------------------------------------------- the gates

def test_gates_are_unchanged():
    """Pre-registered 2026-08-17, before the audit first ran. Not adjustable
    after seeing a failure — that is laundering, not tuning."""
    assert oa.NULL_GATE == 0.15
    assert oa.RNG_SEED == 26
    assert list(oa.FAMILIES) == ["STATIC", "EARLY_BANK", "NOON_FLAT",
                                 "TIGHT_TRAIL", "EXIT_TP2", "RIDE"]
    assert oa.FEATURES == ["cls_SINGLE_NAME", "cls_CASH_INDEX", "cls_FUTURES",
                           "direction", "or_risk_pct", "gap_pct",
                           "pre_tr_med_r", "entry_minute", "dow"]


def test_null_gate_is_read_not_bypassed():
    """The verdict must key off the gate, not around it."""
    src = Path(oa.__file__).read_text()
    assert "null_ok = null_leak < NULL_GATE" in src
    assert '"NOTHING_QUOTABLE" if not null_ok' in src


# ------------------------------------------------------- cache redirection

def test_empty_cache_raises_rather_than_falling_back(tmp_path):
    """A silent fallback would label a run with one population's name and
    another population's data — the worst available outcome."""
    with pytest.raises(BarDataError, match="Refusing to fall back"):
        oa._collect(["NVDA"], tmp_path)


def test_cache_is_restored_after_a_successful_collect():
    before = oa.bars_mod.CACHE
    oa._collect(["NVDA"], EXTENDED)
    assert oa.bars_mod.CACHE == before, "module global left faulted"


def test_cache_is_restored_even_when_collect_raises(tmp_path, monkeypatch):
    before = oa.bars_mod.CACHE
    (tmp_path / "NVDA_5m.parquet").touch()      # satisfies the glob guard only

    def boom(*a, **k):
        raise RuntimeError("simulated failure mid-collect")

    monkeypatch.setattr(oa, "load_sessions", boom)
    with pytest.raises(RuntimeError):
        oa._collect(["NVDA"], tmp_path)
    assert oa.bars_mod.CACHE == before, "faulted cache survived an exception"


# ------------------------------------------------------------ the split

def test_collect_never_reaches_past_tune_end():
    """The 36 sealed sessions after TUNE_END must not appear in a tune run."""
    entries = oa._collect(["NVDA"], EXTENDED)
    assert entries, "extended cache produced no entries"
    assert all(e.day <= str(TUNE_END) for _, _, e in entries)


def test_single_symbol_population_is_one_entry_per_day():
    """The prior run was 8.62 entries/day across 16 correlated symbols, so its
    day-blocked null had ~39 independent days, not 336. One symbol removes the
    cross-sectional double-counting mechanisms.K_EFF exists to price."""
    entries = oa._collect(["NVDA"], EXTENDED)
    days = {e.day for _, _, e in entries}
    assert len(days) == len(entries)


def test_bar_data_error_identity():
    """`import bars` and `from daytrade import bars` are different modules, so
    their BarDataError classes are different. Pre-existing repo-wide pattern;
    pinned here because a test that catches the wrong one passes vacuously."""
    from daytrade.bars import BarDataError as PkgErr
    assert oa.BarDataError is not PkgErr
    assert oa.BarDataError.__module__ == "bars"


# ------------------------------------------- the structural finding, pinned

def test_null_leak_does_not_shrink_with_sample_size():
    """null_leak = E[max of K permuted columns] - best_fixed.

    That is a property of the MARGINAL distribution, not an estimation error:
    more rows measure the constant more precisely, they never drive it to zero.
    This was mispredicted as a 1/sqrt(n) quantity when the extended re-run was
    planned. Pinning it so the mistake is not repeated.
    """
    rng = np.random.default_rng(oa.RNG_SEED)
    R = rng.standard_normal((4000, len(oa.FAMILIES))) * 1.1

    def leak(S):
        bf = S.mean(axis=0).max()
        P = np.column_stack([rng.permutation(S[:, j]) for j in range(S.shape[1])])
        return P.max(axis=1).mean() - bf

    small = np.mean([leak(R[rng.choice(len(R), 40, replace=False)])
                     for _ in range(60)])
    large = np.mean([leak(R[rng.choice(len(R), 1200, replace=False)])
                     for _ in range(60)])

    assert large > 0.5 * small, (
        "null_leak collapsed with n — if this ever passes, the statistic is "
        "not the max-over-columns one the gate was written for")
    assert large > oa.NULL_GATE, (
        "a 6-column max on sigma~1.1 returns should sit far above a 0.15 gate")
