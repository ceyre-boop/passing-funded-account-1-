"""az/odd.py — the ODD chassis: tiers, preconditions, and minimal risk maneuvers.

WHAT THIS IS, AND DELIBERATELY IS NOT
    ODD.md v0.1 is SEALED AT T0 and dormant. This module implements the half of
    it that carries no measured numbers: the SHAPE of the preconditions gate, the
    degradation ladder and its ratchet, and the MRM lookup.

    It implements NONE of the §1 envelope values. ODD.md §1 says plainly that
    those bounds are "proposed defaults, not measured ones ... each needs its
    value replaced by a measured distribution before it gates anything." Baking
    an unmeasured threshold into code is how a proposal quietly becomes a
    constant, so every threshold here is UNSET.

    UNSET is not zero and not a placeholder to be filled in casually. A check
    whose threshold is UNSET returns UNKNOWN, and UNKNOWN fails closed. The
    chassis is therefore fully wired and completely unable to authorize risk,
    which is exactly the state ODD.md describes.

FAIL-CLOSED IS THE WHOLE DESIGN
    Three-valued logic, not boolean. TRUE / FALSE / UNKNOWN. Only an explicit
    TRUE opens anything; UNKNOWN is treated as FALSE for authorization and is
    reported separately so "we could not evaluate this" is never silently
    indistinguishable from "we evaluated it and it passed".

RELATIONSHIP TO THE ENGINES
    Direction of authority is one-way (ODD.md §1b rule 4): the exit core can
    refuse an entry; the entry layer can never override an exit. When the two
    disagree about domain membership, the exit core wins -- it is the frozen,
    verified component, and the entry layer is the one that has never passed a
    gate.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace


class OddError(RuntimeError):
    """Refused. Never partial, never guessed."""


# --------------------------------------------------------------- three-valued

class Truth(enum.Enum):
    """UNKNOWN is a first-class answer, not a missing TRUE."""
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"

    @property
    def authorizes(self) -> bool:
        """Only TRUE authorizes. UNKNOWN fails closed."""
        return self is Truth.TRUE


class Unset:
    """Sentinel for a threshold ODD.md has proposed but nobody has measured.

    Distinct from None and from 0. Any comparison against it raises rather than
    silently defaulting -- ODD.md's envelope values are explicitly not yet data,
    and a threshold that quietly evaluates is the failure this guards."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self):
        raise OddError("UNSET has no truth value — a threshold ODD.md proposed "
                       "but has not measured cannot gate anything")

    def __lt__(self, other): raise OddError("cannot compare against UNSET")
    __le__ = __gt__ = __ge__ = __lt__


UNSET = Unset()


# ------------------------------------------------------------------ the tiers

@enum.unique
class RunEnvironment(enum.Enum):
    """Where the system is actually running. UNKNOWN is the default and fails
    closed: "we could not establish where we are" must never read as "we are
    safely in a simulator"."""
    UNKNOWN = "UNKNOWN"
    BACKTEST = "BACKTEST"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"


@enum.unique
class Tier(enum.IntEnum):
    """ODD.md §4, plus T_SIM. Ordered so that a LOWER value is a MORE degraded
    state, which makes "the more conservative of two" a min() and removes a class
    of bug."""
    T_SIM = -1          # the closed track — see below
    T3_HALT = 0
    T2_DEFENSIVE = 1
    T1_RESTRICTED = 2
    T0_NOMINAL = 3

    @property
    def sealed(self) -> bool:
        """T0 is SEALED IN v0.1 and not currently attainable."""
        return self is Tier.T0_NOMINAL

    @property
    def may_open_risk(self) -> bool:
        """REAL risk. T_SIM is deliberately absent: driving on a closed track is
        not a licence to trade, and conflating the two is the whole hazard."""
        return self in (Tier.T0_NOMINAL, Tier.T1_RESTRICTED)

    @property
    def may_open_simulated_risk(self) -> bool:
        return self is Tier.T_SIM


# T_SIM exists so the full loop -- perceive, decide, hand off, execute, log,
# degrade -- can be exercised end to end without an edge. Every other tier is
# about live risk, which left no way to drive on a closed track and made the
# engineering goal blocked by the safety artifact.
#
# It is a NARROWING (strictly less permissive toward real risk than T3_HALT), so
# ODD.md §6 does not gate it -- §6 gates widening. But it is a door in a wall
# built on purpose, so it is locked from three sides:
#
#   1. it authorizes nothing outside a proven BACKTEST -- and that is a FAULT,
#      not a refusal (see authorize_entry)
#   2. it is unreachable by transition: enterable only at construction
#   3. it runs its own explicit precondition list, never a gate bypass
LIVE_TIERS = (Tier.T3_HALT, Tier.T2_DEFENSIVE, Tier.T1_RESTRICTED, Tier.T0_NOMINAL)

