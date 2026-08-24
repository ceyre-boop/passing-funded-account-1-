#!/usr/bin/env python3
"""MANUAL READ LANE — quarantined news classifications, produced by the owner
interactively via Claude Code (Max subscription) during the Anthropic API
blackout (blocked until 2026-09-01), never via the API.

Governing rule, spec 030 (specs/030_DECISION_LEDGER.md:32-35), generalized
from backfill rows to this lane: "backfilled rows are valid evidence for
'what did the market look like', and are NEVER evidence for 'what did the
system observe'. Every row carries its `source`, and pooling them for the
second question is a category error." A manual read is exactly a backfill of
the second kind — it says what a model, run by hand, thought about archived
headlines. It says nothing about what the operator observed, because the
operator was not the one reading.

THE QUARANTINE IS THE POINT:

  - Writes ONLY to data/daytrade/manual_reads.jsonl. Never records.jsonl,
    forecasts.jsonl, evidence.jsonl, or decision_ledger.jsonl — this module
    imports none of alpha_operator.py, forecast.py, evidence.py, or
    decision_ledger.py, and defines no path pointing at any of their files.
  - Every row's id starts with MANUAL_PREFIX ("mr-"), visibly distinct from
    the operator's "op-"/"fc-op-"/"dir-op-" namespace, so no id collision is
    even representable.
  - A ManualRead is a frozen dataclass with a FIXED field set that has no
    scenario_probs, no expected_r band, and no numeric confidence of any
    kind — only a categorical confidence_label. It cannot be constructed
    with forecast-shaped fields (Python raises TypeError on the unknown
    kwarg), and record() additionally rejects any input payload that even
    NAMES a forecast-shaped key, so a mistaken or malicious payload is
    refused before construction is attempted. If it cannot enter the
    scoring apparatus by construction, it cannot corrupt it.

Two CLI verbs:
  emit    — build a plain-text prompt packet from the news archive for a
            symbol + time window, to paste into Claude Code by hand. Excludes
            Polygon's sentiment/insights fields from the packet, matching the
            same "kept out of the prompt" discipline polygon_news.py already
            applies to the operator's own reads.
  record  — validate a returned classification (JSON) against the schema
            below and append it. Validation failure raises; nothing partial
            or malformed is ever written.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import news_archive  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MANUAL_READS = ROOT / "data" / "daytrade" / "manual_reads.jsonl"

MANUAL_PREFIX = "mr-"          # visibly distinct from the operator's "op-"
DIRECTIONS = ("bullish", "bearish", "mixed", "unknown")
CONFIDENCE_LABELS = ("low", "medium", "high")

# Any of these appearing in a `record` input payload is refused outright —
# these are the field NAMES the promotion gate's scoring machinery consumes
# (forecast.py's Forecast.scenario_probs, an expected_r band). Naming them is
# enough to refuse; ManualRead has no slot for their values regardless.
FORBIDDEN_FORECAST_KEYS = frozenset({
    "scenario_probs", "expected_r", "expected_r_band", "probability",
    "probabilities", "recommendation", "confidence",
})


class ManualReadError(RuntimeError):
    """Malformed or forecast-shaped input. Never repaired, never partial."""


def _aware(ts: str, field_: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError) as e:
        raise ManualReadError(f"{field_}={ts!r} is not ISO-8601") from e
    if dt.tzinfo is None:
        raise ManualReadError(f"{field_}={ts!r} is timezone-naive — refusing to guess")
    return dt


@dataclass(frozen=True)
class ManualRead:
    """Fixed field set, by construction. No scenario_probs, no expected_r,
    no numeric confidence — nothing the promotion gate's scoring machinery
    could consume even if a reader ignored the file-boundary quarantine."""
    manual_read_id: str
    symbol: str
    window_start: str            # ISO tz-aware — start of the headline window read
    window_end: str              # ISO tz-aware — end of the headline window read
    article_ids: tuple           # archive article ids this read is based on
    direction: str               # bullish | bearish | mixed | unknown
    confidence_label: str        # low | medium | high — categorical, not numeric
    summary: str                 # free-text conclusion, owner/model prose
    authored_by: str             # model name + "via Claude Code (manual, not API)"
    authored_at: str             # ISO tz-aware wall-clock time of authoring
    source: str = "manual"
    caveats: str = ""            # anything that qualifies this read — anchoring,
                                 # hindsight, partial windows. Empty means the
                                 # author asserted none, not that none were checked.

    def __post_init__(self):
        if self.source != "manual":
            raise ManualReadError("source must be 'manual' — this lane has no other kind")
        if not self.manual_read_id.startswith(MANUAL_PREFIX):
            raise ManualReadError(
                f"manual_read_id {self.manual_read_id!r} must start with "
                f"{MANUAL_PREFIX!r} — operator ids ('op-...') must never be reachable here")
        if not self.symbol:
            raise ManualReadError("symbol is required")
        if self.direction not in DIRECTIONS:
            raise ManualReadError(f"unknown direction {self.direction!r}; known: {DIRECTIONS}")
        if self.confidence_label not in CONFIDENCE_LABELS:
            raise ManualReadError(
                f"unknown confidence_label {self.confidence_label!r}; known: {CONFIDENCE_LABELS}")
        if not self.article_ids:
            raise ManualReadError("a manual read must cite at least one archive article id")
        if not self.summary or not self.summary.strip():
            raise ManualReadError("summary is required and must be non-empty")
        if not self.authored_by or "Claude Code" not in self.authored_by:
            raise ManualReadError(
                "authored_by must name the model AND state it came via Claude "
                "Code, not the API")
        _aware(self.window_start, "window_start")
        _aware(self.window_end, "window_end")
        _aware(self.authored_at, "authored_at")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["article_ids"] = list(d["article_ids"])
        return d


def _known_article_ids(symbol: str) -> set[str]:
    return {r["article_id"] for r in news_archive.rows()
            if r.get("symbol") == symbol and r.get("article_id")}


# --------------------------------------------------------------------- emit

def emit_packet(symbol: str, since_hours: float) -> str:
    """Plain-text prompt packet: headlines only, no Polygon sentiment — the
    owner pastes this into Claude Code by hand. Raises if the archive itself
    is corrupt (news_archive.ArchiveError propagates)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    articles = [r for r in news_archive.rows()
                if r.get("symbol") == symbol and r.get("kind") == "headline"
                and r.get("published_utc")
                and _aware(r["published_utc"], "published_utc") >= cutoff]
    articles.sort(key=lambda r: r["published_utc"])

    lines = [
        f"MANUAL READ PACKET — {symbol} — last {since_hours}h "
        f"({len(articles)} headline(s))",
        "Classify direction (bullish/bearish/mixed/unknown), a confidence_label",
        "(low/medium/high), and a short summary. Cite article_ids you used.",
        "",
    ]
    if not articles:
        lines.append("(no archived headlines in this window)")
    for a in articles:
        lines.append(f"- article_id={a['article_id']}  published={a['published_utc']}")
        lines.append(f"  publisher={a.get('publisher', '')}")
        lines.append(f"  title: {a.get('title', '')}")
        if a.get("description"):
            lines.append(f"  desc: {a['description']}")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------- record

