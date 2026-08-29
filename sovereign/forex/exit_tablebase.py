"""sovereign/forex/exit_tablebase.py — the exit tablebase and its anchored walk-forward.

Spec `specs/045_CARRY_EXIT_TABLEBASE.md` §3-§7, §9 (as amended by AMENDMENT 1,
2026-08-29) is law; nothing here loosens a declared parameter. Backward induction
(pure E[R], no variance penalty — HYP-071's reopen condition) over a discretized
state `(r_bin, t, weekend_next)` with `t` EXACT — Amendment 1: bucketing `t` made
the state graph cyclic in cells (an extreme-R FX carry path can stay in the same
wide bucket for consecutive bars), which is why the original Gauss-Seidel sweep
never converged. With `t` exact, every transition goes from level `t` to level
`t+1` strictly, so a single backward pass (`t = h_max..1`) suffices by
construction; `t_buckets` is retained on `Discretization` for REPORTING only
(coverage/deviation aggregated by bucket) and plays no part in the key.

Fit per anchored walk-forward block on TRAIN rows only, cross-fit A/B to remove
selection optimism, with a per-row "follow the incumbent" fallback below `n_min`
visits (I67 — never `max`, never 0). Terminal rows are absorbing (I71). Applying
the policy to held-out trades prices every decided exit through a swappable
`fill_model` object; an all-FALLBACK table must reproduce the incumbent exactly.

Nothing in this module fits, chooses, or reports a hypothesis test — that is
`sovereign/forex/sprt.py` and the driver's job, deliberately kept out (CLAUDE.md #1).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from sovereign.forex import inventory

REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "artifacts/carry_paths.parquet",
    "artifacts/carry_trades.parquet",
    "artifacts/carry_units.json",
)


class TablebaseError(RuntimeError):
    """Every halt in this module — a bad discretization, a non-finite value,
    an un-purged train/test overlap, a Gauss-Seidel sweep that never settles.
    Never downgraded to a warning."""


# --------------------------------------------------------------------------- #
# Discretization
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Discretization:
    """`r_bin` edges (n_bins - 1 of them) plus the declared `t_bucket` ranges.

    `t_buckets` is always passed in by the caller — never a dataclass default —
    so a walk-forward block can never silently inherit a stale discretization.
    """
    r_edges: tuple[float, ...]
    t_buckets: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        if len(self.r_edges) < 1:
            raise TablebaseError("r_edges must contain at least one edge")
        edges = list(self.r_edges)
        if any(not math.isfinite(e) for e in edges):
            raise TablebaseError(f"r_edges must be finite; got {self.r_edges!r}")
        if edges != sorted(edges):
            raise TablebaseError(f"r_edges must be non-decreasing; got {self.r_edges!r}")
        if len(self.t_buckets) < 1:
            raise TablebaseError("t_buckets must contain at least one bucket")
        prev_hi: int | None = None
        for lo, hi in self.t_buckets:
            if lo > hi:
                raise TablebaseError(f"t_bucket ({lo}, {hi}) has lo > hi")
            if prev_hi is not None and lo <= prev_hi:
                raise TablebaseError(f"t_buckets must be sorted and non-overlapping; got {self.t_buckets!r}")
            prev_hi = hi

    @property
    def n_bins(self) -> int:
        return len(self.r_edges) + 1

    def to_json(self) -> dict[str, Any]:
        return {"r_edges": list(self.r_edges), "t_buckets": [list(b) for b in self.t_buckets]}


def fit_bins(train_rows: pd.DataFrame, *, n_bins: int, t_buckets: Sequence[Sequence[int]]) -> Discretization:
    """Edges at the {1/n_bins, ..., (n_bins-1)/n_bins} quantiles of `unrealized_r_net`
    over TRAIN decision rows (non-absorbing rows) only (I65)."""
    if n_bins < 2:
        raise TablebaseError(f"n_bins must be >= 2; got {n_bins}")
    decision = train_rows[train_rows["absorbed_by"].isna()]
    if decision.empty:
        raise TablebaseError("no training decision rows to fit bin edges from")
    vals = decision["unrealized_r_net"].to_numpy(dtype=float)
    if not np.all(np.isfinite(vals)):
        raise TablebaseError("non-finite unrealized_r_net among training decision rows")
    quantiles = [k / n_bins for k in range(1, n_bins)]
    edges = tuple(float(x) for x in np.quantile(vals, quantiles))
    return Discretization(r_edges=edges, t_buckets=tuple(tuple(b) for b in t_buckets))


def _field(row: Any, name: str) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    try:
        return row[name]
    except (KeyError, TypeError) as e:
        raise TablebaseError(f"row is missing field {name!r}") from e


def state_key(row: Any, disc: Discretization) -> tuple[int, int, int]:
    """`(r_bin, t, weekend_next)` — `t` is EXACT (Amendment 1). `disc.t_buckets` is
    retained for reporting only and is never consulted here."""
    r = float(_field(row, "unrealized_r_net"))
    if not math.isfinite(r):
        raise TablebaseError(f"unrealized_r_net is not finite: {r!r}")
    r_bin = int(np.searchsorted(np.asarray(disc.r_edges, dtype=float), r, side="right"))
    r_bin = min(r_bin, len(disc.r_edges))  # clamp into [0, n_bins - 1]

    t = int(_field(row, "t"))
    if t < 1:
        raise TablebaseError(f"t must be >= 1; got {t}")

    weekend_raw = _field(row, "weekend_next")
    weekend_next = int(weekend_raw)
    if weekend_next not in (0, 1):
        raise TablebaseError(f"weekend_next must be 0 or 1; got {weekend_raw!r}")
    return (r_bin, t, weekend_next)


def t_bucket_index(t: int, t_buckets: Sequence[Sequence[int]]) -> int:
    """Reporting-only (Amendment 1): which declared `t_bucket` a raw `t` falls into.
    Never used to build a state key."""
    for idx, (lo, hi) in enumerate(t_buckets):
        if lo <= t <= hi:
            return idx
    raise TablebaseError(f"t={t} is outside every declared t_bucket {tuple(t_buckets)!r}")


# --------------------------------------------------------------------------- #
# Table
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Cell:
    key: tuple[int, int, int]
    n: int
    q_exit: float
    q_hold: float
    value: float
    action: str  # "EXIT" | "HOLD" | "FALLBACK"


@dataclass(frozen=True)
class Table:
    disc: Discretization
    n_min: int
    cells: dict[tuple[int, int, int], Cell]
    n_undervisited: int          # FALLBACK because n < n_min
    n_tie_fallback: int          # FALLBACK because Q_exit == Q_hold on full train
    n_cross_fit_fallback: int    # FALLBACK because a cross-fit half was empty
    passes: int                  # backward-induction passes (Amendment 1: always 1; a second
                                  # confirmation pass is asserted, not counted as an iteration)


@dataclass(frozen=True)
class _DecisionRow:
    trade_id: int
    unit_id: int
    t: int
    exit_value: float
    fallback_value: float
    cell: tuple[int, int, int]
    next_kind: str                       # "terminal" | "cell"
    next_terminal_r: float | None
    next_cell: tuple[int, int, int] | None


def _row_fallback_value(action: Any, exit_value: float, incumbent_r_net: float, *, trade_id: int, t: int) -> float:
    """Row-level 'follow the incumbent' value (I67) — never `max`, never 0."""
    if action == "EXITED":
        return exit_value
    if action == "HOLD" or (isinstance(action, str) and action.startswith("EXIT:")):
        return incumbent_r_net
    raise TablebaseError(f"trade_id={trade_id} t={t}: unexpected incumbent_action {action!r}")


def _build_decision_rows(paths: pd.DataFrame, trades: pd.DataFrame, disc: Discretization) -> list[_DecisionRow]:
    trades_idx = trades.set_index("trade_id")
    out: list[_DecisionRow] = []
    for trade_id, g in paths.groupby("trade_id", sort=False):
        g = g.sort_values("t").reset_index(drop=True)
        if trade_id not in trades_idx.index:
            raise TablebaseError(f"trade_id {trade_id} has path rows but no matching trades row")
        trow = trades_idx.loc[trade_id]
        unit_id = int(trow["unit_id"])
        incumbent_r_net = float(trow["incumbent_r_net"])
        n = len(g)
        if n == 0:
            continue
        last = g.iloc[-1]
        if pd.isna(last["absorbed_by"]):
            raise TablebaseError(f"trade_id {trade_id}: last path row (t={last['t']}) is not absorbing")
        for i in range(n - 1):
            row = g.iloc[i]
            if not pd.isna(row["absorbed_by"]):
                raise TablebaseError(f"trade_id {trade_id} t={row['t']}: absorbing row is not the last row")
            exit_value = float(row["unrealized_r_net"])
            if not math.isfinite(exit_value):
                raise TablebaseError(f"trade_id {trade_id} t={row['t']}: non-finite unrealized_r_net")
            fallback_value = _row_fallback_value(row["incumbent_action"], exit_value, incumbent_r_net,
                                                  trade_id=trade_id, t=int(row["t"]))
            cell = state_key(row, disc)
            nxt = g.iloc[i + 1]
            if not pd.isna(nxt["absorbed_by"]):
                tr = nxt["terminal_r"]
                if pd.isna(tr):
                    raise TablebaseError(
                        f"trade_id {trade_id}: absorbing row at t={nxt['t']} has NaN terminal_r "
                        "(a terminal row must carry a realized terminal_r — I71)")
                out.append(_DecisionRow(trade_id=int(trade_id), unit_id=unit_id, t=int(row["t"]),
                                         exit_value=exit_value, fallback_value=fallback_value, cell=cell,
                                         next_kind="terminal", next_terminal_r=float(tr), next_cell=None))
            else:
                if int(nxt["t"]) <= int(row["t"]):
                    raise TablebaseError(
                        f"trade_id {trade_id}: next decision row t={nxt['t']} is not strictly greater than "
                        f"t={row['t']} — the single-backward-pass premise (Amendment 1) requires it")
                next_cell = state_key(nxt, disc)
                out.append(_DecisionRow(trade_id=int(trade_id), unit_id=unit_id, t=int(row["t"]),
                                         exit_value=exit_value, fallback_value=fallback_value, cell=cell,
                                         next_kind="cell", next_terminal_r=None, next_cell=next_cell))
    return out


def _next_value(row: _DecisionRow, v: Mapping[tuple[int, int, int], float]) -> float:
    if row.next_kind == "terminal":
        return row.next_terminal_r  # type: ignore[return-value]
    return v.get(row.next_cell, 0.0)  # type: ignore[arg-type]


def _mean(xs: Sequence[float]) -> float:
    if not xs:
        raise TablebaseError("cannot take the mean of zero rows")
    total = sum(xs)
    m = total / len(xs)
    if not math.isfinite(m):
        raise TablebaseError(f"non-finite mean over {len(xs)} rows (values include a NaN/inf)")
    return m


def build_table(train_paths: pd.DataFrame, train_trades: pd.DataFrame, *, disc: Discretization,
                 n_min: int, seed: int) -> Table:
    """Backward induction, spec §4, as amended (Amendment 1, 2026-08-29): the state
    key uses `t` EXACT, so every row's "next" cell (when not terminal) is the next
    row of the same trade sorted by `t` — strictly greater `t`, never the same cell,
    never an earlier one (verified contiguous, +1, on the real 350; the argument
    only needs strictly-greater). A single backward pass over cells in descending
    `t` therefore computes every cell's value exactly once, using only
    already-resolved higher-`t` values; termination is by construction, not by
    iteration. A second confirmation pass is run and asserted to change nothing by
    more than 1e-12 — if it does, the "next is always strictly higher t" premise
    was violated somewhere and this halts rather than silently iterating."""
    if n_min < 1:
        raise TablebaseError(f"n_min must be >= 1; got {n_min}")
    decision_rows = _build_decision_rows(train_paths, train_trades, disc)
    if not decision_rows:
        raise TablebaseError("no training decision rows — cannot build a table")

    cells: dict[tuple[int, int, int], list[_DecisionRow]] = {}
    for r in decision_rows:
        cells.setdefault(r.cell, []).append(r)

    unit_ids = sorted({r.unit_id for r in decision_rows})
    if len(unit_ids) < 2:
        raise TablebaseError(f"cross-fit needs at least 2 training units with decision rows; got {len(unit_ids)}")
    rng = np.random.default_rng(seed)
    ids_arr = np.array(unit_ids)
    rng.shuffle(ids_arr)
    half = len(ids_arr) // 2
    unit_a: set[int] = set(int(x) for x in ids_arr[:half])
    unit_b: set[int] = set(int(x) for x in ids_arr[half:])

    fallback_mean: dict[tuple[int, int, int], float] = {
        key: _mean([row.fallback_value for row in rows]) for key, rows in cells.items()
    }
    undervisited = {key for key, rows in cells.items() if len(rows) < n_min}

    v: dict[tuple[int, int, int], float] = {key: fallback_mean[key] for key in undervisited}
    # Exact-t key: index 1 of (r_bin, t, weekend_next) is the raw bar. Processing
    # strictly-descending t guarantees every "next" reference used below (always at
    # t + 1, per _build_decision_rows) is already resolved by the time it is read.
    dynamic_keys = sorted((k for k in cells if k not in undervisited), key=lambda k: -k[1])

    def _cell_value(key: tuple[int, int, int], rows: list[_DecisionRow],
                     v_lookup: Mapping[tuple[int, int, int], float]) -> float:
        q_exit = _mean([row.exit_value for row in rows])
        q_hold = _mean([_next_value(row, v_lookup) for row in rows])
        if q_exit == q_hold:
            return fallback_mean[key]
        rows_a = [r for r in rows if r.unit_id in unit_a]
        rows_b = [r for r in rows if r.unit_id in unit_b]
        if not rows_a or not rows_b:
            return fallback_mean[key]
        qa_exit = _mean([r.exit_value for r in rows_a])
        qa_hold = _mean([_next_value(r, v_lookup) for r in rows_a])
        qb_exit = _mean([r.exit_value for r in rows_b])
        qb_hold = _mean([_next_value(r, v_lookup) for r in rows_b])
        action_a = "EXIT" if qa_exit > qa_hold else ("HOLD" if qa_hold > qa_exit else "HOLD")
        action_b = "EXIT" if qb_exit > qb_hold else ("HOLD" if qb_hold > qb_exit else "HOLD")
        value_a_on_b = qb_exit if action_a == "EXIT" else qb_hold
        value_b_on_a = qa_exit if action_b == "EXIT" else qa_hold
        return (value_a_on_b + value_b_on_a) / 2.0

    # The single backward pass.
    for key in dynamic_keys:
        new_v = _cell_value(key, cells[key], v)
        if not math.isfinite(new_v):
            raise TablebaseError(f"cell {key}: non-finite V={new_v}")
        v[key] = new_v

    # Convergence ASSERTION (not a loop): a second pass over the fully-populated
    # table must be a fixed point everywhere.
    for key in dynamic_keys:
        confirm_v = _cell_value(key, cells[key], v)
        delta = abs(confirm_v - v[key])
        if delta > 1e-12:
            raise TablebaseError(
                f"single backward pass is not a fixed point at cell {key}: "
                f"first pass V={v[key]!r}, second pass V={confirm_v!r}, Δ={delta!r} "
                "(a 'next' reference was not strictly at t + 1 — investigate _build_decision_rows)")

    cell_records: dict[tuple[int, int, int], Cell] = {}
    n_tie_fallback = 0
    n_cross_fit_fallback = 0
    for key, rows in cells.items():
        q_exit = _mean([row.exit_value for row in rows])
        q_hold = _mean([_next_value(row, v) for row in rows])
        n = len(rows)
        if key in undervisited:
            action = "FALLBACK"
        elif q_exit == q_hold:
            action = "FALLBACK"
            n_tie_fallback += 1
        else:
            rows_a = [r for r in rows if r.unit_id in unit_a]
            rows_b = [r for r in rows if r.unit_id in unit_b]
            if not rows_a or not rows_b:
                action = "FALLBACK"
                n_cross_fit_fallback += 1
            else:
                action = "EXIT" if q_exit > q_hold else "HOLD"
        cell_records[key] = Cell(key=key, n=n, q_exit=q_exit, q_hold=q_hold, value=v[key], action=action)

    return Table(disc=disc, n_min=n_min, cells=cell_records, n_undervisited=len(undervisited),
                 n_tie_fallback=n_tie_fallback, n_cross_fit_fallback=n_cross_fit_fallback, passes=1)


# --------------------------------------------------------------------------- #
# Applying the policy
# --------------------------------------------------------------------------- #

def _price_exit(row: Any, trow: Any, fill: Any) -> float:
    from sovereign.forex.fill_model import net_r  # local import — keeps fill_model optional at import time

    close = float(_field(row, "close"))
    open_next_raw = _field(row, "open_next")
    open_next = float(open_next_raw) if open_next_raw is not None and not pd.isna(open_next_raw) else float("nan")
    exit_price = fill.exit_price(close=close, open_next=open_next)

    direction = int(trow["direction"])
    entry_price = float(trow["entry_price"])
    gross = direction * (exit_price / entry_price - 1.0)
    spread_frac, swap_frac = fill.cost_fracs(
        pair=str(trow["pair"]), entry_price=entry_price, direction=direction,
        hold_bars=int(_field(row, "t")), entry_date=pd.Timestamp(trow["entry_date"]))
    risk_pct = float(trow["risk_pct"])
    return net_r(gross_pnl_pct=gross, spread_frac=spread_frac, swap_frac=swap_frac, risk_pct=risk_pct)


def apply_policy(table: Table, test_paths: pd.DataFrame, test_trades: pd.DataFrame, *, fill: Any) -> pd.DataFrame:
    """Walk each test trade's rows t=1..; price every decided exit through `fill`."""
    trades_idx = test_trades.set_index("trade_id")
    records: list[dict[str, Any]] = []
    for trade_id, g in test_paths.groupby("trade_id", sort=False):
        if trade_id not in trades_idx.index:
            raise TablebaseError(f"trade_id {trade_id} has test path rows but no matching trades row")
        trow = trades_idx.loc[trade_id]
        g = g.sort_values("t").reset_index(drop=True)

        candidate_r: float | None = None
        decided = False
        exit_bar: int | None = None
        absorbed_by: Any = None

        for i in range(len(g)):
            row = g.iloc[i]
            if not pd.isna(row["absorbed_by"]):
                tr = row["terminal_r"]
                if pd.isna(tr):
                    raise TablebaseError(f"trade_id {trade_id}: absorbing row at t={row['t']} has NaN terminal_r")
                candidate_r = float(tr)
                decided = False
                exit_bar = int(row["t"])
                absorbed_by = row["absorbed_by"]
                break

            key = state_key(row, table.disc)
            cell = table.cells.get(key)
            action = cell.action if cell is not None else "FALLBACK"

            if action == "EXIT":
                candidate_r = _price_exit(row, trow, fill)
                decided = True
                exit_bar = int(row["t"])
                break
            if action == "HOLD":
                continue

            # FALLBACK — follow the incumbent, per row.
            inc_action = row["incumbent_action"]
            if isinstance(inc_action, str) and inc_action.startswith("EXIT:"):
                candidate_r = _price_exit(row, trow, fill)
                decided = True
                exit_bar = int(row["t"])
                break
            if inc_action == "EXITED":
                candidate_r = _price_exit(row, trow, fill)
                decided = True
                exit_bar = int(row["t"])
                break
            if inc_action == "HOLD":
                continue
            raise TablebaseError(f"trade_id {trade_id} t={row['t']}: unexpected incumbent_action {inc_action!r}")
        else:
            raise TablebaseError(f"trade_id {trade_id}: path never absorbed and no candidate decision was made")

        if candidate_r is None or exit_bar is None:
            raise TablebaseError(f"trade_id {trade_id}: no candidate_r/exit_bar determined")

        incumbent_r_net = float(trow["incumbent_r_net"])
        delta_r = candidate_r - incumbent_r_net
        records.append({
            "trade_id": int(trade_id),
            "unit_id": int(trow["unit_id"]),
            "candidate_exit_bar": exit_bar,
            "candidate_r": candidate_r,
            "incumbent_r_net": incumbent_r_net,
            "delta_r": delta_r,
            "deviated": not math.isclose(delta_r, 0.0, abs_tol=1e-9),
            "decided": decided,
            "absorbed_by": absorbed_by,
        })
    return pd.DataFrame.from_records(records)


