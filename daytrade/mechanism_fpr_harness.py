#!/usr/bin/env python3
"""FALSE-POSITIVE HARNESS for the 029 transfer test — run it on itself.

Adversarial review (2026-08-19) claims the three-class sign contrast confirms
noise, because ~99% of the exit vocabulary produces the same sign pattern
("hurts single names, helps index/futures") for a reason that has nothing to
do with any mechanism: single names carry the fattest right tail, and every
config in the vocabulary caps upside.

If that is true, the framework must reject NULL mechanisms — random lever
perturbations paired with RANDOMLY ASSIGNED sign patterns — at far above its
nominal alpha. This harness measures that rate directly. No claim in spec 029
survives a bad number here.

Nothing is written to the ledger. This is the framework auditing itself.
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import tune_sessions                                   # noqa: E402
from ceiling import find_entry, simulate, wide_space               # noqa: E402
from stockfish_tune import CLASSES                                 # noqa: E402
import mechanisms as mx                                            # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "mechanism_fpr_report.json"
N_NULL = 20
SEED = 1029


def main() -> int:
    sym_class = {s: c for c, syms in CLASSES.items() for s in syms}
    entries = []
    for sym in [s for v in CLASSES.values() for s in v]:
        try:
            for sess in tune_sessions(load_sessions(sym, "5m", allow_fetch=False)):
                e = find_entry(sess)
                if e:
                    entries.append((sym, sess, e))
        except BarDataError as ex:
            print(f"  !! {sym}: {ex} — excluded loudly")
    days = {str(s.day) for _, s, _ in entries}
    print(f"  {len(entries)} entries · {len(days)} independent days · "
          f"{len({s for s, _, _ in entries})} symbols")

    grid = list(wide_space().values())
    rng = random.Random(SEED)
    base = {"trail_mult": None, "be_arm_frac": 1.0, "partial_frac": 0.5,
            "flatten_et": None, "hold_past_tp2": True}

    # cache the base arm once — it is the same for every null mechanism
    base_r = {}
    for sym, sess, e in entries:
        base_r[(sym, str(sess.day), e.ts)] = simulate(sess, e, dict(base))

    results, confirmed, narrowed = [], 0, 0
    for k in range(N_NULL):
        cfg = rng.choice(grid)
        # RANDOM sign pattern — no causal claim, so any pass is a false pass
        pattern = {c: rng.choice(["help", "neutral", "hurt"]) for c in CLASSES}
        if all(mx.WEIGHTS[v] == 0 for v in pattern.values()):
            pattern["FUTURES"] = "help"

        rows, by_class = [], defaultdict(list)
        for sym, sess, e in entries:
            d = simulate(sess, e, dict(cfg)) - base_r[(sym, str(sess.day), e.ts)]
            cls = sym_class[sym]
            rows.append((str(sess.day), cls, d))
            by_class[cls].append(d)
        obs = mx.contrast(by_class, pattern)
        p = mx.permutation_p(rows, pattern, obs, n_perm=400, seed=SEED + k)
        sign_ok = True
        for c, want in pattern.items():
            got = (sum(by_class[c]) / len(by_class[c])) if by_class[c] else None
            if got is None or mx.WEIGHTS[want] == 0:
                continue
            if (got > 0) != (mx.WEIGHTS[want] > 0):
                sign_ok = False
        verdict = ("CONFIRMED" if (p < mx.ALPHA and sign_ok) else
                   "KILLED" if p < mx.ALPHA else
                   "NARROWED" if sign_ok else "KILLED")
        confirmed += verdict == "CONFIRMED"
        narrowed += verdict == "NARROWED"
        results.append({"i": k, "pattern": pattern, "contrast": round(obs, 4),
                        "p": round(p, 4), "sign_ok": sign_ok, "verdict": verdict})
        print(f"    null {k:2d}  {str(pattern):68s} T {obs:+.3f}  p {p:.3f}  {verdict}")

    fpr = confirmed / N_NULL
    soft = (confirmed + narrowed) / N_NULL
    print(f"\n  NULL MECHANISMS: {N_NULL}")
    print(f"  CONFIRMED (false positives): {confirmed}  -> FPR {fpr:.0%} "
          f"(nominal alpha {mx.ALPHA:.0%})")
    print(f"  CONFIRMED or NARROWED (any 'supportive' read): {soft:.0%}")
    verdict = ("FRAMEWORK_UNSOUND" if fpr > 2 * mx.ALPHA else
               "FRAMEWORK_CALIBRATED")
    print(f"\n  VERDICT: {verdict}")
    OUT.write_text(json.dumps(
        {"n_entries": len(entries), "n_days": len(days), "n_null": N_NULL,
         "false_positive_rate": fpr, "supportive_rate": soft,
         "nominal_alpha": mx.ALPHA, "verdict": verdict, "runs": results}, indent=1))
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
