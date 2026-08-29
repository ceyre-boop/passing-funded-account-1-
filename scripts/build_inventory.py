#!/usr/bin/env python3
"""scripts/build_inventory.py — Phase 0 inventory, computed not eyeballed.

Writes `artifacts/inventory.json` (via sovereign.forex.inventory, which owns the
`hashes` map every evaluation gate must verify before it runs) and prints a
summary under 40 lines. Never prints raw rows.

Sections written under `phase0`:
  engines        the two decide_exit engines, their callers, checkpoints + hashes
  population     the honest CB-off trade set (n, span, exit-reason mix, sum R)
  path_data      spot-cache coverage per pair
  fx_state       the 10,188-row vector's current size and null columns
  self_exclusion which measurement scripts assert self-exclusion (grep-based)
  sealed         SEALS.json entries re-hashed: matches / mismatches
  tests          test-file counts per tree
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sovereign.forex import inventory as inv  # noqa: E402

DEPENDENCIES = {
    "sovereign/forex/exit_machine.py": "carry incumbent decide_exit — the rule under test",
    "sovereign/forex/fast_backtester.py": "the per-bar loop that calls the incumbent; entry/stop/hold semantics",
    "sovereign/forex/forex_backtester.py": "incumbent config constants + _apply_costs (SPREAD_COST, SWAP_RATES_ANNUAL)",
    "sovereign/forex/swap_model.py": "financing model; static table since swap_calibration.json is absent",
    "sovereign/forex/signal_engine.py": "atr_pct + signal frame the incumbent consumes",
    "data/cb_ab/cb_off_trades.csv": "honest population: 350 CB-off trades 2015-2024",
    "data/cb_ab/stats.json": "anchor: cb_off total_r 34.41, n 350",
    "data/research/spot_cache/EURUSD_ohlc.parquet": "path data",
    "data/research/spot_cache/GBPUSD_ohlc.parquet": "path data",
    "data/research/spot_cache/USDJPY_ohlc.parquet": "path data",
    "data/research/spot_cache/AUDUSD_ohlc.parquet": "path data",
    "data/daytrade/SF_FROZEN_001.json": "intraday checkpoint (not the carry incumbent) — must stay untouched",
    "data/daytrade/SF_FROZEN_002.json": "intraday checkpoint (current) — must stay untouched",
    "data/proof/backtest_trades_v015_2015_2024.csv": "sealed 411 — secondary only, contaminated (102 in fabricated CB windows)",
}
ABSENT_MUST_STAY_ABSENT = [
    "data/research/swap_calibration.json",
    "data/execution/calibrated_costs.json",
]
MEASUREMENT_GLOBS = ["scripts/build_*_study.py", "scripts/ruin_engine.py", "scripts/carry_buy_gate.py",
                     "scripts/drawdown_margin.py", "scripts/run_cb_ab.py", "scripts/run_vintage_ab.py",
                     "scripts/eval_lab.py", "scripts/tsmom_backtest.py", "scripts/carry_exit_sprt.py",
                     "scripts/carry_tablebase_paths.py", "scripts/carry_bench.py",
                     "sovereign/forex/forex_backtester.py", "daytrade/ceiling.py", "daytrade/oracle_audit.py",
                     "daytrade/exit_evaluator.py", "daytrade/stack_exam.py", "daytrade/ontology_audit.py",
                     "daytrade/mechanism_fpr_harness.py", "sovereign/forex/exit_tablebase.py"]
SELF_EXCL_RE = re.compile(r"exclude_self|assert_disjoint|grep_sources|tune_sessions|holdout_guard|validate_date_range")


def _grep(pattern: str, paths: list[str]) -> list[str]:
    out = subprocess.run(["grep", "-rn", "--include=*.py", "-E", pattern, *paths],
                         cwd=ROOT, capture_output=True, text=True).stdout
    return [line.split(":")[0] + ":" + line.split(":")[1] for line in out.splitlines() if line]


def engines() -> dict:
    callers_dt = _grep(r"decide_exit\(", ["daytrade", "scripts"])
    callers_fx = _grep(r"exit_machine|decide_exit\(", ["sovereign"])
    ck = {}
    for name in ("SF_FROZEN_001", "SF_FROZEN_002"):
        p = ROOT / "data/daytrade" / f"{name}.json"
        with p.open() as fh:
            j = json.load(fh)
        ck[name] = {"engine_sha256": j.get("engine_sha256"), "file_sha256": inv.sha256_file(p)}
    fp = subprocess.run([sys.executable, "-m", "daytrade.frozen_policy", "verify"], cwd=ROOT,
                        capture_output=True, text=True)
    return {
        "intraday": {"module": "daytrade/stockfish_exit.py", "callers": sorted(set(callers_dt)),
                     "checkpoints": ck, "frozen_policy_verify_rc": fp.returncode},
        "carry": {"module": "sovereign/forex/exit_machine.py", "callers_or_imports": sorted(set(callers_fx)),
                  "checkpoint": "data/carry/CARRY_FROZEN_001.json" if (ROOT / "data/carry/CARRY_FROZEN_001.json").exists() else "ABSENT"},
    }


def population() -> dict:
    d = pd.read_csv(ROOT / "data/cb_ab/cb_off_trades.csv")
    r = d.pnl_pct / d.risk_pct
    with (ROOT / "data/cb_ab/stats.json").open() as fh:
        st = json.load(fh)["cb_off"]
    return {"file": "data/cb_ab/cb_off_trades.csv", "n": int(len(d)),
            "span": [str(d.entry_date.min()), str(d.exit_date.max())],
            "sum_r": round(float(r.sum()), 4), "anchor_total_r": st["total_r"], "anchor_n": st["n"],
            "exit_reasons": d.exit_reason.value_counts().to_dict(),
            "mean_r_by_reason": d.assign(R=r).groupby("exit_reason").R.mean().round(3).to_dict(),
            "entry_date_clusters": int(d.entry_date.nunique()),
            "per_pair": d.pair.value_counts().to_dict()}


def path_data() -> dict:
    out = {}
    for pair in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD"):
        p = ROOT / f"data/research/spot_cache/{pair}_ohlc.parquet"
        df = pd.read_parquet(p)
        idx = pd.to_datetime(df.index)
        gaps = int((idx.to_series().diff().dt.days > 5).sum())
        out[pair] = {"rows": int(len(df)), "start": str(idx.min().date()), "end": str(idx.max().date()),
                     "cols": list(df.columns), "gaps_gt_5d": gaps}
    return out


def fx_state() -> dict:
    p = ROOT / "data/carry/fx_state.jsonl"
    if not p.exists():
        return {"file": str(p.relative_to(ROOT)), "status": "ABSENT"}
    df = pd.read_json(p, lines=True)
    nulls = df.isna().mean()
    return {"file": "data/carry/fx_state.jsonl", "rows": int(len(df)), "cols": int(df.shape[1]),
            "null_cols_pct": {c: round(float(v) * 100, 2) for c, v in nulls.items() if v > 0},
            "date_range": [str(df.session_date.min()), str(df.session_date.max())] if "session_date" in df else None,
            "rows_per_pair": df.pair.value_counts().to_dict() if "pair" in df else None}


def self_exclusion() -> dict:
    out = {}
    for g in MEASUREMENT_GLOBS:
        for p in sorted(ROOT.glob(g)):
            text = p.read_text(errors="replace")
            hits = [i + 1 for i, line in enumerate(text.splitlines()) if SELF_EXCL_RE.search(line)]
            out[str(p.relative_to(ROOT))] = {"asserts": bool(hits), "lines": hits[:5]}
    return out


def sealed() -> dict:
    with (ROOT / "SEALS.json").open() as fh:
        j = json.load(fh)
    matches, mismatches, unhashed = [], [], []

    def walk(node):
        if isinstance(node, dict):
            path = node.get("path") or node.get("file")
            sha = node.get("sha256")
            if path and isinstance(path, str) and (ROOT / path).is_file():
                if sha:
                    (matches if inv.sha256_file(ROOT / path) == sha else mismatches).append(path)
                else:
                    unhashed.append(path)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(j)
    return {"matches": len(matches), "mismatches": mismatches, "unhashed": unhashed}


def tests() -> dict:
    return {t: len(list((ROOT / t).rglob("test_*.py"))) for t in ("daytrade", "scripts", "sovereign", "execution")}


def main() -> int:
    for rel in ABSENT_MUST_STAY_ABSENT:
        if (ROOT / rel).exists():
            print(f"HALT: {rel} exists — the incumbent's cost model was pinned with it ABSENT", file=sys.stderr)
            return 2
    phase0 = {"engines": engines(), "population": population(), "path_data": path_data(),
              "fx_state": fx_state(), "self_exclusion": self_exclusion(), "sealed": sealed(),
              "tests": tests(), "absent_by_design": ABSENT_MUST_STAY_ABSENT}
    hashes = inv.record({k: v for k, v in DEPENDENCIES.items() if (ROOT / k).exists()})
    j = inv.load()
    j["phase0"] = phase0
    inv._write(j)

    e, pop, fx, se, sl = phase0["engines"], phase0["population"], phase0["fx_state"], phase0["self_exclusion"], phase0["sealed"]
    print(f"inventory: {len(hashes)} dependency hashes recorded -> artifacts/inventory.json (HEAD {j['head'][:8]})")
    print(f"intraday engine: {len(e['intraday']['callers'])} decide_exit call sites; frozen_policy verify rc={e['intraday']['frozen_policy_verify_rc']}")
    print(f"carry engine: checkpoint {e['carry']['checkpoint']}")
    print(f"population: n={pop['n']} span {pop['span'][0]}..{pop['span'][1]} sumR={pop['sum_r']} (anchor {pop['anchor_total_r']}, n {pop['anchor_n']}) clusters={pop['entry_date_clusters']}")
    print(f"  exit reasons {pop['exit_reasons']}")
    print(f"  mean R by reason {pop['mean_r_by_reason']}")
    for k, v in phase0["path_data"].items():
        print(f"path {k}: {v['rows']} rows {v['start']}..{v['end']} gaps>5d={v['gaps_gt_5d']}")
    print(f"fx_state: rows={fx.get('rows')} cols={fx.get('cols')} nulls={fx.get('null_cols_pct')}")
    y = [k for k, v in se.items() if v["asserts"]]
    n = [k for k, v in se.items() if not v["asserts"]]
    print(f"self-exclusion: {len(y)} assert, {len(n)} do not: {', '.join(Path(x).name for x in n)}")
    print(f"sealed register: {sl['matches']} match, mismatches={sl['mismatches']}, unhashed={sl['unhashed']}")
    print(f"tests: {phase0['tests']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
