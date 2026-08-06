#!/usr/bin/env python3
"""CONTEXT DIRECTIVE — spec 018. The narrow bridge, and nothing wider.

A directive is an EXPIRING, SCOPED statement of meaning carrying a requested
authority level. It is data. It cannot act.

The envelope has no field for an order type, a quantity, a stop price, or a fill
claim — not because those are filtered out, but because they were never
representable. A payload carrying them is MALFORMED. That is the strongest form
this guarantee can take: not a rule someone enforces, a shape that cannot hold
the thing.

Everything a directive asks for is translated into Stockfish's EXISTING bounded
vocabulary (None / 'tighten' / 'exit', plus a policy *candidate* that still has
to pass `policy_params` and the constitution) or refused with an auditable
reason. No new mechanical capability can enter through this envelope.

`evaluate()` is a PURE function of (directives, context, now). Same inputs, same
result, every time, regardless of input ordering — which is what makes an
acceptance decision reproducible from a log months later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal, Optional

SCHEMA_VERSION = "1"

Interrupt = Literal["TIGHTEN", "REDUCE_RISK", "INVALIDATE", "EMERGENCY"]

# Total order. EMERGENCY wins, always, deterministically.
PRECEDENCE: dict[Optional[str], int] = {
    None: 0, "TIGHTEN": 1, "REDUCE_RISK": 2, "INVALIDATE": 3, "EMERGENCY": 4,
}

# What each interrupt is ALLOWED to express, and the minimum authority to ask.
MIN_AUTHORITY: dict[Optional[str], int] = {
    None: 0, "TIGHTEN": 1, "REDUCE_RISK": 1, "INVALIDATE": 3, "EMERGENCY": 4,
}

DEFAULT_GRANTED_LEVEL = 1          # tighten only. Everything above is armed.

Reason = Literal["STALE", "NOT_YET_VALID", "OUT_OF_SCOPE", "OVER_AUTHORIZED",
                 "UNKNOWN_SCHEMA", "MALFORMED", "UNKNOWN_ENUM", "SUPERSEDED"]


class DirectiveError(RuntimeError):
    """Construction-time refusal. Runtime refusals are Rejections, not raises —
    a stale directive is an ordinary event, not a bug."""


@dataclass(frozen=True)
class Scope:
    market: list[str] = field(default_factory=list)
    sector: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    trade_id: Optional[str] = None

    def is_empty(self) -> bool:
        return not (self.market or self.sector or self.symbols or self.trade_id)


@dataclass(frozen=True)
class ContextDirective:
    directive_id: str
    scope: Scope
    issued_at: str
    expires_at: str
    model_version: str
    authority_level: int
    schema_version: str = SCHEMA_VERSION
    regime: dict = field(default_factory=dict)
    thesis_state: Optional[str] = None
    recommendation: Optional[str] = None
    interrupt: Optional[str] = None
    confidence: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.directive_id:
            raise DirectiveError("directive_id is required — an unidentifiable directive "
                                 "cannot be audited or superseded")
        if not (0 <= self.confidence <= 1):
            raise DirectiveError(f"confidence {self.confidence} outside [0, 1]")
        if self.interrupt is not None and self.interrupt not in PRECEDENCE:
            raise DirectiveError(f"unknown interrupt {self.interrupt!r}; "
                                 f"known: {sorted(k for k in PRECEDENCE if k)}")
        if not (0 <= self.authority_level <= 4):
            raise DirectiveError(f"authority_level {self.authority_level} outside 0..4")
        for f_ in ("issued_at", "expires_at"):
            _parse_ts(getattr(self, f_), f_)
        if _parse_ts(self.expires_at, "expires_at") <= _parse_ts(self.issued_at, "issued_at"):
            raise DirectiveError("expires_at must be strictly after issued_at")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scope"] = asdict(self.scope)
        return d

    @staticmethod
    def from_dict(d: dict) -> "ContextDirective":
        """Strict. Unknown keys are MALFORMED, which is how an order type or a
        quantity gets refused — the envelope simply has nowhere to put it."""
        known = set(ContextDirective.__dataclass_fields__)
        extra = set(d) - known
        if extra:
            raise DirectiveError(
                f"unknown field(s) {sorted(extra)} — the envelope has no field for an "
                "order type, quantity, stop price, or fill claim, and adding one here "
                "would be granting mechanical authority through the back door")
        kw = dict(d)
        kw["scope"] = Scope(**kw["scope"]) if isinstance(kw.get("scope"), dict) else kw.get("scope")
        return ContextDirective(**kw)


def _parse_ts(v, field_: str) -> datetime:
    if not isinstance(v, str):
        raise DirectiveError(f"{field_} must be an ISO-8601 string, got {type(v).__name__}")
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise DirectiveError(f"{field_}={v!r} is not ISO-8601: {e}") from e
    if dt.tzinfo is None:
        # An assumed timezone is how a directive quietly lives an hour longer
        # than intended. Refuse rather than guess.
        raise DirectiveError(f"{field_}={v!r} is timezone-naive — refusing to assume UTC")
    return dt


# ------------------------------------------------------------------ context

@dataclass(frozen=True)
class ReceiverContext:
    """What the RECEIVER knows. Authority is granted here, never claimed."""
    symbol: str
    trade_id: Optional[str] = None
    sector_members: frozenset[str] = frozenset()
    index_linked: bool = False
    granted_level: int = DEFAULT_GRANTED_LEVEL
    thesis_channel_armed: bool = False


@dataclass(frozen=True)
class Rejection:
    directive_id: str
    reason: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    """The full audit record. Every directive seen appears here, accepted or not."""
    accepted: list[ContextDirective]
    rejected: list[Rejection]
    interrupt: Optional[str]
    urgent: Optional[str]              # Stockfish's existing vocabulary
    policy_candidate: Optional[str]    # still subject to policy_params + constitution
    why: str

    def to_dict(self) -> dict:
        return {"accepted": [d.directive_id for d in self.accepted],
                "rejected": [r.to_dict() for r in self.rejected],
                "interrupt": self.interrupt, "urgent": self.urgent,
                "policy_candidate": self.policy_candidate, "why": self.why}


def in_scope(d: ContextDirective, ctx: ReceiverContext) -> bool:
    """An empty scope matches NOTHING. A directive naming no target is a
    configuration error, not a broadcast — an accidental market-wide directive
    is a far worse failure than a dropped one."""
    if d.scope.is_empty():
        return False
    if d.scope.trade_id is not None:
        return ctx.trade_id is not None and d.scope.trade_id == ctx.trade_id
    if ctx.symbol in d.scope.symbols:
        return True
    if any(m in ctx.sector_members for m in d.scope.sector):
        return True
    return bool(d.scope.market) and ctx.index_linked


def evaluate(directives: list[ContextDirective], ctx: ReceiverContext, *,
             now: Optional[datetime] = None) -> Decision:
    """Pure. Same inputs -> same Decision, independent of input ordering."""
    from stockfish_exit import INTENT
    now = now or datetime.now(timezone.utc)

    accepted: list[ContextDirective] = []
    rejected: list[Rejection] = []

    # Deterministic traversal so the result cannot depend on caller ordering.
    for d in sorted(directives, key=lambda x: (x.issued_at, x.directive_id)):
        if d.schema_version != SCHEMA_VERSION:
            rejected.append(Rejection(d.directive_id, "UNKNOWN_SCHEMA",
                            f"schema_version {d.schema_version!r} != {SCHEMA_VERSION!r}; "
                            "refusing a best-effort parse"))
            continue
        if _parse_ts(d.issued_at, "issued_at") > now:
            rejected.append(Rejection(d.directive_id, "NOT_YET_VALID",
                            f"issued_at {d.issued_at} is in the future"))
            continue
        if _parse_ts(d.expires_at, "expires_at") <= now:
            # Ignored, NEVER deleted — history is what a scorecard grades.
            rejected.append(Rejection(d.directive_id, "STALE",
                            f"expired at {d.expires_at}"))
            continue
        if not in_scope(d, ctx):
            rejected.append(Rejection(d.directive_id, "OUT_OF_SCOPE",
                            f"scope does not cover {ctx.symbol}"
                            + (" (scope is empty)" if d.scope.is_empty() else "")))
            continue
        if d.authority_level > ctx.granted_level:
            # Refused, not clamped: silently downgrading an emergency to a
            # tighten is exactly the failure the authority table prevents.
            rejected.append(Rejection(d.directive_id, "OVER_AUTHORIZED",
                            f"requests level {d.authority_level}, receiver grants "
                            f"{ctx.granted_level}"))
            continue
        need = MIN_AUTHORITY.get(d.interrupt, 99)
        if d.authority_level < need:
            rejected.append(Rejection(d.directive_id, "OVER_AUTHORIZED",
                            f"interrupt {d.interrupt} needs level {need}, directive "
                            f"claims {d.authority_level}"))
            continue
        if d.recommendation is not None and d.recommendation not in INTENT:
            rejected.append(Rejection(d.directive_id, "UNKNOWN_ENUM",
                            f"recommendation {d.recommendation!r} is not a known policy"))
            continue
        accepted.append(d)

    if not accepted:
        return Decision([], rejected, None, None, None,
                        f"no directive accepted ({len(rejected)} rejected)")

    winner = max(accepted, key=lambda d: (PRECEDENCE[d.interrupt], d.issued_at,
                                          d.directive_id))
    interrupt = winner.interrupt
    urgent, why = _to_urgent(interrupt, ctx)

    # A policy is only a CANDIDATE. policy_params still validates it and the
    # constitution still governs whatever actions result (spec 018 acceptance
    # case 1: recommendation changes policy only through Stockfish validation).
    policy = None
    if ctx.granted_level >= 2:
        pol_src = max((d for d in accepted if d.recommendation),
                      key=lambda d: (d.confidence, d.issued_at), default=None)
        if pol_src:
            policy = pol_src.recommendation
            why += f"; policy candidate {policy} from {pol_src.directive_id}"
    return Decision(accepted, rejected, interrupt, urgent, policy, why)


def _to_urgent(interrupt: Optional[str], ctx: ReceiverContext) -> tuple[Optional[str], str]:
    """Map an interrupt onto Stockfish's EXISTING vocabulary. Nothing else."""
    if interrupt is None:
        return None, "no interrupt among accepted directives"
    if interrupt in ("TIGHTEN", "REDUCE_RISK"):
        # REDUCE_RISK is a request to carry less risk; tightening is the bounded
        # expression of that. It does not get to size positions.
        return "tighten", f"{interrupt} -> tighten"
    if interrupt == "INVALIDATE":
        if ctx.thesis_channel_armed:
            return "exit", "INVALIDATE -> exit (thesis channel armed)"
        return "tighten", ("INVALIDATE would flatten, but the thesis channel is not "
                           "armed — tightening instead")
    if interrupt == "EMERGENCY":
        return "exit", "EMERGENCY -> exit"
    raise DirectiveError(f"unmapped interrupt {interrupt!r}")


