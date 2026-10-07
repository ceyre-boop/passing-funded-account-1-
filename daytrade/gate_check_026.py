#!/usr/bin/env python3
"""SUPERHUMAN-001 GATE CHECK — the TRAIN-side decision, spec 026.

This is the step that decides whether SEALED-002 gets read AT ALL, and it is the
most valuable thing in the spec 026 loop because the cheapest holdout is the one
you never spend.

Spec 026's stop conditions are three, and only one of them involves a holdout:
  ACHIEVED             — all six gates pass on SEALED-002
  NOT ACHIEVED         — G-S1 fails on SEALED-002
  BLOCKED underpowered — SEALED-002 cannot reach n >= 250

But there is a fourth state the spec implies and this script makes explicit:
**NO-GO — do not spend the holdout.** If the regime policy cannot clear the
margin on TRAIN, where it has every advantage (it was built here, the thresholds
were chosen here, and it has been looked at freely), then reading SEALED-002
cannot produce a pass — it can only burn the holdout to confirm a failure already
visible for free. SEALED-001 was spent exactly that way on 2026-08-17: the tune
split promised +0.1135R, the holdout returned -0.1066R, and the holdout is now
gone for that rule_version forever.

So: TRAIN must show the margin BEFORE the holdout is touched. A holdout confirms
a pre-stated expectation; it does not go looking for one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

ROOT = HERE.parent
TRAIN_REPORT = ROOT / "data" / "daytrade" / "entry_selection_report.json"
LABELS_REPORT = ROOT / "data" / "daytrade" / "regime_labels_report.json"
OUT = ROOT / "data" / "daytrade" / "gate_check_026.json"

MARGIN_REQ = 0.05        # G-S2, pre-registered in stockfish_tune and spec 026
BANKLIKE = 0.10          # G-S1 net target
MIN_SEALED_N = 250       # G-S1 power requirement
MIN_SCORED = 300         # G-S6


def _fmt(v, nd=4):
    return "n/a" if v is None else f"{v:+.{nd}f}"


def main() -> int:
    if not TRAIN_REPORT.exists():
        print(f"  MISSING {TRAIN_REPORT.name} — run entry_selection.py first")
        return 2
    t = json.loads(TRAIN_REPORT.read_text())
    e1 = t.get("E1_policy_selection_train") or {}
    base = t.get("E1_best_fixed")
    margin = t.get("E1_margin_vs_best_fixed_r")
    reg = e1.get("r_regime") or {}
    bas = e1.get(base) or {}
    counts = t.get("counts", {})
    cost = t.get("cost_model", {})

    print("  ── SUPERHUMAN-001 · TRAIN-SIDE GATE CHECK ──")
    print(f"  cost model: embedded median {_fmt(cost.get('embedded_median_r'))}R "
          f"measured · target {cost.get('cost_target_r')}R assumed")
    print(f"  TRAIN n {counts.get('train')}  DEV n {counts.get('dev')}  "
          f"SEALED-002 n {counts.get('sealed_not_read')} (unread)")
    print()
    print(f"  best hand-executable fixed policy : {base}")
    print(f"    gross {_fmt(bas.get('mean_gross_r'))}R   net {_fmt(bas.get('mean_net_r'))}R"
          f"   CI {bas.get('net_ci95')}")
    print(f"  regime-conditioned policy         : r_regime")
    print(f"    gross {_fmt(reg.get('mean_gross_r'))}R   net {_fmt(reg.get('mean_net_r'))}R"
          f"   CI {reg.get('net_ci95')}")
    print(f"  MARGIN on TRAIN                   : {_fmt(margin)}R  "
          f"(G-S2 needs >= +{MARGIN_REQ})")

    labels = json.loads(LABELS_REPORT.read_text()) if LABELS_REPORT.exists() else {}
    bss = (labels.get("report") or {}).get("brier_skill_score")
    n_scored = labels.get("n_gradeable")
    g6_n = bool(labels.get("gate_G_S6_n_met"))
    g6_b = bool(labels.get("gate_G_S6_brier_skill_met"))

    print()
    print(f"  G-S6 reward signal: {n_scored} scored calls "
          f"(needs >= {MIN_SCORED}: {'MET' if g6_n else 'NOT MET'}), "
          f"Brier skill {bss} (needs > 0: {'MET' if g6_b else 'NOT MET'})")

    # ---- the TRAIN-side preconditions for spending the holdout
    checks = {
        "train_margin_clears_G_S2":
            margin is not None and margin >= MARGIN_REQ,
        "train_regime_net_is_positive":
            reg.get("mean_net_r") is not None and reg["mean_net_r"] > 0,
        "sealed_is_powered":
            (counts.get("sealed_not_read") or 0) >= MIN_SEALED_N,
        "reward_signal_scored_G_S6": g6_n and g6_b,
    }
    print()
    for k, v in checks.items():
        print(f"    {k:34} {'PASS' if v else 'FAIL'}")

    go = all(checks.values())
    if go:
        verdict = "GO — TRAIN supports a SEALED-002 read"
        detail = ("Every TRAIN-side precondition holds. The holdout can now "
                  "confirm or refute a pre-stated expectation. Freeze the "
                  "rule_version, commit it, let it stand one commit, then run "
                  "sealed_read_026.py — Colin's call, once.")
    else:
        failed = [k for k, v in checks.items() if not v]
        verdict = "NO-GO — do NOT spend SEALED-002"
        detail = (f"Failing: {', '.join(failed)}. On TRAIN the regime policy has "
                  f"every advantage — it was built here and tuned here — so a "
                  f"margin it cannot show here cannot appear out of sample. "
                  f"Reading the holdout now would only burn it to confirm a "
                  f"failure already visible for free, which is precisely how "
                  f"SEALED-001 was spent on 2026-08-17 (tune +0.1135R -> sealed "
                  f"-0.1066R, NOT_VALIDATED).")

    print(f"\n  VERDICT: {verdict}")
    print(f"  {detail}")

    OUT.write_text(json.dumps({
        "spec": "026_SUPERHUMAN_TARGET", "stage": "TRAIN_GATE_CHECK",
        "best_fixed": base, "train_margin_r": margin,
        "regime_net_r": reg.get("mean_net_r"),
        "best_fixed_net_r": bas.get("mean_net_r"),
        "counts": counts, "cost_model": cost,
        "n_scored_calls": n_scored, "brier_skill_score": bss,
        "checks": checks, "go": go, "verdict": verdict, "detail": detail,
    }, indent=1))
    print(f"  record: {OUT.relative_to(ROOT)}")
    return 0 if go else 1


if __name__ == "__main__":
    sys.exit(main())
