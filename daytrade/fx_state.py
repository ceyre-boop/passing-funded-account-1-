#!/usr/bin/env python3
"""FX STATE VECTOR — spec 032. The dictionary the machinery reads, rewritten
in the units a six-day carry position actually experiences.

The machine was reading five-minute equity bars and being graded on FX carry.
No learning survives that mismatch — the gradient has nowhere to flow. This
records, per (pair, session): the carry itself (rate differential and its
drift), what holding costs (swap accrual, weekend exposure), and daily-bar
price state. Point-in-time, hash-chained, live/backfill honest — spec 030's
discipline, applied to the right market.

THE VOCABULARY STARTS EMPTY. The equity ontology asserted nine regime labels
and the audit found six were decoration and two were the same label twice.
Here state is recorded numerically and a label enters only by passing
`ontology_audit` on FX outcomes. Nothing is named until it earns the name.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chain                                                       # noqa: E402
from execution.alpaca import load_env                              # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CARRY = ROOT / "data" / "carry"
LEDGER = CARRY / "fx_state.jsonl"
RATES = CARRY / "rates"
BARS = CARRY / "bars"

# The four sealed pairs, and which policy rate each leg reads.
# rate_diff = base - quote: the carry you are paid (or pay) to hold.
PAIRS = {
    "EURUSD=X": {"base": "EUR", "quote": "USD"},
    "GBPUSD=X": {"base": "GBP", "quote": "USD"},
    "USDJPY=X": {"base": "USD", "quote": "JPY"},
    "AUDUSD=X": {"base": "AUD", "quote": "USD"},
}
# Verified live 2026-08-20 before this module was written (spec 032).
# JPY and AUD are MONTHLY series and lag ~60 days — that staleness is
# reported per row, never forward-filled into a pretence of currency.
SERIES = {"USD": "DFF", "EUR": "ECBDFR", "GBP": "IUDSOIA",
          "JPY": "IRSTCI01JPM156N", "AUD": "IR3TIB01AUM156N"}

FRED = "https://api.stlouisfed.org/fred/series/observations"


class FxStateError(RuntimeError):
    """Unusable inputs. Never guessed around."""


# ------------------------------------------------------------------ sources

def fetch_rates(start: str = "2015-01-01") -> int:
    """Cache each policy-rate series to disk. Cached history only grows —
    the same frozen-history rule bars.py uses."""
    load_env()
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise FxStateError("FRED_API_KEY missing — rate differentials are the "
                           "carry itself and cannot be approximated")
    RATES.mkdir(parents=True, exist_ok=True)
    for ccy, sid in SERIES.items():
        url = (f"{FRED}?series_id={sid}&api_key={key}&file_type=json"
               f"&observation_start={start}")
        with urllib.request.urlopen(url, timeout=30) as r:
            obs = json.loads(r.read())["observations"]
        clean = {o["date"]: float(o["value"]) for o in obs if o["value"] != "."}
        (RATES / f"{ccy}.json").write_text(json.dumps(
            {"series_id": sid, "currency": ccy, "n": len(clean),
             "fetched_at": datetime.now(timezone.utc).isoformat(),
             "observations": clean}, indent=1))
        last = max(clean) if clean else "none"
        print(f"  {ccy} ({sid}): {len(clean)} observations, latest {last}")
    return 0


def fetch_bars(period: str = "10y") -> int:
    """Daily FX bars — the resolution a six-day hold is actually lived at."""
    import warnings
    warnings.filterwarnings("ignore")
    import pandas as pd
    import yfinance as yf
    BARS.mkdir(parents=True, exist_ok=True)
    for pair in PAIRS:
        df = yf.download(pair, period=period, interval="1d",
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            print(f"  !! {pair}: no bars returned — excluded loudly")
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close"]].sort_index()
        path = BARS / f"{pair.replace('=', '_')}.parquet"
        if path.exists():                       # frozen history wins
            old = pd.read_parquet(path)
            old.index = pd.to_datetime(old.index)
            df.index = pd.to_datetime(df.index)
            add = df.index.difference(old.index)
            df = pd.concat([old, df.loc[add]]).sort_index()
        df.to_parquet(path)
        print(f"  {pair}: {len(df)} daily bars, {df.index.min().date()} .. "
              f"{df.index.max().date()}")
    return 0


def _rate_at(ccy: str, on: date) -> tuple[float | None, int | None]:
    """The most recent observation AT OR BEFORE `on`, and how stale it is.
    Never interpolates forward — a rate you did not have is not a rate."""
    path = RATES / f"{ccy}.json"
    if not path.exists():
        return None, None
    obs = json.loads(path.read_text())["observations"]
    prior = [d for d in obs if d <= on.isoformat()]
    if not prior:
        return None, None
    d = max(prior)
    return obs[d], (on - date.fromisoformat(d)).days


# ------------------------------------------------------------- the vector

def swap_per_day() -> float:
    """From the firm contract. Never re-declared here (spec 032 I44)."""
    sys.path.insert(0, str(ROOT))
    from sovereign.propfirm.firm_contracts import load_contract
    c = load_contract("cti_1step")
    costs = getattr(c, "costs", None)          # FirmContract.costs: Costs
    v = getattr(costs, "swap_haircut_r_per_day", None)
    if v is None:
        raise FxStateError("contract exposes no costs.swap_haircut_r_per_day")
    return float(v)


def build(pair: str, *, source: str = "backfill", days: int = 0) -> int:
    """One row per session with everything knowable that session."""
    import pandas as pd
    path = BARS / f"{pair.replace('=', '_')}.parquet"
    if not path.exists():
        raise FxStateError(f"no bars for {pair} — run fetch-bars")
    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index)
    legs = PAIRS[pair]
    swap = swap_per_day()

    have = {(r["pair"], r["session_date"]) for r in chain.rows(LEDGER)}
    sessions = list(df.index)[-days:] if days else list(df.index)
    n = 0
    for ts in sessions:
        sess = ts.date()
        if (pair, sess.isoformat()) in have:
            continue
        hist = df[df.index <= ts]
        if len(hist) < 55:                      # need 50d trend + slack
            continue
        rb, sb = _rate_at(legs["base"], sess)
        rq, sq = _rate_at(legs["quote"], sess)
        diff = (rb - rq) if (rb is not None and rq is not None) else None
        stale = max([x for x in (sb, sq) if x is not None], default=None)

        def diff_ago(k: int):
            if len(hist) <= k:
                return None
            d0 = hist.index[-1 - k].date()
            a, _ = _rate_at(legs["base"], d0)
            b, _ = _rate_at(legs["quote"], d0)
            return None if (a is None or b is None or diff is None) else diff - (a - b)

        close = float(hist["Close"].iloc[-1])
        tr = (hist["High"] - hist["Low"]).abs()
        atr14 = float(tr.tail(14).mean()) / close * 100
        rets = hist["Close"].pct_change().tail(20)
        vol20 = float(rets.std()) * 100 if len(rets) > 2 else None
        ma20 = float(hist["Close"].tail(20).mean())
        ma50 = float(hist["Close"].tail(50).mean())
        hi20 = float(hist["Close"].tail(20).max())

        # next session across a weekend? 72% of sealed trades cross one.
        nxt = df[df.index > ts]
        weekend_next = (bool((nxt.index[0].date() - sess).days > 1)
                        if len(nxt) else None)

        chain.append(LEDGER, {
            "kind": "fx_state", "source": source, "pair": pair,
            "session_date": sess.isoformat(), "as_of": ts.isoformat(),
            "max_data_ts": hist.index.max().isoformat(),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            # the carry
            "rate_base": rb, "rate_quote": rq, "rate_diff": diff,
            "rate_diff_stale_days": stale,
            "rate_diff_d5": diff_ago(5), "rate_diff_d20": diff_ago(20),
            # what holding costs
            "swap_accrual_r_per_day": swap,
            "weekend_next_session": weekend_next,
            "weekend_cost_r": (round(3 * swap, 6) if weekend_next else 0.0),
            # price state on DAILY bars
            "close": round(close, 6),
            "atr14_pct": round(atr14, 4),
            "realized_vol_20d_pct": round(vol20, 4) if vol20 else None,
            "trend_pct_vs_ma20": round((close - ma20) / ma20 * 100, 4),
            "trend_pct_vs_ma50": round((close - ma50) / ma50 * 100, 4),
            "drawdown_from_20d_high_pct": round((close - hi20) / hi20 * 100, 4),
            # event risk — no verified free source yet, stated absent (I43)
            "cb_calendar_days_to_next": None,
            "positioning_extreme": None,
            # I45: no regime labels until one earns entry via ontology_audit
            "regimes": [],
        })
        n += 1
    print(f"  {pair}: {n} session row(s) written")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 032 — the FX state vector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("fetch-rates"); r.add_argument("--start", default="2015-01-01")
    b = sub.add_parser("fetch-bars"); b.add_argument("--period", default="10y")
    bu = sub.add_parser("build"); bu.add_argument("--pair", default=None)
    bu.add_argument("--days", type=int, default=0)
    sub.add_parser("verify")
    sub.add_parser("stats")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "fetch-rates":
            return fetch_rates(a.start)
        if a.cmd == "fetch-bars":
            return fetch_bars(a.period)
        if a.cmd == "build":
            for p in ([a.pair] if a.pair else list(PAIRS)):
                build(p, days=a.days)
            return 0
        if a.cmd == "verify":
            return chain.verify(LEDGER)
        rows = chain.rows(LEDGER)
        from collections import Counter
        with_diff = [r for r in rows if r.get("rate_diff") is not None]
        print(f"  {len(rows)} FX state rows · "
              f"{dict(Counter(r['pair'] for r in rows))}")
        if rows:
            print(f"  sessions: {min(r['session_date'] for r in rows)} .. "
                  f"{max(r['session_date'] for r in rows)}")
            print(f"  rows with a usable rate_diff: {len(with_diff)}/{len(rows)}")
            st = [r["rate_diff_stale_days"] for r in with_diff
                  if r["rate_diff_stale_days"] is not None]
            if st:
                print(f"  rate staleness days: median "
                      f"{sorted(st)[len(st)//2]}, max {max(st)}")
            print(f"  weekend-crossing sessions: "
                  f"{sum(1 for r in rows if r.get('weekend_next_session'))}")
            print(f"  regime labels defined: "
                  f"{len({g for r in rows for g in r['regimes']})} "
                  "(empty until one earns entry — spec 032 I45)")
        return 0
    except FxStateError as e:
        print(f"  REFUSED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
