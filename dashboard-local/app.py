#!/usr/bin/env python3
"""Standalone local monitoring dashboard for passing-funded-account-1-.

Self-contained: stdlib + Flask only. Never imports repo modules (sovereign.*,
scripts.*, daytrade.*) — parses the repo's data files directly with csv/json.

Run:  python dashboard-local/app.py
Then: open http://127.0.0.1:5050
"""
from __future__ import annotations

import csv
import json
import math
import re
import statistics
from datetime import datetime, date
from pathlib import Path

from flask import Flask, jsonify, render_template

# Resolve repo root from this file's location — never hardcode a path.
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

BACKTEST_CSV = DATA_DIR / "proof" / "backtest_trades_v015_2015_2024.csv"
PAPER_JSONL = DATA_DIR / "trade_logs" / "paper_carry_trades.jsonl"
REPLAY_JSONL = DATA_DIR / "trade_logs" / "carry_replay_2024-12-03_2026-08-26.jsonl"
FX_STATE_JSONL = DATA_DIR / "carry" / "fx_state.jsonl"
FIRM_CONTRACTS_YAML = DATA_DIR / "propfirm" / "firm_contracts.yaml"

# TSMOM lane — pre-registered trend-following strategy, see sovereign/trend/SPEC.md
TSMOM_SUMMARY_JSON = {
    "primary": DATA_DIR / "trend" / "tsmom_primary_summary.json",
    "secondary": DATA_DIR / "trend" / "tsmom_secondary_summary.json",
}
TSMOM_EQUITY_CSV = {
    "primary": DATA_DIR / "trend" / "tsmom_primary_equity.csv",
    "secondary": DATA_DIR / "trend" / "tsmom_secondary_equity.csv",
}
TSMOM_TRADES_CSV = {
    "primary": DATA_DIR / "trend" / "tsmom_primary_trades.csv",
    "secondary": DATA_DIR / "trend" / "tsmom_secondary_trades.csv",
}
TSMOM_PAPER_JSONL = DATA_DIR / "trade_logs" / "paper_tsmom_trades.jsonl"

TSMOM_BANNER_TEXT = (
    "Pre-registered rule (Moskowitz-Ooi-Pedersen 2012), zero free parameters, "
    "spec hash 13cf5c86…. Backtest 2017-08-22 → 2026-08-26. Primary = the "
    "4 FX pairs the firm contract can trade; secondary = informational "
    "diversification check, not tradeable on the FX prop account."
)

# Replay window facts, surfaced verbatim in the UI banner. Not derived from the
# data — these are the documented conditions of this specific replay run.
REPLAY_WINDOW_START = "2024-12-03"
REPLAY_WINDOW_END = "2026-08-26"
REPLAY_SEALED_CUTOFF = "2024-12-09"

# Fallback used ONLY if firm_contracts.yaml can't be read/parsed for the scalar
# below. Source of truth is data/propfirm/firm_contracts.yaml ->
# cti_1step.costs.swap_haircut_r_per_day (kept in sync manually; this constant
# is a last-resort default, not the primary source).
SWAP_FALLBACK_CONSTANT = 0.004

app = Flask(__name__)


def _strip_pair(pair: str) -> str:
    """EURUSD=X -> EURUSD"""
    return pair.replace("=X", "") if pair else pair


def _parse_date(s: str | None):
    """Parse a date-ish string (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS or ISO) into a date."""
    if not s:
        return None
    s = s.strip()
    try:
        # Handle trailing 'Z' or timezone offsets that fromisoformat may reject on
        # older parsers; strip a trailing Z defensively.
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        return datetime.fromisoformat(s2).date()
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    return None