# ODD.md §4: "no path from T3 to T0 at all — it routes through T1."
# Plus: nothing may slide INTO T_SIM. A live system degrading into simulation
# while holding real risk would be a silent loss of the execution path.
FORBIDDEN_TRANSITIONS = frozenset(
    {(Tier.T3_HALT, Tier.T0_NOMINAL)}
    | {(t, Tier.T_SIM) for t in LIVE_TIERS}
)


# ---------------------------------------------------------- preconditions §2

@dataclass(frozen=True)
class Precondition:
    """One §2 checklist line. `threshold` is UNSET wherever ODD.md proposes a
    bound it has not measured; such a check can only return UNKNOWN."""
    key: str
    text: str
    threshold: object = UNSET
    value: object = None
    _forced: Truth | None = None

    def evaluate(self) -> Truth:
        if self._forced is not None:
            return self._forced
        if isinstance(self.threshold, Unset):
            return Truth.UNKNOWN
        if self.value is None:
            return Truth.UNKNOWN
        raise OddError(
            f"{self.key}: a measured threshold is present but this chassis "
            "implements no comparison logic — the evaluator belongs with the "
            "measurement that sets the threshold, not here")


# ODD.md §2, verbatim in order. Every threshold UNSET: none has been measured.
PRECONDITIONS: tuple[Precondition, ...] = (
    Precondition("envelope", "every §1 dimension evaluated, none out-of-domain"),
    Precondition("heartbeat", "data pipeline heartbeat fresh"),
    Precondition("reconciliation", "no unresolved reconciliation break from prior session"),
    Precondition("slippage", "realized slippage over last K trades within tolerance"),
    Precondition("exit_domain", "exit core in-domain for full expected holding window (§1b)"),
    Precondition("checkpoint_hash", "frozen exit-core checkpoint hash matches the pinned reference"),
    Precondition("policy_version", "entry policy version matches the version that cleared pre-registration"),
    Precondition("prereg_current", "pre-registration unexpired and unamended since acceptance"),
    Precondition("holdout_sealed", "sealed holdout remains sealed, or its single authorized unseal is logged"),
    # NON-NEGOTIABLE, added 2026-09-01 by operator ruling. Shadow mode is not a
    # phase anyone may skip when the backtest looks good: the model runs live,
    # logging its decisions against CLAUDE's discretionary exits, for weeks,
    # BEFORE it touches capital. That comparison is also the first real
    # calibration set — the only one drawn from live conditions rather than
    # replay, so skipping it costs the calibration too, not just the safety.
    #
    # THE COMPARATOR IS CLAUDE, NOT THE OPERATOR, and that is a deliberate
    # choice with a cost attached. The operator's stated reason is sound: a
    # beginner's chart read is a weak label, and lookback would beat it. But
    # promoting Claude to the standard does not make the standard good — it
    # makes Claude the ceiling. "Is Claude's discretionary exit better than a
    # rate-matched coin" is exactly as open a question as it was for the
    # operator, and it must be measured the same way, against the same control,
    # before this comparison set is treated as ground truth rather than as a
    # second opinion.
    Precondition("shadow_mode_served",
                 "the model has run live in shadow for the declared period, its "
                 "decisions logged against Claude's discretionary exits, and the "
                 "comparison retained as the calibration set"),
    # ODD.md §2 final line: absent in v0.1, so this gate cannot pass.
    Precondition("t0_unseal", "T0 unseal authorization present",
                 _forced=Truth.FALSE),
)


# The T_SIM checklist. This is a SHORTER LIST, NOT A BYPASS, and the distinction
# is the whole safety argument: a bypass is unauditable and grows silently, while
# four named lines can be read and argued with.
#
# Dropped as live-only and meaningless on a closed track: envelope, heartbeat,
# reconciliation, slippage, exit_domain, policy_version, prereg_current,
# t0_unseal. Each is a claim about live venue conditions or about an edge having
# cleared pre-registration; neither exists in a backtest, and pretending to
# evaluate them would be the "silently invent unavailable context" failure.
SIM_PRECONDITIONS: tuple[Precondition, ...] = (
    Precondition("no_live_venue",
                 "environment structurally proven BACKTEST (kernel clock, not config)"),
    Precondition("holdout_sealed",
                 "sealed holdout remains sealed — a backtest is not a licence to unseal"),
    Precondition("checkpoint_hash",
                 "frozen exit-core checkpoint hash matches — the point is exercising "
                 "the REAL core, not a stand-in"),
    Precondition("policy_declares_no_edge",
                 "the entry policy declares itself a calibration arm claiming no edge"),
)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    true: tuple[str, ...]
    false: tuple[str, ...]
    unknown: tuple[str, ...]

    def why_not(self) -> str:
        if self.passed:
            return "gate open"
        parts = []
        if self.false:
            parts.append(f"FALSE: {', '.join(self.false)}")
        if self.unknown:
            parts.append(f"UNKNOWN (fails closed): {', '.join(self.unknown)}")
        return " | ".join(parts)