def build_manual_read(payload: dict) -> ManualRead:
    """Validate a returned classification against the schema and construct a
    ManualRead. Raises ManualReadError on anything missing, malformed, or
    forecast-shaped. Never accepts a partial read."""
    if not isinstance(payload, dict):
        raise ManualReadError("payload must be a JSON object")

    forbidden = FORBIDDEN_FORECAST_KEYS & set(payload)
    if forbidden:
        raise ManualReadError(
            f"payload names forecast-shaped field(s) {sorted(forbidden)} — "
            "a manual read cannot carry anything the promotion gate scores")

    required = ("symbol", "window_start", "window_end", "article_ids",
                "direction", "confidence_label", "summary", "authored_by_model")
    missing = [k for k in required if k not in payload or payload[k] in (None, "")]
    if missing:
        raise ManualReadError(f"missing required field(s): {missing}")

    # An unknown key is a caller believing it recorded something it did not.
    # FORBIDDEN_FORECAST_KEYS above already refuses the dangerous shapes loudly;
    # everything else used to be dropped SILENTLY, which is the same defect one
    # step quieter — a `caveats` disclosure was supplied on 2026-08-24 and
    # vanished, leaving a contaminated read looking clean. Refuse instead.
    accepted = set(required) | {"caveats"}
    unknown = sorted(set(payload) - accepted)
    if unknown:
        raise ManualReadError(
            f"payload names unknown field(s) {unknown} — refusing rather than "
            f"dropping them silently. Accepted fields: {sorted(accepted)}")

    symbol = payload["symbol"]
    article_ids = payload["article_ids"]
    if not isinstance(article_ids, list) or not article_ids:
        raise ManualReadError("article_ids must be a non-empty list")

    known = _known_article_ids(symbol)          # raises ArchiveError if corrupt
    unknown_ids = [a for a in article_ids if a not in known]
    if unknown_ids:
        raise ManualReadError(
            f"article_ids not found in the archive for {symbol}: {unknown_ids} — "
            "a manual read may only cite archived headlines")

    now = datetime.now(timezone.utc)
    manual_read_id = f"{MANUAL_PREFIX}{now.strftime('%Y%m%d-%H%M%S-%f')}-{symbol}"
    authored_by = f"{payload['authored_by_model']} via Claude Code (manual read, not Anthropic API)"

    return ManualRead(
        manual_read_id=manual_read_id,
        symbol=symbol,
        window_start=payload["window_start"],
        window_end=payload["window_end"],
        article_ids=tuple(article_ids),
        direction=payload["direction"],
        confidence_label=payload["confidence_label"],
        summary=payload["summary"],
        authored_by=authored_by,
        authored_at=now.isoformat(),
        source="manual",
        caveats=str(payload.get("caveats", "")),
    )


def record(payload: dict) -> ManualRead:
    mr = build_manual_read(payload)
    MANUAL_READS.parent.mkdir(parents=True, exist_ok=True)
    with MANUAL_READS.open("a") as fh:
        fh.write(json.dumps(mr.to_dict(), sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return mr


# ----------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="quarantined manual news-read lane (Claude Code, not API)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("emit", help="print a prompt packet to paste into Claude Code")
    e.add_argument("--symbol", default="NVDA")
    e.add_argument("--since-hours", type=float, default=4.0)

    r = sub.add_parser("record", help="validate + append a returned classification")
    r.add_argument("--file", default="-",
                    help="path to a JSON classification, or '-' for stdin")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "emit":
            print(emit_packet(a.symbol, a.since_hours))
            return 0

        raw = sys.stdin.read() if a.file == "-" else Path(a.file).read_text()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as ex:
            raise ManualReadError(f"input is not valid JSON: {ex}") from ex
        mr = record(payload)
        print(f"  recorded {mr.manual_read_id}  {mr.symbol}  {mr.direction} "
              f"({mr.confidence_label})  -> {MANUAL_READS}")
        return 0
    except (ManualReadError, news_archive.ArchiveError) as e:
        print(f"  REFUSED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
