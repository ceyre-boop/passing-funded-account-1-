#!/usr/bin/env python3
"""ALPHAZERO's news brain — spec 009. Claude reads the tape's headlines.

Replaces alphazero_bias.py's keyword-valence placeholder (the one graded FALSE
on 2026-08-03) with an actual judgment, and submits that judgment to exactly the
same scoring discipline as everything else here. No exemption for being an LLM:
every call lands in data/daytrade/news_scorecard.csv to be graded against what
the tape did, alongside dumb baselines.

MONEY IS THE BINDING CONSTRAINT, so it is enforced rather than hoped for:

  1. HARD SPEND CAP. Every call appends its real cost to llm_spend.jsonl. Before
     each call the ledger is summed; at the cap the client REFUSES rather than
     spending the next cent. A runaway loop stops instead of draining a budget —
     prior experience: an unattended key burned $13 in two days.
  2. DELTA-ONLY FIRING. The headline set is hashed; unchanged headlines mean no
     call at all. A quiet morning — most of the morning — costs nothing.
  3. PROMPT CACHING — declared, but MEASURED AS INACTIVE. The breakpoint sits on
     the frozen instruction block, which is 391 tokens against Opus 5's 512-token
     minimum cacheable prefix, so it silently caches nothing today (verify with
     `cache_read_input_tokens` in llm_spend.jsonl — currently 0 on every row).
     The breakpoint is kept because it starts working the moment the instructions
     grow past the minimum, but do not count it as a saving until that shows up
     in the ledger. At $0.002-0.016 a call it is not worth padding the prompt to
     force it.
  4. COUNT BEFORE SPEND. --estimate prices a call via count_tokens, no call made.

TWO TIERS, because the morning read and the delta checks are different jobs:

  MORNING (Opus 5, high effort, ONCE per day) sets the day's frame — every
  important headline, a predicted schedule of what happens when, key levels, a
  day plan, and the specific things that would invalidate it. This is the call
  worth paying for: everything after it is measured against this frame.

  DELTA (Sonnet 5, low effort, every 5 min when headlines change) does NOT
  re-read the day from scratch. It receives the morning plan and answers one
  question: does that thesis still hold, and if not, what changed? A narrower
  question on a smaller output, which is why a capable cheaper model is the
  right tool rather than a compromise.

The morning read is guarded to one per day — burning Opus on a repeat is a
mistake the code should prevent, not one to remember not to make.

URGENCY IS DISARMED BY DEFAULT. `urgency: exit` flattens a live position. This
model has zero scored calls, so it does not get that authority until it earns
it: the field is forced to "none" unless --allow-urgency is passed, while the
model's own suggestion is still recorded so it can be graded before it is ever
trusted. Same discipline as broker.py starting at --broker off.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from execution.alpaca import load_env                       # noqa: E402  one env path

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daytrade"
BIAS = OUT / "bias.json"
SPEND = OUT / "llm_spend.jsonl"
SCORECARD = OUT / "news_scorecard.csv"
STATE = OUT / "news_state.json"

# $/1M tokens. Explicit rather than fetched, so a pricing change is a visible
# diff in review and not a surprise invoice.
PRICES = {
    "claude-opus-5":    (5.00, 25.00),
    "claude-sonnet-5":  (3.00, 15.00),
    "claude-haiku-4-5": (1.00,  5.00),
}
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.10

DEFAULT_CAP_USD = 5.00          # under the $6 remaining, deliberately

# The two jobs and what each is worth. Opus buys the frame once; Sonnet tracks
# against it.
#
# Morning effort dropped high -> medium 2026-08-06 (Colin's call), after high
# measured at $0.105 — about 70% of a session's total spend. Measured side by
# side on identical headlines; see the comparison in the commit for what the
# downgrade actually costs in output quality. Raise it back with --effort high
# on any morning that genuinely warrants it (earnings day, a live policy shock).
MODES = {
    "morning": {"model": "claude-opus-5",   "effort": "medium"},
    "delta":   {"model": "claude-sonnet-5", "effort": "low"},
}
PLAN = OUT / "morning_plan.json"
CALENDAR = OUT / "macro_calendar.md"


class SpendCapReached(RuntimeError):
    """The budget is exhausted. Never downgraded to a warning."""


# ------------------------------------------------------------------ the money

def cost_of(usage, model: str) -> float:
    """Dollar cost of one response, with the cache tiers priced separately."""
    if model not in PRICES:
        raise ValueError(f"no price for {model!r}; known: {sorted(PRICES)}")
    inp, outp = PRICES[model]
    w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    r = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (usage.input_tokens / 1e6 * inp
            + w / 1e6 * inp * CACHE_WRITE_MULT
            + r / 1e6 * inp * CACHE_READ_MULT
            + usage.output_tokens / 1e6 * outp)


def spent_so_far() -> float:
    if not SPEND.exists():
        return 0.0
    return sum(json.loads(l)["cost_usd"] for l in SPEND.open() if l.strip())


def check_budget(cap: float) -> float:
    used = spent_so_far()
    if used >= cap:
        raise SpendCapReached(
            f"${used:.4f} of ${cap:.2f} already spent — refusing to call. Raise --cap "
            f"deliberately, or clear {SPEND.name} if that spend is stale.")
    return used


def record_spend(model: str, usage, cost: float, kind: str) -> None:
    SPEND.parent.mkdir(parents=True, exist_ok=True)
    with SPEND.open("a") as fh:
        fh.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(), "model": model, "kind": kind,
            "input": usage.input_tokens, "output": usage.output_tokens,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cost_usd": round(cost, 8),
        }) + "\n")


# ------------------------------------------------------------- the judgment

from pydantic import BaseModel, Field                       # noqa: E402


class ScheduleItem(BaseModel):
    time_et: str = Field(description="HH:MM ET, or 'pre-open' / 'after-close'")
    event: str = Field(description="what happens")
    why_it_matters: str = Field(description="why a day trader in this name cares")


class MorningPlan(BaseModel):
    """The day's frame. Opus, once. Everything else is measured against this."""
    bias: float = Field(description="-1.0 to +1.0 for TODAY's session")
    conviction: Literal["low", "medium", "high"]
    thesis: str = Field(description="ONE sentence: what today is actually about")
    important_news: list[str] = Field(description="the headlines that actually matter, most important first; drop the noise")
    schedule: list[ScheduleItem] = Field(description="predicted schedule for today — releases, earnings, known catalysts, and the session's own structural moments")
    key_levels: list[str] = Field(description="price levels or technical markers worth watching, with why")
    day_plan: str = Field(description="2-3 sentences: what a day trader should be prepared for today")
    invalidators: list[str] = Field(description="specific, checkable things that would break this read")
    consensus_eps: Optional[float] = Field(default=None)
    projected_eps: Optional[float] = Field(default=None)
    suggested_urgency: Literal["none", "tighten", "exit"]


