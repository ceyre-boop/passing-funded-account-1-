"""Spec 049 §4/§5/§2.1 as tests. Each invariant has a deliberate violation."""
import datetime as dt

import numpy as np
import pytest

from az.prereg import (ArmResult, PreregError, adjudicate, max_stat_null,
                       sub_period_verdict, sub_periods)


# ---- I81: the pair rule -----------------------------------------------------

def test_pair_holds_when_both_clear_with_agreeing_sign():
    v = adjudicate(ArmResult("SPY", 0.09, 0.05), ArmResult("QQQ", 0.07, 0.05))
    assert v.verdict == "HOLDS"


def test_one_clearing_one_failing_is_NULL_not_partial():
    """The load-bearing one. This must never render as 'held on SPY'."""
    v = adjudicate(ArmResult("SPY", 0.20, 0.05), ArmResult("QQQ", 0.01, 0.05))
    assert v.verdict == "NULL"
    assert "NOT a partial" in v.reason
    assert "held on SPY" not in v.header()


def test_disagreeing_signs_are_NULL_even_if_both_clear():
    v = adjudicate(ArmResult("SPY", 0.20, 0.05), ArmResult("QQQ", -0.20, 0.05))
    assert v.verdict == "NULL" and "signs disagree" in v.reason


def test_pair_rule_refuses_a_single_arm():
    with pytest.raises(PreregError, match=">=2 arms"):
        adjudicate(ArmResult("SPY", 0.2, 0.05))


# ---- sub-periods ------------------------------------------------------------

def _days(spec):
    out = {}
    for (y0, y1), val, n in spec:
        d = dt.date(y0, 1, 2)
        for i in range(n):
            out[d + dt.timedelta(days=i)] = val
    return out


def test_all_four_agree_and_clear():
    v = _days([((2016, 2017), .5, 400), ((2018, 2019), .5, 400),
               ((2020, 2021), .5, 400), ((2022, 2026), .5, 400)])
    res = sub_periods(v, sigma_day=1.0)
    assert all(r.n_days == 400 for r in res)
    assert sub_period_verdict(res)[0] == "HOLDS"


def test_one_period_flipping_sign_is_NULL():
    v = _days([((2016, 2017), .5, 400), ((2018, 2019), -.5, 400),
               ((2020, 2021), .5, 400), ((2022, 2026), .5, 400)])
    verdict, why = sub_period_verdict(sub_periods(v, 1.0))
    assert verdict == "NULL" and "sign does not agree" in why


def test_a_period_carrying_over_half_the_effect_is_NULL():
    """A result carried by P3 is a COVID artifact, and must say so."""
    # all four must CLEAR (mde(1.0, 400) = 0.124) so the magnitude rule does not
    # fire first and mask the check under test — then P3 dominates the total.
    v = _days([((2016, 2017), .2, 400), ((2018, 2019), .2, 400),
               ((2020, 2021), 5.0, 400), ((2022, 2026), .2, 400)])
    verdict, why = sub_period_verdict(sub_periods(v, 1.0))
    assert verdict == "NULL" and "half" in why and "P3" in why


def test_a_period_with_no_days_is_NULL_not_silently_skipped():
    v = _days([((2016, 2017), .5, 400), ((2018, 2019), .5, 400), ((2020, 2021), .5, 400)])
    verdict, why = sub_period_verdict(sub_periods(v, 1.0))
    assert verdict == "NULL" and "P4" in why


# ---- I82: the max-statistic null -------------------------------------------

def _matrix(n_days, per, n_cells, seed, signal_cell=None, lift=0.0):
    rng = np.random.default_rng(seed)
    cells = rng.integers(0, n_cells, size=n_days * per)
    days = np.repeat(np.arange(n_days), per)
    out = rng.normal(0, 1, size=n_days * per)
    if signal_cell is not None:
        out[cells == signal_cell] += lift
    return cells, out, days


def test_pure_noise_does_not_survive():
    """The point of the null: with no signal, the best of many correlated cells
    must NOT look significant."""
    c, o, d = _matrix(300, 40, 30, seed=1)
    r = max_stat_null(c, o, d, min_days=30)
    assert not r["survives"], f"noise survived at p={r['p_value']}"


def test_a_planted_signal_does_survive():
    c, o, d = _matrix(300, 40, 30, seed=2, signal_cell=7, lift=1.2)
    r = max_stat_null(c, o, d, min_days=30)
    assert r["survives"] and r["p_value"] < 0.05


def test_ragged_slots_are_refused_not_silently_permuted():
    """Masking must happen BEFORE permutation. Ragged days would misalign the
    day-shift and quietly corrupt the null."""
    c, o, d = _matrix(50, 10, 8, seed=3)
    with pytest.raises(PreregError, match="ragged"):
        max_stat_null(c[:-1], o[:-1], d[:-1], min_days=5)


def test_valued_cells_count_DAYS_not_rows():
    """40 rows/day means a cell can hit min_days=30 on ONE day if rows were
    counted. It must not."""
    c2, o2, d2 = _matrix(10, 40, 1, seed=5)
    r = max_stat_null(c2, o2, d2, min_days=30)
    # 10 days x 40 rows = 400 ROWS in one cell. A row-counter would call that
    # valued at a 30 floor. Counting days, it is not.
    assert r["n_cells_valued"] == 0, "10 days must not clear a 30-day floor"
    assert max_stat_null(c2, o2, d2, min_days=10)["n_cells_valued"] == 1
