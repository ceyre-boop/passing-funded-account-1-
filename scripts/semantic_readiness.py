#!/usr/bin/env python3
"""scripts/semantic_readiness.py — how far is the one untested information source?

WHY THIS LANE IS DIFFERENT FROM EVERY CLOSED ONE
    Every null in this repository is on PRICE-DERIVED features: the entry grid,
    the exit sweep, the four event studies, the self-play learner, the three
    learned-exit attempts. Nine independent nulls, all asking one question --
    does the recent price path predict the next price path on SPY intraday.

    The operator asks a different question. It reads news, classifies evidence,
    and forms a semantic judgment. That information source has never been tested
    at any scale, and thirteen resolved judgments is not a test.

WHAT THIS SCRIPT IS NOT
    Not a search, and not an attempt to squeeze a verdict out of 13 rows. It is
    arithmetic: what effect is even detectable at the n available, what the gate
    demands, and therefore how far away a real answer is. The output is a
    distance, not a finding.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "gate"), str(ROOT / "daytrade")]

from mechanisms import mde  # noqa: E402

FC = ROOT / "data" / "daytrade" / "operator" / "forecasts.jsonl"
GATE_MIN_DECISIONS = 50
N_SCENARIOS = 5


def main() -> int:
    rows = [json.loads(l) for l in FC.read_text().splitlines() if l.strip()]
    fc = [r for r in rows if r["kind"] == "forecast"]
    res = [r for r in rows if r["kind"] == "resolution"]
    unres = [r for r in rows if r["kind"] == "unresolvable"]

    print("SEMANTIC LANE — the one information source never tested\n")
    print(f"  forecasts written      {len(fc)}")
    print(f"  resolved               {len(res)}")
    print(f"  unresolvable           {len(unres)}")
    print(f"  still open             {len(fc) - len(res) - len(unres)}")
    days = sorted({r['as_of'][:10] for r in fc})
    print(f"  distinct days          {len(days)}   ({days[0]} .. {days[-1]})")

    print(f"\n  outcome mix over the {len(res)} resolved:")
    for k, v in Counter(r["outcome_scenario"] for r in res).most_common():
        print(f"    {k:<24}{v:>3}")
    stale = sum(1 for r in res if r.get("was_stale"))
    regret = sum(1 for r in res if r.get("policy_regret_r") is not None)
    print(f"\n  stale at decision time {stale}/{len(res)}")
    print(f"  carrying policy_regret {regret}/{len(res)}   "
          f"<- the tail gate's supplier")

    # what n buys, in Brier units. sd of a Brier score over 5 scenarios is ~0.3.
    sd = 0.30
    print(f"\n  DETECTABLE EFFECT (Brier, assumed sd {sd}):")
    for n in (len(res), GATE_MIN_DECISIONS, 150, 500):
        tag = ""
        if n == len(res):
            tag = "  <- what exists"
        if n == GATE_MIN_DECISIONS:
            tag = "  <- gate minimum"
        if n == N_SCENARIOS * 3 * 10:
            tag = "  <- needed for 3-regime stratification"
        print(f"    n={n:<5} MDE = {mde(sd, n):.4f}{tag}")

    print(f"\n  DISTANCE TO A REAL TEST")
    print(f"    {GATE_MIN_DECISIONS - len(res)} more resolved judgments to reach the gate minimum,")
    print(f"    and {N_SCENARIOS * 3 * 10 - len(res)} more before a 3-regime stratification means anything.")
    rate = len(fc) / max(1, len(days))
    print(f"    At the observed {rate:.1f} forecasts/active-day, that is "
          f"{(GATE_MIN_DECISIONS - len(res)) / rate:.0f} active days —")
    print(f"    but only {len(days)} active days exist across the whole span, so the")
    print(f"    binding constraint is SESSIONS RUN, not forecasts per session.")

    print(f"\n  AND IT CANNOT BE BACKTESTED. The forecaster is an LLM with a")
    print(f"  training cutoff; pointed at a historical session it may already know")
    print(f"  how the day ended. Sealing is the whole mechanism, and you cannot")
    print(f"  retro-seal against a model that might remember. This lane only")
    print(f"  accumulates forward, in real time, one session at a time.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