def load_backtest_trades():
    """Load the 411 sealed backtest closed trades. Returns [] if file missing/empty."""
    trades = []
    if not BACKTEST_CSV.exists():
        return trades
    with BACKTEST_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                direction_raw = row["direction"].strip()
                direction = "LONG" if direction_raw == "1" else "SHORT" if direction_raw == "-1" else None
                risk_pct = float(row["risk_pct"]) if row.get("risk_pct") not in (None, "") else None
                pnl_pct = float(row["pnl_pct"]) if row.get("pnl_pct") not in (None, "") else None
                risk_adjusted_pnl_pct = (
                    float(row["risk_adjusted_pnl_pct"])
                    if row.get("risk_adjusted_pnl_pct") not in (None, "")
                    else None
                )
                r_multiple = (
                    (pnl_pct / risk_pct) if (risk_pct not in (None, 0) and pnl_pct is not None) else None
                )
                trades.append(
                    {
                        "source": "backtest",
                        "pair": _strip_pair(row["pair"]),
                        "direction": direction,
                        "entry": float(row["entry"]) if row.get("entry") not in (None, "") else None,
                        "exit": float(row["exit"]) if row.get("exit") not in (None, "") else None,
                        "entry_date": row.get("entry_date"),
                        "exit_date": row.get("exit_date"),
                        "entry_date_parsed": _parse_date(row.get("entry_date")),
                        "exit_date_parsed": _parse_date(row.get("exit_date")),
                        "pnl_pct": pnl_pct,
                        "risk_pct": risk_pct,
                        "risk_adjusted_pnl_pct": risk_adjusted_pnl_pct,
                        "r_multiple": r_multiple,
                        "hold_days": int(row["hold_days"]) if row.get("hold_days") not in (None, "") else None,
                        "exit_reason": row.get("exit_reason"),
                        "units": row.get("units"),
                    }
                )
            except (ValueError, KeyError):
                # Malformed CSV row — skip rather than crash. Sealed data should
                # never have these, but don't fabricate a row if it does.
                continue
    return trades


def load_paper_trades():
    """Load paper trades from the (currently empty) jsonl ledger.

    Returns (open_trades, closed_trades, parse_errors). Handles: missing file,
    empty file, blank lines, malformed JSON lines (skipped + counted).
    """
    open_trades, closed_trades = [], []
    parse_errors = 0
    if not PAPER_JSONL.exists():
        return open_trades, closed_trades, parse_errors
    text = PAPER_JSONL.read_text()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        try:
            direction_raw = str(rec.get("direction", "")).strip().upper()
            direction = direction_raw if direction_raw in ("LONG", "SHORT") else None
            rec_out = {
                "source": "paper",
                "id": rec.get("id"),
                "status": rec.get("status"),
                "pair": _strip_pair(rec.get("pair", "")),
                "direction": direction,
                "entry": rec.get("entry"),
                "stop": rec.get("stop"),
                "risk_pct": rec.get("risk_pct"),
                "qty": rec.get("qty"),
                "entry_date": rec.get("entry_date"),
                "entry_date_parsed": _parse_date(rec.get("entry_date")),
                "exit": rec.get("exit"),
                "exit_date": rec.get("exit_date"),
                "exit_date_parsed": _parse_date(rec.get("exit_date")),
                "hold_days": rec.get("hold_days"),
                "R": rec.get("R"),
                "exit_reason": rec.get("exit_reason"),
            }
        except (AttributeError, TypeError):
            parse_errors += 1
            continue
        if rec_out["status"] == "open":
            open_trades.append(rec_out)
        elif rec_out["status"] == "closed":
            closed_trades.append(rec_out)
        else:
            # Unknown/missing status — don't silently drop, but don't guess
            # which bucket it belongs in either. Count as a parse error.
            parse_errors += 1
    return open_trades, closed_trades, parse_errors


def _most_stale_entry(staleness):
    """Given a {'{COUNTRY}_{rates|cpi}': int|None, ...} dict, return
    (key, value) for the entry with the largest magnitude (most stale),
    ignoring nulls. Sign is preserved as-is — negative means the rate cache
    extends past the trade date, which is meaningful and must not be hidden.
    Returns (None, None) if the dict is missing/empty or all-null."""
    if not isinstance(staleness, dict) or not staleness:
        return None, None
    best_key, best_val = None, None
    for k, v in staleness.items():
        if v is None:
            continue
        if best_val is None or abs(v) > abs(best_val):
            best_key, best_val = k, v
    return best_key, best_val


