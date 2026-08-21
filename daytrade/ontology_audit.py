#!/usr/bin/env python3
"""ONTOLOGY AUDIT — does the vocabulary carve markets at real joints?

Spec 029 measures CLAIMS. Spec 030 records STATE using a regime vocabulary
that was simply asserted — nine labels a model invented, never tested. A
system whose claims are measured but whose VOCABULARY is exempt cannot grow:
it can only fill in a table whose columns it can never question.

This asks the vocabulary to earn its existence. For each label: does
conditioning on it shift the outcome distribution more than a RANDOM
partition of the same size? A label that fails is decoration — it consumes
attention, invites stories, and encodes an assumption nobody has checked.

Read-only. Proposes nothing, trades nothing, writes one report.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions, TUNE_END                         # noqa: E402
from ceiling import find_entry, simulate                           # noqa: E402
import decision_ledger as dl                                       # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "ontology_audit.json"
N_PERM = 40000    # see the MC-error note below
SEED = 30
# Spec 035: a verdict must survive being asked of half the days. SUB_PERM is
# deliberately lower than N_PERM — the subsample test asks only whether p
# clears 0.05, not where it sits against a Bonferroni floor.
K_SUB = 8
SUB_PERM = 2000
# The null for a reproduction rate is NOT "most halves" — an arbitrary floor
# is just another unexamined assumption. A label with no real effect still
# clears p<0.05 in ~5% of half-samples by construction, so that 5% IS the
# null, and the question is whether the observed rate beats it.
NOISE_CARVE_RATE = 0.05
# Halves are drawn from one record and overlap, so they are positively
# correlated and the binomial tail UNDERSTATES the true p. Compensated with a
# stricter alpha rather than pretending the draws are independent.
STABILITY_ALPHA = 0.01
SHIPPED = {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
           "flatten_et": None, "hold_past_tp2": True}


def audit_sessions(sym: str) -> list:
    """I-035-3. TUNE SPLIT ONLY.

    This audit publishes a verdict, so it may not read the sealed holdout
    without going through sealed_sessions()'s unseal ritual — which it
    deliberately does not do. It called load_sessions() directly until
    2026-08-21, and once the decision ledger was backfilled to 90 days that
    put 40 post-boundary days into a published measurement. Every ontology
    number produced before this line existed was computed on a mixed sample
    and is withdrawn.
    """
    return tune_sessions(load_sessions(sym, "5m", allow_fetch=False))


def perm_p(a: list, b: list, rng, n_perm: int) -> tuple:
    """Two-sided permutation p for a difference of means, against random
    partitions of the SAME sizes."""
    obs = abs(statistics.mean(a) - statistics.mean(b))
    pool = list(a) + list(b)
    m = len(a)
    hits = 0
    for _ in range(n_perm):
        shuf = pool[:]
        rng.shuffle(shuf)
        if abs(statistics.mean(shuf[:m]) - statistics.mean(shuf[m:])) >= obs:
            hits += 1
    return (hits + 1) / (n_perm + 1), obs


def binom_tail(carved: int, n: int, p0: float) -> float:
    """P(X >= carved) under Binomial(n, p0). Exact, stdlib only."""
    return sum(math.comb(n, i) * p0 ** i * (1 - p0) ** (n - i)
               for i in range(carved, n + 1))


def subsample_stability(outcomes: dict, labels: dict, label: str, rng,
                        *, k: int = K_SUB, n_perm: int = SUB_PERM) -> dict:
    """I-035-1. Re-ask the question of K half-samples drawn BY DAY and report
    how often the answer is yes.

    The audit already guarded the within-run coin flip (at_boundary). This
    guards the across-sample one: when the ledger grew 438 -> 674 sessions,
    TREND_UP and TREND_DOWN swapped verdicts outright. A label that carves a
    real joint sharpens as data arrives; two labels trading places is what
    noise looks like when the sample is small enough that which noise wins is
    arbitrary.

    Returns a dict. `rate` is None when too few half-samples could be
    evaluated — named, never defaulted (I-035-2).
    """
    days = sorted({key[1] for key in outcomes})
    half = len(days) // 2
    if half < 1:
        return {"rate": None, "n_eval": 0, "carved": 0, "stability_p": None}
    evaluable = carved = 0
    for _ in range(k):
        pick = set(rng.sample(days, half))
        keys = [key for key in outcomes if key[1] in pick]
        a = [outcomes[key] for key in keys if label in labels[key]]
        b = [outcomes[key] for key in keys if label not in labels[key]]
        if len(a) < 10 or len(b) < 10:
            continue                      # this half cannot answer; not a "no"
        evaluable += 1
        p, _ = perm_p(a, b, rng, n_perm)
        carved += int(p < 0.05)
    if evaluable * 2 < k:                 # I-035-2: untested, not stable
        return {"rate": None, "n_eval": evaluable, "carved": carved,
                "stability_p": None}
    return {"rate": carved / evaluable, "n_eval": evaluable, "carved": carved,
            "stability_p": binom_tail(carved, evaluable, NOISE_CARVE_RATE)}


def classify(p: float, boundary: bool, stab: dict) -> str:
    """The single place a verdict is decided. UNSTABLE is neither CARVES nor
    DECORATION: the honest statement is that the answer depends on which half
    of the record you ask."""
    if boundary:
        return "BOUNDARY"
    if p >= 0.05:
        return "DECORATION"
    if stab.get("stability_p") is None:
        return "UNSTABLE_UNTESTED"
    if stab["stability_p"] >= STABILITY_ALPHA:
        return "UNSTABLE"
    return "CARVES"


def surviving(tested: list) -> list:
    """The Bonferroni-surviving set. CARVES only — an UNSTABLE label cannot
    enter it no matter how small its full-sample p is (I-035-1)."""
    return [r for r in tested if r["p_value"] < 0.05 / max(len(tested), 1)
            and r["verdict"] == "CARVES"]


def main() -> int:
    rows = [r for r in dl._rows() if r.get("kind") == "decision_point"]
    if not rows:
        print("  no decision points — nothing to audit")
        return 1

    # One label set per (symbol, session): the union of regimes seen that
    # morning. The outcome is that session's OR-break result under the
    # shipped policy — the decision the labels are supposed to inform.
    labels: dict[tuple, set] = defaultdict(set)
    for r in rows:
        labels[(r["symbol"], r["session"])].update(r["regimes"])

    outcomes: dict[tuple, float] = {}
    for sym in sorted({s for s, _ in labels}):
        try:
            sessions = audit_sessions(sym)
        except BarDataError as e:
            print(f"  !! {sym}: {e} — excluded loudly")
            continue
        for sess in sessions:
            key = (sym, str(sess.day))
            if key not in labels:
                continue
            e = find_entry(sess)
            if e is None:
                continue                      # no decision that day, no outcome
            outcomes[key] = simulate(sess, e, dict(SHIPPED))

    keys = sorted(outcomes)
    n = len(keys)
    print(f"  {n} labeled sessions with an outcome "
          f"({len({k[0] for k in keys})} symbols)")
    if n < 30:
        print("  too few to audit — reporting nothing rather than noise")
        return 1

    rng = random.Random(SEED)
    all_r = [outcomes[k] for k in keys]
    results = []
    for label in dl.REGIMES:
        marked = [k for k in keys if label in labels[k]]
        m = len(marked)
        if m < 10 or n - m < 10:
            results.append({"label": label, "n_marked": m,
                            "verdict": "UNTESTABLE — too rare or too universal"})
            continue
        a = [outcomes[k] for k in marked]
        b = [outcomes[k] for k in keys if label not in labels[k]]
        # null: random partitions of the SAME size. If a label does no more
        # than a coin flip that splits the same way, it carves nothing.
        p, obs = perm_p(a, b, rng, N_PERM)
        # Monte Carlo error on p itself. The science loop caught the reason
        # this matters: TREND_UP landed at p=0.0055 against a Bonferroni
        # threshold of 0.00556, and two runs of the SAME test disagreed about
        # whether it survived. A verdict inside its own MC error is a coin
        # flip, and must be reported as one rather than as a finding.
        mc_se = (p * (1 - p) / N_PERM) ** 0.5
        bonf = 0.05 / 9
        boundary = abs(p - bonf) < 2 * mc_se
        stab = subsample_stability(outcomes, labels, label, rng)
        results.append({
            "mc_se": round(mc_se, 5), "bonferroni_threshold": round(bonf, 5),
            "at_boundary": boundary,
            "subsample_carve_rate": (None if stab["rate"] is None
                                     else round(stab["rate"], 3)),
            "subsamples_evaluable": stab["n_eval"],
            "subsamples_carved": stab["carved"],
            "stability_p": (None if stab["stability_p"] is None
                            else round(stab["stability_p"], 6)),
            "stability_alpha": STABILITY_ALPHA,
            "label": label, "n_marked": m, "n_unmarked": n - m,
            "mean_marked_R": round(statistics.mean(a), 4),
            "mean_unmarked_R": round(statistics.mean(b), 4),
            "separation_R": round(obs, 4), "p_value": round(p, 4),
            "verdict": classify(p, boundary, stab)})

    print(f"\n  {'label':14s} {'n':>4s} {'marked R':>10s} {'other R':>10s} "
          f"{'sep':>7s} {'p':>7s}   verdict")
    for r in results:
        if "separation_R" not in r:
            print(f"  {r['label']:14s} {r['n_marked']:4d} {'':>10s} {'':>10s} "
                  f"{'':>7s} {'':>7s}   {r['verdict']}")
            continue
        print(f"  {r['label']:14s} {r['n_marked']:4d} {r['mean_marked_R']:>+10.3f} "
              f"{r['mean_unmarked_R']:>+10.3f} {r['separation_R']:>7.3f} "
              f"{r['p_value']:>7.3f}   {r['verdict']}"
              + (f"  [{r['subsamples_carved']}/{r['subsamples_evaluable']}"
                 f" halves, vs-noise p={r['stability_p']:.4f}]"
                 if r.get("subsample_carve_rate") is not None else ""))

    tested = [r for r in results if "p_value" in r]
    carves = [r for r in tested if r["verdict"] == "CARVES"]
    # 9 labels tested against one outcome: a Bonferroni floor is the honest
    # bar, stated up front rather than after seeing which ones passed.
    bonf = surviving(tested)
    knife = [r["label"] for r in tested if r["at_boundary"]]
    unstable = [r["label"] for r in tested
                if r["verdict"].startswith("UNSTABLE")]
    print(f"\n  {len(carves)}/{len(tested)} labels separate outcomes at p<0.05 "
          f"({len(bonf)} survive Bonferroni over {len(tested)} tests)")
    if unstable:
        print(f"  !! UNSTABLE under subsampling (the verdict depends on which "
              f"half of the record you ask): {unstable}")
    if knife:
        print(f"  !! ON THE KNIFE EDGE (inside their own Monte Carlo error, "
              f"so the verdict is a coin flip): {knife}")
    print("  A label that does not carve is DECORATION: it invites stories "
          "about a distinction the tape does not make.")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_sessions": n, "n_perm": N_PERM,
        "split": "tune", "tune_end": str(TUNE_END),
        "vocabulary": list(dl.REGIMES), "results": results,
        "n_carves": len(carves), "n_survive_bonferroni": len(bonf),
        "n_at_boundary": len(knife), "labels_at_boundary": knife,
        "n_unstable": len(unstable), "labels_unstable": unstable,
        "stability_alpha": STABILITY_ALPHA, "k_subsamples": K_SUB,
        "noise_carve_rate": NOISE_CARVE_RATE,
        "stability_note": "half-samples overlap, so the binomial tail is an "
                          "optimistic bound on the true stability p; alpha is "
                          "tightened to compensate rather than assuming "
                          "independent draws",
        "note": "outcome = OR-break session R under the shipped policy; "
                "labels are the union of regimes observed that morning",
    }, indent=1))
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
