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
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from execution.alpaca import load_env                       # noqa: E402  one env path

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


class NewsRead(BaseModel):
    """Structured, never prose. Prose cannot be scored."""
    bias: float = Field(description="-1.0 (definitely down) to +1.0 (definitely up), today only")
    conviction: Literal["low", "medium", "high"]
    thesis: str = Field(description="ONE sentence naming the actual catalyst")
    consensus_eps: Optional[float] = Field(default=None, description="analyst consensus EPS if known, else null")
    projected_eps: Optional[float] = Field(default=None, description="your own projected EPS if you have a view, else null")
    watch_for: list[str] = Field(description="2-5 specific checkable things that would change this read")
    suggested_urgency: Literal["none", "tighten", "exit"] = Field(
        description="'exit' only for a shock that makes holding through it unreasonable")


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="spec 009 — Claude as the news brain")
    ap.add_argument("--symbol", default="NVDA")
    ap.add_argument("--model", default="claude-opus-5", choices=sorted(PRICES))
    ap.add_argument("--effort", default="low", choices=("low", "medium", "high"))
    ap.add_argument("--cap", type=float, default=DEFAULT_CAP_USD, help="hard $ ceiling")
    ap.add_argument("--context", default="regular session")
    ap.add_argument("--estimate", action="store_true", help="price it without calling")
    ap.add_argument("--force", action="store_true", help="call even if headlines unchanged")
    ap.add_argument("--allow-urgency", action="store_true",
                    help="let the model set bias.json urgency (it can flatten a position)")
    ap.add_argument("--spend", action="store_true", help="print the spend ledger and exit")
    a = ap.parse_args(argv)

    if a.spend:
        print(f"  spent: ${spent_so_far():.4f}")
        if SPEND.exists():
            for l in list(SPEND.open())[-10:]:
                d = json.loads(l)
                print(f"    {d['ts'][:19]} {d['model']:16s} {d['kind']:9s} ${d['cost_usd']:.5f}")
        return 0

    from polygon_news import fetch_headlines
    heads = fetch_headlines(a.symbol)
    print(f"  {len(heads)} headlines for {a.symbol}")

    if a.estimate:
        for m in sorted(PRICES):
            estimate(a.symbol, heads, a.context, m)
        return 0

    fp = headline_fingerprint(heads)
    if not changed(fp) and not a.force:
        print("  headlines unchanged since the last call — no API call, no cost. (--force to override)")
        return 0

    try:
        read, cost = read_news(a.symbol, heads, a.context, model=a.model, cap=a.cap,
                               effort=a.effort, kind="read")
    except SpendCapReached as e:
        print(f"  !! SPEND CAP: {e}")
        return 1

    STATE.write_text(json.dumps({"fingerprint": fp, "ts": datetime.now(timezone.utc).isoformat()}))
    write_bias(read, a.symbol, a.model, len(heads), a.allow_urgency)
    append_scorecard(read, a.symbol, a.model, cost)

    print(f"\n  bias {read.bias:+.2f} ({read.conviction})   suggested_urgency={read.suggested_urgency}")
    print(f"  thesis: {read.thesis}")
    for w in read.watch_for:
        print(f"    watch: {w}")
    if read.consensus_eps is not None or read.projected_eps is not None:
        print(f"  EPS: consensus {read.consensus_eps} vs Claude's projection {read.projected_eps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
