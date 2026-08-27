#!/usr/bin/env python3
"""EXTENDED FEATURES — per-day ex-ante feature table over the extended NVDA
population.

WHY THIS EXISTS
---------------
`data/daytrade/bars_extended/NVDA_5m.parquet` holds 664 sessions
(2024-01-02..2026-08-25, Alpaca SIP) — see `scripts/prove_datasource.py`. Every
ex-ante feature source that already exists in the repo is far shorter:
`data/daytrade/decision_ledger.jsonl` covers only 86 sessions
(2026-05-07..2026-08-24). The one artifact that spans the whole extended
window is `data/carry/macro_state.jsonl` / `data/carry/macro/*.json`
(2015-01-02..2026-08-20, 12 FRED series), and it is currently unused by any
equity-lane module. This script joins the two: bar-derived decision-point
state from the extended cache, plus macro conditions, for every trading
session the extended cache holds.

WHAT IT DOES
------------
1. Regenerates decision-ledger-style snapshots (`daytrade/decision_ledger.py
   ::snapshot`) over the extended bar cache, at the canonical
   `decision_ledger.DECISION_POINTS`. `bars.CACHE` is redirected to
   `data/daytrade/bars_extended` for the duration, using the exact
   save/redirect/restore-in-`finally` pattern in
   `daytrade/oracle_audit.py::_collect` — including its refusal to fall back
   silently when the target cache is empty.
2. Joins `data/carry/macro_state.jsonl`'s underlying per-series observation
   files on **D-1, never D** — FRED daily series publish with a lag, so at
   09:30 ET on session date D the newest genuinely-knowable macro print is
   D-1's. The as-of lookup is `daytrade/macro_state.py::series_at`, called
   directly against `macro_state._load(sid)` — not reimplemented here.
3. Emits one row per (session_date, decision_point).

HARD CONSTRAINTS
----------------
- NO LOOK-AHEAD. `_assert_no_lookahead` below is the explicit boundary
  assertion `specs/005_BACKTEST.md` requires, in the shape
  `daytrade/backtest.py::label_history`'s docstring specifies: classify-time
  data (`<=` the decision point) may never contain anything the block did not
  have yet, and the macro block must never reach forward into the session
  date itself. It is invoked on EVERY row, not sampled.
- Absence is first-class (`daytrade/regime_vector.py`'s source ∈ {computed,
  judged, unavailable} convention, applied here to macro conditions). A
  missing macro observation becomes an explicit `None` plus a stated reason
  in `macro_unavailable` — never a silent 0.
- `headlines_last_hour` is excluded. It is live-only in
  `daytrade/decision_ledger.py::snapshot` (populated only when
  `POLYGON_API_KEY` is set and `source == "live"`) and historically 156/6979
  non-null — a backfill run over 664 sessions would emit a column that is
  >99% null and would look like a real feature. Naming the exclusion here is
  more honest than shipping the column.
- `data/daytrade/bars/` (the live cache) and `data/proof/` are never touched.

    python3 scripts/build_extended_features.py --symbol NVDA
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))
import bars as bars_mod                                            # noqa: E402
from bars import BarDataError                                      # noqa: E402
import decision_ledger                                             # noqa: E402
import macro_state                                                 # noqa: E402

ET = ZoneInfo("America/New_York")
EXT_CACHE = ROOT / "data" / "daytrade" / "bars_extended"
OUT = ROOT / "data" / "daytrade" / "extended_features.parquet"
OUT_SIDECAR = ROOT / "data" / "daytrade" / "extended_features.json"
SYMBOL = "NVDA"

# The row fields decision_ledger.snapshot() returns that are genuinely
# ex-ante features. Explicitly NOT here: `headlines_last_hour` (see module
# docstring), `kind`/`source`/`captured_at` (bookkeeping, not market state).
SNAPSHOT_FIELDS = (
    "last", "day_open", "prior_close", "gap_pct",
    "or_high", "or_low", "or_complete", "or_state",
    "tr5", "tr20_median", "compression", "trend_pct_vs_ma20",
    "volume_last",
)


class FeatureBuildError(RuntimeError):
    """Unusable inputs or a broken invariant. Never repaired silently."""


@contextlib.contextmanager
def _redirected_cache(cache: Path):
    """SAVE/RESTORE-IN-`finally`, copied from `oracle_audit.py::_collect`.

    A faulted module global outliving this call would silently repoint every
    later reader in the process at the wrong parquet tree, so the guard
    against an empty cache and the `finally` restore are both non-negotiable.
    """
    if not any(cache.glob("*_5m.parquet")):
        raise BarDataError(
            f"cache {cache} holds no *_5m.parquet. Refusing to fall back to "
            f"{bars_mod.CACHE} — a silent fallback would build features from "
            f"one population's name and another's data.")
    orig = bars_mod.CACHE
    bars_mod.CACHE = cache
    try:
        yield
    finally:
        bars_mod.CACHE = orig


def _trading_sessions(symbol: str, cache: Path) -> list[date]:
    """Distinct ET trading dates the extended cache holds."""
    path = cache / f"{symbol}_5m.parquet"
    if not path.exists():
        raise BarDataError(f"no bar cache for {symbol} at {cache}")
    df = pd.read_parquet(path)
    idx = pd.to_datetime(df.index)
    idx = idx.tz_localize(ET) if idx.tz is None else idx.tz_convert(ET)
    return sorted({t.date() for t in idx})


def macro_block(session_date: date) -> tuple[dict, list[str]]:
    """Every macro series evaluated at D-1, never D. Uses
    `macro_state.series_at` directly against `macro_state._load` — the as-of
    lookup is not reimplemented here. Absent series get an explicit `None`
    plus a stated reason in the returned `missing` list, never a zero."""
    d_minus_1 = session_date - timedelta(days=1)
    out: dict[str, dict | None] = {}
    missing: list[str] = []
    for sid in macro_state.SERIES:
        obs = macro_state._load(sid)
        if not obs:
            out[sid] = None
            missing.append(f"{sid}: no cached observations")
            continue
        s = macro_state.series_at(sid, d_minus_1, obs)
        if s is None:
            out[sid] = None
            missing.append(f"{sid}: no observation on or before {d_minus_1}")
        else:
            out[sid] = s
    return out, missing


def _assert_no_lookahead(row: dict, macro: dict, session_date: date) -> None:
    """The look-ahead boundary assertion specs/005_BACKTEST.md requires.

    Two independent checks, either of which is a hard failure:
      1. the bar-derived state never reaches past its own decision point.
      2. the macro block never reaches forward past D-1 into the session
         date it is attached to (the FRED publication-lag boundary).
    """
    as_of = datetime.fromisoformat(row["as_of"])
    max_bar_ts = datetime.fromisoformat(row["max_data_ts"])
    if max_bar_ts > as_of:
        raise FeatureBuildError(
            f"LOOK-AHEAD: bar data at {max_bar_ts} is after decision point "
            f"{as_of} ({row['symbol']} {row['session']} {row['et_time']})")
    d_minus_1 = session_date - timedelta(days=1)
    for sid, s in macro.items():
        if s is None:
            continue
        macro_as_of = date.fromisoformat(s["as_of"])
        if macro_as_of > d_minus_1:
            raise FeatureBuildError(
                f"LOOK-AHEAD: macro series {sid} as_of {macro_as_of} is "
                f"after D-1 ({d_minus_1}) for session {session_date} — the "
                f"D-1 publication-lag join was violated")


def _flatten(session_date: date, dp: str, row: dict, macro: dict,
             missing: list[str]) -> dict:
    flat = {
        "session_date": str(session_date),
        "decision_point": dp,
        "symbol": row["symbol"],
        "as_of": row["as_of"],
        "et_time": row["et_time"],
        "max_data_ts": row["max_data_ts"],
        "regimes": "|".join(row["regimes"]),
        "macro_unavailable": "|".join(missing) if missing else None,
    }
    for f in SNAPSHOT_FIELDS:
        flat[f] = row[f]
    for sid, s in macro.items():
        if s is None:
            flat[f"macro_{sid}_value"] = None
            flat[f"macro_{sid}_as_of"] = None
            flat[f"macro_{sid}_stale_days"] = None
            flat[f"macro_{sid}_pctile_2y"] = None
            flat[f"macro_{sid}_change_20d"] = None
        else:
            flat[f"macro_{sid}_value"] = s["value"]
            flat[f"macro_{sid}_as_of"] = s["as_of"]
            flat[f"macro_{sid}_stale_days"] = s["stale_days"]
            flat[f"macro_{sid}_pctile_2y"] = s["pctile_2y"]
            flat[f"macro_{sid}_change_20d"] = s["change_20d"]
    return flat


def build(symbol: str = SYMBOL, cache: Path = EXT_CACHE) -> tuple[pd.DataFrame, list[dict], list[date]]:
    """Returns (feature table, per-decision-point skip log, sessions with no
    row at all)."""
    rows: list[dict] = []
    skipped: list[dict] = []
    with _redirected_cache(cache):
        sessions = _trading_sessions(symbol, cache)
        for sess in sessions:
            for hhmm in decision_ledger.DECISION_POINTS:
                h, mnt = int(hhmm[:2]), int(hhmm[3:])
                as_of = datetime(sess.year, sess.month, sess.day, h, mnt,
                                  tzinfo=ET).astimezone(timezone.utc)
                try:
                    row = decision_ledger.snapshot(symbol, as_of, source="backfill")
                except decision_ledger.LedgerError as e:
                    skipped.append({"session": str(sess), "decision_point": hhmm,
                                     "reason": str(e)})
                    continue
                macro, missing = macro_block(sess)
                _assert_no_lookahead(row, macro, sess)
                rows.append(_flatten(sess, hhmm, row, macro, missing))
    df = pd.DataFrame(rows)
    produced_sessions = {r["session_date"] for r in rows}
    empty_sessions = [s for s in sessions if str(s) not in produced_sessions]
    return df, skipped, empty_sessions


def _write(df: pd.DataFrame, skipped: list[dict], empty_sessions: list[date],
           symbol: str, out: Path, sidecar: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    null_rates = {c: round(float(df[c].isna().mean()), 4) for c in df.columns}
    meta = {
        "symbol": symbol,
        "rows": len(df),
        "columns": list(df.columns),
        "decision_points": list(decision_ledger.DECISION_POINTS),
        "sessions_covered": int(df["session_date"].nunique()) if len(df) else 0,
        "date_span": [df["session_date"].min(), df["session_date"].max()] if len(df) else None,
        "null_rate_by_column": null_rates,
        "excluded_columns": {
            "headlines_last_hour": "live-only (source=='live' & POLYGON_API_KEY "
                                    "set in decision_ledger.snapshot); historically "
                                    "156/6979 non-null — would ship >99% null"},
        "macro_join": "D-1 (never D) via daytrade/macro_state.py::series_at",
        "decision_point_skips": skipped,
        "sessions_with_no_row": [str(s) for s in empty_sessions],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    sidecar.write_text(json.dumps(meta, indent=1, default=str))
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default=SYMBOL)
    ap.add_argument("--cache", default=str(EXT_CACHE))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--sidecar", default=str(OUT_SIDECAR))
    a = ap.parse_args(argv)

    try:
        df, skipped, empty_sessions = build(a.symbol, Path(a.cache).resolve())
    except (BarDataError, FeatureBuildError, macro_state.MacroError) as e:
        print(f"  REFUSED: {e}")
        return 1

    meta = _write(df, skipped, empty_sessions, a.symbol, Path(a.out).resolve(),
                  Path(a.sidecar).resolve())
    print(f"  {meta['rows']} rows over {meta['sessions_covered']} sessions "
          f"({meta['date_span']})")
    print(f"  {len(skipped)} decision-point skip(s); "
          f"{len(empty_sessions)} session(s) with no row")
    print(f"  wrote {a.out}")
    print(f"  wrote {a.sidecar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