class DeltaRead(BaseModel):
    """Does the morning thesis still hold? Sonnet, cheap, narrow."""
    still_holds: bool = Field(description="does the morning thesis still hold?")
    bias: float = Field(description="-1.0 to +1.0, updated")
    conviction: Literal["low", "medium", "high"]
    what_changed: str = Field(description="ONE sentence. 'nothing material' is a valid and common answer")
    invalidator_triggered: Optional[str] = Field(default=None, description="which morning invalidator fired, if any")
    suggested_urgency: Literal["none", "tighten", "exit"]


class NewsRead(BaseModel):
    """Legacy single-shot shape, kept for the standalone --mode read path."""
    bias: float = Field(description="-1.0 (definitely down) to +1.0 (definitely up), today only")
    conviction: Literal["low", "medium", "high"]
    thesis: str = Field(description="ONE sentence naming the actual catalyst")
    consensus_eps: Optional[float] = Field(default=None, description="analyst consensus EPS if known, else null")
    projected_eps: Optional[float] = Field(default=None, description="your own projected EPS if you have a view, else null")
    watch_for: list[str] = Field(description="2-5 specific checkable things that would change this read")
    suggested_urgency: Literal["none", "tighten", "exit"] = Field(
        description="'exit' only for a shock that makes holding through it unreasonable")


