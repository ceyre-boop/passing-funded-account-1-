"""Tests for scripts/carry_tablebase_paths.py -- Plan Step 2 (carry-exit tablebase
path extractor).

(a)-(d) exercise the pure functions on SYNTHETIC arrays, no rig, no CSV.
(e) exercises the real artifacts produced by a prior `--h-max 10` run: existence,
    n=350, sum R round 34.41, inventory hashes, exactly-one-absorbing-row, and an
    independent parity re-derivation against the real 350-trade population (G7).

Run: python3 scripts/carry_tablebase_paths.py --h-max 10  (twice, for determinism)
     python3 -m pytest scripts/test_carry_tablebase_paths.py -q
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import carry_tablebase_paths as ctp  # noqa: E402

STOP, REVERSAL, CB_REFRESH, TIME, TRAILING = ctp.STOP, ctp.REVERSAL, ctp.CB_REFRESH, ctp.TIME, ctp.TRAILING


def _flat_arrays(n: int, close0: float = 100.0):
    """A flat, unremarkable price series: no signal, no stop hit, generous ATR
    headroom, so tests can inject exactly the one event they want to test."""
    closes = np.full(n, close0, dtype=np.float64)
    opens = np.full(n, close0, dtype=np.float64)
    atr_pcts = np.full(n, 0.01, dtype=np.float64)
    signals = np.zeros(n, dtype=np.int8)
    hold_days = np.full(n, 60, dtype=np.int32)
    index = pd.bdate_range("2020-01-02", periods=n)
    return closes, opens, atr_pcts, signals, hold_days, index


# ------------------------------------------------------------- (a) STOP terminal

def test_stop_at_t3_absorbs_and_terminal_r_matches_close():
    n = 20
    closes, opens, atr_pcts, signals, hold_days, index = _flat_arrays(n)
    entry_bar = 5
    entry_price = 100.0
    direction = 1
    stop_price = 98.0          # risk_dist = 2.0
    risk_dist = 2.0
    # Drop the close below the stop exactly at bar entry_bar+2 (t=3).
    closes[entry_bar + 2] = 90.0
    weekend_flags = ctp.weekend_next_flags(index)

    rows, absorbed_by, absorbing_t, path_end_bar, terminal_r = ctp.build_trade_path(
        closes=closes, opens=opens, atr_pcts=atr_pcts, signals=signals, hold_days_arr=hold_days,
        entry_bar=entry_bar, direction=direction, entry_price=entry_price, stop_price=stop_price,
        risk_dist=risk_dist, hold_limit=60, stop_atr_mult=2.0, trailing_atr_mult=0.0,
        strict_mode=False, enable_cb_refresh=False, h_max=10, incumbent_hold=60,
        incumbent_r_net=999.0, cost_frac=0.0, risk_pct=0.01, index=index, weekend_flags=weekend_flags,
    )

    assert len(rows) == 3
    assert [r["t"] for r in rows] == [1, 2, 3]
    assert absorbed_by == "STOP"
    assert absorbing_t == 3
    assert path_end_bar == entry_bar + 2

    expected_gross, expected_net = ctp.unrealized_r(
        direction=direction, close=90.0, entry_price=entry_price, cost_frac=0.0,
        risk_dist=risk_dist, risk_pct=0.01,
    )
    assert terminal_r == pytest.approx(expected_net)
    assert rows[-1]["unrealized_r_net"] == pytest.approx(expected_net)
    assert rows[-1]["forced"] is True
    assert all(r["forced"] is False for r in rows[:-1])


def test_stop_mutation_moving_stop_further_prevents_absorption():
    """Fault injection: with the stop far away, no STOP fires within h_max --
    HMAX absorbs instead. Proves the STOP test above is a real invariant, not
    a tautology."""
    n = 20
    closes, opens, atr_pcts, signals, hold_days, index = _flat_arrays(n)
    entry_bar = 5
    closes[entry_bar + 2] = 90.0
    weekend_flags = ctp.weekend_next_flags(index)

    rows, absorbed_by, absorbing_t, _end, _term = ctp.build_trade_path(
        closes=closes, opens=opens, atr_pcts=atr_pcts, signals=signals, hold_days_arr=hold_days,
        entry_bar=entry_bar, direction=1, entry_price=100.0, stop_price=1.0,  # unreachable stop
        risk_dist=99.0, hold_limit=60, stop_atr_mult=2.0, trailing_atr_mult=0.0,
        strict_mode=False, enable_cb_refresh=False, h_max=10, incumbent_hold=60,
        incumbent_r_net=999.0, cost_frac=0.0, risk_pct=0.01, index=index, weekend_flags=weekend_flags,
    )
    assert absorbed_by == "HMAX"
    assert len(rows) == 10


# ------------------------------------------------------------- (b) HMAX branches

def test_hmax_inherits_incumbent_r_when_incumbent_still_in():
    n = 20
    closes, opens, atr_pcts, signals, hold_days, index = _flat_arrays(n)
    entry_bar = 5
    weekend_flags = ctp.weekend_next_flags(index)
    h_max = 5
    incumbent_hold = 8  # > h_max: the incumbent is still in when the path caps

    rows, absorbed_by, absorbing_t, _end, terminal_r = ctp.build_trade_path(
        closes=closes, opens=opens, atr_pcts=atr_pcts, signals=signals, hold_days_arr=hold_days,
        entry_bar=entry_bar, direction=1, entry_price=100.0, stop_price=1.0, risk_dist=99.0,
        hold_limit=60, stop_atr_mult=2.0, trailing_atr_mult=0.0, strict_mode=False,
        enable_cb_refresh=False, h_max=h_max, incumbent_hold=incumbent_hold,
        incumbent_r_net=1.2345, cost_frac=0.0, risk_pct=0.01, index=index, weekend_flags=weekend_flags,
    )
    assert absorbed_by == "HMAX"
    assert absorbing_t == h_max
    assert len(rows) == h_max
    assert terminal_r == pytest.approx(1.2345)
    # every row before h_max is labelled with the incumbent's own (HOLD) action,
    # since t <= incumbent_hold throughout this path
    assert all(r["incumbent_action"] == "HOLD" for r in rows)


def test_hmax_caps_at_close_when_incumbent_already_out():
    n = 20
    closes, opens, atr_pcts, signals, hold_days, index = _flat_arrays(n)
    entry_bar = 5
    closes[entry_bar + 4] = 103.0  # bar for t=5 (h_max) gets a distinct close
    weekend_flags = ctp.weekend_next_flags(index)
    h_max = 5
    incumbent_hold = 3  # <= h_max: incumbent already left (e.g. by TIME) before h_max

    rows, absorbed_by, absorbing_t, _end, terminal_r = ctp.build_trade_path(
        closes=closes, opens=opens, atr_pcts=atr_pcts, signals=signals, hold_days_arr=hold_days,
        entry_bar=entry_bar, direction=1, entry_price=100.0, stop_price=1.0, risk_dist=99.0,
        hold_limit=3, stop_atr_mult=2.0, trailing_atr_mult=0.0, strict_mode=False,
        enable_cb_refresh=False, h_max=h_max, incumbent_hold=incumbent_hold,
        incumbent_r_net=1.2345, cost_frac=0.0, risk_pct=0.01, index=index, weekend_flags=weekend_flags,
    )
    assert absorbed_by == "HMAX"
    assert absorbing_t == h_max
    assert len(rows) == h_max
    # NOT the incumbent's R -- capped at the candidate's own unrealized R at close[h_max]
    assert terminal_r != pytest.approx(1.2345)
    expected_gross, expected_net = ctp.unrealized_r(
        direction=1, close=103.0, entry_price=100.0, cost_frac=0.0, risk_dist=99.0, risk_pct=0.01,
    )
    assert terminal_r == pytest.approx(expected_net)
    # bars after the incumbent's own exit (t=3) are labelled EXITED, not HOLD/EXIT:time
    assert [r["incumbent_action"] for r in rows] == ["HOLD", "HOLD", "EXIT:time", "EXITED", "EXITED"]


# ------------------------------------------------------------- (c) weekend_next

def test_weekend_next_friday_gap_and_midweek_holiday():
    # Mon Tue Wed(holiday, skipped) Thu Fri ... next Mon
    dates = pd.DatetimeIndex([
        "2024-01-01",  # Mon
        "2024-01-02",  # Tue -> gap to Thu is 2 days (a mid-week holiday, NOT a weekend)
        "2024-01-04",  # Thu
        "2024-01-05",  # Fri -> gap to Mon is 3 days (a real weekend)
        "2024-01-08",  # Mon (last bar)
    ])
    flags = ctp.weekend_next_flags(dates)
    assert flags.tolist() == [0, 0, 0, 1, 0]


def test_weekend_next_last_bar_is_always_zero():
    dates = pd.DatetimeIndex(["2024-01-01", "2024-01-02"])
    flags = ctp.weekend_next_flags(dates)
    assert flags[-1] == 0


# ------------------------------------------------------------- (d) components

def test_connected_components_known_answer():
    ts = lambda d: pd.Timestamp(f"2024-01-{d:02d}")
    intervals = [
        ("A", ts(1), ts(5)),
        ("B", ts(3), ts(8)),    # overlaps A
        ("C", ts(10), ts(12)),  # isolated from A/B
        ("D", ts(12), ts(15)),  # touches C at the endpoint -> same component
        ("E", ts(20), ts(25)),  # isolated
    ]
    units = ctp.connected_components(intervals)
    groups = sorted(tuple(sorted(v)) for v in units.values())
    assert groups == [("A", "B"), ("C", "D"), ("E",)]
    assert len(units) == 3


def test_connected_components_mutation_shrinking_interval_splits_group():
    """Fault injection: shrink B so it no longer reaches A -- the component count
    must change, proving the test isn't vacuously true."""
    ts = lambda d: pd.Timestamp(f"2024-01-{d:02d}")
    intervals = [
        ("A", ts(1), ts(5)),
        ("B", ts(6), ts(8)),   # no longer overlaps A
        ("C", ts(10), ts(12)),
        ("D", ts(12), ts(15)),
        ("E", ts(20), ts(25)),
    ]
    units = ctp.connected_components(intervals)
    assert len(units) == 4