# --------------------------------------------------------------------------- #
# Anchored walk-forward
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BlockResult:
    k: int  # 2..5 — the driver (scripts/carry_exit_sprt.py) reads this name
    train_trade_ids: tuple[int, ...]   # driver-required
    test_trade_ids: tuple[int, ...]    # driver-required
    coverage: float                    # driver-required: fraction of test decision rows in non-FALLBACK cells
    disc: Discretization                # driver-required: .disc.r_edges
    train_unit_ids: tuple[int, ...]
    test_unit_ids: tuple[int, ...]
    purged_unit_ids: tuple[int, ...]
    coverage_by_bucket: dict[tuple[int, int], float]  # reporting only (Amendment 1), keyed by declared t_bucket
    table: Table
    applied: pd.DataFrame

    @property
    def block_index(self) -> int:
        """Alias of `k`, kept for readability at call sites that predate the driver contract."""
        return self.k


@dataclass(frozen=True)
class WalkForwardResult:
    blocks: list[BlockResult]  # ordered by k ascending — the driver iterates `for b in res.blocks`
    oof: pd.DataFrame          # trade_id, unit_id, block, delta_r, deviated, decided, candidate_r, incumbent_r_net, ...
    per_unit_delta: pd.DataFrame  # unit_id, block, n_trades, mean_delta_r, sum_delta_r


