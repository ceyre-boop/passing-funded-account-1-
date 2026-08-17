#!/usr/bin/env python3
"""POOLED EXIT EVALUATOR — spec 025. The sweep reframed as (state, action) → reward.

Everything decision-relevant is IMPORTED (find_entry, simulate + observer hook,
config spaces) — the engine and harness stay one implementation. This file
adds: the per-bar dataset, a day-grouped gradient-boosted advantage model, the
derived exit policy, and the PRE-REGISTERED verdict from specs/025.

Honesty constraints enforced here, per the spec:
  - TUNE split only; the sealed holdout is unreachable from this file.
  - ALL validation is GroupKFold on calendar DATE — same-day cross-symbol
    entries are nearly one bet and random splits would leak it.
  - Reported numbers are out-of-fold. The model that scores a trajectory
    never saw its date.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions, TUNE_END                         # noqa: E402
from ceiling import find_entry, simulate, COST_PER_SHARE           # noqa: E402
from stockfish_exit import Stage                                   # noqa: E402
from stockfish_tune import CLASSES, UNION_BASKET                   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "exit_evaluator_report.json"

SYM_CLASS = {s: c for c, syms in CLASSES.items() for s in syms}
CLASS_NAMES = tuple(CLASSES)

# The configs whose trajectories we log. Pre-registered: the three shipped
# policies' shapes + the frozen futures candidate + two spread points, so the
# model sees varied config behavior without the full 396 grid (which would be
# 396 near-duplicate trajectories per entry, not 396x information).
LOG_CONFIGS = {
    "STATIC":   {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
                 "flatten_et": None, "hold_past_tp2": True},
    "TRAIL_WIDE": {"trail_mult": 1.5, "be_arm_frac": 1.0, "partial_frac": 0.5,
                   "flatten_et": None, "hold_past_tp2": True},
    "TRAIL_TIGHT": {"trail_mult": 0.5, "be_arm_frac": 0.5, "partial_frac": 0.5,
                    "flatten_et": None, "hold_past_tp2": True},
    "FUT_CAND": {"trail_mult": None, "be_arm_frac": 0.25, "partial_frac": 0.75,
                 "flatten_et": "12:00", "hold_past_tp2": True},
    "EARLY_BANK": {"trail_mult": None, "be_arm_frac": 0.25, "partial_frac": 0.75,
                   "flatten_et": None, "hold_past_tp2": True},
    "NOON_STATIC": {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
                    "flatten_et": "12:00", "hold_past_tp2": True},
}

BEST_SHIPPED = {"SINGLE_NAME": "STATIC", "CASH_INDEX": "TRAIL_WIDE",
                "FUTURES": "TRAIL_WIDE"}          # from the furnace report
SUPERSEDE_MARGIN = 0.05

_STAGE_ORD = {Stage.ENTERED: 0, Stage.PROTECTED: 1, Stage.SCALED: 2,
              Stage.RUNNER: 3, Stage.CLOSING: 4, Stage.CLOSED: 5}

FEATURES = ["r_banked", "unrealized_r", "hwm_r", "dd_from_hwm_r", "bars_held",
            "minutes_to_close", "dist_to_stop_r", "dist_to_tp2_r",
            "stage_ord", "tr5_r",
            "cls_SINGLE_NAME", "cls_CASH_INDEX", "cls_FUTURES",
            "cfg_no_trail", "cfg_trail_mult", "cfg_be_arm", "cfg_partial",
            "cfg_flatten_min_left", "cfg_hold_past_tp2"]


def build_dataset(entries: list[tuple]) -> dict:
    """One row per (entry, config, bar-while-open). Labels = continuation
    advantage (final trajectory R − exit-now R at this bar)."""
    X, y, meta = [], [], []
    for sym, s, e in entries:
        cls = SYM_CLASS[sym]
        tr5: list[float] = []
        for cname, cfg in LOG_CONFIGS.items():
            rows = []

            def obs(ts, bar, st, realized, held):
                if st.stage in (Stage.CLOSING, Stage.CLOSED) or held <= 0:
                    return
                px = float(bar["Open"])
                exit_now = (realized + held * (px - e.entry) * e.direction
                            - COST_PER_SHARE) / e.risk
                hhmm = ts.strftime("%H:%M")
                mins = (int(hhmm[:2]) * 60 + int(hhmm[3:]))
                flat_left = -1.0
                if cfg["flatten_et"]:
                    fm = int(cfg["flatten_et"][:2]) * 60 + int(cfg["flatten_et"][3:])
                    flat_left = max(0, fm - mins)
                tr = (float(bar["High"]) - float(bar["Low"])) / e.risk
                tr5.append(tr)
                feats = [
                    (realized / e.risk),
                    (px - e.entry) * e.direction / e.risk,
                    (st.hwm - e.entry) * e.direction / e.risk,
                    (st.hwm - px) * e.direction / e.risk,
                    len(rows),
                    (15 * 60 + 55) - mins,
                    (px - st.sl) * e.direction / e.risk,
                    (st.tp2 - px) * e.direction / e.risk,
                    _STAGE_ORD[st.stage],
                    sum(tr5[-5:]) / min(len(tr5), 5),
                    *(1.0 if cls == c else 0.0 for c in CLASS_NAMES),
                    1.0 if cfg["trail_mult"] is None else 0.0,
                    cfg["trail_mult"] or 0.0,
                    cfg["be_arm_frac"], cfg["partial_frac"],
                    flat_left, 1.0 if cfg["hold_past_tp2"] else 0.0,
                ]
                rows.append((feats, exit_now))

            final_r = simulate(s, e, dict(cfg), observer=obs)
            for feats, exit_now in rows:
                X.append(feats)
                y.append(final_r - exit_now)
                meta.append({"sym": sym, "cls": cls, "day": str(s.day),
                             "cfg": cname, "exit_now": exit_now,
                             "final_r": final_r})
    return {"X": np.array(X), "y": np.array(y), "meta": meta}


def day_grouped_oof(ds: dict, n_splits: int = 5):
    """Out-of-fold advantage predictions, grouped by calendar date."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import GroupKFold
    groups = np.array([m["day"] for m in ds["meta"]])
    oof = np.full(len(ds["y"]), np.nan)
    models = []
    for tr, te in GroupKFold(n_splits=n_splits).split(ds["X"], ds["y"], groups):
        m = HistGradientBoostingRegressor(max_iter=300, random_state=17)
        m.fit(ds["X"][tr], ds["y"][tr])
        oof[te] = m.predict(ds["X"][te])
        models.append(m)
    assert not np.isnan(oof).any()
    return oof, models


