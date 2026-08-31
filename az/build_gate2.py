#!/usr/bin/env python3
"""az/build_gate2.py — Gate 2: enumerate, mask, report illegal fraction per day."""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))
from az.build_gate1 import sessions_for          # noqa: E402
from az.candidates import enumerate_day          # noqa: E402
from az.report import Tally                      # noqa: E402

K_STOP = 1.0     # DECLARED. A different k_stop is a different study.


def main() -> int:
    print(f"Gate 2 — enumeration + legality mask   k_stop = {K_STOP} (declared)\n")
    grand = []
    for sym in ("SPY", "QQQ"):
        sess = sessions_for(ROOT / f"data/daytrade/bars_premarket/{sym}_5m.parquet")
        cands, per_day_illegal, prev_close = [], [], None
        for day, chunk in sess:
            c = enumerate_day(sym, day, chunk, prev_close, k_stop=K_STOP)
            cands.extend(c)
            per_day_illegal.append(sum(1 for x in c if not x.legal) / len(c))
            prev_close = float(chunk["Close"].iloc[-1])
        legal = sum(1 for c in cands if c.legal)
        t = Tally(n_days=len(sess), n_candidates=len(cands),
                  n_legal=legal, n_illegal=len(cands) - legal,
                  extra={"slots/day": len(cands) // max(len(sess), 1)})
        print(f"  {sym}:  {t.header()}")
        pdi = np.array(per_day_illegal)
        print(f"        illegal fraction per day — mean {pdi.mean():.3f}  "
              f"min {pdi.min():.3f}  max {pdi.max():.3f}  days at 0% {int((pdi==0).sum()):,}")
        reasons = Counter(c.reason for c in cands if not c.legal)
        for r, n in reasons.most_common():
            print(f"        {n:>7,}  {r[:78]}")
        grand.append(t)
    tot = Tally(n_days=sum(t.n_days for t in grand), n_candidates=sum(t.n_candidates for t in grand),
                n_legal=sum(t.n_legal for t in grand), n_illegal=sum(t.n_illegal for t in grand))
    print(f"\n  LANE TOTAL:  {tot.header()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
