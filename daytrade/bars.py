#!/usr/bin/env python3
"""BARS — session bar loading with a PINNED cache. Foundation for spec 005/008.

WHY THIS CACHES, AND WHY THAT IS NOT AN OPTIMISATION
----------------------------------------------------
yfinance serves intraday history as a rolling window: "last 60 days" of 5m bars
means something different every single morning. If the 40/20 tune/sealed split
(spec 008) were computed against a live fetch, the sealed 20 would quietly change
identity overnight — a holdout that reshuffles itself is not a holdout, and the
contamination would be invisible.

So bars are fetched ONCE into data/daytrade/bars/{symbol}_{tf}.parquet and every
later run reads the cache. `--refresh` appends genuinely new sessions and refuses
to alter any session already stored. Cached history only ever grows.

FAIL LOUD (APEX #2)
-------------------
A gap inside a session raises. A short session (half day) is EXCLUDED with a
printed reason, never silently mixed into a 78-bar population. Nothing is ever
forward-filled — an interpolated bar is a fact the market never produced.
"""
from __future__ import annotations

import sys
import warnings
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daytrade" / "bars"

RTH_OPEN, RTH_CLOSE = "09:30", "15:55"
FULL_SESSION_BARS_5M = 78          # 09:30..15:55 inclusive
MIN_SESSION_BARS_5M = 70           # below this the session is treated as a half day


class BarDataError(RuntimeError):
    """Bad or missing bar data. Never downgraded to a warning."""


@dataclass(frozen=True)
class Session:
    """One trading day of RTH bars for one symbol."""
    symbol: str
    day: date
    df: "object"                   # pandas DataFrame, ET-indexed, RTH only

    def __len__(self) -> int:
        return len(self.df)

    def between(self, start_hhmm: str, end_hhmm: str):
        """Bars with index time in [start, end] — inclusive both ends."""
        idx = self.df.index
        t = idx.strftime("%H:%M")
        return self.df[(t >= start_hhmm) & (t <= end_hhmm)]

    def after(self, hhmm: str):
        t = self.df.index.strftime("%H:%M")
        return self.df[t > hhmm]


def _cache_path(symbol: str, tf: str) -> Path:
    return CACHE / f"{symbol}_{tf}.parquet"