# ------------------------------------------------------------------ tests

def _self_test() -> int:
    import sys, random, itertools
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from datetime import timedelta

    fails = 0
    now = datetime(2026, 8, 6, 15, 0, tzinfo=timezone.utc)
    iso = lambda dt: dt.isoformat()

    def mk(**kw):
        base = dict(directive_id="d1", scope=Scope(symbols=["NVDA"]),
                    issued_at=iso(now - timedelta(minutes=5)),
                    expires_at=iso(now + timedelta(minutes=25)),
                    model_version="az-1", authority_level=1)
        return ContextDirective(**{**base, **kw})

    ctx = lambda **kw: ReceiverContext(**{**dict(symbol="NVDA"), **kw})

    def expect(label, ds, c, reason=None, urgent="__any__"):
        nonlocal fails
        dec = evaluate(ds, c, now=now)
        if reason:
            got = {r.reason for r in dec.rejected}
            ok = reason in got
            print(f"  {'✅' if ok else '❌'} {label:38s} -> {sorted(got) or 'accepted'}")
        else:
            ok = (urgent == "__any__") or (dec.urgent == urgent)
            print(f"  {'✅' if ok else '❌'} {label:38s} -> urgent={dec.urgent} "
                  f"policy={dec.policy_candidate}")
        if not ok:
            fails += 1
        return dec

    print("one fixture per rejection reason:")
    expect("STALE", [mk(expires_at=iso(now - timedelta(minutes=1)))], ctx(), "STALE")
    expect("NOT_YET_VALID", [mk(issued_at=iso(now + timedelta(minutes=5)),
                                expires_at=iso(now + timedelta(minutes=30)))], ctx(),
           "NOT_YET_VALID")
    expect("OUT_OF_SCOPE (other symbol)", [mk(scope=Scope(symbols=["AMD"]))], ctx(),
           "OUT_OF_SCOPE")
    expect("OUT_OF_SCOPE (empty scope)", [mk(scope=Scope())], ctx(), "OUT_OF_SCOPE")
    expect("OVER_AUTHORIZED (above grant)", [mk(authority_level=4, interrupt="EMERGENCY")],
           ctx(granted_level=1), "OVER_AUTHORIZED")
    expect("OVER_AUTHORIZED (under-claimed)", [mk(authority_level=1, interrupt="EMERGENCY")],
           ctx(granted_level=4), "OVER_AUTHORIZED")
    expect("UNKNOWN_SCHEMA", [mk(schema_version="2")], ctx(), "UNKNOWN_SCHEMA")
    expect("UNKNOWN_ENUM (policy)", [mk(recommendation="TRAIL_MEDIUM")],
           ctx(granted_level=2), "UNKNOWN_ENUM")

    print("\nMALFORMED is structural — the envelope cannot hold these:")
    for label, payload in [
        ("order quantity", {"quantity": 100}),
        ("stop price", {"stop_price": 204.0}),
        ("order type", {"order_type": "market"}),
        ("fill claim", {"filled_qty": 50}),
    ]:
        d = mk().to_dict(); d.update(payload)
        try:
            ContextDirective.from_dict(d)
            print(f"  ❌ {label:38s} ACCEPTED"); fails += 1
        except DirectiveError as e:
            print(f"  ✅ {label:38s} -> {str(e)[:44]}")
    for label, kw in [("naive timestamp", dict(issued_at="2026-08-06T15:00:00")),
                      ("expires before issued", dict(expires_at=iso(now - timedelta(hours=2))))]:
        try:
            mk(**kw); print(f"  ❌ {label:38s} ACCEPTED"); fails += 1
        except DirectiveError as e:
            print(f"  ✅ {label:38s} -> {str(e)[:44]}")

    print("\ninterrupt -> Stockfish's existing vocabulary:")
    for it, lvl, armed, want in [("TIGHTEN", 1, False, "tighten"),
                                 ("REDUCE_RISK", 1, False, "tighten"),
                                 ("INVALIDATE", 3, False, "tighten"),
                                 ("INVALIDATE", 3, True, "exit"),
                                 ("EMERGENCY", 4, False, "exit")]:
        expect(f"{it} (armed={armed})", [mk(interrupt=it, authority_level=lvl)],
               ctx(granted_level=lvl, thesis_channel_armed=armed), urgent=want)

    print("\nprecedence is deterministic under shuffling:")
    ds = [mk(directive_id=f"d{i}", interrupt=it, authority_level=lv)
          for i, (it, lv) in enumerate([("TIGHTEN", 1), ("EMERGENCY", 4),
                                        ("REDUCE_RISK", 1), ("INVALIDATE", 3)])]
    random.seed(3)
    results = set()
    for _ in range(60):
        shuffled = ds[:]; random.shuffle(shuffled)
        results.add(evaluate(shuffled, ctx(granted_level=4), now=now).interrupt)
    ok = results == {"EMERGENCY"}
    print(f"  {'✅' if ok else '❌'} 60 shuffles -> {results}")
    if not ok:
        fails += 1

    print("\npurity:")
    outs = {json.dumps(evaluate(ds, ctx(granted_level=4), now=now).to_dict(), sort_keys=True)
            for _ in range(100)}
    ok = len(outs) == 1
    print(f"  {'✅' if ok else '❌'} 100 evaluations -> {len(outs)} distinct result(s)")
    if not ok:
        fails += 1

    print("\nscope matrix:")
    for label, sc, c, want in [
        ("symbol match", Scope(symbols=["NVDA"]), ctx(), True),
        ("sector match", Scope(sector=["SEMIS"]), ctx(sector_members=frozenset(["SEMIS"])), True),
        ("market + index-linked", Scope(market=["SPY"]), ctx(index_linked=True), True),
        ("market, NOT index-linked", Scope(market=["SPY"]), ctx(index_linked=False), False),
        ("trade_id match", Scope(trade_id="t1"), ctx(trade_id="t1"), True),
        ("trade_id mismatch", Scope(trade_id="t1"), ctx(trade_id="t2"), False),
        ("empty scope", Scope(), ctx(), False),
    ]:
        got = in_scope(mk(scope=sc), c)
        print(f"  {'✅' if got == want else '❌'} {label:38s} -> {got}")
        if got != want:
            fails += 1

    print("\nexpired directives are ignored, not deleted:")
    src = [mk(directive_id="old", expires_at=iso(now - timedelta(minutes=1)))]
    before = len(src)
    dec = evaluate(src, ctx(), now=now)
    ok = len(src) == before and any(r.directive_id == "old" for r in dec.rejected)
    print(f"  {'✅' if ok else '❌'} input list untouched, rejection recorded in audit")
    if not ok:
        fails += 1

    print(f"\n{'✅ ALL PASS' if not fails else f'❌ {fails} FAILURE(S)'}")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    print(__doc__)
    print("run with --self-test")