MORNING_SYSTEM = """You set the day's frame for a single-name day trader, once, before the open.

This is the expensive call and the only one that gets to think hard. Everything
later in the session is checked against what you produce here, so an omission
now is invisible for the rest of the day.

Your output is scored. Every call is graded against what the tape actually did,
alongside dumb baselines (always-flat, yesterday's direction, coin flip). A
confident-sounding paragraph that turns out wrong scores worse than an honest
low-conviction read. `conviction: high` is a claim you are betting a track
record on.

If a macro calendar is supplied below, USE IT — those are known facts and every
one of them belongs in `schedule` with its real time and a reason it matters to
this specific name. Do not hedge a supplied event into "possible macro data."
If no calendar is supplied, say so honestly in the relevant schedule entries
rather than guessing at times you are not sure of.

What matters most, in order:
- `schedule` — be concrete about WHEN. Scheduled releases with times, earnings
  before/after the bell, and the session's own structure (the open, the 10:30
  regime shift, the close). A day trader plans around clock, so vague timing is
  worse than an omitted item.
- `important_news` — rank ruthlessly. Two headlines that move the stock beat ten
  that fill a page. Drop retail listicles and ETF filler entirely.
- `invalidators` — specific and checkable ("AMD guides data-center revenue below
  $2B"), never vague ("sentiment sours"). These are what the cheap 5-minute
  checks test against all day, so a lazy invalidator wastes the whole session.
- `key_levels` — say why each level matters, not just the number.
- `bias` is TODAY's session only.

If today genuinely has no catalyst, say so: bias near 0, conviction low, and a
day plan that says the honest thing. Manufacturing a view is the failure mode."""


DELTA_SYSTEM = """You monitor a day trader's morning thesis during the session.

You are NOT re-reading the day from scratch. The morning plan is given to you.
Your only job is: does that thesis still hold, and if not, what changed?

- "nothing material" is the correct answer most of the time. Say it plainly.
  Inventing a change to seem useful is the failure mode here.
- If one of the morning `invalidators` actually fired, name it in
  `invalidator_triggered`. That is the highest-value thing you can report.
- `suggested_urgency` is 'exit' ONLY for a shock that makes holding through it
  unreasonable: a halt, a fraud allegation, a withdrawn guide. Routine
  volatility is 'none'. A false 'exit' closes a live position for nothing.

Your output is scored against what the tape did, same as the morning read."""


SYSTEM = """You read market news for a single-name day trader.

Your output is scored. Every call is graded against what the tape actually did,
alongside dumb baselines (always-flat, yesterday's direction, coin flip). A
confident-sounding paragraph that turns out wrong scores worse than an honest
low-conviction read. Calibrate accordingly: `conviction: high` is a claim you
are betting a track record on.

Rules:
- `bias` is about TODAY's session, not a long-term thesis.
- `thesis` is one sentence naming the actual catalyst, not a summary of headlines.
- `watch_for` items must be specific and checkable ("AMD guides data-center
  revenue above $2B"), never vague ("sentiment improves").
- `suggested_urgency` is 'exit' ONLY for a shock that makes holding through it
  unreasonable: a halt, a fraud allegation, a withdrawn guide. Routine
  volatility is 'none'. A false 'exit' closes a live position for nothing.
- If the headlines say nothing decision-relevant, say so: bias near 0,
  conviction low. Manufacturing a view is the failure mode here."""


def _client():
    load_env()
    import anthropic
    return anthropic.Anthropic()


