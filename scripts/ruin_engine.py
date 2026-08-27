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
imported, not re-picked) for the edge AND for both controls below — a
control sampled IID against a block-bootstrapped edge would not be a fair
comparison, it would be a rigged one.

CONTROLS: THE NUMBER THAT MAKES THIS HONEST
----------------------------------------------
`--control random`: same contract, sizing, costs, and holding-period calendar
as the real series, but every trade's R is replaced with a genuine
zero-expectancy coinflip (+-1R, p=0.5) — thin-tailed, easy to beat.

`--control shuffled`: keeps each trade's own REAL |R| magnitude (so the fat
tails survive exactly as they are in the sealed series) but randomly
permutes which trade gets which sign, destroying any correlation between
timing/magnitude and direction that produced the edge's positive mean. This
is the sharper of the two controls per the task brief: a fat-tailed
zero-expectancy series is a much harder thing to beat than a coinflip, and if
the edge's P(pass) sits inside it, sizing is passing the eval, not edge.

Both controls are built ONCE per run (`make_control_trades`, seeded) — fixed
series, exactly like the edge series is fixed — and then Monte Carlo'd with
COMMON RANDOM NUMBERS: every sampled path draws ONE set of block-start
indices and applies it to the edge and to every requested control
(`simulate_frontier`). This removes pure luck-of-the-draw noise from the
edge-vs-control comparison, so any interval separation that remains is
attributable to signal, not to which paths got sampled.

`fmt_control_row` mirrors `carry_buy_gate.fmt_row`'s "takes both or raises"
discipline: it is structurally impossible to print an edge P(pass) number
without a control number on the same line.

MONTE CARLO TO CONVERGENCE
----------------------------
Each (firm, risk) cell runs in batches until the Wilson 5-95% interval on
P(PASS) narrows below `--tol` or `--max-paths` is hit — not a fixed path
count. Every reported P(pass)/P(ruin) carries its interval; per spec 021 P5
discipline, no probability is quoted as a bare point.

    python3 scripts/ruin_engine.py --firm cti_1step
    python3 scripts/ruin_engine.py --firm cti_1step --risk 0.01 --max-paths 20000
    python3 scripts/ruin_engine.py --firm cti_1step --control shuffled
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

def _block_starts(n: int, horizon_td: int, rng: np.random.Generator) -> list[int]:
    """The block-START index sequence only (BOOT_BLOCK-sized draws from a
    series of length n, imported convention). Factored out of
    `block_bootstrap_path` so the SAME sequence can be replayed against
    several equal-length series in one Monte Carlo iteration — that replay
    is what makes the edge-vs-control comparison common-random-numbers fair
    rather than independently noisy."""
    starts = []
    covered = 0
    while covered < horizon_td:
        starts.append(int(rng.integers(0, n - BOOT_BLOCK)))
        covered += BOOT_BLOCK
    return starts


def _path_from_starts(vi, vw, vopen, starts: list[int], horizon_td: int):
    pi, pw, po = [], [], []
    for s in starts:
        pi.extend(vi[s:s + BOOT_BLOCK])
        pw.extend(vw[s:s + BOOT_BLOCK])
        po.extend(vopen[s:s + BOOT_BLOCK])
    return (np.array(pi[:horizon_td]), np.array(pw[:horizon_td]),
            np.array(po[:horizon_td]))


def block_bootstrap_path(vi, vw, vopen, horizon_td: int, rng: np.random.Generator):
    """Same block convention as carry_buy_gate.bootstrap_pass (BOOT_BLOCK,
    imported — one resampling convention, not two)."""
    starts = _block_starts(len(vi), horizon_td, rng)
    return _path_from_starts(vi, vw, vopen, starts, horizon_td)


# ------------------------------------------------------------------- controls

CONTROL_KINDS = ("random", "shuffled")


