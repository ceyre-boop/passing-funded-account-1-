import numpy as np
import pytest

from sovereign.forex.permutation import PermutationError, sign_flip_test


def test_all_zero_deltas_is_no_evidence():
    r = sign_flip_test([0.0] * 50, draws=2000, seed=1)
    assert r.p_one_sided == 1.0 and r.n_nonzero == 0 and r.observed_mean == 0.0


def test_strong_positive_signal_is_significant_and_reproducible():
    rng = np.random.default_rng(0)
    d = rng.normal(0.5, 0.5, size=80)
    a = sign_flip_test(d, draws=5000, seed=20260829)
    b = sign_flip_test(d, draws=5000, seed=20260829)
    assert a == b
    assert a.p_one_sided < 0.01


def test_negative_signal_is_not_significant():
    rng = np.random.default_rng(1)
    d = rng.normal(-0.5, 0.5, size=80)
    assert sign_flip_test(d, draws=2000, seed=3).p_one_sided > 0.5


def test_p_value_is_never_zero():
    d = np.full(30, 2.0)
    r = sign_flip_test(d, draws=1000, seed=2)
    assert 0.0 < r.p_one_sided <= 2.0 / 1001.0


@pytest.mark.parametrize("bad", [dict(deltas=[], draws=1000, seed=1), dict(deltas=[0.1, float("nan")], draws=1000, seed=1),
                                 dict(deltas=[0.1, 0.2], draws=10, seed=1), dict(deltas=[0.1, 0.2], draws=1000, seed=1.5)])
def test_guards(bad):
    with pytest.raises(PermutationError):
        sign_flip_test(**bad)
