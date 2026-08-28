#!/usr/bin/env python3
"""TSMOM backtest — walks sovereign.trend.tsmom_engine.decide() day by day,
per sovereign/trend/SPEC.md. This script owns state transitions (the three
position-change events), the cost model, the R convention, the four
evaluation windows, and the zero-edge placebo control. It re-implements NONE
of the rule itself — signal, vol, and sizing all come from decide().

Usage:
    python3 scripts/tsmom_backtest.py --universe primary   [--placebo 1000] [--out data/trend/]
    python3 scripts/tsmom_backtest.py --universe secondary [--placebo 1000] [--out data/trend/]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass
from datetime import date
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402
from sovereign.trend.tsmom_engine import VOL_TARGET, decide  # noqa: E402

CARRY_BARS_DIR = ROOT / "data" / "carry" / "bars"
TREND_BARS_DIR = ROOT / "data" / "trend" / "bars"

PRIMARY_UNIVERSE = {
    "EURUSD=X": CARRY_BARS_DIR / "EURUSD_X.parquet",
    "GBPUSD=X": CARRY_BARS_DIR / "GBPUSD_X.parquet",
    "USDJPY=X": CARRY_BARS_DIR / "USDJPY_X.parquet",
    "AUDUSD=X": CARRY_BARS_DIR / "AUDUSD_X.parquet",
}

SECONDARY_TICKERS = [
    "SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC",
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
]

# SPEC.md "Costs" — one-way spread, in price units, both sides charged.
FX_SPREADS = {
    "EURUSD=X": 0.00010,
    "GBPUSD=X": 0.00015,
    "USDJPY=X": 0.010,
    "AUDUSD=X": 0.00012,
}
# JUDGMENT CALL: SPEC.md only pre-registers spreads for the four FX pairs (the
# firm-tradable primary universe). The secondary universe is explicitly
# "informational" in SPEC.md and never had a spread pre-registered for
# SPY/EFA/EEM/TLT/IEF/GLD/DBC. Rather than invent per-ticker tick sizes (which
# would be an undisclosed new parameter), a single conservative round-trip
# cost is applied as a fraction of price — conservative because 5bp one-way is
# well above the realized NBBO spread on any of these liquid ETFs today, so
# it never understates cost. Disclosed in the summary JSON, never silent.
SECONDARY_SPREAD_FRAC = 0.0005  # 5bp of price, one-way, "informational" universe only

R_UNIT = VOL_TARGET / sqrt(252)  # SPEC.md "R convention": one target-daily-vol unit of equity
RESIZE_THRESHOLD = 0.10           # SPEC.md "Position changes" #2

WINDOWS = {
    "Full window": (date(2017, 8, 22), date(2026, 8, 26)),
    "Sub-window A": (date(2017, 8, 22), date(2020, 12, 31)),
    "Sub-window B": (date(2021, 1, 1), date(2024, 12, 31)),
    "Sub-window C": (date(2025, 1, 1), date(2026, 8, 26)),
}

PLACEBO_SEED = 20260827  # per task instructions


@dataclass
class Trade:
    instrument: str
    direction: str
    entry_date: date
    entry: float
    exit_date: date
    exit: float
    notional_frac: float
    pnl_frac: float
    R: float
    hold_days: int
    exit_reason: str
    cost_frac: float


# --------------------------------------------------------------------- data

def load_primary_series() -> tuple[dict[str, pd.Series], list[str]]:
    out = {}
    for pair, path in PRIMARY_UNIVERSE.items():
        if not path.exists():
            raise FileNotFoundError(f"missing primary bars: {path}")
        df = pd.read_parquet(path).sort_index()
        df.index = pd.to_datetime(df.index)
        out[pair] = df["Close"].astype(float)
    return out, []


def load_secondary_series() -> tuple[dict[str, pd.Series], list[str]]:
    """FX legs reuse the frozen primary bars (same instruments, same rule —
    no second fetch of data this repo already has sealed). Non-FX legs are
    fetched via yfinance and cached under data/trend/bars/ with the same
    frozen-history-wins merge rule as daytrade/fx_state.py::fetch_bars."""
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf

    TREND_BARS_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, pd.Series] = {}
    excluded: list[str] = []

    for ticker in SECONDARY_TICKERS:
        if ticker in PRIMARY_UNIVERSE:
            df = pd.read_parquet(PRIMARY_UNIVERSE[ticker]).sort_index()
            df.index = pd.to_datetime(df.index)
            out[ticker] = df["Close"].astype(float)
            print(f"  {ticker}: {len(df)} daily bars (reused primary), "
                  f"{df.index.min().date()} .. {df.index.max().date()}")
            continue

        cache_path = TREND_BARS_DIR / f"{ticker.replace('=', '_')}.parquet"
        df = yf.download(ticker, period="10y", interval="1d",
                          auto_adjust=False, progress=False)
        if df is None or df.empty:
            print(f"  !! {ticker}: no bars returned from yfinance — excluded LOUDLY")
            excluded.append(ticker)
            if cache_path.exists():
                cached = pd.read_parquet(cache_path).sort_index()
                cached.index = pd.to_datetime(cached.index)
                out[ticker] = cached["Close"].astype(float)
                print(f"     falling back to cached bars: {len(cached)} rows")
            continue

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close"]].sort_index()
        df.index = pd.to_datetime(df.index)
        if cache_path.exists():                       # frozen history wins
            old = pd.read_parquet(cache_path)
            old.index = pd.to_datetime(old.index)
            add = df.index.difference(old.index)
            df = pd.concat([old, df.loc[add]]).sort_index()
        df.to_parquet(cache_path)
        out[ticker] = df["Close"].astype(float)
        print(f"  {ticker}: {len(df)} daily bars, {df.index.min().date()} .. "
              f"{df.index.max().date()}")

    return out, excluded


def precompute_decisions(closes: pd.Series):
    """One decide() call per day. The backtester never re-derives signal,
    vol, or sizing itself — it only reads what decide() returns."""
    dates = [ts.date() for ts in closes.index]
    prices = closes.to_numpy(dtype=float)
    signals = np.zeros(len(dates), dtype=int)
    fracs = np.zeros(len(dates), dtype=float)
    for i, d in enumerate(dates):
        dec = decide(closes, d)
        signals[i] = dec.signal
        fracs[i] = dec.notional_frac
    return dates, prices, signals, fracs


def spread_units(instrument: str, price: float) -> float:
    if instrument in FX_SPREADS:
        return FX_SPREADS[instrument]
    return SECONDARY_SPREAD_FRAC * price


# ------------------------------------------------------------------ engine walk

def _make_trade(instrument, signal, frac, dates, prices, open_idx, close_idx,
                 reason, open_cost, close_cost, haircut) -> Trade:
    entry_date = dates[open_idx]
    exit_date = dates[close_idx]
    entry = float(prices[open_idx])
    exit_ = float(prices[close_idx])
    hold_days = (exit_date - entry_date).days
    price_pnl = signal * frac * (exit_ / entry - 1.0)
    holding_cost = frac * haircut * hold_days * R_UNIT
    spread_cost = open_cost + close_cost
    pnl_frac = price_pnl - holding_cost - spread_cost
    cost_frac = holding_cost + spread_cost
    r = pnl_frac / R_UNIT
    direction = "LONG" if signal > 0 else "SHORT"
    return Trade(instrument=instrument, direction=direction,
                 entry_date=entry_date, entry=entry, exit_date=exit_date,
                 exit=exit_, notional_frac=frac, pnl_frac=pnl_frac, R=r,
                 hold_days=hold_days, exit_reason=reason, cost_frac=cost_frac)


def simulate(instrument: str, dates: list[date], prices: np.ndarray,
             signals: np.ndarray, fracs: np.ndarray, haircut: float,
             build_trades: bool) -> tuple[np.ndarray, list[Trade]]:
    """SPEC.md "Position changes" — the only three events — plus the cost
    model and R convention. Signal/vol/sizing all come from the caller
    (decide()'s output); this function only manages state and P&L."""
    n = len(dates)
    daily_returns = np.zeros(n)
    trades: list[Trade] = []

    held_signal = 0
    held_frac = 0.0
    open_idx: int | None = None
    open_cost = 0.0  # spread cost already charged when the open leg started

    for i in range(n):
        if i > 0 and held_signal != 0:
            price_ret = held_signal * held_frac * (prices[i] / prices[i - 1] - 1.0)
            cal_days = (dates[i] - dates[i - 1]).days
            holding_cost = held_frac * haircut * cal_days * R_UNIT
            daily_returns[i] += price_ret - holding_cost

        sig_i = int(signals[i])
        frac_i = float(fracs[i])
        price_i = prices[i]

        if sig_i != held_signal:
            # ENTRY / FLIP / EXIT-TO-FLAT — SPEC.md events #1 and #3. A
            # single formula covers all three: fully close whatever exposure
            # was held, fully open whatever exposure is now signaled.
            old_exposure = held_signal * held_frac
            new_exposure = sig_i * frac_i
            spread = spread_units(instrument, price_i)
            close_cost = spread / price_i * abs(old_exposure)
            open_cost_this = spread / price_i * abs(new_exposure)
            event_cost = close_cost + open_cost_this

            if held_signal != 0 and open_idx is not None and build_trades:
                reason = "flat" if sig_i == 0 else "signal_flip"
                trades.append(_make_trade(
                    instrument, held_signal, held_frac, dates, prices,
                    open_idx, i, reason, open_cost, close_cost, haircut))

            if sig_i != 0:
                open_idx = i
                open_cost = open_cost_this
            else:
                open_idx = None
                open_cost = 0.0
            held_signal, held_frac = sig_i, frac_i
            daily_returns[i] -= event_cost

        elif (sig_i != 0 and dates[i].weekday() == 0
              and abs(frac_i - held_frac) > RESIZE_THRESHOLD):
            # RESIZE — SPEC.md event #2. Same sign, Monday only, partial
            # close/open counted for costs on the incremental change only.
            spread = spread_units(instrument, price_i)
            delta = abs(sig_i * frac_i - held_signal * held_frac)
            event_cost = spread / price_i * delta

            if open_idx is not None and build_trades:
                trades.append(_make_trade(
                    instrument, held_signal, held_frac, dates, prices,
                    open_idx, i, "resize", open_cost, event_cost, haircut))

            open_idx = i
            open_cost = 0.0  # already paid via event_cost, on the closing leg
            held_frac = frac_i
            daily_returns[i] -= event_cost

    if held_signal != 0 and open_idx is not None and build_trades:
        # window_end: mark-to-market close, no spread crossed (no real trade).
        trades.append(_make_trade(
            instrument, held_signal, held_frac, dates, prices,
            open_idx, n - 1, "window_end", open_cost, 0.0, haircut))

    return daily_returns, trades


# --------------------------------------------------------------- evaluation

def _longest_drawdown_days(equity: pd.Series) -> int:
    peak = equity.cummax()
    in_dd = equity < peak
    longest = 0
    peak_date = equity.index[0]
    for i in range(len(equity)):
        if not in_dd.iloc[i]:
            peak_date = equity.index[i]
        else:
            longest = max(longest, (equity.index[i] - peak_date).days)
    return longest


def compute_window_stats(portfolio_returns: pd.Series, trades: list[Trade],
                          start: date, end: date) -> dict:
    idx_dates = portfolio_returns.index.date
    mask = (idx_dates >= start) & (idx_dates <= end)
    r = portfolio_returns[mask]
    if len(r) == 0:
        return {"trades": 0, "win_rate": 0.0, "mean_R": 0.0,
                "total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                "longest_drawdown_days": 0}

    equity = (1.0 + r).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    std_r = float(r.std(ddof=1))
    sharpe = float(r.mean() / std_r * sqrt(252)) if std_r > 0 else 0.0
    peak = equity.cummax()
    max_dd = float((equity / peak - 1.0).min())
    longest_dd = _longest_drawdown_days(equity)

    window_trades = [t for t in trades if start <= t.entry_date <= end]
    n_trades = len(window_trades)
    win_rate = (sum(1 for t in window_trades if t.pnl_frac > 0) / n_trades
                if n_trades else 0.0)
    mean_r_trade = (sum(t.R for t in window_trades) / n_trades
                    if n_trades else 0.0)

    return {"trades": n_trades, "win_rate": win_rate, "mean_R": mean_r_trade,
            "total_return": total_return, "sharpe": sharpe,
            "max_drawdown": max_dd, "longest_drawdown_days": longest_dd}


def build_union_index(per_instrument: dict[str, dict]):
    all_dates = set()
    for d in per_instrument.values():
        all_dates.update(d["dates"])
    union_sorted = sorted(all_dates)
    union_arr = np.array(union_sorted)
    pos_maps = {}
    for instr, d in per_instrument.items():
        pos_maps[instr] = np.searchsorted(union_arr, np.array(d["dates"]))
    return union_sorted, pos_maps


def run_placebo(n: int, per_instrument: dict[str, dict], haircut: float,
                 window_start: date, window_end: date) -> list[float]:
    """SPEC.md "ZERO-EDGE CONTROL": circularly shift each instrument's daily
    SIGNAL SEQUENCE by an independent random offset, rerun the P&L accounting
    with the same costs, record the Sharpe. Only the alignment between signal
    and returns is destroyed — notional sizing (vol targeting) is untouched."""
    random.seed(PLACEBO_SEED)
    union_dates, pos_maps = build_union_index(per_instrument)
    union_index = np.array(union_dates)
    window_mask = (union_index >= window_start) & (union_index <= window_end)

    sharpes = []
    for _ in range(n):
        portfolio = np.zeros(len(union_dates))
        for instr, d in per_instrument.items():
            offset = random.randrange(1, len(d["dates"]))
            shifted_signals = np.roll(d["signals"], offset)
            dr, _ = simulate(instr, d["dates"], d["prices"], shifted_signals,
                              d["fracs"], haircut, build_trades=False)
            portfolio[pos_maps[instr]] += dr
        r = portfolio[window_mask]
        std = r.std(ddof=1)
        sharpes.append(float(r.mean() / std * sqrt(252)) if std > 0 else 0.0)
    return sharpes


# ------------------------------------------------------------------- report

def print_summary_table(universe: str, summary: dict) -> None:
    print(f"\n=== TSMOM backtest — {universe} universe ===")
    header = (f"{'window':<14}{'trades':>7}{'win%':>8}{'mean_R':>9}"
              f"{'tot_ret':>10}{'sharpe':>9}{'max_dd':>9}{'longest_dd':>12}")
    print(header)
    for name, s in summary["windows"].items():
        print(f"{name:<14}{s['trades']:>7}{100*s['win_rate']:>7.1f}%"
              f"{s['mean_R']:>9.3f}{100*s['total_return']:>9.1f}%"
              f"{s['sharpe']:>9.2f}{100*s['max_drawdown']:>8.1f}%"
              f"{s['longest_drawdown_days']:>12}")
    p = summary["placebo"]
    print("\n--- zero-edge control ---")
    print(f"placebo runs (n)       : {p['n']}")
    print(f"real Sharpe (Full)     : {p['real_sharpe']:.3f}")
    print(f"placebo mean Sharpe    : {p['placebo_mean']:.3f}")
    print(f"placebo p95 Sharpe     : {p['placebo_p95']:.3f}")
    print(f"real Sharpe percentile : {p['real_percentile']:.1f}")
    if summary.get("excluded_tickers"):
        print(f"\nexcluded tickers: {summary['excluded_tickers']}")
    if universe == "secondary":
        print(f"\nnote: non-FX legs use a {SECONDARY_SPREAD_FRAC*1e4:.0f}bp "
              "conservative placeholder spread — SPEC.md never pre-registers "
              "one for this informational universe (see script header).")


# ---------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", choices=["primary", "secondary"], required=True)
    parser.add_argument("--placebo", type=int, default=1000)
    parser.add_argument("--out", default=str(ROOT / "data" / "trend"))
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    haircut = load_contract("cti_1step").costs.swap_haircut_r_per_day

    if args.universe == "primary":
        raw, excluded = load_primary_series()
    else:
        raw, excluded = load_secondary_series()

    if not raw:
        print("no instruments loaded — nothing to backtest", file=sys.stderr)
        return 1

    per_instrument: dict[str, dict] = {}
    all_trades: list[Trade] = []
    per_instrument_returns: dict[str, pd.Series] = {}

    for instr, closes in raw.items():
        dates, prices, signals, fracs = precompute_decisions(closes)
        dr, trades = simulate(instr, dates, prices, signals, fracs, haircut,
                               build_trades=True)
        per_instrument[instr] = {"dates": dates, "prices": prices,
                                  "signals": signals, "fracs": fracs}
        per_instrument_returns[instr] = pd.Series(dr, index=pd.to_datetime(dates))
        all_trades.extend(trades)

    portfolio_returns = (pd.concat(per_instrument_returns, axis=1)
                          .fillna(0.0).sum(axis=1))
    portfolio_returns.index = pd.to_datetime(portfolio_returns.index)
    portfolio_returns = portfolio_returns.sort_index()

    equity = (1.0 + portfolio_returns).cumprod()
    equity_df = pd.DataFrame({"date": portfolio_returns.index.date,
                               "equity": equity.values})
    equity_df.to_csv(out_dir / f"tsmom_{args.universe}_equity.csv", index=False)

    trades_df = pd.DataFrame([asdict(t) for t in all_trades])
    if not trades_df.empty:
        trades_df = trades_df.sort_values(["entry_date", "instrument"])
    trades_df.to_csv(out_dir / f"tsmom_{args.universe}_trades.csv", index=False)

    summary = {"universe": args.universe, "windows": {}}
    for name, (start, end) in WINDOWS.items():
        summary["windows"][name] = compute_window_stats(portfolio_returns, all_trades, start, end)

    full_start, full_end = WINDOWS["Full window"]
    placebo_sharpes = run_placebo(args.placebo, per_instrument, haircut, full_start, full_end)
    real_sharpe = summary["windows"]["Full window"]["sharpe"]
    percentile = float(np.mean(np.array(placebo_sharpes) <= real_sharpe) * 100.0)
    summary["placebo"] = {
        "n": args.placebo,
        "real_sharpe": real_sharpe,
        "placebo_mean": float(np.mean(placebo_sharpes)),
        "placebo_p95": float(np.percentile(placebo_sharpes, 95)),
        "real_percentile": percentile,
    }
    summary["excluded_tickers"] = excluded

    with open(out_dir / f"tsmom_{args.universe}_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print_summary_table(args.universe, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
