#!/usr/bin/env python3
"""THE MECHANISM LEDGER — spec 029.

A killed parameter teaches nothing; a killed MECHANISM removes
hypothesis-space volume permanently. Every entry here states a causal reason
and predicts where else that reason must hold, then earns or loses that claim
on a day-blocked permutation test across asset classes.

Discipline enforced mechanically (spec 029 I28-I35):
  - no transfer prediction -> refused (it is a parameter, not a mechanism)
  - no numeric predicted effect -> refused (nothing to calibrate against)
  - predicted effect sealed BEFORE the test runs (028's blind pre-registration)
  - the permutation null is DAY-BLOCKED (same-day cross-symbol entries are
    nearly one bet; the ungrouped version is the error specs/026 admits)
  - the predictor's own past optimism shrinks their next proposal
  - a retired axis cannot be re-swept without a new mechanism

This file generates NO hypotheses (CLAUDE.md non-negotiable 1). It records
mechanisms behind results this repo already produced, and emits genuinely new
edges as candidates FOR THE GENERAL REPO rather than testing them here.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "MECHANISMS.json"

CLASS_NAMES = ("SINGLE_NAME", "CASH_INDEX", "FUTURES")
WEIGHTS = {"help": 1.0, "neutral": 0.0, "hurt": -1.0}
# G_DEDUPE (adversarial review 2026-08-19): SPY/ES=F delta-correlation 0.714,
# QQQ/NQ=F 0.902, IWM/RTY=F 0.790, 100% direction agreement. "Confirmed in
# CASH_INDEX and FUTURES" is ONE market counted twice. Effective independent
# instruments measured at 8.7, not 16 — carried here so no result can quote a
# breadth it does not have.
COMPLEXES = {"SPY": "SP", "ES=F": "SP", "QQQ": "NDX", "NQ=F": "NDX",
             "IWM": "RUT", "RTY=F": "RUT"}
K_EFF = 8.7

N_PERM = 1000
ALPHA = 0.05
Z_ALPHA, Z_POWER = 1.645, 0.842          # one-sided 95%, 80% power


class MechanismError(RuntimeError):
    """A ledger rule being argued with. Never downgraded to a warning."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if not LEDGER.exists():
        return {"spec": "029", "mechanisms": [], "calibration_seeds": []}
    try:
        return json.loads(LEDGER.read_text())
    except json.JSONDecodeError as e:
        raise MechanismError(f"MECHANISMS.json is not JSON ({e}) — refusing to guess")


def _save(reg: dict) -> None:
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1))
    os.replace(tmp, LEDGER)


def _find(reg: dict, mid: str) -> dict:
    for m in reg["mechanisms"]:
        if m["id"] == mid:
            return m
    raise MechanismError(f"{mid} is not in the ledger")


# ------------------------------------------------------------- calibration

def predicted_of(m: dict):
    """The numeric prediction, in whatever metric the entry DECLARED. A
    mechanism whose effect is not R/trade must still predict a number — it
    just names its own unit. Calibration ratios are unit-free, so a
    proportion-difference and an R/trade both answer the same question:
    how overconfident was this predictor."""
    if m.get("predicted_effect_r") is not None:
        return m["predicted_effect_r"]
    return m.get("predicted_effect")


def _resolved_ratios(reg: dict, predictor: str) -> list[float]:
    """realized/predicted for every resolved prediction by this predictor,
    seeds included. The predictor's own history, nothing else."""
    out = []
    for s in reg.get("calibration_seeds", []):
        if (s["predicted_by"] == predictor and s.get("pre_registered")
                and s.get("realized_effect_r") is not None):
            if s["predicted_effect_r"]:
                out.append(s["realized_effect_r"] / s["predicted_effect_r"])
    for m in reg["mechanisms"]:
        if m.get("recorded_retrospectively"):
            continue          # history, not a blind prediction — never calibrates
        pred = predicted_of(m)
        if (m.get("predicted_by") == predictor
                and m.get("realized_effect_r") is not None and pred):
            out.append(m["realized_effect_r"] / pred)
    return out


