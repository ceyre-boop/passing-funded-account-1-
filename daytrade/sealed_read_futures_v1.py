#!/usr/bin/env python3
"""SEALED-HOLDOUT READ — futures-exit-v1. One shot, authorized by Colin
2026-08-17 ("run the sealed read on futures-exit-v1").

PRE-REGISTERED VERDICT RULE, written before any sealed number was seen and
committed before this script ever ran:

  On the sealed sessions (> TUNE_END 2026-07-06) of the FUTURES class
  (ES=F, NQ=F, RTY=F, CL=F), same entry rule, same simulator:

    VALIDATED iff  mean_R(candidate) > 0
              AND  mean_R(candidate) > mean_R(TRAIL_WIDE)   [best shipped on tune]
    else NOT_VALIDATED.

  The tune-split margin was +0.086; the sealed rule deliberately requires
  only directional confirmation (>0 and beats shipped), because the sealed
  futures sample is small (~19 sessions) and demanding the full margin of a
  65-entry estimate from a ~15-entry read would make NOT_VALIDATED nearly
  automatic regardless of truth. Registered now, argued never.

  Whatever the verdict: it is final for futures-exit-v1. A NOT_VALIDATED
  candidate is retired, not re-run against this holdout with tweaks.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_sessions, BarDataError                       # noqa: E402
from splits import sealed_sessions, TUNE_END                       # noqa: E402
from ceiling import find_entry, simulate                           # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade" / "sealed_read_futures_v1.json"

RULE_VERSION = "futures-exit-v1"
SYMBOLS = ("ES=F", "NQ=F", "RTY=F", "CL=F")
CANDIDATE = {"trail_mult": None, "be_arm_frac": 0.25, "partial_frac": 0.75,
             "flatten_et": "12:00", "hold_past_tp2": True}
SHIPPED_BASELINE = {"trail_mult": 1.5, "be_arm_frac": 1.0, "partial_frac": 0.5,
                    "flatten_et": None, "hold_past_tp2": True}   # TRAIL_WIDE


def main() -> int:
    if OUT.exists():
        raise SystemExit(f"{OUT.name} already exists — the read was designed "
                         "to happen once. Refusing a quiet second look.")

    entries = []
    for sym in SYMBOLS:
        try:
            sess = load_sessions(sym, "5m", allow_fetch=False)
        except BarDataError as e:
            print(f"  !! {sym}: {e} — excluded loudly")
            continue
        held = sealed_sessions(
            sess,
            unseal_reason=("one-shot validation of futures-exit-v1 (frozen "
                           "2026-08-17, candidate from the per-class furnace; "
                           "spec-025 evaluator returned NO_SUPERSEDE so the "
                           "static config is the confirmed object). Baseline "
                           "TRAIL_WIDE read on the same entries as part of "
                           "this single judgment. Authorized by Colin."),
            rule_version=RULE_VERSION)
        for s in held:
            e = find_entry(s)
            if e:
                entries.append((sym, s, e))

    if not entries:
        raise SystemExit("no sealed entries found — nothing was read")

    cand = [(sym, e.day, simulate(s, e, dict(CANDIDATE))) for sym, s, e in entries]
    ship = [(sym, e.day, simulate(s, e, dict(SHIPPED_BASELINE))) for sym, s, e in entries]
    cand_mean = sum(r for _, _, r in cand) / len(cand)
    ship_mean = sum(r for _, _, r in ship) / len(ship)

    validated = cand_mean > 0 and cand_mean > ship_mean
    verdict = "VALIDATED" if validated else "NOT_VALIDATED"

    result = {
        "rule_version": RULE_VERSION, "read_at": datetime.now(timezone.utc).isoformat(),
        "sealed_boundary": f"> {TUNE_END}", "n_entries": len(cand),
        "symbols": sorted({sym for sym, _, _ in cand}),
        "candidate_mean_R": round(cand_mean, 4),
        "shipped_TRAIL_WIDE_mean_R": round(ship_mean, 4),
        "margin_R": round(cand_mean - ship_mean, 4),
        "candidate_trades": [{"sym": s, "day": d, "r": round(r, 3)} for s, d, r in cand],
        "verdict": verdict,
        "rule": "VALIDATED iff cand_mean > 0 AND cand_mean > shipped_mean; final either way",
    }
    OUT.write_text(json.dumps(result, indent=1))

    print(f"\n  SEALED READ — {RULE_VERSION} — {len(cand)} entries, "
          f"{len(result['symbols'])} symbols, sessions > {TUNE_END}")
    print(f"  candidate   mean {cand_mean:+.4f} R/trade")
    print(f"  TRAIL_WIDE  mean {ship_mean:+.4f} R/trade")
    print(f"  margin      {cand_mean - ship_mean:+.4f}")
    print(f"\n  VERDICT: {verdict}  (final for this rule_version)")
    print(f"  record: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