def make_control_trades(trades: list[dict], kind: str,
                        rng: np.random.Generator) -> list[dict]:
    """Build a control trade list: identical entry/exit/hold (same calendar,
    same weekend/hold accounting, same swap-haircut day-count) as `trades`,
    with only the R value replaced.

    "random": a genuine zero-expectancy coinflip, +-1R at p=0.5, independent
    per trade. Thin-tailed by construction — the EASY control.

    "shuffled": each trade keeps its OWN real |R| magnitude (so the fat-tail
    empirical distribution survives exactly), but the SIGN sequence is
    permuted across trades — the pairing between a trade's timing/magnitude
    and its direction (the thing that makes +0.3556 avg R an edge and not
    noise) is destroyed. Thick-tailed, zero-expectancy-in-construction — the
    HARD control (task brief: harder to beat than a coinflip).

    Built ONCE per run (fixed series), exactly like the real series is fixed
    — only the Monte Carlo block-sampling downstream is randomized per path.
    """
    if kind == "random":
        rs = rng.choice(np.array([-1.0, 1.0]), size=len(trades))
    elif kind == "shuffled":
        real_r = np.array([t["R"] for t in trades], dtype=float)
        mags = np.abs(real_r)
        signs = np.sign(real_r)
        rng.shuffle(signs)   # permutes the SIGN LABELS only; each trade keeps
                              # its own magnitude, dates, and hold days.
        rs = mags * signs
    else:
        raise ValueError(f"unknown control kind {kind!r}; expected one of {CONTROL_KINDS}")
    return [dict(t, R=float(r)) for t, r in zip(trades, rs)]


# --------------------------------------------------------------- Monte Carlo

def _pctl(a, q):
    return float(np.percentile(a, q)) if len(a) else None


def _finalize(c: dict, total: int, risk: float) -> dict:
    """Turn one series' accumulated per-path counts into the reported shape
    (probabilities with Wilson intervals, resolution-day stats, drawdown-path
    percentile distribution). One formatter, used identically for the edge
    and every control — a control that skipped this would not be a fair
    comparison either."""
    p_pass = c["pass_"] / total
    p_ruin = c["ruin_"] / total
    p_open = c["open_"] / total
    pass_lo, pass_hi = wilson_interval(p_pass, total)
    ruin_lo, ruin_hi = wilson_interval(p_ruin, total)
    all_days = c["days_pass"] + c["days_ruin"]
    floors_a = np.array(c["floors"])
    ptts_a = np.array(c["ptts"])
    return dict(
        risk=risk, n_paths=total,
        p_pass=p_pass, p_pass_lo=pass_lo, p_pass_hi=pass_hi,
        p_ruin=p_ruin, p_ruin_lo=ruin_lo, p_ruin_hi=ruin_hi,
        p_open=p_open,
        mean_days_to_resolution=float(np.mean(all_days)) if all_days else None,
        median_days_to_pass=float(np.median(c["days_pass"])) if c["days_pass"] else None,
        median_days_to_ruin=float(np.median(c["days_ruin"])) if c["days_ruin"] else None,
        drawdown_floor_depth={
            "p50": _pctl(floors_a, 50), "p90": _pctl(floors_a, 90),
            "p95": _pctl(floors_a, 95), "p99": _pctl(floors_a, 99),
            "max": _pctl(floors_a, 100),
            "mean": float(np.mean(floors_a)) if len(floors_a) else None,
        },
        drawdown_peak_to_trough={
            "p50": _pctl(ptts_a, 50), "p90": _pctl(ptts_a, 90),
            "p95": _pctl(ptts_a, 95), "p99": _pctl(ptts_a, 99),
            "max": _pctl(ptts_a, 100),
            "mean": float(np.mean(ptts_a)) if len(ptts_a) else None,
        },
    )


