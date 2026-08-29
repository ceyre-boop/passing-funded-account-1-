"""sovereign/forex/test_exit_tablebase.py — invariants for the exit tablebase and its
anchored walk-forward (spec `specs/045_CARRY_EXIT_TABLEBASE.md` §9). Each test names an
invariant and, where the spec asks for it, a mutation that makes the invariant fail."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from sovereign.forex import exit_tablebase as tb
from sovereign.forex import inventory as inv
from sovereign.forex.fill_model import BaseFill

ROOT = inv.ROOT

# --------------------------------------------------------------------------- #
# Synthetic-data builders
# --------------------------------------------------------------------------- #

_PATH_COLS = ["trade_id", "t", "close", "open_next", "weekend_next",
              "unrealized_r_net", "incumbent_action", "absorbed_by", "terminal_r"]
_TRADE_COLS = ["trade_id", "unit_id", "pair", "direction", "entry_price", "entry_date",
               "risk_pct", "incumbent_r_net"]


def _paths_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_PATH_COLS)
    df = pd.DataFrame(rows)
    for c in _PATH_COLS:
        if c not in df.columns:
            df[c] = np.nan
    return df[_PATH_COLS]


def _trades_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_TRADE_COLS)
    df = pd.DataFrame(rows)
    for c in _TRADE_COLS:
        if c not in df.columns:
            raise AssertionError(f"fixture missing required trades column {c!r}")
    return df[_TRADE_COLS]


def _row(trade_id, t, r_net, action, *, absorbed_by=None, terminal_r=None,
         close=1.0, open_next=1.0, weekend_next=0) -> dict:
    return dict(trade_id=trade_id, t=t, close=close, open_next=open_next, weekend_next=weekend_next,
                unrealized_r_net=r_net, incumbent_action=action, absorbed_by=absorbed_by, terminal_r=terminal_r)


# --------------------------------------------------------------------------- #
# I71 — terminal rows are absorbing
# --------------------------------------------------------------------------- #

def test_terminal_rows_absorbing() -> None:
    """3 trades, each: t=1 modest profit, t=2 smaller profit, STOP absorbs at t=3
    (terminal_r=-2.0, common to both arms). Holding through t=2 is valued at the
    stop R via the terminal chain, not a naive pre-stop mean of the positive bars
    — so the policy exits at t=1 even though every unrealized R was positive."""
    disc = tb.Discretization(r_edges=(0.0,), t_buckets=((1, 1), (2, 2)))
    paths_rows = []
    trades_rows = []
    for i in range(3):
        tid = i
        paths_rows += [
            _row(tid, 1, 0.3, "HOLD"),
            _row(tid, 2, 0.1, "HOLD"),
            _row(tid, 3, -2.0, "EXIT:stop", absorbed_by="STOP", terminal_r=-2.0),
        ]
        trades_rows.append(dict(trade_id=tid, unit_id=tid, pair="EURUSD=X", direction=1,
                                 entry_price=1.1, entry_date="2020-01-01", risk_pct=0.01,
                                 incumbent_r_net=-2.0))
    paths = _paths_df(paths_rows)
    trades = _trades_df(trades_rows)

    decision_rows = tb._build_decision_rows(paths, trades, disc)
    last_decision_per_trade = {r.trade_id: r for r in decision_rows if r.t == 2}
    for tid, row in last_decision_per_trade.items():
        assert row.next_kind == "terminal"
        assert row.next_terminal_r == pytest.approx(-2.0)

    table = tb.build_table(paths, trades, disc=disc, n_min=1, seed=1)
    cell_t1 = table.cells[(1, 1, 0)]  # (r_bin=1, t=1, weekend_next=0) — Amendment 1: t is exact
    cell_t2 = table.cells[(1, 2, 0)]  # (r_bin=1, t=2, weekend_next=0)
    assert cell_t2.action == "EXIT"
    assert cell_t2.q_hold == pytest.approx(-2.0)   # the chain, not a pre-stop mean
    assert cell_t1.action == "EXIT"

    # Mutation: mark the terminal row absorbing but strip its terminal_r -> must raise.
    bad_paths = paths.copy()
    bad_paths.loc[(bad_paths["t"] == 3), "terminal_r"] = np.nan
    with pytest.raises(tb.TablebaseError, match="NaN terminal_r"):
        tb._build_decision_rows(bad_paths, trades, disc)


# --------------------------------------------------------------------------- #
# I67 — an under-visited cell follows the incumbent per row, never 0, never max
# --------------------------------------------------------------------------- #

def test_undervisited_cell_follows_incumbent() -> None:
    disc = tb.Discretization(r_edges=(0.0,), t_buckets=((1, 1),))
    paths = _paths_df([
        _row(0, 1, 0.3, "HOLD", absorbed_by=None, terminal_r=None),
        _row(0, 2, 0.9, "EXIT:time", absorbed_by="HMAX", terminal_r=0.9),
        _row(1, 1, 0.4, "EXITED", absorbed_by=None, terminal_r=None),
        _row(1, 2, 0.2, "EXITED", absorbed_by="HMAX", terminal_r=0.2),
    ])
    trades = _trades_df([
        dict(trade_id=0, unit_id=0, pair="EURUSD=X", direction=1, entry_price=1.0,
             entry_date="2020-01-01", risk_pct=0.01, incumbent_r_net=0.9),
        dict(trade_id=1, unit_id=1, pair="EURUSD=X", direction=1, entry_price=1.0,
             entry_date="2020-01-05", risk_pct=0.01, incumbent_r_net=0.2),
    ])
    table = tb.build_table(paths, trades, disc=disc, n_min=5, seed=1)
    cell = table.cells[(1, 1, 0)]  # (r_bin=1, t=1, weekend_next=0) — Amendment 1: t is exact
    assert cell.n == 2
    assert cell.action == "FALLBACK"
    # row 0 (t=1): incumbent_action "HOLD" -> incumbent_r_net (0.9); row 1 (t=1): "EXITED" -> exit_value (0.4)
    expected = (0.9 + 0.4) / 2.0
    assert cell.value == pytest.approx(expected)
    assert cell.value != 0.0
    assert cell.value != max(0.9, 0.4)


# --------------------------------------------------------------------------- #
# I65 — bin edges derive from training rows only
# --------------------------------------------------------------------------- #

def test_bins_from_train_rows_only() -> None:
    paths = _paths_df([
        _row(0, 1, 0.10, "HOLD"), _row(0, 2, -2.0, "EXIT:stop", absorbed_by="STOP", terminal_r=-2.0),
        _row(1, 1, 0.20, "HOLD"), _row(1, 2, -2.0, "EXIT:stop", absorbed_by="STOP", terminal_r=-2.0),
        _row(2, 1, 0.30, "HOLD"), _row(2, 2, -2.0, "EXIT:stop", absorbed_by="STOP", terminal_r=-2.0),
    ])
    train_ids, test_ids = [0, 1], [2]
    t_buckets = ((1, 1), (2, 2))

    train_rows = paths[paths["trade_id"].isin(train_ids)]
    edges_before = tb.fit_bins(train_rows, n_bins=2, t_buckets=t_buckets).r_edges

    # Perturb a TEST-partition row +100: since it is never passed to fit_bins, edges are unchanged.
    perturbed = paths.copy()
    perturbed.loc[(perturbed["trade_id"].isin(test_ids)) & (perturbed["t"] == 1), "unrealized_r_net"] += 100.0
    train_rows_after_test_perturb = perturbed[perturbed["trade_id"].isin(train_ids)]
    edges_after_test_perturb = tb.fit_bins(train_rows_after_test_perturb, n_bins=2, t_buckets=t_buckets).r_edges
    assert edges_after_test_perturb == edges_before

    # Perturb a TRAIN-partition row +100: edges change.
    perturbed2 = paths.copy()
    perturbed2.loc[(perturbed2["trade_id"] == train_ids[0]) & (perturbed2["t"] == 1), "unrealized_r_net"] += 100.0
    train_rows_after_train_perturb = perturbed2[perturbed2["trade_id"].isin(train_ids)]
    edges_after_train_perturb = tb.fit_bins(train_rows_after_train_perturb, n_bins=2, t_buckets=t_buckets).r_edges
    assert edges_after_train_perturb != edges_before


# --------------------------------------------------------------------------- #
# I66 — no training path window overlaps the test block
# --------------------------------------------------------------------------- #

def test_no_train_test_overlap() -> None:
    test_start, test_end = pd.Timestamp("2018-01-01"), pd.Timestamp("2018-12-31")
    clean = pd.DataFrame({
        "trade_id": [0], "entry_date": [pd.Timestamp("2015-01-01")], "path_end_date": [pd.Timestamp("2015-01-10")],
    })
    tb._assert_no_train_test_overlap(clean, test_start=test_start, test_end=test_end)  # no raise

    overlapping = pd.DataFrame({
        "trade_id": [0, 1],
        "entry_date": [pd.Timestamp("2015-01-01"), pd.Timestamp("2017-06-01")],
        "path_end_date": [pd.Timestamp("2015-01-10"), pd.Timestamp("2018-06-01")],  # trade 1: un-purged, overlaps
    })
    with pytest.raises(tb.TablebaseError, match="I66"):
        tb._assert_no_train_test_overlap(overlapping, test_start=test_start, test_end=test_end)


# --------------------------------------------------------------------------- #
# Identity check — an all-FALLBACK table reproduces the incumbent (I68 composed)
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def real_paths_trades():
    inv.require_hashes(tb.REQUIRED_ARTIFACTS)
    paths = pd.read_parquet(ROOT / "artifacts" / "carry_paths.parquet")
    trades = pd.read_parquet(ROOT / "artifacts" / "carry_trades.parquet")
    return paths, trades


def test_all_fallback_table_reproduces_incumbent(real_paths_trades) -> None:
    paths, trades = real_paths_trades
    t_buckets = ((1, 1), (2, 3), (4, 5), (6, 8), (9, 10))
    disc = tb.fit_bins(paths, n_bins=5, t_buckets=t_buckets)
    # n_min far larger than any cell's population forces every cell to FALLBACK.
    table = tb.build_table(paths, trades, disc=disc, n_min=10_000_000, seed=20260829)
    assert all(c.action == "FALLBACK" for c in table.cells.values())

    applied = tb.apply_policy(table, paths, trades, fill=BaseFill())
    assert len(applied) == 350
    max_err = (applied["candidate_r"] - applied["incumbent_r_net"]).abs().max()
    assert max_err < 1e-9, f"all-FALLBACK candidate diverged from incumbent by {max_err}"


# --------------------------------------------------------------------------- #
# Cross-fit cannot exceed the in-sample optimistic estimate
# --------------------------------------------------------------------------- #

def test_cross_fit_value_not_above_in_sample_max() -> None:
    seed = 42
    unit_ids = np.array([0, 1, 2, 3, 4, 5])
    rng = np.random.default_rng(seed)
    shuffled = unit_ids.copy()
    rng.shuffle(shuffled)
    half_a = set(int(x) for x in shuffled[:3])  # {3, 2, 5} for this seed
    half_b = set(int(x) for x in shuffled[3:])  # {4, 1, 0}

    # Half A units: exit strongly beats hold. Half B units: hold strongly beats exit.
    # Overall (pooled) exit still edges out hold, but the cross-fit average of
    # "half A's choice valued on B" and "half B's choice valued on A" swings hard
    # negative — well below the in-sample max(Q_exit, Q_hold).
    paths_rows, trades_rows = [], []
    for uid in unit_ids:
        exit_r, hold_terminal = (2.0, -3.0) if uid in half_a else (-0.5, 3.0)
        paths_rows.append(_row(int(uid), 1, exit_r, "HOLD"))
        paths_rows.append(_row(int(uid), 2, hold_terminal, "EXIT:time", absorbed_by="HMAX", terminal_r=hold_terminal))
        trades_rows.append(dict(trade_id=int(uid), unit_id=int(uid), pair="EURUSD=X", direction=1, entry_price=1.0,
                                 entry_date="2020-01-01", risk_pct=0.01, incumbent_r_net=hold_terminal))
    paths = _paths_df(paths_rows)
    trades = _trades_df(trades_rows)
    # Edge well below every exit_r (2.0 and -0.5) so both profiles land in the same cell.
    disc = tb.Discretization(r_edges=(-10.0,), t_buckets=((1, 2),))  # t_buckets: reporting only (Amendment 1)

    table = tb.build_table(paths, trades, disc=disc, n_min=1, seed=seed)
    cell = table.cells[(1, 1, 0)]  # (r_bin=1, t=1, weekend_next=0) — Amendment 1: t is exact
    assert cell.n == 6
    in_sample_max = max(cell.q_exit, cell.q_hold)
    assert cell.value <= in_sample_max + 1e-9
    assert cell.value < in_sample_max - 1.0, "expected a case where cross-fit is well below the in-sample optimum"


# --------------------------------------------------------------------------- #
# Amendment 1 — a single backward pass over exact t is a fixed point, real artifacts
# --------------------------------------------------------------------------- #

def test_single_backward_pass_is_a_fixed_point(real_paths_trades) -> None:
    """Declared parameters (h_max=10, n_min=20, n_bins=5, seed=20260829) against the
    real block-2 training set (train=B1 only, the smallest/first walk-forward split).

    Before Amendment 1, the bucketed state key `(r_bin, t_bucket, weekend_next)` let a
    cell reference itself (an extreme-R path can stay in the same wide t_bucket for
    consecutive bars), which produced a genuine, non-damping period-2 Gauss-Seidel
    oscillation — verified by hand on this exact block, traced to 40 sweeps without
    settling. With `t` exact, `build_table`'s internal confirmation-pass assertion
    (a second pass must change nothing by > 1e-12) is the real test here: if it were
    still possible to construct a self-referencing cell, `build_table` itself would
    raise `TablebaseError` before this test's `build_table(...)` call returns."""
    paths, trades = real_paths_trades
    t_buckets = ((1, 1), (2, 3), (4, 5), (6, 8), (9, 10))
    boundary = pd.Timestamp("2017-01-01")
    train_ids = trades.loc[pd.to_datetime(trades["entry_date"]) < boundary, "trade_id"]
    train_paths = paths[paths["trade_id"].isin(train_ids)]
    train_trades = trades[trades["trade_id"].isin(train_ids)]
    disc = tb.fit_bins(train_paths, n_bins=5, t_buckets=t_buckets)
    table = tb.build_table(train_paths, train_trades, disc=disc, n_min=20, seed=20260829)
    assert table.passes == 1
    # Exact-t cells can only ever reference a strictly higher t (never their own key) —
    # confirm no cell key repeats its own t via a self-loop.
    assert all(row.next_kind != "cell" or row.next_cell != row.cell
               for row in tb._build_decision_rows(train_paths, train_trades, disc))


# --------------------------------------------------------------------------- #
# I64 — the driver halts on an unhashed or moved dependency
# --------------------------------------------------------------------------- #

def test_hash_guardrail(monkeypatch) -> None:
    monkeypatch.setattr(inv, "load", lambda: {"generated": None, "head": None, "hashes": {}, "notes": {}})
    empty_paths = _paths_df([])
    empty_trades = _trades_df([])
    with pytest.raises(inv.InventoryError):
        tb.walk_forward(
            empty_paths, empty_trades, {},
            blocks=("2015-01-01", "2017-01-01"), h_max=10, n_min=20, n_bins=5,
            t_buckets=((1, 1), (2, 3), (4, 5), (6, 8), (9, 10)), seed=20260829, fill=BaseFill(),
            bins_out_dir=ROOT / "artifacts",
        )
