#!/usr/bin/env python3
"""TRADE / SKIP — spec 037 pre-registration. Given the entry rule fired,
should we have taken the trade at all?

Everything here is fixed by `specs/037_TRADE_SKIP_PREREGISTRATION.md`, written
BEFORE any model was fitted (commit b32fba8). This module implements exactly
that contract — population, policy column, label, model class, validation
scheme, gates, and reporting discipline — and nothing beyond it. A second
feature set, model class, or threshold under this run forfeits the
pre-registration; build a NEW spec instead of editing constants here.

POPULATION (frozen, spec 037 §Population)
------------------------------------------
NVDA only. `data/daytrade/bars_extended/NVDA_5m.parquet`. `splits.tune_sessions`,
`TUNE_END = 2026-07-06`, unedited — 402 tune entry days. The 36 sealed sessions
after TUNE_END are never read here.

POLICY COLUMN AND LABEL (frozen)
---------------------------------
`EARLY_BANK` — ONE column from `oracle_audit.FAMILIES`, never a max over
columns. The label is `R_EARLY_BANK` on the entry day, continuous, from
`ceiling.simulate`. Decision is trade-or-skip; skipping realizes exactly 0.0 R.

FEATURES (frozen)
-------------------
`data/daytrade/extended_features.parquet` (commit 85de749), joined per entry
day to the LATEST decision point strictly at or before the entry bar's
timestamp — the same no-look-ahead convention as
`residual_model.py::_conditions` (latest snapshot at-or-before a cutoff, never
after). Numeric feature columns only (float64/int64 dtype); id/text/bookkeeping
columns are dropped. The exact columns used are recorded in the output JSON.

MODEL AND VALIDATION (frozen)
-------------------------------
One class: `sklearn.ensemble.HistGradientBoostingRegressor` on R, threshold at
0 (predicted_trade = oof_prediction > 0). No model search, no hyperparameter
sweep. `GroupKFold(5)` grouped on calendar date — `RNG_SEED = 26` throughout.

GATES (frozen, spec 037 §Gates — pre-registered, order fixed)
-----------------------------------------------------------------
  T1 POWER            n >= 129
  T2 NON-DEGENERATE    model trades on >= 40% of days, else DEGENERATE_SKIPPER
                       and T3-T5 are NOT computed or quoted.
  T3 BEATS RANDOM      OOF realized mean R 5-95% interval LOWER bound >
                       rate-matched random skipper interval UPPER bound,
                       1000 draws. Rate-matched = skip the same NUMBER of
                       days the model skipped, chosen uniformly at random.
  T4 BEATS NOISE       OOF realized mean R > shuffled-feature control + 0.05.
  T5 BEATS ALWAYS-TRADE OOF realized mean R > -0.0061 + 0.05. Weakest gate,
                       listed last deliberately.

REPORTING (spec 021 P5 discipline, mirrored from
`scripts/carry_buy_gate.py::fmt_row`'s "takes both or raises" shape)
-----------------------------------------------------------------------------
The formatter prints all four reference points on one screen, or raises:
`always-trade -> rate-matched random -> shuffled-feature -> MODEL (OOF)`,
trade rate beside each. The model is never shown alone.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bars as bars_mod                                            # noqa: E402
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions, TUNE_END                         # noqa: E402
from ceiling import find_entry, simulate                           # noqa: E402
from oracle_audit import FAMILIES                                  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daytrade" / "bars_extended"
FEATURES_PATH = ROOT / "data" / "daytrade" / "extended_features.parquet"
OUT = ROOT / "data" / "daytrade" / "trade_skip_result.json"

SYMBOL = "NVDA"
POLICY_COLUMN = "EARLY_BANK"
RNG_SEED = 26

EXPECTED_TUNE_ENTRIES = 402

# id / text / bookkeeping columns in extended_features.parquet — never fed to
# the model. Everything else with numeric dtype (float64/int64) is a feature;
# `or_complete` (bool) is treated as bookkeeping like the *_as_of strings, not
# a continuous numeric signal, matching the 59-numeric-column count the
# feature table ships with.
NON_FEATURE_COLUMNS = {"session_date", "decision_point"}

# --------------------------------------------------------------- gates
T1_MIN_N = 129
T2_MIN_TRADE_RATE = 0.40
T3_DRAWS = 1000
T3_LO_PCT, T3_HI_PCT = 5, 95
T4_MARGIN = 0.05
T5_MARGIN = 0.05
ALWAYS_TRADE_BASELINE = -0.0061          # frozen, spec 037 §Measured baselines


class PopulationError(RuntimeError):
    """The frozen population did not come back as pre-registered."""


class ReportingError(RuntimeError):
    """A caller tried to print the model without its mandatory controls."""


# ------------------------------------------------------------ population

def _collect_entries():
    """NVDA tune-session entries with their EARLY_BANK R. Cache redirect
    save/restore-in-`finally`, identical pattern to `oracle_audit._collect`."""
    if not any(CACHE.glob("*_5m.parquet")):
        raise BarDataError(
            f"{CACHE} holds no *_5m.parquet. Refusing to fall back to "
            f"{bars_mod.CACHE} — a silent fallback would label this run with "
            f"one population's name and another's data.")
    orig = bars_mod.CACHE
    bars_mod.CACHE = CACHE
    try:
        sessions = tune_sessions(load_sessions(SYMBOL, "5m", allow_fetch=False))
    finally:
        bars_mod.CACHE = orig

    for s in sessions:
        if s.day > TUNE_END:
            raise PopulationError(
                f"session {s.day} is after TUNE_END {TUNE_END} — the sealed "
                f"split leaked into the tune population")

    cfg = dict(FAMILIES[POLICY_COLUMN])
    entries = []
    for s in sessions:
        e = find_entry(s)
        if e is None:
            continue
        r = simulate(s, e, cfg)
        entries.append({"day": str(s.day), "entry_ts": e.ts,
                        "entry_hhmm": e.ts[11:16], "R": float(r)})
    return entries


def _feature_columns(feat: pd.DataFrame) -> list[str]:
    cols = [c for c in feat.columns if c not in NON_FEATURE_COLUMNS
           and feat[c].dtype.kind in ("f", "i")]
    return cols


def _join_features(entries: list[dict], feat: pd.DataFrame, cols: list[str]):
    """Join each entry to the LATEST decision point strictly at or before the
    entry bar's HH:MM — the no-look-ahead join. A decision point AFTER the
    entry bar is never eligible, by construction of the `<=` filter below."""
    X, y, days, meta = [], [], [], []
    for ent in entries:
        sub = feat[(feat["session_date"] == ent["day"])
                  & (feat["decision_point"] <= ent["entry_hhmm"])]
        if sub.empty:
            raise PopulationError(
                f"no feature row at or before {ent['entry_hhmm']} on "
                f"{ent['day']} — coverage gap in extended_features.parquet")
        row = sub.sort_values("decision_point").iloc[-1]
        if row["decision_point"] > ent["entry_hhmm"]:
            raise PopulationError(
                f"joined decision point {row['decision_point']} is after "
                f"entry bar {ent['entry_hhmm']} on {ent['day']} — look-ahead")
        feats = [float(row[c]) for c in cols]
        X.append(feats)
        y.append(ent["R"])
        days.append(ent["day"])
        meta.append({"day": ent["day"], "entry_hhmm": ent["entry_hhmm"],
                     "decision_point": row["decision_point"]})
    return np.array(X), np.array(y), np.array(days), meta


def load_dataset():
    entries = _collect_entries()
    n = len(entries)
    if n != EXPECTED_TUNE_ENTRIES:
        raise PopulationError(
            f"expected exactly {EXPECTED_TUNE_ENTRIES} tune entry days, got "
            f"{n} — the frozen population changed; this pre-registration "
            f"does not cover a different n")
    feat = pd.read_parquet(FEATURES_PATH)
    cols = _feature_columns(feat)
    X, y, days, meta = _join_features(entries, feat, cols)
    return X, y, days, meta, cols


# --------------------------------------------------------------- model

def _fit_oof(X: np.ndarray, y: np.ndarray, days: np.ndarray, *, seed: int = RNG_SEED):
    """GroupKFold(5) on calendar date, HistGradientBoostingRegressor on R.
    Returns OOF predictions, same shape as y."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import GroupKFold

    oof = np.full(len(y), np.nan)
    for tr, te in GroupKFold(5).split(X, y, days):
        m = HistGradientBoostingRegressor(random_state=seed)
        m.fit(X[tr], y[tr])
        oof[te] = m.predict(X[te])
    assert not np.isnan(oof).any()
    return oof