def _morning_blocks(symbol: str, headlines: list[dict], context: str):
    system = [{"type": "text", "text": MORNING_SYSTEM, "cache_control": {"type": "ephemeral"}}]
    lines = "\n".join(
        f"- [{h.get('published_utc','')[:16]}] {h.get('publisher','')}: {h.get('title','')}"
        f"{chr(10) + '    ' + h['description'][:200] if h.get('description') else ''}"
        for h in headlines) or "(no headlines returned)"
    user = [{"type": "text", "text":
             f"Symbol: {symbol}\nContext: {context}\n\nHeadlines:\n{lines}"}]
    return system, user


def _delta_blocks(symbol: str, headlines: list[dict], context: str, plan: dict):
    """The morning plan goes in the CACHED prefix, not the volatile user turn.

    Measured: with the plan in the user block, a delta call was 2816 input
    tokens for 103 output — the cost was almost entirely re-sending a document
    that does not change all day. The plan is stable from the open to the close,
    so it belongs in the cached prefix; only the headlines vary. This also pushes
    the prefix past Sonnet 5's 1024-token cache minimum, which the 391-token
    system prompt alone never reached, so the breakpoint stops being decorative
    and starts billing reads at ~0.1x.
    """
    frame = (f"THIS MORNING'S PLAN ({plan.get('ts','')[:16]}):\n"
             f"  thesis: {plan.get('thesis')}\n"
             f"  bias: {plan.get('bias')} ({plan.get('conviction')})\n"
             f"  day plan: {plan.get('day_plan')}\n"
             f"  schedule:\n" +
             "".join(f"    {i.get('time_et','')}: {i.get('event','')}\n"
                     for i in plan.get("schedule", [])) +
             f"  invalidators:\n" +
             "".join(f"    - {i}\n" for i in plan.get("invalidators", [])) +
             f"  key levels:\n" +
             "".join(f"    - {l}\n" for l in plan.get("key_levels", [])))
    system = [{"type": "text", "text": DELTA_SYSTEM + "\n\n" + frame,
               "cache_control": {"type": "ephemeral"}}]
    lines = "\n".join(
        f"- [{h.get('published_utc','')[:16]}] {h.get('publisher','')}: {h.get('title','')}"
        for h in headlines) or "(no headlines returned)"
    user = [{"type": "text", "text":
             f"Symbol: {symbol}\nNow: {context}\n\nCURRENT HEADLINES:\n{lines}"}]
    return system, user


def _blocks(symbol: str, headlines: list[dict], context: str):
    """System (frozen) and user (volatile) content.

    The cache_control breakpoint is currently inert — see the module docstring.
    Placement is still correct: stable content first, volatile content after, so
    it engages automatically if the instructions ever exceed the minimum.
    """
    system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
    lines = "\n".join(
        f"- [{h.get('published_utc','')[:16]}] {h.get('publisher','')}: {h.get('title','')}"
        for h in headlines) or "(no headlines returned)"
    user = [{"type": "text", "text":
             f"Symbol: {symbol}\nSession context: {context}\n\nHeadlines:\n{lines}"}]
    return system, user


def read_news(symbol: str, headlines: list[dict], context: str, *,
              model: str, cap: float, effort: str, kind: str):
    used = check_budget(cap)
    client = _client()
    system, user = _blocks(symbol, headlines, context)

    kw = {"model": model, "max_tokens": 1024, "system": system,
          "messages": [{"role": "user", "content": user}], "output_format": NewsRead}
    # Thinking ON at LOW effort rather than disabled: on Opus 5 a disabled-thinking
    # path can leak internal tags into the output, and low effort is the cheaper,
    # safer lever anyway.
    if model.startswith(("claude-opus-5", "claude-sonnet-5")):
        kw["thinking"] = {"type": "adaptive"}
        kw["output_config"] = {"effort": effort}

    r = client.messages.parse(**kw)
    cost = cost_of(r.usage, model)
    record_spend(model, r.usage, cost, kind)
    print(f"  [{model} {kind}] ${cost:.5f}  (total ${used + cost:.4f} / ${cap:.2f} cap)  "
          f"in {r.usage.input_tokens} cache_r {getattr(r.usage,'cache_read_input_tokens',0) or 0} "
          f"out {r.usage.output_tokens}")
    return r.parsed_output, cost


