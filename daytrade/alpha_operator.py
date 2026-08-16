#!/usr/bin/env python3
"""ALPHA OPERATOR — spec 023. The autonomous AlphaZero research desk.

The firewall was built first: bounded directives (018), earned authority (016),
forecast promotion gates (017), a runner directive channel that is WIRED and
tested. What never existed was a producer on the other side of it. This is that
producer.

One run: deterministic triggers decide whether anything meaningful changed. If
nothing did, zero API calls, zero cost. If something did, the operator builds an
evidence packet (bars, plan, headlines, position events), asks Claude for ONE
structured judgment — both sides argued, invalidators named, scenario
probabilities, a verdict — and seals the whole thing to disk BEFORE any
directive is observable. The forecast lands in the 017 ledger where its outcome
physically cannot enter early; the resolver closes it against the tape later.

AUTHORITY: this model is UNPROMOTED. It may say TIGHTEN (authority 1). When its
judgment is EXIT, the sealed record and the forecast carry EXIT verbatim so the
scorecard can grade the call it actually made — but the emitted directive is
capped at TIGHTEN with `suppressed_to` saying so. Same discipline as
news_claude's --allow-urgency: authority over a live position is earned through
the 017 promotion gate, never granted by being asked nicely.

MECHANICS: none. No field in anything this file emits can hold an order type,
a quantity, or a stop price — the 018 envelope refuses them structurally, and
every directive round-trips ContextDirective.from_dict(to_dict()) before the
file write, so a malformed payload cannot reach the runner.

Spend rides the 009 ledger (llm_spend.jsonl) under the same hard cap. The
operator does not get its own budget.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import Evidence, EvidenceStore, EVIDENCE_TYPES, DIRECTIONS  # noqa: E402
from forecast import Forecast, Resolution, ForecastLedger, ForecastError  # noqa: E402
from context_directive import (ContextDirective, Scope, Abstention,       # noqa: E402
                               ABSTENTION_REASONS, DirectiveError)
from scenarios import ALL_SCENARIOS                                       # noqa: E402
import news_claude                                                        # noqa: E402
import bars as bars_mod                                                   # noqa: E402

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade"
OPDIR = OUT / "operator"
RECORDS = OPDIR / "records.jsonl"
EV_LOG = OPDIR / "evidence.jsonl"
FC_LOG = OPDIR / "forecasts.jsonl"
STATE = OPDIR / "state.json"
DIRECTIVES = OUT / "directives.json"          # the runner's existing input
PLAN = OUT / "plan.json"

MODEL_VERSION_BASE = "alpha-operator-v1"
PROMPT_VERSION = "op-1"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_HORIZON_MIN = 60
DIRECTIVE_TTL_MIN = 45
FLAT_BAND = 0.0015                 # ±0.15% net move counts as flat
SHOCK_TR_MULT = 3.0                # bar TR > 3x prior-20 median TR == shock
STALE_BAR_MIN = 15                 # newest bar older than this at call time
PROB_RENORM_TOL = 0.05             # LLM float drift we renormalise; beyond, raise

VERDICTS = ("ABSTAIN", "ALLOW_BASELINE", "TIGHTEN", "EXIT")


class OperatorError(RuntimeError):
    """Malformed model output or corrupt persistence. Never repaired silently."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ------------------------------------------------------------- the judgment

from pydantic import BaseModel, Field  # noqa: E402

_EVIDENCE_TYPE = Literal["EARNINGS", "GUIDANCE", "PRODUCT", "REGULATORY",
                         "MACRO", "ANALYST", "RUMOR_SOCIAL", "MARKET_STRUCTURE"]


class EvidenceItem(BaseModel):
    """Claude classifies the semantics; the code validates every enum against
    spec 015's vocabulary before an Evidence object exists."""
    headline: str
    evidence_type: _EVIDENCE_TYPE
    direction: Literal["bullish", "bearish", "mixed", "unknown"]
    severity: float = Field(description="0..1 — how much this could move THIS name today")
    scope: Literal["market", "sector", "symbol"]


