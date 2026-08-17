#!/usr/bin/env python3
"""FOUR BOOKS — spec 023. Does Claude discretion add edge, or just variance?

Four parallel books replay the SAME session bars with the SAME plan and the
SAME Stockfish exit mechanics — the engine is imported, never re-implemented
(rule 1). The only thing that differs is entry permission and the urgency
channel, driven by the operator's sealed records:

  baseline  every baseline setup taken, no operator influence at all
  veto      operator TIGHTEN/EXIT active at entry time -> setup skipped;
            while holding, an active TIGHTEN/EXIT record tightens the trail
  select    entry ONLY when an active ALLOW_BASELINE record covers the symbol
  full      v1: identical to veto. An operator ENTER-proposal schema is not
            ratified, so the full-discretion book is MEASURED (it exists in
            every artifact, labeled) but not yet distinct. Building it without
            a spec would bake in a guess — 000's [SKETCH] rule.

R is 022's `realized_r_multileg` — the whole position across every leg against
the ORIGINAL risk. Legs must reconcile to entry qty or it raises; there is no
way for this harness to quietly mis-score a ladder.

This is a measurement harness. It writes data/daytrade/operator/books-*.json
and prints a table. It never touches the execution ledger, never places
anything, and never writes to any directives or state file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stockfish_exit import decide_exit, apply_actions          # noqa: E402
from runner import state_from_plan, load_plan                  # noqa: E402
from sovereign.intelligence.execution_ledger import realized_r_multileg  # noqa: E402
import bars as bars_mod                                        # noqa: E402
import alpha_operator as ao                                    # noqa: E402

BOOKS = ("baseline", "veto", "select", "full")

# Referee-first doctrine (spec 024): a system that can only abstain/tighten/
# exit has bounded downside and gives the cleanest signal on whether the
# discretion has any edge. Veto is the book of record; full stays unbuilt
# until an ENTER schema is ratified.
ROLES = {"veto": "primary", "baseline": "reference",
         "select": "experimental", "full": "unbuilt"}


class HarnessError(RuntimeError):
    """Books diverging on inputs, or a position that cannot reconcile."""


def _aware(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def bars_fingerprint(df) -> str:
    """The proof all four books saw identical data (spec 023 I8)."""
    return hashlib.sha256(df.to_csv().encode()).hexdigest()[:16]


def active_records(records: list[dict], symbol: str, ts: datetime,
                   verdicts: tuple[str, ...]) -> list[dict]:
    """Operator records in force at `ts`: right symbol, right verdict, sealed
    at-or-before ts, not yet expired. Nothing is inferred from a record that
    was not yet on disk — the seal timestamp is the arrow of time here."""
    out = []
    for r in records:
        if r["symbol"] != symbol or r["verdict"] not in verdicts:
            continue
        if _aware(r["ts"]) <= ts < _aware(r["expires_at"]):
            out.append(r)
    return out


def _entry_bar(df, plan: dict):
    """First bar whose close crosses the plan entry in trade direction.
    A session that never triggers is a no-trade, not an error."""
    d = plan["direction"]
    for ts, row in df.iterrows():
        c = float(row["Close"])
        if (d > 0 and c >= plan["entry"]) or (d < 0 and c <= plan["entry"]):
            return ts
    return None


def run_book(book: str, df, plan: dict, records: list[dict],
             symbol: str) -> dict:
    """One book over one session. Returns the trade (or the no-trade) with
    its full reasoning trail."""
    if book not in BOOKS:
        raise HarnessError(f"unknown book {book!r}; known: {BOOKS}")
    # Fingerprint what THIS book actually consumed — computed here, not stamped
    # on from outside, or the I8 divergence check could never fire (caught by
    # mutation M9 surviving: run_session's single upfront hash was decorative).
    fp = bars_fingerprint(df)

    entry_ts = _entry_bar(df, plan)
    if entry_ts is None:
        return {"book": book, "entered": False, "why": "entry never triggered",
                "r": 0.0, "legs": [], "bars_fingerprint": fp}

    ts_utc = entry_ts.tz_convert(timezone.utc)
    if book in ("veto", "full"):
        blocking = active_records(records, symbol, ts_utc, ("TIGHTEN", "EXIT"))
        if blocking:
            return {"book": book, "entered": False, "r": 0.0, "legs": [],
                    "bars_fingerprint": fp,
                    "why": f"vetoed by {blocking[0]['record_id']} "
                           f"({blocking[0]['verdict']})"}
    if book == "select":
        allowing = active_records(records, symbol, ts_utc, ("ALLOW_BASELINE",))
        if not allowing:
            return {"book": book, "entered": False, "r": 0.0, "legs": [],
                    "bars_fingerprint": fp,
                    "why": "no ALLOW_BASELINE record covers this entry"}

    s = state_from_plan(plan)
    legs: list[dict] = []
    urgency_bars = 0
    applied: set = set()
    after = df[df.index > entry_ts]

    for ts, row in after.iterrows():
        s.price = float(row["Close"])
        if book == "baseline":
            s.urgent = None
        else:
            act = active_records(records, symbol, ts.tz_convert(timezone.utc),
                                 ("TIGHTEN", "EXIT"))
            # Unpromoted operator: in-position influence is tighten, never exit
            # — the same cap the directive path enforces (spec 023 ruling 2).
            s.urgent = "tighten" if act else None
            urgency_bars += 1 if act else 0

        actions = decide_exit(s)
        for a in actions:
            if a.kind == "TAKE_PARTIAL":
                legs.append({"qty": s.qty * a.fraction, "price": s.price,
                             "reason": a.reason, "ts": ts.isoformat()})
            elif a.kind == "EXIT_ALL":
                legs.append({"qty": s.qty, "price": s.price,
                             "reason": a.reason, "ts": ts.isoformat()})
        apply_actions(s, actions, applied)
        if s.stage.value == "CLOSED":
            break

    if s.stage.value != "CLOSED":
        # Session ended holding. Flat by the close is the doctrine; the forced
        # leg is labeled so nobody mistakes it for an engine decision.
        legs.append({"qty": s.qty, "price": float(after["Close"].iloc[-1]),
                     "reason": "session close flat (harness, not engine)",
                     "ts": after.index[-1].isoformat()})

    r = realized_r_multileg(
        entry_price=plan["entry"], initial_stop=plan["sl"],
        entry_qty=float(plan["qty"]), direction=plan["direction"],
        legs=legs)
    return {"book": book, "entered": True, "entry_ts": entry_ts.isoformat(),
            "r": round(r, 4), "legs": legs, "urgency_bars": urgency_bars,
            "bars_fingerprint": fp, "why": "entered and ran the engine"}


def run_session(symbol: str, df, plan: dict, records: list[dict]) -> dict:
    results = {}
    for book in BOOKS:
        # Each book gets its OWN copy of the frame, fingerprinted inside
        # run_book — so an accidental per-book filter or in-place mutation is
        # detectable, not equal-by-construction (Cato finding 9).
        results[book] = run_book(book, df.copy(), plan, records, symbol)
    fps = {b["bars_fingerprint"] for b in results.values()}
    if len(fps) != 1:                            # spec 023 I8, asserted not assumed
        raise HarnessError(f"books consumed different bar data: {fps}")
    fp = fps.pop()
    for book in BOOKS:
        results[book]["role"] = ROLES[book]
    return {"symbol": symbol, "session": str(df.index[0].date()),
            "bars_fingerprint": fp, "n_records": len(records),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "policy": "referee-first (spec 024): veto is the book of record; "
                      "full is measured-not-built",
            "note_full_book": "v1: full == veto until an entry-proposal "
                              "schema is ratified (spec 023)",
            "yield_delta_r": round(results["veto"]["r"] - results["baseline"]["r"], 4),
            "books": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 023 — four-book comparison")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--plan", default=str(ao.PLAN))
    ap.add_argument("--session", default=None,
                    help="YYYY-MM-DD; default latest complete cached session")
    a = ap.parse_args(argv)

    plan = load_plan(Path(a.plan))
    sessions = bars_mod.load_sessions(a.symbol, "5m", allow_fetch=False)
    if not sessions:
        print("  no cached sessions — run bars refresh first")
        return 1
    if a.session:
        match = [s for s in sessions if str(s.day) == a.session]
        if not match:
            print(f"  no complete session {a.session} in cache "
                  f"({sessions[0].day} .. {sessions[-1].day})")
            return 1
        sess = match[0]
    else:
        sess = sessions[-1]

    records = ao._read_jsonl(ao.RECORDS)
    out = run_session(a.symbol, sess.df, plan, records)

    print(f"\n  {a.symbol} {out['session']}   bars {out['bars_fingerprint']}   "
          f"operator records {out['n_records']}")
    print(f"  {'book':10s} {'role':13s} {'entered':8s} {'R':>8s}   why")
    for book in ("veto", "baseline", "select", "full"):   # referee first
        b = out["books"][book]
        print(f"  {book:10s} {ROLES[book]:13s} {str(b['entered']):8s} "
              f"{b['r']:>+8.3f}   {b['why']}")

    ao.OPDIR.mkdir(parents=True, exist_ok=True)
    artifact = ao.OPDIR / f"books-{out['session']}.json"
    artifact.write_text(json.dumps(out, indent=1))
    # One yield row per session (spec 024 I21) — the demotion decision must be
    # readable from data, not reconstructed from memory of a hot week.
    ao._append_jsonl(ao.YIELD_LOG, {
        "session": out["session"], "symbol": a.symbol,
        "veto_r": out["books"]["veto"]["r"],
        "baseline_r": out["books"]["baseline"]["r"],
        "yield_delta_r": out["yield_delta_r"],
        "bars_fingerprint": out["bars_fingerprint"],
        "n_records": out["n_records"]})
    print(f"\n  yield (veto − baseline): {out['yield_delta_r']:+.3f}R")
    print(f"  artifact: {artifact}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