class TruncatedResponse(RuntimeError):
    """The model hit max_tokens mid-answer. Loud, because a truncated JSON body
    is indistinguishable from a malformed one at the parse site."""


# Per-mode output ceilings. The morning plan is a much larger object (schedule +
# ranked news + levels + invalidators) AND Opus 5 thinks by default, with
# thinking counted against the same max_tokens. 2048 truncated it mid-string on
# the first real run — and that truncated call still cost money.
MAX_TOKENS = {"morning": 8000, "delta": 2048}


def _call(system, user, schema, *, model: str, cap: float, effort: str, kind: str):
    """One priced, ledgered API call. Every call in this file goes through here.

    Uses create() + manual parse rather than messages.parse() for one reason:
    parse() validates inside the SDK's response handler, so a validation failure
    raises before any of our code sees `usage` — the call is billed and the
    ledger never hears about it. A budget guard that under-counts on exactly the
    calls that went wrong is worse than none. Here `usage` is recorded FIRST and
    parsing happens after, so every cent that leaves the account is on the ledger.

    Note this leans on two private SDK helpers (anthropic.lib._parse). If a
    version bump moves them, the fallback below keeps working with the ledger gap
    stated out loud rather than hidden.
    """
    used = check_budget(cap)
    client = _client()
    max_tok = MAX_TOKENS.get(kind, 2048)

    oc: dict = {}
    if model.startswith(("claude-opus-5", "claude-sonnet-5")):
        oc["effort"] = effort

    try:
        from pydantic import TypeAdapter
        from anthropic.lib._parse._transform import transform_schema
        from anthropic.lib._parse._response import parse_text
        oc["format"] = {"type": "json_schema",
                        "schema": transform_schema(TypeAdapter(schema).json_schema())}
        kw = {"model": model, "max_tokens": max_tok, "system": system,
              "messages": [{"role": "user", "content": user}], "output_config": oc}
        if model.startswith(("claude-opus-5", "claude-sonnet-5")):
            kw["thinking"] = {"type": "adaptive"}

        r = client.messages.create(**kw)
        cost = cost_of(r.usage, model)
        record_spend(model, r.usage, cost, kind)          # BEFORE parsing. Always.
        _report(model, kind, effort, cost, used, r.usage, max_tok)

        if r.stop_reason == "max_tokens":
            raise TruncatedResponse(
                f"{model} hit max_tokens={max_tok} on a {kind} read — the JSON body is "
                f"cut off. The call was still billed (${cost:.5f}) and is on the ledger. "
                f"Raise MAX_TOKENS[{kind!r}].")
        text = next(b.text for b in r.content if b.type == "text")
        return parse_text(text, schema), cost

    except ImportError:
        print("  !! SDK private parse helpers moved — falling back to messages.parse().\n"
              "     A validation failure on this path is billed but NOT ledgered.")
        kw = {"model": model, "max_tokens": max_tok, "system": system,
              "messages": [{"role": "user", "content": user}], "output_format": schema}
        if oc:
            kw["output_config"], kw["thinking"] = oc, {"type": "adaptive"}
        r = client.messages.parse(**kw)
        cost = cost_of(r.usage, model)
        record_spend(model, r.usage, cost, kind)
        _report(model, kind, effort, cost, used, r.usage, max_tok)
        return r.parsed_output, cost


def _report(model, kind, effort, cost, used, usage, max_tok) -> None:
    print(f"  [{model} {kind} effort={effort}] ${cost:.5f}   "
          f"(total ${used + cost:.4f} / cap)   "
          f"in {usage.input_tokens} out {usage.output_tokens}/{max_tok}")


