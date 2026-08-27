#!/usr/bin/env python3
"""Spec 039 — does the v015 carry edge survive publication-date-correct rates?

Runs the SAME offline rig (scripts/oos_campaign_test.get_trades — the same
ForexBacktester, the same monkeypatched spot cache) three times, changing ONE
thing: which vintage of the rate/CPI inputs the macro layer is allowed to see.

  sealed       the legacy data/cache/macro tree (2019-truncated) — continuity
               with data/agent/repro_gap_report.json only.
  nominal      full-history rates at their NOMINAL observation date. The sealed
               v015 generator's information set, look-ahead included. CONTROL ARM.
  publication  the same values at the date they were first published. TREATMENT.

No threshold, gate, weight or exit rule is touched. `git diff` on
sovereign/forex/{signal_engine,forex_backtester}.py must show no strategy change.

Every reported number is printed beside a zero-edge control (spec 021 P5): the
identical trade stream with each trade's R replaced by R minus the arm's own
mean R, so expectancy is exactly zero and only the shape survives.

Writes data/vintage_ab/{mode}_trades.csv and data/vintage_ab/summary.json.
Never writes anything under data/proof/.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT_DIR = ROOT / "data" / "vintage_ab"
SEALED_CSV = ROOT / "data" / "proof" / "backtest_trades_v015_2015_2024.csv"
START, END = "2015-01-01", "2024-12-31"


def _r(trades: list[dict]) -> list[float]:
    """Per-trade R using that trade's OWN risk_pct — a constant divisor is wrong."""
    return [t["pnl_pct"] / t["risk_pct"] for t in trades]


def portfolio_sharpe(dates, r_values) -> float:
    """Monthly-aggregated Sharpe on the pooled 4-pair R stream.

    One convention applied identically to every arm and every control, so the
    DELTA is meaningful even where the level is not directly comparable to the
    manifest's per-pair 1.2504.
    """
    s = pd.Series(r_values, index=pd.to_datetime(dates)).sort_index()
    monthly = s.resample("ME").sum()
    if len(monthly) < 2 or monthly.std() == 0:
        return 0.0
    return float(monthly.mean() / monthly.std() * np.sqrt(12))


def summarize(trades: list[dict], label: str) -> dict:
    r = _r(trades)
    exits = [t["exit_date"] for t in trades]
    holds = [t["hold_days"] for t in trades]
    ctrl = [x - statistics.mean(r) for x in r] if r else []
    return {
        "label": label,
        "n": len(r),
        "avg_r": round(statistics.mean(r), 4) if r else None,
        "win_rate": round(sum(1 for x in r if x > 0) / len(r), 4) if r else None,
        "median_hold_days": statistics.median(holds) if holds else None,
        "sharpe": round(portfolio_sharpe(exits, r), 4) if r else None,
        "worst_r": round(min(r), 3) if r else None,
        "total_r": round(sum(r), 2) if r else None,
        "control": {
            "avg_r": round(statistics.mean(ctrl), 6) if ctrl else None,
            "win_rate": round(sum(1 for x in ctrl if x > 0) / len(ctrl), 4) if ctrl else None,
            "sharpe": round(portfolio_sharpe(exits, ctrl), 4) if ctrl else None,
            "total_r": round(sum(ctrl), 6) if ctrl else None,
        },
    }


def sealed_trades() -> list[dict]:
    df = pd.read_csv(SEALED_CSV)
    return [
        {
            "pair": row.pair,
            "entry_date": pd.Timestamp(row.entry_date),
            "exit_date": pd.Timestamp(row.exit_date),
            "pnl_pct": float(row.pnl_pct),
            "risk_pct": float(row.risk_pct),
            "hold_days": int(row.hold_days),
        }
        for row in df.itertuples()
    ]


def run_mode(mode: str) -> tuple[list[dict], list[dict]]:
    os.environ["CARRY_RATE_VINTAGE"] = mode
    for m in list(sys.modules):
        if m.startswith("sovereign.forex") or m == "oos_campaign_test":
            del sys.modules[m]
    import oos_campaign_test as rig  # noqa: E402  (re-imported per mode on purpose)
    trades = rig.get_trades(START, END)
    from sovereign.forex import signal_engine  # noqa: E402
    exclusions = getattr(signal_engine, "_LAST_RUN_EXCLUSIONS", [])
    for t in trades:
        t.setdefault("risk_pct", 0.0075)
    return trades, exclusions


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    modes = sys.argv[1:] or ["sealed", "nominal", "publication"]
    summary = {"sealed_csv": summarize(sealed_trades(), "SEALED CSV (411, pinned)")}

    for mode in modes:
        trades, exclusions = run_mode(mode)
        pd.DataFrame([
            {
                "pair": t["pair"], "entry_date": t["entry_date"], "exit_date": t["exit_date"],
                "direction": t.get("direction"), "pnl_pct": t["pnl_pct"],
                "risk_pct": t["risk_pct"], "hold_days": t["hold_days"],
                "exit_reason": t.get("exit_reason"),
            }
            for t in trades
        ]).to_csv(OUT_DIR / f"{mode}_trades.csv", index=False)
        s = summarize(trades, f"RIG {mode}")
        s["vintage_exclusions"] = len(exclusions)
        s["vintage_exclusion_sample"] = exclusions[:10]
        summary[mode] = s
        print(json.dumps(s, indent=2, default=str))

    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nwrote {OUT_DIR/'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
