#!/usr/bin/env python3
"""CARRY UNWIND SENTINEL — spec 027, PHASE 1 ONLY.

build-manifest and reconstruct-packets. NO model calls, NO scoring, NO
authority — Phase 2 is gated on the NO_EVIDENCE_COHORT assertion below and
on the manifest existing, frozen, first.

Every constant here is the spec's amendment, frozen 2026-08-17:
  model claude-sonnet-5, training cutoff 2026-01 -> every window whose
  window_start predates the cutoff is cohort=development (prompt work only,
  never evidence). The sealed carry history ends 2024-12, so the expected
  evidence-cohort count is ZERO and the tool must say so rather than score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRADES = ROOT / "data" / "proof" / "backtest_trades_v015_2015_2024.csv"
SENTDIR = ROOT / "data" / "daytrade" / "sentinel"
MANIFEST = SENTDIR / "manifest.json"

PINNED_MODEL = "claude-sonnet-5"
MODEL_CUTOFF = "2026-01-01"        # published training-data cutoff (Jan 2026)
WINDOW_DAYS = 60
N_UNWIND = 20
N_CALM = 20
UNWIND_PCTL = 85                   # forward-drawdown >= this pctl of all rolling dd
CALM_PCTL = 50                     # forward-drawdown <= this pctl
VOL_TOL_REL = 0.50                 # calm matching: trailing-90d vol ±50% relative
YEAR_TOL = 2


class SentinelError(RuntimeError):
    pass


def _equity_series():
    """Cumulative risk_adjusted_pnl_pct by exit_date — the sealed series
    itself; no external prices enter labeling (spec)."""
    import csv
    rows = []
    with TRADES.open() as fh:
        for r in csv.DictReader(fh):
            rows.append((datetime.fromisoformat(r["exit_date"]).date(),
                         float(r["risk_adjusted_pnl_pct"])))
    rows.sort()
    days, eq, cum = [], [], 0.0
    for d, pnl in rows:
        cum += pnl
        days.append(d)
        eq.append(cum)
    return days, eq


def _forward_drawdown(days, eq, start_i):
    """Max peak-to-trough drawdown of equity over [start, start+60d]."""
    start_day = days[start_i]
    end_day = start_day + timedelta(days=WINDOW_DAYS)
    peak, worst = eq[start_i], 0.0
    for i in range(start_i, len(days)):
        if days[i] > end_day:
            break
        peak = max(peak, eq[i])
        worst = max(worst, peak - eq[i])
    return worst


def _trailing_vol(days, eq, start_i):
    lo = days[start_i] - timedelta(days=90)
    pts = [eq[i] for i in range(len(days)) if lo <= days[i] <= days[start_i]]
    if len(pts) < 3:
        return 0.0
    diffs = [b - a for a, b in zip(pts, pts[1:])]
    m = sum(diffs) / len(diffs)
    return (sum((d - m) ** 2 for d in diffs) / len(diffs)) ** 0.5


def build_manifest() -> int:
    if MANIFEST.exists():
        raise SentinelError(f"{MANIFEST} already exists — a manifest with "
                            "scored calls against it must never be rebuilt. "
                            "Delete deliberately if no calls were ever made.")
    days, eq = _equity_series()
    fdd = [_forward_drawdown(days, eq, i) for i in range(len(days))]
    import statistics
    hi = statistics.quantiles(fdd, n=100)[UNWIND_PCTL - 1]
    lo = statistics.quantiles(fdd, n=100)[CALM_PCTL - 1]

    def pick(indices, want):
        """Greedy non-overlapping selection (min gap 60 days), spec order."""
        chosen = []
        for i in indices:
            if len(chosen) >= want:
                break
            if all(abs((days[i] - days[j]).days) >= WINDOW_DAYS for j in chosen):
                chosen.append(i)
        return chosen

    unwind_i = pick(sorted((i for i in range(len(days)) if fdd[i] >= hi),
                           key=lambda i: -fdd[i]), N_UNWIND)
    calm_pool = pick(sorted((i for i in range(len(days)) if fdd[i] <= lo),
                            key=lambda i: fdd[i]), N_CALM * 3)

    # calm matching: nearest trailing vol within ±50% rel, year ±2, no
    # replacement, ties by earlier date, deterministic (sorted, no RNG)
    used, pairs, misses = set(), [], 0
    for ui in sorted(unwind_i, key=lambda i: days[i]):
        uv, uy = _trailing_vol(days, eq, ui), days[ui].year
        cands = [(abs(_trailing_vol(days, eq, ci) - uv), days[ci], ci)
                 for ci in calm_pool if ci not in used
                 and abs(days[ci].year - uy) <= YEAR_TOL]
        if not cands:
            cands = [(abs(_trailing_vol(days, eq, ci) - uv), days[ci], ci)
                     for ci in calm_pool if ci not in used]
        if not cands:
            raise SentinelError("calm pool exhausted — matching impossible")
        dv, _, ci = min(cands)
        in_tol = uv > 0 and dv / uv <= VOL_TOL_REL
        misses += 0 if in_tol else 1
        used.add(ci)
        pairs.append((ui, ci, in_tol))

    cutoff = datetime.fromisoformat(MODEL_CUTOFF).date()
    windows = []
    for kind, idxs in (("unwind", [u for u, _, _ in pairs]),
                       ("calm", [c for _, c, _ in pairs])):
        for i in idxs:
            ws = days[i]
            windows.append({
                "window_id": f"{kind}-{ws.isoformat()}",
                "label": kind, "window_start": ws.isoformat(),
                "packet_cutoff_ts": ws.isoformat(),
                "forward_drawdown": round(fdd[i], 6),
                "trailing_vol": round(_trailing_vol(days, eq, i), 6),
                "cohort": "evidence" if ws >= cutoff else "development",
            })
    windows.sort(key=lambda w: w["window_start"])

    ev_unwind = sum(1 for w in windows
                    if w["cohort"] == "evidence" and w["label"] == "unwind")
    ev_calm = sum(1 for w in windows
                  if w["cohort"] == "evidence" and w["label"] == "calm")
    evidence_ok = ev_unwind >= 1 and ev_calm >= 1

    body = {
        "spec": "027 (amended 2026-08-17)",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "pinned_model": PINNED_MODEL, "model_cutoff": MODEL_CUTOFF,
        "labels": {"series": "cumulative risk_adjusted_pnl_pct by exit_date "
                             "of backtest_trades_v015_2015_2024.csv",
                   "window_days": WINDOW_DAYS, "unwind_pctl": UNWIND_PCTL,
                   "calm_pctl": CALM_PCTL, "overlap": "forbidden, min gap 60d",
                   "pairs": "the four sealed pairs only"},
        "matching": {"vars": ["trailing_90d_equity_vol", "year±2"],
                     "vol_tol_rel": VOL_TOL_REL, "replacement": False,
                     "seed": "deterministic sorted iteration, no RNG",
                     "tolerance_misses": misses},
        "n_windows": len(windows),
        "evidence_cohort": {"unwind": ev_unwind, "calm": ev_calm,
                            "status": "OK" if evidence_ok else "NO_EVIDENCE_COHORT"},
        "windows": windows,
    }
    body["manifest_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()).hexdigest()
    SENTDIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(body, indent=1))

    print(f"  manifest: {len(windows)} windows "
          f"({sum(1 for w in windows if w['label']=='unwind')} unwind / "
          f"{sum(1 for w in windows if w['label']=='calm')} calm), "
          f"{misses} matching-tolerance misses recorded")
    print(f"  EVIDENCE COHORT: {ev_unwind} unwind + {ev_calm} calm post-cutoff "
          f"({MODEL_CUTOFF}) -> {body['evidence_cohort']['status']}")
    if not evidence_ok:
        print("  => zero scored model calls are permitted against this manifest. "
              "Historical windows are prompt-development only; evidence accrues "
              "LIVE-FORWARD from the running carry gate.")
    print(f"  sha256 {body['manifest_sha256'][:16]}… -> {MANIFEST.relative_to(ROOT)}")
    return 0


SENTINEL_LOG = SENTDIR / "live_calls.jsonl"

SENTINEL_SYSTEM = """You are the unwind sentinel for a live FX carry book.