class OperatorRead(BaseModel):
    """One sealed judgment. Both sides argued, then a verdict."""
    bull: str = Field(description="strongest honest bull case, 1-2 sentences")
    base: str = Field(description="most likely path, 1-2 sentences")
    bear: str = Field(description="strongest honest bear case, 1-2 sentences")
    invalidators: list[str] = Field(description="specific checkable things that break this read")
    prob_bull_continuation: float
    prob_bear_continuation: float
    prob_range_consolidation: float
    prob_failed_breakout: float
    prob_risk_event: float
    direction: Literal["up", "down", "flat"]
    horizon_min: int = Field(description="minutes until this forecast can be judged, 15-240")
    verdict: Literal["ABSTAIN", "ALLOW_BASELINE", "TIGHTEN", "EXIT"]
    abstain_reason: Optional[Literal["NO_OPINION", "LOW_CONFIDENCE",
                                     "CONFLICTING_EVIDENCE", "STALE_CONTEXT",
                                     "DATA_INCOMPLETE"]] = Field(
        default=None, description="required iff verdict is ABSTAIN")
    confidence: float = Field(description="0..1, calibrated — this is scored against baselines")
    expires_min: int = Field(description="minutes any resulting directive stays valid, 5-120")
    evidence: list[EvidenceItem] = Field(description="the headlines that actually matter, classified")


OPERATOR_SYSTEM = """You are the discretionary research desk for a single-name
day-trading system. You do NOT place orders, size positions, or move stops —
a separate mechanical engine owns all of that. Your output is a JUDGMENT that
is sealed to disk before the outcome is known and graded against dumb baselines
(uniform scenario probabilities, always-flat). A confident wrong call scores
worse than an honest abstention.

Rules:
- Argue BOTH sides before the verdict. The bull and bear cases must each be the
  strongest honest version, not strawmen.
- scenario probabilities are over exactly: bull_continuation, bear_continuation,
  range_consolidation, failed_breakout, risk_event. They must sum to 1.
- verdict vocabulary, in full:
    ABSTAIN         — you have no defensible read. A valid, common answer.
    ALLOW_BASELINE  — the mechanical baseline setup is fine as planned.
    TIGHTEN         — risk should be carried tighter; stops defend earlier.
    EXIT            — holding through what is coming is unreasonable. Reserve
                      for shocks: halts, fraud, withdrawn guidance. A false
                      EXIT costs a live position for nothing — and note your
                      EXIT is recorded but mechanically capped to TIGHTEN until
                      this model earns interrupt authority on its track record.
- invalidators must be specific and checkable, never vague.
- classify each headline that matters into the evidence taxonomy; drop noise.
- If the packet says data is missing or stale, say ABSTAIN with the honest
  reason rather than manufacturing a view. That is the failure mode."""


# ------------------------------------------------------------- persistence

def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(obj, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    """Every line parses or the load fails. A corrupt line in an append-only
    audit log is data loss to investigate, not a row to skip."""
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.open(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise OperatorError(f"{path.name}:{i} is not JSON ({e}) — "
                                "refusing to skip a corrupt audit line") from e
    return out


def load_ledger() -> ForecastLedger:
    """Rebuild the 017 ledger by replaying the on-disk log. The in-memory
    modules stay untouched; persistence is the operator's job (spec 023)."""
    led = ForecastLedger()
    for row in _read_jsonl(FC_LOG):
        kind = row.get("kind")
        body = {k: v for k, v in row.items() if k != "kind"}
        if kind == "forecast":
            body["scenario_probs"] = dict(body["scenario_probs"])
            body["evidence_ids"] = tuple(body.get("evidence_ids") or ())
            led.record(Forecast(**body))
        elif kind == "resolution":
            led.resolve(Resolution(**body))
        else:
            raise OperatorError(f"unknown row kind {kind!r} in {FC_LOG.name}")
    return led


def load_evidence_store() -> EvidenceStore:
    store = EvidenceStore()
    for row in _read_jsonl(EV_LOG):
        kind = row.get("kind")
        body = {k: v for k, v in row.items() if k != "kind"}
        if kind == "evidence":
            body["symbols"] = tuple(body.get("symbols") or ())
            store.add(Evidence(**body))
        elif kind == "state":
            store.advance_state(body["dup_group"], body["new_state"])
        else:
            raise OperatorError(f"unknown row kind {kind!r} in {EV_LOG.name}")
    return store


def _load_state() -> dict:
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text())


def _save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True))
    tmp.replace(STATE)


