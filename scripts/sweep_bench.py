#!/usr/bin/env python3
"""scripts/sweep_bench.py — how long does ONE full sweep cost?

Fishtest works because a patch costs minutes. Every mechanism testable per day is
bounded by this number and nothing else, so it gets measured rather than guessed.

Sessions are independent, so the sweep parallelises across them. The per-bar
decision loop is untouched: workers call `ceiling.simulate`, the one canonical
harness — this is a scheduling change, not a second implementation.
"""
from __future__ import annotations

import sys, time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "daytrade"))

from bars import ET, MIN_SESSION_BARS_5M, RTH_CLOSE, RTH_OPEN, Session, _internal_gaps  # noqa: E402
from ceiling import find_entry, simulate, wide_space                                     # noqa: E402
from splits import TUNE_END                                                              # noqa: E402


def load_spy():
    df = pd.read_parquet(ROOT / "data/daytrade/bars_premarket/SPY_5m.parquet")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    t = df.index.strftime("%H:%M")
    rth = df[(t >= RTH_OPEN) & (t <= RTH_CLOSE)]
    out = [Session("SPY", d, c) for d, c in rth.groupby(rth.index.date)
           if len(c) >= MIN_SESSION_BARS_5M and not _internal_gaps(c, "5m")]
    return [s for s in out if s.day <= TUNE_END]


def _one(args):
    sess, cfgs = args
    e = find_entry(sess)
    if e is None:
        return None
    return [simulate(sess, e, dict(c)) for c in cfgs]


def sweep(sessions, cfgs, workers: int | None):
    if not workers or workers == 1:
        return [r for r in (_one((s, cfgs)) for s in sessions) if r is not None]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return [r for r in ex.map(_one, ((s, cfgs) for s in sessions), chunksize=32) if r is not None]


def main() -> int:
    import os
    cfgs = list(wide_space().values())
    t0 = time.perf_counter(); sessions = load_spy(); t_load = time.perf_counter() - t0
    print(f"sessions {len(sessions):,}   configs {len(cfgs)}   load {t_load:.2f}s")
    for w in (1, os.cpu_count() or 4):
        t0 = time.perf_counter(); res = sweep(sessions, cfgs, w); dt = time.perf_counter() - t0
        n = len(res); vals = sum(len(r) for r in res)
        chk = round(sum(sum(r) for r in res), 6)
        print(f"  workers {w:>2}   {dt:7.2f}s   entries {n:,}   R values {vals:,}   checksum {chk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