def load_replay_trades():
    """Load the live-writing replay ledger (out-of-sample, CB-layer-disabled).

    This file is actively appended to by a running process, so every call:
      - re-reads the file from disk fresh (no caching across requests), and
      - tolerates a torn/partial final JSON line (counts it as a parse error,
        never crashes).

    Returns (open_trades, closed_trades, parse_errors, exists, is_empty).
    """
    open_trades, closed_trades = [], []
    parse_errors = 0
    if not REPLAY_JSONL.exists():
        return open_trades, closed_trades, parse_errors, False, True
    try:
        text = REPLAY_JSONL.read_text()
    except OSError:
        return open_trades, closed_trades, parse_errors, True, True
    is_empty = len(text.strip()) == 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # Covers both genuinely malformed rows and a torn trailing line
            # from the writer process still mid-append.
            parse_errors += 1
            continue
        try:
            direction_raw = str(rec.get("direction", "")).strip().upper()
            direction = direction_raw if direction_raw in ("LONG", "SHORT") else None
            staleness = rec.get("rate_staleness_days")
            stale_key, stale_val = _most_stale_entry(staleness)
            rec_out = {
                "source": "replay",
                "id": rec.get("id"),
                "status": rec.get("status"),
                "pair": _strip_pair(rec.get("pair", "")),
                "direction": direction,
                "entry": rec.get("entry"),
                "stop": rec.get("stop"),
                "risk_pct": rec.get("risk_pct"),
                "qty": rec.get("qty"),
                "entry_date": rec.get("entry_date"),
                "entry_date_parsed": _parse_date(rec.get("entry_date")),
                "exit": rec.get("exit"),
                "exit_date": rec.get("exit_date"),
                "exit_date_parsed": _parse_date(rec.get("exit_date")),
                "hold_days": rec.get("hold_days"),
                "R": rec.get("R"),
                "exit_reason": rec.get("exit_reason"),
                "rate_staleness_days": staleness if isinstance(staleness, dict) else None,
                "most_stale_key": stale_key,
                "most_stale_days": stale_val,
            }
        except (AttributeError, TypeError):
            parse_errors += 1
            continue
        if rec_out["status"] == "open":
            open_trades.append(rec_out)
        elif rec_out["status"] == "closed":
            closed_trades.append(rec_out)
        else:
            parse_errors += 1
    return open_trades, closed_trades, parse_errors, True, is_empty


def compute_replay_header_stats(replay_closed_sorted):
    """Replay-only stats, reported SEPARATELY from the combined backtest+paper
    stats — never merged in, per spec. Uses R directly (not the compounded
    equity curve) for win rate / mean / total; drawdown is computed on the
    cumulative (non-compounded) sum of R, in R units."""
    r_values = [t.get("R") for t in replay_closed_sorted if t.get("R") is not None]
    trade_count = len(replay_closed_sorted)
    if not r_values:
        return {
            "replay_trade_count": trade_count,
            "replay_win_rate_pct": None,
            "replay_mean_r": None,
            "replay_total_r": None,
            "replay_max_drawdown_r": None,
        }
    wins = sum(1 for r in r_values if r > 0)
    win_rate_pct = wins / len(r_values) * 100.0
    mean_r = statistics.mean(r_values)
    total_r = sum(r_values)

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in replay_closed_sorted:
        r = t.get("R")
        if r is None:
            continue
        cum += r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "replay_trade_count": trade_count,
        "replay_win_rate_pct": win_rate_pct,
        "replay_mean_r": mean_r,
        "replay_total_r": total_r,
        "replay_max_drawdown_r": max_dd,
    }


def load_tsmom_summary(universe):
    """Load a tsmom_{universe}_summary.json. Returns None if missing/unparsable."""
    path = TSMOM_SUMMARY_JSON[universe]
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_tsmom_equity(universe):
    """Load a tsmom_{universe}_equity.csv into [{date, equity}, ...]. [] if missing."""
    path = TSMOM_EQUITY_CSV[universe]
    points = []
    if not path.exists():
        return points
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                points.append({"date": row["date"], "equity": float(row["equity"])})
            except (ValueError, KeyError):
                continue
    return points


