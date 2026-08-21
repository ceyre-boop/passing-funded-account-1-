#!/usr/bin/env python3
"""MACRO STATE — the conditions a carry book actually lives or dies in.

The carry edge's one catastrophic failure mode is a regime break / carry
unwind, and unwinds are driven by things absent from a price bar: risk
appetite collapsing, financial conditions tightening, the dollar bidding.
Those have public daily series, so they are observable — the reason the
system could not see them was that nobody had wired them, not that they were
unknowable.

Same discipline as every other ledger here: point-in-time (never an
observation the date did not have), per-series staleness recorded rather
than forward-filled into a pretence of currency, hash-chained, absences
stated.

ONE ADDITION THAT MATTERS FOR A MODEL READER: raw levels are close to
meaningless without context. "VIX 14.89" tells a language model very little;
"VIX 14.89, 12th percentile of the trailing two years" tells it a great deal
in the same number of tokens. Every series therefore carries its trailing
percentile rank AND its 20-day change, computed point-in-time.
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
LEDGER = CARRY / "macro_state.jsonl"
SERIES_DIR = CARRY / "macro"
FRED = "https://api.stlouisfed.org/fred/series/observations"
LOOKBACK_DAYS = 730          # trailing window for percentile context

# Verified live 2026-08-21 before this module was written. Each is here for a
# stated reason — no series is included because it was available.
SERIES = {
    "VIXCLS":            "equity implied vol — the risk thermometer",
    "VXVCLS":            "3-month VIX; the term structure inverts before stress",
    "DTWEXBGS":          "broad dollar index — a bid dollar IS the carry unwind",
    "BAMLH0A0HYM2":      "US high-yield OAS — risk appetite, the classic tell",
    "BAMLEMHBHYCRPIOAS": "EM high-yield OAS — where carry funding shows first",
    "STLFSI4":           "St Louis financial stress index",
    "NFCI":              "Chicago financial conditions index",
    "DGS10":             "US 10y nominal",
    "DGS2":              "US 2y nominal — the funding leg",
    "T10Y2Y":            "10y-2y spread; inversion regimes behave differently",
    "DFII10":            "10y TIPS real yield — the real funding cost",
    "DCOILWTICO":        "WTI crude — the AUD/CAD commodity channel",
}


class MacroError(RuntimeError):
    """Unusable inputs. Never guessed around."""


def fetch(start: str = "2015-01-01") -> int:
    load_env()
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise MacroError("FRED_API_KEY missing — macro conditions cannot be "
                         "approximated from price")
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    for sid, why in SERIES.items():
        url = (f"{FRED}?series_id={sid}&api_key={key}&file_type=json"
               f"&observation_start={start}")
        with urllib.request.urlopen(url, timeout=30) as r:
            obs = json.loads(r.read())["observations"]
        clean = {o["date"]: float(o["value"]) for o in obs if o["value"] != "."}
        (SERIES_DIR / f"{sid}.json").write_text(json.dumps(
            {"series_id": sid, "why": why, "n": len(clean),
             "fetched_at": datetime.now(timezone.utc).isoformat(),
             "observations": clean}, indent=1))
        print(f"  {sid:20s} {len(clean):5d} obs, latest "
              f"{max(clean) if clean else 'none'}")
    return 0


def _load(sid: str) -> dict:
    p = SERIES_DIR / f"{sid}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())["observations"]


def series_at(sid: str, on: date, obs: dict | None = None) -> dict | None:
    """Value, staleness, trailing percentile and 20-day change — all computed
    from observations AT OR BEFORE `on`. Nothing the date did not have."""
    obs = obs if obs is not None else _load(sid)
    prior = sorted(d for d in obs if d <= on.isoformat())
    if not prior:
        return None
    latest = prior[-1]
    val = obs[latest]
    window_start = (on - timedelta(days=LOOKBACK_DAYS)).isoformat()
    window = [obs[d] for d in prior if d >= window_start]
    pct = (round(100 * sum(1 for v in window if v <= val) / len(window), 1)
           if len(window) >= 30 else None)
    d20 = None
    ref = (on - timedelta(days=28)).isoformat()      # ~20 business days
    older = [d for d in prior if d <= ref]
    if older:
        d20 = round(val - obs[max(older)], 4)
    return {"value": val, "as_of": latest,
            "stale_days": (on - date.fromisoformat(latest)).days,
            "pctile_2y": pct, "change_20d": d20}


def snapshot(on: date) -> dict:
    """Every macro series as of a date, with context. Absent series appear
    with a stated reason rather than vanishing."""
    out, missing = {}, []
    for sid in SERIES:
        obs = _load(sid)
        if not obs:
            missing.append(sid)
            continue
        s = series_at(sid, on, obs)
        if s is None:
            missing.append(sid)
        else:
            out[sid] = s
    return {"series": out, "unavailable": missing}


def build(days: int = 0) -> int:
    """One chained row per business date the series can support."""
    all_dates = set()
    for sid in SERIES:
        all_dates |= set(_load(sid))
    if not all_dates:
        raise MacroError("no series cached — run fetch first")
    dates = sorted(date.fromisoformat(d) for d in all_dates)
    if days:
        dates = dates[-days:]
    have = {r["session_date"] for r in chain.rows(LEDGER)}
    n = 0
    for d in dates:
        if d.isoformat() in have:
            continue
        snap = snapshot(d)
        if len(snap["series"]) < 6:            # too thin to be a condition read
            continue
        chain.append(LEDGER, {
            "kind": "macro_state", "session_date": d.isoformat(),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "n_series": len(snap["series"]),
            "unavailable": snap["unavailable"], **snap["series"]})
        n += 1
    print(f"  {n} macro row(s) written")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="macro conditions, point-in-time")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--start", default="2015-01-01")
    b = sub.add_parser("build"); b.add_argument("--days", type=int, default=0)
    sub.add_parser("verify")
    s = sub.add_parser("show"); s.add_argument("--date", default=None)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "fetch":
            return fetch(a.start)
        if a.cmd == "build":
            return build(a.days)
        if a.cmd == "verify":
            return chain.verify(LEDGER)
        on = (date.fromisoformat(a.date) if a.date
              else datetime.now(timezone.utc).date())
        snap = snapshot(on)
        print(f"  macro conditions as of {on}")
        for sid, s in snap["series"].items():
            pct = f"{s['pctile_2y']:5.1f}%ile" if s["pctile_2y"] is not None else "  n/a  "
            stale = f"{s['stale_days']}d old" if s["stale_days"] else "current"
            print(f"    {sid:20s} {s['value']:>10.3f}  {pct}  "
                  f"Δ20d {str(s['change_20d']):>8s}  ({stale})")
        if snap["unavailable"]:
            print(f"    UNAVAILABLE (stated, not guessed): {snap['unavailable']}")
        return 0
    except MacroError as e:
        print(f"  REFUSED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