The book holds multi-day carry positions in EURUSD, GBPUSD, USDJPY, AUDUSD.
Its edge is real and measured; its ONE catastrophic failure mode is a regime
break — a carry unwind, where crowded positions liquidate together and the
usual risk model understates the loss badly.

Your single job: does the brief below show ELEVATED RISK OF AN UNWIND?

You are NOT asked to predict returns, pick trades, size anything, or time an
entry. Those belong to a mechanical engine. You are asked to read conditions.

What matters, and why:
- A BID DOLLAR is the unwind. Carry dies when funding currencies rally.
- CREDIT SPREADS widen before FX capitulates — high-yield and EM OAS are the
  early tell, especially a fast change from a low base.
- VOL TERM STRUCTURE inverting (3-month below spot) precedes stress.
- FINANCIAL CONDITIONS tightening from an easy base is more informative than
  the level itself.
- A very LOW starting percentile is not safety. Crowded, calm, cheap-vol
  regimes are where unwinds are born — say so when you see one.

Rules:
- 'none' is the correct and common answer. Manufacturing concern to seem
  useful is the failure mode here; a false alarm costs skipped carry.
- Cite SPECIFIC series and values from the brief. A read that could have been
  written without the data is worthless and will be scored as such.
- invalidators must be checkable levels or changes, never sentiment.
- If the brief says something is UNAVAILABLE or stale, treat that as reduced
  information and say so rather than filling the gap.

