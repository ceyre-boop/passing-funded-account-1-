#!/usr/bin/env python3
"""scripts/self_play.py — let it learn to trade from its own experience.

THE METHOD
    Cross-entropy method. Sample a population of policies, keep the elite,
    refit the sampling distribution, repeat. No gradient, because the reward
    comes through a simulator and is not differentiable; no expert labels,
    because it is meant to work this out itself.

THE TWO GUARDS, WITHOUT WHICH THIS IS JUST OVERFITTING
    WALK-FORWARD  train on the earlier sessions, report on the later ones it
                  has never seen. Split by SESSION, never by row: bars inside a
                  day are one bet.

    THE NULL ARM  the identical learning procedure, same population, same
                  generations, run against SHUFFLED outcomes. A learner given
                  noise still finds a policy that looks good in training --
                  that is what learners do. The only question that matters is
                  whether the real-trained policy beats the noise-trained one
                  OUT OF SAMPLE.

    Reported together, always. A held-out number on its own is not evidence.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from body.features import FEATURE_NAMES  # noqa: E402
from body.learned_policy import N_PARAMS, activity, score, unpack  # noqa: E402

EXP = ROOT / "data" / "daytrade" / "experience.npz"
TRAIN_FRAC = 0.6
POP, ELITE, GENS, SEED = 200, 20, 40, 20260901
FIRST_ORDER_MIN = 0.05   # below this there is nothing to combine


def cem(X, rl, rs, rng, *, gens=GENS):
    mu = np.zeros(N_PARAMS)
    sigma = np.ones(N_PARAMS)
    best = (-1e9, mu)
    for _ in range(gens):
        pop = rng.normal(mu, sigma, size=(POP, N_PARAMS))
        sc = np.array([score(t, X, rl, rs) for t in pop])
        idx = np.argsort(sc)[-ELITE:]
        elite = pop[idx]
        mu, sigma = elite.mean(0), elite.std(0) + 1e-3
        if sc[idx[-1]] > best[0]:
            best = (float(sc[idx[-1]]), pop[idx[-1]].copy())
    return best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="self-play entry learner")
    ap.add_argument("--gens", type=int, default=GENS)
    ap.add_argument("--ignore-first-order", action="store_true",
                    help="learn even though no feature carries standalone signal")
    a = ap.parse_args(argv)

    d = np.load(EXP)
    X, rl, rs, day = d["X"], d["r_long"], d["r_short"], d["day"]
    n_sess = int(d["n_sessions"])
    cut = int(n_sess * TRAIN_FRAC)
    tr, te = day < cut, day >= cut
    print(f"experience: {len(X)} decision points over {n_sess} sessions")
    print(f"  train  sessions 0..{cut-1}   {tr.sum()} points")
    print(f"  HELD OUT sessions {cut}..{n_sess-1}   {te.sum()} points  (never seen)\n")

    # ---- FIRST-ORDER GATE, before any learning happens ---------------------
    # Orthogonality is SECOND-order and meaningless without first-order signal.
    # Averaging N independent noise sources converges toward zero FASTER as N
    # grows, so a composite over worthless components is worse than any one of
    # them. Correct order: does any single component predict anything, THEN are
    # they independent. This gate exists because the first version of this
    # script had that order backwards and spent an entire build finding out.
    best_abs, best_name = 0.0, ""
    for i, name in enumerate(FEATURE_NAMES):
        for r in (rl[tr], rs[tr]):
            c = abs(np.corrcoef(X[tr][:, i], r)[0, 1])
            if c > best_abs:
                best_abs, best_name = c, name
    print(f"FIRST-ORDER CHECK: strongest single-feature |corr| = {best_abs:.4f} "
          f"({best_name})")
    if best_abs < FIRST_ORDER_MIN:
        print(f"  Below {FIRST_ORDER_MIN}. No component carries standalone signal, so")
        print("  any composite of them combines noise. Learning will still fit the")
        print("  training set — that is what learners do — and will not survive")
        print("  contact with unseen sessions.")
        if not a.ignore_first_order:
            print("\n  REFUSING to learn. Re-run with --ignore-first-order to proceed,")
            print("  which is a decision to record, not a default.")
            return 3
        print("  --ignore-first-order given; proceeding under protest.\n")

    rng = np.random.default_rng(SEED)
    print("learning on REAL outcomes…")
    s_tr, theta = cem(X[tr], rl[tr], rs[tr], rng, gens=a.gens)

    print("learning on SHUFFLED outcomes (the null arm)…")
    perm = rng.permutation(tr.sum())
    s_tr_n, theta_n = cem(X[tr], rl[tr][perm], rs[tr][perm], rng, gens=a.gens)

    real_te = score(theta, X[te], rl[te], rs[te])
    null_te = score(theta_n, X[te], rl[te], rs[te])
    always_l = float(rl[te].mean())
    always_s = float(rs[te].mean())

    print(f"\n{'':<26}{'train':>10}{'HELD OUT':>12}{'activity':>10}")
    print(f"{'learned on real':<26}{s_tr:>+10.4f}{real_te:>+12.4f}"
          f"{activity(theta, X[te]):>9.1%}")
    print(f"{'learned on shuffled':<26}{s_tr_n:>+10.4f}{null_te:>+12.4f}"
          f"{activity(theta_n, X[te]):>9.1%}")
    print(f"{'always long':<26}{'':>10}{always_l:>+12.4f}{'100.0%':>10}")
    print(f"{'always short':<26}{'':>10}{always_s:>+12.4f}{'100.0%':>10}")
    print(f"{'never trade':<26}{'':>10}{0.0:>+12.4f}{'0.0%':>10}")

    # STANDING RULE: every R comparison prints the absolute value of the
    # incumbent, and if every arm is negative the header says so. A first
    # version of this reported "real beats null" while the real arm was LOSING
    # money and never-trading beat it -- the least-bad-loser trap, in the
    # verdict logic of the very tool built to avoid it.
    edge = real_te - null_te
    best_simple = max(0.0, always_l, always_s)
    all_negative = real_te <= 0 and null_te <= 0

    print(f"\n  real minus null, held out: {edge:+.4f} R per opportunity")
    if all_negative:
        print(f"\n  HEADER: EVERY LEARNED ARM IS NEGATIVE OUT OF SAMPLE.")
        print(f"    learned on real      {real_te:+.4f}")
        print(f"    learned on shuffled  {null_te:+.4f}")
        print(f"    NOT TRADING          {0.0:+.4f}   <- beats both")
    if real_te <= 0:
        print("\n  VERDICT: NO EDGE LEARNED. The policy loses out of sample and")
        print("  does worse than sitting flat. Whatever it beat the null arm by")
        print("  is a gap between two losing policies, which is not a finding.")
    elif real_te <= best_simple:
        print(f"\n  VERDICT: NO EDGE LEARNED. It is positive but does not beat the")
        print(f"  best trivial baseline ({best_simple:+.4f}), which needed no learning.")
    elif edge <= 0:
        print("\n  VERDICT: NO LEARNING. A policy trained on SHUFFLED outcomes does")
        print("  as well or better out of sample — the training gain was noise.")
    else:
        print("\n  Beats the null arm, sitting flat, and both trivial baselines.")
        print("  STILL NOT AN EDGE: it must next clear the detection floor at")
        print("  n = SESSIONS and survive a pre-registered rerun on untouched data.")

    if always_s > 0 and always_s > real_te:
        print(f"\n  WATCH: 'always short' scores {always_s:+.4f} on the held-out")
        print("  period and beats the learned policy. That is almost certainly a")
        print("  directional drift in this particular window, not a short edge —")
        print("  and it is exactly the artifact a naive read would promote.")

    w, b, thr = unpack(theta)
    print(f"\n  what it learned to look at (|weight|, top 5):")
    for i in np.argsort(np.abs(w))[::-1][:5]:
        print(f"    {FEATURE_NAMES[i]:<18} {w[i]:+.3f}")
    print(f"    bias {b:+.3f}   threshold {thr:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