def fetch(symbol: str, tf: str = "5m", period: str = "60d"):
    """Raw pull from yfinance, ET-localised. No caching, no filtering."""
    warnings.filterwarnings("ignore")
    import pandas as pd
    import yfinance as yf

    df = yf.download(symbol, period=period, interval=tf,
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise BarDataError(f"yfinance returned no {tf} bars for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = df.index.tz_convert(ET)
    return df[["Open", "High", "Low", "Close", "Volume"]].sort_index()


def refresh_cache(symbol: str, tf: str = "5m", period: str = "60d") -> dict:
    """Fetch and merge into the cache. Existing sessions are NEVER rewritten.

    A session already on disk is frozen history. If a fresh pull disagrees with
    it, that is reported loudly and the stored version wins — silently accepting
    a vendor restatement would move the ground under an already-measured split.
    """
    import pandas as pd

    fresh = fetch(symbol, tf, period)
    path = _cache_path(symbol, tf)
    CACHE.mkdir(parents=True, exist_ok=True)

    if path.exists():
        old = pd.read_parquet(path)
        old.index = pd.to_datetime(old.index, utc=True).tz_convert(ET)
        overlap = fresh.index.intersection(old.index)
        changed = 0
        if len(overlap):
            diff = (fresh.loc[overlap, "Close"] - old.loc[overlap, "Close"]).abs()
            changed = int((diff > 1e-6).sum())
            if changed:
                print(f"  !! {changed} cached bars disagree with a fresh pull — "
                      f"KEEPING the cached values (frozen history wins)")
        added = fresh.index.difference(old.index)
        merged = pd.concat([old, fresh.loc[added]]).sort_index()
        result = {"added": len(added), "restated_ignored": changed}
    else:
        merged = fresh
        result = {"added": len(fresh), "restated_ignored": 0}

    merged.to_parquet(path)
    result["total_bars"] = len(merged)
    result["path"] = str(path)
    return result


def load_sessions(symbol: str, tf: str = "5m", *, allow_fetch: bool = True) -> list[Session]:
    """Every complete RTH session in the cache, oldest first.

    Half days and any session carrying an internal gap are excluded and the
    reason is printed. They are never repaired.
    """
    import pandas as pd

    path = _cache_path(symbol, tf)
    if not path.exists():
        if not allow_fetch:
            raise BarDataError(f"no cache at {path} and fetching is disabled")
        print(f"  no cache for {symbol} {tf} — fetching once")
        refresh_cache(symbol, tf)

    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    t = df.index.strftime("%H:%M")
    rth = df[(t >= RTH_OPEN) & (t <= RTH_CLOSE)]

    sessions, skipped = [], []
    for day, chunk in rth.groupby(rth.index.date):
        if len(chunk) < MIN_SESSION_BARS_5M:
            skipped.append((day, f"{len(chunk)} bars — half day or partial"))
            continue
        gaps = _internal_gaps(chunk, tf)
        if gaps:
            raise BarDataError(
                f"{symbol} {day}: {len(gaps)} missing bar(s) inside the session "
                f"(first at {gaps[0]}). Bars are never interpolated — fix the "
                "cache or exclude the day explicitly.")
        sessions.append(Session(symbol, day, chunk))

    if skipped:
        print(f"  excluded {len(skipped)} incomplete session(s): " +
              ", ".join(f"{d} ({why})" for d, why in skipped[:5]) +
              (" ..." if len(skipped) > 5 else ""))
    return sessions


def load_partial_session(symbol: str, day: date, end_hhmm: str,
                          tf: str = "5m") -> Session | None:
    """One IN-PROGRESS session for `day`, RTH_OPEN..end_hhmm inclusive, or None.

    NOT a weakening of load_sessions. load_sessions' MIN_SESSION_BARS_5M /
    70-bar completeness rule protects the BACKTEST POPULATION: a half day or a
    still-forming today must never be counted as a sealed session, because
    that population is what tune/sealed splits and every measured edge are
    built on. That rule is untouched here and must stay untouched.

    This accessor exists for a different consumer with a structurally
    different need: a same-day LIVE writer (write_baseline_plan.py) whose
    entry rule (ceiling.find_entry) only ever reads bars through the trigger
    window (09:30 open through TRIGGER_END, currently 11:00 — 19 bars, not
    the 78-bar full session or even the 70-bar completeness floor). Waiting
    for load_sessions' completeness gate to clear (~15:20 ET, when the 70th
    bar exists) means a plan computable at ~11:05 doesn't get written until
    the session is nearly over — every forecast in between is permanently
    unscoreable. This function serves exactly that narrow, same-day need and
    must never be used to assemble a backtest population — callers that build
    tune/sealed splits or measure an edge belong on load_sessions, always.

    Fails loud the same way load_sessions does: a gap inside whatever bars
    ARE present still raises via _internal_gaps — a hole is corruption
    whether the session is finished or still forming, and it is never
    interpolated. This check runs BEFORE the coverage check below, and
    deliberately does not depend on the window having reached end_hhmm yet —
    a hole earlier in a still-forming morning is exactly as much corruption
    as a hole in a finished day, and must not be masked by "well, we're not
    done yet" (that would be silent data loss with a plausible excuse).

    Coverage is checked separately, after the gap check: if the last bar on
    file for the day is before end_hhmm — the window simply has not been
    reached yet, nothing missing, just not-there-yet — this returns None
    rather than a truncated partial-of-a-partial. Callers should read None as
    "not yet", not as "nothing there" and not as "corrupt".
    """
    import pandas as pd

    path = _cache_path(symbol, tf)
    if not path.exists():
        raise BarDataError(f"no cache at {path}")

    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    t = df.index.strftime("%H:%M")
    rth = df[(t >= RTH_OPEN) & (t <= RTH_CLOSE)]

    chunk = rth[rth.index.date == day]
    if chunk.empty:
        return None

    t2 = chunk.index.strftime("%H:%M")
    window = chunk[t2 <= end_hhmm]
    if window.empty:
        return None

    gaps = _internal_gaps(window, tf)
    if gaps:
        raise BarDataError(
            f"{symbol} {day}: {len(gaps)} missing bar(s) inside the partial "
            f"session window (first at {gaps[0]}). Bars are never "
            "interpolated — fix the cache or exclude the day explicitly.")

    expected_end = pd.Timestamp.combine(day, pd.Timestamp(end_hhmm).time()).tz_localize(ET)
    if window.index[-1] < expected_end:
        return None                    # window not fully present yet — "not yet", not "empty"

    return Session(symbol, day, window)


def _internal_gaps(chunk, tf: str) -> list:
    """Timestamps that should exist between first and last bar but don't."""
    import pandas as pd
    step = pd.Timedelta(minutes=int(tf.rstrip("m")))
    expected = pd.date_range(chunk.index[0], chunk.index[-1], freq=step)
    return [ts for ts in expected if ts not in chunk.index]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="bar cache management")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--refresh", action="store_true", help="pull and merge new sessions")
    a = ap.parse_args()

    if a.refresh:
        r = refresh_cache(a.symbol, a.tf)
        print(f"cache {r['path']}: +{r['added']} bars, {r['total_bars']} total")

    s = load_sessions(a.symbol, a.tf)
    if not s:
        print("no complete sessions"); sys.exit(1)
    print(f"{len(s)} complete sessions  {s[0].day} -> {s[-1].day}")
    print(f"bars per session: min {min(len(x) for x in s)}  max {max(len(x) for x in s)}")