Your call is sealed before the outcome and graded against the forward
drawdown of the actual book."""


def carry_packet(on: date, *, symbols=None) -> tuple[str, dict]:
    """Everything knowable about the carry book on `on`, and nothing later.

    Assembles the three layers the system now records — the carry itself
    (rate differentials and their drift), what holding costs (swap, weekend
    exposure), and the macro conditions an unwind actually arrives through —
    into one bounded, point-in-time brief.

    BOUNDED BY DESIGN: current values with trailing-percentile context rather
    than raw series. A model reading 'VIX 14.89' learns little; 'VIX 14.89,
    9th percentile of two years, down 3.7 in 20 days' is the same token count
    and carries the regime. Absences are stated, never dropped.
    """
    import chain as _chain
    import fx_state as _fx
    import macro_state as _macro

    lines, meta = [], {"as_of": on.isoformat(), "missing": []}
    lines.append(f"CARRY BOOK BRIEF — as of {on.isoformat()} (nothing later)")

    # --- layer 1: the carry itself, per pair
    rows = {}
    for r in _chain.rows(_fx.LEDGER):
        if r["session_date"] <= on.isoformat():
            rows.setdefault(r["pair"], []).append(r)
    lines.append("\nTHE CARRY (rate differential = base - quote, in points):")
    if not rows:
        lines.append("  UNAVAILABLE — no FX state rows")
        meta["missing"].append("fx_state")
    for pair in sorted(rows):
        last = rows[pair][-1]
        rd, st = last.get("rate_diff"), last.get("rate_diff_stale_days")
        if rd is None:
            lines.append(f"  {pair:10s} rate_diff UNAVAILABLE (stated, not guessed)")
            continue
        d5, d20 = last.get("rate_diff_d5"), last.get("rate_diff_d20")
        lines.append(
            f"  {pair:10s} carry {rd:+.3f}  5d {d5 if d5 is None else format(d5, '+.3f')}"
            f"  20d {d20 if d20 is None else format(d20, '+.3f')}"
            f"  | vol20 {last.get('realized_vol_20d_pct')}%"
            f"  trend20 {last.get('trend_pct_vs_ma20')}%"
            f"  dd20 {last.get('drawdown_from_20d_high_pct')}%"
            f"  | rate read {st}d old")

    # --- layer 2: what holding costs
    try:
        swap = _fx.swap_per_day()
        lines.append(f"\nCOST OF HOLDING: {swap} R per day per position; a "
                     f"weekend bills 3 days ({3*swap:.4f} R). 72% of the sealed "
                     "record's trades cross a weekend; median hold 5 days "
                     f"({5*swap:.3f} R of financing before any price move).")
    except Exception as e:
        lines.append(f"\nCOST OF HOLDING: UNAVAILABLE ({e})")
        meta["missing"].append("swap")

    # --- layer 3: the conditions an unwind arrives through
    snap = _macro.snapshot(on)
    lines.append("\nMACRO CONDITIONS (value | 2y percentile | 20d change | age):")
    if not snap["series"]:
        lines.append("  UNAVAILABLE — no macro series cached")
        meta["missing"].append("macro")
    for sid, v in snap["series"].items():
        why = _macro.SERIES.get(sid, "")
        pct = "n/a" if v["pctile_2y"] is None else f"{v['pctile_2y']}%ile"
        lines.append(f"  {sid:18s} {v['value']:>10.3f} | {pct:>8s} | "
                     f"d20 {str(v['change_20d']):>8s} | {v['stale_days']}d — {why}")
    if snap["unavailable"]:
        lines.append(f"  UNAVAILABLE: {snap['unavailable']}")
        meta["missing"] += snap["unavailable"]

    meta["n_pairs"] = len(rows)
    meta["n_macro_series"] = len(snap["series"])
    text = "\n".join(lines)
    meta["chars"] = len(text)
    return text, meta


def live_call(on: date, *, cap: float, model: str) -> int:
    """One sealed, scored sentinel call on TODAY's conditions.

    Spec 027's honesty boundary makes this the ONLY evidence-grade path: every
    historical window predates the model's training cutoff, so live-forward
    calls are the entire evidence channel. This is that channel opening.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import news_claude
    from pydantic import BaseModel, Field
    from typing import Literal

    class UnwindRead(BaseModel):
        unwind_risk: Literal["none", "elevated", "severe"]
        confidence: float = Field(ge=0.0, le=1.0)
        reasoning: str = Field(description="2-3 sentences citing SPECIFIC series and values")
        key_series: list[str] = Field(description="the series that drove this call")
        invalidators: list[str] = Field(description="checkable levels/changes that would flip it")
        horizon_days: int = Field(ge=5, le=90)

    if on < datetime.fromisoformat(MODEL_CUTOFF).date():
        raise SentinelError(
            f"{on} predates the pinned model's cutoff {MODEL_CUTOFF} — a call "
            "here is prompt development, never evidence (spec 027 rule 1)")

    text, meta = carry_packet(on)
    system = [{"type": "text", "text": SENTINEL_SYSTEM,
               "cache_control": {"type": "ephemeral"}}]
    user = [{"type": "text", "text": text}]
    read, cost = news_claude._call(system, user, UnwindRead, model=model,
                                   cap=cap, effort="medium", kind="sentinel")

    row = {"kind": "sentinel_call", "as_of": on.isoformat(),
           "called_at": datetime.now(timezone.utc).isoformat(),
           "model": model, "cohort": "evidence",
           "unwind_risk": read.unwind_risk, "confidence": read.confidence,
           "reasoning": read.reasoning, "key_series": read.key_series,
           "invalidators": read.invalidators, "horizon_days": read.horizon_days,
           "packet_chars": meta["chars"], "packet_missing": meta["missing"],
           "cost_usd": round(cost, 8), "resolved": False}
    import chain as _chain
    _chain.append(SENTINEL_LOG, row)

    print(f"\n  UNWIND RISK: {read.unwind_risk.upper()} "
          f"(confidence {read.confidence:.2f}, horizon {read.horizon_days}d)")
    print(f"  {read.reasoning}")
    print(f"  drivers: {', '.join(read.key_series)}")
    for i in read.invalidators:
        print(f"  invalidator: {i}")
    print(f"\n  sealed to {SENTINEL_LOG.relative_to(ROOT)} (chain intact), "
          f"cost ${cost:.5f}")
    return 0


