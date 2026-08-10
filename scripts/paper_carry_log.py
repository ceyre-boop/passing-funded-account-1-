#!/usr/bin/env python3
"""Paper carry log — spec 021 G5. The n>=80 sprint that unlocks the BUY verdict.

Logs paper trades with the SAME sizing, cost, and R conventions as the evaluator:
R is computed from entry/stop/exit exactly as the sealed CSV convention
(pnl fraction / risk fraction), minus the same swap haircut per hold day the
evaluator applies (imported from the firm contract — never re-declared).

Every open/close also goes through sovereign/intelligence/decision_logger per
repo non-negotiable 3. This CLI adds no signal logic: it records what the
existing scan produced.

Usage:
  paper_carry_log.py open  --pair EURUSD=X --direction LONG --entry 1.0850 \
      --stop 1.0790 --risk 0.02 [--date 2026-08-10]
  paper_carry_log.py close --id <trade_id> --exit 1.0930 [--date 2026-08-15] \
      [--reason trailing_stop]
  paper_carry_log.py status
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sovereign.propfirm.firm_contracts import load_contract  # noqa: E402

LOG_PATH = ROOT / "data" / "trade_logs" / "paper_carry_trades.jsonl"
G5_MIN_N = 80  # must match scripts/carry_buy_gate.py


def _read():
    if not LOG_PATH.exists():
        return []
    return [json.loads(line) for line in LOG_PATH.read_text().splitlines() if line.strip()]


def _write(records):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("".join(json.dumps(r) + "\n" for r in records))


def compute_r(direction: str, entry: float, stop: float, exit_: float,
              hold_days: int, swap_haircut_r_per_day: float) -> float:
    """R with the evaluator's exact conventions (spec 021 P1/P4)."""
    risk_frac = abs(entry - stop) / entry
    if risk_frac == 0:
        raise ValueError("entry == stop: risk is zero, R undefined")
    sign = 1.0 if direction == "LONG" else -1.0
    pnl_frac = sign * (exit_ - entry) / entry
    return pnl_frac / risk_frac - swap_haircut_r_per_day * max(hold_days, 1)


def cmd_open(args):
    if args.direction not in ("LONG", "SHORT"):
        raise SystemExit("direction must be LONG or SHORT")
    trade_id = uuid.uuid4().hex[:10]
    rec = dict(id=trade_id, status="open", pair=args.pair, direction=args.direction,
               entry=args.entry, stop=args.stop, risk_pct=args.risk,
               entry_date=args.date, R=None)
    records = _read()
    records.append(rec)
    _write(records)
    from sovereign.intelligence.decision_logger import log_forex_decision
    log_forex_decision(pair=args.pair, direction=args.direction,
                       entry_level=args.entry, stop_loss=args.stop,
                       hold_days=0, risk_pct=args.risk,
                       signal_layers=["paper_carry_sprint_021"],
                       extra=dict(paper_trade_id=trade_id))
    print(f"opened {trade_id}: {args.pair} {args.direction} @ {args.entry} "
          f"stop {args.stop} risk {args.risk:.2%} on {args.date}")


def cmd_close(args):
    records = _read()
    match = [r for r in records if r["id"] == args.id]
    if not match:
        raise SystemExit(f"no open trade with id {args.id!r}")
    rec = match[0]
    if rec["status"] == "closed":
        raise SystemExit(f"trade {args.id} is already closed")
    hold = (date.fromisoformat(args.date) - date.fromisoformat(rec["entry_date"])).days
    if hold < 0:
        raise SystemExit("exit date precedes entry date")
    haircut = load_contract("cti_1step").costs.swap_haircut_r_per_day
    r = compute_r(rec["direction"], rec["entry"], rec["stop"], args.exit,
                  hold, haircut)
    rec.update(status="closed", exit=args.exit, exit_date=args.date,
               hold_days=hold, R=round(r, 6), exit_reason=args.reason)
    _write(records)
    from sovereign.intelligence.decision_logger import update_outcome
    update_outcome(pair=rec["pair"], entry_timestamp=rec["entry_date"],
                   outcome="WIN" if r > 0 else "LOSS", r_realized=r,
                   exit_timestamp=args.date, system="FOREX")
    print(f"closed {args.id}: R={r:+.3f} over {hold}d ({args.reason})")
    cmd_status(args)


def cmd_status(_args):
    records = _read()
    closed = [r for r in records if r["status"] == "closed" and r["R"] is not None]
    n = len(closed)
    line = f"paper sprint: {n}/{G5_MIN_N} closed trades"
    if closed:
        mean_r = sum(r["R"] for r in closed) / n
        line += f", mean R {mean_r:+.3f}"
    open_n = sum(1 for r in records if r["status"] == "open")
    print(line + f", {open_n} open")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("open")
    o.add_argument("--pair", required=True)
    o.add_argument("--direction", required=True)
    o.add_argument("--entry", type=float, required=True)
    o.add_argument("--stop", type=float, required=True)
    o.add_argument("--risk", type=float, required=True)
    o.add_argument("--date", default=date.today().isoformat())
    o.set_defaults(fn=cmd_open)
    c = sub.add_parser("close")
    c.add_argument("--id", required=True)
    c.add_argument("--exit", type=float, required=True)
    c.add_argument("--date", default=date.today().isoformat())
    c.add_argument("--reason", default="manual")
    c.set_defaults(fn=cmd_close)
    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