PSEUDO_COUNT = 2.0        # partial-pooling weight toward "no shrinkage"


def shrinkage(reg: dict, predictor: str) -> tuple[float, int]:
    """Shrinkage factor for a predictor, PARTIALLY POOLED toward 1.0.

    The raw statistic is the median realized/predicted ratio, clamped to
    [0, 1] — a predictor who under-promises is not rewarded with inflation.
    But one miss must not zero a predictor forever, so the estimate is pooled
    with PSEUDO_COUNT=2 observations of "no shrinkage":

        factor = (n * median_ratio + k * 1.0) / (n + k)

    n=1 miss -> 0.33x, n=5 misses -> 0.14x, and a genuinely calibrated
    predictor climbs back. Evidence accumulates; a single bad night does not
    become a permanent sentence, and a run of them does."""
    ratios = _resolved_ratios(reg, predictor)
    if not ratios:
        return 1.0, 0
    raw = max(0.0, min(1.0, statistics.median(ratios)))
    n = len(ratios)
    return (n * raw + PSEUDO_COUNT) / (n + PSEUDO_COUNT), n


def mde(sigma_per_day: float, n_day_clusters: int) -> float:
    """Minimum detectable effect at one-sided 95% / 80% power.
    Below this, a test can only produce noise wearing a result's clothes."""
    if n_day_clusters <= 0:
        raise MechanismError("no day clusters — MDE undefined")
    return (Z_ALPHA + Z_POWER) * sigma_per_day / (n_day_clusters ** 0.5)


# ---------------------------------------------------------- the transfer test

def contrast(deltas_by_class: dict[str, list[float]], pattern: dict) -> float:
    """T = sum_c w_c * mean(paired deltas in class c). Weights from the
    PRE-REGISTERED sign pattern, never from the data."""
    total = 0.0
    for cls, ds in deltas_by_class.items():
        if not ds:
            continue
        total += WEIGHTS[pattern[cls]] * (sum(ds) / len(ds))
    return total


def permutation_p(rows: list[tuple], pattern: dict, observed: float,
                  n_perm: int = N_PERM, seed: int = 29,
                  day_blocked: bool = True) -> float:
    """rows: (day, class, delta). Permute CLASS LABELS **within calendar-day
    blocks**, preserving each entry's own delta.

    day_blocked=False exists ONLY so a named test can prove the ungrouped
    null is wrong (spec 029 I30) — it must never be used for a verdict.
    """
    import random
    rng = random.Random(seed)
    by_day: dict[str, list] = defaultdict(list)
    for day, cls, d in rows:
        by_day[day].append((cls, d))

    hits = 0
    for _ in range(n_perm):
        shuffled: dict[str, list[float]] = defaultdict(list)
        if day_blocked:
            for day, items in by_day.items():
                labels = [c for c, _ in items]
                rng.shuffle(labels)
                for lbl, (_, d) in zip(labels, items):
                    shuffled[lbl].append(d)
        else:
            labels = [c for _, c, _ in rows]
            rng.shuffle(labels)
            for lbl, (_, _, d) in zip(labels, rows):
                shuffled[lbl].append(d)
        if contrast(shuffled, pattern) >= observed:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def placebo_excess(rows_by_cfg: dict, pattern: dict, bite: float,
                   observed: float) -> tuple[float, int]:
    """G_PLACEBO_FLOOR. ~99% of the exit vocabulary produces the sign pattern
    'hurts single names, helps index/futures' — single names carry the fattest
    right tail and every config caps upside, so the pattern is a property of
    the INSTRUMENTS, not of any mechanism. The reportable statistic is
    therefore the EXCESS over what a mechanism-free perturbation of the same
    bite rate delivers. Confirmed empirically by the FPR harness: the largest
    contrast among 20 random null mechanisms (+0.250) went to exactly that
    generic pattern."""
    ref = [contrast(bc, pattern) for b, bc in rows_by_cfg.values()
           if abs(b - bite) <= 0.06]
    if not ref:
        return observed, 0
    return observed - (sum(ref) / len(ref)), len(ref)


