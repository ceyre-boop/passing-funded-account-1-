#!/usr/bin/env python3
"""gate/calibrate.py — two-sided calibration of the SPRT gate.

A gate that has only ever produced ACCEPT_H0 is uncalibrated. It must be shown
to do BOTH things before it is trusted with a real candidate:

  NULL ARM      shipped policy against itself -> ACCEPT_H0, deviation ~0.
                Proves the gate recognises "no change".
  DEGRADED ARM  a deliberately worse candidate -> a decisive REJECT.
                Proves the failure path fires. Same logic as fault-injecting an
                assertion: an assertion that has never fired is not verified.

Sigma is the SD of the PAIRED DIFFERENCE, not of the level. Last night's carry
gate used the level SD, which inflated delta to roughly twice the whole edge.
Delta is a declared plausible-improvement argument, passed in, never derived
from the candidate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))

from gate.sprt import Decision, sprt  # noqa: E402


def paired_deltas(incumbent_r: dict[str, float], candidate_r: dict[str, float]) -> tuple[list[str], np.ndarray]:
    """Per-episode ΔR = candidate − incumbent, in a stable chronological order."""
    eps = sorted(set(incumbent_r) & set(candidate_r))
    if not eps:
        raise ValueError("no episodes in common between incumbent and candidate")
    return eps, np.array([candidate_r[e] - incumbent_r[e] for e in eps], dtype=float)


def deviation_rate(deltas: np.ndarray, *, tol: float = 1e-9) -> float:
    """Fraction of episodes where the candidate actually did something different.
    A gate whose deviation is ~0 has not been tested; it has been agreed with."""
    return float(np.mean(np.abs(deltas) > tol))


def run_arm(name: str, incumbent_r, candidate_r, *, delta: float, alpha: float, beta: float,
            sigma: float | None = None) -> dict:
    eps, d = paired_deltas(incumbent_r, candidate_r)
    dev = deviation_rate(d)
    # sigma from the PAIRED DIFFERENCE. Degenerate (all-zero) differences have no
    # spread, so fall back to a declared floor rather than dividing by zero — and
    # say which happened.
    s = float(np.std(d, ddof=1)) if len(d) > 1 else 0.0
    sigma_used, sigma_src = (sigma, "supplied") if sigma is not None else (
        (s, "paired-difference SD") if s > 1e-12 else (1.0, "FLOOR — paired differences are degenerate"))
    res = sprt(d.tolist(), delta=delta, sigma=sigma_used, alpha=alpha, beta=beta)
    return {"arm": name, "n": len(d), "decision": res.decision.value, "stop_index": res.stop_index,
            "llr_final": res.llr_final, "upper": res.upper_bound, "lower": res.lower_bound,
            "deviation_rate": dev, "mean_delta": float(d.mean()), "sigma": sigma_used,
            "sigma_source": sigma_src, "delta": delta}


def verdict(null_arm: dict, degraded_arm: dict) -> tuple[bool, str]:
    """The gate is calibrated only if it recognises a null AND rejects a degradation."""
    problems = []
    if null_arm["decision"] != Decision.ACCEPT_H0.value:
        problems.append(f"null arm returned {null_arm['decision']}, expected ACCEPT_H0")
    if null_arm["deviation_rate"] > 1e-6:
        problems.append(f"null arm deviated ({null_arm['deviation_rate']:.4f}) — it is not a true null")
    if degraded_arm["decision"] == Decision.INCONCLUSIVE.value:
        problems.append("degraded arm was INCONCLUSIVE — the gate could not reject a deliberate degradation")
    if degraded_arm["deviation_rate"] < 0.05:
        problems.append(f"degraded arm barely deviated ({degraded_arm['deviation_rate']:.4f}) — degradation too weak to test the gate")
    if degraded_arm["mean_delta"] >= 0:
        problems.append(f"degraded arm mean ΔR is {degraded_arm['mean_delta']:+.4f} — not actually degraded")
    return (not problems), ("CALIBRATED — recognises a null and rejects a degradation"
                            if not problems else "UNCALIBRATED: " + "; ".join(problems))


def split_by_cell_status(deltas_by_episode: dict[str, float], valued_episodes: set[str]) -> dict:
    """Point 3: cells below min_paths return NO_VALUE and route to a DEFAULT action.
    That default IS a policy and its deviation has nothing to do with the value
    function, so valued and unvalued must never be pooled in a headline number."""
    v = np.array([d for e, d in deltas_by_episode.items() if e in valued_episodes], dtype=float)
    u = np.array([d for e, d in deltas_by_episode.items() if e not in valued_episodes], dtype=float)
    out = {}
    for label, arr in (("valued", v), ("unvalued_default", u)):
        out[label] = {"n": int(arr.size),
                      "mean_delta": float(arr.mean()) if arr.size else None,
                      "deviation_rate": deviation_rate(arr) if arr.size else None}
    return out