# ---------------------------------------------------------------- triggers

def _latest_bar_ts(symbol: str, *, refresh: bool) -> Optional[str]:
    import pandas as pd
    path = bars_mod._cache_path(symbol, "5m")
    if refresh:
        try:
            bars_mod.refresh_cache(symbol, "5m")
        except Exception as e:  # vendor down is a disabled trigger, said loudly
            print(f"  !! bar refresh failed ({e}) — bar/level triggers see the cache only")
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.index.max().isoformat()


def _last_two_closes(symbol: str) -> Optional[tuple[float, float]]:
    import pandas as pd
    path = bars_mod._cache_path(symbol, "5m")
    if not path.exists():
        return None
    df = pd.read_parquet(path).sort_index()
    if len(df) < 2:
        return None
    return float(df["Close"].iloc[-2]), float(df["Close"].iloc[-1])


def _plan_levels(plan: dict) -> list[float]:
    lv = [plan.get(k) for k in ("entry", "sl", "tp1", "tp2")]
    return [float(x) for x in lv if x is not None]


def _event_line_count() -> int:
    return sum(sum(1 for _ in p.open()) for p in sorted(OUT.glob("events*.jsonl")))


def check_triggers(symbol: str, st: dict, *, refresh: bool) -> list[tuple[str, str]]:
    """Every deterministic reason Claude might need to run, priority order.
    An unchanged world returns [] and costs nothing."""
    fired: list[tuple[str, str]] = []
    now_et = datetime.now(ET)

    n_events = _event_line_count()
    if n_events != st.get("event_lines", 0):
        fired.append(("position", f"event stream {st.get('event_lines', 0)} -> {n_events} lines"))

    if os.environ.get("POLYGON_API_KEY"):
        from polygon_news import fetch_headlines
        heads = fetch_headlines(symbol)
        fp = news_claude.headline_fingerprint(heads)
        if fp != st.get("news_fp"):
            fired.append(("news", f"headline set changed ({len(heads)} headlines)"))
    else:
        print("  !! POLYGON_API_KEY not set — news trigger DISABLED (spec 023 rule 3)")

    bar_ts = _latest_bar_ts(symbol, refresh=refresh)
    if bar_ts is not None:
        if PLAN.exists():
            closes = _last_two_closes(symbol)
            if closes:
                prev, last = closes
                for lv in _plan_levels(json.loads(PLAN.read_text())):
                    if (prev - lv) * (last - lv) < 0:
                        fired.append(("level", f"close crossed {lv} ({prev} -> {last})"))
                        break
        if bar_ts != st.get("bar_ts"):
            fired.append(("bar", f"new 5m bar {bar_ts}"))

    if now_et.strftime("%H:%M") < "09:30" and st.get("premarket_date") != str(now_et.date()):
        fired.append(("premarket", f"first run of {now_et.date()} before the open"))

    prio = {"position": 0, "news": 1, "level": 2, "bar": 3, "premarket": 4}
    return sorted(fired, key=lambda t: prio[t[0]])


def _mark_state(symbol: str, st: dict) -> dict:
    """Advance trigger dedup state to the world just observed."""
    st["event_lines"] = _event_line_count()
    if os.environ.get("POLYGON_API_KEY"):
        from polygon_news import fetch_headlines
        st["news_fp"] = news_claude.headline_fingerprint(fetch_headlines(symbol))
    ts = _latest_bar_ts(symbol, refresh=False)
    if ts:
        st["bar_ts"] = ts
    now_et = datetime.now(ET)
    if now_et.strftime("%H:%M") < "09:30":
        st["premarket_date"] = str(now_et.date())
    return st


# ------------------------------------------------------------ the packet