def morning_read(symbol: str, headlines: list[dict], context: str, *, cap: float,
                 model: str = None, effort: str = None):
    m = MODES["morning"]
    return _call(*_morning_blocks(symbol, headlines, context), MorningPlan,
                 model=model or m["model"], cap=cap,
                 effort=effort or m["effort"], kind="morning")


def delta_read(symbol: str, headlines: list[dict], context: str, plan: dict, *,
               cap: float, model: str = None, effort: str = None):
    m = MODES["delta"]
    return _call(*_delta_blocks(symbol, headlines, context, plan), DeltaRead,
                 model=model or m["model"], cap=cap,
                 effort=effort or m["effort"], kind="delta")


def plan_is_from_today() -> bool:
    """Morning read is once per day. Burning Opus on a repeat is a mistake the
    code should prevent, not one to remember not to make."""
    if not PLAN.exists():
        return False
    from datetime import date
    return json.loads(PLAN.read_text()).get("ts", "")[:10] == str(date.today())


def estimate(symbol: str, headlines: list[dict], context: str, model: str) -> None:
    """Price a call without making it."""
    client = _client()
    system, user = _blocks(symbol, headlines, context)
    n = client.messages.count_tokens(model=model, system=system,
                                     messages=[{"role": "user", "content": user}]).input_tokens
    inp, outp = PRICES[model]
    lo, hi = n / 1e6 * inp + 200 / 1e6 * outp, n / 1e6 * inp + 500 / 1e6 * outp
    print(f"  {model:17s} {n:5d} in  ->  ${lo:.5f} - ${hi:.5f} per call")


# ------------------------------------------------------------- delta gating

def headline_fingerprint(headlines: list[dict]) -> str:
    return hashlib.sha256("|".join(sorted(h.get("title", "") for h in headlines))
                          .encode()).hexdigest()[:16]


def changed(fp: str) -> bool:
    """True if the headline set differs from the last call's. The single biggest
    cost control here: a quiet morning makes zero API calls."""
    if not STATE.exists():
        return True
    return json.loads(STATE.read_text()).get("fingerprint") != fp


# -------------------------------------------------------------- the outputs

def write_bias(read, symbol: str, model: str, n: int, allow_urgency: bool) -> None:
    """Merge into the FROZEN bias.json contract — additive only, never remove."""
    urgency = read.suggested_urgency if allow_urgency else "none"
    if read.suggested_urgency != "none" and not allow_urgency:
        print(f"  !! model suggested urgency={read.suggested_urgency!r} — SUPPRESSED to 'none'.\n"
              f"     Recorded in the scorecard so it can be graded. Pass --allow-urgency to give\n"
              f"     an unvalidated model authority over a live position.")
    BIAS.write_text(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "bias": read.bias, "urgency": urgency, "n_headlines": n,
        "conviction": read.conviction, "thesis": read.thesis,
        "consensus_eps": read.consensus_eps, "projected_eps": read.projected_eps,
        "watch_for": read.watch_for,
        "suggested_urgency": read.suggested_urgency, "urgency_armed": allow_urgency,
        "model": f"{model} (spec 009, scored — see news_scorecard.csv)",
        "symbol": symbol,
    }, indent=1))


def append_scorecard(read, symbol: str, model: str, cost: float) -> None:
    """One row per call. Graded later, against baselines, no exemption."""
    new = not SCORECARD.exists()
    with SCORECARD.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["call_date", "session_graded", "source", "model_version", "bias",
                        "conviction", "thesis", "predicted", "actual", "hit", "notes"])
        w.writerow([datetime.now(timezone.utc).date(), "", f"news_claude:{symbol}", model,
                    f"{read.bias:+.2f}", read.conviction, read.thesis,
                    "; ".join(read.watch_for), "", "",
                    f"cost ${cost:.5f}; suggested_urgency={read.suggested_urgency}"])


