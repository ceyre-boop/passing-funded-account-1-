#!/usr/bin/env python3
"""Cockpit snapshot writer — refreshes the embedded data block in docs/cockpit.html.

The cockpit page prefers a live docs/data.json when it is served next to one
(GitHub Pages, or `python3 -m http.server` from docs/). The embedded snapshot is
the fallback for when the page travels on its own — a published artifact, a file
opened straight off disk. Without a refresher the fallback silently rots, which
is the same failure as any other stale number in this repo.

Stdlib only, no operator imports — the dashboard lane must never be able to
steer anything (same rule as build_dashboard_data.py).

Absent inputs become an honest absence in the snapshot, never a fabricated
number: a missing decision ledger yields an empty closed_trades list, not an
invented trade.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data.json"
PAGE = ROOT / "docs" / "cockpit.html"
LEDGER_ROOT = ROOT / "data" / "decision_logs"

BEGIN = '<script id="cockpit-data" type="application/json">'
END = "</script>"

# Only these branches are embedded. The page reads nothing else, and carrying
# the whole 100KB data.json inline would bloat every copy of the sheet.
KEEP = (
    "generated_at", "session", "alphazero", "forecasts", "stockfish",
    "learning_loop", "verdicts", "latest_judgment", "spend", "containment",
    "verification", "ontology",
)
SERIES_KEEP = 12          # judgment log rows retained in the fallback
TRADES_KEEP = 12


def closed_trades() -> list[dict]:
    """Real closed trades from the mode-partitioned execution ledger.

    Returns [] when the ledger is absent — an empty positions table is honest;
    a placeholder row would not be.
    """
    if not LEDGER_ROOT.exists():
        return []
    rows = []
    for path in sorted(LEDGER_ROOT.rglob("decisions_*.jsonl")):
        mode = path.parent.name if path.parent != LEDGER_ROOT else "unpartitioned"
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError as e:      # I10: fail loud on corruption
                raise SystemExit(f"CORRUPT LINE {path}:{i} — refusing to publish: {e}")
            if r.get("r_realized") is None:
                continue                            # never scored, never shown
            entry = (r.get("entry_fill") or {})
            exit_ = (r.get("exit_fill") or {})
            rows.append({
                "trade_id": r.get("trade_id"),
                "mode": r.get("mode", mode),
                "side": "SHORT" if (r.get("direction") in (-1, "short", "SHORT")) else "LONG",
                "entry": entry.get("price"),
                "exit": exit_.get("price"),
                "sl": r.get("stop_loss") or r.get("sl"),
                "r": r.get("r_realized"),
                "outcome": r.get("outcome"),
                "basis": entry.get("basis"),
                "reason": r.get("exit_reason"),
                # A superseded row stays IN this list — it is real history,
                # not hidden — but the page must never fold it into "N closed"
                # or any count/mean over the table (execution_ledger's
                # mark_superseded; NVDA-2026-08-21/-22 is the row this exists
                # to carry through honestly rather than silently drop).
                "superseded": bool(r.get("superseded")),
                "superseded_by": r.get("superseded_by"),
                "superseded_reason": r.get("superseded_reason"),
            })
    rows.sort(key=lambda r: str(r.get("trade_id")))
    return rows[-TRADES_KEEP:]


def build() -> dict:
    if not DATA.exists():
        raise SystemExit(f"no {DATA} — run build_dashboard_data.py first")
    full = json.loads(DATA.read_text())

    snap = {k: full[k] for k in KEEP if k in full}
    missing = [k for k in KEEP if k not in full]
    if missing:
        print(f"  note: absent from data.json, omitted rather than faked: {', '.join(missing)}")

    series = (snap.get("verdicts") or {}).get("series")
    if series:
        snap["verdicts"] = dict(snap["verdicts"], series=series[-SERIES_KEEP:])

    snap["closed_trades"] = closed_trades()
    return snap


def main() -> int:
    snap = build()
    html = PAGE.read_text()
    if BEGIN not in html:
        raise SystemExit(f"{PAGE} has no cockpit-data block — refusing to guess where it goes")

    head, rest = html.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    out = f"{head}{BEGIN}\n{json.dumps(snap, separators=(',', ':'))}\n{END}{tail}"
    PAGE.write_text(out)

    n_tr = len(snap["closed_trades"])
    n_superseded = sum(1 for t in snap["closed_trades"] if t.get("superseded"))
    n_counted = n_tr - n_superseded
    print(f"  wrote {PAGE.relative_to(ROOT)}")
    print(f"  snapshot: {len(snap)} sections, {n_counted} closed trade(s)"
          + (f" ({n_superseded} superseded, shown but not counted)" if n_superseded else "")
          + f", generated_at {snap.get('generated_at', '?')}")
    if n_counted == 0:
        print("  no scored trades in the ledger — positions table renders its empty state")
    return 0


if __name__ == "__main__":
    sys.exit(main())
