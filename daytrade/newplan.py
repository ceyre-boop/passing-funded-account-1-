#!/usr/bin/env python3
"""NEWPLAN — turn a fill into a blueprint, at speed.

The doctrine says the blueprint is set BEFORE the open. Almost all of it can be:
the exit policy, the R geometry, the flatten time — those are decisions, and
decisions made at 09:31 are worse than decisions made at 08:45. What genuinely
cannot be known pre-open is the fill and the stop.

So the policy block is committed the night before, and this merges the two
numbers you learn at the break into a complete plan.json. Two numbers typed
under time pressure instead of eight.

    python3 daytrade/newplan.py --entry 207.10 --stop 206.55 --qty 100 --direction long

Everything it writes is echoed back before the runner ever sees it. Read the
echo. A transposed digit in the stop is a real trade with a real wrong risk.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "data" / "daytrade" / "policy_today.json"
PLAN = ROOT / "data" / "daytrade" / "plan.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="fill + stop -> plan.json")
    ap.add_argument("--entry", type=float, required=True, help="your actual fill")
    ap.add_argument("--stop", type=float, required=True, help="far side of the trigger bar")
    ap.add_argument("--qty", type=float, required=True, help="shares actually filled")
    ap.add_argument("--direction", choices=("long", "short"), required=True)
    ap.add_argument("--policy", default=str(POLICY))
    ap.add_argument("--out", default=str(PLAN))
    a = ap.parse_args(argv)

    pol = json.loads(Path(a.policy).read_text())
    d = 1 if a.direction == "long" else -1
    risk = abs(a.entry - a.stop)
    if risk <= 0:
        print("!! entry and stop are equal — no risk geometry, nothing to manage")
        return 1
    # A stop on the wrong side of the fill is the single most expensive typo
    # available at 09:31, and it is trivially detectable. Refuse it.
    if (d > 0 and a.stop >= a.entry) or (d < 0 and a.stop <= a.entry):
        print(f"!! {a.direction} with stop {a.stop} on the wrong side of entry {a.entry}")
        return 1

    plan = {k: v for k, v in pol.items() if not k.startswith("_")}
    plan.update({"direction": a.direction, "entry": a.entry, "sl": a.stop, "qty": a.qty})

    Path(a.out).write_text(json.dumps(plan, indent=1))

    from runner import load_plan
    p = load_plan(Path(a.out))
    goal = risk * float(pol.get("tp2_r", 0)) * a.qty
    print(f"  wrote {a.out}\n")
    print(f"  {plan['symbol']} {a.direction.upper()} {a.qty:g} @ {a.entry}")
    print(f"  stop {a.stop}   risk ${risk:.2f}/share = ${risk*a.qty:.2f} total")
    print(f"  TP1  {p['tp1']:.2f}  ({pol.get('tp1_r')}R — breakeven arms at "
          f"{pol.get('be_arm_frac', 1.0)} of this)")
    print(f"  TP2  {p['tp2']:.2f}  ({pol.get('tp2_r')}R = ${goal:.0f} day goal)")
    print(f"  trail {p['trail_dist']:.2f}  mult {pol.get('trail_mult')}   "
          f"flatten {pol.get('flatten_at_et')} ET")
    print(f"\n  read those numbers back before starting the runner.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