def _write_outputs(obj, symbol: str, model: str, n: int, allow_urgency: bool,
                   cost: float, mode: str, plan: dict | None = None) -> None:
    """bias.json (frozen contract, additive only) + one scorecard row."""
    urgency = obj.suggested_urgency if allow_urgency else "none"
    if obj.suggested_urgency != "none" and not allow_urgency:
        print(f"  !! model suggested urgency={obj.suggested_urgency!r} — SUPPRESSED to 'none'.\n"
              f"     Recorded in the scorecard so it can be graded. Pass --allow-urgency to give\n"
              f"     an unvalidated model authority over a live position.")
    body = {
        "ts": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "mode": mode,
        "bias": obj.bias, "urgency": urgency, "n_headlines": n,
        "conviction": obj.conviction,
        "suggested_urgency": obj.suggested_urgency, "urgency_armed": allow_urgency,
        "model": f"{model} (spec 009 {mode}, scored — see news_scorecard.csv)",
    }
    if mode == "morning":
        body.update({"thesis": obj.thesis, "day_plan": obj.day_plan,
                     "important_news": obj.important_news,
                     "schedule": [i.model_dump() for i in obj.schedule],
                     "key_levels": obj.key_levels, "invalidators": obj.invalidators,
                     "consensus_eps": obj.consensus_eps, "projected_eps": obj.projected_eps})
        PLAN.write_text(json.dumps(body, indent=1))
    else:
        body.update({"thesis": (plan or {}).get("thesis"),
                     "still_holds": obj.still_holds, "what_changed": obj.what_changed,
                     "invalidator_triggered": obj.invalidator_triggered})
    BIAS.write_text(json.dumps(body, indent=1))

    new = not SCORECARD.exists()
    with SCORECARD.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["call_date", "session_graded", "source", "model_version", "bias",
                        "conviction", "thesis", "predicted", "actual", "hit", "notes"])
        thesis = obj.thesis if mode == "morning" else obj.what_changed
        pred = "; ".join(obj.invalidators) if mode == "morning" else (obj.invalidator_triggered or "")
        w.writerow([datetime.now(timezone.utc).date(), "", f"news_claude:{mode}:{symbol}",
                    model, f"{obj.bias:+.2f}", obj.conviction, thesis, pred, "", "",
                    f"cost ${cost:.5f}; suggested_urgency={obj.suggested_urgency}"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 009 — Claude as the news brain")
    ap.add_argument("--mode", default="delta", choices=("morning", "delta"),
                    help="morning: Opus, once/day, sets the frame. delta: Sonnet, tracks it.")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--model", default=None, help="override the mode's model")
    ap.add_argument("--effort", default=None, choices=("low", "medium", "high"))
    ap.add_argument("--cap", type=float, default=DEFAULT_CAP_USD, help="hard $ ceiling")
    ap.add_argument("--context", default=None)
    ap.add_argument("--estimate", action="store_true", help="price it without calling")
    ap.add_argument("--force", action="store_true",
                    help="call even if headlines unchanged / morning already ran")
    ap.add_argument("--allow-urgency", action="store_true",
                    help="let the model set bias.json urgency (it can flatten a position)")
    ap.add_argument("--spend", action="store_true", help="print the spend ledger and exit")
    a = ap.parse_args(argv)

    if a.spend:
        print(f"  spent: ${spent_so_far():.4f}")
        if SPEND.exists():
            for l in list(SPEND.open())[-12:]:
                d = json.loads(l)
                print(f"    {d['ts'][:19]} {d['model']:16s} {d['kind']:8s} ${d['cost_usd']:.5f}")
        return 0

    ctx = a.context or datetime.now(ET).strftime("%a %Y-%m-%d %H:%M ET")
    if a.mode == "morning":
        # MEASURED 2026-08-06: the schedule's quality tracks whether the model was
        # GIVEN the macro calendar, not how hard it thought. At medium effort with
        # no calendar it hedged ("possible secondary macro ... -type filler") and
        # missed the 13:00 30-year auction entirely; at medium effort WITH the
        # calendar it named the auction, the refunding sequence, and the
        # pre-payrolls close — matching high effort at 21% less cost, and without
        # relying on the model to recall a date correctly.
        #
        # So: supply the calendar. It is public information and cheaper as input
        # than as inference.
        if CALENDAR.exists():
            ctx += "\n\nTODAY'S MACRO CALENDAR:\n" + CALENDAR.read_text().strip()
            print(f"  macro calendar supplied from {CALENDAR.name}")
        else:
            print(f"  !! no {CALENDAR.name} — the schedule will hedge on macro timing.\n"
                  f"     Write today's releases there (or pass --context); it measurably\n"
                  f"     beats paying higher effort to recall them.")
    from polygon_news import fetch_headlines, baseline_bias
    heads = fetch_headlines(a.symbol)
    print(f"  {len(heads)} headlines for {a.symbol}   "
          f"(polygon baseline bias {baseline_bias(heads)} — held out, not shown to Claude)")

    if a.estimate:
        for m in sorted(PRICES):
            estimate(a.symbol, heads, ctx, m)
        return 0

    if a.mode == "morning":
        if plan_is_from_today() and not a.force:
            print(f"  today's morning plan already exists ({PLAN.name}) — Opus runs ONCE a day.\n"
                  f"  Use --force to pay for a second one, or --mode delta to track against it.")
            return 0
        obj, cost = morning_read(a.symbol, heads, ctx, cap=a.cap,
                                 model=a.model, effort=a.effort)
        _write_outputs(obj, a.symbol, a.model or MODES["morning"]["model"], len(heads),
                       a.allow_urgency, cost, "morning")
        print(f"\n  bias {obj.bias:+.2f} ({obj.conviction})   urgency={obj.suggested_urgency}")
        print(f"  thesis: {obj.thesis}\n  plan:   {obj.day_plan}\n")
        print("  today's schedule:")
        for i in obj.schedule:
            print(f"    {i.time_et:>12s}  {i.event}\n{'':18s}{i.why_it_matters}")
        print("\n  news that matters:")
        for x in obj.important_news:
            print(f"    - {x}")
        print("\n  levels:")
        for x in obj.key_levels:
            print(f"    - {x}")
        print("\n  invalidators (what the 5-min checks test against all day):")
        for x in obj.invalidators:
            print(f"    - {x}")
        return 0

    # --- delta ---
    if not PLAN.exists():
        print(f"  no morning plan at {PLAN.name} — run --mode morning first.\n"
              f"  Delta checks measure against the morning frame; without one there is nothing to check.")
        return 1
    plan = json.loads(PLAN.read_text())
    fp = headline_fingerprint(heads)
    if not changed(fp) and not a.force:
        print("  headlines unchanged since the last call — no API call, no cost. (--force to override)")
        return 0

    try:
        obj, cost = delta_read(a.symbol, heads, ctx, plan, cap=a.cap,
                               model=a.model, effort=a.effort)
    except SpendCapReached as e:
        print(f"  !! SPEND CAP: {e}")
        return 1

    STATE.write_text(json.dumps({"fingerprint": fp, "ts": datetime.now(timezone.utc).isoformat()}))
    _write_outputs(obj, a.symbol, a.model or MODES["delta"]["model"], len(heads),
                   a.allow_urgency, cost, "delta", plan)
    flag = "HOLDS" if obj.still_holds else "!! THESIS BROKEN"
    print(f"\n  {flag}   bias {obj.bias:+.2f} ({obj.conviction})   urgency={obj.suggested_urgency}")
    print(f"  changed: {obj.what_changed}")
    if obj.invalidator_triggered:
        print(f"  !! INVALIDATOR FIRED: {obj.invalidator_triggered}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
