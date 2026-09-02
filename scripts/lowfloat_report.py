#!/usr/bin/env python3
"""Report the screen's base rate per the prereg's standing format:
every rate with its n, absolute values beside ratios, the median non-runner
beside the hit rate, per-year cut always shown."""
import json, sys, statistics as st
from collections import defaultdict

rows = json.load(open(sys.argv[1] if len(sys.argv) > 1
                      else "data/nasdaq/screen_dev.json"))
n = len(rows)
if not n:
    sys.exit("no candidate-days")

def pct(x): return f"{100*x:6.2f}%"
def q(v, p): return st.quantiles(sorted(v), n=100)[p-1] if len(v) > 2 else float('nan')

ran   = [r for r in rows if r["ran"]]
miss  = [r for r in rows if not r["ran"]]

print(f"CANDIDATE-DAYS  n = {n:,}   unique symbols = {len({r['sym'] for r in rows}):,}")
print(f"HIT RATE ('ran' = intraday high >= +20% over prior close)")
print(f"  ran      {len(ran):>7,}   {pct(len(ran)/n)}")
print(f"  did not  {len(miss):>7,}   {pct(len(miss)/n)}")

print(f"\nWHAT THE MEDIAN CANDIDATE ACTUALLY DID  (n = {n:,})")
for f, lab in (("gap","overnight gap"),("high_r","best (high vs prior close)"),
               ("close_r","close vs prior close"),("low_r","worst (low)"),
               ("mae_open","MAE from the open")):
    v = [r[f] for r in rows]
    print(f"  {lab:<28} median {pct(st.median(v))}   mean {pct(st.fmean(v))}")

print(f"\nTHE MISSES — what the {len(miss):,} non-runners cost")
for f, lab in (("close_r","close vs prior close"),("mae_open","MAE from the open")):
    v = [r[f] for r in miss]
    print(f"  {lab:<28} median {pct(st.median(v))}   p10 {pct(q(v,10))}   p05 {pct(q(v,5))}")

print(f"\nLEFT TAIL (all candidates, close vs prior close)")
v = sorted(r["close_r"] for r in rows)
for p in (1, 5, 10, 25, 50, 75, 90, 99):
    print(f"  p{p:<3} {pct(q(v,p) if p not in (50,) else st.median(v))}")

print(f"\nBY YEAR  (pooled numbers hide regime; this is the check)")
by = defaultdict(list)
for r in rows:
    by[r["date"][:4]].append(r)
print(f"  {'year':<6}{'n':>8}{'hit rate':>11}{'med close':>12}{'med MAE':>10}{'wide tape':>11}")
for y in sorted(by):
    g = by[y]
    print(f"  {y:<6}{len(g):>8,}{pct(sum(x['ran'] for x in g)/len(g)):>11}"
          f"{pct(st.median([x['close_r'] for x in g])):>12}"
          f"{pct(st.median([x['mae_open'] for x in g])):>10}"
          f"{pct(sum(x['wide_tape'] for x in g)/len(g)):>11}")

wt = sum(r["wide_tape"] for r in rows)
print(f"\nWIDE-TAPE PROXY (range > 40% of prior close — NOT a halt record)")
print(f"  flagged {wt:,} of {n:,} = {pct(wt/n)}   these are the least fillable days")