def _realized(y: np.ndarray, predicted_trade: np.ndarray) -> np.ndarray:
    return np.where(predicted_trade, y, 0.0)


# ------------------------------------------------------------- controls

def _bootstrap_interval(values: np.ndarray, *, draws: int, seed: int):
    """5-95% interval of the mean under `draws` resamples with replacement."""
    rng = np.random.default_rng(seed)
    n = len(values)
    means = np.array([values[rng.integers(0, n, size=n)].mean()
                      for _ in range(draws)])
    return float(np.percentile(means, T3_LO_PCT)), float(np.percentile(means, T3_HI_PCT))


def _rate_matched_random(y: np.ndarray, k_skip: int, *, draws: int, seed: int):
    """Skip the same NUMBER of days the model skipped, chosen uniformly at
    random, `draws` times. Returns (mean of per-draw means, lo, hi)."""
    rng = np.random.default_rng(seed)
    n = len(y)
    means = []
    for _ in range(draws):
        skip_idx = rng.choice(n, size=k_skip, replace=False)
        mask = np.ones(n, dtype=bool)
        mask[skip_idx] = False           # False = skipped -> realizes 0.0
        means.append(_realized(y, mask).mean())
    means = np.array(means)
    return (float(means.mean()), float(np.percentile(means, T3_LO_PCT)),
            float(np.percentile(means, T3_HI_PCT)))


