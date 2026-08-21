#!/usr/bin/env python3
"""THE SCIENCE LOOP — what runs when nobody is watching.

A nightly re-run of identical tests on identical data is not science, it is a
heartbeat. What makes this science is that DATA ARRIVES, the measurements are
recomputed against the grown record, and the loop reports WHAT MOVED — not
"tests passed".

Three things it does, in order:

  1. INGEST     new FX sessions and policy-rate observations. This is the only
                genuinely new information a day produces without the market
                being open and a model being paid.
  2. RE-MEASURE every deterministic instrument against the grown record.
  3. DIFF       compare to the previous run and surface only what changed,
                plus any threshold a growing n has now crossed — a mechanism
                becoming testable, an MDE becoming reachable, a chain
                breaking, a seal drifting.

What it deliberately does NOT do: call any model, spend any money, touch a
sealed artifact, re-pin a checkpoint, or trade. Those need a human, a market,
or both.

Writes `data/daytrade/science_log.jsonl` (append-only, hash-chained) so the
history of what moved is itself auditable.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import chain                                                       # noqa: E402

LOG = ROOT / "data" / "daytrade" / "science_log.jsonl"
REPORTS = {
    "carry_reconcile": ROOT / "data" / "daytrade" / "carry_reconcile.json",
    "exit_quality": ROOT / "data" / "daytrade" / "exit_quality.json",
    "ontology_audit": ROOT / "data" / "daytrade" / "ontology_audit.json",
    "stack_exam": ROOT / "data" / "daytrade" / "stack_exam.json",
    "wrapper_anomaly": ROOT / "data" / "daytrade" / "wrapper_anomaly.json",
    "residual_model": ROOT / "data" / "daytrade" / "residual_model.json",
}
# Metrics watched across runs. A move in any of these is the signal; the rest
# of a report is context.
WATCHED = {
    "fx_rows": ("fx_state", None),
    "carry_reason_match": ("carry_reconcile", "reason_match_rate"),
    "carry_day_match": ("carry_reconcile", "day_match_rate"),
    "exit_efficiency": ("exit_quality", "median_efficiency"),
    "pct_unwinnable": ("exit_quality", "pct_unwinnable"),
    "ontology_carves": ("ontology_audit", "n_carves"),
    "ontology_bonferroni": ("ontology_audit", "n_survive_bonferroni"),
    "ontology_unstable": ("ontology_audit", "n_unstable"),
    "ontology_n_sessions": ("ontology_audit", "n_sessions"),
    "wrapper_p": ("wrapper_anomaly", "p_pooled"),
}
# nested metrics the flat WATCHED map cannot reach
NESTED = {
    "residual_skill": ("residual_model", ("stockfish_residual", "skill_vs_baseline")),
    "residual_corr": ("residual_model", ("stockfish_residual", "oof_correlation")),
    "az_resolved": ("residual_model", ("alphazero_track_record", "n_resolved")),
}
MOVE_EPS = 1e-9


def sh(cmd: list[str], *, quiet: bool = True) -> int:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 and not quiet:
        print(r.stdout[-1500:])
        print(r.stderr[-1500:])
    return r.returncode


def integrity() -> list[str]:
    """Nothing is measured on a record that has drifted. Checked FIRST, so a
    broken chain aborts the run instead of producing confident numbers on a
    corrupted history."""
    problems = []
    for name, cmd in (
            ("decision ledger chain", ["python3", "daytrade/decision_ledger.py", "verify"]),
            ("fx state chain", ["python3", "daytrade/fx_state.py", "verify"]),
            ("seal registry", ["python3", "daytrade/seals.py", "check"]),
            ("frozen checkpoint", ["python3", "daytrade/frozen_policy.py", "verify"]),
            ("mechanism ledger", ["python3", "daytrade/mechanisms.py", "check"])):
        if sh(cmd) != 0:
            problems.append(name)
    return problems


def ingest() -> dict:
    """New sessions and new rate prints. The only genuinely new information."""
    import os
    before = len(chain.rows(ROOT / "data" / "carry" / "fx_state.jsonl"))
    rates_ok = bool(os.environ.get("FRED_API_KEY"))
    if rates_ok:
        rates_ok = sh(["python3", "daytrade/fx_state.py", "fetch-rates"]) == 0
    else:
        print("  !! FRED_API_KEY absent — rate observations NOT refreshed. "
              "Bars and measurements still run; rate_diff ages until a key "
              "exists. Stated, not silently skipped.")
    sh(["python3", "daytrade/fx_state.py", "fetch-bars", "--period", "1y"])
    sh(["python3", "daytrade/fx_state.py", "build", "--days", "10"])
    after = len(chain.rows(ROOT / "data" / "carry" / "fx_state.jsonl"))
    return {"fx_rows_before": before, "fx_rows_after": after,
            "fx_rows_added": after - before, "rates_refreshed": rates_ok}


def remeasure() -> list[str]:
    """Every deterministic instrument, against the grown record."""
    failed = []
    for name, cmd in (
            ("carry_reconcile", ["python3", "daytrade/carry_reconcile.py"]),
            ("exit_quality", ["python3", "daytrade/exit_quality.py"]),
            ("ontology_audit", ["python3", "daytrade/ontology_audit.py"]),
            ("wrapper_anomaly", ["python3", "daytrade/wrapper_anomaly.py"]),
            ("residual_model", ["python3", "daytrade/residual_model.py"]),
            ("stack_exam", ["python3", "daytrade/stack_exam.py"])):
        if sh(cmd) != 0:
            failed.append(name)
    return failed


def snapshot() -> dict:
    """The watched metrics, right now."""
    out = {"fx_rows": len(chain.rows(ROOT / "data" / "carry" / "fx_state.jsonl"))}
    loaded = {}
    for key, path in REPORTS.items():
        loaded[key] = json.loads(path.read_text()) if path.exists() else {}
    for metric, (report, field) in WATCHED.items():
        if field is None:
            continue
        out[metric] = loaded.get(report, {}).get(field)
    for metric, (report, path) in NESTED.items():
        node = loaded.get(report, {})
        for k in path:
            node = node.get(k, {}) if isinstance(node, dict) else None
        out[metric] = node if not isinstance(node, dict) else None
    out["residual_usable"] = ((loaded.get("residual_model", {})
                               .get("stockfish_residual") or {}).get("usable"))
    ex = loaded.get("stack_exam", {}).get("tally", {})
    out["exam_pass"] = ex.get("PASS")
    out["exam_fail"] = ex.get("FAIL")
    out["exam_gap"] = ex.get("GAP")
    return out


def thresholds(snap: dict) -> list[str]:
    """What a growing n has made possible that was not possible before.
    This is the part that turns a heartbeat into a queue."""
    notes = []
    try:
        import mechanisms as mx
        led = mx._load()
        for m in led["mechanisms"]:
            if m["status"] != "proposed":
                continue
            ev = m.get("soak_evidence") or {}
            if ev.get("verdict") == "EMPTY_CHANNEL" and ev.get("n_divergences", 0) > 0:
                notes.append(f"{m['id']}: the channel is no longer empty "
                             f"({ev['n_divergences']} divergences) — testable")
            pred = mx.predicted_of(m)
            res = m.get("minimum_detectable_effect_r")
            if pred and res and abs(pred) >= res:
                notes.append(f"{m['id']}: predicted effect {pred} now clears "
                             f"the MDE {res} — testable")
    except Exception as e:                     # never let the queue kill the run
        notes.append(f"threshold scan unavailable: {e}")
    return notes


def diff(prev: dict, snap: dict) -> dict:
    moved = {}
    for k, v in snap.items():
        old = prev.get(k)
        if old is None and v is None:
            continue
        if old is None or v is None or (
                isinstance(v, (int, float)) and isinstance(old, (int, float))
                and abs(v - old) > MOVE_EPS) or (
                not isinstance(v, (int, float)) and v != old):
            moved[k] = {"from": old, "to": v}
    return moved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the always-on science loop")
    ap.add_argument("--no-ingest", action="store_true",
                    help="re-measure only (offline / test)")
    a = ap.parse_args(argv)

    started = datetime.now(timezone.utc).isoformat()
    print(f"=== science loop {started} ===")

    broken = integrity()
    if broken:
        print(f"  !! INTEGRITY FAILURE: {broken}")
        print("  refusing to measure on a record that has drifted")
        chain.append(LOG, {"kind": "science_run", "started": started,
                           "aborted": True, "integrity_failures": broken})
        return 1
    print("  integrity: chains, seals, checkpoint, ledger all intact")

    ing = ingest() if not a.no_ingest else {"fx_rows_added": 0, "skipped": True}
    print(f"  ingest: +{ing['fx_rows_added']} FX state rows")

    failed = remeasure()
    if failed:
        print(f"  !! instruments failed: {failed}")

    snap = snapshot()
    rows = [r for r in chain.rows(LOG) if r.get("kind") == "science_run"
            and not r.get("aborted")]
    prev = rows[-1]["metrics"] if rows else {}
    moved = diff(prev, snap)
    crossed = thresholds(snap)

    print(f"\n  WHAT MOVED ({len(moved)} of {len(snap)} watched):")
    if not moved:
        print("    nothing — the record grew but no measured quantity changed")
    for k, d in sorted(moved.items()):
        print(f"    {k:24s} {d['from']} -> {d['to']}")
    if crossed:
        print("\n  THRESHOLDS CROSSED:")
        for c in crossed:
            print(f"    ! {c}")

    chain.append(LOG, {
        "kind": "science_run", "started": started,
        "finished": datetime.now(timezone.utc).isoformat(),
        "aborted": False, "ingest": ing, "instruments_failed": failed,
        "metrics": snap, "moved": moved, "thresholds_crossed": crossed})
    print(f"\n  logged to {LOG.relative_to(ROOT)} "
          f"(run #{len(rows) + 1}, chain intact)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
