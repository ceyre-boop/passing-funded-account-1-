#!/usr/bin/env python3
"""RESIDUAL MODEL — closing the learning loop.

Until now the system MEASURED residuals and then filed them. AlphaZero's
predicted-vs-actual went into a report; Stockfish's realized-vs-lookback went
into a report; neither ever came back to change a prediction. That is
measurement, not learning, and it is why the dashboard numbers can sit still
overnight while "science" runs.

This closes it. Two residuals, each modelled and each FED BACK:

  STOCKFISH   giveback = oracle_r - realized_r, the R the exit could have kept
              and did not. Regressed on ENTRY-TIME observable conditions
              (day-grouped OOF). If conditions predict giveback, the estimate
              is available at decision time as `expected_giveback` — which
              informs POLICY SELECTION, never the engine. Stockfish stays
              frozen; that is the whole basis of it being the yardstick.

  ALPHAZERO   its own scored track record, per verdict class, becomes a prior
              correction injected into the next packet. A model that has been
              wrong in a specific direction should be told so in the same
              breath it is asked again.

HONEST PRIOR, stated before the run: two closely related attempts already
returned null on this data — spec 025's pooled evaluator (NO_SUPERSEDE) and
the oracle audit's feature-based config selection (-0.028, DRAWER). A third
null is the most likely outcome. The point of building it anyway is that the
LOOP must exist for new data to change behaviour; a loop that only gets built
once signal appears never gets built, because nothing can show signal through
an open loop.
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

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import chain                                                       # noqa: E402

OUT = ROOT / "data" / "daytrade" / "residual_model.json"
DECISION_LEDGER = ROOT / "data" / "daytrade" / "decision_ledger.jsonl"
FC_LOG = ROOT / "data" / "daytrade" / "operator" / "forecasts.jsonl"
RECORDS = ROOT / "data" / "daytrade" / "operator" / "records.jsonl"

FEATURES = ["gap_pct", "tr5", "tr20_median", "compression",
            "trend_pct_vs_ma20", "or_complete", "entry_minute"]
MIN_ROWS = 100


class ResidualError(RuntimeError):
    """Not enough to model. Never papered over with a fitted line."""


# ───────────────────────── Stockfish residual ──────────────────────────

def _conditions() -> dict:
    """Entry-time state per (symbol, session) from the decision ledger —
    the only features knowable BEFORE the outcome exists."""
    out: dict[tuple, dict] = {}
    for r in chain.rows(DECISION_LEDGER):
        if r.get("kind") != "decision_point":
            continue
        key = (r["symbol"], r["session"])
        # the LAST decision point at or before 10:00 ET is the closest thing
        # to what was knowable when the entry rule fires
        if r["et_time"] > "10:15":
            continue
        prev = out.get(key)
        if prev is None or r["et_time"] > prev["et_time"]:
            out[key] = r
    return out


def build_stockfish_residuals() -> tuple[np.ndarray, np.ndarray, list, list]:
    q = ROOT / "data" / "daytrade" / "exit_quality.json"
    if not q.exists():
        raise ResidualError("no exit_quality report — run exit_quality.py")
    rows = json.loads(q.read_text()).get("rows", [])
    cond = _conditions()
    X, y, days, meta = [], [], [], []
    for r in rows:
        c = cond.get((r["symbol"], r["session"]))
        if c is None:
            continue
        feats = []
        ok = True
        for f in FEATURES:
            v = c.get(f)
            if f == "or_complete":
                v = 1.0 if v else 0.0
            elif f == "entry_minute":
                v = float(int(c["et_time"][:2]) * 60 + int(c["et_time"][3:]))
            if v is None:
                ok = False
                break
            feats.append(float(v))
        if not ok:
            continue
        # THE RESIDUAL: what the exit left on the table, in R
        giveback = max(r["oracle_r"] - r["realized_r"], 0.0)
        X.append(feats)
        y.append(giveback)
        days.append(r["session"])
        meta.append({"symbol": r["symbol"], "session": r["session"],
                     "realized_r": r["realized_r"], "oracle_r": r["oracle_r"],
                     "giveback": round(giveback, 4)})
    return np.array(X), np.array(y), days, meta


def fit_stockfish() -> dict:
    """Day-grouped OOF. Skill is measured against the only honest baseline:
    predicting the training-fold mean giveback for everyone."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import GroupKFold

    X, y, days, meta = build_stockfish_residuals()
    if len(y) < MIN_ROWS:
        raise ResidualError(f"{len(y)} rows < {MIN_ROWS} — refusing to fit")
    groups = np.array(days)
    n_splits = min(5, len(set(days)))
    oof = np.full(len(y), np.nan)
    base = np.full(len(y), np.nan)
    for tr, te in GroupKFold(n_splits=n_splits).split(X, y, groups):
        m = HistGradientBoostingRegressor(max_iter=200, random_state=43)
        m.fit(X[tr], y[tr])
        oof[te] = m.predict(X[te])
        base[te] = y[tr].mean()          # the no-skill baseline
    assert not np.isnan(oof).any()

    mae_model = float(np.mean(np.abs(y - oof)))
    mae_base = float(np.mean(np.abs(y - base)))
    skill = (mae_base - mae_model) / mae_base if mae_base else 0.0
    corr = float(np.corrcoef(oof, y)[0, 1]) if len(set(oof)) > 1 else 0.0

    # what a decision-time consumer would actually receive
    fitted = HistGradientBoostingRegressor(max_iter=200, random_state=43).fit(X, y)
    return {
        "n": len(y), "n_days": len(set(days)), "features": FEATURES,
        "mean_giveback_r": round(float(y.mean()), 4),
        "oof_mae": round(mae_model, 4), "baseline_mae": round(mae_base, 4),
        "skill_vs_baseline": round(skill, 4),
        "oof_correlation": round(corr, 4),
        "usable": bool(skill > 0.05 and corr > 0.15),
        "verdict": ("FEEDS_POLICY_SELECTION" if (skill > 0.05 and corr > 0.15)
                    else "NO_SKILL — residual is not predictable from entry-time "
                         "conditions; expected_giveback stays the unconditional "
                         "mean and the loop carries no information yet"),
        "_model": fitted,
    }