def load_tsmom_backtest_trades(universe):
    """Load a tsmom_{universe}_trades.csv. Returns [] if file missing/empty."""
    path = TSMOM_TRADES_CSV[universe]
    trades = []
    if not path.exists():
        return trades
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                trades.append(
                    {
                        "source": "tsmom_backtest",
                        "universe": universe,
                        "pair": _strip_pair(row["instrument"]),
                        "direction": row.get("direction"),
                        "entry": float(row["entry"]) if row.get("entry") not in (None, "") else None,
                        "exit": float(row["exit"]) if row.get("exit") not in (None, "") else None,
                        "entry_date": row.get("entry_date"),
                        "exit_date": row.get("exit_date"),
                        "entry_date_parsed": _parse_date(row.get("entry_date")),
                        "exit_date_parsed": _parse_date(row.get("exit_date")),
                        "notional_frac": float(row["notional_frac"]) if row.get("notional_frac") not in (None, "") else None,
                        "pnl_frac": float(row["pnl_frac"]) if row.get("pnl_frac") not in (None, "") else None,
                        "R": float(row["R"]) if row.get("R") not in (None, "") else None,
                        "hold_days": int(row["hold_days"]) if row.get("hold_days") not in (None, "") else None,
                        "exit_reason": row.get("exit_reason"),
                        "cost_frac": float(row["cost_frac"]) if row.get("cost_frac") not in (None, "") else None,
                    }
                )
            except (ValueError, KeyError):
                # Malformed CSV row — skip rather than crash, never fabricate.
                continue
    return trades


def load_tsmom_paper_trades():
    """Load the live TSMOM paper ledger. Same schema-tolerance as
    load_paper_trades: missing/empty file, blank lines, torn/malformed JSON
    lines all handled without crashing. Schema differs from the carry paper
    ledger: notional_frac instead of qty, stop is null by design (no stop
    loss per SPEC.md — the signal flip is the exit), plus realized_vol.

    Returns (open_trades, closed_trades, parse_errors, exists, is_empty).
    """
    open_trades, closed_trades = [], []
    parse_errors = 0
    if not TSMOM_PAPER_JSONL.exists():
        return open_trades, closed_trades, parse_errors, False, True
    try:
        text = TSMOM_PAPER_JSONL.read_text()
    except OSError:
        return open_trades, closed_trades, parse_errors, True, True
    is_empty = len(text.strip()) == 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        try:
            direction_raw = str(rec.get("direction", "")).strip().upper()
            direction = direction_raw if direction_raw in ("LONG", "SHORT") else None
            rec_out = {
                "source": "tsmom",
                "id": rec.get("id"),
                "status": rec.get("status"),
                "pair": _strip_pair(rec.get("pair", "")),
                "direction": direction,
                "entry": rec.get("entry"),
                "stop": rec.get("stop"),
                "notional_frac": rec.get("notional_frac"),
                "realized_vol": rec.get("realized_vol"),
                "entry_date": rec.get("entry_date"),
                "entry_date_parsed": _parse_date(rec.get("entry_date")),
                "exit": rec.get("exit"),
                "exit_date": rec.get("exit_date"),
                "exit_date_parsed": _parse_date(rec.get("exit_date")),
                "hold_days": rec.get("hold_days"),
                "R": rec.get("R"),
                "exit_reason": rec.get("exit_reason"),
            }
        except (AttributeError, TypeError):
            parse_errors += 1
            continue
        if rec_out["status"] == "open":
            open_trades.append(rec_out)
        elif rec_out["status"] == "closed":
            closed_trades.append(rec_out)
        else:
            parse_errors += 1
    return open_trades, closed_trades, parse_errors, True, is_empty


def load_fx_state_latest():
    """Latest row per pair from data/carry/fx_state.jsonl. {} if missing/empty."""
    latest = {}
    if not FX_STATE_JSONL.exists():
        return latest
    for line in FX_STATE_JSONL.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        pair = rec.get("pair")
        as_of = rec.get("as_of")
        if not pair or not as_of:
            continue
        as_of_parsed = _parse_date(as_of)
        prev = latest.get(pair)
        if prev is None or (prev["as_of_parsed"] and as_of_parsed and as_of_parsed > prev["as_of_parsed"]):
            latest[pair] = {
                "close": rec.get("close"),
                "as_of": as_of,
                "as_of_parsed": as_of_parsed,
                "swap_accrual_r_per_day": rec.get("swap_accrual_r_per_day"),
            }
    return latest