def build_packet(symbol: str) -> tuple[str, list[dict], dict]:
    """Everything Claude sees: recent tape, the plan, position events,
    headlines. Missing pieces are NAMED as missing, never papered over —
    the model is instructed to abstain on bad data, so it must see the truth."""
    import pandas as pd
    lines: list[str] = []
    meta: dict = {"bar_age_min": None}

    path = bars_mod._cache_path(symbol, "5m")
    if path.exists():
        df = pd.read_parquet(path).sort_index().tail(24)
        meta["bar_age_min"] = round(
            (_utcnow() - df.index.max().tz_convert(timezone.utc)).total_seconds() / 60, 1)
        lines.append(f"RECENT 5M BARS (last {len(df)}, newest bar "
                     f"{meta['bar_age_min']} min old):")
        for ts, r in df.iterrows():
            lines.append(f"  {ts.strftime('%m-%d %H:%M')} O {r.Open:.2f} H {r.High:.2f} "
                         f"L {r.Low:.2f} C {r.Close:.2f} V {int(r.Volume)}")
    else:
        lines.append("RECENT BARS: NONE CACHED — price context is missing")

    if PLAN.exists():
        plan = json.loads(PLAN.read_text())
        lines.append(f"\nBASELINE PLAN: {json.dumps({k: plan.get(k) for k in ('symbol', 'direction', 'entry', 'sl', 'tp1', 'tp2', 'qty')})}")
    else:
        lines.append("\nBASELINE PLAN: none on file")

    ev_files = sorted(OUT.glob("events*.jsonl"))
    if ev_files:
        tail = _read_jsonl(ev_files[-1])[-5:]
        lines.append("\nRECENT POSITION EVENTS:")
        for e in tail:
            lines.append(f"  {json.dumps(e)[:200]}")
    else:
        lines.append("\nRECENT POSITION EVENTS: none")

    heads: list[dict] = []
    if os.environ.get("POLYGON_API_KEY"):
        from polygon_news import fetch_headlines
        heads = fetch_headlines(symbol)
        lines.append(f"\nHEADLINES ({len(heads)}):")
        for h in heads:
            lines.append(f"  [{h.get('published_utc', '')[:16]}] {h.get('publisher', '')}: "
                         f"{h.get('title', '')}")
    else:
        lines.append("\nHEADLINES: UNAVAILABLE (no POLYGON_API_KEY) — news context is missing")

    return "\n".join(lines), heads, meta


# ----------------------------------------------------- read -> typed outputs

def _scenario_probs(read: OperatorRead) -> dict[str, float]:
    """Map explicit fields onto spec 019's vocabulary. LLM float drift within
    tolerance is renormalised DETERMINISTICALLY and visibly; beyond it, raise."""
    probs = {"bull_continuation": read.prob_bull_continuation,
             "bear_continuation": read.prob_bear_continuation,
             "range_consolidation": read.prob_range_consolidation,
             "failed_breakout": read.prob_failed_breakout,
             "risk_event": read.prob_risk_event}
    if set(probs) != set(ALL_SCENARIOS):
        raise OperatorError(f"scenario vocabulary drifted: {sorted(probs)} vs "
                            f"{sorted(ALL_SCENARIOS)}")
    total = sum(probs.values())
    if abs(total - 1.0) > PROB_RENORM_TOL:
        raise OperatorError(f"scenario probs sum to {total:.4f} — outside the "
                            f"±{PROB_RENORM_TOL} renorm tolerance, refusing")
    if total <= 0:
        raise OperatorError("scenario probs sum to zero")
    if abs(total - 1.0) > 1e-9:
        print(f"  probs summed to {total:.4f} — renormalised (within tolerance)")
        probs = {k: v / total for k, v in probs.items()}
    return probs


def _make_evidence(read: OperatorRead, symbol: str, now: datetime,
                   record_id: str) -> list[Evidence]:
    out = []
    for i, item in enumerate(read.evidence):
        out.append(Evidence(
            evidence_id=f"{record_id}-ev{i}",
            evidence_type=item.evidence_type,
            scope=item.scope,
            symbols=(symbol,) if item.scope in ("symbol", "trade") else (symbol,),
            headline=item.headline,
            source="operator:model-classified",
            source_time=_iso(now), first_seen=_iso(now),
            severity=item.severity, direction=item.direction,
            reliability=None, reliability_basis="unrated"))
    return out