def preconditions_for(tier: Tier) -> tuple[Precondition, ...]:
    """Which checklist applies. T_SIM gets its own SHORTER LIST -- selecting a
    different list is auditable; skipping the gate would not be."""
    return SIM_PRECONDITIONS if tier is Tier.T_SIM else PRECONDITIONS


def evaluate_gate(preconditions=PRECONDITIONS, *, tier: Tier | None = None) -> GateResult:
    """ODD.md §2: ALL must be TRUE to open. ANY false → no new risk.

    Passing `tier` selects the right checklist; passing `preconditions` directly
    stays supported so a caller can hand-build one for a test."""
    if tier is not None:
        preconditions = preconditions_for(tier)
    t, f, u = [], [], []
    for p in preconditions:
        {Truth.TRUE: t, Truth.FALSE: f, Truth.UNKNOWN: u}[p.evaluate()].append(p.key)
    return GateResult(passed=not f and not u, true=tuple(t),
                      false=tuple(f), unknown=tuple(u))


# ----------------------------------------------------------------- MRM §3

@enum.unique
class Maneuver(enum.IntEnum):
    """Ordered by conservatism. ODD.md §3: "Default when a trigger is ambiguous:
    the more conservative MRM" — so ambiguity resolves with max()."""
    HALT_NEW_ENTRIES = 0
    HAND_TO_EXIT_NO_REENTRY = 1
    FLAT_AT_NEXT_LIQUID_WINDOW = 2
    FLAT_AT_REOPEN_NO_REENTRY = 3
    FLAT_IMMEDIATELY = 4


@dataclass(frozen=True)
class Mrm:
    trigger: str
    maneuver: Maneuver
    override: str            # who may override; "nobody" is machine-meaningful
    tier_floor: Tier         # tier the system drops to, at most

    @property
    def overridable(self) -> bool:
        return self.override != "nobody"


# ODD.md §3, in table order. No thresholds appear: each trigger names a
# condition, and the measurement that decides it lives with its own data.
MRM_TABLE: dict[str, Mrm] = {m.trigger: m for m in (
    Mrm("data_staleness", Maneuver.HALT_NEW_ENTRIES, "nobody", Tier.T2_DEFENSIVE),
    Mrm("vendor_price_disagreement", Maneuver.HALT_NEW_ENTRIES, "colin_logged_same_session", Tier.T2_DEFENSIVE),
    Mrm("vol_regime_exits_band", Maneuver.HAND_TO_EXIT_NO_REENTRY, "nobody", Tier.T2_DEFENSIVE),
    Mrm("correlation_break", Maneuver.HALT_NEW_ENTRIES, "colin_logged", Tier.T2_DEFENSIVE),
    Mrm("unscheduled_halt", Maneuver.FLAT_AT_REOPEN_NO_REENTRY, "nobody", Tier.T2_DEFENSIVE),
    Mrm("drawdown_breach", Maneuver.FLAT_IMMEDIATELY, "nobody", Tier.T3_HALT),
    Mrm("execution_quality_breach", Maneuver.FLAT_IMMEDIATELY, "colin_after_root_cause", Tier.T3_HALT),
    Mrm("exit_core_left_domain", Maneuver.FLAT_AT_NEXT_LIQUID_WINDOW, "nobody", Tier.T2_DEFENSIVE),
    Mrm("orphaned_position", Maneuver.FLAT_IMMEDIATELY, "nobody", Tier.T3_HALT),
    Mrm("version_mismatch", Maneuver.FLAT_IMMEDIATELY, "nobody", Tier.T3_HALT),
)}


def resolve_mrm(*triggers: str) -> Mrm:
    """ODD.md §3: ambiguity resolves to the MORE CONSERVATIVE maneuver, and the
    LOWER tier floor. The two are resolved independently — a trigger can demand
    the harsher maneuver while another demands the deeper tier."""
    if not triggers:
        raise OddError("resolve_mrm requires at least one trigger")
    unknown = [t for t in triggers if t not in MRM_TABLE]
    if unknown:
        raise OddError(f"unknown trigger(s) {unknown!r} — an unrecognised trigger "
                       "must not silently resolve to a mild maneuver")
    hits = [MRM_TABLE[t] for t in triggers]
    worst = max(hits, key=lambda m: m.maneuver)
    return replace(worst,
                   trigger="+".join(sorted(triggers)),
                   tier_floor=min(m.tier_floor for m in hits),
                   # an override is only available if EVERY contributing trigger
                   # allows one; "nobody" anywhere wins.
                   override=("nobody" if any(not m.overridable for m in hits)
                             else worst.override))