def run_transfer_test(m: dict, symbols: list[str] | None = None) -> dict:
    """Paired per-entry deltas (config ON vs OFF) over the tune split,
    scored against the pre-registered sign pattern."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from bars import load_sessions, BarDataError
    from splits import tune_sessions
    from ceiling import find_entry, simulate
    from stockfish_tune import CLASSES

    sym_class = {s: c for c, syms in CLASSES.items() for s in syms}
    syms = symbols or [s for v in CLASSES.values() for s in v]

    rows, by_class = [], defaultdict(list)
    per_day: dict[str, list[float]] = defaultdict(list)
    syms_seen: set = set()
    for sym in syms:
        try:
            sessions = tune_sessions(load_sessions(sym, "5m", allow_fetch=False))
        except BarDataError as e:
            print(f"  !! {sym}: {e} — excluded loudly")
            continue
        for s in sessions:
            e = find_entry(s)
            if e is None:
                continue
            d = (simulate(s, e, dict(m["config_on"]))
                 - simulate(s, e, dict(m["config_off"])))
            cls = sym_class[sym]
            rows.append((str(s.day), cls, d))
            by_class[cls].append(d)
            per_day[str(s.day)].append(d)
            syms_seen.add(sym)

    if not rows:
        raise MechanismError("no entries — nothing to test")

    pattern = m["transfer_prediction"]
    observed = contrast(by_class, pattern)
    p = permutation_p(rows, pattern, observed)

    # realized sign per class, and whether it matches every weighted claim
    realized = {c: (sum(v) / len(v) if v else None) for c, v in by_class.items()}
    sign_ok = True
    for c, want in pattern.items():
        got = realized.get(c)
        if got is None or WEIGHTS[want] == 0:
            continue
        if (got > 0) != (WEIGHTS[want] > 0):
            sign_ok = False

    day_means = [sum(v) / len(v) for v in per_day.values()]
    sigma = statistics.pstdev(day_means) if len(day_means) > 1 else 0.0
    detectable = mde(sigma, len(per_day))

    # Verdicts, corrected 2026-08-19 after the FPR harness. Measured on 20
    # NULL mechanisms with random sign patterns: 0/20 false CONFIRMED (the
    # day-blocked null held), but 45% drew a "supportive" read under the old
    # NARROWED state — sign-pattern matching alone carries almost no
    # information. NARROWED is therefore gone: an underpowered result is
    # INDETERMINATE, which is neither support nor a kill and writes no dead
    # axis (G_KILL_REQUIRES_POWER — killing a live hypothesis on noise is the
    # more expensive error).
    pred = predicted_of(m)
    powered = abs(observed) >= detectable
    if p < ALPHA and sign_ok and powered:
        verdict = "CONFIRMED_CANDIDATE"     # tune split can never CONFIRM
    elif p > 0.50 and pred is not None and abs(observed - pred) > detectable:
        verdict = "KILLED"                  # CI excludes the registered effect
    elif p < ALPHA and not sign_ok:
        verdict = "KILLED"                  # real, but not the stated claim
    else:
        verdict = "INDETERMINATE"

    complexes = {COMPLEXES.get(sym, sym) for sym in syms_seen}
    return {"tested_at": _now(), "n_entries": len(rows),
            "n_complexes": len(complexes), "k_eff_registered": K_EFF,
            "n_day_clusters": len(per_day),
            "per_class_mean_delta": {c: (round(v, 4) if v is not None else None)
                                     for c, v in realized.items()},
            "contrast": round(observed, 4), "p_value": round(p, 4),
            "sign_pattern_matched": sign_ok,
            "per_day_sigma": round(sigma, 4),
            "minimum_detectable_effect_r": round(detectable, 4),
            "realized_effect_r": round(observed, 4),
            "verdict": verdict}


def run_soak_test(m: dict) -> dict:
    """The SOAK evidence channel (spec 029): live-forward veto information.

    A veto overlay on a weak-expectancy entry improves measured R by trading
    less of something bad, information or not — so the comparison is against
    `expected_rate_random_veto`, NEVER baseline. Sessions are reconstructed
    with the same OR-break rule the furnace uses, so no new entry logic
    enters here.

    Reports DIVERGENCE EVENTS as the real sample size: a session where the
    veto book and the control made the same choice carries no information
    about the claim, however many bars it contains.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from bars import load_sessions, BarDataError
    from ceiling import find_entry
    import four_books as fb

    records = [json.loads(l) for l in (ROOT / "data" / "daytrade" / "operator"
                                       / "records.jsonl").open() if l.strip()]
    if not records:
        raise MechanismError("no operator records — the soak has produced nothing")
    symbols = sorted({r["symbol"] for r in records})
    soak_days = sorted({r["ts"][:10] for r in records})

    per_session, diverged = [], 0
    for sym in symbols:
        try:
            sessions = load_sessions(sym, "5m", allow_fetch=False)
        except BarDataError as e:
            print(f"  !! {sym}: {e} — excluded loudly")
            continue
        for sess in sessions:
            if str(sess.day) not in soak_days:
                continue                      # live-forward only, never backfill
            e = find_entry(sess)
            if e is None:
                continue
            plan = {"symbol": sym, "direction": e.direction, "entry": e.entry,
                    "sl": e.stop, "qty": 1.0, "tp1": e.tp1, "tp2": e.tp2,
                    "trail_dist": e.trail_dist, "exit_policy": "DEFAULT",
                    "risk_per_share": e.risk}
            veto = fb.run_book("veto", sess.df.copy(), plan, records, sym)
            ctrl = fb.run_book("expected_rate_random_veto", sess.df.copy(), plan,
                               records, sym)
            same = (veto["entered"] == ctrl["entered"])
            diverged += 0 if same else 1
            per_session.append({"day": str(sess.day), "symbol": sym,
                                "veto_r": veto["r"], "control_r": ctrl["r"],
                                "veto_entered": veto["entered"],
                                "control_entered": ctrl["entered"],
                                "diverged": not same})

    n = len(per_session)
    if n == 0:
        return {"verdict": "NO_SAMPLE", "n_sessions": 0, "n_divergences": 0,
                "detail": "no soak-window session produced an OR-break entry — "
                          "the books never had a decision to disagree about"}
    edge = sum(s["veto_r"] - s["control_r"] for s in per_session) / n
    sigma = (statistics.pstdev([s["veto_r"] - s["control_r"] for s in per_session])
             if n > 1 else 0.0)
    detectable = mde(sigma, n) if sigma > 0 else float("inf")
    pred = predicted_of(m) or 0.0
    sessions_needed = (((Z_ALPHA + Z_POWER) * sigma / pred) ** 2
                       if sigma > 0 and pred else None)

    verdict = ("EMPTY_CHANNEL" if diverged == 0
               else "UNMEASURABLE" if abs(edge) < detectable else "MEASURED")
    return {"verdict": verdict, "tested_at": _now(), "n_sessions": n,
            "n_divergences": diverged, "edge_vs_control_r": round(edge, 4),
            "per_session_sigma": round(sigma, 4),
            "minimum_detectable_effect_r": (round(detectable, 4)
                                            if detectable != float("inf") else None),
            "sessions_needed_for_predicted_effect": (round(sessions_needed)
                                                     if sessions_needed else None),
            "sessions": per_session}