def _make_forecast(read: OperatorRead, symbol: str, now: datetime,
                   record_id: str, model: str,
                   evidence_ids: tuple[str, ...]) -> Forecast:
    return Forecast(
        forecast_id=f"fc-{record_id}",
        model_version=f"{MODEL_VERSION_BASE}/{model}",
        prompt_version=PROMPT_VERSION,
        as_of=_iso(now), symbol=symbol,
        horizon_min=max(15, min(240, read.horizon_min)),
        scenario_probs=_scenario_probs(read),
        direction=read.direction,
        recommendation=None,
        interrupt="TIGHTEN" if read.verdict in ("TIGHTEN", "EXIT") else None,
        confidence=read.confidence,
        evidence_ids=evidence_ids)


def _make_directive(read: OperatorRead, symbol: str, now: datetime,
                    record_id: str, model: str,
                    evidence_ids: list[str]) -> tuple[Optional[ContextDirective], Optional[str]]:
    """Verdict -> bounded directive. Returns (directive, suppressed_to).
    The interrupt is ALWAYS TIGHTEN at authority 1 — spec 023 ruling 2. An EXIT
    judgment is sealed verbatim elsewhere; here it is capped, and the cap is
    itself recorded."""
    if read.verdict in ("ABSTAIN", "ALLOW_BASELINE"):
        return None, None
    suppressed = "TIGHTEN" if read.verdict == "EXIT" else None
    ttl = max(5, min(120, read.expires_min))
    d = ContextDirective(
        directive_id=f"dir-{record_id}",
        scope=Scope(symbols=[symbol]),
        issued_at=_iso(now),
        expires_at=_iso(now + timedelta(minutes=ttl)),
        model_version=f"{MODEL_VERSION_BASE}/{model}",
        authority_level=1,
        interrupt="TIGHTEN",
        confidence=read.confidence,
        evidence_ids=list(evidence_ids))
    # The round-trip is the proof: what lands in the file is exactly what the
    # 018 contract accepts, or nothing lands at all.
    ContextDirective.from_dict(d.to_dict())
    return d, suppressed


def _write_directive(d: ContextDirective) -> None:
    """Append to the runner's list, atomically. Expired history is ignored by
    evaluate(), never deleted — history is what a scorecard grades."""
    existing = []
    if DIRECTIVES.exists():
        existing = json.loads(DIRECTIVES.read_text())
        if not isinstance(existing, list):
            raise OperatorError(f"{DIRECTIVES.name} is not a list — refusing to overwrite")
    existing.append(d.to_dict())
    tmp = DIRECTIVES.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, indent=1))
    tmp.replace(DIRECTIVES)


# ----------------------------------------------------------------- run once

