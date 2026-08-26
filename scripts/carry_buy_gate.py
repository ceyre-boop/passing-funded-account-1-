#!/usr/bin/env python3
"""Carry buy gate — the ONE campaign evaluator. Spec 021; plan Plans/soft-churning-pelican.md.

Replaces eval_lab.py / eval_lab_carry_fix.py / instant_pro_sim.py /
colin_v2_campaign_sim.py / oos_campaign_test.py's campaign section.

Pre-registered mechanics (spec 021 P4/P5 — do not tune after reading results):
- Per-trade R = pnl_pct / that row's OWN risk_pct (the sealed CSV holds two values).
- Daily-increment engine: each trade credits R (minus swap haircut x hold_days) at its
  exit date on a business-day calendar; `worst` = the day's increment clipped at 0 as
  the intraday adverse proxy. Concurrent trades sum into the same day (correlation).
- Contract mark semantics: `close` marks bust checks on end-of-day balance; `intraday`
  marks them on the worst proxy.
- A "trading day" for min_trading_days = a day with any open position or any close.
- No deadline in the contract => TIMEOUT is not a legal outcome. Campaigns are scored
  at fixed OBSERVATION horizons (365/730 calendar days) from starts with full runway;
  an unfinished campaign is "not yet passed at horizon", never a bust.
- Bust => rebuy, same policy, fee counted, phase restarts next trading day.
- Zero-edge control: identical campaign on the mean-centered (post-haircut) R series.
  Every reported number carries real AND control; the formatter refuses one without
  the other.
- G1 golden gate runs first on every invocation; scoring any non-sealed series
  without G1 green in the same process is a hard exit (spec P7).
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sovereign.propfirm.firm_contracts import FirmContract, load_contract  # noqa: E402

SEALED_CSV = ROOT / "data" / "proof" / "backtest_trades_v015_2015_2024.csv"
OOS_JSON = ROOT / "data" / "oos_trades_2025_2026.json"
PAPER_JSONL = ROOT / "data" / "trade_logs" / "paper_carry_trades.jsonl"
REPRO_REPORT = ROOT / "data" / "agent" / "repro_gap_report.json"
STATE_PATH = ROOT / "data" / "agent" / "carry_buy_gate_state.json"

RNG_SEED = 42
RISK_SWEEP = [0.010, 0.015, 0.020, 0.025, 0.030]   # P3
START_STEP_TDAYS = 3                                # P5
BOOT_BLOCK = 20                                     # P5
BOOT_PATHS = 2000                                   # P5
OBS_HORIZONS_DAYS = (365, 730)                      # P4 observation horizons

# G1 golden numbers, measured from the sealed CSV itself (spec P6).
GOLDEN = dict(n=411, avg_r=0.3556, avg_r_tol=0.005, wr=0.4866, wr_tol=0.005,
              median_hold=5, weekend_frac=0.7226, weekend_tol=0.01)

# G5 band (spec P6).
G5_MIN_N = 80
G5_R_BAND = 0.25


# ---------------------------------------------------------------- series loading

def load_sealed():
    trades = []
    with open(SEALED_CSV) as f:
        for row in csv.DictReader(f):
            trades.append(dict(
                pair=row["pair"],
                entry=row["entry_date"][:10],
                exit=row["exit_date"][:10],
                R=float(row["pnl_pct"]) / float(row["risk_pct"]),
                hold=int(row["hold_days"]),
            ))
    return trades


def load_oos():
    raw = json.load(open(OOS_JSON))
    return [dict(pair=None, entry=t["entry_date"], exit=t["exit_date"],
                 R=float(t["R"]), hold=int(t["hold_days"])) for t in raw]


def crosses_weekend(entry: str, exit_: str) -> bool:
    d0, d1 = datetime.fromisoformat(entry), datetime.fromisoformat(exit_)
    d = d0
    while d < d1:
        if d.weekday() == 4:
            return True
        d += timedelta(days=1)
    return False


# ---------------------------------------------------------------------- gate G1

def golden_gate(trades) -> dict:
    """Raw replay of the sealed series: no haircut, per-row risk_pct."""
    rs = [t["R"] for t in trades]
    holds = sorted(t["hold"] for t in trades)
    stats = dict(
        n=len(trades),
        avg_r=float(np.mean(rs)),
        wr=float(np.mean([r > 0 for r in rs])),
        median_hold=float(np.median(holds)),
        weekend_frac=float(np.mean([crosses_weekend(t["entry"], t["exit"]) for t in trades])),
    )
    checks = dict(
        n=stats["n"] == GOLDEN["n"],
        avg_r=abs(stats["avg_r"] - GOLDEN["avg_r"]) <= GOLDEN["avg_r_tol"],
        wr=abs(stats["wr"] - GOLDEN["wr"]) <= GOLDEN["wr_tol"],
        median_hold=stats["median_hold"] == GOLDEN["median_hold"],
        weekend=abs(stats["weekend_frac"] - GOLDEN["weekend_frac"]) <= GOLDEN["weekend_tol"],
    )
    return dict(status="GREEN" if all(checks.values()) else "RED",
                measured=stats, checks=checks)


# ---------------------------------------------------------------------- gate G4

def fit_gate(trades, contract: FirmContract) -> dict:
    violations = []
    for i, t in enumerate(trades):
        if t["hold"] >= 1 and not contract.permissions["overnight_hold"]:
            violations.append(dict(idx=i, entry=t["entry"], why="overnight hold forbidden"))
        elif crosses_weekend(t["entry"], t["exit"]) and not contract.permissions["weekend_hold"]:
            violations.append(dict(idx=i, entry=t["entry"], why="weekend hold forbidden"))
    return dict(status="GREEN" if not violations else "RED",
                n_checked=len(trades), violations=violations)


# ------------------------------------------------------------------ the engine

def build_series(trades, haircut_r_per_day: float, center: bool):
    """Business-day inc/worst series + per-day open-risk-unit count."""
    d0 = min(t["entry"] for t in trades)
    d1 = max(t["exit"] for t in trades)
    idx = pd.bdate_range(d0, d1)
    inc = pd.Series(0.0, index=idx)
    open_units = pd.Series(0, index=idx)
    rs = [t["R"] - haircut_r_per_day * max(t["hold"], 1) for t in trades]
    if center:
        m = float(np.mean(rs))
        rs = [r - m for r in rs]
    for t, r in zip(trades, rs):
        xd = pd.Timestamp(t["exit"])
        pos = idx.searchsorted(xd)
        if pos >= len(idx):
            pos = len(idx) - 1
        inc.iloc[pos] += r
        lo = idx.searchsorted(pd.Timestamp(t["entry"]))
        open_units.iloc[lo:pos + 1] += 1
    worst = inc.clip(upper=0.0)
    return idx, inc.values, worst.values, open_units.values


def run_phase(vi, vw, vopen, i0, contract: FirmContract, phase_idx, risk, horizon_end_i):
    """Walk one phase from index i0. Returns (outcome, next_i, heat_days).

    outcome: 'PASS' | 'BUST' | 'UNRESOLVED' (observation horizon reached).
    """
    phase = contract.phases[phase_idx]
    daily = contract.daily_dd
    dd = contract.max_dd
    bal = peak = 1.0
    target = 1.0 + phase.target_pct
    trading_days = 0
    heat = 0
    if phase.max_days is not None:
        raise AssertionError(
            f"{contract.key} phase {phase_idx} has a deadline; the no-deadline lane "
            "was pre-registered — refusing to invent TIMEOUT semantics (spec P4)")
    for i in range(i0, horizon_end_i):
        day_start = bal
        intraday = bal * (1.0 + risk * vw[i])
        close = bal * (1.0 + risk * vi[i])
        if vopen[i] > 0 or vi[i] != 0.0:
            trading_days += 1
        if daily is not None:
            if vopen[i] * risk > daily.pct:
                heat += 1
            budget = daily.pct  # fraction of account (basis: balance)
            floor = day_start - budget
            marked = intraday if daily.mark == "intraday" else close
            if marked <= floor:
                return "BUST", i + 1, heat
        dd_base = peak if dd.type == "trailing" else 1.0
        dd_floor = dd_base * (1.0 - dd.pct)
        marked = intraday if dd.mark == "intraday" else close
        if marked <= dd_floor:
            return "BUST", i + 1, heat
        bal = close
        peak = max(peak, bal)
        if bal >= target and trading_days >= phase.min_trading_days:
            return "PASS", i + 1, heat
    return "UNRESOLVED", horizon_end_i, heat


def run_campaign(vi, vw, vopen, i0, contract, risk, horizon_end_i):
    """Attempts until funded or horizon. Returns dict."""
    i = i0
    evals = 1
    heat_days = 0
    phase_idx = 0
    while i < horizon_end_i:
        outcome, i, heat = run_phase(vi, vw, vopen, i, contract, phase_idx, risk, horizon_end_i)
        heat_days += heat
        if outcome == "PASS":
            phase_idx += 1
            if phase_idx == len(contract.phases):
                return dict(passed=True, evals=evals, end_i=i, heat_days=heat_days)
        elif outcome == "BUST":
            evals += 1
            phase_idx = 0
        else:
            break
    return dict(passed=False, evals=evals, end_i=horizon_end_i, heat_days=heat_days)


def wilson_interval(p, n, z=1.6449):  # 5–95%
    if n == 0:
        return 0.0, 1.0
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def campaign_sweep(idx, vi, vw, vopen, contract, risk, horizon_days):
    horizon_td = int(horizon_days * 5 / 7)
    starts = [i for i in range(0, len(vi) - horizon_td, START_STEP_TDAYS)]
    if not starts:
        return None
    results = [run_campaign(vi, vw, vopen, i, contract, risk, i + horizon_td) for i in starts]
    passed = [r for r in results if r["passed"]]
    p = len(passed) / len(results)
    lo, hi = wilson_interval(p, len(results))
    days = sorted((idx[r["end_i"] - 1] - idx[starts[j]]).days
                  for j, r in enumerate(results) if r["passed"]) or [None]
    evals = sorted(r["evals"] for r in results)
    fee, refund = contract.costs.fee_usd, contract.costs.refund_on_pass
    costs = sorted(r["evals"] * fee - (fee * refund if r["passed"] else 0.0) for r in results)

    def pct(v, q):
        return v[min(len(v) - 1, int(q * len(v)))] if v and v[0] is not None else None

    return dict(
        n_starts=len(results), p_pass=p, p_pass_lo=lo, p_pass_hi=hi,
        median_days=pct(days, 0.5), p10_days=pct(days, 0.10), p90_days=pct(days, 0.90),
        mean_evals=float(np.mean(evals)), mean_cost=float(np.mean(costs)),
        heat_days=int(sum(r["heat_days"] for r in results)),
    )


def bootstrap_pass(vi, vw, vopen, contract, risk, horizon_days, seed=RNG_SEED):
    rng = np.random.default_rng(seed)
    horizon_td = int(horizon_days * 5 / 7)
    hits = 0
    n = len(vi)
    if n <= BOOT_BLOCK:
        return None
    for _ in range(BOOT_PATHS):
        pi, pw, po = [], [], []
        while len(pi) < horizon_td:
            s = rng.integers(0, n - BOOT_BLOCK)
            pi.extend(vi[s:s + BOOT_BLOCK])
            pw.extend(vw[s:s + BOOT_BLOCK])
            po.extend(vopen[s:s + BOOT_BLOCK])
        r = run_campaign(np.array(pi[:horizon_td]), np.array(pw[:horizon_td]),
                         np.array(po[:horizon_td]), 0, contract, risk, horizon_td)
        hits += r["passed"]
    p = hits / BOOT_PATHS
    lo, hi = wilson_interval(p, BOOT_PATHS)
    return dict(p_pass=p, p_pass_lo=lo, p_pass_hi=hi, n_paths=BOOT_PATHS)


# ------------------------------------------------------------------- formatter

def fmt_row(label, real, control):
    """Refuses to format a real number without its zero-edge control (spec P5)."""
    if real is None or control is None:
        raise ValueError(f"{label}: real and zero-edge control are both mandatory; "
                         f"got real={real is not None}, control={control is not None}")
    return (f"{label:>26} | REAL p(pass) {real['p_pass']:6.1%} "
            f"[{real['p_pass_lo']:.1%},{real['p_pass_hi']:.1%}] | "
            f"ZERO-EDGE {control['p_pass']:6.1%} "
            f"[{control['p_pass_lo']:.1%},{control['p_pass_hi']:.1%}]")


def max_safe_risk_line(trades, contract, haircut: float, risks: list[float]) -> str:
    """Loud line naming the realized-curve max-safe-risk beside whatever risk
    was actually selected for this run (drawdown_margin.max_safe_risk, spec-
    adjacent — see scripts/drawdown_margin.py docstring for why this differs
    from the P5 ladder statistics and is not a p_pass number needing a
    zero-edge control)."""
    # Deferred import: drawdown_margin imports build_series/load_sealed/load_oos
    # from this module, so importing it at module scope would be circular.
    from scripts.drawdown_margin import max_safe_risk

    idx, vi, vw, vopen = build_series(trades, haircut, center=False)
    safe = max_safe_risk(vi, vw, vopen, contract)
    risk_desc = ", ".join(f"{r:.2%}" for r in risks)
    flag = " ** SELECTED RISK EXCEEDS MAX SAFE RISK **" if any(r > safe for r in risks) else ""
    return (f"MAX SAFE RISK (realized curve, {contract.key}): {safe:.3%} per R  |  "
            f"selected risk(s): {risk_desc}{flag}")


# ----------------------------------------------------------------- gates G2/G5

def repro_gate() -> dict:
    if not REPRO_REPORT.exists():
        return dict(status="RED", why="no diagnosis report; run scripts/diagnose_repro_gap.py")
    rep = json.load(open(REPRO_REPORT))
    ok = rep.get("status") in ("ATTRIBUTED", "WAIVED")
    return dict(status="GREEN" if ok else "RED", report=rep.get("status"),
                asof=rep.get("timestamp"))


def paper_gate() -> dict:
    if not PAPER_JSONL.exists():
        return dict(status="RED", n=0, need=G5_MIN_N, why="paper log not started")
    closed = []
    with open(PAPER_JSONL) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("status") == "closed" and rec.get("R") is not None:
                closed.append(float(rec["R"]))
    n = len(closed)
    if n < G5_MIN_N:
        return dict(status="RED", n=n, need=G5_MIN_N,
                    why=f"paper sprint incomplete (n={n}/{G5_MIN_N})")
    mean_r = float(np.mean(closed))
    in_band = abs(mean_r - GOLDEN["avg_r"]) <= G5_R_BAND
    return dict(status="GREEN" if in_band else "RED", n=n, mean_r=mean_r,
                band=[GOLDEN["avg_r"] - G5_R_BAND, GOLDEN["avg_r"] + G5_R_BAND])


# ------------------------------------------------------------------------ main

def git_rev():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser(description="Carry buy gate evaluator (spec 021)")
    ap.add_argument("--series", default="sealed", help="sealed | oos | /path/to/series.json")
    ap.add_argument("--firm", default="cti_1step")
    ap.add_argument("--risk", type=float, default=None,
                    help="single risk fraction; omit for the pre-registered sweep")
    ap.add_argument("--update-state", action="store_true",
                    help=f"write {STATE_PATH.relative_to(ROOT)}")
    args = ap.parse_args()

    contract = load_contract(args.firm)
    sealed = load_sealed()

    # G1 first, every invocation (spec P7).
    g1 = golden_gate(sealed)
    print(f"G1 GOLDEN: {g1['status']}  measured={ {k: round(v, 4) for k, v in g1['measured'].items()} }")
    if g1["status"] != "GREEN":
        print("G1 RED — sealed replay does not reproduce the pre-registered stats. "
              "Nothing else may be scored.", file=sys.stderr)
        sys.exit(2)

    if args.series == "sealed":
        trades, series_label = sealed, "sealed 2015-2024"
    elif args.series == "oos":
        trades, series_label = load_oos(), "TRUE OOS 2025-2026 (degraded-rig artifact; G2 applies)"
    else:
        p = Path(args.series)
        if not p.exists():
            print(f"series not found: {p}", file=sys.stderr)
            sys.exit(2)
        raw = json.load(open(p))
        trades = [dict(pair=None, entry=t["entry_date"], exit=t["exit_date"],
                       R=float(t["R"]), hold=int(t["hold_days"])) for t in raw]
        series_label = str(p)

    g4 = fit_gate(sealed, contract)
    g2 = repro_gate()
    g5 = paper_gate()

    haircut = contract.costs.swap_haircut_r_per_day
    risks = [args.risk] if args.risk else RISK_SWEEP
    watermark = ("" if g5["status"] == "GREEN"
                 else f"  ** paper sprint incomplete (n={g5.get('n', 0)}/{G5_MIN_N}) **")

    print(f"\n=== {contract.display_name} | series: {series_label}{watermark} ===")
    print(max_safe_risk_line(trades, contract, haircut, risks))
    tables = {}
    for horizon in OBS_HORIZONS_DAYS:
        print(f"\n-- observation horizon {horizon}d (not a contract deadline) --")
        for risk in risks:
            idx_r, vi_r, vw_r, vo_r = build_series(trades, haircut, center=False)
            idx_c, vi_c, vw_c, vo_c = build_series(trades, haircut, center=True)
            real = campaign_sweep(idx_r, vi_r, vw_r, vo_r, contract, risk, horizon)
            ctrl = campaign_sweep(idx_c, vi_c, vw_c, vo_c, contract, risk, horizon)
            if real is None or ctrl is None:
                print(f"{risk:5.2%} | series too short for a {horizon}d horizon — skipped")
                continue
            print(fmt_row(f"start-sweep {risk:.2%}", real, ctrl))
            print(f"{'':>26} | median {real['median_days']}d, evals E[{real['mean_evals']:.2f}], "
                  f"E[$ {real['mean_cost']:.0f}] | control evals E[{ctrl['mean_evals']:.2f}]")
            breal = bootstrap_pass(vi_r, vw_r, vo_r, contract, risk, horizon)
            bctrl = bootstrap_pass(vi_c, vw_c, vo_c, contract, risk, horizon)
            if breal and bctrl:
                print(fmt_row(f"bootstrap {risk:.2%}", breal, bctrl))
            tables[f"{horizon}d@{risk:.4f}"] = dict(real=real, control=ctrl,
                                                    boot_real=breal, boot_control=bctrl)

    # G3 is pre-registered as an OOS comparison (spec P6) — an in-sample run must
    # not turn it green, whatever its intervals say.
    g3 = dict(status="RED", why="G3 requires --series oos (pre-registered as an OOS test)")
    for key, t in tables.items() if args.series == "oos" else ():
        br, bc = t.get("boot_real"), t.get("boot_control")
        if br and bc:
            beats = br["p_pass_lo"] > bc["p_pass_hi"]
            g3 = dict(status="GREEN" if beats else "RED", at=key,
                      real_lo=br["p_pass_lo"], control_hi=bc["p_pass_hi"],
                      series=series_label)
            if beats:
                break

    gates = dict(G1=g1, G2=g2, G3=g3, G4=g4, G5=g5)
    verdict = "BUY" if all(g["status"] == "GREEN" for g in gates.values()) else "NOT READY"
    print(f"\nGates: " + "  ".join(f"{k}:{v['status']}" for k, v in gates.items()))
    print(f"VERDICT: {verdict}")

    if args.update_state:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state = dict(timestamp=datetime.now().isoformat(timespec="seconds"),
                     evaluator_rev=git_rev(), spec="021", firm=args.firm,
                     series=series_label, gates=gates, verdict=verdict, tables=tables)
        STATE_PATH.write_text(json.dumps(state, indent=1, default=str))
        print(f"state written: {STATE_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
