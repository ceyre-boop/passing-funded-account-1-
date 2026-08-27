#!/usr/bin/env python3
"""Carry scan — the signal source for the spec 021 G5 paper sprint.

WHY THIS REUSES THE BACKTESTER INSTEAD OF REIMPLEMENTING THE RULES
------------------------------------------------------------------
G5 asks whether forward paper trades land inside the golden R band of the
sealed 411-trade record. That comparison only means something if the paper
trades come from THE SAME decision logic that produced the sealed record. A
hand-written copy of the entry conditions would drift silently, and the sprint
would then measure a different strategy while reporting a verdict about v015.

So this script decides nothing. It calls `ForexBacktester._get_pair_signals` —
the same call `backtest_pair()` makes — and reads the signal on the most recent
bar. Direction and stop distance come out of that engine, not out of this file.

HOW THE SIGNAL ACTUALLY FIRES (verified against the sealed record)
------------------------------------------------------------------
- Macro entries are evaluated ONLY on the first business day of each month
  (`close.resample('BMS')` in signal_engine). Overlay layers (quarter-end,
  March JPY repat, CPI-surprise fade, CB-surprise) can fire on other days.
  Most days produce nothing, and that is the design, not a failure.
- A signal on bar i is FILLED AT THE OPEN OF BAR i+1 (fast_backtester:139).
  So a signal on the last closed bar is an order for the next session's open;
  the fill price does not exist yet.
- The initial stop uses ATR% from the SIGNAL bar, not the fill bar:
  stop_dist = fill * STOP_ATR_MULT * atr_pct[signal_bar].

WHY THE PREFLIGHT IS FAIL-LOUD
------------------------------
The upstream engine degrades silently in two ways that both look exactly like
"no setup today": a yfinance failure is swallowed by a bare `except: pass`,
which disables the SPY/VIX regime gate, and a missing FRED key turns the rate
and CPI series into flat constants, which collapses the carry leg. A stale
macro cache does the same thing more quietly. An empty scan is only
information if the inputs behind it were sound, so this refuses to print a
verdict until it has checked them.

NOT THE ICT LANE. sovereign/forex/forex_specialist.py runs the ICT pipeline,
which this repo deliberately excludes (unproven, permutation p=0.52).
sovereign/forex/carry_engine.py is a different carry strategy on different
pairs (AUDCHF, NZDJPY). Neither is v015. Do not substitute either.

This writes to no ledger and logs no decision. A scan candidate is not an
executed trade — that is the mistake spec 022 removed from ForexSpecialist.

Usage:
  python3 scripts/carry_scan.py
  python3 scripts/carry_scan.py --as-of 2024-12-02      # reproduce a sealed signal
  python3 scripts/carry_scan.py --ignore-preflight      # inspect only, never to trade
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

SEALED_CSV = ROOT / "data" / "proof" / "backtest_trades_v015_2015_2024.csv"
MACRO_CACHE = ROOT / "data" / "cache" / "macro"
CB_DECISIONS = ROOT / "data" / "cache" / "cb_decisions.json"

WARMUP_YEARS = 5          # >= 252 price bars and >= 200 SPY bars for the VIX gate
MACRO_MAX_STALE_DAYS = 45  # rate/CPI series older than this cannot price today's carry
COUNTRIES = ("US", "EU", "UK", "JP", "AU")   # the legs of the four sealed pairs


def sealed_universe() -> list[str]:
    """Pairs read from the sealed record, not typed here. If the traded universe
    ever diverges from the proven one, that is worth failing on."""
    if not SEALED_CSV.exists():
        raise SystemExit(f"sealed record missing: {SEALED_CSV}")
    with SEALED_CSV.open() as fh:
        pairs = sorted({r["pair"] for r in csv.DictReader(fh)})
    if not pairs:
        raise SystemExit(f"sealed record has no pair values: {SEALED_CSV}")
    return pairs


def preflight(as_of: date) -> list[str]:
    """Every way the signal can be quietly wrong. Returns a list of failures."""
    bad: list[str] = []

    env = (ROOT / ".env")
    has_key = bool(os.environ.get("FRED_API_KEY")) or (
        env.exists() and any(l.startswith("FRED_API_KEY=") and len(l.strip()) > 13
                             for l in env.read_text().splitlines()))
    if not has_key:
        bad.append("FRED_API_KEY absent — rate/CPI series fall back to FLAT CONSTANTS, "
                   "which collapses the carry leg to a constant (warning-only upstream)")

    import pandas as pd
    for c in COUNTRIES:
        for kind in ("rates", "cpi"):
            f = MACRO_CACHE / f"{c}_{kind}.parquet"
            if not f.exists():
                bad.append(f"{c}_{kind}: cache missing")
                continue
            try:
                last = pd.read_parquet(f).index.max()
                stale = (as_of - last.date()).days
                if stale > MACRO_MAX_STALE_DAYS:
                    bad.append(f"{c}_{kind}: ends {str(last)[:10]} ({stale}d stale, "
                               f"limit {MACRO_MAX_STALE_DAYS}d)")
            except Exception as e:
                bad.append(f"{c}_{kind}: unreadable ({type(e).__name__})")

    if not CB_DECISIONS.exists():
        bad.append("cb_decisions.json missing — the CB-surprise layer cannot fire")
    else:
        import json
        d = json.loads(CB_DECISIONS.read_text())
        recs = d if isinstance(d, list) else d.get("decisions", [])
        dates = [r.get("date", "") for r in recs if r.get("date")]
        if dates:
            stale = (as_of - date.fromisoformat(max(dates)[:10])).days
            if stale > MACRO_MAX_STALE_DAYS:
                bad.append(
                    f"cb_decisions.json: ends {max(dates)[:10]} ({stale}d stale) "
                    f"and is UNREPRODUCIBLE — entry_engine.py:23 and :99 name "
                    f"scripts/build_cb_decisions.py as its builder, but that script "
                    f"does not exist in this repo. It cannot be regenerated.")

    try:                                    # the gate inputs the engine swallows
        import yfinance as yf
        for t in ("SPY", "^VIX"):
            if len(yf.download(t, period="5d", progress=False, auto_adjust=True)) == 0:
                bad.append(f"{t}: no data — the SPY/VIX regime gate silently "
                           f"disables itself when this fails")
    except Exception as e:
        bad.append(f"regime-gate data unreachable: {type(e).__name__}: {e}")

    return bad


def rate_staleness(as_of: date) -> dict:
    """Per-{country}_{kind} staleness in days, from the SAME macro cache files
    `preflight()` checks (spec 039: half the signal weight on AUD/JPY carries
    a rate-vintage look-ahead in the sealed proof; every paper record needs
    this attached so a later analysis can separate "the edge failed" from
    "the inputs were stale"). None where a series is missing/unreadable —
    never a fabricated day count."""
    import pandas as pd
    out: dict[str, Optional[int]] = {}
    for c in COUNTRIES:
        for kind in ("rates", "cpi"):
            f = MACRO_CACHE / f"{c}_{kind}.parquet"
            key = f"{c}_{kind}"
            if not f.exists():
                out[key] = None
                continue
            try:
                last = pd.read_parquet(f).index.max()
                out[key] = (as_of - last.date()).days
            except Exception:
                out[key] = None
    return out


def _make_backtester(as_of: date):
    from sovereign.forex.forex_backtester import ForexBacktester
    # yfinance treats `end` as EXCLUSIVE, so passing as_of drops the as_of bar —
    # which is the one carrying the signal we are looking for.
    return ForexBacktester(
        start=(as_of - timedelta(days=365 * WARMUP_YEARS)).isoformat(),
        end=(as_of + timedelta(days=1)).isoformat())


def pair_bar(bt, pair: str):
    """The latest bar's close/ATR%/signal/hold_days for ONE pair, from the SAME
    engine call `scan()` and the backtester itself use — never a hand-copy of
    the rules. Returns None (with a note) if there isn't enough price history.

    Unlike `scan()`'s hits, this returns a bar EVEN WHEN signal == 0 — an
    already-open position needs today's close/ATR every day it holds, not
    only on the days a fresh entry signal fires. Shared by `scan()` (entries)
    and `scripts/paper_carry_resolver.py` (exits on open positions) so both
    read price/ATR/signal off one code path.

    Returns a dict: pair, bar_date, close, atr_pct, signal, hold_days — or
    (None, note) on failure.
    """
    from sovereign.forex.pair_universe import PAIR_CONFIG, CB_TO_COUNTRY

    cfg = PAIR_CONFIG.get(pair)
    if not cfg:
        return None, f"{pair}: absent from PAIR_CONFIG"
    df = bt._download_price(pair)
    if df is None or len(df) < 252:
        return None, f"{pair}: {0 if df is None else len(df)} bars, needs 252"

    sig = bt._get_pair_signals(
        df=df, pair=pair, hold_days=bt.PAIR_HOLD_OVERRIDES.get(pair, bt.HOLD_DAYS),
        base_country=CB_TO_COUNTRY[cfg.base_central_bank],
        quote_country=CB_TO_COUNTRY[cfg.quote_central_bank])
    if pair in bt.PAIR_VIX_GATES:
        sig = bt._apply_vix_regime_gate(sig, pair=pair, start=bt.start, end=bt.end)

    close = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
    atr_pct = float(bt._signals._compute_atr_pct(close, df).iloc[-1])
    return dict(pair=pair, bar_date=sig.index[-1].date(),
               close=float(close.iloc[-1]), atr_pct=atr_pct,
               signal=float(sig["signal"].iloc[-1]),
               hold_days=int(sig["hold_days"].iloc[-1])), None


def scan(as_of: date):
    bt = _make_backtester(as_of)
    pairs = sealed_universe()

    hits, notes = [], []
    for pair in pairs:
        bar, note = pair_bar(bt, pair)
        if bar is None:
            notes.append(note)
            continue
        if bar["signal"] == 0:
            continue

        stop_frac = bar["atr_pct"] * bt.STOP_ATR_MULT

        # The fill is the NEXT bar's open. Historically that bar exists, so the
        # exact sealed fill can be shown; live it does not exist yet.
        # Fetched separately rather than by widening the signal window, so no
        # post-as_of bar can reach the signal computation.
        fill, fill_bar = _next_open(pair, as_of)
        hits.append(dict(pair=pair, direction="LONG" if bar["signal"] > 0 else "SHORT",
                         signal_bar=bar["bar_date"], stop_frac=stop_frac, fill=fill,
                         fill_bar=fill_bar, hold=bar["hold_days"]))
    return hits, notes, pairs


def _next_open(pair: str, as_of: date):
    """The fill bar: the first session strictly after as_of. Live this does not
    exist yet and returns (None, None); historically it is the sealed fill."""
    try:
        import yfinance as yf
        d = yf.download(pair, start=(as_of + timedelta(days=1)).isoformat(),
                        end=(as_of + timedelta(days=8)).isoformat(),
                        progress=False, auto_adjust=True)
        if d is None or len(d) == 0:
            return None, None
        o = d["Open"].iloc[0]
        return float(o.iloc[0] if hasattr(o, "iloc") else o), d.index[0].date()
    except Exception:
        return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--risk", type=float, default=0.0075,
                    help="risk fraction for the suggested command; sealed record "
                         "used 0.0075 (median size_mult 0.75 x 1%%)")
    ap.add_argument("--firm", default="cti_1step")
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--ignore-preflight", action="store_true",
                    help="print the scan even if inputs are degraded. For "
                         "inspection only — never size a real trade from it.")
    a = ap.parse_args()
    as_of = date.fromisoformat(a.as_of)

    print(f"\nCARRY SCAN — v015, as of {as_of}")
    problems = preflight(as_of)
    if problems:
        print("\nPREFLIGHT FAILED — these inputs cannot price today's carry:")
        for p in problems:
            print(f"  ! {p}")
        if not a.ignore_preflight:
            print("\nRefusing to emit signals. A scan on degraded inputs is "
                  "indistinguishable from 'no setup today', which is exactly the "
                  "failure this guard exists to prevent.\n"
                  "Re-run with --ignore-preflight only to inspect.\n")
            return 2
        print("\n  --ignore-preflight set: output below is NOT tradeable.\n")
    else:
        print("preflight: ok")

    hits, notes, pairs = scan(as_of)
    print(f"universe (from sealed record): {', '.join(pairs)}")
    print("=" * 68)
    for n in notes:
        print(f"  ! {n}")

    print(f"\nSIGNALS ON {as_of}: {len(hits)}")
    if not hits:
        print("  none. Macro entries only fire on the first business day of the "
              "month; overlay layers are event-driven. Most days are empty.")
    for h in hits:
        print(f"\n  {h['pair']:10s} {h['direction']:5s}  signal bar {h['signal_bar']}  "
              f"hold {h['hold']}d")
        print(f"    stop distance {h['stop_frac']:.4%} of fill "
              f"(ATR%% at signal bar x {2.0})")
        if h["fill"] is None:
            print(f"    fill: NEXT SESSION'S OPEN — not yet known. Log the trade "
                  f"after the open with the actual fill:")
            print(f"      stop = fill * (1 - {h['stop_frac']:.6f})  [LONG]  /  "
                  f"fill * (1 + {h['stop_frac']:.6f})  [SHORT]")
        else:
            sgn = -1 if h["direction"] == "LONG" else 1
            stop = h["fill"] * (1 + sgn * h["stop_frac"])
            print(f"    fill {h['fill']:.5f} at open {h['fill_bar']}  ->  "
                  f"stop {stop:.5f}")
            try:
                from paper_carry_log import usd_risk_per_unit
                from sovereign.propfirm.firm_contracts import load_contract
                acct = load_contract(a.firm).account_size
                qty = (a.risk * acct) / usd_risk_per_unit(h["pair"], h["fill"], stop)
                print(f"      python3 scripts/paper_carry_log.py open "
                      f"--pair {h['pair']} --direction {h['direction']} "
                      f"--entry {h['fill']:.5f} --stop {stop:.5f} "
                      f"--risk {a.risk} --qty {qty:.0f}")
            except SystemExit as e:
                print(f"      (cannot size: {e})")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
