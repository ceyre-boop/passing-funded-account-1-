#!/usr/bin/env python3
"""DECISION LEDGER — spec 030. What was knowable, at the moment it mattered.

The soak proved the expensive way that a system which is not OBSERVING the
decision channel cannot learn from it: 22 sealed judgments, zero of them in
the 09:30-11:00 ET entry window, so MECH-006 accrued no evidence and never
would have. Judgment is expensive and needs a live model; OBSERVATION is
cheap and needs nothing but discipline. This file separates them.

Two capture modes, never conflated:

  live     — written by the tick as the session runs. What the machine
             actually saw, when it saw it.
  backfill — reconstructed later from point-in-time sources (bars with their
             own timestamps, headlines with published_utc). Honest about
             WHAT WAS KNOWABLE at T, silent about what any model saw, because
             nothing was running. Every row says which it is.

Immutability is a hash chain: each row carries the sha256 of the row before
it, so a rewritten past is detectable rather than merely discouraged. The
chain is verified by the test suite (same guard shape as 028/029).

This file makes no decisions and calls no model. It is the substrate the
mechanism ledger measures against — conditions, not verdicts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "daytrade" / "decision_ledger.jsonl"
CALENDAR = ROOT / "data" / "daytrade" / "macro_calendar.md"

OR_START, OR_END = "09:30", "10:00"
TRIGGER_END = "11:00"
DECISION_POINTS = ("09:35", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00")

# A small, explicit regime vocabulary (architecture review 2026-08-19). These
# are DESCRIPTIVE LABELS on observed state — not a classifier, not a policy
# input, and deliberately not spec 001's REGIME decision rule, which stays
# unbuilt. Every mechanism must state the domain it expects to hold in; these
# are the words it uses.
REGIMES = ("TREND_UP", "TREND_DOWN", "COMPRESSION", "HIGH_VOL", "LOW_VOL",
           "GAP_UP", "GAP_DOWN", "EVENT_DAY", "UNREADABLE")


class LedgerError(RuntimeError):
    """Broken chain or unusable inputs. Never repaired silently."""


def _rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for i, line in enumerate(LEDGER.open(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise LedgerError(f"{LEDGER.name}:{i} is not JSON ({e})") from e
    return out


def _row_hash(row: dict) -> str:
    body = {k: v for k, v in row.items() if k != "row_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def append(row: dict) -> dict:
    """Append with the hash chain. fsync'd — an audit row the OS buffered and
    lost is a row that never happened at the worst possible moment."""
    prev = _rows()
    row["seq"] = len(prev)
    row["prev_sha256"] = prev[-1]["row_sha256"] if prev else None
    row["row_sha256"] = _row_hash(row)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return row


def verify() -> int:
    """Walk the chain. A rewritten past breaks it, loudly."""
    rows = _rows()
    prev_hash = None
    for r in rows:
        if r.get("prev_sha256") != prev_hash:
            print(f"  !! CHAIN BREAK at seq {r.get('seq')}: prev_sha256 mismatch")
            return 1
        if _row_hash(r) != r.get("row_sha256"):
            print(f"  !! ROW TAMPERED at seq {r.get('seq')}: hash mismatch")
            return 1
        prev_hash = r["row_sha256"]
    print(f"  {len(rows)} decision row(s), chain intact")
    return 0


# ------------------------------------------------------- the observable state

def _event_day(day) -> bool:
    """Event-day flag from the macro calendar the morning read already uses.
    Absent calendar = False with the absence stated, never a guess."""
    if not CALENDAR.exists():
        return False
    return str(day) in CALENDAR.read_text()


def snapshot(symbol: str, as_of: datetime, *, source: str) -> dict:
    """Every feature knowable at `as_of` and nothing later. The point-in-time
    discipline is spec 024 I14's, applied to structured state instead of
    prose: bars strictly <= as_of, headlines by published_utc."""
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bars as bars_mod

    path = bars_mod._cache_path(symbol, "5m")
    if not path.exists():
        raise LedgerError(f"no bar cache for {symbol} — nothing was knowable")
    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index, utc=True)
    hist = df[df.index <= as_of]
    if len(hist) < 6:
        raise LedgerError(f"{symbol} @ {as_of}: fewer than 6 prior bars")

    et_day = as_of.astimezone(ET).date()
    today = hist[hist.index.map(lambda t: t.astimezone(ET).date()) == et_day]
    prior = hist[hist.index.map(lambda t: t.astimezone(ET).date()) < et_day]

    last = float(hist["Close"].iloc[-1])
    tr = (hist["High"] - hist["Low"]).abs()
    tr5 = float(tr.tail(5).mean())
    tr20 = float(tr.tail(20).median())
    prior_close = float(prior["Close"].iloc[-1]) if len(prior) else last
    day_open = float(today["Open"].iloc[0]) if len(today) else last
    gap_pct = (day_open - prior_close) / prior_close * 100 if prior_close else 0.0

    # opening-range state, the thing the entry rule actually keys on
    or_bars = today[today.index.map(
        lambda t: OR_START <= t.astimezone(ET).strftime("%H:%M") <= OR_END)]
    or_hi = float(or_bars["High"].max()) if len(or_bars) else None
    or_lo = float(or_bars["Low"].min()) if len(or_bars) else None
    or_complete = len(or_bars) >= 6
    broken = (None if not or_complete else
              "up" if last > or_hi else "down" if last < or_lo else "inside")

    ma20 = float(hist["Close"].tail(20).mean())
    trend = (last - ma20) / ma20 * 100 if ma20 else 0.0
    compression = (tr5 / tr20) if tr20 else None
    vol_state = ("HIGH_VOL" if compression and compression > 1.5 else
                 "LOW_VOL" if compression and compression < 0.7 else None)

    regimes = []
    if _event_day(et_day):
        regimes.append("EVENT_DAY")
    if abs(gap_pct) > 0.5:
        regimes.append("GAP_UP" if gap_pct > 0 else "GAP_DOWN")
    if compression is not None and compression < 0.7:
        regimes.append("COMPRESSION")
    if vol_state:
        regimes.append(vol_state)
    if abs(trend) > 0.5:
        regimes.append("TREND_UP" if trend > 0 else "TREND_DOWN")
    if not regimes:
        regimes = ["UNREADABLE"]

    heads_1h = None
    if os.environ.get("POLYGON_API_KEY") and source == "live":
        # headlines are only honestly countable live; a backfill would need a
        # historical news window this tier does not serve, so it stays null
        try:
            from polygon_news import fetch_headlines
            cutoff = as_of - timedelta(hours=1)
            heads_1h = sum(1 for h in fetch_headlines(symbol)
                           if h.get("published_utc")
                           and cutoff <= datetime.fromisoformat(
                               h["published_utc"].replace("Z", "+00:00")) <= as_of)
        except Exception as e:
            print(f"  !! headline count unavailable ({e}) — recorded as null")

    return {
        "kind": "decision_point", "source": source,
        "symbol": symbol, "as_of": as_of.isoformat(),
        "session": str(et_day), "et_time": as_of.astimezone(ET).strftime("%H:%M"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "max_data_ts": hist.index.max().isoformat(),
        "last": round(last, 4), "day_open": round(day_open, 4),
        "prior_close": round(prior_close, 4), "gap_pct": round(gap_pct, 4),
        "or_high": round(or_hi, 4) if or_hi else None,
        "or_low": round(or_lo, 4) if or_lo else None,
        "or_complete": or_complete, "or_state": broken,
        "tr5": round(tr5, 4), "tr20_median": round(tr20, 4),
        "compression": round(compression, 4) if compression else None,
        "trend_pct_vs_ma20": round(trend, 4),
        "volume_last": int(hist["Volume"].iloc[-1]),
        "headlines_last_hour": heads_1h,
        "regimes": regimes,
    }


def capture(symbol: str) -> int:
    """One live row for right now. Cheap by design — no model, no API cost —
    so it can run on every tick regardless of whether a judgment happens."""
    row = snapshot(symbol, datetime.now(timezone.utc), source="live")
    append(row)
    print(f"  captured {symbol} {row['et_time']} ET  regimes={row['regimes']}  "
          f"or_state={row['or_state']}")
    return 0


def backfill(symbol: str, days: int) -> int:
    """Reconstruct the entry-window decision points for recent sessions from
    point-in-time sources.

    HONESTY BOUNDARY: a backfilled row says what was KNOWABLE at T. It does
    NOT claim any model saw it — nothing was running. Rows are marked
    source=backfill and must never be pooled with live rows when the question
    is 'what did the system observe'. They are valid for 'what did the market
    look like', which is what conditional mechanisms need.
    """
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bars as bars_mod

    path = bars_mod._cache_path(symbol, "5m")
    if not path.exists():
        raise LedgerError(f"no bar cache for {symbol}")
    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index, utc=True)
    sessions = sorted({t.astimezone(ET).date() for t in df.index})[-days:]

    have = {(r["symbol"], r["session"], r["et_time"]) for r in _rows()
            if r.get("kind") == "decision_point"}
    n = 0
    for sess in sessions:
        for hhmm in DECISION_POINTS:
            if (symbol, str(sess), hhmm) in have:
                continue
            h, mnt = int(hhmm[:2]), int(hhmm[3:])
            as_of = datetime(sess.year, sess.month, sess.day, h, mnt,
                             tzinfo=ET).astimezone(timezone.utc)
            if as_of > datetime.now(timezone.utc):
                continue
            try:
                append(snapshot(symbol, as_of, source="backfill"))
                n += 1
            except LedgerError:
                continue          # session had no bars at that point; skip quietly
    print(f"  {symbol}: {n} decision point(s) backfilled over {len(sessions)} session(s)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 030 — the decision ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture"); c.add_argument("--symbol", default="NVDA")
    b = sub.add_parser("backfill"); b.add_argument("--symbol", default="NVDA")
    b.add_argument("--days", type=int, default=30)
    sub.add_parser("verify")
    s = sub.add_parser("stats")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "capture":
            return capture(a.symbol)
        if a.cmd == "backfill":
            return backfill(a.symbol, a.days)
        if a.cmd == "verify":
            return verify()
        rows = [r for r in _rows() if r.get("kind") == "decision_point"]
        from collections import Counter
        print(f"  {len(rows)} decision points "
              f"({sum(1 for r in rows if r['source'] == 'live')} live / "
              f"{sum(1 for r in rows if r['source'] == 'backfill')} backfill)")
        print(f"  sessions covered: {len({r['session'] for r in rows})}")
        print(f"  entry-window (09:35-11:00) rows: "
              f"{sum(1 for r in rows if '09:35' <= r['et_time'] <= '11:00')}")
        print(f"  regimes: {dict(Counter(g for r in rows for g in r['regimes']))}")
        return 0
    except LedgerError as e:
        print(f"  REFUSED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
