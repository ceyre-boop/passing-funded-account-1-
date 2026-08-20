#!/usr/bin/env python3
"""Dashboard data generator — aggregates the operator's audit logs into one
docs/data.json for the GitHub Pages dashboard.

Stdlib only, no operator imports (the dashboard must never be able to steer
anything). Fail loud on corrupt lines (I10 doctrine); absent files become
honest {"available": false} sections, never fake zeros.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPDIR = ROOT / "data" / "daytrade" / "operator"
RECORDS = OPDIR / "records.jsonl"
FC_LOG = OPDIR / "forecasts.jsonl"
YIELD_LOG = OPDIR / "yield.jsonl"
SPEND = ROOT / "data" / "daytrade" / "llm_spend.jsonl"
OUT = ROOT / "docs" / "data.json"
CAP_USD = 5.0

DISCIPLINES = [
    {"id": 1, "name": "Pre-registration",
     "mechanism": "Expected-R band in [-5,+5] plus a closed predicate vocabulary, sealed before the outcome; the resolver scores every claim as a prereg_score row.",
     "invariants": ["I11", "I12", "I13"], "mutations": ["M17", "M26"], "status": "active"},
    {"id": 2, "name": "Point-in-time packets",
     "mechanism": "build_packet(as_of=T) admits no bar, headline, or event newer than T; max_data_ts is sealed in the record as proof.",
     "invariants": ["I14"], "mutations": ["M18", "M19"], "status": "active"},
    {"id": 3, "name": "Caps & co-drift",
     "mechanism": "3 directives per session; a 2nd same-group interrupt inside 30 min pauses instead of averaging. Emission refused, judgment still sealed.",
     "invariants": ["I15", "I16", "I17"], "mutations": ["M20", "M21", "M22"], "status": "active"},
    {"id": 4, "name": "Referee first",
     "mechanism": "The veto book is the book of record — bounded downside, cleanest edge signal. Full discretion stays unbuilt.",
     "invariants": [], "mutations": [], "status": "active"},
    {"id": 5, "name": "Sim→live gap tripwire",
     "mechanism": "gap_log.py matches trade_ids across sim and paper ledgers; widening drift beyond 0.05R blocks the promotion report.",
     "invariants": ["I18", "I19", "I20"], "mutations": ["M24", "M25"], "status": "armed — no paper ledger yet"},
    {"id": 6, "name": "Yield curve",
     "mechanism": "One yield row per session (veto R − baseline R); three monotone declines print YIELD_DECAY. Demotion readable from data.",
     "invariants": ["I21"], "mutations": [], "status": "awaiting first live session"},
    {"id": 7, "name": "Shadow soak",
     "mechanism": "--shadow: full judgment, sealed record, forecast — zero directive bytes. Excluded from the session cap.",
     "invariants": ["I22"], "mutations": ["M23"], "status": "ON"},
]

VERIFICATION = {
    "tests": 290,
    "mutation_rows": 26,
    "mutations_killed": 26,
    "mutations": [f"M{i}" for i in range(1, 27)],
    "flagged": {
        "M9": "survived first run — decorative fingerprint check found and fixed, then killed",
        "M26": "false-killed first (bad test target); real negative test added, then killed",
    },
    "gates": [
        {"gate": 1, "name": "AZ spine", "status": "closed"},
        {"gate": 2, "name": "Event memory", "status": "closed"},
        {"gate": 3, "name": "Execution → broker", "status": "open"},
        {"gate": 4, "name": "Shadow / regret", "status": "open"},
        {"gate": 5, "name": "Evidence / directive producer", "status": "producer built (023)"},
        {"gate": 6, "name": "Forecast producer", "status": "producer built (023)"},
        {"gate": 7, "name": "Portfolio guards", "status": "advisory wired (enforce last)"},
    ],
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for i, line in enumerate(path.open(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"CORRUPT LINE {path}:{i} ({e}) — refusing to publish")
    return rows


def build_session(records: list[dict]) -> dict:
    if not records:
        return {"available": False, "mode": "NO RECORDS YET", "records_total": 0}
    last = records[-1]
    return {"available": True,
            "mode": "SHADOW SOAK" if last.get("shadow") else "LIVE DIRECTIVES",
            "shadow": bool(last.get("shadow")),
            "last_record_ts": last["ts"], "last_trigger": last.get("trigger"),
            "records_total": len(records), "symbol": last.get("symbol")}


def build_verdicts(records: list[dict]) -> dict:
    counts = Counter(r["verdict"] for r in records)
    reasons = Counter(r["abstention"]["reason"] for r in records
                      if r.get("abstention"))
    return {"counts": {v: counts.get(v, 0)
                       for v in ("ABSTAIN", "ALLOW_BASELINE", "TIGHTEN", "EXIT")},
            "abstain_reasons": dict(reasons),
            "series": [{"ts": r["ts"], "verdict": r["verdict"],
                        "confidence": r.get("confidence", 0.0),
                        "shadow": bool(r.get("shadow"))} for r in records[-40:]]}


def build_latest(records: list[dict]) -> dict:
    if not records:
        return {"available": False}
    r = records[-1]
    return {"available": True,
            **{k: r.get(k) for k in
               ("record_id", "ts", "verdict", "trigger", "confidence",
                "bar_age_min", "both_sides", "invalidators", "pre_registration",
                "abstention", "emission_refused", "packet_as_of",
                "packet_max_data_ts", "cost_usd", "model_version")}}


def build_spend(rows: list[dict]) -> dict:
    by_day: dict[str, dict] = {}
    for r in rows:
        d = r["ts"][:10]
        s = by_day.setdefault(d, {"date": d, "usd": 0.0, "calls": 0})
        s["usd"] += r["cost_usd"]
        s["calls"] += 1
    total = sum(r["cost_usd"] for r in rows)
    return {"cap_usd": CAP_USD, "total_usd": round(total, 4),
            "call_count": len(rows),
            "avg_call_usd": round(total / len(rows), 5) if rows else None,
            "by_day": [dict(v, usd=round(v["usd"], 4))
                       for v in sorted(by_day.values(), key=lambda x: x["date"])][-14:]}


def build_forecasts(rows: list[dict]) -> dict:
    fcs = [r for r in rows if r["kind"] == "forecast"]
    resolved = {r["forecast_id"] for r in rows if r["kind"] == "resolution"}
    scores = [r for r in rows if r["kind"] == "prereg_score"]
    banded = [s for s in scores if s.get("r_in_band") is not None]
    in_band = sum(1 for s in banded if s["r_in_band"])
    return {"total": len(fcs), "resolved": len(resolved),
            "open": len(fcs) - len(resolved),
            "prereg_scored": len(scores), "prereg_in_band": in_band,
            "prereg_in_band_rate": round(in_band / len(banded), 3) if banded else None,
            "direction_counts": dict(Counter(f["direction"] for f in fcs))}


def build_yield(rows: list[dict]) -> dict:
    return {"available": bool(rows),
            "rows": [{"session": r["session"],
                      "yield_delta_r": r["yield_delta_r"]} for r in rows][-20:]}


def build_books() -> dict:
    files = sorted(OPDIR.glob("books-*.json"))
    if not files:
        return {"available": False}
    b = json.loads(files[-1].read_text())
    order = ("veto", "baseline", "select", "full")
    roles = {"veto": "BOOK OF RECORD", "baseline": "reference",
             "select": "experimental", "full": "unbuilt (== veto)"}
    return {"available": True, "session": b["session"], "symbol": b["symbol"],
            "bars_fingerprint": b["bars_fingerprint"],
            "n_records": b["n_records"],
            "note_full_book": b.get("note_full_book", ""),
            "yield_delta_r": b.get("yield_delta_r",
                                   round(b["books"]["veto"]["r"]
                                         - b["books"]["baseline"]["r"], 4)),
            "books": [{"book": k, "role": b["books"][k].get("role", roles[k]),
                       "entered": b["books"][k]["entered"],
                       "r": b["books"][k]["r"], "why": b["books"][k]["why"]}
                      for k in order]}


def build_alphazero(records: list[dict], fc_rows: list[dict]) -> dict:
    """Every prediction it made and whether the tape agreed — the only
    question that decides whether this component is worth re-pointing at FX."""
    fs = {r["forecast_id"]: r for r in fc_rows if r["kind"] == "forecast"}
    res = {r["forecast_id"]: r for r in fc_rows if r["kind"] == "resolution"}
    prereg = {r["forecast_id"]: r for r in fc_rows if r["kind"] == "prereg_score"}
    recs = {r["forecast_id"]: r for r in records if r.get("forecast_id")}

    preds = []
    for fid, f in sorted(fs.items(), key=lambda kv: kv[1]["as_of"]):
        r = res.get(fid)
        rec = recs.get(fid, {})
        top = max(f["scenario_probs"], key=f["scenario_probs"].get)
        pr = prereg.get(fid, {})
        preds.append({
            "as_of": f["as_of"], "symbol": f["symbol"],
            "verdict": rec.get("verdict"), "confidence": f.get("confidence"),
            "predicted_direction": f["direction"],
            "predicted_scenario": top,
            "top_prob": round(f["scenario_probs"][top], 3),
            "horizon_min": f["horizon_min"],
            "expected_r": ((rec.get("pre_registration") or {}).get("expected_r_low"),
                           (rec.get("pre_registration") or {}).get("expected_r_high")),
            "outcome_direction": (r or {}).get("outcome_direction"),
            "outcome_scenario": (r or {}).get("outcome_scenario"),
            "resolved": r is not None,
            "direction_hit": (None if r is None
                              else f["direction"] == r["outcome_direction"]),
            "scenario_hit": (None if r is None
                             else top == r["outcome_scenario"]),
            "realized_r": pr.get("realized_r"), "r_in_band": pr.get("r_in_band"),
        })

    resolved = [p for p in preds if p["resolved"]]
    decisive = [p for p in resolved if p["predicted_direction"] != "flat"
                and p["verdict"] != "ABSTAIN"]
    dir_hits = sum(1 for p in resolved if p["direction_hit"])
    scen_hits = sum(1 for p in resolved if p["scenario_hit"])
    return {
        "n_predictions": len(preds), "n_resolved": len(resolved),
        "n_decisive_resolved": len(decisive),
        "abstain_rate": (round(1 - len(decisive) / len(resolved), 3)
                         if resolved else None),
        "direction_hits": dir_hits,
        "direction_accuracy": (round(dir_hits / len(resolved), 3)
                               if resolved else None),
        "scenario_hits": scen_hits,
        "scenario_accuracy": (round(scen_hits / len(resolved), 3)
                              if resolved else None),
        "chance_scenario": 0.2,
        "decisive_accuracy_quotable": len(decisive) >= 30,
        "min_n_for_quotable": 30,
        "predictions": preds[-40:],
    }


def build_stockfish() -> dict:
    """Exit quality judged by lookback — what the shipped policy kept of what
    was reachable, and how often the ENTRY made the exit irrelevant."""
    path = ROOT / "data" / "daytrade" / "exit_quality.json"
    if not path.exists():
        return {"available": False}
    q = json.loads(path.read_text())
    q["available"] = True
    return q


def build_containment(records: list[dict]) -> dict:
    emitted = sum(1 for r in records if r.get("directive") and not r.get("shadow"))
    suppressed = sum(1 for r in records if r.get("directive") and r.get("shadow"))
    refused = sum(1 for r in records if r.get("emission_refused"))
    directives_file = ROOT / "data" / "daytrade" / "directives.json"
    return {"directives_emitted": emitted,
            "directives_suppressed_shadow": suppressed,
            "emission_refusals": refused,
            "directives_file_exists": directives_file.exists(),
            "clean": emitted == 0 and not directives_file.exists()}


def main() -> int:
    records = load_jsonl(RECORDS)
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": build_session(records),
        "verdicts": build_verdicts(records),
        "latest_judgment": build_latest(records),
        "spend": build_spend(load_jsonl(SPEND)),
        "forecasts": build_forecasts(load_jsonl(FC_LOG)),
        "alphazero": build_alphazero(records, load_jsonl(FC_LOG)),
        "stockfish": build_stockfish(),
        "yield_curve": build_yield(load_jsonl(YIELD_LOG)),
        "four_books": build_books(),
        "disciplines": DISCIPLINES,
        "verification": VERIFICATION,
        "containment": build_containment(records),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, OUT)
    print(f"  {OUT.relative_to(ROOT)}: {len(records)} records, "
          f"${data['spend']['total_usd']:.4f} spend, "
          f"containment {'CLEAN' if data['containment']['clean'] else 'DIRTY'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
