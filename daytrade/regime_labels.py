#!/usr/bin/env python3
"""REGIME LABELS — spec 001 definition-of-done step 1, and spec 026 gate G-S6.

Runs `regime.classify` over EVERY 5-minute block of a set of sessions, grades
each call with `scorecard.grade` against the NEXT K blocks, appends the rows to
`data/regime_scorecard.csv` and prints `scorecard.report`.

WHY THIS FILE IS THE POINT
--------------------------
`alpha_operator.py grade` has answered "nothing resolved to grade" for its whole
life, because the reward pathway did not exist: `regime.py` and `scorecard.py`
were both `NotImplementedError`. This is the loop closing. Spec 004 is blunt
about why it matters more than P&L: P&L gives ~1 noisy sample per day and
conflates the entry read, the exit policy and luck, whereas a regime call is
scorable ~78 times per session against what the tape actually did next.

Spec 026 G-S6 requires >= 300 scored calls on TRAIN with a Brier skill score > 0
against a base-rate baseline. `scorecard.report` computes exactly that, and
reports the dumb baselines beside every accuracy because a rate without its
baseline is not evidence in this repo (specs/README.md rule 3).

SPLIT DISCIPLINE
----------------
TRAIN only, enforced by `splits.tune_sessions`. There is no flag here that reads
SEALED-002; that is a separate, deliberate script.

NO LOOKAHEAD
------------
Two independent guards, neither of which trusts this file:
  - `classify` is handed bars ENDING at the block being read and raises if the
    frame reaches past `ctx.now_et`.
  - `grade` re-asserts that its forward window starts strictly AFTER the read's
    timestamp.
The forward window is the next FORWARD_BLOCKS bars and is required to be
complete — a call near the close whose 15 minutes run past 16:00 is dropped as
ungradeable rather than graded on a short window.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bars                                                   # noqa: E402
import regime as regime_mod                                   # noqa: E402
import scorecard                                              # noqa: E402
import splits                                                 # noqa: E402
from bars import load_sessions                                 # noqa: E402

ROOT = HERE.parent
OUT = ROOT / "data" / "daytrade" / "regime_labels_report.json"
LOOKBACK_SESSIONS = 60


def _continuous(sessions):
    import pandas as pd
    return pd.concat([s.df for s in sessions]).sort_index()


def _lookback_start(cont, day):
    prior = sorted({d for d in cont.index.date if d < day})
    if len(prior) <= LOOKBACK_SESSIONS:
        return cont.index[0]
    first = prior[-LOOKBACK_SESSIONS]
    return cont.index[cont.index.date >= first][0]


def _session_job(args):
    """One session -> graded rows. Runs in a worker process."""
    sym, day_iso = args
    import pandas as pd
    sessions = load_sessions(sym, "5m", allow_fetch=False, on_gap="exclude")
    byday = {str(s.day): s for s in sessions}
    sess = byday.get(day_iso)
    if sess is None:
        return sym, day_iso, [], "session not present"
    cont = _continuous(sessions)
    cut = _lookback_start(cont, sess.day)

    prior = sorted({d for d in cont.index.date if d < sess.day})
    pdh = pdl = None
    if prior:
        p = cont[cont.index.date == prior[-1]]
        pdh, pdl = float(p["High"].max()), float(p["Low"].min())

    day_idx = list(sess.df.index)
    rows = []
    for i, ts in enumerate(day_idx):
        fwd = sess.df.iloc[i + 1: i + 1 + scorecard.FORWARD_BLOCKS]
        if len(fwd) < scorecard.FORWARD_BLOCKS:
            continue                      # incomplete forward window — not graded
        up = cont[(cont.index >= cut) & (cont.index <= ts)]
        ctx = regime_mod.Context(
            symbol=sym, now_et=ts.strftime("%H:%M"), session_day=sess.day,
            pdh=pdh, pdl=pdl, onh=None, onl=None,
            unavailable=("onh", "onl"),
            unavailable_reason="overnight pools need extended-hours bars; the "
                               "Alpaca source is clipped to regular hours")
        try:
            read = regime_mod.classify(up, None, ctx)
        except regime_mod.RegimeError:
            continue                      # cannot classify -> cannot grade
        try:
            rows.append(scorecard.grade(read, fwd))
        except scorecard.ScorecardError:
            continue
    return sym, day_iso, rows, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 001 DoD step 1 / spec 026 G-S6")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--sessions", type=int, default=60,
                    help="most recent N TRAIN sessions (spec 001 says 60)")
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--no-append", action="store_true",
                    help="compute and report without writing the CSV")
    a = ap.parse_args(argv)

    allsess = load_sessions(a.symbol, "5m", allow_fetch=False, on_gap="exclude")
    train = splits.tune_sessions(allsess)
    if not train:
        print("  no TRAIN sessions — nothing to label")
        return 1
    pick = train[-a.sessions:]
    print(f"  {a.symbol}: {len(allsess)} cached, {len(train)} TRAIN, "
          f"labelling {len(pick)} ({pick[0].day} .. {pick[-1].day})")

    rows, problems = [], []
    jobs = [(a.symbol, str(s.day)) for s in pick]
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for i, (sym, day, got, err) in enumerate(ex.map(_session_job, jobs), 1):
            if err:
                problems.append(f"{sym} {day}: {err}")
            rows.extend(got)
            if i % 10 == 0 or i == len(jobs):
                print(f"    {i}/{len(jobs)} sessions · {len(rows)} graded rows",
                      flush=True)

    if not rows:
        print("  no graded rows")
        return 1

    rep = scorecard.report(rows)
    gradeable = [r for r in rows if r.get("gradeable")]
    print(f"\n  rows {len(rows)}  gradeable {len(gradeable)}")
    print(f"  G-S6 needs >= 300 scored calls: "
          f"{'MET' if len(gradeable) >= 300 else 'NOT MET'} ({len(gradeable)})")
    acc = rep.get("overall_accuracy")
    print(f"  overall_accuracy {acc}  (decoration — per-regime lift is the headline)")
    print(f"  baseline_always_consolidation {rep.get('baseline_always_consolidation')}")
    print(f"  baseline_time_prior_only      {rep.get('baseline_time_prior_only')}")
    print(f"  lift_over_baseline            {rep.get('lift_over_baseline')}")
    print(f"  brier {rep.get('brier')}  baseline {rep.get('brier_baseline')}  "
          f"SKILL {rep.get('brier_skill_score')}")
    bss = rep.get("brier_skill_score")
    print(f"  G-S6 needs Brier skill > 0: "
          f"{'MET' if (bss is not None and bss > 0) else 'NOT MET'} ({bss})")
    for rg, d in (rep.get("by_regime") or {}).items():
        print(f"    {rg:14} {d}")

    if not a.no_append:
        n = scorecard.append_rows(rows)
        print(f"  appended {n} row(s) -> {scorecard.SCORECARD_CSV.relative_to(ROOT)}")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": a.symbol, "sessions_labelled": len(pick),
        "span": [str(pick[0].day), str(pick[-1].day)],
        "rule_version": regime_mod.RULE_VERSION,
        "grade_version": scorecard.GRADE_VERSION,
        "n_rows": len(rows), "n_gradeable": len(gradeable),
        "gate_G_S6_n_met": len(gradeable) >= 300,
        "gate_G_S6_brier_skill_met": bool(bss is not None and bss > 0),
        "report": rep, "problems": problems,
    }, indent=1, default=str))
    print(f"  report: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
