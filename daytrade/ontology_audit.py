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
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from ceiling import find_entry, simulate                           # noqa: E402
import decision_ledger as dl                                       # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "ontology_audit.json"
N_PERM = 2000
SEED = 30
SHIPPED = {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
           "flatten_et": None, "hold_past_tp2": True}


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
            sessions = load_sessions(sym, "5m", allow_fetch=False)
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
        obs = abs(statistics.mean(a) - statistics.mean(b))
        # null: random partitions of the SAME size. If a label does no more
        # than a coin flip that splits the same way, it carves nothing.
        hits = 0
        for _ in range(N_PERM):
            shuf = all_r[:]
            rng.shuffle(shuf)
            if abs(statistics.mean(shuf[:m]) - statistics.mean(shuf[m:])) >= obs:
                hits += 1
        p = (hits + 1) / (N_PERM + 1)
        results.append({
            "label": label, "n_marked": m, "n_unmarked": n - m,
            "mean_marked_R": round(statistics.mean(a), 4),
            "mean_unmarked_R": round(statistics.mean(b), 4),
            "separation_R": round(obs, 4), "p_value": round(p, 4),
            "verdict": "CARVES" if p < 0.05 else "DECORATION"})

    print(f"\n  {'label':14s} {'n':>4s} {'marked R':>10s} {'other R':>10s} "
          f"{'sep':>7s} {'p':>7s}   verdict")
    for r in results:
        if "separation_R" not in r:
            print(f"  {r['label']:14s} {r['n_marked']:4d} {'':>10s} {'':>10s} "
                  f"{'':>7s} {'':>7s}   {r['verdict']}")
            continue
        print(f"  {r['label']:14s} {r['n_marked']:4d} {r['mean_marked_R']:>+10.3f} "
              f"{r['mean_unmarked_R']:>+10.3f} {r['separation_R']:>7.3f} "
              f"{r['p_value']:>7.3f}   {r['verdict']}")

    tested = [r for r in results if "p_value" in r]
    carves = [r for r in tested if r["verdict"] == "CARVES"]
    # 9 labels tested against one outcome: a Bonferroni floor is the honest
    # bar, stated up front rather than after seeing which ones passed.
    bonf = [r for r in tested if r["p_value"] < 0.05 / max(len(tested), 1)]
    print(f"\n  {len(carves)}/{len(tested)} labels separate outcomes at p<0.05 "
          f"({len(bonf)} survive Bonferroni over {len(tested)} tests)")
    print("  A label that does not carve is DECORATION: it invites stories "
          "about a distinction the tape does not make.")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_sessions": n, "n_perm": N_PERM,
        "vocabulary": list(dl.REGIMES), "results": results,
        "n_carves": len(carves), "n_survive_bonferroni": len(bonf),
        "note": "outcome = OR-break session R under the shipped policy; "
                "labels are the union of regimes observed that morning",
    }, indent=1))
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