def _shuffled_feature_control(X: np.ndarray, y: np.ndarray, days: np.ndarray,
                              *, seed: int):
    """Permute feature rows against labels, refit the IDENTICAL pipeline,
    same fold structure (GroupKFold on the same `days`). Expected
    improvement over always-trade is ~0 under this control."""
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(X))
    X_shuffled = X[perm]
    oof = _fit_oof(X_shuffled, y, days, seed=seed)
    predicted_trade = oof > 0
    realized = _realized(y, predicted_trade)
    return realized, predicted_trade


# --------------------------------------------------------------- gates

def run_gates(X, y, days, meta, cols):
    n = len(y)
    result: dict = {"n": n, "policy_column": POLICY_COLUMN,
                    "feature_columns": cols, "rng_seed": RNG_SEED}

    # T1 -----------------------------------------------------------------
    t1_pass = n >= T1_MIN_N
    result["T1_POWER"] = {"pass": t1_pass, "n": n, "min_n": T1_MIN_N}

    oof = _fit_oof(X, y, days)
    predicted_trade = oof > 0
    trade_rate = float(predicted_trade.mean())
    model_realized = _realized(y, predicted_trade)
    model_mean = float(model_realized.mean())

    # T2 -----------------------------------------------------------------
    t2_pass = trade_rate >= T2_MIN_TRADE_RATE
    result["T2_NON_DEGENERATE"] = {"pass": t2_pass, "trade_rate": trade_rate,
                                   "min_trade_rate": T2_MIN_TRADE_RATE}

    result["always_trade"] = {"mean": ALWAYS_TRADE_BASELINE, "trade_rate": 1.0,
                              "computed_mean": float(y.mean())}
    result["model"] = {"mean": model_mean, "trade_rate": trade_rate}

    if not t2_pass:
        result["verdict"] = "DEGENERATE_SKIPPER"
        return result

    k_skip = int((~predicted_trade).sum())
    random_mean, random_lo, random_hi = _rate_matched_random(
        y, k_skip, draws=T3_DRAWS, seed=RNG_SEED)
    model_lo, model_hi = _bootstrap_interval(model_realized, draws=T3_DRAWS,
                                             seed=RNG_SEED)
    result["rate_matched_random"] = {
        "mean": random_mean, "lo": random_lo, "hi": random_hi,
        "trade_rate": float((n - k_skip) / n), "draws": T3_DRAWS}
    result["model"]["lo"] = model_lo
    result["model"]["hi"] = model_hi

    t3_pass = model_lo > random_hi
    result["T3_BEATS_RANDOM"] = {
        "pass": t3_pass, "model_lo": model_lo, "random_hi": random_hi}

    shuffled_realized, shuffled_predicted_trade = _shuffled_feature_control(
        X, y, days, seed=RNG_SEED)
    shuffled_mean = float(shuffled_realized.mean())
    result["shuffled_feature"] = {
        "mean": shuffled_mean, "trade_rate": float(shuffled_predicted_trade.mean())}

    t4_pass = model_mean > shuffled_mean + T4_MARGIN
    result["T4_BEATS_NOISE"] = {
        "pass": t4_pass, "model_mean": model_mean,
        "shuffled_mean": shuffled_mean, "margin": T4_MARGIN}

    t5_pass = model_mean > ALWAYS_TRADE_BASELINE + T5_MARGIN
    result["T5_BEATS_ALWAYS_TRADE"] = {
        "pass": t5_pass, "model_mean": model_mean,
        "always_trade_baseline": ALWAYS_TRADE_BASELINE, "margin": T5_MARGIN}

    result["verdict"] = ("ALL_GATES_PASS"
                         if (t3_pass and t4_pass and t5_pass) else "FAILED")
    return result


