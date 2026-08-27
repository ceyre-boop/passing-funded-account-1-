#!/usr/bin/env python3
"""Paper carry resolver — closes open `paper_carry_trades.jsonl` rows against
the real tape, using the SAME exit decision the sealed backtest and the L2
live exit manager use. Part of the loop that turns n=411 (sealed, frozen)
into n growing (paper, live).

WHY THIS REUSES `sovereign/execution/forex_exit_manager.py` INSTEAD OF
REIMPLEMENTING THE EXIT RULES
------------------------------------------------------------------------
`forex_exit_manager.py` already extracted the exit decision
(`sovereign/forex/exit_machine.decide_exit`) into a broker-free pure core —
`TradeState` / `MarketBar` / `step_trade` / `cfg_for_pair` — specifically so
"live == backtest by construction, not by re-implementation." That file's
`run_daily()` wraps the pure core around an OANDA `bridge`; this module
wraps the SAME pure core around the paper ledger instead. No exit rule is
re-typed here — only the state source (paper ledger vs. broker) differs.

State is persisted SEPARATELY from the live shadow file
(`data/exec/exit_manager_state.json`) at `data/exec/paper_carry_exit_state.json`
— this resolver must never read or write the live L2 state.

FAIL LOUD ON A CORRUPT RECORD
------------------------------
A malformed open record (missing a required field, entry==stop, an
unparseable date, an unrecognized direction) makes `resolve()` raise
`ResolverError` immediately. It does NOT get silently skipped — a skip here
would look identical to "nothing to resolve today," which is exactly the
failure mode CLAUDE.md rule 3 (an unlogged trade is silent data loss) exists
to prevent on the write side, and its mirror on the read side.

A record this module simply CANNOT price today (market data unavailable) IS
skipped, with a printed reason — that is a real, distinguishable outcome
from corruption, and fabricating a fill for it would be worse than skipping.

Usage:
  python3 scripts/paper_carry_resolver.py [--as-of 2026-08-26] [--firm cti_1step]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from sovereign.execution.forex_exit_manager import (  # noqa: E402
    Action, MarketBar, TradeState, cfg_for_pair, step_trade,
)

import carry_scan  # noqa: E402
import paper_carry_log as pcl  # noqa: E402

# PAPER_CARRY_EXIT_STATE_PATH — replay isolation (see scripts/carry_replay.py
# and paper_carry_log.py's PAPER_CARRY_LOG_PATH docstring). This state file
# (per-trade trailing-stop/hold-count state between daily runs) is as much a
# live artifact as the ledger it resolves against — a replay run must never
# read or write the live file. Defaults to the live path unchanged when unset.
_DEFAULT_STATE_PATH = ROOT / "data" / "exec" / "paper_carry_exit_state.json"
STATE_PATH = Path(os.environ["PAPER_CARRY_EXIT_STATE_PATH"]) if os.environ.get(
    "PAPER_CARRY_EXIT_STATE_PATH") else _DEFAULT_STATE_PATH
STATE_VERSION = 1

_REQUIRED_FIELDS = ("id", "pair", "direction", "entry", "stop", "entry_date", "status")


class ResolverError(RuntimeError):
    """A ledger record is malformed. Raised, never downgraded to a skip."""


# ------------------------------------------------------------------ validation

def validate_open_record(rec: dict) -> None:
    """Everything the resolver needs to trust before it touches the tape.
    Raises ResolverError with the specific defect — never returns a partial
    or best-guess reading of a bad record."""
    missing = [f for f in _REQUIRED_FIELDS if f not in rec]
    if missing:
        raise ResolverError(f"record {rec.get('id', '<no id>')!r} missing fields: {missing}")
    if rec["direction"] not in ("LONG", "SHORT"):
        raise ResolverError(f"record {rec['id']!r}: direction must be LONG or SHORT, "
                            f"got {rec['direction']!r}")
    for f in ("entry", "stop"):
        v = rec[f]
        if not isinstance(v, (int, float)) or v != v:  # NaN check via v != v
            raise ResolverError(f"record {rec['id']!r}: {f}={v!r} is not a finite number")
    if float(rec["entry"]) == float(rec["stop"]):
        raise ResolverError(f"record {rec['id']!r}: entry == stop ({rec['entry']}) — "
                            f"risk is zero, R would be undefined at close")
    try:
        date.fromisoformat(rec["entry_date"])
    except (TypeError, ValueError) as e:
        raise ResolverError(f"record {rec['id']!r}: entry_date {rec.get('entry_date')!r} "
                            f"is not a valid ISO date ({e})")


# ------------------------------------------------------------------ pair mapping

def _to_oanda_style(pair: str) -> str:
    """'EURUSD=X' -> 'EUR_USD', matching cfg_for_pair's PAIR_TRAILING_OVERRIDES keys."""
    sym = pair.replace("=X", "").upper()
    if len(sym) != 6:
        raise ResolverError(f"cannot map {pair!r} to an OANDA-style pair (expected 6 letters)")
    return f"{sym[:3]}_{sym[3:]}"


