#!/usr/bin/env python3
"""scripts/carry_tablebase_paths.py — Plan Step 2: the carry-exit tablebase path extractor.

Reproduces `scripts/run_cb_ab.py`'s `cb_off` arm EXACTLY (env + patch + module-reload
dance + `oos_campaign_test.get_trades(START, END)`), while capturing every call to
`sovereign.forex.fast_backtester.simulate_forex_trades_arrays` (and the
`_simulate_forex_core` call inside it — `simulate_forex_trades` routes through both,
confirmed by inspection: both names are looked up in `fast_backtester`'s own module
globals at call time, so patching them there intercepts every caller).

Emits three artifacts:
  artifacts/carry_trades.parquet — one row per trade (350), the incumbent's own
    outcome plus everything the tablebase needs to start a candidate path.
  artifacts/carry_paths.parquet  — one row per (trade, decision bar) t=1..H_MAX
    (or up to the first forced terminal), with exactly one absorbing row per trade.
  artifacts/carry_units.json     — connected components of the [entry, path-end]
    interval overlap graph across all 4 pairs (the SPRT unit), plus entry-date
    clusters as a secondary grouping.

Every halt raises `ExtractorError` (exit code 2 from `main`) and never loosens.
See Plans/cheeky-mixing-locket.md, "Step 2 — Path extractor" and
"What the review changed" (H_max 10, absorbing terminals, per-row cost_frac).
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sovereign.forex.exit_machine import (  # noqa: E402
    BarContext, ExitConfig, ExitDecision, PositionState, decide_exit,
)

START, END = "2015-01-01", "2024-12-31"
RATE_MODE = "nominal"
CSV_PATH = ROOT / "data" / "cb_ab" / "cb_off_trades.csv"
ARTIFACTS = ROOT / "artifacts"
TRADES_OUT = ARTIFACTS / "carry_trades.parquet"
PATHS_OUT = ARTIFACTS / "carry_paths.parquet"
UNITS_OUT = ARTIFACTS / "carry_units.json"
EXPECTED_N = 350
EXPECTED_SUM_R = 34.41

# The nominal-vintage macro cache (data/cache/macro_nominal/) is not present in this
# working tree -- it is a rebuildable cache (scripts/build_rate_vintages.py needs
# FRED_API_KEY + live ALFRED network, unavailable here), not sealed evidence. An
# identical copy (byte-for-byte, the one that reproduces the pinned cb_off population)
# is on disk in a sibling session's scratchpad. Same technique, same rationale, as
# scripts/run_cb_ab.py's own SCRATCH_NOMINAL_DIR patch (that file is read-only to this
# task) -- reused here because "replicate exactly how that arm ran" requires the same
# information set the pinned data/cb_ab/cb_off_trades.csv was produced under.
SCRATCH_NOMINAL_DIR = Path(
    "/private/tmp/claude-501/-Users-taboost-passing-funded-account-1-/"
    "42802b2d-5476-426f-b67f-631a5679f292/scratchpad/macro_nominal"
)

STOP, REVERSAL, CB_REFRESH, TIME, TRAILING, DONCHIAN = (
    int(ExitDecision.INITIAL_STOP), int(ExitDecision.REVERSAL), int(ExitDecision.CB_REFRESH),
    int(ExitDecision.TIME), int(ExitDecision.TRAILING_ATR), int(ExitDecision.DONCHIAN),
)
HOLD = int(ExitDecision.HOLD)
FORCED_SET = {STOP, REVERSAL, CB_REFRESH}
ABSORBED_STR = {STOP: "STOP", REVERSAL: "REVERSAL", CB_REFRESH: "CB_REFRESH"}
ACTION_STR = {
    HOLD: "HOLD", STOP: "EXIT:stop", REVERSAL: "EXIT:reversal", CB_REFRESH: "EXIT:cb_refresh",
    TIME: "EXIT:time", TRAILING: "EXIT:trailing_stop", DONCHIAN: "EXIT:donchian_exit",
}
CSV_REASON_STR = {
    STOP: "stop", REVERSAL: "reversal", CB_REFRESH: "cb_refresh", TIME: "time",
    TRAILING: "trailing_stop", DONCHIAN: "donchian_exit",
}

TRADE_COLUMNS = [
    "trade_id", "pair", "direction", "entry_bar", "entry_date", "entry_price",
    "stop_price", "risk_dist", "hold_limit", "trailing_mult", "stop_atr_mult",
    "incumbent_exit_bar", "incumbent_hold", "incumbent_reason", "incumbent_r_gross",
    "cost_frac", "cost_spread_frac", "cost_swap_frac", "incumbent_r_net", "risk_pct",
    "path_end_bar", "path_end_reason", "unit_id", "entry_cluster",
]
PATH_COLUMNS = [
    "trade_id", "pair", "direction", "t", "bar", "date", "close", "open_next",
    "atr_pct", "signal", "hold_today", "weekend_next", "unrealized_r_gross",
    "unrealized_r_net", "incumbent_action", "forced", "absorbed_by", "terminal_r",
]


class ExtractorError(RuntimeError):
    """A Step-2 halt condition. Never downgraded to a warning, never loosened."""


# ─────────────────────────── pure, importable functions ─────────────────────────── #
# These read no files and touch no CSV; they are exercised directly by
# scripts/test_carry_tablebase_paths.py on synthetic arrays.

def weekend_next_flags(index) -> np.ndarray:
    """1 if the NEXT bar in `index` is >= 3 calendar days after this bar's date,
    else 0. The last bar is always 0 (no next bar to gap against)."""
    idx = pd.DatetimeIndex(index)
    n = len(idx)
    out = np.zeros(n, dtype=np.int8)
    for i in range(n - 1):
        gap_days = (idx[i + 1] - idx[i]).days
        out[i] = 1 if gap_days >= 3 else 0
    return out


def compute_stop_price(*, entry_price: float, direction: int, atr_pcts, entry_bar: int,
                        stop_atr_mult: float) -> tuple[float, float]:
    """Mirrors `_simulate_forex_core`'s stop-price assignment exactly: the ATR used is
    read at the SIGNAL bar (entry_bar - 1), not the entry (fill) bar itself. Returns
    (stop_price, risk_dist) where risk_dist = |entry_price - stop_price| = stop_dist."""
    signal_bar = entry_bar - 1
    if signal_bar < 0:
        raise ExtractorError(f"entry_bar {entry_bar} has no preceding signal bar")
    entry_atr = max(float(atr_pcts[signal_bar]), 1e-6)
    stop_dist = entry_price * stop_atr_mult * entry_atr
    stop_price = entry_price - stop_dist if direction == 1 else entry_price + stop_dist
    return stop_price, stop_dist


def unrealized_r(*, direction: int, close: float, entry_price: float, cost_frac: float,
                  risk_dist: float, risk_pct: float) -> tuple[float, float]:
    """unrealized_r_gross = direction*(close-entry_price)/risk_dist (price-distance R).
    unrealized_r_net = (direction*(close-entry_price)/entry_price - cost_frac) / risk_pct
    -- `cost_frac` is the trade's single, per-trade cost fraction (not recomputed
    per bar), so this is exact at the incumbent's own realized exit bar by
    construction (gross - cost_frac == csv net pnl_pct there)."""
    if not (risk_dist > 0):
        raise ExtractorError(f"risk_dist must be > 0, got {risk_dist}")
    if not (risk_pct > 0):
        raise ExtractorError(f"risk_pct must be > 0, got {risk_pct}")
    gross = direction * (close - entry_price) / risk_dist
    net = (direction * (close - entry_price) / entry_price - cost_frac) / risk_pct
    return gross, net


def replay_decisions(*, closes, atr_pcts, signals, hold_days_arr, entry_bar: int,
                      direction: int, entry_price: float, stop_price: float,
                      hold_limit: int, stop_atr_mult: float, trailing_atr_mult: float,
                      strict_mode: bool, enable_cb_refresh: bool, max_t: int):
    """Bar-by-bar `decide_exit` replay from t=1 (the entry bar, hold_count=1 at its
    close) through t=max_t inclusive. `best_price`/`worst_price` initialise to
    `entry_price` -- mirrors what `_simulate_forex_core` does at entry. Returns a
    list of (t, bar, decision_int) tuples, one per bar walked; never stops early
    except by raising (missing data is a halt, never a silent truncation)."""
    if max_t < 1:
        raise ExtractorError(f"max_t must be >= 1, got {max_t}")
    cfg = ExitConfig(stop_atr_mult, trailing_atr_mult, strict_mode, enable_cb_refresh)
    state = PositionState(direction, stop_price, entry_price, entry_price, 0, hold_limit)
    n = len(closes)
    out = []
    for t in range(1, max_t + 1):
        bar = entry_bar + t - 1
        if bar >= n:
            raise ExtractorError(f"replay needs bar {bar} (t={t}) but only {n} bars of data exist")
        close = closes[bar]
        atrp = atr_pcts[bar]
        hold_today = hold_days_arr[bar]
        if close is None or np.isnan(close):
            raise ExtractorError(f"close missing at bar {bar}")
        if atrp is None or np.isnan(atrp):
            raise ExtractorError(f"atr_pct missing at bar {bar}")
        if hold_today is None or (isinstance(hold_today, float) and np.isnan(hold_today)):
            raise ExtractorError(f"hold_days missing at bar {bar}")
        bctx = BarContext(float(close), float(atrp), int(signals[bar]), int(hold_today), float("nan"))
        res = decide_exit(state, bctx, cfg)
        state = res.state
        out.append((t, bar, int(res.decision)))
    return out


def build_trade_path(*, closes, opens, atr_pcts, signals, hold_days_arr, entry_bar: int,
                      direction: int, entry_price: float, stop_price: float, risk_dist: float,
                      hold_limit: int, stop_atr_mult: float, trailing_atr_mult: float,
                      strict_mode: bool, enable_cb_refresh: bool, h_max: int,
                      incumbent_hold: int, incumbent_r_net: float, cost_frac: float,
                      risk_pct: float, index, weekend_flags):
    """The row builder + terminal logic (I71: terminals are absorbing states), pure
    and testable on synthetic arrays. Returns (rows, absorbed_by, absorbing_t,
    path_end_bar, terminal_r) with `rows` containing exactly one row whose
    `absorbed_by` / `terminal_r` are set (I70/G7 companion: "exactly one
    absorbing row per path" is asserted here, not just hoped for)."""
    bounded = replay_decisions(
        closes=closes, atr_pcts=atr_pcts, signals=signals, hold_days_arr=hold_days_arr,
        entry_bar=entry_bar, direction=direction, entry_price=entry_price, stop_price=stop_price,
        hold_limit=hold_limit, stop_atr_mult=stop_atr_mult, trailing_atr_mult=trailing_atr_mult,
        strict_mode=strict_mode, enable_cb_refresh=enable_cb_refresh, max_t=h_max,
    )
    emitted = []
    absorbed_by = None
    absorbing_t = None
    for (t, bar, decision) in bounded:
        close = float(closes[bar])
        g_r, n_r = unrealized_r(direction=direction, close=close, entry_price=entry_price,
                                 cost_frac=cost_frac, risk_dist=risk_dist, risk_pct=risk_pct)
        action = "EXITED" if t > incumbent_hold else ACTION_STR[decision]
        forced = decision in FORCED_SET
        row = {
            "t": t, "bar": bar, "date": index[bar], "close": close,
            "open_next": float(opens[bar + 1]) if bar + 1 < len(opens) else float("nan"),
            "atr_pct": float(atr_pcts[bar]), "signal": int(signals[bar]),
            "hold_today": int(hold_days_arr[bar]), "weekend_next": int(weekend_flags[bar]),
            "unrealized_r_gross": g_r, "unrealized_r_net": n_r,
            "incumbent_action": action, "forced": bool(forced),
            "absorbed_by": None, "terminal_r": float("nan"),
        }
        emitted.append(row)
        if forced:
            absorbed_by = ABSORBED_STR[decision]
            absorbing_t = t
            break
    if absorbed_by is None:
        absorbed_by = "HMAX"
        absorbing_t = h_max
        if len(emitted) != h_max:
            raise ExtractorError(f"expected {h_max} rows for HMAX absorption, got {len(emitted)}")

    absorbing_row = emitted[absorbing_t - 1]
    if absorbing_row["t"] != absorbing_t:
        raise ExtractorError("absorbing row index mismatch")

    if absorbed_by == "HMAX" and incumbent_hold > h_max:
        terminal_r = incumbent_r_net
    else:
        terminal_r = absorbing_row["unrealized_r_net"]
    absorbing_row["terminal_r"] = terminal_r
    absorbing_row["absorbed_by"] = absorbed_by

    n_absorbing = sum(1 for r in emitted if r["absorbed_by"] is not None)
    if n_absorbing != 1:
        raise ExtractorError(f"expected exactly one absorbing row, got {n_absorbing}")

    return emitted, absorbed_by, absorbing_t, int(absorbing_row["bar"]), terminal_r


def connected_components(intervals):
    """`intervals`: iterable of (id, start, end) with start <= end, start/end
    comparable (e.g. pd.Timestamp). Returns {unit_id: [ids...]} for connected
    components of the CLOSED-interval overlap graph (start_a <= end_b and
    start_b <= end_a), unit_id assigned 0..K-1 in order of the component's
    minimum start. The classic "merge overlapping intervals" sweep IS connected
    components here: sorted by start, a new interval joins the running group iff
    its start falls within the group's running max-end (transitively correct --
    anything already grouped has start <= this interval's start, so once the
    running max-end is beaten, nothing later can reach back into the old group)."""
    ordered = sorted(intervals, key=lambda x: (x[1], x[2], x[0]))
    groups = []
    current = None
    for _id, start, end in ordered:
        if current is None:
            current = {"ids": [_id], "max_end": end}
        elif start <= current["max_end"]:
            current["ids"].append(_id)
            if end > current["max_end"]:
                current["max_end"] = end
        else:
            groups.append(current)
            current = {"ids": [_id], "max_end": end}
    if current is not None:
        groups.append(current)
    return {i: sorted(g["ids"]) for i, g in enumerate(groups)}


# ───────────────────────────── the rig capture (impure) ─────────────────────────── #

def _run_arm_capture():
    """Replicates run_cb_ab.run_arm(cb_on=False): env + patch + the module-reload
    dance, then oos_campaign_test.get_trades(START, END) -- while capturing every
    `simulate_forex_trades_arrays` call's inputs, its inner `_simulate_forex_core`
    call's raw outputs, and which pair drove it (via a wrap on
    `ForexBacktester._simulate_trades`, the only call site that actually carries
    `pair`). Returns (trades, arrays_calls)."""
    os.environ["CARRY_RATE_VINTAGE"] = RATE_MODE
    for m in list(sys.modules):
        if m.startswith("sovereign.forex") or m == "oos_campaign_test":
            del sys.modules[m]
    import oos_campaign_test as rig  # noqa: E402
    from sovereign.forex import entry_engine, rate_vintage  # noqa: E402
    from sovereign.forex import fast_backtester as fbk  # noqa: E402
    from sovereign.forex import forex_backtester as fb  # noqa: E402

    if SCRATCH_NOMINAL_DIR.is_dir():
        rate_vintage._DIRS[rate_vintage.NOMINAL] = SCRATCH_NOMINAL_DIR
    else:
        try:
            rate_vintage.require_cache(rate_vintage.macro_cache_dir(rate_vintage.NOMINAL))
        except Exception as e:  # noqa: BLE001 -- re-raised as our own halt type below
            raise ExtractorError(
                f"nominal rate vintage cache unavailable at "
                f"{rate_vintage.macro_cache_dir(rate_vintage.NOMINAL)}, and the scratch "
                f"fallback {SCRATCH_NOMINAL_DIR} is also missing -- cannot reproduce the "
                "pinned cb_off population without it"
            ) from e

    entry_engine.CB_LAYER_DISABLED = True
    if entry_engine.CB_LAYER_DISABLED is not True:
        raise ExtractorError("CB_LAYER_DISABLED patch did not take")

    current_pair = {"pair": None}
    core_calls: list[dict] = []
    arrays_calls: list[dict] = []

    orig_core = fbk._simulate_forex_core

    def wrapped_core(**kwargs):
        result = orig_core(**kwargs)
        core_calls.append({"kwargs": kwargs, "result": result})
        return result

    fbk._simulate_forex_core = wrapped_core

    orig_arrays = fbk.simulate_forex_trades_arrays
    sig_arrays = inspect.signature(orig_arrays)

    def wrapped_arrays(*args, **kwargs):
        pair = current_pair["pair"]
        if pair is None:
            raise ExtractorError("simulate_forex_trades_arrays called with no known pair context")
        n0 = len(core_calls)
        result = orig_arrays(*args, **kwargs)
        n1 = len(core_calls)
        if n1 != n0 + 1:
            raise ExtractorError(
                f"expected exactly one _simulate_forex_core call per simulate_forex_trades_arrays "
                f"call (pair={pair}), got {n1 - n0}"
            )
        bound = sig_arrays.bind(*args, **kwargs)
        bound.apply_defaults()
        arrays_calls.append({
            "pair": pair, "outer": dict(bound.arguments), "core": core_calls[-1], "trades": result,
        })
        return result

    fbk.simulate_forex_trades_arrays = wrapped_arrays

    orig_sim_trades = fb.ForexBacktester._simulate_trades

    def wrapped_sim_trades(self, df, signals, pair=None, trailing_mult=None):
        current_pair["pair"] = pair
        try:
            return orig_sim_trades(self, df, signals, pair=pair, trailing_mult=trailing_mult)
        finally:
            current_pair["pair"] = None

    fb.ForexBacktester._simulate_trades = wrapped_sim_trades

    trades = rig.get_trades(START, END)
    return trades, arrays_calls


# ──────────────────────────────────── driver ─────────────────────────────────────── #

def main(argv=None) -> int:
    t_start = time.time()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--h-max", type=int, required=True, dest="h_max",
                         help="path horizon in decision bars (spec 045: 10, no default)")
    args = parser.parse_args(argv)
    h_max = args.h_max
    if h_max < 1:
        raise ExtractorError(f"--h-max must be >= 1, got {h_max}")

    csv_df = pd.read_csv(CSV_PATH)
    if len(csv_df) != EXPECTED_N:
        raise ExtractorError(f"ground-truth CSV has n={len(csv_df)} trades, expected {EXPECTED_N}")
    sum_r = float((csv_df["pnl_pct"] / csv_df["risk_pct"]).sum())
    if round(sum_r, 2) != EXPECTED_SUM_R:
        raise ExtractorError(f"ground-truth CSV round(sum R,2)={round(sum_r, 2)} != {EXPECTED_SUM_R}")
    csv_df["entry_date"] = pd.to_datetime(csv_df["entry_date"])
    csv_df["exit_date"] = pd.to_datetime(csv_df["exit_date"])

    captured_trades, arrays_calls = _run_arm_capture()
    if len(captured_trades) != EXPECTED_N:
        raise ExtractorError(f"captured rig run produced n={len(captured_trades)} trades, expected {EXPECTED_N}")

    id_to_ref = {}
    for ci, c in enumerate(arrays_calls):
        for j, tr in enumerate(c["trades"]):
            id_to_ref[id(tr)] = (ci, j)

    flat = []
    for tr in captured_trades:
        ci, j = id_to_ref[id(tr)]
        flat.append({
            "pair": tr["pair"], "call_idx": ci, "j": j,
            "entry_date": pd.Timestamp(tr["entry_date"]), "exit_date": pd.Timestamp(tr["exit_date"]),
            "direction": int(tr["direction"]),
        })
    index_map: dict = {}
    for f in flat:
        key = (f["pair"], f["entry_date"], f["exit_date"], f["direction"])
        index_map.setdefault(key, []).append(f)

    zero, multi, matched_refs = [], [], []
    for row in csv_df.itertuples():
        key = (row.pair, row.entry_date, row.exit_date, int(row.direction))
        refs = index_map.get(key, [])
        if len(refs) == 0:
            zero.append(key)
        elif len(refs) > 1:
            multi.append(key)
        else:
            matched_refs.append((row.Index, refs[0]))
    if zero or multi:
        raise ExtractorError(
            f"trade matching failed: {len(zero)} unmatched, {len(multi)} duplicated "
            f"(examples unmatched={zero[:3]} duplicated={multi[:3]})"
        )

    order = sorted(
        matched_refs,
        key=lambda p: (csv_df.loc[p[0], "entry_date"], csv_df.loc[p[0], "pair"]),
    )

    from sovereign.forex.fill_model import BaseFill  # noqa: E402  (deferred: see module docstring)
    base_fill = BaseFill()

    weekend_cache: dict = {}
    trade_records = []
    path_rows = []
    absorbed_counts = Counter()
    hmax_inherit = 0
    hmax_cap = 0
    max_cost_err = 0.0
    n_parity_checked = 0

    for trade_id, (csv_idx, ref) in enumerate(order):
        csv_row = csv_df.loc[csv_idx]
        c = arrays_calls[ref["call_idx"]]
        j = ref["j"]
        core_kwargs = c["core"]["kwargs"]
        entry_idx, exit_idx, directions, pnls, holds, reasons, _units = c["core"]["result"]
        closes = core_kwargs["closes"]
        opens = core_kwargs["opens"]
        signals = core_kwargs["signals"]
        hold_days_arr = core_kwargs["hold_days"]
        atr_pcts = core_kwargs["atr_pcts"]
        stop_atr_mult = float(core_kwargs["stop_atr_mult"])
        trailing_atr_mult = float(core_kwargs["trailing_atr_mult"])
        strict_mode = bool(core_kwargs["strict_mode"])
        enable_cb_refresh = bool(core_kwargs["enable_cb_refresh"])
        index = c["outer"]["index"]

        pair = ref["pair"]
        e_bar = int(entry_idx[j])
        x_bar = int(exit_idx[j])
        direction = int(directions[j])
        gross = float(pnls[j])
        incumbent_hold = int(holds[j])
        reason_code = int(reasons[j])
        entry_price = float(opens[e_bar])

        if str(csv_row["exit_reason"]) != CSV_REASON_STR.get(reason_code):
            raise ExtractorError(
                f"trade {trade_id} ({pair} {csv_row['entry_date']}): exit_reason mismatch "
                f"captured={CSV_REASON_STR.get(reason_code)!r} csv={csv_row['exit_reason']!r}"
            )
        if incumbent_hold != int(csv_row["hold_days"]):
            raise ExtractorError(
                f"trade {trade_id}: hold_days mismatch captured={incumbent_hold} csv={csv_row['hold_days']}"
            )
        if x_bar != e_bar + incumbent_hold - 1:
            raise ExtractorError(f"trade {trade_id}: exit bar / hold_count inconsistency")

        stop_price, risk_dist = compute_stop_price(
            entry_price=entry_price, direction=direction, atr_pcts=atr_pcts,
            entry_bar=e_bar, stop_atr_mult=stop_atr_mult,
        )
        hold_limit = max(int(hold_days_arr[e_bar - 1]), 1)

        spread_frac, swap_frac = base_fill.cost_fracs(
            pair=pair, entry_price=entry_price, direction=direction,
            hold_bars=int(csv_row["hold_days"]), entry_date=csv_row["entry_date"],
        )
        net_reconstructed = gross - spread_frac + swap_frac
        cost_err = abs(net_reconstructed - float(csv_row["pnl_pct"]))
        max_cost_err = max(max_cost_err, cost_err)
        if cost_err > 1e-12:
            raise ExtractorError(
                f"trade {trade_id}: cost reconstruction mismatch |{net_reconstructed}-"
                f"{csv_row['pnl_pct']}|={cost_err} > 1e-12"
            )
        cost_frac = spread_frac - swap_frac
        risk_pct = float(csv_row["risk_pct"])
        incumbent_r_net = float(csv_row["pnl_pct"]) / risk_pct

        # G7 parity: unbounded (well, to incumbent_hold) replay reproduces the
        # incumbent's own real exit bar and reason, for every one of the 350.
        parity_rows = replay_decisions(
            closes=closes, atr_pcts=atr_pcts, signals=signals, hold_days_arr=hold_days_arr,
            entry_bar=e_bar, direction=direction, entry_price=entry_price, stop_price=stop_price,
            hold_limit=hold_limit, stop_atr_mult=stop_atr_mult, trailing_atr_mult=trailing_atr_mult,
            strict_mode=strict_mode, enable_cb_refresh=enable_cb_refresh, max_t=incumbent_hold,
        )
        for (t, _bar, decision) in parity_rows[:-1]:
            if decision != HOLD:
                raise ExtractorError(f"trade {trade_id}: parity failure -- early non-HOLD decision at t={t}")
        last_t, last_bar, last_decision = parity_rows[-1]
        if last_bar != x_bar or last_decision != reason_code:
            raise ExtractorError(
                f"trade {trade_id}: parity failure at incumbent exit -- "
                f"replay bar={last_bar} decision={last_decision}, incumbent bar={x_bar} reason={reason_code}"
            )
        n_parity_checked += 1

        if pair not in weekend_cache:
            weekend_cache[pair] = weekend_next_flags(index)
        weekend_flags = weekend_cache[pair]

        emitted, absorbed_by, absorbing_t, path_end_bar, terminal_r = build_trade_path(
            closes=closes, opens=opens, atr_pcts=atr_pcts, signals=signals, hold_days_arr=hold_days_arr,
            entry_bar=e_bar, direction=direction, entry_price=entry_price, stop_price=stop_price,
            risk_dist=risk_dist, hold_limit=hold_limit, stop_atr_mult=stop_atr_mult,
            trailing_atr_mult=trailing_atr_mult, strict_mode=strict_mode,
            enable_cb_refresh=enable_cb_refresh, h_max=h_max, incumbent_hold=incumbent_hold,
            incumbent_r_net=incumbent_r_net, cost_frac=cost_frac, risk_pct=risk_pct,
            index=index, weekend_flags=weekend_flags,
        )
        absorbed_counts[absorbed_by] += 1
        if absorbed_by == "HMAX":
            if incumbent_hold > h_max:
                hmax_inherit += 1
            else:
                hmax_cap += 1

        if incumbent_hold <= h_max:
            inc_row = next(r for r in emitted if r["t"] == incumbent_hold)
            err = abs(inc_row["unrealized_r_net"] - incumbent_r_net)
            if err > 1e-9:
                raise ExtractorError(
                    f"trade {trade_id}: unrealized_r_net at incumbent exit bar != incumbent_r_net "
                    f"(|{inc_row['unrealized_r_net']}-{incumbent_r_net}|={err} > 1e-9)"
                )

        entry_date_str = csv_row["entry_date"].strftime("%Y-%m-%d")
        for r in emitted:
            path_rows.append({
                "trade_id": trade_id, "pair": pair, "direction": direction, **r,
            })

        trade_records.append({
            "trade_id": trade_id, "pair": pair, "direction": direction,
            "entry_bar": e_bar, "entry_date": entry_date_str, "entry_price": entry_price,
            "stop_price": stop_price, "risk_dist": risk_dist, "hold_limit": hold_limit,
            "trailing_mult": trailing_atr_mult, "stop_atr_mult": stop_atr_mult,
            "incumbent_exit_bar": x_bar, "incumbent_hold": incumbent_hold,
            "incumbent_reason": str(csv_row["exit_reason"]), "incumbent_r_gross": gross,
            "cost_frac": cost_frac, "cost_spread_frac": spread_frac, "cost_swap_frac": swap_frac,
            "incumbent_r_net": incumbent_r_net, "risk_pct": risk_pct,
            "path_end_bar": path_end_bar, "path_end_reason": absorbed_by,
            "unit_id": None, "entry_cluster": entry_date_str,
            "_entry_ts": csv_row["entry_date"], "_path_end_ts": pd.Timestamp(index[path_end_bar]),
            "_incumbent_exit_ts": pd.Timestamp(index[x_bar]),
        })

    if n_parity_checked != EXPECTED_N:
        raise ExtractorError(f"parity checked {n_parity_checked} trades, expected {EXPECTED_N}")

    # ── units: connected components of [entry, path_end] across all 4 pairs ── #
    intervals = [(r["trade_id"], r["_entry_ts"], r["_path_end_ts"]) for r in trade_records]
    units = connected_components(intervals)
    trade_to_unit = {tid: uid for uid, tids in units.items() for tid in tids}
    for r in trade_records:
        r["unit_id"] = trade_to_unit[r["trade_id"]]

    ref_intervals = [(r["trade_id"], r["_entry_ts"], r["_incumbent_exit_ts"]) for r in trade_records]
    ref_units = connected_components(ref_intervals)

    entry_clusters: dict = {}
    for r in trade_records:
        entry_clusters.setdefault(r["entry_cluster"], []).append(r["trade_id"])
    for k in entry_clusters:
        entry_clusters[k].sort()

    # ── write artifacts ── #
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    trades_df = pd.DataFrame(trade_records)[TRADE_COLUMNS]
    trades_df.to_parquet(TRADES_OUT, index=False)

    paths_df = pd.DataFrame(path_rows)[PATH_COLUMNS]
    paths_df.to_parquet(PATHS_OUT, index=False)

    # exactly one absorbing row per trade, checked at the artifact level too
    absorbing_counts = paths_df[paths_df["absorbed_by"].notna()].groupby("trade_id").size()
    if len(absorbing_counts) != EXPECTED_N or (absorbing_counts != 1).any():
        bad = absorbing_counts[absorbing_counts != 1]
        raise ExtractorError(f"paths without exactly one absorbing row: {dict(bad)}")

    units_payload = {
        "h_max": h_max,
        "n_units": len(units),
        "units": {str(uid): tids for uid, tids in units.items()},
        "entry_clusters": entry_clusters,
        "n_entry_clusters": len(entry_clusters),
    }
    with UNITS_OUT.open("w") as fh:
        json.dump(units_payload, fh, indent=2)
        fh.write("\n")

    from sovereign.forex import inventory  # noqa: E402  (deferred: see module docstring)
    hashes = inventory.record({
        TRADES_OUT: "carry_tablebase_paths.py Step 2 -- per-trade incumbent outcomes + candidate setup",
        PATHS_OUT: "carry_tablebase_paths.py Step 2 -- per (trade, decision bar) path rows, H_MAX capped",
        UNITS_OUT: "carry_tablebase_paths.py Step 2 -- SPRT units (interval overlap components)",
    })

    runtime = time.time() - t_start
    print(f"n_trades: {EXPECTED_N}  sum_R: {round(sum_r, 4)} (round2={round(sum_r, 2)})")
    print(f"n_units (path_end intervals): {len(units)}  n_units (incumbent_exit intervals, reference): {len(ref_units)}")
    print(f"n_entry_clusters: {len(entry_clusters)}")
    print(f"parity: {n_parity_checked}/{EXPECTED_N} passed")
    print(f"cost reconstruction max abs error: {max_cost_err:.3e}")
    print(f"absorbed_by counts: {dict(absorbed_counts)}")
    print(f"HMAX inherit (incumbent still in): {hmax_inherit}  HMAX cap (incumbent already out): {hmax_cap}")
    for rel, sha in sorted(hashes.items()):
        print(f"hash {rel}: {sha}")
    print(f"runtime: {runtime:.2f}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExtractorError as e:
        print(f"HALT: {e}", file=sys.stderr)
        raise SystemExit(2)