def _assert_no_train_test_overlap(train_trades: pd.DataFrame, *, test_start: pd.Timestamp,
                                   test_end: pd.Timestamp) -> None:
    """I66 — no training trade's [entry_date, path_end_date] intersects [test_start, test_end]."""
    overlap = train_trades[
        (train_trades["entry_date"] <= test_end) & (train_trades["path_end_date"] >= test_start)
    ]
    if not overlap.empty:
        raise TablebaseError(
            f"self-exclusion violated (I66): {len(overlap)} training trade(s) overlap "
            f"[{test_start.date()}, {test_end.date()}] — trade_ids {sorted(overlap['trade_id'].tolist())[:10]}")


def walk_forward(paths: pd.DataFrame, trades: pd.DataFrame, units: Mapping[Any, Sequence[int]], *,
                  blocks: tuple[str, ...], h_max: int, n_min: int, n_bins: int,
                  t_buckets: Sequence[Sequence[int]], seed: int, fill: Any, bins_out_dir: Path) -> WalkForwardResult:
    """Anchored walk-forward over units, spec §5. `require_hashes` runs first (the
    guardrail); a hash mismatch or missing entry halts before anything is fit."""
    inventory.require_hashes(REQUIRED_ARTIFACTS)

    if h_max < 1:
        raise TablebaseError(f"h_max must be >= 1; got {h_max}")
    declared_h_max = max(hi for _, hi in t_buckets)
    if declared_h_max != h_max:
        raise TablebaseError(f"h_max={h_max} does not match t_buckets' declared max ({declared_h_max})")
    if len(blocks) < 2:
        raise TablebaseError(f"walk-forward needs at least 2 block starts (train + 1 test); got {blocks!r}")

    boundaries = [pd.Timestamp(b) for b in blocks]
    if boundaries != sorted(boundaries):
        raise TablebaseError(f"block starts must be chronological; got {blocks!r}")

    paths = paths[paths["t"] <= h_max].copy()

    # `units` may be either the direct {unit_id: [trade_ids]} mapping or the raw
    # artifacts/carry_units.json wrapper ({"units": {...}, "h_max": ..., ...}) — the
    # driver (scripts/carry_exit_sprt.py) loads and passes the whole file as-is.
    units_map = units["units"] if ("units" in units and isinstance(units.get("units"), Mapping)) else units
    units_norm: dict[int, list[int]] = {int(k): [int(t) for t in v] for k, v in units_map.items()}
    trades_idx = trades.set_index("trade_id")
    for unit_id, trade_ids in units_norm.items():
        for tid in trade_ids:
            if tid not in trades_idx.index:
                raise TablebaseError(f"unit {unit_id} references trade_id {tid} absent from trades")
            actual_unit = int(trades_idx.loc[tid, "unit_id"])
            if actual_unit != unit_id:
                raise TablebaseError(
                    f"unit map disagrees with trades.unit_id for trade_id {tid}: "
                    f"units.json says {unit_id}, trades says {actual_unit}")

    entry_dates = pd.to_datetime(trades.set_index("trade_id")["entry_date"])
    path_end_dates = paths.sort_values("t").groupby("trade_id")["date"].max()

    unit_earliest_entry: dict[int, pd.Timestamp] = {}
    unit_latest_path_end: dict[int, pd.Timestamp] = {}
    for unit_id, trade_ids in units_norm.items():
        unit_earliest_entry[unit_id] = min(entry_dates.loc[tid] for tid in trade_ids)
        unit_latest_path_end[unit_id] = max(path_end_dates.loc[tid] for tid in trade_ids)

    def block_index(dt: pd.Timestamp) -> int:
        idx = 0
        for i, b in enumerate(boundaries):
            if dt >= b:
                idx = i
            else:
                break
        return idx

    last_end = pd.Timestamp("2024-12-31")
    bins_out_dir = Path(bins_out_dir)
    bins_out_dir.mkdir(parents=True, exist_ok=True)

    block_results: dict[int, BlockResult] = {}
    oof_frames: list[pd.DataFrame] = []
    per_unit_rows: list[dict[str, Any]] = []

    for k in range(2, len(boundaries) + 1):
        test_block_idx = k - 1  # 0-indexed
        test_start = boundaries[test_block_idx]
        test_end = boundaries[test_block_idx + 1] - pd.Timedelta(days=1) if test_block_idx + 1 < len(boundaries) \
            else last_end

        train_units_raw = {u for u, dt in unit_earliest_entry.items() if block_index(dt) < test_block_idx}
        test_units = {u for u, dt in unit_earliest_entry.items() if block_index(dt) == test_block_idx}
        purged_units = {u for u in train_units_raw if unit_latest_path_end[u] >= test_start}
        train_units = train_units_raw - purged_units

        if not train_units:
            raise TablebaseError(f"block {k}: no training units survive purging")
        if not test_units:
            raise TablebaseError(f"block {k}: no test units")

        train_trade_ids = sorted(tid for u in train_units for tid in units_norm[u])
        test_trade_ids = sorted(tid for u in test_units for tid in units_norm[u])

        train_trades_block = trades[trades["trade_id"].isin(train_trade_ids)].copy()
        train_trades_block["entry_date"] = pd.to_datetime(train_trades_block["entry_date"])
        train_trades_block["path_end_date"] = train_trades_block["trade_id"].map(path_end_dates)
        _assert_no_train_test_overlap(train_trades_block, test_start=test_start, test_end=test_end)

        train_paths_block = paths[paths["trade_id"].isin(train_trade_ids)]
        disc = fit_bins(train_paths_block, n_bins=n_bins, t_buckets=t_buckets)

        bins_path = bins_out_dir / f"tablebase_bins_block{k}.json"
        with bins_path.open("w") as fh:
            json.dump(disc.to_json(), fh, indent=2, sort_keys=True)
            fh.write("\n")
        inventory.record({bins_path: f"exit tablebase discretization, block {k} (train B1..B{test_block_idx})"})

        table = build_table(train_paths_block, train_trades_block, disc=disc, n_min=n_min, seed=seed)

        test_paths_block = paths[paths["trade_id"].isin(test_trade_ids)]
        test_trades_block = trades[trades["trade_id"].isin(test_trade_ids)]

        # Coverage overall AND by t_bucket (Amendment 1: t_buckets is reporting-only).
        decision_test_rows = test_paths_block[test_paths_block["absorbed_by"].isna()]
        covered_by_bucket: dict[tuple[int, int], int] = {}
        total_by_bucket: dict[tuple[int, int], int] = {}
        covered = 0
        for row in decision_test_rows.itertuples(index=False):
            key = state_key(row, disc)
            cell = table.cells.get(key)
            is_covered = cell is not None and cell.action != "FALLBACK"
            covered += int(is_covered)
            bucket = t_buckets[t_bucket_index(int(row.t), t_buckets)]
            total_by_bucket[bucket] = total_by_bucket.get(bucket, 0) + 1
            covered_by_bucket[bucket] = covered_by_bucket.get(bucket, 0) + int(is_covered)
        n_decision = len(decision_test_rows)
        coverage = covered / n_decision if n_decision else float("nan")
        coverage_by_bucket = {b: covered_by_bucket[b] / total_by_bucket[b] for b in total_by_bucket}

        applied = apply_policy(table, test_paths_block, test_trades_block, fill=fill)
        applied["block"] = k
        applied["unit_earliest_entry"] = applied["unit_id"].map(unit_earliest_entry)
        applied = applied.sort_values(["unit_earliest_entry", "trade_id"]).reset_index(drop=True)

        block_results[k] = BlockResult(
            k=k, train_trade_ids=tuple(train_trade_ids), test_trade_ids=tuple(test_trade_ids),
            coverage=coverage, disc=disc, train_unit_ids=tuple(sorted(train_units)),
            test_unit_ids=tuple(sorted(test_units)), purged_unit_ids=tuple(sorted(purged_units)),
            coverage_by_bucket=coverage_by_bucket, table=table, applied=applied)
        oof_frames.append(applied)

        for unit_id, g in applied.groupby("unit_id"):
            per_unit_rows.append({
                "unit_id": int(unit_id), "block": k, "n_trades": len(g),
                "mean_delta_r": float(g["delta_r"].mean()), "sum_delta_r": float(g["delta_r"].sum()),
            })

    oof = pd.concat(oof_frames, ignore_index=True) if oof_frames else pd.DataFrame()
    per_unit_delta = pd.DataFrame.from_records(per_unit_rows)
    blocks_list = [block_results[k] for k in sorted(block_results)]
    return WalkForwardResult(blocks=blocks_list, oof=oof, per_unit_delta=per_unit_delta)
