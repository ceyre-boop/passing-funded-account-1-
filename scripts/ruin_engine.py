#!/usr/bin/env python3
"""RUIN ENGINE — survival-probability frontier across risk-per-trade sizing.

WHY THIS EXISTS, AND HOW IT DIFFERS FROM carry_buy_gate.py's P(pass)
----------------------------------------------------------------------
`carry_buy_gate.py` answers "does a REBUY CAMPAIGN clear the funded threshold
within an observation horizon" — a bust just restarts the same phase at the
next trading day, so its P(pass) blends signal quality with retry structure
(exactly SANITY_AUDIT's finding: with enough retries, P(pass) mostly measures
retry cadence, not edge).

This script answers a narrower, structural question that a passing decision
actually needs and campaign P(pass) does not answer: on a SINGLE eval
ATTEMPT sized at risk r (no rebuy), what is P(PASS), P(RUIN) (bust on that
one attempt), and P(OPEN) (still running, unresolved, at the computational
horizon)? Then it sweeps r from 0.10% to 3.00% in 0.05% steps to find where
the frontier turns over — the survival-probability question the task brief
is built from: "signal quality has been the optimized input; bet size
dominates the answer and is unratified."

REUSE, NOT REIMPLEMENTATION
----------------------------
Every mechanic here is imported, not rewritten:
  - `carry_buy_gate.build_series`     -> the R-series (swap haircut, weekend
                                          accounting, per-row risk_pct).
  - `carry_buy_gate.run_phase`        -> bust/pass/unresolved mechanics:
                                          trailing vs static DD, close vs
                                          intraday mark, daily-loss floor
                                          where the contract has one (CTI has
                                          none), min_trading_days, the
                                          pinned refusal to invent TIMEOUT
                                          for a no-deadline contract.
  - `drawdown_margin.walk`            -> the drawdown-path statistics (worst
                                          floor depth vs the contract's own
                                          floor definition, worst
                                          peak-to-trough) for the SAME slice
                                          `run_phase` just walked.
A second implementation of either walk that silently disagreed with the
pinned one would be worse than no second implementation (CLAUDE.md, task
brief). `test_ruin_engine.py::test_dd_walk_agrees_with_run_phase_bust_index`
locks the one place these two pinned functions are asked to agree (the
breach day) so drift between them fails loudly instead of silently.

SAMPLING: BLOCK BOOTSTRAP, NOT IID
------------------------------------
Carry returns are autocorrelated (shared macro/rate-differential drivers
across overlapping holds; regime persistence). IID resampling destroys that
structure and overstates P(pass). This uses the same block convention as
`carry_buy_gate.bootstrap_pass` (`BOOT_BLOCK` calendar-index blocks,
imported, not re-picked).

MONTE CARLO TO CONVERGENCE
----------------------------
Each (firm, risk) cell runs in batches until the Wilson 5-95% interval on
P(PASS) narrows below `--tol` or `--max-paths` is hit — not a fixed path
count. Every reported P(pass)/P(ruin) carries its interval; per spec 021 P5
discipline, no probability is quoted as a bare point.

    python3 scripts/ruin_engine.py --firm cti_1step
    python3 scripts/ruin_engine.py --firm cti_1step --risk 0.01 --max-paths 20000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.carry_buy_gate import (  # noqa: E402
    BOOT_BLOCK, RNG_SEED, build_series, load_oos, load_sealed, run_phase,
    wilson_interval,
)
from scripts.drawdown_margin import walk as dd_walk  # noqa: E402
from sovereign.propfirm.firm_contracts import FirmContract, load_contract  # noqa: E402

OUT_JSON = ROOT / "data" / "agent" / "ruin_engine_frontier.json"
OUT_CHART = ROOT / "data" / "agent" / "ruin_engine_frontier.png"

RISK_LO, RISK_HI, RISK_STEP = 0.0010, 0.0300, 0.0005   # 0.10% .. 3.00%, 0.05% steps
RISK_SWEEP = [round(RISK_LO + RISK_STEP * i, 4)
              for i in range(int(round((RISK_HI - RISK_LO) / RISK_STEP)) + 1)]

DEFAULT_HORIZON_DAYS = 730   # calendar days; COMPUTATIONAL cutoff, not a contract
                              # deadline (every contract here has max_days=null —
                              # a no-deadline eval literally never times out).
                              # "OPEN" below means "not yet resolved by this cutoff",
                              # not "the firm declares it unresolved".

MIN_PATHS = 900
MAX_PATHS = 6000
BATCH = 300
DEFAULT_TOL = 0.015           # stop when the Wilson 5-95% half-width on P(pass) < tol


# ---------------------------------------------------------------- single attempt

def run_single_attempt(vi, vw, vopen, contract: FirmContract, risk: float,
                        horizon_end_i: int) -> dict:
    """One eval attempt, NO rebuy. Walks contract.phases strictly in order via
    the pinned `run_phase` (never reimplemented here). BUST on any phase is
    terminal RUIN — the retry-restart carry_buy_gate.run_campaign performs is
    deliberately absent: this function measures the single-attempt survival
    question, not the campaign question.

    Drawdown-path stats (worst floor depth, worst peak-to-trough) come from
    the pinned `drawdown_margin.walk` run over the exact same slice
    `run_phase` just walked, maxed across phases (each phase resets bal=peak
    to 1.0, matching run_phase's own convention — concatenating phases into
    one walk would silently carry a phase-1 ending balance into phase 2's
    peak, which run_phase itself never does).
    """
    i = 0
    worst_floor = 0.0
    worst_ptt = 0.0
    for phase_idx in range(len(contract.phases)):
        outcome, nxt, _heat = run_phase(vi, vw, vopen, i, contract, phase_idx,
                                        risk, horizon_end_i)
        seg = dd_walk(vi[i:nxt], vw[i:nxt], vopen[i:nxt], contract, risk)
        worst_floor = max(worst_floor, seg["worst_floor_depth"])
        worst_ptt = max(worst_ptt, seg["worst_peak_to_trough"])
        if outcome == "BUST":
            return dict(outcome="RUIN", end_i=nxt, days=nxt - 0,
                        worst_floor=worst_floor, worst_ptt=worst_ptt)
        if outcome == "UNRESOLVED":
            return dict(outcome="OPEN", end_i=nxt, days=nxt - 0,
                        worst_floor=worst_floor, worst_ptt=worst_ptt)
        i = nxt  # PASS -> next phase starts here
    return dict(outcome="PASS", end_i=i, days=i - 0,
                worst_floor=worst_floor, worst_ptt=worst_ptt)


# ------------------------------------------------------------------- sampling

def block_bootstrap_path(vi, vw, vopen, horizon_td: int, rng: np.random.Generator):
    """Same block convention as carry_buy_gate.bootstrap_pass (BOOT_BLOCK,
    imported — one resampling convention, not two)."""
    n = len(vi)
    pi, pw, po = [], [], []
    while len(pi) < horizon_td:
        s = rng.integers(0, n - BOOT_BLOCK)
        pi.extend(vi[s:s + BOOT_BLOCK])
        pw.extend(vw[s:s + BOOT_BLOCK])
        po.extend(vopen[s:s + BOOT_BLOCK])
    return (np.array(pi[:horizon_td]), np.array(pw[:horizon_td]),
            np.array(po[:horizon_td]))


# --------------------------------------------------------------- Monte Carlo

def simulate_risk(vi, vw, vopen, contract: FirmContract, risk: float,
                   horizon_td: int, rng: np.random.Generator,
                   min_paths=MIN_PATHS, max_paths=MAX_PATHS, batch=BATCH,
                   tol=DEFAULT_TOL) -> dict:
    """Monte Carlo one (firm, risk) cell to convergence on P(PASS)'s Wilson
    interval. Returns full outcome counts/intervals, day-to-resolution stats,
    and the drawdown-path distribution (percentiles across paths — reported
    as a distribution, not a bare point, matching every other probability
    here)."""
    n_i = len(vi)
    if n_i <= BOOT_BLOCK:
        raise ValueError("series too short for the block size")

    n_pass = n_ruin = n_open = 0
    days_pass, days_ruin = [], []
    floors, ptts = [], []
    total = 0

    while total < max_paths:
        for _ in range(batch):
            pi, pw, po = block_bootstrap_path(vi, vw, vopen, horizon_td, rng)
            res = run_single_attempt(pi, pw, po, contract, risk, horizon_td)
            total += 1
            floors.append(res["worst_floor"])
            ptts.append(res["worst_ptt"])
            if res["outcome"] == "PASS":
                n_pass += 1
                days_pass.append(res["days"])
            elif res["outcome"] == "RUIN":
                n_ruin += 1
                days_ruin.append(res["days"])
            else:
                n_open += 1
        p_pass = n_pass / total
        lo, hi = wilson_interval(p_pass, total)
        if total >= min_paths and (hi - lo) < 2 * tol:
            break

    p_ruin = n_ruin / total
    ruin_lo, ruin_hi = wilson_interval(p_ruin, total)
    p_open = n_open / total
    pass_lo, pass_hi = wilson_interval(p_pass, total)

    all_days = days_pass + days_ruin
    floors_a = np.array(floors)
    ptts_a = np.array(ptts)

    def pctl(a, q):
        return float(np.percentile(a, q)) if len(a) else None

    return dict(
        risk=risk, n_paths=total,
        p_pass=p_pass, p_pass_lo=pass_lo, p_pass_hi=pass_hi,
        p_ruin=p_ruin, p_ruin_lo=ruin_lo, p_ruin_hi=ruin_hi,
        p_open=p_open,
        mean_days_to_resolution=float(np.mean(all_days)) if all_days else None,
        median_days_to_pass=float(np.median(days_pass)) if days_pass else None,
        median_days_to_ruin=float(np.median(days_ruin)) if days_ruin else None,
        drawdown_floor_depth={
            "p50": pctl(floors_a, 50), "p90": pctl(floors_a, 90),
            "p95": pctl(floors_a, 95), "p99": pctl(floors_a, 99),
            "max": pctl(floors_a, 100), "mean": float(np.mean(floors_a)),
        },
        drawdown_peak_to_trough={
            "p50": pctl(ptts_a, 50), "p90": pctl(ptts_a, 90),
            "p95": pctl(ptts_a, 95), "p99": pctl(ptts_a, 99),
            "max": pctl(ptts_a, 100), "mean": float(np.mean(ptts_a)),
        },
    )


def sweep(vi, vw, vopen, contract, risks, horizon_td, seed=RNG_SEED, **kw):
    rng = np.random.default_rng(seed)
    out = []
    for r in risks:
        out.append(simulate_risk(vi, vw, vopen, contract, r, horizon_td, rng, **kw))
    return out


# ---------------------------------------------------------------- reporting

def print_frontier(rows: list[dict], contract: FirmContract, horizon_days: int):
    print(f"\nRUIN ENGINE — {contract.display_name} — single-attempt frontier "
          f"(horizon {horizon_days}d, computational cutoff, not a contract deadline)")
    print("=" * 100)
    print(f"{'risk':>7} | {'P(pass)':>22} | {'P(ruin)':>22} | {'P(open)':>7} | "
          f"{'E[days]':>7} | {'floor p95':>9} | {'n':>6}")
    print("-" * 100)
    for r in rows:
        pp = f"{r['p_pass']:6.1%} [{r['p_pass_lo']:.1%},{r['p_pass_hi']:.1%}]"
        pr = f"{r['p_ruin']:6.1%} [{r['p_ruin_lo']:.1%},{r['p_ruin_hi']:.1%}]"
        ed = r['mean_days_to_resolution']
        ed_s = f"{ed:7.0f}" if ed is not None else "    n/a"
        fp95 = r['drawdown_floor_depth']['p95']
        fp95_s = f"{fp95:8.2%}" if fp95 is not None else "     n/a"
        print(f"{r['risk']:6.2%} | {pp:>22} | {pr:>22} | {r['p_open']:6.1%} | "
              f"{ed_s} | {fp95_s} | {r['n_paths']:6d}")

    argmax = max(rows, key=lambda r: r["p_pass"])
    print("-" * 100)
    print(f"argmax P(pass): risk={argmax['risk']:.2%}  "
          f"P(pass)={argmax['p_pass']:.1%} [{argmax['p_pass_lo']:.1%},"
          f"{argmax['p_pass_hi']:.1%}]")

    # monotonicity check — reported, not assumed (task brief correction).
    pvals = [r["p_pass"] for r in rows]
    is_monotone = all(pvals[i] <= pvals[i + 1] + 1e-9 for i in range(len(pvals) - 1))
    print(f"monotone non-decreasing over the swept range: {is_monotone}")


def make_chart(rows: list[dict], contract: FirmContract, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    risks = [r["risk"] * 100 for r in rows]
    p_pass = [r["p_pass"] * 100 for r in rows]
    p_pass_lo = [r["p_pass_lo"] * 100 for r in rows]
    p_pass_hi = [r["p_pass_hi"] * 100 for r in rows]
    p_ruin = [r["p_ruin"] * 100 for r in rows]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.fill_between(risks, p_pass_lo, p_pass_hi, alpha=0.2, color="C0",
                    label="P(pass) 5-95% CI")
    ax.plot(risks, p_pass, color="C0", label="P(pass)", linewidth=2)
    ax.plot(risks, p_ruin, color="C3", label="P(ruin)", linewidth=2)
    ax.set_xlabel("risk per trade (% of account per R)")
    ax.set_ylabel("probability (%)")
    ax.set_title(f"Ruin engine — single-attempt frontier — {contract.display_name}")
    ax.legend()
    ax.grid(alpha=0.3)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--firm", default="cti_1step")
    ap.add_argument("--series", choices=("sealed", "oos"), default="sealed")
    ap.add_argument("--risk", type=float, default=None,
                    help="single risk fraction; omit for the full 0.10%%-3.00%% sweep")
    ap.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    ap.add_argument("--min-paths", type=int, default=MIN_PATHS)
    ap.add_argument("--max-paths", type=int, default=MAX_PATHS)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL)
    ap.add_argument("--seed", type=int, default=RNG_SEED)
    ap.add_argument("--no-chart", action="store_true")
    ap.add_argument("--write", action="store_true", help=f"write {OUT_JSON.relative_to(ROOT)}")
    a = ap.parse_args(argv)

    contract = load_contract(a.firm)
    trades = load_sealed() if a.series == "sealed" else load_oos()
    haircut = contract.costs.swap_haircut_r_per_day
    idx, vi, vw, vopen = build_series(trades, haircut, center=False)

    horizon_td = int(a.horizon_days * 5 / 7)
    risks = [a.risk] if a.risk else RISK_SWEEP

    print(f"RUIN ENGINE — {contract.display_name} | series={a.series} n={len(trades)} trades")
    print(f"horizon={a.horizon_days}d ({horizon_td} trading days, computational cutoff) "
          f"| risks swept: {len(risks)} | block={BOOT_BLOCK} | tol={a.tol}")

    rows = sweep(vi, vw, vopen, contract, risks, horizon_td, seed=a.seed,
                min_paths=a.min_paths, max_paths=a.max_paths, batch=a.batch, tol=a.tol)

    print_frontier(rows, contract, a.horizon_days)

    if not a.no_chart:
        make_chart(rows, contract, OUT_CHART)
        print(f"\nchart written: {OUT_CHART.relative_to(ROOT)}")

    if a.write:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(dict(
            firm=a.firm, series=a.series, horizon_days=a.horizon_days,
            horizon_trading_days=horizon_td, seed=a.seed, rows=rows,
        ), indent=1))
        print(f"state written: {OUT_JSON.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