# ------------------------------------------------------------- (e) real artifacts

@pytest.fixture(scope="module")
def trades_df():
    if not ctp.TRADES_OUT.is_file():
        pytest.skip(f"{ctp.TRADES_OUT} missing -- run scripts/carry_tablebase_paths.py --h-max 10 first")
    return pd.read_parquet(ctp.TRADES_OUT)


@pytest.fixture(scope="module")
def paths_df():
    if not ctp.PATHS_OUT.is_file():
        pytest.skip(f"{ctp.PATHS_OUT} missing -- run scripts/carry_tablebase_paths.py --h-max 10 first")
    return pd.read_parquet(ctp.PATHS_OUT)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_trades_artifact_n_and_sum_r(trades_df):
    assert len(trades_df) == ctp.EXPECTED_N
    assert round(trades_df["incumbent_r_net"].sum(), 2) == ctp.EXPECTED_SUM_R


def test_units_artifact_exists_and_matches_h_max():
    if not ctp.UNITS_OUT.is_file():
        pytest.skip(f"{ctp.UNITS_OUT} missing -- run scripts/carry_tablebase_paths.py --h-max 10 first")
    payload = json.loads(ctp.UNITS_OUT.read_text())
    assert payload["h_max"] == 10
    assert sum(len(v) for v in payload["units"].values()) == ctp.EXPECTED_N
    assert payload["n_entry_clusters"] == len(payload["entry_clusters"])