# ------------------------------------------------------------ reporting

def format_reference_line(always_trade, rate_matched, shuffled, model) -> str:
    """Prints all four reference points, or raises. Never the model alone —
    mirrors `scripts/carry_buy_gate.py::fmt_row`'s "takes both or raises"."""
    refs = [("always-trade", always_trade), ("rate-matched random", rate_matched),
           ("shuffled-feature", shuffled), ("MODEL (OOF)", model)]
    missing = [name for name, ref in refs if ref is None]
    if missing:
        raise ReportingError(
            f"formatter requires all four reference points; missing: "
            f"{', '.join(missing)}")
    lines = []
    for name, ref in refs:
        lines.append(f"  {name:>22} | mean R {ref['mean']:+.4f} | "
                     f"trade rate {ref['trade_rate']:6.1%}")
    return "\n".join(lines)


# ------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = Path(a.out).resolve() if a.out else OUT

    X, y, days, meta, cols = load_dataset()
    n_days = len(set(days.tolist()))
    print(f"  {len(y)} tune entries (<= {TUNE_END}) over {n_days} distinct days "
         f"· policy={POLICY_COLUMN} · {len(cols)} feature columns")

    result = run_gates(X, y, days, meta, cols)

    print(f"\n  T1 POWER              n={result['n']} "
         f"[{'PASS' if result['T1_POWER']['pass'] else 'FAIL'}]")
    print(f"  T2 NON-DEGENERATE      trade rate "
         f"{result['model']['trade_rate']:.1%} "
         f"[{'PASS' if result['T2_NON_DEGENERATE']['pass'] else 'FAIL'}]")

    if result["verdict"] == "DEGENERATE_SKIPPER":
        print("\n  DEGENERATE_SKIPPER — T3-T5 are NOT computed or quoted.")
    else:
        print()
        print(format_reference_line(
            result["always_trade"], result["rate_matched_random"],
            result["shuffled_feature"], result["model"]))
        print(f"\n  T3 BEATS RANDOM        model lo {result['T3_BEATS_RANDOM']['model_lo']:+.4f} "
             f"> random hi {result['T3_BEATS_RANDOM']['random_hi']:+.4f} "
             f"[{'PASS' if result['T3_BEATS_RANDOM']['pass'] else 'FAIL'}]")
        print(f"  T4 BEATS NOISE         {result['T4_BEATS_NOISE']['model_mean']:+.4f} > "
             f"{result['T4_BEATS_NOISE']['shuffled_mean']:+.4f} + {T4_MARGIN} "
             f"[{'PASS' if result['T4_BEATS_NOISE']['pass'] else 'FAIL'}]")
        print(f"  T5 BEATS ALWAYS-TRADE  {result['T5_BEATS_ALWAYS_TRADE']['model_mean']:+.4f} > "
             f"{ALWAYS_TRADE_BASELINE} + {T5_MARGIN} "
             f"[{'PASS' if result['T5_BEATS_ALWAYS_TRADE']['pass'] else 'FAIL'}]")

    print(f"\n  VERDICT: {result['verdict']}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }, indent=2, default=float))
    print(f"\n  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