def derived_policy_r(ds: dict, oof: np.ndarray) -> dict:
    """Per class: ride the class's best-shipped trajectory, exit at the first
    bar whose OOF-predicted advantage < 0. Counterfactual exit values are
    already in the dataset — no new simulation."""
    per_traj: dict[tuple, list[int]] = defaultdict(list)
    for i, m in enumerate(ds["meta"]):
        per_traj[(m["sym"], m["day"], m["cfg"])].append(i)

    out = {}
    for cls in CLASS_NAMES:
        base = BEST_SHIPPED[cls]
        model_rs, static_rs = [], []
        for (sym, day, cfg), idxs in per_traj.items():
            if cfg != base or SYM_CLASS[sym] != cls:
                continue
            idxs = sorted(idxs, key=lambda i: ds["meta"][i]["exit_now"] * 0
                          + i)                     # insertion order = bar order
            final = ds["meta"][idxs[0]]["final_r"]
            static_rs.append(final)
            r = final
            for i in idxs:
                if oof[i] < 0:
                    r = ds["meta"][i]["exit_now"]
                    break
            model_rs.append(r)
        out[cls] = {"n": len(model_rs),
                    "model_mean_R": round(float(np.mean(model_rs)), 4),
                    "shipped_mean_R": round(float(np.mean(static_rs)), 4),
                    "delta_R": round(float(np.mean(model_rs) - np.mean(static_rs)), 4)}
    return out


