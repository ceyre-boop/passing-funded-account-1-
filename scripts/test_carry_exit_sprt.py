"""spec 045 driver invariants: I63 (self-exclusion by raise), I64 (halt on unhashed input),
and the declared-numbers guard (the data may not drift under the spec)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import carry_exit_sprt as drv  # noqa: E402
from daytrade.measure import MeasurementError  # noqa: E402


def _result(blocks):
    return SimpleNamespace(blocks=[SimpleNamespace(k=k, train_trade_ids=tr, test_trade_ids=te) for k, tr, te in blocks])


def test_i63_disjoint_blocks_pass():
    assert drv._assert_self_excluded(_result([(2, [1, 2, 3], [4, 5]), (3, [1, 2, 3, 4, 5], [6, 7])])) == 4


def test_i63_train_test_overlap_raises():
    with pytest.raises(MeasurementError, match="both train and test"):
        drv._assert_self_excluded(_result([(2, [1, 2, 3], [3, 4])]))


def test_i63_trade_evaluated_twice_raises():
    with pytest.raises(MeasurementError, match="already evaluated"):
        drv._assert_self_excluded(_result([(2, [1], [4, 5]), (3, [1, 2], [5, 6])]))


def test_i63_empty_oof_raises():
    with pytest.raises(MeasurementError, match="empty"):
        drv._assert_self_excluded(_result([(2, [1, 2], [])]))


def test_declared_numbers_match_frozen_artifacts():
    """The spec's four incumbent-derived cells must be reproducible from the frozen artifacts."""
    import json
    trades = pd.read_parquet(ROOT / drv.TRADES)
    units = json.load((ROOT / drv.UNITS).open())
    uf = drv._units_frame(trades, units)
    blocks = [pd.Timestamp(x) for x in ("2015-01-01", "2017-01-01", "2018-10-01", "2021-04-01", "2023-03-01")]
    dec = drv._check_declared(uf, blocks)
    assert dec["n_units"] == 121 and dec["n_units_oof"] == 97
    assert round(dec["sigma"], 4) == 0.7580 and round(dec["delta"], 4) == 0.1914


def test_declared_numbers_drift_halts():
    import json
    trades = pd.read_parquet(ROOT / drv.TRADES)
    units = json.load((ROOT / drv.UNITS).open())
    uf = drv._units_frame(trades, units)
    uf.loc[0, "incumbent_r"] += 5.0   # one unit's incumbent R moves → sigma moves → halt
    blocks = [pd.Timestamp(x) for x in ("2015-01-01", "2017-01-01", "2018-10-01", "2021-04-01", "2023-03-01")]
    with pytest.raises(drv.DriverError, match="no longer yields"):
        drv._check_declared(uf, blocks)


def test_i64_driver_halts_on_unhashed_dependency(monkeypatch):
    from sovereign.forex import inventory as inv
    monkeypatch.setattr(inv, "load", lambda: {"hashes": {}, "notes": {}})
    with pytest.raises(inv.InventoryError, match="UNHASHED"):
        drv.main(["--h-max", "10", "--n-min", "20", "--n-bins", "5", "--seed", "1", "--alpha", "0.05", "--beta", "0.2",
                  "--spread-mult", "2", "--slip-mult", "2", "--delay-bars", "1", "--draws", "1000", "--perm-seed", "1",
                  "--blocks", "2015-01-01,2017-01-01,2018-10-01,2021-04-01,2023-03-01", "--t-buckets", "1-1,2-3,4-5,6-8,9-10"])


def test_every_parameter_is_required():
    with pytest.raises(SystemExit):
        drv._args(["--h-max", "10"])