def simulate_frontier(series_map: dict[str, tuple], contract: FirmContract,
                      risk: float, horizon_td: int, rng: np.random.Generator,
                      min_paths=MIN_PATHS, max_paths=MAX_PATHS, batch=BATCH,
                      tol=DEFAULT_TOL, target_key: str = "edge") -> dict[str, dict]:
    """Monte Carlo one (firm, risk) cell for one or more equal-length series
    at once, using COMMON RANDOM NUMBERS: every path draws ONE block-start
    sequence (`_block_starts`) and replays it against every series in
    `series_map`. All series must share the same business-day length — true
    by construction, since a control differs from the edge only in its R
    values, never in its dates.

    Convergence is judged on `target_key`'s (default "edge") P(pass) Wilson
    interval — the other series ride along on the same paths, so they
    typically converge at least as tightly.

    Returns {key: result_dict} with the exact per-series shape `_finalize`
    produces — every probability carries its interval (spec 021 P5
    discipline, reused here)."""
    keys = list(series_map)
    if not keys:
        raise ValueError("simulate_frontier requires at least one series")
    n = len(series_map[keys[0]][0])
    if n <= BOOT_BLOCK:
        raise ValueError("series too short for the block size")
    for k in keys[1:]:
        if len(series_map[k][0]) != n:
            raise ValueError(
                f"series length mismatch: {k!r} has {len(series_map[k][0])} days, "
                f"{keys[0]!r} has {n} — common random numbers require equal-length "
                f"series (edge and controls must share the same trade calendar)")
    if target_key not in series_map:
        raise ValueError(f"target_key {target_key!r} not in series_map keys {keys}")

    counts = {k: dict(pass_=0, ruin_=0, open_=0, days_pass=[], days_ruin=[],
                      floors=[], ptts=[]) for k in keys}
    total = 0
    while total < max_paths:
        for _ in range(batch):
            starts = _block_starts(n, horizon_td, rng)
            total += 1
            for k in keys:
                vi, vw, vopen = series_map[k]
                pi, pw, po = _path_from_starts(vi, vw, vopen, starts, horizon_td)
                res = run_single_attempt(pi, pw, po, contract, risk, horizon_td)
                c = counts[k]
                c["floors"].append(res["worst_floor"])
                c["ptts"].append(res["worst_ptt"])
                if res["outcome"] == "PASS":
                    c["pass_"] += 1
                    c["days_pass"].append(res["days"])
                elif res["outcome"] == "RUIN":
                    c["ruin_"] += 1
                    c["days_ruin"].append(res["days"])
                else:
                    c["open_"] += 1
        p_pass_target = counts[target_key]["pass_"] / total
        lo, hi = wilson_interval(p_pass_target, total)
        if total >= min_paths and (hi - lo) < 2 * tol:
            break

    return {k: _finalize(counts[k], total, risk) for k in keys}


def simulate_risk(vi, vw, vopen, contract: FirmContract, risk: float,
                   horizon_td: int, rng: np.random.Generator, **kw) -> dict:
    """Single-series Monte Carlo (the edge alone, no controls) — a thin
    wrapper over `simulate_frontier` with a one-key series map, kept for
    call sites and tests that only need the edge."""
    return simulate_frontier({"edge": (vi, vw, vopen)}, contract, risk,
                             horizon_td, rng, target_key="edge", **kw)["edge"]


def sweep(vi, vw, vopen, contract, risks, horizon_td, seed=RNG_SEED, **kw):
    rng = np.random.default_rng(seed)
    out = []
    for r in risks:
        out.append(simulate_risk(vi, vw, vopen, contract, r, horizon_td, rng, **kw))
    return out