# --------------------------------------------------------- the ratchet §4

@dataclass(frozen=True)
class Recovery:
    """ODD.md §4 re-entry requirements. All must hold to step UP one tier."""
    condition_back_in_domain_full_session: bool = False
    root_cause_logged: bool = False
    one_session_delay_served: bool = False
    manual_rearm: bool = False           # additionally required leaving T3

    def satisfied_for(self, frm: Tier) -> bool:
        base = (self.condition_back_in_domain_full_session
                and self.root_cause_logged and self.one_session_delay_served)
        return base and (self.manual_rearm if frm is Tier.T3_HALT else True)


def degrade(current: Tier, to: Tier) -> Tier:
    """Instant and automatic. ODD.md §4: "tiers degrade automatically and
    instantly." Degrading is never gated -- except into T_SIM, which is not a
    degradation at all but a different mode, and must never be slid into while
    real risk is open."""
    if to > current:
        raise OddError(f"degrade() cannot raise a tier ({current.name} -> {to.name})")
    if (current, to) in FORBIDDEN_TRANSITIONS:
        raise OddError(
            f"{current.name} -> {to.name} is forbidden. T_SIM is enterable only at "
            "construction: a live system sliding into simulation would silently "
            "lose its execution path while holding a position.")
    return to


def recover(current: Tier, recovery: Recovery) -> Tier:
    """One tier at a time, slowly and manually. T0 is sealed in v0.1, so this
    never returns T0 — and T3 never reaches T0 even once T0 opens."""
    target = Tier(min(current + 1, Tier.T0_NOMINAL))
    if target is current:
        return current
    if (current, target) in FORBIDDEN_TRANSITIONS:
        raise OddError(f"{current.name} -> {target.name} is forbidden; route through T1")
    if target.sealed:
        # ODD.md §0/§4: T0 SEALED IN v0.1, "not currently attainable".
        return current
    if not recovery.satisfied_for(current):
        return current
    return target


def authorize_entry(tier: Tier, gate: GateResult, *,
                    exit_core_in_domain: Truth,
                    entry_layer_in_domain: Truth,
                    environment: RunEnvironment = RunEnvironment.UNKNOWN,
                    ) -> tuple[bool, str]:
    """ODD.md §1b: no entry without exit authorization, and when the engines
    disagree the exit core wins. Returns (authorized, reason).

    `environment` defaults to UNKNOWN and therefore fails closed. It is a FAULT,
    not a refusal, for T_SIM to appear anywhere but a proven backtest."""
    if tier is Tier.T_SIM:
        if environment is not RunEnvironment.BACKTEST:
            # Deliberately a raise. A refusal would let a mis-wired live system
            # keep running quietly at a simulation tier; this is a corrupted
            # system, not a declined trade.
            raise OddError(
                f"T_SIM in environment {environment.value} — refusing to continue. "
                "The simulation tier exists only for a proven BACKTEST kernel, and "
                "the environment is derived from the injected clock, never from "
                "configuration. Reaching here means the door was opened from the "
                "wrong side.")
        if not gate.passed:
            return False, f"sim preconditions: {gate.why_not()}"
        # Deliberately does NOT consult exit_core_in_domain: on a closed track
        # nothing measures it, and inventing a value is the failure this whole
        # module exists to prevent. Orphans are DETECTED at runtime instead.
        return True, "authorized (T_SIM — simulated risk only, no edge claimed)"

    if environment is RunEnvironment.UNKNOWN:
        return False, ("environment UNKNOWN — cannot authorize live risk without "
                       "establishing where the system is running")
    if not tier.may_open_risk:
        return False, f"tier {tier.name} may not open risk"
    if not gate.passed:
        return False, f"preconditions: {gate.why_not()}"
    if not exit_core_in_domain.authorizes:
        # covers both FALSE and UNKNOWN, and covers disagreement: the exit core
        # refusing is decisive regardless of what the entry layer thinks.
        return False, ("exit core not in-domain for the expected holding window "
                       f"({exit_core_in_domain.value}) — entry would be an orphan")
    if not entry_layer_in_domain.authorizes:
        return False, f"entry layer not in-domain ({entry_layer_in_domain.value})"
    return True, "authorized"
