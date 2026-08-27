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
from stockfish_exit import _satisfiable                                   # noqa: E402
from shadow import run_shadow                                             # noqa: E402
import regret as regret_mod                                               # noqa: E402

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

# The shadow tournament regret is graded against (spec 014). Deliberately the
# three policies that are both auto-emittable and satisfiable by any plan with
# no extra fields (unlike EVENT, which needs flatten_at_et, and SALVAGE /
# SCRATCH_FAST, which stockfish_exit.NOT_AUTO_EMITTABLE reserves for a signal
# that does not exist yet) — DEFEND and RIDE are the pair the 2026-08-03
# ceiling measurement's own comment names as the ones that genuinely diverge.
SHADOW_TOURNAMENT_POLICIES = ("DEFEND", "HARVEST", "RIDE")
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_HORIZON_MIN = 60
DIRECTIVE_TTL_MIN = 45
FLAT_BAND = 0.0015                 # ±0.15% net move counts as flat
SHOCK_TR_MULT = 3.0                # bar TR > 3x prior-20 median TR == shock
STALE_BAR_MIN = 15                 # newest bar older than this at call time
PROB_RENORM_TOL = 0.05             # LLM float drift we renormalise; beyond, raise

# ---- spec 024 pre-committed constants. Sealed in the spec card; changing one
# after data has been seen is the exact failure the discipline layer exists
# to prevent.
# INTERRUPT CHANNEL RETIRED 2026-08-17 (spec 026 addendum). The competence
# study measured perfect-hindsight tighten at ~0 uplift in every cell and
# perfect exit at ~0 in 11/12 — a channel with a zero ceiling is strictly
# negative under imperfect use. Judgments are still sealed in full (that is
# the training data); directives are constructed and recorded but NOT written
# to the runner's file. Do not rediscover this as a good idea: re-arming
# requires new evidence that clears the same competence harness this failed.
# Tests exercise the live path by overriding this to "live".
EMISSION_MODE = "log-only"

MAX_DIRECTIVES_PER_SESSION = 3
CODRIFT_N = 2
CODRIFT_WINDOW_MIN = 30
GROUPS_FILE = OPDIR / "correlated_groups.json"
LIMITS_FILE = OPDIR / "limits.json"

VERDICTS = ("ABSTAIN", "ALLOW_BASELINE", "TIGHTEN", "EXIT")


