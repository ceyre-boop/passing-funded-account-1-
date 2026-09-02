#!/usr/bin/env python3
"""scripts/adjudicate.py — judge ODD §5 disengagement rows, blind.

WHAT THIS TOOL CANNOT DO, BY CONSTRUCTION
    It cannot see how any decision turned out. The outcome path lives in
    `body/disengagement_outcomes.py`, which this file does not import and does
    not name; `body/test_disengagement.py` walks this file's AST and fails the
    suite if it ever references that module, that path, or any outcome field.

    That is not caution, it is the only way the log means anything. A
    disengagement judged with the outcome in view is not a judgment, it is a
    scorecard — and it will quietly get better over time while teaching nothing.

WHAT IT SHIPS
    The tool. Not the judgments. Every row here is unjudged and stays that way
    until an operator writes one, because "what I would have done" is not a
    thing a model can supply on the operator's behalf.

§5 COLUMNS
    Date · Tier at time · What the system did/wanted   -> shown, from the log
    What I would have done · Delta · Root cause · ODD change? -> yours

USAGE
    adjudicate.py status              counts and the §5 health metric
    adjudicate.py next                show the next unjudged row
    adjudicate.py judge <row_id> --did "..." --root-cause "..." [--same] [--odd-change "..."]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from body.disengagement import (  # noqa: E402
    DECISIONS, JUDGMENTS, DisengagementError, Judgment, read_jsonl,
    record_judgment, judged_ids)

SAME_ROOT_CAUSE_DEFECT = 3      # §5: three with one root cause = an ODD defect


def _rows() -> list[dict]:
    rows = read_jsonl(DECISIONS)
    if not rows:
        raise DisengagementError(
            f"no rows at {DECISIONS} — run scripts/backfill_disengagement.py")
    return rows


def cmd_status(_a) -> int:
    rows, js = _rows(), read_jsonl(JUDGMENTS)
    done = {j["row_id"] for j in js}
    dis = [j for j in js if j.get("disengaged")]
    print(f"ODD §5 DISENGAGEMENT LOG\n")
    print(f"  rows            {len(rows)}")
    print(f"  judged          {len(js)}")
    print(f"  unjudged        {len(rows) - len(done)}")
    print(f"  disengagements  {len(dis)}")
    by_arm = Counter(r["arm"] for r in rows)
    by_tier = Counter(r["tier_at_time"] for r in rows)
    print(f"\n  by arm   {dict(by_arm)}")
    print(f"  by tier  {dict(by_tier)}   (§5: rows are logged below live, "
          "and with T0 sealed these are the only rows that exist)")
    agree = Counter(r["engine_agreement"] for r in rows)
    print(f"  engines  {dict(agree)}   (§5 addition 1 — a two-engine "
          "disagreement is a different defect class)")

    if js:
        rate = len(dis) / len(js) * 100
        print(f"\n  §5 HEALTH METRIC: {rate:.1f} disengagements per 100 "
              f"judged decisions   (target: trending down)")
        causes = Counter(j["root_cause"] for j in dis if j.get("root_cause"))
        for cause, n in causes.most_common():
            flag = "  <- ODD DEFECT (§5: three with one root cause)" \
                if n >= SAME_ROOT_CAUSE_DEFECT else ""
            print(f"    {cause:<40} {n}{flag}")
    else:
        print("\n  §5 HEALTH METRIC: undefined — nothing judged yet.")
        print("  A rate over zero judgments is not a low rate, it is no data.")
    return 0


def cmd_next(_a) -> int:
    rows = _rows()
    done = judged_ids()
    pending = [r for r in rows if r["row_id"] not in done]
    if not pending:
        print("nothing unjudged.")
        return 0
    r = pending[0]
    st = r.get("state", {})
    print(f"row            {r['row_id']}     ({len(pending)} unjudged)")
    print(f"date           {r['date']}")
    print(f"tier at time   {r['tier_at_time']}")
    print(f"engine         {r['engine']}  ({r['engine_agreement']})")
    print(f"\nwhat the system did / wanted:\n  {r['what_system_did_or_wanted']}")
    print(f"\nstate as of the decision:")
    print(f"  eagerness {st.get('eagerness')}   vetoes {st.get('vetoes')}   "
          f"margin {st.get('margin')}")
    print(f"  for      {st.get('for')}")
    print(f"  against  {st.get('against')}")
    print(f"\nYou cannot see how this turned out. That is deliberate.")
    print(f"\n  adjudicate.py judge {r['row_id']} --did \"...\" --root-cause \"...\"")
    return 0


def cmd_judge(a) -> int:
    rows = {r["row_id"]: r for r in _rows()}
    if a.row_id not in rows:
        print(f"no such row: {a.row_id}")
        return 1
    disengaged = not a.same
    if disengaged and not a.root_cause:
        print("a disengagement needs a --root-cause. §5 counts them by cause, "
              "and an uncaused disengagement cannot reach the three-strike rule.")
        return 1
    try:
        record_judgment(Judgment(
            row_id=a.row_id,
            what_i_would_have_done=a.did,
            delta="none — agreed with the system" if a.same else a.did,
            root_cause=a.root_cause or "",
            odd_change=a.odd_change or "",
            judged_at=datetime.now(timezone.utc).isoformat(),
            disengaged=disengaged))
    except DisengagementError as e:
        print(f"REFUSED: {e}")
        return 1
    print(f"recorded {'DISENGAGEMENT' if disengaged else 'agreement'} "
          f"on {a.row_id}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="adjudicate ODD §5 rows, blind")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("next").set_defaults(fn=cmd_next)
    j = sub.add_parser("judge")
    j.add_argument("row_id")
    j.add_argument("--did", required=True, help="what you would have done")
    j.add_argument("--root-cause", default="", help="required if you disengaged")
    j.add_argument("--odd-change", default="", help="§5 column 7")
    j.add_argument("--same", action="store_true",
                   help="you would have done what the system did")
    j.set_defaults(fn=cmd_judge)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except DisengagementError as e:
        print(f"REFUSED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