def verdict(policy: dict, fut_cand_mean: float) -> dict:
    """The pre-registered rule from specs/025, verbatim."""
    wins = {c: p["delta_R"] >= SUPERSEDE_MARGIN for c, p in policy.items()}
    fut_beats_cand = (policy["FUTURES"]["model_mean_R"]
                      >= fut_cand_mean + SUPERSEDE_MARGIN)
    supersede = (sum(wins.values()) >= 2 and wins["FUTURES"] and fut_beats_cand)
    return {"verdict": "SUPERSEDE_STATIC" if supersede else "NO_SUPERSEDE",
            "class_wins": wins, "futures_beats_frozen_candidate": fut_beats_cand,
            "frozen_candidate_mean_R": round(fut_cand_mean, 4),
            "detail": ("evaluator policy clears +0.05 in >=2 classes incl. "
                       "FUTURES and beats the frozen candidate" if supersede else
                       "static candidates stand; the sealed read's object is "
                       "unchanged — a null result is a result")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 025 — pooled exit evaluator")
    ap.add_argument("--symbols", default=",".join(UNION_BASKET))
    a = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]

    entries = []
    for sym in symbols:
        try:
            for s in tune_sessions(load_sessions(sym, "5m", allow_fetch=False)):
                e = find_entry(s)
                if e:
                    entries.append((sym, s, e))
        except BarDataError as ex:
            print(f"  !! {sym}: {ex} — excluded loudly")
    print(f"  {len(entries)} tune entries (<= {TUNE_END}) · "
          f"{len(LOG_CONFIGS)} logged configs")

    t0 = datetime.now()
    ds = build_dataset(entries)
    print(f"  dataset: {len(ds['y']):,} decision points in "
          f"{(datetime.now()-t0).total_seconds():.1f}s")

    oof, models = day_grouped_oof(ds)
    corr = float(np.corrcoef(oof, ds["y"])[0, 1])
    print(f"  OOF advantage corr: {corr:+.3f} (day-grouped 5-fold)")

    from sklearn.inspection import permutation_importance
    imp = permutation_importance(models[-1], ds["X"][-4000:], ds["y"][-4000:],
                                 n_repeats=3, random_state=17)
    top_feats = sorted(zip(FEATURES, imp.importances_mean),
                       key=lambda kv: -kv[1])[:8]
    print("  top features: " + ", ".join(f"{n} {v:.3f}" for n, v in top_feats))

    policy = derived_policy_r(ds, oof)
    # frozen-candidate mean on FUTURES from the logged FUT_CAND trajectories:
    seen = set()
    fut_rs = []
    for m in ds["meta"]:
        key = (m["sym"], m["day"])
        if m["cfg"] == "FUT_CAND" and m["cls"] == "FUTURES" and key not in seen:
            seen.add(key)
            fut_rs.append(m["final_r"])
    fut_cand_mean = float(np.mean(fut_rs)) if fut_rs else float("nan")

    v = verdict(policy, fut_cand_mean)

    print()
    for cls, p in policy.items():
        print(f"  {cls:12s} n {p['n']:3d}  shipped {p['shipped_mean_R']:+.4f}  "
              f"evaluator {p['model_mean_R']:+.4f}  Δ {p['delta_R']:+.4f}")
    print(f"  frozen FUT candidate mean: {fut_cand_mean:+.4f}")
    print(f"\n  VERDICT: {v['verdict']} — {v['detail']}")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_entries": len(entries), "n_decision_points": len(ds["y"]),
        "oof_advantage_corr": round(corr, 4),
        "top_features": [[n, round(float(x), 4)] for n, x in top_feats],
        "policy": policy, "verdict": v,
        "preregistration": "specs/025_EXIT_EVALUATOR.md",
    }, indent=1))
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