class OperatorError(RuntimeError):
    """Malformed model output or corrupt persistence. Never repaired silently."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ------------------------------------------------------- RTH tradability gate
#
# spec 024 review (2026-08-2x): 4 forecasts stuck OPEN forever because their
# [as_of, as_of+horizon] window never had 2 bars to resolve against —
# NVDA_5m.parquet is RTH-only by design (bars.py), so a window issued
# overnight or one that runs past the close simply never accrues data. This
# is not a bars-fetching bug; it is an unscoreable claim that should never
# have been minted. Gated here at EMISSION (see _seal_and_emit) and reused at
# RESOLUTION (see resolve_open) to retire the already-stuck rows.
#
# The session bounds below are bars.py's OWN constants (RTH_OPEN/RTH_CLOSE) —
# no new calendar is invented and no hour is hardcoded here. Weekday-only is
# the same trading-day heuristic operator_tick.sh's own guard already uses
# (ET_DOW>5 excluded, operator_tick.sh:11-15); this module does not have,
# and does not attempt to build, a holiday calendar.
MIN_BAR_INTERVAL_MIN = 5            # the "5m" cache everywhere in this module


def _rth_session_bounds(day_et) -> "Optional[tuple[datetime, datetime]]":
    """RTH open/close as tz-aware ET datetimes for one ET calendar date, or
    None if that date is not a trading day at all (weekend)."""
    if day_et.weekday() >= 5:                       # Saturday=5, Sunday=6
        return None
    oh, om = (int(x) for x in bars_mod.RTH_OPEN.split(":"))
    ch, cm = (int(x) for x in bars_mod.RTH_CLOSE.split(":"))
    open_dt = datetime(day_et.year, day_et.month, day_et.day, oh, om, tzinfo=ET)
    close_dt = datetime(day_et.year, day_et.month, day_et.day, ch, cm, tzinfo=ET)
    return open_dt, close_dt


def _rth_overlap_minutes(start: datetime, end: datetime) -> float:
    """Minutes of [start, end] that fall inside the RTH session on START's
    own ET calendar date. Zero on a non-trading day, or a window that never
    touches that day's session at all (e.g. issued after the close, or
    entirely overnight)."""
    start_et = start.astimezone(ET)
    end_et = end.astimezone(ET)
    bounds = _rth_session_bounds(start_et.date())
    if bounds is None:
        return 0.0
    session_open, session_close = bounds
    overlap_start = max(start_et, session_open)
    overlap_end = min(end_et, session_close)
    return max(0.0, (overlap_end - overlap_start).total_seconds() / 60.0)


def _tradable_forecast_window(as_of: datetime, horizon_min: int) -> dict:
    """Decide whether [as_of, as_of+horizon_min] is a scoreable claim.

    Returns one of:
      {"ok": True,  "horizon_min": horizon_min, "clamped_from": None}
        — the window lies wholly inside a tradable RTH session.
      {"ok": True,  "horizon_min": <shrunk>, "clamped_from": horizon_min}
        — the window crossed the session close; horizon_min is clamped to
          end exactly at the close. Still emitted, just not a free claim
          past hours the market was never open.
      {"ok": False, "reason": "window has no tradable session"}
        — zero RTH minutes anywhere in the window (issued overnight, on a
          weekend, or entirely after that day's close). Not emittable.
    """
    horizon_end = as_of + timedelta(minutes=horizon_min)
    overlap_min = _rth_overlap_minutes(as_of, horizon_end)
    if overlap_min <= 0:
        return {"ok": False, "reason": "window has no tradable session"}
    session_close = _rth_session_bounds(as_of.astimezone(ET).date())[1]
    if horizon_end.astimezone(ET) > session_close:
        clamped = max(1, int((session_close - as_of.astimezone(ET))
                             .total_seconds() // 60))
        return {"ok": True, "horizon_min": clamped, "clamped_from": horizon_min}
    return {"ok": True, "horizon_min": horizon_min, "clamped_from": None}


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


class InvalidationPredicate(BaseModel):
    """One machine-checkable invalidation, closed vocabulary (spec 024 I13).
    close_below/close_above: a 5m close beyond `value` (a price).
    range_exceeds_r: window true range exceeds `value` (in R, needs plan risk).
    """
    kind: Literal["close_below", "close_above", "range_exceeds_r"]
    value: float


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
    horizon_min: int = Field(ge=15, le=240,
                             description="minutes until this forecast can be judged, 15-240")
    verdict: Literal["ABSTAIN", "ALLOW_BASELINE", "TIGHTEN", "EXIT"]
    abstain_reason: Optional[Literal["NO_OPINION", "LOW_CONFIDENCE",
                                     "CONFLICTING_EVIDENCE", "STALE_CONTEXT",
                                     "DATA_INCOMPLETE"]] = Field(
        default=None, description="required iff verdict is ABSTAIN")
    confidence: float = Field(ge=0.0, le=1.0,
                              description="0..1, calibrated — this is scored against baselines")
    expires_min: int = Field(ge=5, le=120,
                             description="minutes any resulting directive stays valid, 5-120")
    evidence: list[EvidenceItem] = Field(description="the headlines that actually matter, classified")
    expected_r_low: Optional[float] = Field(
        default=None, ge=-5, le=5,
        description="lower bound of the expected R outcome for the baseline setup over the horizon; REQUIRED unless verdict is ABSTAIN")
    expected_r_high: Optional[float] = Field(
        default=None, ge=-5, le=5,
        description="upper bound of the expected R outcome; REQUIRED unless verdict is ABSTAIN")
    invalidation_predicates: list[InvalidationPredicate] = Field(
        default_factory=list,
        description="machine-checkable invalidations (price levels / R ranges); the scoreable subset of your invalidators")


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
- PRE-REGISTER your number: unless you ABSTAIN, you MUST give expected_r_low
  and expected_r_high — the R-multiple band you expect the baseline setup to
  realize over your horizon. This band is sealed before the outcome and scored
  against what actually happened. Give the honest band, not a wide one: a band
  that always contains the outcome scores as an uninformative forecast.
- invalidation_predicates: express every invalidator that IS a price level or
  range as a typed predicate (close_below/close_above with the price,
  range_exceeds_r with the R multiple) so it can be auto-checked. Keep the
  prose version too.
- classify each headline that matters into the evidence taxonomy; drop noise.
- If the packet says data is missing or stale, say ABSTAIN with the honest
  reason rather than manufacturing a view. That is the failure mode.
- ALWAYS classify at least one evidence item when ANY headline or market
  observation informed your read — including for ABSTAIN. An abstention
  whose reasons cite no evidence cannot be audited, and unexplained
  judgments are excluded from promotion (explainability gate, 95%). Only a
  packet with literally nothing decision-relevant justifies empty evidence."""


# ------------------------------------------------------------- persistence

def _append_jsonl(path: Path, obj: dict) -> None:
    """Append + fsync. These are audit logs; a row the OS buffered but never
    flushed is a row that never happened at the worst possible moment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(obj, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


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
        elif kind == "unresolvable":
            led.mark_unresolvable(body["forecast_id"], body["reason"],
                                  at=body["at"])
        elif kind == "prereg_score":
            pass                       # spec 024: scored pre-registration rows
                                       # live beside the ledger, not inside 017
        elif kind == "regret":
            pass                       # policy_regret_r's WHY; travels beside the
                                       # ledger, same as prereg_score — Resolution
                                       # already carries the graded value itself
        else:
            raise OperatorError(f"unknown row kind {kind!r} in {FC_LOG.name}")
    return led


def _reconcile_derived() -> int:
    """Crash recovery: the sealed record is the transaction; EV_LOG and FC_LOG
    are derived views. Any record whose forecast/evidence rows never landed
    (crash between the seal and the derived appends) gets them re-derived here.
    Idempotent; returns the number of rows recovered."""
    recovered = 0
    have_fc = {r["forecast_id"] for r in _read_jsonl(FC_LOG) if r["kind"] == "forecast"}
    have_ev = {r["evidence_id"] for r in _read_jsonl(EV_LOG) if r["kind"] == "evidence"}
    for rec in _read_jsonl(RECORDS):
        fc = rec.get("forecast")
        if fc and fc["forecast_id"] not in have_fc:
            _append_jsonl(FC_LOG, {"kind": "forecast", **fc})
            have_fc.add(fc["forecast_id"])
            recovered += 1
        for ev in rec.get("evidence") or []:
            if ev["evidence_id"] not in have_ev:
                _append_jsonl(EV_LOG, {"kind": "evidence", **ev})
                have_ev.add(ev["evidence_id"])
                recovered += 1
    if recovered:
        print(f"  reconciled {recovered} derived row(s) from sealed records "
              "(crash recovery — spec 023 seal-is-the-transaction)")
    return recovered


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
    try:
        return json.loads(STATE.read_text())
    except json.JSONDecodeError as e:
        raise OperatorError(f"{STATE.name} is not JSON ({e}) — refusing to skip "
                            "a corrupt audit line") from e


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


def _event_files(symbol: str) -> list[Path]:
    """The runner writes events_{session_date}_{symbol}.jsonl — scope to THIS
    symbol so a second symbol's session cannot fire our position trigger or
    leak into our packet (multi-symbol review fix)."""
    return sorted(OUT.glob(f"events_*_{symbol}.jsonl"))


def _event_line_count(symbol: str) -> int:
    return sum(sum(1 for _ in p.open()) for p in _event_files(symbol))


def check_triggers(symbol: str, st: dict, *, refresh: bool
                   ) -> tuple[list[tuple[str, str]], dict]:
    """Every deterministic reason Claude might need to run, priority order,
    plus a SNAPSHOT of the world as observed. The snapshot — not a later
    re-observation — is what advances the dedup state, so an event arriving
    between observation and judgment cannot be marked seen-but-never-shown
    (Cato finding 3). An unchanged world returns ([], snapshot), costs nothing."""
    fired: list[tuple[str, str]] = []
    snapshot: dict = {}
    now_et = datetime.now(ET)

    n_events = _event_line_count(symbol)
    snapshot["event_lines"] = n_events
    if n_events != st.get("event_lines", 0):
        fired.append(("position", f"event stream {st.get('event_lines', 0)} -> {n_events} lines"))

    if os.environ.get("POLYGON_API_KEY"):
        from polygon_news import fetch_headlines
        heads = fetch_headlines(symbol)
        fp = news_claude.headline_fingerprint(heads)
        snapshot["news_fp"] = fp
        if fp != st.get("news_fp"):
            fired.append(("news", f"headline set changed ({len(heads)} headlines)"))
    else:
        print("  !! POLYGON_API_KEY not set — news trigger DISABLED (spec 023 rule 3)")

    bar_ts = _latest_bar_ts(symbol, refresh=refresh)
    if bar_ts is not None:
        snapshot["bar_ts"] = bar_ts
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

    if now_et.strftime("%H:%M") < "09:30":
        snapshot["premarket_date"] = str(now_et.date())
        if st.get("premarket_date") != str(now_et.date()):
            fired.append(("premarket", f"first run of {now_et.date()} before the open"))

    prio = {"position": 0, "news": 1, "level": 2, "bar": 3, "premarket": 4}
    return sorted(fired, key=lambda t: prio[t[0]]), snapshot


# ------------------------------------------------------------ the packet

def build_packet(symbol: str, *, as_of: Optional[datetime] = None
                 ) -> tuple[str, list[dict], dict]:
    """Everything Claude sees: recent tape, the plan, position events,
    headlines. Missing pieces are NAMED as missing, never papered over —
    the model is instructed to abstain on bad data, so it must see the truth.

    POINT-IN-TIME (spec 024 I14): nothing newer than `as_of` enters the
    packet — bars, headlines, and events are all filtered, and the maximum
    data timestamp actually included is returned in meta so the sealed record
    can prove it. Claude cannot evaluate a setup it has already seen resolve.
    """
    import pandas as pd
    as_of = as_of or _utcnow()
    lines: list[str] = []
    max_ts: Optional[datetime] = None
    meta: dict = {"bar_age_min": None, "as_of": _iso(as_of)}

    def _bump(ts: datetime) -> None:
        nonlocal max_ts
        if max_ts is None or ts > max_ts:
            max_ts = ts

    path = bars_mod._cache_path(symbol, "5m")
    if path.exists():
        df = pd.read_parquet(path).sort_index()
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[df.index <= as_of].tail(24)
        if len(df):
            newest = df.index.max().to_pydatetime()
            _bump(newest)
            meta["bar_age_min"] = round((as_of - newest).total_seconds() / 60, 1)
            lines.append(f"RECENT 5M BARS (last {len(df)}, newest bar "
                         f"{meta['bar_age_min']} min old):")
            for ts, r in df.iterrows():
                lines.append(f"  {ts.strftime('%m-%d %H:%M')} O {r.Open:.2f} H {r.High:.2f} "
                             f"L {r.Low:.2f} C {r.Close:.2f} V {int(r.Volume)}")
        else:
            lines.append("RECENT BARS: none at-or-before as_of — price context is missing")
    else:
        lines.append("RECENT BARS: NONE CACHED — price context is missing")

    if PLAN.exists():
        plan = json.loads(PLAN.read_text())
        lines.append(f"\nBASELINE PLAN: {json.dumps({k: plan.get(k) for k in ('symbol', 'direction', 'entry', 'sl', 'tp1', 'tp2', 'qty')})}")
    else:
        lines.append("\nBASELINE PLAN: none on file")

    ev_files = _event_files(symbol)
    ev_tail = []
    if ev_files:
        for e in _read_jsonl(ev_files[-1]):
            ts_raw = e.get("occurred_at") or e.get("ts")
            if ts_raw:
                try:
                    ts = _aware(ts_raw)
                except ValueError:
                    continue
                if ts > as_of:
                    continue
                _bump(ts)
            ev_tail.append(e)
    if ev_tail:
        lines.append("\nRECENT POSITION EVENTS:")
        for e in ev_tail[-5:]:
            lines.append(f"  {json.dumps(e)[:200]}")
    else:
        lines.append("\nRECENT POSITION EVENTS: none")

    heads: list[dict] = []
    if os.environ.get("POLYGON_API_KEY"):
        from polygon_news import fetch_headlines
        raw = fetch_headlines(symbol)
        dropped = 0
        for h in raw:
            pub = h.get("published_utc")
            if not pub:
                dropped += 1          # undated headline: excluded, counted, loud
                continue
            try:
                ts = _aware(pub)
            except ValueError:
                dropped += 1
                continue
            if ts > as_of:
                dropped += 1
                continue
            _bump(ts)
            heads.append(h)
        note = f" ({dropped} dropped: undated or after as_of)" if dropped else ""
        lines.append(f"\nHEADLINES ({len(heads)}){note}:")
        for h in heads:
            lines.append(f"  [{h.get('published_utc', '')[:16]}] {h.get('publisher', '')}: "
                         f"{h.get('title', '')}")
    else:
        lines.append("\nHEADLINES: UNAVAILABLE (no POLYGON_API_KEY) — news context is missing")

    # LOOP CLOSURE: hand the model its own scored track record in the same
    # breath it is asked again. Measuring a residual and filing it is not
    # learning; this is the return path. It refuses to state a rate it cannot
    # support, so a small sample teaches counts rather than false skill.
    try:
        from residual_model import prior_correction_text
        lines.append("\n" + prior_correction_text())
    except Exception as e:
        lines.append(f"\nYOUR TRACK RECORD: unavailable ({e})")

    meta["max_data_ts"] = _iso(max_ts) if max_ts else None
    return "\n".join(lines), heads, meta


# ------------------------------------------------ spec 024 discipline layer

def _pre_registration(read: OperatorRead) -> Optional[dict]:
    """The number, committed before it is seen (spec 024 I11). Non-ABSTAIN
    without a coherent R band is refused — a judgment that declines to state
    what it expects cannot be scored, only remembered as correct."""
    if read.verdict == "ABSTAIN":
        return None
    lo, hi = read.expected_r_low, read.expected_r_high
    if lo is None or hi is None:
        raise OperatorError(
            f"verdict {read.verdict} carries no expected R band — "
            "pre-registration is required for every non-ABSTAIN judgment")
    if lo > hi:
        raise OperatorError(f"expected R band inverted: [{lo}, {hi}]")
    return {"expected_r_low": lo, "expected_r_high": hi,
            "invalidation_predicates": [p.model_dump()
                                        for p in read.invalidation_predicates]}


def _session_directive_count(symbol: str, now: datetime) -> int:
    """Directives EMITTED for this symbol this ET day (refused ones excluded)."""
    day = now.astimezone(ET).date().isoformat()
    return sum(1 for r in _read_jsonl(RECORDS)
               if r["symbol"] == symbol and r.get("directive")
               and not r.get("shadow")      # shadow directives were never emitted
               and _aware(r["ts"]).astimezone(ET).date().isoformat() == day)


def _load_groups() -> dict:
    """Correlated groups, upstream-supplied (portfolio_guard's contract).
    Missing file = no groups = co-drift gate inert. Malformed = loud."""
    if not GROUPS_FILE.exists():
        return {}
    try:
        groups = json.loads(GROUPS_FILE.read_text())
    except json.JSONDecodeError as e:
        raise OperatorError(f"{GROUPS_FILE.name} is not JSON ({e})") from e
    if not isinstance(groups, dict):
        raise OperatorError(f"{GROUPS_FILE.name} must map group -> [symbols]")
    return groups


def check_emission_gate(symbol: str, now: datetime) -> Optional[dict]:
    """Deterministic pre-emission gates, in order (spec 024 I15/I16).
    Returns a refusal dict, or None to allow. The judgment is sealed either
    way — only the emission is gated."""
    n = _session_directive_count(symbol, now)
    if n >= MAX_DIRECTIVES_PER_SESSION:
        return {"gate": "session_cap",
                "detail": f"{n} directives already emitted for {symbol} today "
                          f"(cap {MAX_DIRECTIVES_PER_SESSION})"}

    groups = _load_groups()
    my_groups = [g for g, syms in groups.items() if symbol in syms]
    if my_groups:
        window_start = now - timedelta(minutes=CODRIFT_WINDOW_MIN)
        peers = {s for g in my_groups for s in groups[g]} - {symbol}
        recent = [r for r in _read_jsonl(RECORDS)
                  if r.get("directive") and not r.get("shadow")
                  and r["symbol"] in peers
                  and r["verdict"] in ("TIGHTEN", "EXIT")
                  and window_start <= _aware(r["ts"]) <= now]
        if len(recent) >= CODRIFT_N - 1:
            return {"gate": "codrift_pause",
                    "detail": f"{len(recent)} correlated interrupt(s) in the last "
                              f"{CODRIFT_WINDOW_MIN}m ({sorted({r['symbol'] for r in recent})}) — "
                              "N same-group interrupts are one decision with N× "
                              "the size; pausing, not averaging"}
    return None


def _portfolio_advisory() -> Optional[dict]:
    """portfolio_guard.check, ADVISORY ONLY (Gate 7 enforces last — I17).
    Needs limits.json plus a position picture; either missing = skipped with
    a printed reason, present-but-malformed = loud."""
    if not LIMITS_FILE.exists():
        return None
    from portfolio_guard import PortfolioLimits, PositionSnapshot, check, GuardError
    try:
        cfg = json.loads(LIMITS_FILE.read_text())
        limits = PortfolioLimits(**cfg["limits"])
        positions = [PositionSnapshot(**p) for p in cfg.get("positions", [])]
        verdict = check(limits, positions,
                        realized_today_r=float(cfg.get("realized_today_r", 0.0)),
                        correlated_groups=_load_groups() or None)
    except (KeyError, TypeError, ValueError, GuardError) as e:
        raise OperatorError(f"{LIMITS_FILE.name} present but unusable: {e}") from e
    return {"violations": [v.to_dict() for v in verdict.violations],
            "required_action": verdict.required_action, "why": verdict.why}


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
            # Market/sector stories name no symbol — storing them as
            # symbol-scoped would misrepresent provenance in the 015 store
            # (Cato finding 8). Delivery to this symbol then correctly runs
            # through relevant_to's index_linked rule, not through a claim
            # the evidence never made.
            symbols=(symbol,) if item.scope in ("symbol", "trade") else (),
            headline=item.headline,
            source="operator:model-classified",
            source_time=_iso(now), first_seen=_iso(now),
            severity=item.severity, direction=item.direction,
            reliability=None, reliability_basis="unrated"))
    return out


def _make_forecast(read: OperatorRead, symbol: str, now: datetime,
                   record_id: str, model: str,
                   evidence_ids: tuple[str, ...], *,
                   horizon_min: Optional[int] = None,
                   horizon_clamped_from: Optional[int] = None) -> Forecast:
    return Forecast(
        forecast_id=f"fc-{record_id}",
        model_version=f"{MODEL_VERSION_BASE}/{model}",
        prompt_version=PROMPT_VERSION,
        as_of=_iso(now), symbol=symbol,
        # bounds enforced by the schema; a violation raises rather than being
        # silently repaired (Cato finding 5). `horizon_min` may be a caller
        # override — the RTH tradability gate clamps a window that crosses
        # the session close to end exactly at that close (spec 024 review) —
        # `horizon_clamped_from` records the original, unclamped claim so the
        # grader can see it was not a free extra-hours minute.
        horizon_min=read.horizon_min if horizon_min is None else horizon_min,
        scenario_probs=_scenario_probs(read),
        direction=read.direction,
        recommendation=None,
        interrupt="TIGHTEN" if read.verdict in ("TIGHTEN", "EXIT") else None,
        confidence=read.confidence,
        evidence_ids=evidence_ids,
        horizon_clamped_from=horizon_clamped_from)


def _make_directive(read: OperatorRead, symbol: str, now: datetime,
                    record_id: str, model: str, evidence_ids: list[str],
                    ttl: int) -> tuple[Optional[ContextDirective], Optional[str]]:
    """Verdict -> bounded directive. Returns (directive, suppressed_to).
    The interrupt is ALWAYS TIGHTEN at authority 1 — spec 023 ruling 2. An EXIT
    judgment is sealed verbatim elsewhere; here it is capped, and the cap is
    itself recorded. `ttl` is computed ONCE by the caller so the directive's
    expiry and the sealed record's expires_at cannot desynchronise."""
    if read.verdict in ("ABSTAIN", "ALLOW_BASELINE"):
        return None, None
    suppressed = "TIGHTEN" if read.verdict == "EXIT" else None
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
    evaluate(), never deleted — history is what a scorecard grades.

    EVERY element — pre-existing included — must round-trip the 018 contract
    and sit at-or-below the unpromoted cap before this process will re-persist
    it. An over-authority payload already sitting in the file is refused, not
    laundered through under the operator's write (Cato finding 4)."""
    existing = []
    if DIRECTIVES.exists():
        try:
            existing = json.loads(DIRECTIVES.read_text())
        except json.JSONDecodeError as e:
            raise OperatorError(f"{DIRECTIVES.name} is not JSON ({e}) — "
                                "refusing to overwrite what I cannot read") from e
        if not isinstance(existing, list):
            raise OperatorError(f"{DIRECTIVES.name} is not a list — refusing to overwrite")
    existing.append(d.to_dict())
    for p in existing:
        cd = ContextDirective.from_dict(p)     # malformed -> raises, file untouched
        if cd.authority_level > 1 or cd.interrupt not in (None, "TIGHTEN",
                                                          "REDUCE_RISK"):
            raise OperatorError(
                f"directive {cd.directive_id!r} carries authority "
                f"{cd.authority_level}/interrupt {cd.interrupt!r} — above the "
                "unpromoted cap. The operator will not re-persist it.")
    tmp = DIRECTIVES.with_suffix(f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(existing, indent=1))
    tmp.replace(DIRECTIVES)


# ----------------------------------------------------------------- run once

def run_once(symbol: str, *, cap: float, model: str,
             force: Optional[str] = None, refresh: bool = True,
             shadow: bool = False) -> int:
    _reconcile_derived()
    st = _load_state()
    fired, snapshot = check_triggers(symbol, st, refresh=refresh)
    if force:
        fired = [(force, "forced by --force")]
    if not fired:
        print("  no trigger fired — no API call, no cost (spec 023 I5)")
        return 0
    trigger, detail = fired[0]
    print(f"  trigger: {trigger} — {detail}")

    now = _utcnow()                    # the decision timestamp — fixed BEFORE
    packet, heads, meta = build_packet(symbol, as_of=now)   # the packet (I14)
    # Microseconds: two triggers inside the same wall-clock second must not
    # mint the same id — the 017 ledger rightly refuses a duplicated claim.
    record_id = f"op-{now.strftime('%Y%m%d-%H%M%S-%f')}-{symbol}"

    # MECHANICAL STALE GATE: the prompt asks Claude to abstain on stale data,
    # but an instruction is not an enforcement. Bars missing or older than
    # STALE_BAR_MIN produce a sealed code-authored ABSTAIN(STALE_CONTEXT) with
    # no API call and no directive — the model never gets the chance to form a
    # view on data this desk would refuse to trade on.
    age = meta.get("bar_age_min")
    if age is None or age > STALE_BAR_MIN:
        why = ("no cached bars" if age is None
               else f"newest bar {age:.0f} min old (> {STALE_BAR_MIN})")
        abst = Abstention(model_version=f"{MODEL_VERSION_BASE}/stale-gate",
                          reason="STALE_CONTEXT", detail=why, issued_at=_iso(now))
        _append_jsonl(RECORDS, {
            "record_id": record_id, "ts": _iso(now), "trigger": trigger,
            "symbol": symbol, "model_version": f"{MODEL_VERSION_BASE}/stale-gate",
            "prompt_version": PROMPT_VERSION, "forecast_id": None,
            "trade_id": None, "evidence_ids": [],
            "both_sides": None, "invalidators": [],
            "verdict": "ABSTAIN", "confidence": 0.0,
            "expires_at": _iso(now), "suppressed_to": None,
            "directive": None, "abstention": abst.to_dict(),
            "forecast": None, "evidence": [],
            "pre_registration": None, "emission_refused": None,
            "portfolio_advisory": None, "shadow": shadow,
            "packet_as_of": meta.get("as_of"),
            "packet_max_data_ts": meta.get("max_data_ts"),
            "bar_age_min": age, "cost_usd": 0.0})
        _save_state({**st, **snapshot})
        print(f"  STALE GATE: {why} — sealed ABSTAIN(STALE_CONTEXT), "
              "no API call, no directive")
        return 0

    system = [{"type": "text", "text": OPERATOR_SYSTEM,
               "cache_control": {"type": "ephemeral"}}]
    user = [{"type": "text", "text":
             f"Symbol: {symbol}\nTrigger: {trigger} ({detail})\n"
             f"Now: {datetime.now(ET).strftime('%a %Y-%m-%d %H:%M ET')}\n\n{packet}"}]

    read, cost = news_claude._call(system, user, OperatorRead, model=model,
                                   cap=cap, effort="medium", kind="operator")
    try:
        return _seal_and_emit(read, cost, symbol, trigger, now, record_id,
                              model, meta, shadow=shadow)
    finally:
        # The call above was PRICED. Whatever happens to the judgment after —
        # a malformed response, a refused forecast — the observed-world
        # snapshot advances, so a persistently bad response costs one call,
        # never one per invocation (Cato finding 11).
        _save_state({**st, **snapshot})


def _seal_and_emit(read: "OperatorRead", cost: float, symbol: str, trigger: str,
                   now: datetime, record_id: str, model: str, meta: dict,
                   *, shadow: bool = False) -> int:
    if read.verdict == "ABSTAIN" and read.abstain_reason is None:
        raise OperatorError("verdict ABSTAIN with no abstain_reason — "
                            "anonymous silence cannot be scored")

    # VALIDATE EVERYTHING BEFORE WRITING ANYTHING (Cato finding 6): evidence,
    # forecast, directive, and abstention are all constructed — every contract
    # check has run — before the first byte hits an append-only log. A refused
    # judgment leaves no orphaned rows.
    ttl = read.expires_min                      # schema-bounded; computed once
    prereg = _pre_registration(read)            # I11: refused if band missing
    evs = _make_evidence(read, symbol, now, record_id)
    ev_ids = tuple(ev.evidence_id for ev in evs)

    # RTH TRADABILITY GATE (spec 024 review, 2026-08-2x): a forecast whose
    # [as_of, as_of+horizon] window never touches a tradable RTH session can
    # never accrue the 2 bars resolve_open() needs — it would sit OPEN
    # forever, not because bars are missing, but because they can never
    # exist. Gate here, at the moment a claim would be minted, rather than
    # discovering it at resolution: the judgment is still sealed below either
    # way, but a claim that cannot be scored is never emitted as one.
    window = _tradable_forecast_window(now, read.horizon_min)
    fc = None
    forecast_refused = None
    if window["ok"]:
        fc = _make_forecast(read, symbol, now, record_id, model, ev_ids,
                            horizon_min=window["horizon_min"],
                            horizon_clamped_from=window["clamped_from"])
        ledger = load_ledger()
        ledger.record(fc)                       # validates against the 017 contract
        if window["clamped_from"] is not None:
            print(f"  RTH GATE: horizon clamped {window['clamped_from']}m -> "
                  f"{window['horizon_min']}m — window crossed the session close")
    else:
        forecast_refused = {
            "reason": window["reason"],
            "as_of": _iso(now), "horizon_min": read.horizon_min,
            "detail": f"[{_iso(now)} +{read.horizon_min}m] never touches a "
                      "tradable RTH session"}
        print(f"  RTH GATE: forecast refused — {window['reason']} "
              f"[{_iso(now)} +{read.horizon_min}m]")
    directive, suppressed = _make_directive(read, symbol, now, record_id,
                                            model, list(ev_ids), ttl)

    # Deterministic emission gates (I15/I16) + advisory guards (I17), decided
    # BEFORE the seal so the record carries the refusal. Shadow mode (I22)
    # suppresses emission entirely; the gates still run so their verdicts soak.
    emission_refused = None
    if directive:
        emission_refused = check_emission_gate(symbol, now)
        if emission_refused:
            directive = None            # judgment sealed verbatim; emission gated
    advisory = _portfolio_advisory()
    abst = None
    if read.verdict == "ABSTAIN":
        abst = Abstention(model_version=f"{MODEL_VERSION_BASE}/{model}",
                          reason=read.abstain_reason,
                          detail=read.base, issued_at=_iso(now))

    # SEAL FIRST, DURABLY, WITH EVERYTHING (spec 023 I3 + crash-safety review):
    # the record is the single transaction. It embeds the full forecast and
    # evidence dicts, so a crash between this fsync'd write and the derived
    # EV_LOG/FC_LOG appends loses nothing — _reconcile_derived() rebuilds the
    # derived rows from the record on the next start. The directive is still
    # last: nothing is observable by the runner before the seal exists.
    _append_jsonl(RECORDS, {
        "record_id": record_id, "ts": _iso(now), "trigger": trigger,
        "symbol": symbol, "model_version": f"{MODEL_VERSION_BASE}/{model}",
        "prompt_version": PROMPT_VERSION,
        "forecast_id": fc.forecast_id if fc else None,
        "trade_id": None, "evidence_ids": list(ev_ids),
        "both_sides": {"bull": read.bull, "base": read.base, "bear": read.bear},
        "invalidators": read.invalidators, "verdict": read.verdict,
        "confidence": read.confidence,
        "expires_at": _iso(now + timedelta(minutes=ttl)),
        "suppressed_to": suppressed,
        "directive": directive.to_dict() if directive else None,
        "abstention": abst.to_dict() if abst else None,
        "forecast": fc.to_dict() if fc else None,
        "evidence": [ev.to_dict() for ev in evs],
        "pre_registration": prereg, "emission_refused": emission_refused,
        "forecast_refused": forecast_refused,
        "portfolio_advisory": advisory, "shadow": shadow,
        "packet_as_of": meta.get("as_of"),
        "packet_max_data_ts": meta.get("max_data_ts"),
        "bar_age_min": meta.get("bar_age_min"), "cost_usd": round(cost, 8)})

    store = load_evidence_store()
    for ev in evs:
        store.add(ev)
        _append_jsonl(EV_LOG, {"kind": "evidence", **ev.to_dict()})
    if fc is not None:
        _append_jsonl(FC_LOG, {"kind": "forecast", **fc.to_dict()})

    if directive and shadow:
        print(f"  SHADOW: directive {directive.directive_id} suppressed "
              "(soak mode — sealed, never emitted)")
    elif directive and EMISSION_MODE == "log-only":
        print(f"  CHANNEL RETIRED: directive {directive.directive_id} sealed, "
              "not emitted (spec 026 — interrupt ceiling measured at zero)")
    elif directive:
        _write_directive(directive)
        note = f" (EXIT suppressed to TIGHTEN — unpromoted)" if suppressed else ""
        print(f"  directive {directive.directive_id} written{note}")
    elif emission_refused:
        print(f"  EMISSION REFUSED [{emission_refused['gate']}]: "
              f"{emission_refused['detail']}")
    print(f"\n  VERDICT: {read.verdict} ({read.confidence:.2f})   direction {read.direction}"
          f"   horizon {read.horizon_min}m")
    print(f"  bull: {read.bull}\n  base: {read.base}\n  bear: {read.bear}")
    for x in read.invalidators:
        print(f"  invalidator: {x}")
    return 0


# ---------------------------------------------------------------- resolver

def _score_pre_registration(rec: dict, f: Forecast, window) -> Optional[dict]:
    """Score the pre-committed claim against what happened (spec 024 I13).
    Deterministic: same window, same verdicts. R uses the plan risk sealed at
    decision time; without it, r_in_band is null with the reason stated —
    never a defaulted 0."""
    prereg = rec.get("pre_registration")
    if not prereg:
        return None
    start_close = float(window["Close"].iloc[0])
    end_close = float(window["Close"].iloc[-1])

    r_in_band = None
    realized_r = None
    reason = None
    plan = json.loads(PLAN.read_text()) if PLAN.exists() else None
    if plan and plan.get("entry") is not None and plan.get("sl") is not None:
        risk = abs(float(plan["entry"]) - float(plan["sl"]))
        if risk > 0:
            d = {"long": 1, "short": -1}.get(str(plan.get("direction")).lower(),
                                             plan.get("direction"))
            d = d if d in (1, -1) else 1
            realized_r = (end_close - start_close) * d / risk
            r_in_band = bool(prereg["expected_r_low"] <= realized_r
                             <= prereg["expected_r_high"])
        else:
            reason = "plan risk is zero"
    else:
        reason = "no plan with entry/sl on file — R is undefined"

    fired = []
    hi = float(window["High"].max())
    lo = float(window["Low"].min())
    closes = window["Close"]
    for p in prereg.get("invalidation_predicates", []):
        kind, val = p["kind"], float(p["value"])
        if kind == "close_below":
            hit = bool((closes < val).any())
        elif kind == "close_above":
            hit = bool((closes > val).any())
        elif kind == "range_exceeds_r":
            if plan and plan.get("entry") is not None and plan.get("sl") is not None \
                    and abs(float(plan["entry"]) - float(plan["sl"])) > 0:
                risk = abs(float(plan["entry"]) - float(plan["sl"]))
                hit = bool((hi - lo) / risk > val)
            else:
                hit = None             # unscoreable without risk; stated, not 0
        else:
            raise OperatorError(f"unknown predicate kind {kind!r}")
        fired.append({**p, "fired": hit})

    return {"kind": "prereg_score", "forecast_id": f.forecast_id,
            "record_id": rec["record_id"], "realized_r": realized_r,
            "r_in_band": r_in_band, "unscoreable_reason": reason,
            "predicates": fired}

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

    # Baseline volatility: the LAST 20 bars strictly before as_of — the spec's
    # "prior 20-bar median", not a horizon-scaled window (Cato finding 7).
    import pandas as pd
    path = bars_mod._cache_path(symbol, "5m")
    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index, utc=True)
    prior = df[df.index < _aware(f.as_of)].iloc[-20:]
    if len(prior) < 20:
        raise OperatorError(
            f"only {len(prior)} bars precede {f.as_of} — the 20-bar shock "
            "baseline is undefined, refusing to resolve on a guess")
    prior_tr = float((prior["High"] - prior["Low"]).abs().median())
    if not (prior_tr > 0):
        raise OperatorError(
            f"prior 20-bar median true range is {prior_tr} — a zero/NaN "
            "baseline would silently disable shock detection, refusing")
    win_tr = (window["High"] - window["Low"]).abs()
    shock = bool(float(win_tr.max()) > SHOCK_TR_MULT * prior_tr)

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


def _regret_for(f: Forecast, window) -> tuple[Optional[float], str]:
    """Supply Resolution.policy_regret_r (D3 in THE_BIG_PLAN.md) from a real
    graded outcome, never from a guess.

    Grades the policy this forecast actually RECOMMENDED against the same
    shadow tournament run_shadow() runs (spec 014), using the plan on file
    and the real bars the resolution window covers as the cycle stream.
    `policy_regret_r` is derived (this call site's decision, NOT a change to
    regret.grade() or RegretReport) as realized_r minus the best counterfactual
    in the tournament — i.e. -max(counterfactual_delta_r.values()): <= 0 when
    some modeled policy would have done better, 0 when the followed policy WAS
    the best of the tournament, and it can go positive when the followed
    policy beat every modeled counterfactual.

    Returns (policy_regret_r, reason). reason is '' on success; on any refusal
    policy_regret_r is None and reason states why — never a defaulted 0.0
    (CLAUDE.md rule 3, same class of bug as a silently dropped caveat)."""
    if f.recommendation is None:
        return None, "forecast carried no recommendation — no policy was followed, nothing to grade"
    if not PLAN.exists():
        return None, "no plan.json on file — no trade to grade against this forecast"
    try:
        plan = json.loads(PLAN.read_text())
    except json.JSONDecodeError as e:
        return None, f"plan.json is not valid JSON: {e}"
    if plan.get("symbol") != f.symbol:
        return None, f"plan on file is for {plan.get('symbol')!r}, not {f.symbol!r}"
    if plan.get("entry") is None or plan.get("sl") is None:
        return None, "plan on file has no entry/sl — trade risk is undefined"
    try:
        risk = abs(float(plan["entry"]) - float(plan["sl"]))
    except (TypeError, ValueError):
        return None, "plan entry/sl are not numeric"
    if risk <= 0:
        return None, "plan risk (|entry - sl|) is zero"
    if len(window) < 2:
        return None, "fewer than 2 bars in the resolution window — nothing to grade against"
    if not _satisfiable(f.recommendation, plan):
        return None, (f"recommended policy {f.recommendation!r} is not satisfiable "
                      "by the plan on file")

    cycles = [(float(row.Close), ts.tz_convert(ET).strftime("%H:%M"))
             for ts, row in window.iterrows()]
    policies = sorted(set(SHADOW_TOURNAMENT_POLICIES) | {f.recommendation})
    try:
        results = run_shadow(plan, cycles, policies)
    except ValueError as e:
        return None, f"shadow tournament refused: {e}"

    actual = results[f.recommendation]
    if not actual.closed:
        return None, (f"{f.recommendation} did not close within the resolution "
                      "window — regret on a still-open position is refused "
                      "(regret.grade() would raise on it)")
    try:
        rep = regret_mod.grade(trade_id=f.forecast_id, realized_r=actual.realized_r,
                               closed=True, plan=plan, cycles=cycles,
                               shadow_results=results)
    except regret_mod.RegretError as e:
        return None, str(e)

    best_delta = max(rep.counterfactual_delta_r.values(), default=0.0)
    return -best_delta, ""


def resolve_open(now: Optional[datetime] = None) -> int:
    """Close every open forecast whose horizon has passed. Missing bars leave
    the forecast open with a printed reason — nothing resolves partially."""
    now = now or _utcnow()
    _reconcile_derived()
    ledger = load_ledger()
    records = {r["forecast_id"]: r for r in _read_jsonl(RECORDS)}
    rows = _read_jsonl(FC_LOG)
    open_ids = ({r["forecast_id"] for r in rows if r["kind"] == "forecast"}
                - {r["forecast_id"] for r in rows if r["kind"] == "resolution"}
                - {r["forecast_id"] for r in rows if r["kind"] == "unresolvable"})
    n = 0
    n_unresolvable = 0
    for fid in sorted(open_ids):
        f = ledger.forecast(fid)
        horizon_end = _aware(f.as_of) + timedelta(minutes=f.horizon_min)
        if now < horizon_end:
            continue
        window = _bars_between(f.symbol, _aware(f.as_of), horizon_end)
        if window is None or len(window) < 2:
            # Bars missing is usually transient (cache hasn't refreshed yet).
            # It is PERMANENT when the window's own overlap with a tradable
            # RTH session is too short to ever contain 2 five-minute bars —
            # issued overnight/weekend (zero overlap), or so close to the
            # session close that only one bar timestamp can ever exist in
            # the window (spec 024 review, 2026-08-2x). Route this through a
            # terminal state, never through resolve() with a guessed outcome.
            overlap_min = _rth_overlap_minutes(_aware(f.as_of), horizon_end)
            if overlap_min < MIN_BAR_INTERVAL_MIN:
                reason = "window has no tradable session"
                ledger.mark_unresolvable(fid, reason, at=_iso(now))
                _append_jsonl(FC_LOG, {"kind": "unresolvable",
                                       "forecast_id": fid, "reason": reason,
                                       "at": _iso(now)})
                n_unresolvable += 1
                print(f"  {fid}: {reason} [{f.as_of} +{f.horizon_min}m] — "
                      "UNRESOLVABLE")
            else:
                print(f"  {fid}: bars missing for [{f.as_of} +{f.horizon_min}m] — stays OPEN")
            continue
        try:
            scenario, direction, shock = _outcome(f.symbol, f, window)
        except OperatorError as e:
            print(f"  {fid}: {e} — stays OPEN")
            continue
        rec = records.get(fid)
        if rec is None:
            # A forecast with no sealed record is a broken audit chain, and
            # was_stale feeds the promotion gate — defaulting it would bias
            # toward promotion on exactly the missing-data case (Cato finding 2).
            raise OperatorError(
                f"{fid} has no sealed record in {RECORDS.name} — refusing to "
                "resolve a forecast whose provenance is missing")
        # bar_age_min None means NO cached bars existed at call time — the
        # stalest possible context, so it counts as stale, never as fresh.
        was_stale = (rec.get("bar_age_min") is None
                     or rec["bar_age_min"] > STALE_BAR_MIN)
        policy_regret_r, regret_reason = _regret_for(f, window)
        r = Resolution(forecast_id=fid, resolved_at=_iso(now),
                       outcome_scenario=scenario, outcome_direction=direction,
                       shock_occurred=shock, was_stale=was_stale,
                       policy_regret_r=policy_regret_r)
        ledger.resolve(r)                   # 017 refuses early resolution
        _append_jsonl(FC_LOG, {"kind": "resolution", **r.__dict__})
        # policy_regret_r's WHY travels separately (same pattern as
        # unscoreable_reason on prereg_score): a None regret and a computed
        # one are different facts, and the reason must stay auditable even
        # though Resolution itself carries no reason field (spec 017 schema).
        _append_jsonl(FC_LOG, {"kind": "regret", "forecast_id": fid,
                               "policy_regret_r": policy_regret_r,
                               "reason": regret_reason})
        prereg_row = _score_pre_registration(rec, f, window)
        if prereg_row is not None:
            _append_jsonl(FC_LOG, prereg_row)
        hit = "HIT" if direction == f.direction else "miss"
        print(f"  {fid}: {scenario} / {direction} ({hit})"
              + (" SHOCK" if shock else ""))
        n += 1
    still_open = len(open_ids) - n - n_unresolvable
    print(f"  resolved {n} forecast(s); {n_unresolvable} unresolvable "
          f"(window has no tradable session); {still_open} still open")
    return 0


YIELD_LOG = OPDIR / "yield.jsonl"


def grade(model: str) -> int:
    ledger = load_ledger()
    try:
        rep = ledger.grade(f"{MODEL_VERSION_BASE}/{model}", oos=False)
    except ForecastError as e:
        print(f"  {e}")
        return 1
    for k, v in rep.to_dict().items():
        print(f"  {k:24s} {v}")

    # Honesty re-cut (2026-08-17 review): pooled Brier at small n and fused
    # direction/abstention statistics are uninterpretable. Report decisive
    # calls separately, abstention rate as its own line, and refuse to print
    # sub-sample metrics under n=30 as anything but "insufficient".
    rows = _read_jsonl(FC_LOG)
    fs = {r["forecast_id"]: r for r in rows if r["kind"] == "forecast"}
    res = [r for r in rows if r["kind"] == "resolution"
           and fs.get(r["forecast_id"], {}).get("model_version", "").endswith(model)]
    if res:
        recs = {r["forecast_id"]: r for r in _read_jsonl(RECORDS)}
        decisive = [r for r in res
                    if recs.get(r["forecast_id"], {}).get("verdict") != "ABSTAIN"
                    and fs[r["forecast_id"]]["direction"] != "flat"]
        n_dec = len(decisive)
        abst_rate = 1 - n_dec / len(res)
        print(f"  {'abstention_rate':24s} {abst_rate:.0%} "
              f"({len(res) - n_dec}/{len(res)} resolved calls non-decisive)")
        if n_dec >= 30:
            hits = sum(1 for r in decisive
                       if fs[r['forecast_id']]['direction'] == r['outcome_direction'])
            print(f"  {'decisive_dir_accuracy':24s} {hits}/{n_dec}")
        else:
            print(f"  {'decisive_dir_accuracy':24s} insufficient (n={n_dec} < 30) "
                  "— no number is quotable")
        # honest promotion date: 50 DECISIVE resolved calls at the observed rate
        days_so_far = len({fs[r['forecast_id']]['as_of'][:10] for r in res})
        dec_per_day = n_dec / max(days_so_far, 1)
        if dec_per_day > 0:
            print(f"  {'promotion_horizon':24s} ~{50 / dec_per_day:.0f} trading days "
                  f"to 50 decisive calls at {dec_per_day:.1f}/day")
        else:
            print(f"  {'promotion_horizon':24s} unbounded — zero decisive calls so far")

    # Pre-registration scoreboard (spec 024): the number, vs the number.
    scores = [r for r in _read_jsonl(FC_LOG) if r["kind"] == "prereg_score"]
    banded = [s for s in scores if s["r_in_band"] is not None]
    if banded:
        hit = sum(1 for s in banded if s["r_in_band"])
        print(f"  {'prereg_r_in_band':24s} {hit}/{len(banded)}")
    elif scores:
        print(f"  {'prereg_r_in_band':24s} {len(scores)} scored, none R-scoreable")

    # Yield trajectory (spec 024 I21): demotion must be readable from data.
    ys = _read_jsonl(YIELD_LOG)
    if ys:
        traj = " → ".join(f"{y['yield_delta_r']:+.3f}" for y in ys[-6:])
        print(f"  {'veto_yield_trajectory':24s} {traj}")
        last3 = [y["yield_delta_r"] for y in ys[-3:]]
        if len(last3) == 3 and last3[0] > last3[1] > last3[2]:
            print("  !! YIELD_DECAY: three consecutive declining sessions — "
                  "the overlay is fading; promotion case weakens")

    # Sim→live tripwire (spec 024 I20): wraps the report, not the 017 gate.
    import gap_log
    st = gap_log.status()
    print(f"  {'sim_live_gap':24s} drift={st['drift']} n={st['n_gaps']}")
    if st["tripwire"]:
        print(f"  !! TRIPWIRE: {st['tripwire']}")
        print("  PROMOTABLE: NO (tripwire)")
        return 1
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
    run.add_argument("--shadow", action="store_true",
                     help="soak mode (spec 024 I22): full judgment, sealed record, "
                          "forecast — but ZERO directive writes")
    run.add_argument("--no-refresh", action="store_true",
                     help="skip the bar-cache refresh (offline / test)")

    res = sub.add_parser("resolve", help="close open forecasts whose horizon passed")
    grd = sub.add_parser("grade", help="score the operator against the 017 gates")
    grd.add_argument("--model", default=DEFAULT_MODEL)

    a = ap.parse_args(argv)
    if a.cmd == "run":
        try:
            return run_once(a.symbol, cap=a.cap, model=a.model, force=a.force,
                            shadow=a.shadow,
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
