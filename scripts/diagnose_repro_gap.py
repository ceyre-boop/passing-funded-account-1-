#!/usr/bin/env python3
"""Diagnose the sealed-vs-rig reproduction gap — spec 021 G2.

The offline rig (spot_cache parquets, monkeypatched yfinance) reproduces only
~300 of the sealed CSV's 411 in-sample trades. This script attributes that gap
mechanically and REFUSES to call it diagnosed while any residual is unattributed.

Facts established statically (verified against the code, not assumed):
- ^VIX3M / ^TNX feed _compute_size_multipliers only (clip [0.5,1.5]) — size, not
  entry. Their absence CANNOT remove trades. (signal_engine.py:189-304)
- COT: gate_signal returns a size multiplier and returns 1.0 when the cache is
  empty (cot_engine.py:57-65) — also cannot remove trades; the empty
  data/cache/cot/ changes SIZE fidelity only.
- cb_decisions.json is present, so the CB event trigger runs.

Remaining candidate causes this script measures:
  A. spot-cache coverage: sealed trade dates the offline cache simply lacks.
  B. macro-history truncation: data/cache/macro/{CC}_{rates,cpi}.parquet start
     2019/2020 for EU, UK, US, AU, JP(cpi) — the engine cannot form rate/CPI
     differential signals for entries earlier than (that pair's latest macro
     start + WARMUP). Evidence: the rig matches the sealed CSV PERFECTLY from
     2021 onward (18/18, 14/14, ...) and the entire residual sits 2016-2020,
     tapering exactly as macro coverage begins.
  C. everything else — data vintage and engine drift vs the ~/quant generator.
     Not separable from this checkout; stays UNATTRIBUTED and lands on the
     restore list. Definitive proof of B (restore full macro history, re-run)
     also lives on the restore list.

Writes data/agent/repro_gap_report.json with status:
  ATTRIBUTED  — every missing trade accounted for by a named cause
  PARTIAL     — residual remains; G2 stays RED (a waiver via decision_logger is
                the only other path to green, and this script never writes one)
"""
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from oos_campaign_test import get_trades, CACHE  # noqa: E402  (rig + monkeypatch)
import carry_buy_gate as cbg  # noqa: E402

REPORT = ROOT / "data" / "agent" / "repro_gap_report.json"
MATCH_TOL_DAYS = 3
MACRO_DIR = ROOT / "data" / "cache" / "macro"
MACRO_WARMUP_DAYS = 365  # trailing history the differential signals need
PAIR_COUNTRIES = {
    "EURUSD=X": ("EU", "US"), "GBPUSD=X": ("UK", "US"),
    "USDJPY=X": ("US", "JP"), "AUDUSD=X": ("AU", "US"),
}

RESTORE_LIST = [
    "data/research/spot_cache/VIX3M_ohlc.parquet (^VIX3M) — size fidelity",
    "data/research/spot_cache/TNX_ohlc.parquet (^TNX) — size fidelity",
    "data/cache/cot/{EUR,GBP,JPY,AUD,USD}_cot.parquet — size fidelity",
    "scripts/build_cb_decisions.py — cb_decisions.json regenerator",
    "writer script for data/oos_trades_2025_2026.json — OOS artifact provenance",
    "the sealed CSV's generator (v015 engine + data vintage) — the only way to "
    "attribute residual entry-count divergence",
]


