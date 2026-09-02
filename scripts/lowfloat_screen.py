#!/usr/bin/env python3
"""Frozen screen from artifacts/LOWFLOAT_SCREEN_PREREG.md (commit abd7f34).

No threshold in here may be changed after a result is seen. Defaults to the
DEVELOPMENT half only; the sealed half requires --sealed and is a one-shot.
"""
import argparse, glob, gzip, json, os, statistics as st
from collections import defaultdict
from datetime import date, timedelta

S1_LO, S1_HI = 1.00, 10.00       # prior close band
S2_DOLLARVOL = 1_000_000         # prior dollar volume floor
S3_RVOL      = 3.0               # prior volume / 20d median
RAN          = 1.20              # high(D) / close(D-1)
WIDE_TAPE    = 0.40              # range/close(D-1) -> halt/spread PROXY

DEV    = ("2016-01-01", "2021-12-31")
SEALED = ("2022-01-01", "2026-08-31")

def excluded(sym: str) -> bool:
    """Warrants / units / rights. Belt-and-braces on top of Polygon type=CS."""
    return len(sym) > 4 and sym[-1] in ("W", "U", "R")

def split_days():
    """(ticker, day) pairs to drop — a split on D or D-1 fakes the one-day move.
    Prices are RAW per Amendment 1, so splits are not silently absorbed."""
    bad = set()
    if not os.path.exists("data/nasdaq/splits.json"):
        return bad
    for sp in json.load(open("data/nasdaq/splits.json")):
        t, ed = sp.get("ticker"), sp.get("execution_date")
        if not (t and ed):
            continue
        d0 = date.fromisoformat(ed)
        for off in (0, 1, 2):          # D, and D-1 spilling into the next session
            bad.add((t, str(d0 + timedelta(days=off))))
    return bad

def run(lo, hi, label):
    bad, dropped = split_days(), 0
    rows = []
    for path in sorted(glob.glob("data/nasdaq/daily/batch_*.jsonl.gz")):
        with gzip.open(path, "rt") as f:
            for line in f:
                rec = json.loads(line)
                sym = rec["symbol"]
                if excluded(sym):
                    continue
                bars = [b for b in rec["bars"] if b["t"][:10] <= hi]
                for i in range(21, len(bars)):
                    p, d = bars[i - 1], bars[i]          # D-1 and D
                    day = d["t"][:10]
                    if not (lo <= day <= hi):
                        continue
                    pc, pv = p["c"], p["v"]
                    if not (S1_LO <= pc <= S1_HI):       # S1
                        continue
                    if pc * pv < S2_DOLLARVOL:           # S2
                        continue
                    med = st.median([b["v"] for b in bars[i - 21:i - 1]])
                    if med <= 0 or pv < S3_RVOL * med:   # S3
                        continue
                    if (sym, day) in bad:                # split-corrupted
                        dropped += 1
                        continue
                    o, h, l, c = d["o"], d["h"], d["l"], d["c"]
                    if o <= 0 or pc <= 0:
                        continue
                    rows.append({
                        "date": day, "sym": sym, "prior_close": pc,
                        "gap":      o / pc - 1,
                        "high_r":   h / pc - 1,
                        "close_r":  c / pc - 1,
                        "low_r":    l / pc - 1,
                        "mae_open": l / o - 1,
                        "range_pct": (h - l) / pc,
                        "ran": h >= pc * RAN,
                        "wide_tape": (h - l) / pc > WIDE_TAPE,
                    })
    print(f"  dropped {dropped:,} split-corrupted candidate-days")
    return rows

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sealed", action="store_true",
                    help="ONE-SHOT verdict run on 2022-2026. Requires the rule frozen.")
    a = ap.parse_args()
    lo, hi = SEALED if a.sealed else DEV
    label = "SEALED 2022-2026" if a.sealed else "DEVELOPMENT 2016-2021"
    rows = run(lo, hi, label)
    out = f"data/nasdaq/screen_{'sealed' if a.sealed else 'dev'}.json"
    json.dump(rows, open(out, "w"))
    print(f"{label}: {len(rows):,} candidate-days -> {out}")