def run_once(symbol: str, *, cap: float, model: str,
             force: Optional[str] = None, refresh: bool = True) -> int:
    st = _load_state()
    if force:
        fired = [(force, "forced by --force")]
    else:
        fired = check_triggers(symbol, st, refresh=refresh)
    if not fired:
        print("  no trigger fired — no API call, no cost (spec 023 I5)")
        return 0
    trigger, detail = fired[0]
    print(f"  trigger: {trigger} — {detail}")

    packet, heads, meta = build_packet(symbol)
    now = _utcnow()
    # Microseconds: two triggers inside the same wall-clock second must not
    # mint the same id — the 017 ledger rightly refuses a duplicated claim.
    record_id = f"op-{now.strftime('%Y%m%d-%H%M%S-%f')}-{symbol}"

    system = [{"type": "text", "text": OPERATOR_SYSTEM,
               "cache_control": {"type": "ephemeral"}}]
    user = [{"type": "text", "text":
             f"Symbol: {symbol}\nTrigger: {trigger} ({detail})\n"
             f"Now: {datetime.now(ET).strftime('%a %Y-%m-%d %H:%M ET')}\n\n{packet}"}]

    read, cost = news_claude._call(system, user, OperatorRead, model=model,
                                   cap=cap, effort="medium", kind="operator")

    if read.verdict == "ABSTAIN" and read.abstain_reason is None:
        raise OperatorError("verdict ABSTAIN with no abstain_reason — "
                            "anonymous silence cannot be scored")

    evs = _make_evidence(read, symbol, now, record_id)
    store = load_evidence_store()
    for ev in evs:
        store.add(ev)
        _append_jsonl(EV_LOG, {"kind": "evidence", **ev.to_dict()})
    ev_ids = tuple(ev.evidence_id for ev in evs)

    fc = _make_forecast(read, symbol, now, record_id, model, ev_ids)
    ledger = load_ledger()
    ledger.record(fc)                       # validates against the 017 contract
    _append_jsonl(FC_LOG, {"kind": "forecast", **fc.to_dict()})

    directive, suppressed = _make_directive(read, symbol, now, record_id,
                                            model, list(ev_ids))
    abst = None
    if read.verdict == "ABSTAIN":
        abst = Abstention(model_version=f"{MODEL_VERSION_BASE}/{model}",
                          reason=read.abstain_reason,
                          detail=read.base, issued_at=_iso(now))

    # SEAL FIRST (spec 023 I3): the record hits disk before the directive is
    # observable by the runner.
    _append_jsonl(RECORDS, {
        "record_id": record_id, "ts": _iso(now), "trigger": trigger,
        "symbol": symbol, "model_version": f"{MODEL_VERSION_BASE}/{model}",
        "prompt_version": PROMPT_VERSION, "forecast_id": fc.forecast_id,
        "trade_id": None, "evidence_ids": list(ev_ids),
        "both_sides": {"bull": read.bull, "base": read.base, "bear": read.bear},
        "invalidators": read.invalidators, "verdict": read.verdict,
        "confidence": read.confidence,
        "expires_at": _iso(now + timedelta(minutes=max(5, min(120, read.expires_min)))),
        "suppressed_to": suppressed,
        "directive": directive.to_dict() if directive else None,
        "abstention": abst.to_dict() if abst else None,
        "bar_age_min": meta.get("bar_age_min"), "cost_usd": round(cost, 8)})

    if directive:
        _write_directive(directive)
        note = f" (EXIT suppressed to TIGHTEN — unpromoted)" if suppressed else ""
        print(f"  directive {directive.directive_id} written{note}")

    _save_state(_mark_state(symbol, st))
    print(f"\n  VERDICT: {read.verdict} ({read.confidence:.2f})   direction {read.direction}"
          f"   horizon {read.horizon_min}m")
    print(f"  bull: {read.bull}\n  base: {read.base}\n  bear: {read.bear}")
    for x in read.invalidators:
        print(f"  invalidator: {x}")
    return 0


# ---------------------------------------------------------------- resolver

def _bars_between(symbol: str, start: datetime, end: datetime):
    import pandas as pd
    path = bars_mod._cache_path(symbol, "5m")
    if not path.exists():
        return None
    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index, utc=True)
    return df[(df.index >= start) & (df.index <= end)]


def _outcome(symbol: str, f: Forecast, window) -> tuple[str, str, bool]:
    """Deterministic outcome mapping, spec 023. Direction from net move vs the
    flat band; shock from true-range expansion; scenario from both."""
    import pandas as pd
    start_close = float(window["Close"].iloc[0])
    end_close = float(window["Close"].iloc[-1])
    net = (end_close - start_close) / start_close

    full = _bars_between(symbol, _aware(f.as_of) - timedelta(minutes=20 * 5 + f.horizon_min),
                         _aware(f.as_of))
    prior_tr = None
    if full is not None and len(full) >= 5:
        tr = (full["High"] - full["Low"]).abs()
        prior_tr = float(tr.median())
    win_tr = (window["High"] - window["Low"]).abs()
    shock = bool(prior_tr and float(win_tr.max()) > SHOCK_TR_MULT * prior_tr)

    if net > FLAT_BAND:
        direction = "up"
    elif net < -FLAT_BAND:
        direction = "down"
    else:
        direction = "flat"

    hi = float(window["High"].max())
    lo = float(window["Low"].min())
    broke_up = hi > start_close * (1 + FLAT_BAND)
    broke_dn = lo < start_close * (1 - FLAT_BAND)
    if shock:
        scenario = "risk_event"
    elif direction == "flat" and (broke_up or broke_dn):
        scenario = "failed_breakout"
    elif direction == "up":
        scenario = "bull_continuation"
    elif direction == "down":
        scenario = "bear_continuation"
    else:
        scenario = "range_consolidation"
    return scenario, direction, shock


