#!/usr/bin/env python3
"""Data health — is every input the strategy depends on actually sound today?

WHY THIS EXISTS
---------------
The signal path degrades quietly in several independent ways, and every one of
them produces the same visible symptom: a scan that says "no setup today".

  - a yfinance failure is swallowed by a bare `except: pass`, which disables
    the SPY/VIX regime gate (signal_engine.py)
  - a missing FRED_API_KEY turns rate/CPI into flat constants (data_fetcher.py
    FALLBACK_RATES / FALLBACK_CPI)
  - a cached parquet is returned whole regardless of the requested start
  - and the one that is easiest to miss: the upstream FRED SERIES ITSELF can be
    discontinued. Then the cache is "fresh" (it matches FRED), FRED is
    reachable, no error is raised anywhere — and `.asof(date)` silently
    forward-fills an inflation reading from years ago, forever.

That last case is why this tool separates three states that all look alike:

  CACHE_STALE  — FRED has newer data than the local parquet. Refreshable.
  SOURCE_DEAD  — FRED itself stopped publishing. A refresh CANNOT fix it;
                 the series must be replaced.
  NO_SERIES    — the repo maps this input to '' and uses a hardcoded constant.

Read-only. Touches no cache, writes no file. Exit 1 if any input feeding a
traded pair is unsound.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MACRO_CACHE = ROOT / "data" / "cache" / "macro"

# Legs of the four sealed pairs. base/quote as the signal engine sees them.
PAIR_LEGS = {
    "EURUSD=X": ("EU", "US"),
    "GBPUSD=X": ("UK", "US"),
    "USDJPY=X": ("US", "JP"),
    "AUDUSD=X": ("AU", "US"),
}

YF_SYMBOLS = {
    "SPY":     "SPY 200-SMA leg of the bull/VIX regime gate",
    "^VIX":    "VIX leg of the regime gate (per-pair thresholds)",
    "^VIX3M":  "VIX term-structure size multiplier",
    "^TNX":    "EURUSD rate-divergence size boost (HYP-028)",
}

ARTIFACTS = {
    "data/cache/cb_decisions.json":        ("CB-surprise entry layer", True),
    "data/proof/backtest_trades_v015_2015_2024.csv": ("sealed record / golden R", True),
    "data/oos_trades_2025_2026.json":      ("OOS gate G3", True),
    "data/execution/calibrated_costs.json": ("per-pair spread/slippage; absent -> static table", False),
    "data/research/swap_calibration.json": ("swap financing; absent -> static table that its own "
                                            "comment says understates OANDA ~9x", False),
}


def _load_env_key() -> str | None:
    if os.environ.get("FRED_API_KEY"):
        return os.environ["FRED_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("FRED_API_KEY=") and len(line.strip()) > 13:
                return line.split("=", 1)[1].strip()
    return None


def check_macro(as_of: date, offline: bool):
    """Per-country rate and CPI: cache vs FRED (or, for NON_FRED_CPI_SOURCES,
    the live replacement source) vs today."""
    from sovereign.forex.data_fetcher import (
        FRED_RATES, FRED_CPI, NON_FRED_CPI_SOURCES, ForexDataFetcher,
    )
    import pandas as pd

    key = _load_env_key()
    fred = None
    if key and not offline:
        try:
            from fredapi import Fred
            fred = Fred(api_key=key)
        except Exception:
            fred = None

    rows = []
    countries = sorted({c for legs in PAIR_LEGS.values() for c in legs})
    for c in countries:
        for kind, mapping in (("rates", FRED_RATES), ("cpi", FRED_CPI)):
            sid = mapping.get(c, "")
            f = MACRO_CACHE / f"{c}_{kind}.parquet"
            cache_end, constant = None, False
            if f.exists():
                try:
                    d = pd.read_parquet(f)
                    cache_end = d.index.max().date()
                    # A fallback constant persisted to disk is indistinguishable
                    # from a healthy cache by date alone — JP_cpi.parquet is 1978
                    # rows of the literal 3.2. Only the value spread reveals it.
                    constant = len(d) > 30 and d.iloc[:, 0].nunique() == 1
                except Exception:
                    pass

            if constant:
                rows.append((f"{c}_{kind}", sid or "(none)", cache_end, None,
                             "SYNTHETIC",
                             f"cache is a FLAT LINE ({d.iloc[0, 0]}) across {len(d)} rows — "
                             f"a hardcoded fallback written to disk, not observed data"))
                continue

            # CPI for countries whose FRED mirror is SOURCE_DEAD, or which
            # FRED never had (JP), is now fetched live from ONS/ABS/e-Stat
            # instead of FRED — compare against that source, not the dead/
            # nonexistent FRED series.
            if kind == "cpi" and c in NON_FRED_CPI_SOURCES and not offline:
                src_label = NON_FRED_CPI_SOURCES[c]
                src_end = None
                try:
                    fetcher = ForexDataFetcher.__new__(ForexDataFetcher)
                    if c == "UK":
                        src_end = fetcher._fetch_ons_uk_cpi_yoy(
                            "2015-01-01").index.max().date()
                    elif c == "JP":
                        src_end = fetcher._fetch_estat_jp_cpi_yoy(
                            "2015-01-01").index.max().date()
                    elif c == "AU":
                        src_end = fetcher._fetch_abs_au_cpi_index(
                            "2015-01-01").index.max().date()
                except Exception as e:
                    rows.append((f"{c}_{kind}", src_label, cache_end, None,
                                 "SOURCE_UNREACHABLE",
                                 f"live {src_label} fetch failed: {type(e).__name__}: {e}"))
                    continue
                if src_end is None:
                    state, note = "UNKNOWN", f"{src_label} not queried"
                elif cache_end is None:
                    state, note = "MISSING", "no local cache"
                elif cache_end < src_end:
                    state = "CACHE_STALE"
                    note = f"{src_label} has data to {src_end}; refreshable"
                else:
                    state, note = "OK", ""
                rows.append((f"{c}_{kind}", src_label, cache_end, src_end, state, note))
                continue

            if not sid:
                rows.append((f"{c}_{kind}", sid or "(none)", cache_end, None,
                             "NO_SERIES",
                             "repo maps this to '' and substitutes a hardcoded constant"))
                continue

            src_end = None
            if fred:
                try:
                    src_end = fred.get_series(sid).dropna().index.max().date()
                except Exception:
                    src_end = None

            if src_end is None:
                state, note = ("UNKNOWN", "FRED not queried (offline or unreachable)")
            elif (as_of - src_end).days > 200:
                state = "SOURCE_DEAD"
                note = (f"FRED stopped publishing {(as_of - src_end).days}d ago — "
                        f"a refresh cannot fix this; the series needs replacing")
            elif cache_end is None:
                state, note = "MISSING", "no local cache"
            elif cache_end < src_end:
                state = "CACHE_STALE"
                note = f"FRED has data to {src_end}; refreshable"
            else:
                state, note = "OK", ""
            rows.append((f"{c}_{kind}", sid, cache_end, src_end, state, note))
    return rows


def check_network(offline: bool):
    rows = []
    if offline:
        return [(s, "UNKNOWN", "skipped (--offline)") for s in YF_SYMBOLS]
    import yfinance as yf
    for sym, why in YF_SYMBOLS.items():
        try:
            d = yf.download(sym, period="10d", progress=False, auto_adjust=True)
            if d is None or len(d) == 0:
                rows.append((sym, "DEAD", f"no data returned — {why}"))
            else:
                rows.append((sym, "OK", str(d.index[-1].date())))
        except Exception as e:
            rows.append((sym, "DEAD", f"{type(e).__name__} — {why}"))
    return rows


def check_artifacts(as_of: date):
    rows = []
    for rel, (why, required) in ARTIFACTS.items():
        p = ROOT / rel
        if not p.exists():
            rows.append((rel, "MISSING" if required else "ABSENT_FALLBACK", why))
            continue
        note = why
        if p.suffix == ".json" and "cb_decisions" in rel:
            try:
                d = json.loads(p.read_text())
                recs = d if isinstance(d, list) else d.get("decisions", [])
                ds = [r.get("date", "") for r in recs if r.get("date")]
                if ds:
                    note = f"ends {max(ds)[:10]} ({(as_of - date.fromisoformat(max(ds)[:10])).days}d ago)"
            except Exception:
                note = "unparseable"
        rows.append((rel, "OK", note))
    return rows


# Artifacts that gate something real and have NO builder in this repo. If any is
# deleted or corrupted it cannot be regenerated — the gate it feeds dies with it.
# Each entry: path -> the reference that names a builder which does not exist.
UNREPRODUCIBLE = {
    "data/cache/cb_decisions.json":
        "entry_engine.py:23/:99 name scripts/build_cb_decisions.py — that file does not exist",
    "data/oos_trades_2025_2026.json":
        "gates G3; no writer found anywhere (searched ~/quant exhaustively)",
    "data/proof/backtest_trades_v015_2015_2024.csv":
        "the sealed record; diagnose_repro_gap.py:65 notes its generator is absent",
}

# Directories whose absence silently switches a subsystem to hardcoded defaults.
REQUIRED_DIRS = {
    "config": "rr_engine R-targets (1.5/3.0/5.0), autonomous is_live(), CAPE params "
              "— all silently defaulted while this is missing",
    "data/execution": "calibrated_costs.json lives here; absent -> modelled slippage "
                      "and the static swap table",
}


def check_structure():
    rows = []
    for d, why in REQUIRED_DIRS.items():
        rows.append((d, "OK" if (ROOT / d).is_dir() else "MISSING_DIR", why))
    for f, why in UNREPRODUCIBLE.items():
        present = (ROOT / f).exists()
        rows.append((f, "PRESENT_UNREPRODUCIBLE" if present else "LOST", why))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--offline", action="store_true",
                    help="skip all network checks (cache-only inspection)")
    a = ap.parse_args()
    as_of = date.fromisoformat(a.as_of)

    print(f"\nDATA HEALTH — {as_of}")
    print("=" * 78)

    print("\nMACRO (FRED)   cache_end / source_end")
    macro = check_macro(as_of, a.offline)
    for name, sid, cend, send, state, note in macro:
        flag = " " if state == "OK" else "!"
        print(f" {flag} {name:10s} {sid:22s} {str(cend or '-'):11s} {str(send or '-'):11s} "
              f"{state}")
        if note:
            print(f"     {note}")

    print("\nNETWORK (yfinance)")
    for sym, state, note in check_network(a.offline):
        print(f" {' ' if state=='OK' else '!'} {sym:8s} {state:8s} {note}")

    print("\nARTIFACTS")
    for rel, state, note in check_artifacts(as_of):
        print(f" {' ' if state=='OK' else '!'} {rel:48s} {state:16s} {note}")

    print("\nSTRUCTURE  (missing dirs silently switch a subsystem to defaults;"
          "\n            unreproducible artifacts cannot be rebuilt if lost)")
    lost = 0
    for path, state, why in check_structure():
        if state == "LOST":
            lost += 1
        print(f" {' ' if state == 'OK' else '!'} {path:48s} {state}")
        if state != "OK":
            print(f"     {why}")

    # Per-pair verdict: can this pair's carry leg be priced today?
    # UNKNOWN counts as unsound. Offline we cannot see SOURCE_DEAD, and a
    # verdict of "sound" that only means "not checked" is the exact false green
    # this tool exists to stop.
    bad = {r[0]: r[4] for r in macro
           if r[4] in ("SOURCE_DEAD", "NO_SERIES", "MISSING", "SYNTHETIC",
                       "UNKNOWN", "SOURCE_UNREACHABLE")}
    print("\nPER-PAIR SIGNAL INTEGRITY  (real_rate_diff needs BOTH legs' rate AND cpi)")
    unsound = 0
    for pair, (b, q) in sorted(PAIR_LEGS.items()):
        broken = [f"{c}_{k}={bad[f'{c}_{k}']}" for c in (b, q)
                  for k in ("rates", "cpi") if f"{c}_{k}" in bad]
        if broken:
            unsound += 1
            print(f" ! {pair:10s} DEGRADED — {', '.join(broken)}")
        else:
            print(f"   {pair:10s} sound")

    print()
    if unsound:
        print(f"{unsound} of {len(PAIR_LEGS)} traded pairs have an unsound carry input.")
        print("A scan on these produces a plausible-looking signal from stale or "
              "constant inflation. Fix the inputs before sourcing paper trades.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
