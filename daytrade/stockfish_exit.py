#!/usr/bin/env python3
"""STOCKFISH v3 — the deterministic trade operating system.

Answers exactly one question: *given the approved trade plan and the current
facts, what actions are legally allowed right now?* It has no opinion about the
market. AlphaZero communicates meaning; Stockfish controls mechanics, and that
boundary does not move as either side grows (specs/000 ruling 1).

THREE STRUCTURES, one file:

  1. LIFECYCLE STAGES — a position moves ENTERED -> PROTECTED -> SCALED ->
     RUNNER -> CLOSING -> CLOSED, monotonically. Each stage declares which
     actions are legal in it, so an impossible transition is structurally
     unavailable rather than merely unlikely.

  2. LAYERED PROTECTION — several candidate stops exist at once (catastrophic,
     thesis, time-decay, breakeven, volatility, profit-lock, trail) and the most
     protective ACTIVE one wins. This makes "the stop never loosens" an
     invariant of the selection rule instead of an accident of branch ordering,
     and it makes every stop move explain itself: which layer took over, from
     what level to what level.

  3. INTENT POLICIES — DEFEND / HARVEST / RIDE / EVENT / SALVAGE. Named intent
     expands into deterministic mechanical parameters. The parameters remain
     authoritative on TradeState so spec 008's ceiling can sweep configurations
     the shipped policy table does not contain.

DOCTRINE ENCODED (I_AM_A_GOOD_TRADER.md):
  TP1 = don't lose on the day  -> breakeven layer arms
  TP2 = the set goal ($300)    -> bank the goal, profit-lock layer arms
  TP3 = trail the rest         -> trail layer, "you never know when it rips"
  Urgent channel (ALPHAZERO): 'exit' flattens; 'tighten' halves the trail.
  The entry doesn't matter, the exit is the profession.

SAFETY (APEX): never talks to a broker. Emits advice actions; a human executes.
Fail loud: malformed state raises, unknown policy raises, an action illegal in
the current stage raises. Nothing is ever silently downgraded to a safe guess.

v3 NOTE ON THE REPLAY BASELINE: at TP2 the v2 engine emitted two stop moves
(to TP1, then to the trail). Under most-protective-wins both are candidates at
that instant and the trail already dominates, so v3 emits one. The stop LEVELS
are identical; only the announcement granularity changed. The v2 log is kept at
data/daytrade/replay_expected/v2_baseline.log beside the v3 one so the change is
reviewable. The invariant that actually matters — runner log == harness log —
is unaffected, and every one of the ceiling's 9,576 per-config R values is
unchanged, which is the real regression test.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List

# ---------------------------------------------------------------- lifecycle

class Stage(str, Enum):
    ENTERED   = "ENTERED"      # hard stop + emergency exit only
    PROTECTED = "PROTECTED"    # breakeven armed; first reduction allowed
    SCALED    = "SCALED"       # goal banked; profit ladder + volatility allowed
    RUNNER    = "RUNNER"       # trailing only
    CLOSING   = "CLOSING"      # no new adjustments; reconcile remaining orders
    CLOSED    = "CLOSED"       # terminal

_ORDER = {s: i for i, s in enumerate(
    [Stage.ENTERED, Stage.PROTECTED, Stage.SCALED, Stage.RUNNER,
     Stage.CLOSING, Stage.CLOSED])}

# What each stage is allowed to emit. A trade in RUNNER cannot take a new
# partial; a trade in CLOSING cannot adjust anything. Violations raise.
ALLOWED_ACTIONS = {
    Stage.ENTERED:   frozenset({"HOLD", "EXIT_ALL"}),
    Stage.PROTECTED: frozenset({"HOLD", "MOVE_SL", "TAKE_PARTIAL", "EXIT_ALL"}),
    Stage.SCALED:    frozenset({"HOLD", "MOVE_SL", "TAKE_PARTIAL", "EXIT_ALL"}),
    Stage.RUNNER:    frozenset({"HOLD", "MOVE_SL", "EXIT_ALL"}),
    Stage.CLOSING:   frozenset({"EXIT_ALL"}),
    Stage.CLOSED:    frozenset(),
}


class StageError(RuntimeError):
    """An illegal transition or an action not permitted in the current stage."""


def advance(state: "TradeState", target: Stage) -> None:
    """Move the position forward. Backwards is structurally impossible.

    This is the guarantee the lifecycle exists for: a trade can never fall back
    from RUNNER into an earlier, riskier state. It already held in v2 because
    the tp1/tp2 flags were never unset — here it is enforced rather than
    inherited from the order the branches happen to run in.
    """
    if _ORDER[target] < _ORDER[state.stage]:
        raise StageError(f"cannot go {state.stage.value} -> {target.value}: "
                         "lifecycle is forward-only")
    state.stage = target


# ------------------------------------------------------------------- helpers

def _et_minutes(hhmm: str, field_: str) -> int:
    """'HH:MM' -> minutes since midnight ET. Malformed input raises."""
    try:
        h, m = hhmm.strip().split(":")
        h, m = int(h), int(m)
    except Exception as e:
        raise ValueError(f"{field_} must be 'HH:MM', got {hhmm!r}") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"{field_} out of range: {hhmm!r}")
    return h * 60 + m


# --------------------------------------------------------------------- state

@dataclass
class TradeState:
    direction: int              # +1 long, -1 short
    entry: float
    qty: float
    price: float                # current
    sl: float                   # the effective stop, as last applied
    tp1: float                  # breakeven trigger
    tp2: float                  # day-goal level ($300 worth)
    trail_dist: float           # trail distance beyond TP2 (points)
    hwm: float = None           # best price seen since entry (favorable extreme)
    urgent: Optional[str] = None    # None | 'tighten' | 'exit'  (from ALPHAZERO)
    goal_fraction: float = 0.5      # fraction banked at TP2 (spec 008: partial_frac)
    # Time-flatten (doctrine: flat before 11:00). BOTH must be supplied for the
    # rule to arm; either left None means no clock was given, so no time exit.
    flatten_at_et: Optional[str] = None     # 'HH:MM' ET, e.g. '11:00'
    now_et: Optional[str] = None            # 'HH:MM' ET, the caller's clock
    # --- mechanical params. Authoritative; policy names are sugar over these.
    trail_mult: Optional[float] = 1.0       # multiplies trail_dist; None = never trail
    be_arm_frac: float = 1.0                # breakeven arms at this fraction of TP1
    hold_past_tp2: bool = True              # False = flatten fully at TP2
    exit_policy: str = "DEFAULT"            # provenance only
    # --- lifecycle
    stage: Stage = Stage.ENTERED
    catastrophic_sl: Optional[float] = None  # the ORIGINAL plan stop; never removed
    # --- inactive layers, awaiting their inputs (see stop_candidates)
    atr: Optional[float] = None              # volatility layer needs this
    thesis_sl: Optional[float] = None        # AlphaZero invalidation level
    # --- constitution (spec 011). Additive: state, not emitted actions, so the
    #     v3 replay log is unchanged. Missing in older session JSONL reads as 0.
    state_revision: int = 0                  # monotonic; advances on MUTATING actions only
    last_now_et: Optional[str] = None        # for C004, no-stale-facts

    def __post_init__(self):
        if self.direction not in (+1, -1): raise ValueError("direction must be +1/-1")
        for f_ in ("entry", "qty", "price", "sl", "tp1", "tp2", "trail_dist"):
            v = getattr(self, f_)
            if v is None or (isinstance(v, (int, float)) and v != v):
                raise ValueError(f"bad field {f_}={v}")   # fail loud, never guess
        for f_ in ("flatten_at_et", "now_et"):            # validate iff supplied
            v = getattr(self, f_)
            if v is not None: _et_minutes(v, f_)
        if self.trail_mult is not None and self.trail_mult <= 0:
            raise ValueError(f"trail_mult must be positive or None, got {self.trail_mult}")
        if not (0 < self.be_arm_frac <= 1.0):
            raise ValueError(f"be_arm_frac must be in (0, 1], got {self.be_arm_frac}")
        if not (0 <= self.goal_fraction <= 1.0):
            raise ValueError(f"goal_fraction must be in [0, 1], got {self.goal_fraction}")
        if self.hwm is None: self.hwm = self.price
        if self.catastrophic_sl is None: self.catastrophic_sl = self.sl

    # Kept as derived properties: runner.py writes both into every session-log
    # record, and the harness diffs against that schema. Changing it would break
    # the diffability the architecture rests on.
    @property
    def tp1_done(self) -> bool: return _ORDER[self.stage] >= _ORDER[Stage.PROTECTED]

    @property
    def tp2_done(self) -> bool: return _ORDER[self.stage] >= _ORDER[Stage.SCALED]


@dataclass
class Action:
    kind: str                   # HOLD | MOVE_SL | TAKE_PARTIAL | EXIT_ALL
    sl: Optional[float] = None
    fraction: Optional[float] = None
    reason: str = ""


@dataclass(frozen=True)
class StopCandidate:
    """One reason a stop could sit at a level. Several are live at once."""
    name: str
    level: Optional[float]
    active: bool
    reason: str


# -------------------------------------------------------------- the layers

def _fav(direction: int, a: float, b: float) -> float:
    return max(a, b) if direction > 0 else min(a, b)


def _crossed(state: TradeState, level: float) -> bool:
    return state.price >= level if state.direction > 0 else state.price <= level


def _arm_level(state: TradeState) -> float:
    """Where breakeven arms. be_arm_frac < 1 arms it EARLIER.

    The == 1.0 branch returns state.tp1 verbatim rather than recomputing it:
    entry + (tp1-entry)*1.0 is not bit-identical to tp1 in floating point and
    would silently shift the trigger.
    """
    return (state.tp1 if state.be_arm_frac == 1.0 else
            state.entry + (state.tp1 - state.entry) * state.be_arm_frac)


def trail_distance(state: TradeState) -> Optional[float]:
    """Effective trail width. 'tighten' MULTIPLIES with the policy rather than
    replacing it: RIDE + tighten = 1.5 * 0.5 = 0.75x, not 0.5x."""
    if state.trail_mult is None:
        return None
    return state.trail_dist * state.trail_mult * (0.5 if state.urgent == "tighten" else 1.0)


def stop_candidates(state: TradeState) -> List[StopCandidate]:
    """Every stop layer, active or not. Inactive layers are CONSTRUCTED and
    returned carrying the reason they are dark — they are not commented-out code
    and not silently absent, so `daytrade/stockfish_exit.py --layers` shows the
    whole protection picture including what is missing and why.
    """
    d, scaled = state.direction, state.tp2_done
    trail = trail_distance(state)

    return [
        StopCandidate("catastrophic", state.catastrophic_sl, True,
                      "original plan stop — never removed, never loosened"),

        StopCandidate("thesis", state.thesis_sl, state.thesis_sl is not None,
                      "AlphaZero invalidation level"
                      if state.thesis_sl is not None else
                      "INACTIVE: needs an invalidation level from the classifier; "
                      "daytrade/regime.py is a stub"),

        StopCandidate("time_decay", None, False,
                      "INACTIVE: time pressure is expressed as the flatten_at_et "
                      "EXIT_ALL rule, not as a stop level. A tightening schedule "
                      "is a separate design — see the [SKETCH] note in this file"),

        StopCandidate("breakeven", state.entry, state.tp1_done,
                      "TP1: breakeven armed, day cannot go red"
                      + ("" if state.be_arm_frac == 1.0
                         else f" (early, {state.be_arm_frac:g} of TP1)")),

        StopCandidate("volatility", None, False,
                      "INACTIVE: needs ATR on TradeState; no caller supplies it. "
                      "Would sit at hwm - k*ATR to survive normal noise"),

        StopCandidate("profit_lock", state.tp1, scaled,
                      "TP2 banked: stop rides to TP1"),

        # NOTE the round(): only the trail level is rounded, and only to 4dp.
        # breakeven and profit_lock sit on prices the plan already chose, so
        # rounding them would move a level the trader set. v2 had this same
        # asymmetry implicitly; making it explicit here is what keeps all 9,576
        # ceiling R values bit-identical across the refactor.
        StopCandidate("trail", (None if trail is None else round(state.hwm - d * trail, 4)),
                      scaled and trail is not None,
                      "" if trail is None else
                      f"trail {'tightened ' if state.urgent == 'tighten' else ''}"
                      f"{trail:g} pts off HWM"),
    ]


def effective_stop(state: TradeState) -> StopCandidate:
    """The most protective ACTIVE candidate. This is where the never-loosen
    invariant lives.

    'Most protective' is the highest level for a long, the lowest for a short.
    Because every active layer is either constant or monotonically tightening
    (the trail rides a monotonic high-water mark), the maximum over them is
    monotonic too — so the effective stop cannot loosen. In v2 that property was
    real but accidental, held up by branch ordering plus a single `cur_ok` check;
    nothing prevented a future branch from emitting a looser MOVE_SL, since
    apply_action assigns whatever it is handed.
    """
    live = [c for c in stop_candidates(state) if c.active and c.level is not None]
    if not live:
        raise StageError("no active stop layer — a position is never unprotected")
    return max(live, key=lambda c: c.level * state.direction)


# ------------------------------------------------------------ intent policies

# Named intent -> mechanical parameters. Values are informed by the 2026-08-03
# ceiling measurement, not by taste: of the oracle's 24 picks, 22 used NO
# trailing, 22 armed breakeven early at 0.25R, and all 24 carried a flatten
# time. Trail width — the axis v2's presets differed on — was reached for twice.
#                 trail_mult  be_arm_frac  hold_past_tp2
INTENT = {
    "DEFEND":     (None,       0.25,        True),   # risk reduction first
    "HARVEST":    (None,       0.50,        False),  # realize the goal, be done
    "RIDE":       (1.50,       1.00,        True),   # maximize participation
    "EVENT":      (None,       0.25,        True),   # out before a known catalyst
    "SALVAGE":    (0.25,       0.25,        True),   # thesis weakened, minimize damage
    # --- legacy names, kept as aliases for one release. ceiling.narrow_space(),
    #     data/daytrade/policy_today.json and TRAINING_DAY_1.md all use these,
    #     and breaking the runbook is a bad trade for a rename.
    "DEFAULT":      (1.0,      1.00,        True),
    "STATIC":       (None,     1.00,        True),
    "TRAIL_WIDE":   (1.50,     1.00,        True),
    "TRAIL_TIGHT":  (0.50,     0.50,        True),
    "SCRATCH_FAST": (0.25,     0.25,        True),
}

# Policies whose meaning depends on a field the plan must supply.
REQUIRES = {"EVENT": ("flatten_at_et",)}

# Policies that exist and may be chosen BY HAND, but that nothing in the system
# may emit automatically, because the signal they respond to does not exist yet.
NOT_AUTO_EMITTABLE = {
    "SALVAGE": "means 'the original thesis has weakened' — only AlphaZero can "
               "assert that, and daytrade/regime.py is a stub",
    "SCRATCH_FAST": "reserved for a confirmed sweep against the position; "
                    "mis-specified it becomes 'panic out of every wick'",
}
POLICY_PARAMS = INTENT          # back-compat alias for spec 003 readers


# How protective each policy is. Lower = tighter grip on capital. Used when a
# scenario distribution is NOT decisive: under genuine uncertainty the engine
# takes the most conservative recommendation on the table rather than the most
# probable one, because a 42% call is not a mandate.
CONSERVATISM = {"SALVAGE": 0, "EVENT": 1, "DEFEND": 2, "SCRATCH_FAST": 0,
                "TRAIL_TIGHT": 1, "STATIC": 2, "DEFAULT": 3,
                "HARVEST": 3, "TRAIL_WIDE": 4, "RIDE": 4}


def _satisfiable(policy: str, plan: dict | None) -> bool:
    """Can this plan actually run this policy? EVENT means 'be out before the
    catalyst' and needs a catalyst time; picking it for a plan with no
    flatten_at_et would raise at TradeState construction — at 09:31, live."""
    try:
        policy_params(policy, plan)
        return True
    except ValueError:
        return False


def policy_from_scenarios(scenario_set, *, fallback: str, decisive_floor: float = 0.50,
                          now=None, plan: dict | None = None) -> tuple[str, str]:
    """Resolve a scenario DISTRIBUTION into the one policy actually in force.

    This is mechanics, so it lives here rather than in scenarios.py — AlphaZero
    recommends a policy per scenario, Stockfish decides which one runs (ruling 1).
    Returns (policy, why) so the choice is always explainable in the log.

    Three rules, in order:
      1. A STALE distribution steers nothing. Freshness is checked at the point
         of use, not trusted from the producer.
      2. A distribution with no dominant scenario resolves to the MOST
         CONSERVATIVE policy among its scenarios — uncertainty tightens the
         grip, it does not average opinions into a middle that no scenario
         actually recommended.
      3. Only a decisive distribution gets to select its top scenario's policy.
    """
    if scenario_set is None:
        return fallback, "no scenario set — using the plan's policy"
    if not scenario_set.is_fresh(now):
        return fallback, (f"scenario set is STALE ({scenario_set.age_min(now):.0f}m old, "
                          f"max {scenario_set.max_age_min}m) — refusing to steer on it")
    if not scenario_set.is_decisive(decisive_floor):
        cands = {s.recommended_policy for s in scenario_set.scenarios}
        usable = {c for c in cands if _satisfiable(c, plan)}
        if not usable:
            return fallback, (f"no scenario above {decisive_floor:.0%} and none of "
                              f"{sorted(cands)} is satisfiable by this plan")
        pick = min(usable, key=lambda p: CONSERVATISM.get(p, 99))
        skipped = sorted(cands - usable)
        return pick, (f"no scenario above {decisive_floor:.0%} "
                      f"(top {scenario_set.top.name} at {scenario_set.top.probability:.0%}) — "
                      f"most conservative of {sorted(usable)}"
                      + (f"; skipped {skipped} (plan cannot satisfy)" if skipped else ""))
    top = scenario_set.top
    if not _satisfiable(top.recommended_policy, plan):
        return fallback, (f"{top.name} at {top.probability:.0%} recommends "
                          f"{top.recommended_policy}, which this plan cannot satisfy "
                          f"(e.g. EVENT with no flatten_at_et) — using the plan's policy")
    return top.recommended_policy, (f"{top.name} at {top.probability:.0%} "
                                    f"(confidence {top.confidence:.2f})")


def urgency_for_thesis(state: str, *, armed: bool = False) -> tuple[Optional[str], str]:
    """Translate a THESIS STATE into the engine's EXISTING urgent vocabulary.

    Deliberately creates no new mechanical authority. A thesis state maps onto
    None / 'tighten' / 'exit' — the same three values the ALPHAZERO news channel
    already uses — so every path through decide_exit is one the parity tests and
    the replay baselines already cover. The thesis monitor produces meaning; this
    function is the whole of its mechanical reach.

    INVALIDATED and EXPIRED are gated behind `armed` for the same reason the
    news urgency is: flattening a live position is authority a signal earns by
    being scored, not one it is handed on arrival. Unarmed, they tighten and say
    loudly what they would have done.
    """
    if state in ("THESIS_PENDING", "THESIS_CONFIRMED"):
        return None, f"{state}: no change"
    if state == "THESIS_WEAKENING":
        return "tighten", "THESIS_WEAKENING: halving the trail"
    if state in ("THESIS_INVALIDATED", "THESIS_EXPIRED"):
        if armed:
            return "exit", f"{state}: the reason to hold is gone — flatten"
        return "tighten", (f"{state}: would FLATTEN, but the thesis channel is not armed "
                           f"— tightening instead. Arm it deliberately once it is scored.")
    raise ValueError(f"unknown thesis state {state!r}")


# The bounded urgency vocabulary, ranked by protectiveness. Module-level so
# arbitration and its tests share ONE definition.
URGENCY_RANK = {None: 0, "tighten": 1, "exit": 2}


def resolve_channels(*, policy: str, policy_candidate: Optional[str],
                     urgents: tuple = (), plan: dict | None = None
                     ) -> tuple[str, Optional[str], str]:
    """Arbitrate between AlphaZero channels. This is MECHANICS — which policy
    and which urgency actually RUN — so it lives here, not in the composition
    that gathers the channels (ruling 1; found by the 020 adversarial review
    living untested in the spine harness).

    Two rules, both conservative by doctrine:

      URGENCY — the most protective of the supplied urgencies wins
      (exit > tighten > None). Protection is never averaged down; an unknown
      urgency value raises rather than ranking as harmless.

      POLICY — an authorized directive candidate may only make the policy MORE
      conservative (CONSERVATISM), never less: when the candidate and the
      scenario-derived policy disagree, the tighter of the two runs. A
      candidate this plan cannot satisfy (e.g. EVENT with no flatten_at_et) is
      ignored with the reason in `why` — never a crash at 09:31, never a
      silent loosen.
    """
    for u in urgents:
        if u not in URGENCY_RANK:
            raise ValueError(f"unknown urgency {u!r}; known: {list(URGENCY_RANK)}")
    urgent = max(urgents, default=None, key=lambda u: URGENCY_RANK[u])

    why = f"policy {policy}"
    if policy_candidate is not None:
        if not _satisfiable(policy_candidate, plan):
            why += (f"; directive candidate {policy_candidate} ignored — "
                    "plan cannot satisfy it")
        else:
            chosen = min((policy, policy_candidate),
                         key=lambda p: CONSERVATISM.get(p, 99))
            if chosen != policy:
                why = (f"policy {chosen} — directive candidate tightened "
                       f"{policy} -> {chosen}")
            else:
                why += (f"; directive candidate {policy_candidate} would loosen "
                        "— refused, scenario policy stands")
            policy = chosen
    return policy, urgent, why


def policy_params(name: str, plan: dict | None = None) -> dict:
    """Expand a named intent into TradeState kwargs.

    An unknown name raises — a typo'd policy must stop the session, never
    silently select a different risk profile. A policy with unmet requirements
    also raises: EVENT means "be out before the catalyst", so EVENT without a
    catalyst time is an error, not a quiet fallback to DEFEND.
    """
    if name not in INTENT:
        raise ValueError(f"unknown exit_policy {name!r}; known: {sorted(INTENT)}")
    for req in REQUIRES.get(name, ()):
        if plan is None or plan.get(req) is None:
            raise ValueError(
                f"exit_policy {name!r} requires {req!r} in the plan. "
                f"{name} exists to be out before a known catalyst; without the "
                "time it has no meaning and must not fall back to a default.")
    tm, be, hold = INTENT[name]
    return {"trail_mult": tm, "be_arm_frac": be, "hold_past_tp2": hold,
            "exit_policy": name}


# ----------------------------------------------------------------- the engine

def decide_exit(state: TradeState) -> List[Action]:
    """The whole exit policy. Returns ordered actions (may be several).

    PRECEDENCE — never violate this order:
      1. urgent == 'exit'   -> EXIT_ALL      (ALPHAZERO news shock)
      2. hard stop hit      -> EXIT_ALL
      3. time flatten       -> EXIT_ALL      (ruling 1: lives HERE, not the runner)
      4. lifecycle advance  -> TAKE_PARTIAL  (TP1 / TP2 ladder)
      5. layered stop       -> MOVE_SL       (most protective active layer)
    """
    acts: List[Action] = []
    state.hwm = _fav(state.direction, state.hwm, state.price)

    # 1. urgent channel outranks everything
    if state.urgent == "exit":
        advance(state, Stage.CLOSING)
        return _gate(state, [Action("EXIT_ALL", reason="ALPHAZERO urgent: exit")])

    # 2. hard stop, tested against the stop as it stood BEFORE this bar advanced
    #    anything — you cannot be stopped at a level that only just became active
    if (state.price <= state.sl if state.direction > 0 else state.price >= state.sl):
        advance(state, Stage.CLOSING)
        return _gate(state, [Action("EXIT_ALL", reason="stop hit")])

    # 3. time-flatten — live once a clock is supplied. No clock == rule not armed,
    #    never a guessed time.
    if state.flatten_at_et is not None and state.now_et is not None:
        if _et_minutes(state.now_et, "now_et") >= _et_minutes(state.flatten_at_et,
                                                              "flatten_at_et"):
            advance(state, Stage.CLOSING)
            return _gate(state, [Action("EXIT_ALL",
                                        reason=f"time flatten {state.flatten_at_et} ET")])

    # 4. lifecycle advancement
    if state.stage is Stage.SCALED:
        advance(state, Stage.RUNNER)          # the partial is behind us; trail only
    if state.stage is Stage.ENTERED and _crossed(state, _arm_level(state)):
        advance(state, Stage.PROTECTED)
    if state.stage is Stage.PROTECTED and _crossed(state, state.tp2):
        advance(state, Stage.SCALED)
        if not state.hold_past_tp2:
            advance(state, Stage.CLOSING)
            return _gate(state, [Action("EXIT_ALL",
                                        reason="TP2: day goal hit, full exit "
                                               "(hold_past_tp2=False)")])
        if state.goal_fraction > 0:
            acts.append(Action("TAKE_PARTIAL", fraction=state.goal_fraction,
                               reason="TP2: day goal banked"))
        # goal_fraction == 0 means "bank nothing at TP2, just move the stop".
        # Emitting a zero-fraction TAKE_PARTIAL would be an instruction to do
        # nothing dressed as an action — constitution C006 refuses it, and
        # broker.intents_from_actions already refused it too, so this path would
        # have raised live on any plan with goal_fraction 0. Found by C006
        # firing across the ceiling's wide grid, which sweeps partial_frac 0.0.

    # 5. the layered stop. One MOVE_SL, naming the layer that took over.
    eff = effective_stop(state)
    tighter = (eff.level > state.sl if state.direction > 0 else eff.level < state.sl)
    if tighter:
        acts.append(Action("MOVE_SL", sl=eff.level,
                           reason=f"{eff.name} now most protective "
                                  f"({state.sl:g} -> {eff.level:g}): {eff.reason}"))

    return _gate(state, acts or [Action("HOLD", reason="set it and forget it")])


def _gate(state: TradeState, acts: List[Action]) -> List[Action]:
    """Refuse any action illegal in the current stage. Raises, never drops —
    a silently swallowed action is a decision made by omission."""
    allowed = ALLOWED_ACTIONS[state.stage]
    for a in acts:
        if a.kind not in allowed:
            raise StageError(
                f"{a.kind} is not permitted in stage {state.stage.value} "
                f"(allowed: {sorted(allowed) or 'nothing'}) — reason was {a.reason!r}")
    return acts


def apply_action(state: TradeState, action: Action, *,
                 applied_keys: frozenset[str] = frozenset(),
                 batch: Optional[List[Action]] = None) -> None:
    """Fold one Action back into the state. ONE implementation, same reason
    decide_exit is one implementation: the replay, the live runner and any
    backtest harness must evolve state identically or their logs can't be
    diffed (APEX DoD).

    This is also where the CONSTITUTION runs (spec 011). Validation is a
    predicate, not a decision — it can only refuse an action decide_exit already
    chose, so it is not a second decision engine. Enforcing here means no path
    can bypass it without also bypassing the thing that makes logs diffable.

    `applied_keys` is caller-owned because only the caller knows whether a crash
    happened between "broker accepted" and "state persisted". Pass the set of
    idempotency keys already applied and C005 will refuse a replayed reduction.

    `batch` is the full ordered decide_exit output this action came from; with
    it, C007 (emergency precedence) can see the ordering. `state.now_et` is
    forwarded so C004 (no stale facts) can see the clock — None (replay) leaves
    the rule unarmed, exactly like time-flatten.
    """
    from stockfish_constitution import enforce, MUTATING
    enforce(state, action, applied_keys=applied_keys, actions=batch,
            now_et=state.now_et)

    if action.kind == "MOVE_SL":
        state.sl = action.sl                      # C003 verified it is not looser
    elif action.kind == "TAKE_PARTIAL":
        state.qty = state.qty * (1.0 - action.fraction)   # what's still held
    elif action.kind == "EXIT_ALL":
        state.stage = Stage.CLOSED

    if action.kind in MUTATING:
        # C009 — advances on MUTATING actions only. HOLD changes nothing, and
        # advancing on a no-op would make this a call counter rather than a
        # state identity, breaking the C005 duplicate key.
        state.state_revision += 1
    if state.now_et:
        state.last_now_et = state.now_et


def apply_actions(state: TradeState, actions: List[Action],
                  applied_keys: Optional[set] = None) -> None:
    """Apply one decide_exit batch in order, threading the constitution's full
    context — the one batch fold for callers that apply whole batches (runner,
    backtest). ceiling.py is the documented exception: its per-action P&L
    accounting interleaves with the fold, so it threads keys/batch in place —
    same context, hand-rolled loop (rule 1 pressure, revisit if a third
    interleaved caller ever appears).

      C007 — every apply sees the whole ordered batch.
      C005 — each TAKE_PARTIAL's idempotency key is recorded in the CALLER-OWNED
             `applied_keys` set (computed before the apply advances the
             revision). Pass the same set across a session and a replayed
             reduction — the crash-between-broker-and-persist retry — is
             refused. Pass None to opt out (a caller with no retry surface).
      C004 — apply_action itself forwards state.now_et.
    """
    from stockfish_constitution import idempotency_key
    for a in actions:
        key = idempotency_key(state, a) if a.kind == "TAKE_PARTIAL" else None
        apply_action(state, a,
                     applied_keys=frozenset(applied_keys or ()), batch=actions)
        if key is not None and applied_keys is not None:
            applied_keys.add(key)


# --- [SKETCH] deliberately unfinished, do not build without a planning pass ---
#
# time_decay AS A TIGHTENING SCHEDULE. Today time pressure is a cliff: hold
# normally, then flatten at flatten_at_et. A schedule that walks the stop in as
# the clock runs down is probably better and is definitely more dangerous — it
# converts a rule you can reason about into a curve you have to tune, and the
# ceiling has 24 entries. Prerequisites: the 3-of-24 entry problem addressed, and
# a counterfactual showing schedule beats cliff on days that reached TP1.
#
# volatility LAYER. Needs ATR on TradeState and a defensible k. Would sit at
# hwm - k*ATR so normal noise cannot take the trade out. Cheap to add once any
# caller actually supplies ATR — bars.py has the data, nothing threads it through.
#
# SALVAGE AUTO-EMISSION. See NOT_AUTO_EMITTABLE. Blocked on regime.py.


if __name__ == "__main__":
    import sys
    if "--layers" in sys.argv:
        s = TradeState(direction=+1, entry=204.0, qty=163, price=205.9, sl=204.0,
                       tp1=204.9, tp2=205.8, trail_dist=0.8, stage=Stage.SCALED)
        print(f"stage {s.stage.value}   price {s.price}   effective stop "
              f"{effective_stop(s).level:.2f} via {effective_stop(s).name}\n")
        for c in stop_candidates(s):
            mark = "ACTIVE  " if c.active else "inactive"
            lvl = f"{c.level:8.2f}" if c.level is not None else "       -"
            print(f"  {mark} {c.name:14s} {lvl}   {c.reason}")
        sys.exit(0)

    # deterministic replay — the log any harness must reproduce
    s = TradeState(direction=+1, entry=204.0, qty=163, price=204.0,
                   sl=202.9, tp1=204.9, tp2=205.8, trail_dist=0.8)
    path = [204.2, 204.9, 205.1, 205.8, 206.4, 207.1, 206.9, 206.2]
    for i, px in enumerate(path):
        s.price = px
        for a in decide_exit(s):
            apply_action(s, a)
            print(f"t{i} px={px:7.2f} [{s.stage.value:9s}] -> {a.kind:12s} "
                  f"sl={a.sl} frac={a.fraction} | {a.reason}")
    s2 = TradeState(direction=+1, entry=204.0, qty=163, price=206.5, sl=205.0,
                    tp1=204.9, tp2=205.8, trail_dist=0.8, stage=Stage.RUNNER,
                    urgent="exit")
    for a in decide_exit(s2):
        print(f"urgent px=206.50 [{s2.stage.value:9s}] -> {a.kind:12s} | {a.reason}")