def _aware(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def resolve_open(now: Optional[datetime] = None) -> int:
    """Close every open forecast whose horizon has passed. Missing bars leave
    the forecast open with a printed reason — nothing resolves partially."""
    now = now or _utcnow()
    ledger = load_ledger()
    records = {r["forecast_id"]: r for r in _read_jsonl(RECORDS)}
    rows = _read_jsonl(FC_LOG)
    open_ids = ({r["forecast_id"] for r in rows if r["kind"] == "forecast"}
                - {r["forecast_id"] for r in rows if r["kind"] == "resolution"})
    n = 0
    for fid in sorted(open_ids):
        f = ledger.forecast(fid)
        horizon_end = _aware(f.as_of) + timedelta(minutes=f.horizon_min)
        if now < horizon_end:
            continue
        window = _bars_between(f.symbol, _aware(f.as_of), horizon_end)
        if window is None or len(window) < 2:
            print(f"  {fid}: bars missing for [{f.as_of} +{f.horizon_min}m] — stays OPEN")
            continue
        scenario, direction, shock = _outcome(f.symbol, f, window)
        rec = records.get(fid, {})
        was_stale = (rec.get("bar_age_min") is not None
                     and rec["bar_age_min"] > STALE_BAR_MIN)
        r = Resolution(forecast_id=fid, resolved_at=_iso(now),
                       outcome_scenario=scenario, outcome_direction=direction,
                       shock_occurred=shock, was_stale=was_stale)
        ledger.resolve(r)                   # 017 refuses early resolution
        _append_jsonl(FC_LOG, {"kind": "resolution", **r.__dict__})
        hit = "HIT" if direction == f.direction else "miss"
        print(f"  {fid}: {scenario} / {direction} ({hit})"
              + (" SHOCK" if shock else ""))
        n += 1
    print(f"  resolved {n} forecast(s); {len(open_ids) - n} still open")
    return 0


def grade(model: str) -> int:
    ledger = load_ledger()
    try:
        rep = ledger.grade(f"{MODEL_VERSION_BASE}/{model}", oos=False)
    except ForecastError as e:
        print(f"  {e}")
        return 1
    for k, v in rep.to_dict().items():
        print(f"  {k:24s} {v}")
    return 0


# --------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 023 — the AlphaZero operator")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="evaluate triggers; one sealed judgment if any fired")
    run.add_argument("--symbol", default="NVDA")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--cap", type=float, default=news_claude.DEFAULT_CAP_USD)
    run.add_argument("--force", choices=("premarket", "bar", "level", "news", "position"),
                     help="run even if nothing changed, attributing to this trigger")
    run.add_argument("--no-refresh", action="store_true",
                     help="skip the bar-cache refresh (offline / test)")

    res = sub.add_parser("resolve", help="close open forecasts whose horizon passed")
    grd = sub.add_parser("grade", help="score the operator against the 017 gates")
    grd.add_argument("--model", default=DEFAULT_MODEL)

    a = ap.parse_args(argv)
    if a.cmd == "run":
        try:
            return run_once(a.symbol, cap=a.cap, model=a.model, force=a.force,
                            refresh=not a.no_refresh)
        except news_claude.SpendCapReached as e:
            print(f"  !! SPEND CAP: {e}")
            return 1
    if a.cmd == "resolve":
        return resolve_open()
    if a.cmd == "grade":
        return grade(a.model)
    return 2


if __name__ == "__main__":
    sys.exit(main())