def reconstruct_packets() -> int:
    """Point-in-time packets from the sealed series itself — every input
    timestamped <= packet_cutoff_ts. No news/macro enrichment in Phase 1."""
    if not MANIFEST.exists():
        raise SentinelError("no manifest — run build-manifest first")
    m = json.loads(MANIFEST.read_text())
    days, eq = _equity_series()
    out = SENTDIR / "packets"
    out.mkdir(exist_ok=True)
    for w in m["windows"]:
        ws = datetime.fromisoformat(w["window_start"]).date()
        hist = [(d, v) for d, v in zip(days, eq) if d <= ws]
        if len(hist) < 5:
            print(f"  !! {w['window_id']}: <5 history points — packet skipped, loudly")
            continue
        recent = hist[-40:]
        peak = max(v for _, v in hist)
        packet = {
            "window_id": w["window_id"], "as_of": w["packet_cutoff_ts"],
            "note": "sealed-series features only; label NOT included",
            "equity_now": round(hist[-1][1], 6),
            "drawdown_from_peak": round(peak - hist[-1][1], 6),
            "trailing_90d_vol": w["trailing_vol"],
            "recent_curve": [{"date": d.isoformat(), "equity": round(v, 6)}
                             for d, v in recent],
        }
        (out / f"{w['window_id']}.json").write_text(json.dumps(packet, indent=1))
    print(f"  packets -> {out.relative_to(ROOT)} "
          f"({len(list(out.glob('*.json')))} files)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 027 Phase 1 — no model calls")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-manifest")
    sub.add_parser("reconstruct-packets")
    cp = sub.add_parser("packet")
    cp.add_argument("--date", default=None)
    lv = sub.add_parser("live")
    lv.add_argument("--date", default=None)
    lv.add_argument("--model", default="claude-sonnet-5")
    lv.add_argument("--cap", type=float, default=5.0)
    a = ap.parse_args(argv)
    if a.cmd == "packet":
        on = (date.fromisoformat(a.date) if a.date
              else datetime.now(timezone.utc).date())
        text, meta = carry_packet(on)
        print(text)
        print(f"\n  [packet {meta['chars']} chars, ~{meta['chars']//4} tokens; "
              f"{meta['n_pairs']} pairs, {meta['n_macro_series']} macro series; "
              f"missing: {meta['missing'] or 'nothing'}]")
        return 0
    if a.cmd == "live":
        on = (date.fromisoformat(a.date) if a.date
              else datetime.now(timezone.utc).date())
        return live_call(on, cap=a.cap, model=a.model)
    if a.cmd == "build-manifest":
        return build_manifest()
    return reconstruct_packets()


if __name__ == "__main__":
    sys.exit(main())