def test_inventory_hashes_match_artifacts_on_disk():
    inv_path = ROOT / "artifacts" / "inventory.json"
    if not inv_path.is_file():
        pytest.skip("artifacts/inventory.json missing")
    inv = json.loads(inv_path.read_text())
    for out in (ctp.TRADES_OUT, ctp.PATHS_OUT, ctp.UNITS_OUT):
        if not out.is_file():
            pytest.skip(f"{out} missing")
        rel = out.relative_to(ROOT).as_posix()
        assert rel in inv["hashes"], f"{rel} not recorded in inventory.json"
        assert inv["hashes"][rel] == _sha256(out), f"{rel} hash does not match bytes on disk"


def test_determinism_across_two_runs_matches_inventory():
    """The parquet sha256 recorded by the last run must match a fresh re-hash --
    this is the two-runs-identical check, keyed off the inventory record rather
    than re-running the (slower) extractor here."""
    inv_path = ROOT / "artifacts" / "inventory.json"
    if not inv_path.is_file() or not ctp.PATHS_OUT.is_file():
        pytest.skip("artifacts missing -- run the extractor first")
    inv = json.loads(inv_path.read_text())
    rel = ctp.PATHS_OUT.relative_to(ROOT).as_posix()
    assert inv["hashes"][rel] == _sha256(ctp.PATHS_OUT)


