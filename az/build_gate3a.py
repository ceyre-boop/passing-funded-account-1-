#!/usr/bin/env python3
"""az/build_gate3a.py — grade every legal candidate. Emits the table the STOP reads."""
from __future__ import annotations
import hashlib, sys, time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))
from az.build_gate1 import sessions_for            # noqa: E402
from az.candidates import enumerate_day            # noqa: E402
from az.grade import grade_all                     # noqa: E402
from az.report import Tally                        # noqa: E402
from az.state import GRANULARITIES, discretize     # noqa: E402
from bars import Session                           # noqa: E402

K_STOP = 1.0
OUT = ROOT / "artifacts" / "az_gate3a_table.parquet"


def _one(args):
    sym, day, chunk, prev = args
    s = Session(sym, day, chunk)
    rows = []
    for c in enumerate_day(sym, day, chunk, prev, k_stop=K_STOP):
        if not c.legal:
            continue                       # masked candidates are ABSENT, never R=0
        cell = discretize(c.raw, c.hhmm, GRANULARITIES["fine"]).key()
        g = grade_all(s, c)
        for (grader, fill), r in g.items():
            rows.append({"symbol": sym, "day": str(day), "hhmm": c.hhmm,
                         "direction": c.direction, "grader": grader, "fill": fill,
                         "R": float(r), "cell": str(cell),
                         "cell_dir": f"{cell}|{c.direction:+d}", "risk": c.entry.risk})
    return rows


def main() -> int:
    import os
    jobs = []
    for sym in ("SPY", "QQQ"):
        sess = sessions_for(ROOT / f"data/daytrade/bars_premarket/{sym}_5m.parquet")
        prev = None
        for day, chunk in sess:
            jobs.append((sym, day, chunk, prev))
            prev = float(chunk["Close"].iloc[-1])
    print(f"grading {len(jobs):,} sessions x ~20 legal candidates x 3 graders x 2 fills")
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as ex:
        out = [r for batch in ex.map(_one, jobs, chunksize=32) for r in batch]
    df = pd.DataFrame(out).sort_values(["symbol", "day", "hhmm", "direction", "grader", "fill"]).reset_index(drop=True)
    OUT.parent.mkdir(exist_ok=True)
    df.to_parquet(OUT, index=False)
    h = hashlib.sha256(OUT.read_bytes()).hexdigest()
    n_days = df.groupby("symbol")["day"].nunique().sum()
    t = Tally(n_days=int(n_days), n_candidates=int(len(df) // 6),
              extra={"graded rows": f"{len(df):,}", "cells": f"{df.cell.nunique():,}",
                     "cell x dir": f"{df.cell_dir.nunique():,}"})
    print(f"  {t.header()}")
    print(f"  {time.perf_counter()-t0:.1f}s   -> {OUT.relative_to(ROOT)}   sha256 {h[:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