def sweep_frontier(series_map: dict[str, tuple], contract, risks, horizon_td,
                   seed=RNG_SEED, target_key="edge", **kw) -> list[dict[str, dict]]:
    """Sweep the full risk grid, returning one {key: result} dict per risk —
    the multi-series (edge + controls) counterpart of `sweep`."""
    rng = np.random.default_rng(seed)
    out = []
    for r in risks:
        out.append(simulate_frontier(series_map, contract, r, horizon_td, rng,
                                     target_key=target_key, **kw))
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

    # The peak is SOFT — every risk whose interval overlaps the argmax's is
    # statistically indistinguishable from it. Report the plateau, not a
    # point; treating argmax as a precise optimum repeats the over-fitting
    # this measurement exists to correct.
    plateau = [r["risk"] for r in rows if r["p_pass_hi"] >= argmax["p_pass_lo"]]
    if len(plateau) > 1:
        print(f"plateau (risks whose P(pass) CI overlaps the argmax's, "
              f"i.e. statistically indistinguishable from it): "
              f"{min(plateau):.2%} .. {max(plateau):.2%} "
              f"({len(plateau)} of {len(rows)} swept risks) — the peak is a "
              f"plateau, not a point.")

    # monotonicity check — reported, not assumed (task brief correction).
    pvals = [r["p_pass"] for r in rows]
    is_monotone = all(pvals[i] <= pvals[i + 1] + 1e-9 for i in range(len(pvals) - 1))
    print(f"monotone non-decreasing over the swept range: {is_monotone}")


# ------------------------------------------------------------- control reporting

def fmt_control_row(risk: float, edge: dict | None, control: dict | None,
                    control_label: str) -> str:
    """Prints the edge's P(pass) and ONE control's P(pass) on the SAME LINE.
    Mirrors carry_buy_gate.fmt_row's "takes both or raises" discipline
    exactly: it is structurally impossible to call this with only one side
    present. A missing control is not an omission you can print around —
    it's a bug, and this raises instead of silently formatting half a
    comparison."""
    if edge is None or control is None:
        raise ValueError(
            f"fmt_control_row: edge and {control_label} control are both "
            f"mandatory; got edge={edge is not None}, control={control is not None}")
    ep = f"{edge['p_pass']:6.1%} [{edge['p_pass_lo']:.1%},{edge['p_pass_hi']:.1%}]"
    cp = f"{control['p_pass']:6.1%} [{control['p_pass_lo']:.1%},{control['p_pass_hi']:.1%}]"
    separates = edge["p_pass_lo"] > control["p_pass_hi"]
    flag = "  SEPARATES" if separates else ""
    return (f"{risk:6.2%} | EDGE {ep} | {control_label.upper():>8} {cp}{flag}")


def separation_summary(rows: list[dict], edge_key: str, control_key: str) -> dict:
    """First risk (in sweep order) where the edge's P(pass) 5-95% lower bound
    clears the control's upper bound — same interval-separation test as
    carry_buy_gate's G3. If none separate, says so plainly; that is reported
    as a finding, not softened into "mostly separates" or omitted."""
    for row in rows:
        edge, ctrl = row[edge_key], row[control_key]
        if edge["p_pass_lo"] > ctrl["p_pass_hi"]:
            return dict(separates=True, risk=edge["risk"],
                       edge_lo=edge["p_pass_lo"], control_hi=ctrl["p_pass_hi"])
    return dict(separates=False, risk=None, edge_lo=None, control_hi=None)


def print_frontier_with_controls(rows: list[dict[str, dict]], contract: FirmContract,
                                 horizon_days: int, control_keys: list[str]):
    print(f"\nRUIN ENGINE — {contract.display_name} — edge vs control(s) "
          f"(horizon {horizon_days}d, computational cutoff, common random numbers)")
    print("=" * 100)
    for ck in control_keys:
        print(f"\n-- edge vs {ck} control --")
        for row in rows:
            print(fmt_control_row(row["edge"]["risk"], row["edge"], row[ck], ck))
        summary = separation_summary(rows, "edge", ck)
        print("-" * 100)
        if summary["separates"]:
            print(f"edge separates from {ck} at risk={summary['risk']:.2%}: "
                  f"edge P(pass) lower bound {summary['edge_lo']:.1%} > "
                  f"{ck} P(pass) upper bound {summary['control_hi']:.1%}")
        else:
            print(f"edge NEVER separates from {ck} over the swept range "
                  f"(0.10%-3.00%) — the edge's P(pass) interval overlaps the "
                  f"{ck} control's at every risk tested. Stated plainly, not "
                  f"softened: at these sample sizes and this horizon, sizing "
                  f"cannot be shown to beat a {ck} zero-expectancy series.")


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