def get_swap_fallback():
    """Read cti_1step.costs.swap_haircut_r_per_day from firm_contracts.yaml via
    regex (no YAML dependency added). Falls back to the module constant if the
    file or the scalar can't be found."""
    try:
        text = FIRM_CONTRACTS_YAML.read_text()
    except OSError:
        return SWAP_FALLBACK_CONSTANT, "module_constant_fallback (file unreadable)"
    # Isolate the cti_1step top-level block (from its header to the next
    # top-level key, i.e. a line that doesn't start with whitespace/#).
    block_match = re.search(r"^cti_1step:\n((?:[ \t].*\n?|\n)*)", text, re.MULTILINE)
    if not block_match:
        return SWAP_FALLBACK_CONSTANT, "module_constant_fallback (cti_1step block not found)"
    block = block_match.group(1)
    scalar_match = re.search(r"swap_haircut_r_per_day:\s*([0-9.]+)", block)
    if not scalar_match:
        return SWAP_FALLBACK_CONSTANT, "module_constant_fallback (scalar not found in block)"
    try:
        return float(scalar_match.group(1)), "firm_contracts.yaml:cti_1step.costs.swap_haircut_r_per_day"
    except ValueError:
        return SWAP_FALLBACK_CONSTANT, "module_constant_fallback (scalar unparsable)"


def build_equity_curve(closed_sorted):
    """Cumulative equity starting at 1.0, compounding a per-trade account-
    fraction return. Backtest trades use risk_adjusted_pnl_pct directly (per
    spec, this field IS the account-fraction return). Paper closed trades
    don't carry that exact field in their schema — as a documented fallback
    (paper ledger is currently empty so this path is untested against real
    data), we approximate the account-fraction return as R * risk_pct, the
    natural analogue of the backtest convention for records that only have
    R and risk_pct."""
    points = []
    equity = 1.0
    for t in closed_sorted:
        if t["source"] == "backtest":
            ret = t.get("risk_adjusted_pnl_pct")
        else:
            r = t.get("R")
            risk_pct = t.get("risk_pct")
            ret = (r * risk_pct) if (r is not None and risk_pct is not None) else None
        if ret is not None:
            equity *= (1.0 + ret)
        points.append(
            {
                "exit_date": t.get("exit_date"),
                "equity": equity,
                "source": t["source"],
            }
        )
    return points


def compute_drawdown_pct(equity_points):
    if not equity_points:
        return None
    peak = equity_points[0]["equity"]
    dd = 0.0
    for p in equity_points:
        peak = max(peak, p["equity"])
        if peak > 0:
            dd = max(dd, (peak - p["equity"]) / peak * 100.0)
    current_peak = max(p["equity"] for p in equity_points)
    current = equity_points[-1]["equity"]
    if current_peak <= 0:
        return None
    return (current_peak - current) / current_peak * 100.0


def compute_rolling_sharpe(closed_sorted, window=100):
    """Per-trade returns (risk_adjusted_pnl_pct for backtest, R*risk_pct
    analogue for paper), last `window` trades. n_per_year derived from the
    actual date span of that window (trades / years spanned), not assumed
    252. Returns (sharpe_or_null, meta) where meta documents window size and
    annualization factor used."""
    window_trades = closed_sorted[-window:] if len(closed_sorted) > window else closed_sorted[:]
    returns = []
    dates_used = []
    for t in window_trades:
        if t["source"] == "backtest":
            ret = t.get("risk_adjusted_pnl_pct")
        else:
            r = t.get("R")
            risk_pct = t.get("risk_pct")
            ret = (r * risk_pct) if (r is not None and risk_pct is not None) else None
        d = t.get("exit_date_parsed")
        if ret is not None and d is not None:
            returns.append(ret)
            dates_used.append(d)

    meta = {"window_size_requested": window, "trades_in_window": len(returns)}
    if len(returns) < 2:
        meta["n_per_year"] = None
        meta["annualization_note"] = "fewer than 2 trades with usable return+date in window"
        return None, meta

    stdev = statistics.stdev(returns)
    if stdev == 0:
        meta["n_per_year"] = None
        meta["annualization_note"] = "stdev of returns is 0"
        return None, meta

    span_days = (max(dates_used) - min(dates_used)).days
    years_spanned = span_days / 365.25 if span_days > 0 else None
    if not years_spanned or years_spanned <= 0:
        meta["n_per_year"] = None
        meta["annualization_note"] = "date span in window is 0 days"
        return None, meta

    n_per_year = len(returns) / years_spanned
    mean = statistics.mean(returns)
    sharpe = (mean / stdev) * math.sqrt(n_per_year)
    meta["n_per_year"] = round(n_per_year, 2)
    meta["annualization_note"] = (
        f"n_per_year derived from {len(returns)} trades spanning "
        f"{span_days} days ({round(years_spanned, 2)} years) in the trailing "
        f"{meta['trades_in_window']}-trade window"
    )
    return sharpe, meta