# ------------------------------------------------------------------ state I/O

def _load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"version": STATE_VERSION, "trades": {}, "processed": {}}
    return json.loads(path.read_text())


def _save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


# ------------------------------------------------------------------ resolve

@dataclass
class ResolveOutcome:
    closed: list      # list of {id, pair, R, reason}
    held: list         # list of {id, pair, action}
    skipped: list       # list of {id, pair, reason} — data unavailable, not corruption


def resolve(as_of: Optional[date] = None, *, firm: str = "cti_1step",  # noqa: ARG001 —
            state_path: Path = STATE_PATH) -> ResolveOutcome:
    # `firm` is accepted for interface symmetry with the rest of this repo's
    # firm-parametrized tools; `pcl.cmd_close()` currently hardcodes
    # "cti_1step" for the swap haircut (a pre-existing quirk, not introduced
    # here) so this parameter is not yet forwarded anywhere.
    """Step every OPEN paper record forward one bar against the real tape.
    Raises ResolverError on any corrupt record (see module docstring) BEFORE
    fetching any market data for that record — a corruption is a property of
    the record, not of the day's data availability.

    Reads/writes through `paper_carry_log.LOG_PATH` (module-level, monkeypatch
    that to isolate a test) so `pcl.cmd_close()` — which reuses R computation
    and the decision-logger write — closes the SAME file this resolves."""
    as_of = as_of or date.today()
    records = pcl._read()
    open_recs = [r for r in records if r.get("status") == "open"]
    for r in open_recs:
        validate_open_record(r)

    state = _load_state(state_path)
    trades_state = state.setdefault("trades", {})
    processed = state.setdefault("processed", {})

    bt = carry_scan._make_backtester(as_of)
    outcome = ResolveOutcome(closed=[], held=[], skipped=[])

    for rec in open_recs:
        tid = rec["id"]
        try:
            bar_raw, note = carry_scan.pair_bar(bt, rec["pair"])
        except Exception as exc:                       # network/data failure — skip, don't fabricate
            outcome.skipped.append(dict(id=tid, pair=rec["pair"],
                                        reason=f"market data unavailable: {exc!r}"))
            continue
        if bar_raw is None:
            outcome.skipped.append(dict(id=tid, pair=rec["pair"], reason=note))
            continue

        bar = MarketBar(pair=rec["pair"], date=str(bar_raw["bar_date"]), close=bar_raw["close"],
                        atr_pct=bar_raw["atr_pct"], signal=int(round(bar_raw["signal"])),
                        hold_today=bar_raw["hold_days"])

        if processed.get(tid) == bar.date:
            outcome.held.append(dict(id=tid, pair=rec["pair"], action="SKIP_DUPLICATE"))
            continue

        direction = 1 if rec["direction"] == "LONG" else -1
        if tid not in trades_state:
            # Seed state from the REAL recorded entry/stop — never re-derive the
            # stop from today's ATR (that would silently substitute a different
            # stop than the one actually written at open). hold_limit uses the
            # canonical v015 hold (60d, PAIR_HOLD_OVERRIDES is empty) — the same
            # constant forex_exit_manager.HOLD_DAYS uses, not a guess.
            from sovereign.execution.forex_exit_manager import HOLD_DAYS
            ts = TradeState(trade_id=tid, pair=rec["pair"], direction=direction,
                            entry_price=float(rec["entry"]), entry_date=rec["entry_date"],
                            stop_price=float(rec["stop"]), last_stop=float(rec["stop"]),
                            best_price=float(rec["entry"]), worst_price=float(rec["entry"]),
                            hold_count=0, hold_limit=HOLD_DAYS)
        else:
            ts = TradeState.from_dict(trades_state[tid])

        cfg = cfg_for_pair(_to_oanda_style(rec["pair"]))
        res = step_trade(ts, bar, cfg)
        processed[tid] = bar.date

        if res.action == Action.CLOSE:
            reason = res.decision.name.lower()
            close_args = type("Args", (), dict(id=tid, exit=bar.close,
                                               date=bar.date, reason=reason))()
            pcl.cmd_close(close_args)
            outcome.closed.append(dict(id=tid, pair=rec["pair"], exit=bar.close, reason=reason))
            trades_state.pop(tid, None)
        else:
            trades_state[tid] = res.new_state.to_dict()
            outcome.held.append(dict(id=tid, pair=rec["pair"], action=res.action.value))

    _save_state(state, state_path)
    return outcome


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--firm", default="cti_1step")
    a = ap.parse_args()
    out = resolve(date.fromisoformat(a.as_of), firm=a.firm)
    print(f"resolver as of {a.as_of}: {len(out.closed)} closed, "
          f"{len(out.held)} held, {len(out.skipped)} skipped")
    for c in out.closed:
        print(f"  CLOSED {c['id']} {c['pair']} @ {c['exit']:.5f} ({c['reason']})")
    for s in out.skipped:
        print(f"  ! skipped {s['id']} {s['pair']}: {s['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