def main():
    sealed = cbg.load_sealed()
    print("running offline rig 2015-2024 (this takes a minute)...")
    rig = get_trades("2015-01-01", "2024-12-31")
    rig_n, sealed_n = len(rig), len(sealed)
    print(f"sealed n={sealed_n}  rig n={rig_n}  raw gap={sealed_n - rig_n}")

    # --- per-pair / per-year table ------------------------------------------
    def key_counts(trades, date_key, pair_key):
        c = Counter()
        for t in trades:
            d = t[date_key] if isinstance(t[date_key], str) else t[date_key].strftime("%Y-%m-%d")
            c[(t[pair_key], d[:4])] += 1
        return c

    sc = key_counts(sealed, "entry", "pair")
    rc = key_counts(rig, "entry_date", "pair")
    years = sorted({y for _, y in set(sc) | set(rc)})
    pairs = sorted({p for p, _ in set(sc) | set(rc)})
    print("\nper-pair/per-year (sealed/rig):")
    print("        " + "  ".join(f"{y}" for y in years))
    for p in pairs:
        print(f"{p:10}" + "  ".join(f"{sc.get((p, y), 0):2}/{rc.get((p, y), 0):2}" for y in years))

    # --- cache coverage ------------------------------------------------------
    coverage = {}
    for f in sorted(CACHE.glob("*.parquet")):
        idx = pd.read_parquet(f).index
        coverage[f.name] = dict(start=str(idx.min())[:10], end=str(idx.max())[:10], rows=len(idx))
    print("\ncache coverage:")
    for name, c in coverage.items():
        print(f"  {name:24} {c['start']} -> {c['end']}  ({c['rows']} rows)")

    # --- match sealed trades to rig trades ----------------------------------
    rig_by_pair = {}
    for t in rig:
        d = t["entry_date"] if isinstance(t["entry_date"], str) else t["entry_date"].strftime("%Y-%m-%d")
        rig_by_pair.setdefault(t["pair"], []).append(pd.Timestamp(d))
    for v in rig_by_pair.values():
        v.sort()

    pair_cache = {}
    def cache_index(pair):
        if pair not in pair_cache:
            name = pair.replace("=X", "")
            f = CACHE / f"{name}_ohlc.parquet"
            pair_cache[pair] = pd.read_parquet(f).index if f.exists() else None
        return pair_cache[pair]

    matched = 0
    missing = []           # sealed trades with no rig counterpart
    used = {p: [False] * len(v) for p, v in rig_by_pair.items()}
    for t in sealed:
        ed = pd.Timestamp(t["entry"])
        cands = rig_by_pair.get(t["pair"], [])
        hit = None
        for i, rd in enumerate(cands):
            if not used[t["pair"]][i] and abs((rd - ed).days) <= MATCH_TOL_DAYS:
                hit = i
                break
        if hit is not None:
            used[t["pair"]][hit] = True
            matched += 1
        else:
            missing.append(t)
    extra = sum(1 for p in used for f in used[p] if not f)

    # Attribution B input: per-pair earliest date with full macro history.
    def macro_ready(pair):
        latest = None
        for cc in PAIR_COUNTRIES[pair]:
            for kind in ("rates", "cpi"):
                f = MACRO_DIR / f"{cc}_{kind}.parquet"
                if not f.exists():
                    continue
                start = pd.read_parquet(f).index.min()
                latest = start if latest is None else max(latest, start)
        return (latest + pd.Timedelta(days=MACRO_WARMUP_DAYS)) if latest is not None else None

    macro_ready_by_pair = {p: macro_ready(p) for p in PAIR_COUNTRIES}
    print("\nmacro-ready date per pair (latest macro-cache start + "
          f"{MACRO_WARMUP_DAYS}d warmup):")
    for p, d in macro_ready_by_pair.items():
        print(f"  {p}: {str(d)[:10]}")

    # Attribution A: cache gap. B: entry precedes macro-ready date. C: residual.
    cache_gap = []
    macro_trunc = []
    residual = []
    for t in missing:
        idx = cache_index(t["pair"])
        ed = pd.Timestamp(t["entry"])
        if idx is None or len(idx[(idx >= ed - pd.Timedelta(days=MATCH_TOL_DAYS))
                                  & (idx <= ed + pd.Timedelta(days=MATCH_TOL_DAYS))]) == 0:
            cache_gap.append(t)
        elif (mr := macro_ready_by_pair.get(t["pair"])) is not None and ed < mr:
            macro_trunc.append(t)
        else:
            residual.append(t)
    assert len(cache_gap) + len(macro_trunc) + len(residual) == len(missing), \
        "attribution must sum to the total gap"

    first_div = min(missing, key=lambda t: t["entry"]) if missing else None
    print(f"\nmatched {matched}/{sealed_n} (tol ±{MATCH_TOL_DAYS}d) | rig-only extras: {extra}")
    print(f"missing from rig: {len(missing)} = cache-gap {len(cache_gap)} "
          f"+ macro-history-truncation {len(macro_trunc)} "
          f"+ UNATTRIBUTED residual {len(residual)}")
    if first_div:
        print(f"first divergence: {first_div['pair']} entry {first_div['entry']}")
    by_year = Counter(t["entry"][:4] for t in residual)
    if by_year:
        print("residual by year: " + ", ".join(f"{y}:{n}" for y, n in sorted(by_year.items())))

    status = "ATTRIBUTED" if not residual else "PARTIAL"
    report = dict(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        status=status,
        sealed_n=sealed_n, rig_n=rig_n, matched=matched,
        missing=len(missing), rig_only_extra=extra,
        attribution=dict(
            cache_gap=len(cache_gap),
            macro_history_truncation=len(macro_trunc),
            macro_ready_by_pair={p: str(d)[:10] for p, d in macro_ready_by_pair.items()},
            macro_warmup_days=MACRO_WARMUP_DAYS,
            unattributed_residual=len(residual),
            residual_by_year=dict(sorted(by_year.items())),
            size_only_causes_ruled_out=["^VIX3M", "^TNX", "COT cache"],
        ),
        first_divergence=(dict(pair=first_div["pair"], entry=first_div["entry"])
                          if first_div else None),
        cache_coverage=coverage,
        restore_list=RESTORE_LIST,
        note=("PARTIAL: residual divergence needs the ~/quant generator/data "
              "vintage; G2 stays RED unless a waiver is logged via decision_logger."
              if residual else "all missing trades attributed"),
    )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=1))
    print(f"\nstatus: {status}  -> {REPORT.relative_to(ROOT)}")
    print("restore list (re-export from ~/quant):")
    for r in RESTORE_LIST:
        print(f"  - {r}")


if __name__ == "__main__":
    main()