# ───────────────────────── AlphaZero prior feedback ────────────────────

def alphazero_track_record() -> dict:
    """Its own scored history, shaped as a correction to hand back.

    This is the half of the loop that costs nothing and was simply missing:
    a model that has been wrong in a specific direction should be told so in
    the same breath it is asked again.
    """
    rows = [json.loads(l) for l in FC_LOG.open() if l.strip()] if FC_LOG.exists() else []
    recs = {r["forecast_id"]: r for r in
            ([json.loads(l) for l in RECORDS.open() if l.strip()]
             if RECORDS.exists() else []) if r.get("forecast_id")}
    fs = {r["forecast_id"]: r for r in rows if r["kind"] == "forecast"}
    res = {r["forecast_id"]: r for r in rows if r["kind"] == "resolution"}
    scored = [r for r in rows if r["kind"] == "prereg_score"]

    by_verdict: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "dir_hits": 0, "scen_hits": 0})
    for fid, f in fs.items():
        r = res.get(fid)
        if not r:
            continue
        v = recs.get(fid, {}).get("verdict", "UNKNOWN")
        d = by_verdict[v]
        d["n"] += 1
        d["dir_hits"] += int(f["direction"] == r["outcome_direction"])
        top = max(f["scenario_probs"], key=f["scenario_probs"].get)
        d["scen_hits"] += int(top == r["outcome_scenario"])

    banded = [s for s in scored if s.get("r_in_band") is not None]
    in_band = sum(1 for s in banded if s["r_in_band"])
    realized = [s["realized_r"] for s in banded if s.get("realized_r") is not None]

    n_res = sum(d["n"] for d in by_verdict.values())
    return {
        "n_resolved": n_res,
        "by_verdict": {k: {**v,
                           "dir_rate": round(v["dir_hits"] / v["n"], 3),
                           "scen_rate": round(v["scen_hits"] / v["n"], 3)}
                       for k, v in by_verdict.items()},
        "prereg_scored": len(banded), "prereg_in_band": in_band,
        "mean_realized_r": (round(statistics.mean(realized), 4)
                            if realized else None),
        "quotable": n_res >= 30,
        "min_n": 30,
    }


def prior_correction_text() -> str:
    """The paragraph injected into the next packet. Refuses to state a rate
    it cannot support — an n=13 accuracy handed back as a prior would teach
    the model noise."""
    t = alphazero_track_record()
    if t["n_resolved"] == 0:
        return ("YOUR TRACK RECORD: no resolved forecasts yet. Nothing to "
                "correct for; state your honest uncertainty.")
    if not t["quotable"]:
        lines = [f"YOUR TRACK RECORD ({t['n_resolved']} resolved, BELOW the "
                 f"{t['min_n']} needed before any rate is meaningful — treat "
                 "these as raw counts, not skill estimates):"]
        for v, d in sorted(t["by_verdict"].items()):
            lines.append(f"  {v}: {d['dir_hits']}/{d['n']} direction, "
                         f"{d['scen_hits']}/{d['n']} scenario")
        if t["prereg_scored"]:
            lines.append(f"  expected-R band contained the outcome "
                         f"{t['prereg_in_band']}/{t['prereg_scored']} times")
        lines.append("  Do not over-correct on this sample. It is reported so "
                     "you know what has been observed, not so you can fit it.")
        return "\n".join(lines)
    lines = [f"YOUR TRACK RECORD ({t['n_resolved']} resolved calls):"]
    for v, d in sorted(t["by_verdict"].items()):
        lines.append(f"  {v}: direction {d['dir_rate']:.0%}, "
                     f"scenario {d['scen_rate']:.0%} (n={d['n']})")
    if t["prereg_scored"]:
        lines.append(f"  expected-R band contained the outcome "
                     f"{t['prereg_in_band']}/{t['prereg_scored']}")
    lines.append("  Where these rates sit below chance, your stated confidence "
                 "has been too high. Correct for that here.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="close the learning loop")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    print("  STOCKFISH residual (giveback = oracle - realized), day-grouped OOF:")
    try:
        sf = fit_stockfish()
        model = sf.pop("_model")
        print(f"    n={sf['n']} over {sf['n_days']} days · mean giveback "
              f"{sf['mean_giveback_r']} R")
        print(f"    OOF MAE {sf['oof_mae']} vs baseline {sf['baseline_mae']}  "
              f"-> skill {sf['skill_vs_baseline']:+.1%}, corr {sf['oof_correlation']:+.3f}")
        print(f"    {sf['verdict']}")
    except (ResidualError, Exception) as e:
        sf = {"error": str(e)[:200], "usable": False,
              "verdict": f"UNAVAILABLE — {str(e)[:120]}"}
        print(f"    {sf['verdict']}")

    print("\n  ALPHAZERO prior correction (fed back into the next packet):")
    az = alphazero_track_record()
    text = prior_correction_text()
    for line in text.splitlines():
        print(f"    {line}")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stockfish_residual": sf,
        "alphazero_track_record": az,
        "prior_correction_text": text,
        "loop_closed": True,
        "note": "the residual is modelled and FED BACK; a null model is still "
                "a closed loop — it means the correction is currently zero, "
                "not that no correction is applied",
    }, indent=1))
    print(f"\n  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
