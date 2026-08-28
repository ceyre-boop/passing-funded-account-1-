#!/usr/bin/env python3
"""Carry replay — runs the EXISTING paper carry daily cycle day-by-day over a
historical window on backfilled data, to produce a real trade list.

WHY THIS DRIVES THE EXISTING LOOP INSTEAD OF REIMPLEMENTING IT
----------------------------------------------------------------
This repo forbids a second implementation of trading logic (see
`scripts/paper_carry_resolver.py`'s module docstring: "live == backtest by
construction, not by re-implementation"). This driver adds ZERO signal or
exit logic. It calls, in the SAME order `scripts/paper_carry_daily.py::main()`
already uses, once per calendar day in the requested window:

  1. `paper_carry_daily.step_resolve`      — close open paper trades against
     the tape (the same `forex_exit_manager` pure core the live shadow path
     uses).
  2. `paper_carry_daily.step_fill_pending` — turn a prior day's queued signal
     into a real paper trade once its fill bar exists.
  3. `paper_carry_daily.step_scan`         — queue today's fresh signal(s),
     via `carry_scan.scan()`, the real signal source.

ISOLATION FROM THE LIVE LANE (the whole point of this file existing)
------------------------------------------------------------------------
A replay run must be provably unable to touch three live artifacts:
  - `data/trade_logs/paper_carry_trades.jsonl`        (live paper ledger, G5)
  - `data/agent/paper_carry_pending_signals.json`      (live pending queue)
  - the live decision log (`sovereign/intelligence/decision_logger.py`)
and, in addition (not one of the three the task named, but exactly as live
a piece of state), `data/exec/paper_carry_exit_state.json` (live per-trade
trailing-stop/hold-count state).

This driver achieves that by setting four env vars BEFORE importing
`paper_carry_daily` (and, transitively, `paper_carry_log` /
`paper_carry_resolver`) — those three modules read the env vars at their own
import time to compute their module-level path constants, and default to
the exact live path, unchanged, when the var is unset:

  PAPER_CARRY_REPLAY          — set to "1": skips every decision_logger call
                                (`paper_carry_log.py`'s REPLAY_MODE) rather
                                than redirecting them — a replay row can
                                never look like a live decision.
  PAPER_CARRY_REPLAY_WINDOW   — "{start}_{end}", stamped onto every opened
                                row (`replay_window`) alongside `replay: true`.
  PAPER_CARRY_LOG_PATH        — this replay's own output ledger, never the
                                live one.
  PAPER_CARRY_PENDING_PATH    — this replay's own pending-signal queue.
  PAPER_CARRY_EXIT_STATE_PATH — this replay's own exit-state file.

Because of that ordering requirement, this file does its own lightweight
arg-parsing and env setup in `__main__` BEFORE importing
`paper_carry_daily` — see `_run()`.

HONESTY, NOT TUNING
--------------------
This driver does not change any staleness limit, threshold, or signal
parameter. If the window produces few (or zero) trades, that is the answer.
See the printed summary for:
  - `rate_staleness_days` per trade (from `carry_scan.rate_staleness`,
    verbatim — including when it is NEGATIVE, which happens for most of
    this window: the macro cache is a single present-day snapshot, not
    point-in-time vintages, so replaying an early historical date sees
    "staleness" computed against data that did not exist yet at that date.
    That is a look-ahead artifact of the cache, not a sign of a healthy
    input — flagged separately from genuine carried-forward staleness).
  - which pairs actually produced trades (USDJPY is dark for the entire
    window: `JP_cpi` has no cache and no free source).
  - the CB-surprise layer is DISABLED (`entry_engine.CB_LAYER_DISABLED`) —
    this replay does not reproduce the sealed engine's full behaviour.

Usage:
  python3 scripts/carry_replay.py --start 2024-12-03 --end 2026-08-26 \
      --risk 0.005 [--firm cti_1step] [--output PATH] [--ignore-preflight]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", required=True, help="ISO date, inclusive")
    ap.add_argument("--end", required=True, help="ISO date, inclusive")
    ap.add_argument("--risk", type=float, required=True,
                    help="risk fraction sized into every trade opened in "
                         "this replay — forwarded to paper_carry_daily.py "
                         "unchanged, no default invented here either")
    ap.add_argument("--firm", default="cti_1step")
    ap.add_argument("--output", default=None,
                    help="replay ledger path (default: "
                         "data/trade_logs/carry_replay_{start}_{end}.jsonl)")
    ap.add_argument("--pending-path", default=None)
    ap.add_argument("--exit-state-path", default=None)
    ap.add_argument("--ignore-preflight", action="store_true",
                    help="scan even on degraded inputs — inspection only, "
                         "matches paper_carry_daily.py's own flag")
    ap.add_argument("--progress-every", type=int, default=25,
                    help="print a progress line to stderr every N days")
    return ap.parse_args()


def _default_paths(start: str, end: str, args: argparse.Namespace):
    window = f"{start}_{end}"
    output = Path(args.output) if args.output else (
        ROOT / "data" / "trade_logs" / f"carry_replay_{window}.jsonl")
    pending = Path(args.pending_path) if args.pending_path else (
        ROOT / "data" / "agent" / f"carry_replay_pending_{window}.json")
    exit_state = Path(args.exit_state_path) if args.exit_state_path else (
        ROOT / "data" / "exec" / f"carry_replay_exit_state_{window}.json")
    return window, output, pending, exit_state


def _iter_dates(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _max_staleness_for_pair(pair_countries: dict, rate_staleness: dict | None,
                            pair: str) -> int | None:
    """Max staleness across a pair's two legs' *_rates series (the series
    that actually feeds the carry differential — cpi feeds an overlay, not
    the core signal). None if unavailable for either leg."""
    if not rate_staleness:
        return None
    legs = pair_countries.get(pair)
    if not legs:
        return None
    vals = [rate_staleness.get(f"{c}_rates") for c in legs]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def run(args: argparse.Namespace) -> int:
    window, output_path, pending_path, exit_state_path = _default_paths(
        args.start, args.end, args)

    # ---- env vars set BEFORE importing paper_carry_daily (see module
    # docstring) — this is the isolation mechanism, not a formality.
    os.environ["PAPER_CARRY_REPLAY"] = "1"
    os.environ["PAPER_CARRY_REPLAY_WINDOW"] = window
    os.environ["PAPER_CARRY_LOG_PATH"] = str(output_path)
    os.environ["PAPER_CARRY_PENDING_PATH"] = str(pending_path)
    os.environ["PAPER_CARRY_EXIT_STATE_PATH"] = str(exit_state_path)

    import paper_carry_daily as pcd  # noqa: E402 — deliberately deferred
    import carry_scan  # noqa: E402

    assert str(pcd.pcl.LOG_PATH) == str(output_path), \
        "isolation check failed: paper_carry_log did not pick up the replay path"
    assert str(pcd.PENDING_PATH) == str(pending_path), \
        "isolation check failed: paper_carry_daily did not pick up the replay pending path"
    assert str(pcd.pcr.STATE_PATH) == str(exit_state_path), \
        "isolation check failed: paper_carry_resolver did not pick up the replay state path"

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    total_days = (end - start).days + 1

    print(f"=== carry replay {args.start} -> {args.end} ({total_days} days) ===",
          file=sys.stderr)
    print(f"    ledger:     {output_path}", file=sys.stderr)
    print(f"    pending:    {pending_path}", file=sys.stderr)
    print(f"    exit state: {exit_state_path}", file=sys.stderr)

    for i, as_of in enumerate(_iter_dates(start, end), start=1):
        pcd.step_resolve(as_of, args.firm)
        pcd.step_fill_pending(as_of, args.firm, args.risk)
        pcd.step_scan(as_of, args.ignore_preflight)
        if args.progress_every and (i % args.progress_every == 0 or as_of == end):
            print(f"  ... {i}/{total_days} days processed (as-of {as_of})",
                  file=sys.stderr)

    # ---- report: read the replay ledger back (never re-derive R by hand —
    # every closed row's R already came out of paper_carry_log.compute_r()).
    records = _read_jsonl(output_path)
    pair_countries = carry_scan._pair_countries()

    closed = [r for r in records if r.get("status") == "closed"]
    still_open = [r for r in records if r.get("status") == "open"]
    closed.sort(key=lambda r: (r["exit_date"], r["entry_date"]))

    for r in records:
        r["_carried_forward"] = False
        r["_lookahead"] = False
        ms = _max_staleness_for_pair(pair_countries, r.get("rate_staleness_days"), r["pair"])
        r["_max_rate_staleness"] = ms
        if ms is not None:
            if ms > 0:
                r["_carried_forward"] = True
            elif ms < 0:
                r["_lookahead"] = True

    n = len(closed)
    win_n = sum(1 for r in closed if r["R"] > 0)
    mean_r = (sum(r["R"] for r in closed) / n) if n else None
    total_r = sum(r["R"] for r in closed) if n else 0.0

    # max drawdown in R along the closed-trade equity curve, ordered by exit date
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for r in closed:
        cum += r["R"]
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    per_pair = {}
    for r in closed:
        per_pair[r["pair"]] = per_pair.get(r["pair"], 0) + 1

    carried_forward_n = sum(1 for r in records if r["_carried_forward"])
    lookahead_n = sum(1 for r in records if r["_lookahead"])

    summary = dict(
        window=window, risk=args.risk, firm=args.firm,
        total_days=total_days,
        trade_count_closed=n, trade_count_still_open=len(still_open),
        win_rate=(win_n / n) if n else None,
        mean_r=mean_r, total_r=total_r, max_drawdown_r=max_dd,
        per_pair_closed=per_pair,
        carried_forward_rate_data_count=carried_forward_n,
        lookahead_rate_data_count=lookahead_n,
        ledger_path=str(output_path),
    )

    print("\n=== TRADE TABLE (closed) ===")
    header = (f"{'pair':<10}{'dir':<6}{'entry_date':<12}{'entry':<10}"
              f"{'exit_date':<12}{'exit':<10}{'R':>8}  {'hold':>4}  "
              f"{'reason':<16}{'staleness':>10}")
    print(header)
    for r in closed[:40]:
        print(f"{r['pair']:<10}{r['direction']:<6}{r['entry_date']:<12}"
              f"{r['entry']:<10.5f}{r['exit_date']:<12}{r['exit']:<10.5f}"
              f"{r['R']:>8.3f}  {r['hold_days']:>4}  {r['exit_reason']:<16}"
              f"{str(r['_max_rate_staleness']):>10}")
    if len(closed) > 40:
        print(f"... {len(closed) - 40} more closed trades (see {output_path})")
    if still_open:
        print(f"\n{len(still_open)} trade(s) still OPEN at window end "
              f"(unresolved, not counted in summary stats):")
        for r in still_open:
            print(f"  {r['pair']} {r['direction']} entry {r['entry_date']} @ {r['entry']}")

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, default=str))

    summary_path = output_path.with_suffix(output_path.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nsummary written to {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(run(parse_args()))
