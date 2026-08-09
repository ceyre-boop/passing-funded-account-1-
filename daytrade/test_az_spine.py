"""Card 020 — the AlphaZero → Stockfish spine, end to end.

One synthetic route (test-harness path; 016 owns the production caller):

    regime vector + scenario set + thesis
      -> ContextDirective (freshness / scope / authority via evaluate)
      -> policy_from_scenarios / urgency_for_thesis
      -> decide_exit -> constitution -> deterministic decision

`spine_decide` below is the route's first real composition and therefore the
FIRST regime consumer on a decision path — per 020 invariant 1 it uses
`require()`, so unavailable data raises instead of becoming a number. The three
integration invariants each have a named test here and a fault row in
specs/020_MUTATION_LOG.md (driver: mutation_check_spine.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from context_directive import ContextDirective, ReceiverContext, Scope, evaluate
from regime_vector import SPEC, Dimension, RegimeError, RegimeVector, require
from scenarios import Scenario, ScenarioSet
from stockfish_exit import (Action, Stage, TradeState, apply_actions, decide_exit,
                            policy_from_scenarios, policy_params, resolve_channels,
                            urgency_for_thesis)

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=timezone.utc)
ISO = NOW.isoformat()

# The spine's regime gate: the dimensions a decision is not allowed to proceed
# without. Chosen from SPEC (the two judgment dims are exactly the ones most
# likely to be absent — which is the point).
REQUIRED_DIMS = ("event_risk", "realized_volatility")


def spine_decide(state: TradeState, *, vec: RegimeVector,
                 scenario_set: ScenarioSet | None, thesis_state: str | None,
                 directives: list[ContextDirective], ctx: ReceiverContext,
                 now: datetime = NOW, fallback: str = "STATIC",
                 plan: dict | None = None):
    """The Gate-1 route. Every stage is the real module, no stand-ins."""
    # 1. regime gate — require(), never available()-with-a-default (invariant 1)
    for name in REQUIRED_DIMS:
        require(vec, name)

    # 2. directives through freshness/scope/authority
    decision = evaluate(directives, ctx, now=now)

    # 3. distribution -> one policy (freshness checked at the point of use)
    scenario_policy, _why = policy_from_scenarios(scenario_set, fallback=fallback,
                                                  now=now, plan=plan)

    # 4. thesis -> bounded urgency
    thesis_urgent = None
    if thesis_state is not None:
        thesis_urgent, _ = urgency_for_thesis(thesis_state,
                                              armed=ctx.thesis_channel_armed)

    # 5. arbitration is MECHANICS and lives in the engine (ruling 1): most
    #    protective urgency wins; a directive candidate may only tighten
    policy, urgent, _resolve_why = resolve_channels(
        policy=scenario_policy, policy_candidate=decision.policy_candidate,
        urgents=(decision.urgent, thesis_urgent), plan=plan)

    # 6. mechanics: policy params onto state, engine decides, constitution
    #    enforces inside the one funnel
    for k, v in policy_params(policy, plan).items():
        setattr(state, k, v)
    state.urgent = urgent
    acts = decide_exit(state)
    apply_actions(state, acts, set())
    return acts, policy, urgent


# ------------------------------------------------------------------ fixtures

def make_vector(unavailable: set[str] = frozenset()) -> RegimeVector:
    dims = {}
    for name, (lo, hi, _d) in SPEC.items():
        if name in unavailable:
            dims[name] = Dimension(name, None, "unavailable", "fixture: not supplied")
        else:
            src = "judged" if name in ("market_breadth", "event_risk") else "computed"
            dims[name] = Dimension(name, (lo + hi) / 2.0, src, "fixture")
    return RegimeVector(ts=ISO, symbol="NVDA", dims=dims)


def make_scenarios(ts: str = ISO, top_policy: str = "RIDE",
                   top_prob: float = 0.70) -> ScenarioSet:
    return ScenarioSet(
        ts=ts, symbol="NVDA", source="test",
        scenarios=[Scenario("bull_continuation", top_prob, 0.7, 120,
                            ["held while peers broke"], ["loses the level"],
                            ["NVDA"], top_policy),
                   Scenario("range_consolidation", round(1.0 - top_prob, 6), 0.6, 120,
                            ["no catalyst"], ["clean break on volume"],
                            ["NVDA"], "DEFEND")])


def make_directive(*, interrupt: str | None = None, authority: int = 1,
                   issued: datetime | None = None, expires: datetime | None = None,
                   recommendation: str | None = None,
                   did: str = "d1") -> ContextDirective:
    issued = issued or NOW - timedelta(minutes=5)
    expires = expires or NOW + timedelta(minutes=25)
    return ContextDirective(directive_id=did, scope=Scope(symbols=["NVDA"]),
                            issued_at=issued.isoformat(), expires_at=expires.isoformat(),
                            model_version="az-test", authority_level=authority,
                            interrupt=interrupt, recommendation=recommendation,
                            confidence=0.8 if recommendation else 0.0)


def make_state(**kw) -> TradeState:
    base = dict(direction=1, entry=200.0, qty=100, price=201.0,
                sl=199.0, tp1=201.0, tp2=202.14, trail_dist=1.0)
    base.update(kw)
    return TradeState(**base)


def ctx(**kw) -> ReceiverContext:
    return ReceiverContext(**{**dict(symbol="NVDA"), **kw})


# --------------------------------------------------- the route, end to end

def test_spine_runs_end_to_end_and_is_deterministic():
    """The whole route on honest inputs, twice, identical both times."""
    def once():
        s = make_state()
        acts, policy, urgent = spine_decide(
            s, vec=make_vector(), scenario_set=make_scenarios(),
            thesis_state="THESIS_CONFIRMED",
            directives=[make_directive(interrupt="TIGHTEN")], ctx=ctx())
        return ([(a.kind, a.sl, a.fraction) for a in acts], policy, urgent,
                s.sl, s.stage.value, s.state_revision)

    a, b = once(), once()
    assert a == b
    acts, policy, urgent = a[0], a[1], a[2]
    assert policy == "RIDE"                       # decisive top scenario selected
    assert urgent == "tighten"                    # TIGHTEN directive accepted


# ----------------------------------------------------------- invariant 1

def test_unavailable_regime_never_becomes_a_number():
    """A vector whose required dimension is unavailable STOPS the route with
    RegimeError — it never proceeds on a defaulted value. The fault (switching
    the spine back to available().get(name, 0.0)) proceeds happily; the
    mutation row proves this test is what forbids it."""
    s = make_state()
    with pytest.raises(RegimeError):
        spine_decide(s, vec=make_vector(unavailable={"event_risk"}),
                     scenario_set=make_scenarios(), thesis_state=None,
                     directives=[], ctx=ctx())
    assert s.state_revision == 0                  # nothing was applied
    assert s.sl == 199.0

    # control: every required dimension present -> the route runs
    spine_decide(make_state(), vec=make_vector(), scenario_set=make_scenarios(),
                 thesis_state=None, directives=[], ctx=ctx())


# ----------------------------------------------------------- invariant 2

def test_stale_state_cannot_influence_decision():
    """A STALE decisive distribution and an EXPIRED EMERGENCY directive leave
    the decision bit-identical to their complete absence."""
    def run(scenario_set, directives):
        s = make_state()
        acts, policy, urgent = spine_decide(
            s, vec=make_vector(), scenario_set=scenario_set, thesis_state=None,
            directives=directives, ctx=ctx(granted_level=4))
        return ([(a.kind, a.sl, a.fraction) for a in acts], policy, urgent, s.sl)

    baseline = run(None, [])

    stale_set = make_scenarios(ts=(NOW - timedelta(minutes=45)).isoformat())  # max 30m
    expired_emergency = make_directive(interrupt="EMERGENCY", authority=4,
                                       issued=NOW - timedelta(hours=2),
                                       expires=NOW - timedelta(hours=1))
    with_stale = run(stale_set, [expired_emergency])

    assert with_stale == baseline
    assert with_stale[1] == "STATIC"              # the fallback, not stale RIDE
    assert with_stale[2] is None                  # the dead EMERGENCY moved nothing

    # sanity: the SAME inputs, fresh, DO change the decision — so the equality
    # above is meaningful, not vacuous
    fresh = run(make_scenarios(), [make_directive(interrupt="EMERGENCY", authority=4)])
    assert fresh != baseline


# ----------------------------------------------------------- invariant 3

def test_thesis_oscillation_cannot_loosen_protection():
    """WEAKENING -> CONFIRMED -> WEAKENING cycles may tighten and un-tighten the
    trail CANDIDATE, but the applied stop is monotone — recovery never retreats
    it. Locks the max-over-layers accident of 019:127-129 as a contract."""
    s = make_state(stage=Stage.SCALED, price=203.0, sl=201.0, hwm=203.0)
    plan = None
    walk = [                                       # (price, thesis_state)
        (203.4, "THESIS_WEAKENING"),               # tighten: trail 1.5*0.5=0.75 off hwm
        (203.4, "THESIS_CONFIRMED"),               # recovery widens the CANDIDATE only
        (203.9, "THESIS_WEAKENING"),
        (203.9, "THESIS_CONFIRMED"),
        (204.5, "THESIS_WEAKENING"),
    ]
    sls = [s.sl]
    for price, thesis_state in walk:
        s.price = price
        spine_decide(s, vec=make_vector(),
                     scenario_set=make_scenarios(top_policy="RIDE"),
                     thesis_state=thesis_state, directives=[], ctx=ctx(),
                     plan=plan)
        assert s.stage is not Stage.CLOSED, "oscillation must not flatten"
        sls.append(s.sl)

    assert all(b >= a for a, b in zip(sls, sls[1:])), f"stop retreated: {sls}"
    assert sls[-1] > sls[0]                        # and it genuinely tightened


# --------------------------------------------- channel arbitration (engine)

def test_resolve_channels_urgency_is_most_protective():
    """exit > tighten > None, never averaged; unknown values raise."""
    _, urgent, _ = resolve_channels(policy="STATIC", policy_candidate=None,
                                    urgents=(None, "tighten"))
    assert urgent == "tighten"
    _, urgent, _ = resolve_channels(policy="STATIC", policy_candidate=None,
                                    urgents=("tighten", "exit", None))
    assert urgent == "exit"
    _, urgent, _ = resolve_channels(policy="STATIC", policy_candidate=None, urgents=())
    assert urgent is None
    with pytest.raises(ValueError):
        resolve_channels(policy="STATIC", policy_candidate=None,
                         urgents=("panic",))


def test_resolve_channels_candidate_only_tightens():
    """A directive candidate may TIGHTEN the policy, never loosen it, and an
    unsatisfiable candidate is ignored with the reason logged."""
    p, _, why = resolve_channels(policy="RIDE", policy_candidate="DEFEND", urgents=())
    assert p == "DEFEND" and "tightened" in why           # tighter candidate wins
    p, _, why = resolve_channels(policy="DEFEND", policy_candidate="RIDE", urgents=())
    assert p == "DEFEND" and "loosen" in why              # looser candidate refused
    p, _, why = resolve_channels(policy="DEFEND", policy_candidate="EVENT",
                                 urgents=(), plan=None)   # EVENT needs flatten_at_et
    assert p == "DEFEND" and "cannot satisfy" in why


def test_directive_candidate_cannot_loosen_policy_end_to_end():
    """Through the whole spine: an authorized directive recommending a LOOSER
    policy than the decisive scenario set does not loosen it; a TIGHTER
    recommendation does run. (The arbitration lives in the engine — this pins
    the composition, the unit tests above pin the rule.)"""
    _, policy, _ = spine_decide(
        make_state(), vec=make_vector(),
        scenario_set=make_scenarios(top_policy="DEFEND"),   # decisive DEFEND
        thesis_state=None,
        directives=[make_directive(recommendation="RIDE")], # looser candidate
        ctx=ctx(granted_level=2))
    assert policy == "DEFEND"

    _, policy, _ = spine_decide(
        make_state(), vec=make_vector(),
        scenario_set=make_scenarios(top_policy="RIDE"),     # decisive RIDE
        thesis_state=None,
        directives=[make_directive(recommendation="DEFEND")],  # tighter candidate
        ctx=ctx(granted_level=2))
    assert policy == "DEFEND"


# ------------------------------------------------- directive gate specifics

def test_over_authorized_directive_moves_nothing():
    """An EMERGENCY asking level 4 of a level-1 receiver is refused, not
    clamped — the decision matches having no directive at all."""
    s1, s2 = make_state(), make_state()
    acts1, _, urgent1 = spine_decide(
        s1, vec=make_vector(), scenario_set=None, thesis_state=None,
        directives=[make_directive(interrupt="EMERGENCY", authority=4)],
        ctx=ctx(granted_level=1))
    acts2, _, urgent2 = spine_decide(
        s2, vec=make_vector(), scenario_set=None, thesis_state=None,
        directives=[], ctx=ctx(granted_level=1))
    assert urgent1 is urgent2 is None
    assert [(a.kind, a.sl) for a in acts1] == [(a.kind, a.sl) for a in acts2]