def r_value(trade):
    """Uniform R-multiple accessor across backtest ('r_multiple') and paper ('R')."""
    if trade["source"] == "backtest":
        return trade.get("r_multiple")
    return trade.get("R")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    backtest_trades = load_backtest_trades()
    paper_open, paper_closed, parse_errors = load_paper_trades()
    replay_open, replay_closed, replay_parse_errors, replay_exists, replay_is_empty = load_replay_trades()
    fx_latest = load_fx_state_latest()
    swap_fallback_value, swap_fallback_source = get_swap_fallback()

    # TSMOM lane — entirely separate strategy, own equity curves per universe,
    # own placebo-control stats. Never merged into the carry combined_closed/
    # equity_points/header_stats above.
    tsmom_open, tsmom_paper_closed, tsmom_parse_errors, tsmom_exists, tsmom_is_empty = load_tsmom_paper_trades()
    tsmom_universes = {}
    for universe in ("primary", "secondary"):
        summary = load_tsmom_summary(universe)
        equity_points_tsmom = load_tsmom_equity(universe)
        backtest_trades_tsmom = load_tsmom_backtest_trades(universe)
        backtest_trades_tsmom_sorted = sorted(
            backtest_trades_tsmom,
            key=lambda t: t.get("exit_date_parsed") or date.min,
            reverse=True,
        )
        placebo = (summary or {}).get("placebo") or {}
        real_percentile = placebo.get("real_percentile")
        tsmom_universes[universe] = {
            "windows": (summary or {}).get("windows"),
            "placebo": placebo,
            "full_window_sharpe": ((summary or {}).get("windows") or {}).get("Full window", {}).get("sharpe"),
            "clears_zero_edge_control": (real_percentile is not None and real_percentile >= 95),
            "equity_curve": equity_points_tsmom,
            "backtest_trade_count": len(backtest_trades_tsmom),
            "last_20_backtest_trades": [
                {
                    "pair": t["pair"],
                    "direction": t.get("direction"),
                    "entry": t.get("entry"),
                    "exit": t.get("exit"),
                    "entry_date": t.get("entry_date"),
                    "exit_date": t.get("exit_date"),
                    "notional_frac": t.get("notional_frac"),
                    "R": t.get("R"),
                    "hold_days": t.get("hold_days"),
                    "exit_reason": t.get("exit_reason"),
                }
                for t in backtest_trades_tsmom_sorted[:20]
            ],
        }

    combined_closed = backtest_trades + paper_closed
    combined_closed_sorted = sorted(
        combined_closed,
        key=lambda t: t.get("exit_date_parsed") or date.min,
    )

    equity_points = build_equity_curve(combined_closed_sorted)
    drawdown_pct = compute_drawdown_pct(equity_points)
    sharpe, sharpe_meta = compute_rolling_sharpe(combined_closed_sorted, window=100)

    # Replay lane: kept entirely separate — its own sort, its own equity
    # curve (own compounding, starting fresh at 1.0), its own header stats.
    # Never folded into combined_closed_sorted/equity_points/header_stats
    # above, per spec — this is an OOS run on a CB-layer-disabled engine and
    # must not be mistaken for the sealed in-sample record.
    replay_closed_sorted = sorted(
        replay_closed,
        key=lambda t: t.get("exit_date_parsed") or date.min,
    )
    replay_equity_points = build_equity_curve(replay_closed_sorted)
    replay_header_stats = compute_replay_header_stats(replay_closed_sorted)

    r_values = [r_value(t) for t in combined_closed_sorted]
    r_values_known = [r for r in r_values if r is not None]
    total_closed = len(combined_closed_sorted)
    wins = sum(1 for r in r_values_known if r > 0)
    win_rate_pct = (wins / len(r_values_known) * 100.0) if r_values_known else None

    header_stats = {
        "current_drawdown_pct": drawdown_pct,
        "rolling_sharpe": sharpe,
        "rolling_sharpe_meta": sharpe_meta,
        "win_rate_pct": win_rate_pct,
        "total_trades": total_closed,
        "backtest_trade_count": len(backtest_trades),
        "paper_closed_trade_count": len(paper_closed),
        "combined_note": "backtest + paper closed trades combined for all stats above",
    }

    # Open positions
    open_positions = []
    for pos in paper_open:
        pair = pos["pair"]
        pair_key = pair + "=X" if not pair.endswith("=X") else pair
        fx = fx_latest.get(pair_key) or fx_latest.get(pair)
        entry = pos.get("entry")
        stop = pos.get("stop")
        entry_date_parsed = pos.get("entry_date_parsed")
        today = date.today()
        days_held = (today - entry_date_parsed).days if entry_date_parsed else None

        unrealized_pnl_frac = None
        unrealized_r = None
        price_as_of = None
        price_age_days = None
        swap_source_used = None
        swap_rate = None

        if fx and fx.get("close") is not None and entry is not None and pos.get("direction"):
            close = fx["close"]
            sign = 1.0 if pos["direction"] == "LONG" else -1.0
            unrealized_pnl_frac = sign * (close - entry) / entry
            if stop is not None and entry:
                risk_frac = abs(entry - stop) / entry
                unrealized_r = (unrealized_pnl_frac / risk_frac) if risk_frac else None
            price_as_of = fx.get("as_of")
            if fx.get("as_of_parsed"):
                price_age_days = (today - fx["as_of_parsed"]).days

        if fx and fx.get("swap_accrual_r_per_day") is not None:
            swap_rate = fx["swap_accrual_r_per_day"]
            swap_source_used = "fx_state.jsonl"
        else:
            swap_rate = swap_fallback_value
            swap_source_used = swap_fallback_source

        swap_accrued_r = -abs(swap_rate * max(days_held, 1)) if (swap_rate is not None and days_held is not None) else None

        open_positions.append(
            {
                "pair": pair,
                "direction": pos.get("direction"),
                "entry": entry,
                "qty": pos.get("qty"),
                "unrealized_pnl_frac": unrealized_pnl_frac,
                "unrealized_r": unrealized_r,
                "price_as_of": price_as_of,
                "price_age_days": price_age_days,
                "days_held": days_held,
                "swap_accrued_r": swap_accrued_r,
                "swap_rate_source": swap_source_used,
            }
        )

    # TSMOM open paper positions — kept in a sibling table (schema differs:
    # notional_frac instead of qty, stop is null by design per SPEC.md — no
    # stop loss, the signal flip is the exit).
    tsmom_open_positions = [
        {
            "pair": pos["pair"],
            "direction": pos.get("direction"),
            "entry": pos.get("entry"),
            "notional_frac": pos.get("notional_frac"),
            "realized_vol": pos.get("realized_vol"),
            "stop": pos.get("stop"),
            "entry_date": pos.get("entry_date"),
        }
        for pos in tsmom_open
    ]

    # Last 20 closed trades, newest first. This listing merges backtest,
    # paper (carry), replay, and paper (tsmom) lanes for display purposes
    # only — the tsmom backtest CSV trades (396/877 rows) stay in their own
    # per-universe "last 20 backtest" sub-table, never merged here.
    all_closed_for_table = combined_closed_sorted + replay_closed_sorted + tsmom_paper_closed
    newest_first = sorted(
        all_closed_for_table,
        key=lambda t: t.get("exit_date_parsed") or date.min,
        reverse=True,
    )[:20]
    last_20 = [
        {
            "source": t["source"],
            "pair": t["pair"],
            "direction": t.get("direction"),
            "entry": t.get("entry"),
            "exit": t.get("exit"),
            "entry_date": t.get("entry_date"),
            "exit_date": t.get("exit_date"),
            "r_multiple": r_value(t),
            "hold_days": t.get("hold_days"),
            "exit_reason": t.get("exit_reason"),
            "most_stale_key": t.get("most_stale_key"),
            "most_stale_days": t.get("most_stale_days"),
        }
        for t in newest_first
    ]

    return jsonify(
        {
            "header_stats": header_stats,
            "replay_header_stats": replay_header_stats,
            "replay_banner": {
                "window_start": REPLAY_WINDOW_START,
                "window_end": REPLAY_WINDOW_END,
                "sealed_cutoff": REPLAY_SEALED_CUTOFF,
                "out_of_sample": True,
                "usdjpy_dark": True,
                "cb_surprise_layer_disabled": True,
                "note": (
                    f"Replay window {REPLAY_WINDOW_START} → {REPLAY_WINDOW_END} "
                    f"is OUT-OF-SAMPLE (the sealed set ends {REPLAY_SEALED_CUTOFF}). "
                    "USDJPY is dark for the entire window (no Japan CPI source "
                    "exists). The CB-surprise layer is DISABLED, so this does NOT "
                    "reproduce the sealed engine."
                ),
            },
            "equity_curve": equity_points,
            "replay_equity_curve": replay_equity_points,
            "open_positions": open_positions,
            "last_20_closed": last_20,
            "tsmom": {
                "banner": TSMOM_BANNER_TEXT,
                "universes": tsmom_universes,
                "open_positions": tsmom_open_positions,
                "paper_ledger": {
                    "path": "data/trade_logs/paper_tsmom_trades.jsonl",
                    "open_count": len(tsmom_open),
                    "closed_count": len(tsmom_paper_closed),
                    "parse_errors": tsmom_parse_errors,
                    "exists": tsmom_exists,
                    "is_empty": tsmom_is_empty,
                },
            },
            "paper_ledger": {
                "path": "data/trade_logs/paper_carry_trades.jsonl",
                "open_count": len(paper_open),
                "closed_count": len(paper_closed),
                "parse_errors": parse_errors,
                "is_empty": (not PAPER_JSONL.exists()) or PAPER_JSONL.stat().st_size == 0,
            },
            "replay_ledger": {
                "path": "data/trade_logs/carry_replay_2024-12-03_2026-08-26.jsonl",
                "open_count": len(replay_open),
                "closed_count": len(replay_closed),
                "parse_errors": replay_parse_errors,
                "exists": replay_exists,
                "is_empty": replay_is_empty,
            },
            "data_sources": {
                "backtest_csv": {
                    "path": "data/proof/backtest_trades_v015_2015_2024.csv",
                    "row_count": len(backtest_trades),
                },
                "paper_jsonl": {
                    "path": "data/trade_logs/paper_carry_trades.jsonl",
                    "open_count": len(paper_open),
                    "closed_count": len(paper_closed),
                    "parse_errors": parse_errors,
                },
                "replay_jsonl": {
                    "path": "data/trade_logs/carry_replay_2024-12-03_2026-08-26.jsonl",
                    "row_count": len(replay_open) + len(replay_closed),
                    "open_count": len(replay_open),
                    "closed_count": len(replay_closed),
                    "parse_errors": replay_parse_errors,
                    "exists": replay_exists,
                    "is_empty": replay_is_empty,
                },
                "fx_state_jsonl": {
                    "path": "data/carry/fx_state.jsonl",
                    "pair_count": len(fx_latest),
                    "pairs": sorted(fx_latest.keys()),
                },
                "swap_fallback": {
                    "value": swap_fallback_value,
                    "source": swap_fallback_source,
                },
                "tsmom": {
                    "primary_summary_json": {
                        "path": "data/trend/tsmom_primary_summary.json",
                        "exists": TSMOM_SUMMARY_JSON["primary"].exists(),
                    },
                    "secondary_summary_json": {
                        "path": "data/trend/tsmom_secondary_summary.json",
                        "exists": TSMOM_SUMMARY_JSON["secondary"].exists(),
                    },
                    "primary_backtest_csv": {
                        "path": "data/trend/tsmom_primary_trades.csv",
                        "row_count": tsmom_universes["primary"]["backtest_trade_count"],
                    },
                    "secondary_backtest_csv": {
                        "path": "data/trend/tsmom_secondary_trades.csv",
                        "row_count": tsmom_universes["secondary"]["backtest_trade_count"],
                    },
                    "paper_jsonl": {
                        "path": "data/trade_logs/paper_tsmom_trades.jsonl",
                        "open_count": len(tsmom_open),
                        "closed_count": len(tsmom_paper_closed),
                        "parse_errors": tsmom_parse_errors,
                        "exists": tsmom_exists,
                        "is_empty": tsmom_is_empty,
                    },
                },
            },
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
