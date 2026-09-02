#!/usr/bin/env python3
"""scripts/mech006_test.py — the last open hypothesis on the entry layer.

MECH-006, as written in MECHANISMS.json:
    "AlphaZero's entry veto carries information a rate-matched coin does not:
     the days it refuses are worse than average, not merely fewer."

THE TEST
    39 bars the policy ENTERED against 39 it REFUSED, sampled at the same rate
    and stratified across sessions. If the veto carries information, the bars
    it took should show more forward opportunity than the ones it declined.

    Forward magnitude (`forward_range_atr`) is the comparison quantity: realized
    range over the following window, normalized by ATR at the decision. Not a
    return -- magnitude is the one thing this repo has measured as detectable.

WHY THE FLOOR IS PRINTED BESIDE THE EFFECT, ALWAYS
    n = 39 per arm is thin, and at that n the detection floor will probably be
    larger than any effect present. An effect smaller than the floor is not a
    weak result; it is an unmeasured one, and the two must never be reported as
    if they were the same thing.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "gate"), str(ROOT / "daytrade")]

from body.disengagement import ARM_CONTROL, ARM_ENTRY, DECISIONS, read_jsonl  # noqa: E402
from body.disengagement_outcomes import OUTCOMES  # noqa: E402
from discovery import Finding  # noqa: E402


def main() -> int:
    dec = read_jsonl(DECISIONS)
    out = {o["row_id"]: o for o in read_jsonl(OUTCOMES)}
    arms = {ARM_ENTRY: [], ARM_CONTROL: []}
    for d in dec:
        arms[d["arm"]].append(float(out[d["row_id"]]["forward_range_atr"]))

    e, c = arms[ARM_ENTRY], arms[ARM_CONTROL]
    print("MECH-006 — does the entry veto carry information?\n")
    print('  "the days it refuses are worse than average, not merely fewer"\n')
    print(f"  {'arm':<16}{'n':>5}{'mean fwd range (ATR)':>24}{'median':>10}")
    print(f"  {'ENTERED':<16}{len(e):>5}{st.mean(e):>24.4f}{st.median(e):>10.4f}")
    print(f"  {'REFUSED (control)':<16}{len(c):>5}{st.mean(c):>24.4f}{st.median(c):>10.4f}")

    diff = st.mean(e) - st.mean(c)
    n_eff = (len(e) * len(c)) / (len(e) + len(c))
    sd = st.pstdev(e + c)
    f = Finding(name="MECH-006 veto information", observed_effect=diff,
                per_unit_sd=sd, n_units=int(n_eff), unit="decisions",
                source="data/daytrade/disengagement/")
    print(f"\n  effect (entered - refused): {diff:+.4f} ATR")
    print(f"  pooled sd {sd:.4f}   n_eff {n_eff:.1f}   MDE {f.mde:.4f}")
    print(f"  ratio MDE/|effect| = {f.ratio:.2f}x  ->  {f.verdict}")

    print()
    if not f.proceeds:
        print("  VERDICT: UNMEASURED, NOT REFUTED.")
        print(f"  The effect is {f.ratio:.1f}x smaller than the smallest thing 39")
        print("  decisions per arm could detect. This does not say the veto is")
        print("  worthless — it says this sample cannot tell, and reporting it as")
        print("  a null would be claiming knowledge the data does not contain.")
        need = (sd * 2.487 / abs(diff)) ** 2 * 2 if diff else float("inf")
        print(f"\n  To decide it: ~{need:.0f} decisions per arm at this effect size,")
        print(f"  against the {len(e)} available. That is the cost of an answer.")
    else:
        print("  VERDICT: MEASURABLE. The effect clears its own detection floor.")
        print("  Next it needs a pre-registered rerun on decisions not yet seen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