# --------------------------------------------------------------- commands

def propose(args) -> int:
    reg = _load()
    pattern = {}
    for tok in (args.transfer or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        cls, _, kind = tok.partition(":")
        if cls not in CLASS_NAMES or kind not in WEIGHTS:
            raise MechanismError(
                f"bad transfer term {tok!r} — use CLASS:help|neutral|hurt over "
                f"{CLASS_NAMES}")
        pattern[cls] = kind
    if not pattern:
        raise MechanismError(
            "no transfer prediction — a claim that does not say where else it "
            "must hold is a PARAMETER, not a mechanism (I28). State it as "
            "e.g. --transfer 'FUTURES:help,SINGLE_NAME:hurt,CASH_INDEX:neutral'")
    if args.predicted_effect is None:
        raise MechanismError("no predicted effect — nothing to calibrate against (I29)")

    factor, n_prior = shrinkage(reg, args.by)
    calibrated = args.predicted_effect * factor
    mid = f"MECH-{len(reg['mechanisms']) + 1:03d}"
    entry = {
        "id": mid, "claim": args.claim, "transfer_prediction": pattern,
        "predicted_effect_r": (args.predicted_effect if args.metric == "R_per_trade"
                               else None),
        "predicted_effect": args.predicted_effect, "effect_metric": args.metric,
        "band": args.band,
        "calibrated_effect_r": round(calibrated, 4),
        "shrinkage_applied": round(factor, 4), "n_prior_predictions": n_prior,
        "predicted_by": args.by, "proposed_at": _now(),
        "structural_vs_lever": {"structural": "unknown", "lever": "unknown"},
        "status": "proposed", "config_on": None, "config_off": None,
    }
    if all(WEIGHTS[v] >= 0 for v in pattern.values()):
        entry["flags"] = ["unfalsifiable_shape: predicts no class where it must HURT"]
    reg["mechanisms"].append(entry)
    _save(reg)
    print(f"  PROPOSED {mid}: {args.claim}")
    print(f"  transfer: {pattern}")
    print(f"  predicted {args.predicted_effect:+.3f} R/trade -> calibrated "
          f"{calibrated:+.3f} (shrink {factor:.2f} from {n_prior} prior prediction(s))")
    if entry.get("flags"):
        print(f"  !! {entry['flags'][0]}")
    print("  seal it before testing:  python3 daytrade/seals.py seal MECHANISMS.json "
          "--condition 'spec 029: unsealed only to record a test result'")
    return 0


def test(args) -> int:
    reg = _load()
    m = _find(reg, args.id)
    if not (m.get("config_on") and m.get("config_off")):
        raise MechanismError(f"{args.id} has no config_on/config_off — a mechanism "
                             "must name the two arms it is tested between")
    if m["status"] != "proposed":
        raise MechanismError(f"{args.id} is {m['status']}; a resolved mechanism is "
                             "not re-tested against the same split")
    result = run_transfer_test(m)
    if abs(m["calibrated_effect_r"]) < result["minimum_detectable_effect_r"]:
        print(f"  !! calibrated effect {m['calibrated_effect_r']:+.3f} is below the "
              f"MDE {result['minimum_detectable_effect_r']:.3f} for "
              f"{result['n_day_clusters']} day-clusters — this test cannot "
              "distinguish the claim from noise. Recorded as UNMEASURABLE.")
        result["verdict"] = "UNMEASURABLE"
    m.update(result)
    # INDETERMINATE and UNMEASURABLE leave the mechanism PROPOSED: neither is
    # support, neither is a kill, and neither may write a dead axis.
    m["status"] = {"CONFIRMED_CANDIDATE": "candidate", "KILLED": "killed",
                   "INDETERMINATE": "proposed",
                   "UNMEASURABLE": "proposed"}[result["verdict"]]
    if result["verdict"] == "KILLED":
        m["dead_axes"] = [{"axis": k, "classes": [c for c, w in
                           m["transfer_prediction"].items() if WEIGHTS[w] != 0]}
                          for k in m["config_on"] if
                          m["config_on"][k] != m["config_off"].get(k)]
    _save(reg)
    for k in ("n_entries", "n_day_clusters", "per_class_mean_delta", "contrast",
              "p_value", "sign_pattern_matched", "minimum_detectable_effect_r"):
        print(f"  {k:28s} {result[k]}")
    print(f"\n  VERDICT: {result['verdict']}")
    return 0


def calibrate(args) -> int:
    reg = _load()
    predictors = {s["predicted_by"] for s in reg.get("calibration_seeds", [])}
    predictors |= {m["predicted_by"] for m in reg["mechanisms"] if m.get("predicted_by")}
    print(f"  {'predictor':16s} {'n':>3s} {'shrinkage':>10s}   ratios")
    for p in sorted(predictors):
        f, n = shrinkage(reg, p)
        ratios = [round(r, 3) for r in _resolved_ratios(reg, p)]
        print(f"  {p:16s} {n:3d} {f:10.3f}   {ratios}")
    print("\n  shrinkage multiplies the NEXT proposal's predicted effect; a "
          "proposal whose shrunk effect sits under the MDE is refused (I33).")
    return 0


def axes(args) -> int:
    reg = _load()
    dead = [(m["id"], a) for m in reg["mechanisms"] if m["status"] == "killed"
            for a in m.get("dead_axes", [])]
    print("  DEAD (re-sweeping these requires a NEW mechanism claim — I32):")
    for mid, a in dead:
        print(f"    {a['axis']:16s} in {a['classes']}   [{mid}]")
    if not dead:
        print("    (none yet)")
    earned = [(m["id"], m.get("earned_axes", [])) for m in reg["mechanisms"]
              if m["status"] == "confirmed"]
    print("  EARNED (licensed by a confirmed mechanism):")
    for mid, ax in earned:
        print(f"    {ax}   [{mid}]")
    if not earned:
        print("    (none — nothing is confirmed yet)")
    return 0


def check(quiet: bool = False) -> int:
    """Ledger integrity — suite-enforced, same guard shape as 028."""
    reg = _load()
    bad = []
    for m in reg["mechanisms"]:
        if not m.get("transfer_prediction"):
            bad.append(f"{m['id']}: no transfer prediction (I28)")
        if predicted_of(m) is None:
            bad.append(f"{m['id']}: no predicted effect in any declared metric (I29)")
        elif (m.get("predicted_effect_r") is None
              and not m.get("effect_metric")):
            bad.append(f"{m['id']}: predicts a number but names no metric (I29)")
        sv = m.get("structural_vs_lever")
        if not isinstance(sv, dict) or set(sv) != {"structural", "lever"}:
            bad.append(f"{m['id']}: structural/lever axes missing (I34)")
        if m["status"] not in ("proposed", "candidate", "confirmed",
                               "killed", "narrowed"):
            bad.append(f"{m['id']}: unknown status {m['status']!r}")
    if bad:
        for b in bad:
            print(f"  !! LEDGER VIOLATION: {b}")
        return 1
    if not quiet:
        print(f"  {len(reg['mechanisms'])} mechanism(s) verified, 0 violations")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 029 — the mechanism ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--claim", required=True)
    p.add_argument("--transfer", help="CLASS:help|neutral|hurt, comma separated")
    p.add_argument("--predicted-effect", type=float, dest="predicted_effect")
    p.add_argument("--band", default=None)
    p.add_argument("--metric", default="R_per_trade",
                   help="unit of the predicted effect; non-R metrics must be named")
    p.add_argument("--by", default=os.environ.get("USER", "unknown"))
    t = sub.add_parser("test"); t.add_argument("id")
    ts = sub.add_parser("test-soak"); ts.add_argument("id")
    sub.add_parser("calibrate")
    sub.add_parser("axes")
    sub.add_parser("check")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "propose":
            return propose(a)
        if a.cmd == "test":
            return test(a)
        if a.cmd == "test-soak":
            reg = _load(); m = _find(reg, a.id)
            res = run_soak_test(m)
            for k, v in res.items():
                if k != "sessions":
                    print(f"  {k:38s} {v}")
            m["soak_evidence"] = {k: v for k, v in res.items() if k != "sessions"}
            _save(reg)
            return 0
        if a.cmd == "calibrate":
            return calibrate(a)
        if a.cmd == "axes":
            return axes(a)
        return check()
    except MechanismError as e:
        print(f"  REFUSED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
