#!/usr/bin/env python3
"""Paper carry daily cycle — the loop that fills `paper_carry_trades.jsonl`.

Run once a day (see `scripts/paper_carry_daily_tick.sh` and
`scheduling/com.alta.paper-carry-daily.plist.template` — NOT loaded by this
repo, install by hand). Three steps,
using ONLY tools this repo already built and validated:

  1. RESOLVE  — `scripts/paper_carry_resolver.py` steps every OPEN paper
     trade forward one bar against the real tape (reusing the exit_machine
     core `sovereign/execution/forex_exit_manager.py` already extracted) and
     closes the ones whose exit condition fired.
  2. FILL PENDING — a signal detected on a PRIOR run has no fill until the
     next session's open exists. Once it does (this run, one day later),
     open the paper trade for real via `scripts/paper_carry_log.py open` —
     same reconciliation, same decision-logger call the G5 sprint already
     uses. `data/agent/paper_carry_pending_signals.json` is the only state
     this step owns.
  3. SCAN — `scripts/carry_scan.py`'s preflight + scan for TODAY. A fresh
     signal is queued as pending (not opened yet — the fill doesn't exist),
     UNLESS a pending or already-open trade exists for the same pair.

REQUIRED SIZE INPUT — never invented here
------------------------------------------
`eval_size()` does not exist yet (a concurrent effort, `scripts/ruin_engine.py`,
is deriving it). This script REQUIRES `--risk` on every invocation and uses it
for every trade opened in that run. It is recorded on the row
(`risk_pct`) precisely so the record stays honest about what size actually
produced it, re-analyzable once the real rule lands. This script does not
select a default, and will not run without it.

WHAT GETS ATTACHED TO EVERY OPENED ROW
-----------------------------------------
- `gate_state`: a snapshot of `data/agent/carry_buy_gate_state.json` at open
  time (None if that file does not exist — never fabricated).
- `rate_staleness_days`: `carry_scan.rate_staleness(as_of)` at the SIGNAL
  bar's date — spec 039's look-ahead finding means this is the honest
  provenance a later analysis needs to separate "the edge failed" from "the
  inputs were stale."
- `paper: true` (set by `paper_carry_log.py` itself) — never confusable with
  a real trade.

WHAT THIS SCRIPT NEVER DOES
------------------------------
- Never fabricates a fill. Pending signals stay pending until `_next_open()`
  returns a real price.
- Never opens a second position for a pair that already has an open or
  pending one (one paper position per pair at a time — matches the sealed
  record's own non-overlap; carry_scan.PAIR_VIX_GATES etc. are per-pair).
- Never touches a broker. Nothing on this path can reach one.
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

import carry_scan  # noqa: E402
import paper_carry_log as pcl  # noqa: E402
import paper_carry_resolver as pcr  # noqa: E402

# PAPER_CARRY_PENDING_PATH — replay isolation (see scripts/carry_replay.py and
# scripts/paper_carry_log.py's PAPER_CARRY_LOG_PATH docstring). Defaults to
# the live path unchanged when unset.
_DEFAULT_PENDING_PATH = ROOT / "data" / "agent" / "paper_carry_pending_signals.json"
PENDING_PATH = Path(os.environ["PAPER_CARRY_PENDING_PATH"]) if os.environ.get(
    "PAPER_CARRY_PENDING_PATH") else _DEFAULT_PENDING_PATH
GATE_STATE_PATH = ROOT / "data" / "agent" / "carry_buy_gate_state.json"


def _load_pending(path: Path = PENDING_PATH) -> list:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _save_pending(pending: list, path: Path = PENDING_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending, indent=2))


def _gate_state_snapshot() -> dict | None:
    if not GATE_STATE_PATH.exists():
        return None
    try:
        return json.loads(GATE_STATE_PATH.read_text())
    except Exception:
        return None


def _pairs_with_open_or_pending(pending: list) -> set:
    open_pairs = {r["pair"] for r in pcl._read() if r.get("status") == "open"}
    pending_pairs = {p["pair"] for p in pending}
    return open_pairs | pending_pairs


def step_resolve(as_of: date, firm: str) -> pcr.ResolveOutcome:
    return pcr.resolve(as_of, firm=firm)


def step_fill_pending(as_of: date, firm: str, risk: float) -> list:
    """Convert pending signals whose fill now exists into real paper trades."""
    pending = _load_pending()
    still_pending, opened = [], []
    for sig in pending:
        signal_date = date.fromisoformat(sig["signal_bar"])
        fill, fill_bar = carry_scan._next_open(sig["pair"], signal_date)
        if fill is None:
            still_pending.append(sig)
            continue
        sgn = -1 if sig["direction"] == "LONG" else 1
        stop = fill * (1 + sgn * sig["stop_frac"])
        from paper_carry_log import usd_risk_per_unit
        from sovereign.propfirm.firm_contracts import load_contract
        acct = load_contract(firm).account_size
        qty = (risk * acct) / usd_risk_per_unit(sig["pair"], fill, stop)

        args = type("Args", (), dict(
            pair=sig["pair"], direction=sig["direction"], entry=fill, stop=stop,
            risk=risk, qty=qty, mechanisms=None, firm=firm,
            date=str(fill_bar), rate_staleness_days=sig.get("rate_staleness_days"),
            gate_state=sig.get("gate_state"),
        ))()
        pcl.cmd_open(args)
        opened.append(dict(pair=sig["pair"], direction=sig["direction"], entry=fill,
                           stop=stop, qty=qty, fill_bar=str(fill_bar)))
    _save_pending(still_pending)
    return opened


def step_scan(as_of: date, ignore_preflight: bool = False) -> tuple[list, list]:
    """Queue today's fresh signals as pending. Refuses to scan on degraded
    inputs (preflight failure) unless explicitly told to inspect only —
    matches carry_scan.py's own CLI discipline.

    A preflight problem only blocks the pair(s) it actually applies to
    (e.g. JP_cpi blocks USDJPY=X, not EURUSD=X/GBPUSD=X/AUDUSD=X) — see
    carry_scan.preflight_by_pair(). Only when EVERY sealed pair is blocked
    does this fall back to the old all-or-nothing refusal."""
    problems = carry_scan.preflight(as_of)
    blocked = carry_scan.preflight_by_pair(as_of, problems)
    clear_pairs = [pair for pair, reasons in blocked.items() if not reasons]
    dark_pairs = [pair for pair, reasons in blocked.items() if reasons]

    if problems and not clear_pairs and not ignore_preflight:
        return [], problems

    pair_notes = []
    if problems:
        if ignore_preflight:
            pair_notes.append("--ignore-preflight set: scanning ALL pairs "
                              "including DARK ones — NOT tradeable")
            pair_notes.extend(problems)
        else:
            pair_notes.append(f"preflight: {len(clear_pairs)}/{len(blocked)} "
                              f"pairs clear — {', '.join(clear_pairs) or '(none)'}")
            for pair in dark_pairs:
                pair_notes.append(f"preflight: {pair} DARK — "
                                  f"{'; '.join(blocked[pair])}")

    pending = _load_pending()
    busy = _pairs_with_open_or_pending(pending)
    scan_pairs = None if (ignore_preflight or not problems) else clear_pairs
    hits, notes, _pairs = carry_scan.scan(as_of, only_pairs=scan_pairs)
    staleness = carry_scan.rate_staleness(as_of)
    gate_state = _gate_state_snapshot()

    queued = []
    for h in hits:
        if h["pair"] in busy:
            notes.append(f"{h['pair']}: signal fired but a position is already "
                        f"open/pending for this pair — skipped, not stacked")
            continue
        pending.append(dict(pair=h["pair"], direction=h["direction"],
                            signal_bar=str(h["signal_bar"]), stop_frac=h["stop_frac"],
                            hold=h["hold"], rate_staleness_days=staleness,
                            gate_state=gate_state))
        queued.append(h["pair"])
    _save_pending(pending)
    return queued, pair_notes + notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--risk", type=float, required=True,
                    help="risk fraction sized into any trade OPENED this run. "
                         "REQUIRED — no default; eval_size() does not exist yet.")
    ap.add_argument("--firm", default="cti_1step")
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--ignore-preflight", action="store_true",
                    help="scan even on degraded inputs — for inspection only, "
                         "never for a real scheduled run")
    a = ap.parse_args()
    as_of = date.fromisoformat(a.as_of)

    print(f"\n=== paper carry daily cycle — {as_of} ===")

    print("\n[1/3] resolve open positions against the tape")
    out = step_resolve(as_of, a.firm)
    print(f"  closed {len(out.closed)}, held {len(out.held)}, skipped {len(out.skipped)}")
    for c in out.closed:
        print(f"    CLOSED {c['id']} {c['pair']} @ {c['exit']:.5f} ({c['reason']})")
    for s in out.skipped:
        print(f"    ! {s['id']} {s['pair']}: {s['reason']}")

    print("\n[2/3] fill pending signals whose fill now exists")
    opened = step_fill_pending(as_of, a.firm, a.risk)
    for o in opened:
        print(f"    OPENED {o['pair']} {o['direction']} @ {o['entry']:.5f} "
              f"stop {o['stop']:.5f} qty {o['qty']:.0f}")
    if not opened:
        print("    none")

    print("\n[3/3] scan today for new signals")
    queued, notes = step_scan(as_of, a.ignore_preflight)
    for n in notes:
        print(f"    ! {n}")
    if queued:
        print(f"    queued as pending: {', '.join(queued)}")
    else:
        print("    no new signal today")

    return 0


if __name__ == "__main__":
    sys.exit(main())