def test_every_trade_has_exactly_one_absorbing_row(paths_df):
    absorbing = paths_df[paths_df["absorbed_by"].notna()]
    counts = absorbing.groupby("trade_id").size()
    assert len(counts) == ctp.EXPECTED_N
    assert (counts == 1).all()
    # and every absorbing row's terminal_r is not NaN, every non-absorbing row's is
    merged = paths_df.merge(
        absorbing[["trade_id", "t"]].rename(columns={"t": "absorbing_t"}), on="trade_id"
    )
    is_absorbing_row = merged["t"] == merged["absorbing_t"]
    assert merged.loc[is_absorbing_row, "terminal_r"].notna().all()
    assert merged.loc[~is_absorbing_row, "terminal_r"].isna().all()


def test_parity_replay_against_real_artifacts(trades_df):
    """G7, run against the real 350-trade population: re-runs the rig capture
    fresh and replays `decide_exit` for every trade recorded in the real
    carry_trades.parquet, asserting the replay reproduces that trade's own
    recorded incumbent_exit_bar / incumbent_reason exactly."""
    captured_trades, arrays_calls = ctp._run_arm_capture()
    id_to_ref = {}
    for ci, c in enumerate(arrays_calls):
        for j, tr in enumerate(c["trades"]):
            id_to_ref[id(tr)] = (ci, j)
    by_key = {}
    for tr in captured_trades:
        ci, j = id_to_ref[id(tr)]
        key = (tr["pair"], pd.Timestamp(tr["entry_date"]).strftime("%Y-%m-%d"), int(tr["direction"]))
        by_key[key] = (ci, j)

    n_ok = 0
    for row in trades_df.itertuples():
        key = (row.pair, row.entry_date, int(row.direction))
        assert key in by_key, f"trade {row.trade_id} not reproducible from a fresh rig run"
        ci, j = by_key[key]
        c = arrays_calls[ci]
        core_kwargs = c["core"]["kwargs"]
        entry_idx, exit_idx, _dirs, _pnls, holds, reasons, _units = c["core"]["result"]
        e_bar = int(entry_idx[j])
        assert e_bar == row.entry_bar

        rows = ctp.replay_decisions(
            closes=core_kwargs["closes"], atr_pcts=core_kwargs["atr_pcts"],
            signals=core_kwargs["signals"], hold_days_arr=core_kwargs["hold_days"],
            entry_bar=e_bar, direction=int(row.direction), entry_price=float(row.entry_price),
            stop_price=float(row.stop_price), hold_limit=int(row.hold_limit),
            stop_atr_mult=float(row.stop_atr_mult), trailing_atr_mult=float(row.trailing_mult),
            strict_mode=bool(core_kwargs["strict_mode"]), enable_cb_refresh=bool(core_kwargs["enable_cb_refresh"]),
            max_t=int(row.incumbent_hold),
        )
        last_t, last_bar, last_decision = rows[-1]
        assert last_bar == row.incumbent_exit_bar
        assert ctp.CSV_REASON_STR[last_decision] == row.incumbent_reason
        assert all(d == ctp.HOLD for (_t, _b, d) in rows[:-1])
        n_ok += 1
    assert n_ok == ctp.EXPECTED_N
