#!/usr/bin/env python3
"""Spec 039 — the before/after table, with controls and error bars.

Reads data/vintage_ab/{nominal,publication,sealed}_trades.csv (written by
run_vintage_ab.py) and the sealed 411 CSV, and prints:

  * per-arm n / avg R / win rate / median hold / Sharpe
  * TWO controls beside every arm:
      - mean-centered (spec 021 P5's control). Degenerate for Sharpe by
        construction — a mean-centered stream has exactly zero mean, so its
        Sharpe is exactly 0. Printed anyway, because its being zero is the
        arithmetic check, not a result.
      - direction-permutation null: each trade's R multiplied by a random +/-1,
        2000 draws. This is the null that matters here — the macro layer's only
        output is an entry SIGN, so "no skill" means a coin-flip sign on the
        same trade population. 5-95% interval reported.
  * the paired comparison: trades present in BOTH arms, matched on
    (pair, entry date within +/-3 days), with a paired bootstrap on delta R.
    Paired is the powerful test; the unpaired arm-vs-arm difference is swamped
    by the per-trade sigma of ~1.7R.

No number here is quotable without the control printed next to it.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
AB = ROOT / "data" / "vintage_ab"
SEALED_CSV = ROOT / "data" / "proof" / "backtest_trades_v015_2015_2024.csv"
MATCH_TOL_DAYS = 3
RNG = np.random.default_rng(20260826)
DRAWS = 2000


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["R"] = df["pnl_pct"] / df["risk_pct"]
    return df


def sharpe(df: pd.DataFrame, col: str = "R") -> float:
    m = pd.Series(df[col].to_numpy(), index=df["exit_date"]).sort_index().resample("ME").sum()
    if len(m) < 2 or m.std() == 0:
        return 0.0
    return float(m.mean() / m.std() * np.sqrt(12))


def perm_null(df: pd.DataFrame) -> dict:
    r = df["R"].to_numpy()
    avgs, sharps = [], []
    exits = df["exit_date"]
    for _ in range(DRAWS):
        flipped = r * RNG.choice([-1.0, 1.0], size=len(r))
        avgs.append(flipped.mean())
        m = pd.Series(flipped, index=exits).sort_index().resample("ME").sum()
        sharps.append(0.0 if m.std() == 0 else m.mean() / m.std() * np.sqrt(12))
    q = lambda a, p: float(np.quantile(a, p))  # noqa: E731
    return {
        "avg_r_5_95": [round(q(avgs, .05), 4), round(q(avgs, .95), 4)],
        "sharpe_5_95": [round(q(sharps, .05), 4), round(q(sharps, .95), 4)],
        "p_avg_r_ge_observed": round(float(np.mean(np.array(avgs) >= df["R"].mean())), 4),
    }


def arm(df: pd.DataFrame, label: str) -> dict:
    r = df["R"]
    centered = df.assign(R=r - r.mean())
    return {
        "label": label,
        "n": len(df),
        "avg_r": round(float(r.mean()), 4),
        "win_rate": round(float((r > 0).mean()), 4),
        "median_hold_days": float(df["hold_days"].median()),
        "sharpe": round(sharpe(df), 4),
        "total_r": round(float(r.sum()), 2),
        "se_avg_r": round(float(r.std(ddof=1) / np.sqrt(len(r))), 4),
        "control_mean_centered": {
            "avg_r": round(float(centered["R"].mean()), 6),
            "win_rate": round(float((centered["R"] > 0).mean()), 4),
            "sharpe": round(sharpe(centered), 6),
        },
        "control_direction_permutation": perm_null(df),
    }


def matched(a: pd.DataFrame, b: pd.DataFrame):
    """Greedy nearest-entry match within pair, tolerance +/-3 calendar days."""
    pairs_a, pairs_b, used = [], [], set()
    for i, ra in a.iterrows():
        cand = b[(b["pair"] == ra["pair"])
                 & ((b["entry_date"] - ra["entry_date"]).abs() <= pd.Timedelta(days=MATCH_TOL_DAYS))]
        cand = cand[~cand.index.isin(used)]
        if len(cand):
            j = (cand["entry_date"] - ra["entry_date"]).abs().idxmin()
            used.add(j)
            pairs_a.append(i)
            pairs_b.append(j)
    return a.loc[pairs_a].reset_index(drop=True), b.loc[pairs_b].reset_index(drop=True)


def main() -> int:
    out = {}
    sealed = pd.read_csv(SEALED_CSV)
    sealed["entry_date"] = pd.to_datetime(sealed["entry_date"])
    sealed["exit_date"] = pd.to_datetime(sealed["exit_date"])
    sealed["R"] = sealed["pnl_pct"] / sealed["risk_pct"]
    out["sealed_411"] = arm(sealed, "SEALED 411 (pinned, untouched)")

    arms = {}
    for mode in ("sealed", "nominal", "publication"):
        f = AB / f"{mode}_trades.csv"
        if f.exists():
            arms[mode] = load(f)
            out[f"rig_{mode}"] = arm(arms[mode], f"RIG {mode}")

    if "nominal" in arms and "publication" in arms:
        ma, mb = matched(arms["nominal"], arms["publication"])
        d = (mb["R"] - ma["R"]).to_numpy()
        boot = [d[RNG.integers(0, len(d), len(d))].mean() for _ in range(DRAWS)]
        out["paired_nominal_vs_publication"] = {
            "n_matched": len(d),
            "n_nominal_only": len(arms["nominal"]) - len(d),
            "n_publication_only": len(arms["publication"]) - len(d),
            "mean_delta_r_pub_minus_nom": round(float(d.mean()), 4),
            "delta_r_5_95": [round(float(np.quantile(boot, .05)), 4),
                             round(float(np.quantile(boot, .95)), 4)],
            "n_trades_with_nonzero_delta": int((np.abs(d) > 1e-9).sum()),
        }

    (AB / "stats.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