def make_chart_with_controls(rows: list[dict[str, dict]], contract: FirmContract,
                             control_keys: list[str], path: Path):
    """Three (or two) curves together: edge P(pass) plus each control's
    P(pass), so the separation question has a picture, not just a table."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"edge": "C0", "random": "C1", "shuffled": "C3"}
    risks = [r["edge"]["risk"] * 100 for r in rows]

    fig, ax = plt.subplots(figsize=(10, 6))
    for key in ["edge"] + control_keys:
        p_pass = [r[key]["p_pass"] * 100 for r in rows]
        p_lo = [r[key]["p_pass_lo"] * 100 for r in rows]
        p_hi = [r[key]["p_pass_hi"] * 100 for r in rows]
        color = colors.get(key, None)
        ax.fill_between(risks, p_lo, p_hi, alpha=0.15, color=color)
        ax.plot(risks, p_pass, color=color, label=f"P(pass) — {key}", linewidth=2)
    ax.set_xlabel("risk per trade (% of account per R)")
    ax.set_ylabel("P(pass) (%)")
    ax.set_title(f"Ruin engine — edge vs control(s) — {contract.display_name}")
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
    ap.add_argument("--control", choices=("none",) + CONTROL_KINDS + ("both",),
                    default="both",
                    help="zero-edge control(s) to compare the edge against, "
                         "printed on the same line (default: both)")
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
          f"| risks swept: {len(risks)} | block={BOOT_BLOCK} | tol={a.tol} "
          f"| control={a.control}")

    if a.control == "none":
        rows = sweep(vi, vw, vopen, contract, risks, horizon_td, seed=a.seed,
                    min_paths=a.min_paths, max_paths=a.max_paths, batch=a.batch,
                    tol=a.tol)
        print_frontier(rows, contract, a.horizon_days)
        chart_rows = rows
        write_payload = dict(firm=a.firm, series=a.series, horizon_days=a.horizon_days,
                             horizon_trading_days=horizon_td, seed=a.seed,
                             control=a.control, rows=rows)
    else:
        control_keys = list(CONTROL_KINDS) if a.control == "both" else [a.control]
        # Controls are built ONCE, seeded off --seed but from an
        # independent stream so control construction never consumes the
        # same draws as the Monte Carlo path sampler.
        ctrl_build_rng = np.random.default_rng(a.seed + 1_000_003)
        series_map = {"edge": (vi, vw, vopen)}
        for ck in control_keys:
            ctrl_trades = make_control_trades(trades, ck, ctrl_build_rng)
            _, cvi, cvw, cvopen = build_series(ctrl_trades, haircut, center=False)
            series_map[ck] = (cvi, cvw, cvopen)

        rows = sweep_frontier(series_map, contract, risks, horizon_td, seed=a.seed,
                              target_key="edge", min_paths=a.min_paths,
                              max_paths=a.max_paths, batch=a.batch, tol=a.tol)
        print_frontier([r["edge"] for r in rows], contract, a.horizon_days)
        print_frontier_with_controls(rows, contract, a.horizon_days, control_keys)
        chart_rows = rows
        write_payload = dict(firm=a.firm, series=a.series, horizon_days=a.horizon_days,
                             horizon_trading_days=horizon_td, seed=a.seed,
                             control=a.control, control_keys=control_keys,
                             rows=rows,
                             separation={ck: separation_summary(rows, "edge", ck)
                                        for ck in control_keys})

    if not a.no_chart:
        if a.control == "none":
            make_chart(chart_rows, contract, OUT_CHART)
        else:
            make_chart_with_controls(chart_rows, contract, control_keys, OUT_CHART)
        print(f"\nchart written: {OUT_CHART.relative_to(ROOT)}")

    if a.write:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(write_payload, indent=1))
        print(f"state written: {OUT_JSON.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
