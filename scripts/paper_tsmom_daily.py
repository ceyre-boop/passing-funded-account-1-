#!/usr/bin/env python3
"""Paper TSMOM daily cycle — the loop that fills `paper_tsmom_trades.jsonl`.

One tick per day (see `scripts/paper_tsmom_daily_tick.sh` and
`scheduling/com.alta.paper-tsmom-daily.plist.template` — NOT installed by
this repo). Mirrors `scripts/paper_carry_daily.py`'s conventions but the
signal source is `sovereign/trend/tsmom_engine.py::decide()` — the ONE
implementation of the TSMOM rule (`sovereign/trend/SPEC.md`). This script
re-implements none of it: it reads bars, calls `decide()`, and reconciles
the result against the open-position ledger.

STEPS EACH TICK
----------------
1. REFRESH BARS — `daytrade/fx_state.py::fetch_bars(period="1mo")` for the
   four primary pairs (data/carry/bars/*.parquet — shared with the carry
   loop, frozen-history-only append).
2. DECIDE — `tsmom_engine.decide(closes, as_of)` per pair.
3. RECONCILE — the spec's three position-change events, applied against
   whatever this ledger currently holds for that pair:
     - flat + signal != 0            -> OPEN
     - held sign != new sign, new!=0 -> CLOSE (signal_flip) then OPEN
     - signal == 0                   -> CLOSE (flat)
     - same sign, RESIZE_DAY, and
       |notional_frac - held| > 0.10 -> CLOSE (resize) then OPEN
     - otherwise                     -> HOLD (nothing written)
4. R on close uses the spec's convention (module docstring below,
   `_compute_r()`), with the swap haircut read from the firm contract
   (`sovereign.propfirm.firm_contracts.load_contract`) exactly as
   `paper_carry_log.py::cmd_close` does — never re-declared here.

REPLAY / PATH ISOLATION — mirrors paper_carry_log.py exactly
--------------------------------------------------------------
  PAPER_TSMOM_LOG_PATH  — overrides the ledger path (default: unchanged,
                          the live data/trade_logs/paper_tsmom_trades.jsonl).
  PAPER_CARRY_REPLAY    — when set (any truthy value), open/close skip the
                          sovereign/intelligence/decision_logger calls
                          entirely and every written row is tagged
                          `replay: true`. Same env var `paper_carry_log.py`
                          already uses — one replay switch for the whole
                          paper-loop family, not two to keep in sync.

IDEMPOTENCY
------------
Keyed on (pair, as_of): a pair that already has an open row with
`entry_date == as_of`, or a closed row with `exit_date == as_of`, is treated
as already handled this tick and reported HOLD — running the tick twice for
the same day never double-opens or double-closes.

WHAT THIS SCRIPT NEVER DOES
------------------------------
- Never invents a stop. TSMOM has none; `stop` is always null on this
  ledger (spec: "There is NO stop loss. The signal flip is the exit.").
- Never defaults a missing bar or a `decide()` failure to zero/flat for a
  pair. It prints the failure loudly, skips that pair, and continues.
- Never re-implements any part of the TSMOM rule. `decide()` is the only
  source of `signal` / `notional_frac` / `realized_vol`.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "daytrade"))

import fx_state as fx  # noqa: E402 — daytrade/fx_state.py, per its own import style
from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402
from sovereign.trend.tsmom_engine import (  # noqa: E402
    LOOKBACK, VOL_WINDOW, VOL_TARGET, MAX_NOTIONAL, decide,
)

# The pre-registered PRIMARY universe (sovereign/trend/SPEC.md) — the four
# pairs the firm contract can actually trade. Read from fx_state.PAIRS
# rather than hardcoded a second time, matching the carry lane's own rule
# (test_carry_scan.py::test_universe_comes_from_the_sealed_record).
PRIMARY_PAIRS = list(fx.PAIRS.keys())

# One-way spread in price units, charged on both entry and exit (spec's
# "Costs" section — never re-declared anywhere else in this repo).
SPREADS = {
    "EURUSD=X": 0.00010,
    "GBPUSD=X": 0.00015,
    "USDJPY=X": 0.010,
    "AUDUSD=X": 0.00012,
}

RISK_PCT = VOL_TARGET / math.sqrt(252)  # one R, in fraction-of-equity terms

_DEFAULT_LOG_PATH = ROOT / "data" / "trade_logs" / "paper_tsmom_trades.jsonl"
LOG_PATH = Path(os.environ["PAPER_TSMOM_LOG_PATH"]) if os.environ.get(
    "PAPER_TSMOM_LOG_PATH") else _DEFAULT_LOG_PATH
# Same replay switch paper_carry_log.py already uses — one family, one flag.
REPLAY_MODE = bool(os.environ.get("PAPER_CARRY_REPLAY"))


def _read(path: Path = None) -> list:
    path = path or LOG_PATH
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write(records: list, path: Path = None) -> None:
    path = path or LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _direction(signal: int) -> str:
    if signal == 1:
        return "LONG"
    if signal == -1:
        return "SHORT"
    raise ValueError(f"direction undefined for signal={signal!r}")


def _sign(direction: str) -> int:
    return 1 if direction == "LONG" else -1


def _is_resize_day(as_of: date) -> bool:
    """RESIZE_DAY per spec: "Monday first trading day of each week". This
    repo has no trading-calendar/holiday table for FX majors (they trade
    Sun evening -> Fri close with no mid-week holidays that matter to this
    universe), so Monday itself is the first trading day of every week
    here. Judgment call — see task report."""
    return as_of.weekday() == 0  # Monday


def _already_handled(records: list, pair: str, as_of_str: str) -> bool:
    for r in records:
        if r["pair"] != pair:
            continue
        if r.get("entry_date") == as_of_str or r.get("exit_date") == as_of_str:
            return True
    return False


def _open_position(records: list, pair: str):
    for r in records:
        if r["pair"] == pair and r["status"] == "open":
            return r
    return None


def _compute_pnl_frac(direction: str, entry: float, exit_: float,
                       spread: float) -> float:
    """Directional return minus the round-trip spread, charged on BOTH the
    open and the close (spec's "Costs" section: "spread ... charged" "on
    every notional change"). Each leg's cost is that leg's own spread
    divided by that leg's own price -- an entry-price-denominated cost on
    open, an exit-price-denominated cost on close."""
    sign = _sign(direction)
    raw = sign * (exit_ - entry) / entry
    cost = spread / entry + spread / exit_
    return raw - cost


def _compute_r(direction: str, entry: float, exit_: float, hold_days: int,
               spread: float, swap_haircut_r_per_day: float) -> float:
    """R per sovereign/trend/SPEC.md's "R convention":
    R = pnl_frac / (VOL_TARGET / sqrt(252)) - haircut * hold_days
    matching the shape of paper_carry_log.py::compute_r."""
    pnl_frac = _compute_pnl_frac(direction, entry, exit_, spread)
    return pnl_frac / RISK_PCT - swap_haircut_r_per_day * max(hold_days, 1)


def _log_open(pair: str, direction: str, entry: float, as_of_str: str,
              notional_frac: float, realized_vol: float) -> dict:
    trade_id = uuid.uuid4().hex[:10]
    rec = dict(id=trade_id, status="open", pair=pair, direction=direction,
               entry=entry, stop=None, risk_pct=RISK_PCT, qty=None,
               entry_date=as_of_str, R=None, paper=True, strategy="tsmom",
               notional_frac=notional_frac, realized_vol=realized_vol)
    if REPLAY_MODE:
        rec["replay"] = True
    return rec


def _log_close(rec: dict, exit_: float, as_of_str: str, exit_reason: str,
               spread: float, swap_haircut_r_per_day: float) -> dict:
    hold_days = (date.fromisoformat(as_of_str) - date.fromisoformat(rec["entry_date"])).days
    if hold_days < 0:
        raise ValueError(f"{rec['pair']}: exit date {as_of_str} precedes "
                          f"entry date {rec['entry_date']}")
    r = _compute_r(rec["direction"], rec["entry"], exit_, hold_days, spread,
                   swap_haircut_r_per_day)
    rec.update(status="closed", exit=exit_, exit_date=as_of_str,
               hold_days=hold_days, R=round(r, 6), exit_reason=exit_reason)
    return rec


def _decision_log_open(pair: str, direction: str, entry: float,
                        notional_frac: float, realized_vol: float,
                        trade_id: str) -> None:
    if REPLAY_MODE:
        return
    from sovereign.intelligence.decision_logger import log_forex_decision
    log_forex_decision(pair=pair, direction=direction, entry_level=entry,
                       stop_loss=None, hold_days=0, risk_pct=RISK_PCT,
                       signal_layers=["paper_tsmom_daily"],
                       extra=dict(paper_trade_id=trade_id,
                                  notional_frac=notional_frac,
                                  realized_vol=realized_vol,
                                  strategy="tsmom"))


def _decision_log_close(pair: str, entry_date: str, r: float,
                         exit_date: str) -> None:
    if REPLAY_MODE:
        return
    from sovereign.intelligence.decision_logger import update_outcome
    update_outcome(pair=pair, entry_timestamp=entry_date,
                   outcome="WIN" if r > 0 else "LOSS", r_realized=r,
                   exit_timestamp=exit_date, system="FOREX")


def _load_closes(pair: str):
    """Close series for one pair, from the shared carry bars parquet."""
    import pandas as pd
    path = fx.BARS / f"{pair.replace('=', '_')}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"no bars for {pair}: {path}")
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    closes = df["Close"]
    if isinstance(closes, pd.DataFrame):  # MultiIndex-column leftovers
        closes = closes.iloc[:, 0]
    return closes


def run_tick(as_of: date, firm: str = "cti_1step", pairs: list = None) -> list:
    """One tick. Returns a list of per-pair result dicts for printing."""
    pairs = pairs or PRIMARY_PAIRS
    as_of_str = as_of.isoformat()
    haircut = load_contract(firm).costs.swap_haircut_r_per_day
    resize_day = _is_resize_day(as_of)

    print(f"\n=== paper tsmom daily cycle — {as_of_str} ===")
    print("\n[1/2] refresh bars")
    try:
        fx.fetch_bars(period="1mo")
    except Exception as exc:  # network/data failure — never silently skip everything
        print(f"  !! fetch_bars failed: {exc} — continuing with cached bars")

    results = []
    print("\n[2/2] decide + reconcile per pair")
    for pair in pairs:
        try:
            result = _tick_one_pair(pair, as_of, as_of_str, resize_day,
                                    haircut, firm)
        except Exception as exc:
            print(f"  !! {pair}: {exc} — skipped")
            results.append(dict(pair=pair, action="ERROR", detail=str(exc)))
            continue
        results.append(result)
        _print_result(result)
    return results


def _print_result(result: dict) -> None:
    pair = result["pair"]
    action = result["action"]
    if action == "HOLD":
        print(f"    {pair}: HOLD ({result.get('detail', '')})")
    elif action == "OPEN":
        print(f"    {pair}: OPEN {result['direction']} @ {result['entry']:.5f} "
              f"notional_frac {result['notional_frac']:.4f} "
              f"vol {result['realized_vol']:.4f}")
    elif action == "CLOSE":
        print(f"    {pair}: CLOSE @ {result['exit']:.5f} R={result['R']:+.4f} "
              f"({result['exit_reason']})")
    elif action == "FLIP":
        print(f"    {pair}: FLIP close R={result['close']['R']:+.4f} -> "
              f"OPEN {result['open']['direction']} @ {result['open']['entry']:.5f}")


def _tick_one_pair(pair: str, as_of: date, as_of_str: str, resize_day: bool,
                   haircut: float, firm: str) -> dict:
    closes = _load_closes(pair)
    dec = decide(closes, as_of)
    entry = _price_at(closes, as_of)
    spread = SPREADS[pair]

    records = _read()
    if _already_handled(records, pair, as_of_str):
        return dict(pair=pair, action="HOLD", detail="already handled today")

    held = _open_position(records, pair)

    if held is None:
        if dec.signal == 0:
            return dict(pair=pair, action="HOLD", detail="flat, no signal")
        direction = _direction(dec.signal)
        rec = _log_open(pair, direction, entry, as_of_str, dec.notional_frac,
                        dec.realized_vol)
        records.append(rec)
        _write(records)
        _decision_log_open(pair, direction, entry, dec.notional_frac,
                           dec.realized_vol, rec["id"])
        return dict(pair=pair, action="OPEN", direction=direction, entry=entry,
                    notional_frac=dec.notional_frac, realized_vol=dec.realized_vol)

    held_sign = _sign(held["direction"])

    if dec.signal == 0:
        _log_close(held, entry, as_of_str, "flat", spread, haircut)
        _write(records)
        _decision_log_close(pair, held["entry_date"], held["R"], as_of_str)
        return dict(pair=pair, action="CLOSE", exit=entry, R=held["R"],
                    exit_reason="flat")

    if dec.signal != held_sign:
        _log_close(held, entry, as_of_str, "signal_flip", spread, haircut)
        _decision_log_close(pair, held["entry_date"], held["R"], as_of_str)
        direction = _direction(dec.signal)
        new_rec = _log_open(pair, direction, entry, as_of_str, dec.notional_frac,
                            dec.realized_vol)
        records.append(new_rec)
        _write(records)
        _decision_log_open(pair, direction, entry, dec.notional_frac,
                           dec.realized_vol, new_rec["id"])
        return dict(pair=pair, action="FLIP",
                    close=dict(R=held["R"], exit_reason="signal_flip"),
                    open=dict(direction=direction, entry=entry))

    # Same sign: resize check.
    if resize_day and abs(dec.notional_frac - held["notional_frac"]) > 0.10:
        _log_close(held, entry, as_of_str, "resize", spread, haircut)
        _decision_log_close(pair, held["entry_date"], held["R"], as_of_str)
        direction = held["direction"]
        new_rec = _log_open(pair, direction, entry, as_of_str, dec.notional_frac,
                            dec.realized_vol)
        records.append(new_rec)
        _write(records)
        _decision_log_open(pair, direction, entry, dec.notional_frac,
                           dec.realized_vol, new_rec["id"])
        return dict(pair=pair, action="FLIP",
                    close=dict(R=held["R"], exit_reason="resize"),
                    open=dict(direction=direction, entry=entry))

    return dict(pair=pair, action="HOLD", detail="same sign, no resize trigger")


def _price_at(closes, as_of: date) -> float:
    """The close ON as_of. Never falls back to the most recent prior bar
    silently — a missing bar for as_of is a loud failure, not a stale fill."""
    import pandas as pd
    idx = pd.Timestamp(as_of)
    if idx not in closes.index:
        raise KeyError(f"no close bar for {as_of.isoformat()}")
    return float(closes.loc[idx])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--firm", default="cti_1step")
    ap.add_argument("--as-of", default=date.today().isoformat())
    a = ap.parse_args()
    as_of = date.fromisoformat(a.as_of)
    run_tick(as_of, firm=a.firm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
