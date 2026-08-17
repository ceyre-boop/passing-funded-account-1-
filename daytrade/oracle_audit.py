#!/usr/bin/env python3
"""ORACLE AUDIT — spec 026 addendum. Deflating the +0.67 before anyone chases it.

The cross-chat review's verdict, adopted: the RANKING (policy >> interrupt >
tighten) is real and banked; the +0.67 R/trade headline is a max-over-396
hindsight statistic and will not survive honest treatment. This script runs
the six deflation moves, each gating the next.

PRE-REGISTERED, written before this script first ran:

  Move 1 — SHORTLIST ORACLE: 6 pre-approved families (below), not 396.
  Move 2 — NULL ORACLE: each family's R-column permuted independently across
     entries (destroys day↔config alignment, preserves marginals); null leak
     = mean(per-entry max of permuted columns) − best fixed family.
     GATE: null leak must be < 0.15 R/trade, else the harness measures
     selection noise and NO leak number is quotable. The honest vocabulary
     leak = shortlist leak − null leak.
  Move 3+5+6 — INFORMATION-LIMITED REALIZABLE BOUND: a depth-3 decision tree
     (≤12 leaves) on entry-time-knowable features only, day-grouped 5-fold
     OOF, choosing among the 6 families. Its OOF-chosen-family mean R minus
     best fixed = the realizable leak estimate. Also reported: OOF top-1
     agreement with the shortlist oracle — the promotion metric that needs
     no capital and no waiting for live R.
  SAMPLE SIZE: days needed to detect +0.20 R/trade at one-sided 95% / 80%
     power from the measured per-day R stdev — computed and recorded NOW so
     the promotion criterion is chosen before anyone wants the answer.

  ON-THE-HOOK PREDICTION (the reviewer's, co-signed): the information-limited
  shortlist estimate lands 0.15–0.30 R/trade, risk-event days retaining most
  of their component. Above 0.5 = something genuinely rich; below 0.1 = the
  policy channel joins tighten in the drawer.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions, TUNE_END                         # noqa: E402
from ceiling import find_entry, simulate                           # noqa: E402
from stockfish_tune import CLASSES                                 # noqa: E402
from competence_report import label_day                            # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "oracle_audit.json"

SYM_CLASS = {s: c for c, syms in CLASSES.items() for s in syms}
NULL_GATE = 0.15
RNG_SEED = 26

# Move 1: the six pre-approved families — one per behavioral axis of the grid.
FAMILIES = {
    "STATIC":     {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
                   "flatten_et": None, "hold_past_tp2": True},
    "EARLY_BANK": {"trail_mult": None, "be_arm_frac": 0.25, "partial_frac": 0.75,
                   "flatten_et": None, "hold_past_tp2": True},
    "NOON_FLAT":  {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
                   "flatten_et": "12:00", "hold_past_tp2": True},
    "TIGHT_TRAIL": {"trail_mult": 0.5, "be_arm_frac": 0.5, "partial_frac": 0.5,
                    "flatten_et": None, "hold_past_tp2": True},
    "EXIT_TP2":   {"trail_mult": None, "be_arm_frac": 0.5, "partial_frac": 0.5,
                   "flatten_et": None, "hold_past_tp2": False},
    "RIDE":       {"trail_mult": 3.0, "be_arm_frac": 1.0, "partial_frac": 0.25,
                   "flatten_et": None, "hold_past_tp2": True},
}

FEATURES = ["cls_SINGLE_NAME", "cls_CASH_INDEX", "cls_FUTURES",
            "direction", "or_risk_pct", "gap_pct", "pre_tr_med_r",
            "entry_minute", "dow"]


def entry_features(sym: str, s, e) -> list[float]:
    """Knowable AT the entry moment, nothing later."""
    cls = SYM_CLASS[sym]
    pre = s.df[s.df.index < e.ts]                    # bars before the entry bar
    prior_close = float(pre["Close"].iloc[0]) if len(pre) else e.entry
    day_open = float(s.df["Open"].iloc[0])
    tr_med = (float((pre["High"] - pre["Low"]).abs().median() / e.risk)
              if len(pre) >= 3 else 0.0)
    hhmm = e.ts[11:16]
    return [1.0 if cls == c else 0.0 for c in CLASSES] + [
        float(e.direction),
        e.risk / e.entry * 100,
        (day_open - prior_close) / prior_close * 100,
        tr_med,
        int(hhmm[:2]) * 60 + int(hhmm[3:]),
        datetime.fromisoformat(e.ts).weekday(),
    ]


def main() -> int:
    entries = []
    for sym in [s for syms in CLASSES.values() for s in syms]:
        try:
            for s in tune_sessions(load_sessions(sym, "5m", allow_fetch=False)):
                e = find_entry(s)
                if e:
                    entries.append((sym, s, e))
        except BarDataError as ex:
            print(f"  !! {sym}: {ex} — excluded loudly")
    n = len(entries)
    print(f"  {n} tune entries (<= {TUNE_END}) · {len(FAMILIES)} families")

    # R matrix: entries x families
    R = np.array([[simulate(s, e, dict(cfg)) for cfg in FAMILIES.values()]
                  for _, s, e in entries])
    fam_names = list(FAMILIES)
    fixed_means = R.mean(axis=0)
    best_fixed_i = int(fixed_means.argmax())
    best_fixed = float(fixed_means[best_fixed_i])

    # Move 1 — shortlist oracle
    shortlist_oracle = float(R.max(axis=1).mean())
    shortlist_leak = shortlist_oracle - best_fixed

    # Move 2 — null oracle (pre-registered permutation scheme), 200 draws
    rng = np.random.default_rng(RNG_SEED)
    null_leaks = []
    for _ in range(200):
        P = np.column_stack([rng.permutation(R[:, j]) for j in range(R.shape[1])])
        null_leaks.append(P.max(axis=1).mean() - best_fixed)
    null_leak = float(np.mean(null_leaks))
    honest_leak = shortlist_leak - null_leak
    null_ok = null_leak < NULL_GATE

    print(f"\n  best fixed family      {fam_names[best_fixed_i]:12s} {best_fixed:+.4f} R/trade")
    print(f"  shortlist oracle (6)                {shortlist_oracle:+.4f}")
    print(f"  shortlist leak                      {shortlist_leak:+.4f}")
    print(f"  null-oracle leak (200 perms)        {null_leak:+.4f}  "
          f"[gate < {NULL_GATE}: {'PASS' if null_ok else 'FAIL — noise, nothing quotable'}]")
    print(f"  HONEST vocabulary leak              {honest_leak:+.4f}")

    # Moves 3+5+6 — information-limited tree, day-grouped OOF
    from sklearn.tree import DecisionTreeClassifier, export_text
    from sklearn.model_selection import GroupKFold
    X = np.array([entry_features(sym, s, e) for sym, s, e in entries])
    y_oracle = R.argmax(axis=1)
    days = np.array([e.day for _, _, e in entries])
    oof_choice = np.full(n, -1)
    for tr, te in GroupKFold(5).split(X, y_oracle, days):
        t = DecisionTreeClassifier(max_depth=3, min_samples_leaf=15,
                                   random_state=RNG_SEED)
        t.fit(X[tr], y_oracle[tr])
        oof_choice[te] = t.predict(X[te])
    tree_r = float(R[np.arange(n), oof_choice].mean())
    realizable_leak = tree_r - best_fixed
    agreement = float((oof_choice == y_oracle).mean())

    # per-scenario retention of the realizable leak
    scen = [label_day(s, e)[0] for _, s, e in entries]
    per_scen = {}
    for sc in sorted(set(scen)):
        idx = [i for i, x in enumerate(scen) if x == sc]
        per_scen[sc] = {"n": len(idx),
                        "tree_R": round(float(R[idx, oof_choice[idx]].mean()), 4),
                        "fixed_R": round(float(R[idx, best_fixed_i].mean()), 4)}

    print(f"\n  info-limited tree (OOF, day-grouped) {tree_r:+.4f}")
    print(f"  REALIZABLE leak estimate            {realizable_leak:+.4f}")
    print(f"  OOF top-1 oracle agreement          {agreement:.1%}  "
          f"(chance ~{1/len(FAMILIES):.0%}, majority-class "
          f"{Counter(y_oracle.tolist()).most_common(1)[0][1]/n:.0%})")
    for sc, d in per_scen.items():
        print(f"    {sc:20s} n {d['n']:3d}  tree {d['tree_R']:+.3f}  fixed {d['fixed_R']:+.3f}")

    # final tree on all tune data, for the audit trail (never for deployment)
    full_tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=15,
                                       random_state=RNG_SEED).fit(X, y_oracle)
    tree_text = export_text(full_tree, feature_names=FEATURES,
                            class_names=fam_names)

    # Sample size — registered now, before anyone wants the answer
    day_r = defaultdict(list)
    for i, (_, _, e) in enumerate(entries):
        day_r[e.day].append(float(R[i, best_fixed_i]))
    daily = [statistics.mean(v) for v in day_r.values()]
    sigma = statistics.pstdev(daily)
    delta = 0.20
    n_days = ((1.645 + 0.842) ** 2) * (sigma / delta) ** 2   # 95% one-sided, 80% power
    years = n_days / 250
    print(f"\n  per-day R stdev {sigma:.3f} → detecting +{delta} R/trade needs "
          f"~{n_days:.0f} trading days (~{years:.1f} years) at 95%/80%")
    print("  => live R CANNOT be the promotion criterion on this horizon; "
          "oracle-agreement (move 5) is the registered gate instead")

    verdict = ("NOTHING_QUOTABLE" if not null_ok else
               "RICH" if realizable_leak > 0.5 else
               "AS_PREDICTED" if realizable_leak >= 0.15 else
               "THIN" if realizable_leak >= 0.10 else "DRAWER")
    print(f"\n  VERDICT vs on-the-hook prediction (0.15–0.30): {verdict} "
          f"({realizable_leak:+.3f})")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_entries": n, "families": fam_names,
        "fixed_means": {f: round(float(m), 4) for f, m in zip(fam_names, fixed_means)},
        "best_fixed": fam_names[best_fixed_i],
        "shortlist_oracle_R": round(shortlist_oracle, 4),
        "shortlist_leak_R": round(shortlist_leak, 4),
        "null_leak_R": round(null_leak, 4), "null_gate": NULL_GATE,
        "null_gate_pass": null_ok,
        "honest_leak_R": round(honest_leak, 4),
        "tree_oof_R": round(tree_r, 4),
        "realizable_leak_R": round(realizable_leak, 4),
        "oracle_agreement_oof": round(agreement, 4),
        "per_scenario": per_scen,
        "sample_size": {"per_day_sigma": round(sigma, 4), "delta": delta,
                        "n_days": round(n_days), "years": round(years, 1),
                        "consequence": "oracle-agreement is the promotion gate"},
        "tree_rules": tree_text,
        "verdict": verdict,
        "prediction_on_the_hook": "0.15-0.30 realizable; >0.5 rich; <0.1 drawer",
    }, indent=1))
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
