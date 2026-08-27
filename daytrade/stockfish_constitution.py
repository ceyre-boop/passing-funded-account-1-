#!/usr/bin/env python3
"""STOCKFISH CONSTITUTION — spec 011. The layer that can only ever REFUSE.

Nine invariants a position may never violate, enforced in ONE place. This adds
no behavior: every action it sees was already chosen by `decide_exit`.

WHY THIS IS NOT A SECOND DECISION ENGINE (the question card 011 posed):
validation is a PREDICATE, not a decision. `validate_action` returns violations
or nothing. It never selects an action, never proposes an alternative, never
mutates. A component that can only say "no" cannot be a second engine, because
it has nothing to say "yes" to.

WHY IT LIVES IN `apply_action`: that is already the single funnel through which
state changes — the replay, the runner, the backtest harness and the ceiling all
route through it, which is exactly why the DoD diff works at all. One place to
enforce, one place to test, and no path can bypass the constitution without also
bypassing the thing that makes logs diffable. A pre-`decide_exit` check would
duplicate enforcement and create the second engine the card forbids.

VIOLATIONS RAISE. They are never downgraded to a warning and never dropped. A
constitution that can be ignored is a style guide.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional

# Rule ids are STABLE FOREVER. They appear in logs, tests, and session records;
# renumbering one would silently invalidate historical audit trails.
RULES: dict[str, str] = {
    "C001": "no quantity increase",
    "C002": "no over-close",
    "C003": "no stop loosening",
    "C004": "no stale facts",
    "C005": "no duplicate reduction",
    "C006": "bounded fractions",
    "C007": "emergency precedence",
    "C008": "closed is terminal",
    "C009": "reproducibility",
}

MUTATING = frozenset({"MOVE_SL", "TAKE_PARTIAL", "EXIT_ALL"})


@dataclass(frozen=True)
class ConstitutionViolation:
    rule_id: str
    rule: str
    detail: str
    state_revision: int
    action_kind: Optional[str] = None

    def __post_init__(self):
        if self.rule_id not in RULES:
            raise ValueError(f"unknown rule id {self.rule_id!r}; known: {sorted(RULES)}")

    def to_dict(self) -> dict:
        return asdict(self)


class ConstitutionError(RuntimeError):
    """One or more invariants were violated. Carries all of them, not just the
    first — a caller fixing one at a time would need N runs to see N problems."""

    def __init__(self, violations: list[ConstitutionViolation]):
        self.violations = violations
        super().__init__("; ".join(f"[{v.rule_id}] {v.rule}: {v.detail}" for v in violations))

    def to_dict(self) -> dict:
        first = self.violations[0] if self.violations else None
        return {"error": "ConstitutionViolation",
                "state_revision": first.state_revision if first else None,
                "action_kind": first.action_kind if first else None,
                "violations": [v.to_dict() for v in self.violations]}


def _rev(state) -> int:
    return getattr(state, "state_revision", 0)


def idempotency_key(state, action) -> str:
    """The key that makes 'duplicate reduction' precise after a partial fill.

    Keyed on the revision the reduction was computed AGAINST. Re-sending the
    same reduction computed against the same state is a duplicate — the classic
    retry-after-timeout. A genuinely new reduction is necessarily computed
    against a later revision, because the first one advanced it.
    """
    return f"{action.kind}:{action.fraction!r}:{_rev(state)}"


# ------------------------------------------------------------------ state

def validate_state(state) -> list[ConstitutionViolation]:
    """Invariants that must hold of the state itself, independent of any action."""
    v: list[ConstitutionViolation] = []
    r = _rev(state)

    if r < 0:
        v.append(ConstitutionViolation("C009", RULES["C009"],
                 f"state_revision {r} is negative", r))
    if state.qty < 0:
        v.append(ConstitutionViolation("C002", RULES["C002"],
                 f"qty {state.qty} is negative — more was closed than was held", r))
    for f_ in ("entry", "price", "sl", "tp1", "tp2", "trail_dist"):
        x = getattr(state, f_, None)
        if isinstance(x, float) and math.isnan(x):
            v.append(ConstitutionViolation("C009", RULES["C009"],
                     f"{f_} is NaN — state is not reproducible", r))
    return v


# ----------------------------------------------------------------- action

def validate_action(state, action, *, applied_keys: frozenset[str] = frozenset()
                    ) -> list[ConstitutionViolation]:
    """Invariants for applying ONE action to THIS state. Pure; mutates nothing."""
    from stockfish_exit import Stage

    v: list[ConstitutionViolation] = []
    r = _rev(state)
    k = action.kind

    # C008 — closed is terminal
    if getattr(state, "stage", None) is Stage.CLOSED:
        v.append(ConstitutionViolation("C008", RULES["C008"],
                 f"{k} attempted on a CLOSED position", r, action_kind=k))

    if k == "MOVE_SL":
        if action.sl is None:
            v.append(ConstitutionViolation("C009", RULES["C009"],
                     "MOVE_SL carries no stop price", r, action_kind=k))
        elif math.isnan(action.sl):
            v.append(ConstitutionViolation("C009", RULES["C009"],
                     "MOVE_SL stop price is NaN", r, action_kind=k))
        else:
            # C003 — direction-specific. Long: loosening is lower. Short: higher.
            looser = (action.sl < state.sl if state.direction > 0
                      else action.sl > state.sl)
            if looser:
                side = "long" if state.direction > 0 else "short"
                v.append(ConstitutionViolation("C003", RULES["C003"],
                         f"{side}: refusing sl {state.sl:g} -> {action.sl:g}", r, action_kind=k))

    elif k == "TAKE_PARTIAL":
        f = action.fraction
        # C006 — bounded fractions
        if f is None or (isinstance(f, float) and math.isnan(f)) or not (0.0 < f <= 1.0):
            v.append(ConstitutionViolation("C006", RULES["C006"],
                     f"fraction {f!r} outside (0, 1]", r, action_kind=k))
        else:
            # C001 / C002 — qty may only decrease, and never past zero
            new_qty = state.qty * (1.0 - f)
            if new_qty > state.qty + 1e-12:
                v.append(ConstitutionViolation("C001", RULES["C001"],
                         f"qty would rise {state.qty:g} -> {new_qty:g}", r, action_kind=k))
            if new_qty < -1e-12:
                v.append(ConstitutionViolation("C002", RULES["C002"],
                         f"qty would go negative: {state.qty:g} -> {new_qty:g}", r, action_kind=k))
            # C005 — duplicate reduction
            key = idempotency_key(state, action)
            if key in applied_keys:
                v.append(ConstitutionViolation("C005", RULES["C005"],
                         f"reduction {key!r} was already applied — refusing a replay", r, action_kind=k))

    elif k not in ("HOLD", "EXIT_ALL"):
        v.append(ConstitutionViolation("C009", RULES["C009"],
                 f"unknown action kind {k!r}", r, action_kind=k))

    return v


def validate_batch(state, actions) -> list[ConstitutionViolation]:
    """C007 — emergency precedence. Nothing may follow an EXIT_ALL in a batch.

    Ordering, not content: an EXIT_ALL means the position is gone, so a later
    action in the same batch would be acting on something that no longer exists.
    """
    v: list[ConstitutionViolation] = []
    r = _rev(state)
    for i, a in enumerate(actions):
        if a.kind == "EXIT_ALL" and i != len(actions) - 1:
            after = [x.kind for x in actions[i + 1:]]
            v.append(ConstitutionViolation("C007", RULES["C007"],
                     f"EXIT_ALL at index {i} of {len(actions)} is followed by {after}", r,
                     action_kind=a.kind))
    return v


def validate_clock(state, now_et: Optional[str]) -> list[ConstitutionViolation]:
    """C004 — no stale facts. The supplied clock may not run backwards."""
    prev = getattr(state, "last_now_et", None)
    if not (now_et and prev):
        return []
    from stockfish_exit import _et_minutes
    if _et_minutes(now_et, "now_et") < _et_minutes(prev, "last_now_et"):
        # Deliberately same-day: HH:MM has no date, so 23:59 -> 00:00 fires this
        # rule too. That is BY DESIGN, not a wraparound bug (020 architect
        # ruling): a midnight crossing means an overnight position, and this
        # day-trade engine refuses those rather than growing a date-aware clock.
        return [ConstitutionViolation("C004", RULES["C004"],
                f"clock went backwards: {prev} -> {now_et} — the session clock is "
                "same-day by design; a midnight crossing means an overnight "
                "position, which this day-trade engine refuses", _rev(state))]
    return []


def enforce(state, action=None, *, applied_keys: frozenset[str] = frozenset(),
            actions=None, now_et: Optional[str] = None) -> None:
    """Raise if anything is violated. The only entry point callers need."""
    v = validate_state(state)
    if actions is not None:
        v += validate_batch(state, actions)
    if action is not None:
        v += validate_action(state, action, applied_keys=applied_keys)
    if now_et is not None:
        v += validate_clock(state, now_et)
    if v:
        raise ConstitutionError(v)


# ------------------------------------------------------------------ tests

def _self_test() -> int:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import random
    from stockfish_exit import (TradeState, Action, decide_exit, apply_action,
                                Stage, INTENT, policy_params)

    fails = 0

    def check(label, fn, expect_rule=None):
        nonlocal fails
        try:
            fn()
            if expect_rule:
                print(f"  ❌ {label}: expected {expect_rule}, nothing raised"); fails += 1
            else:
                print(f"  ✅ {label}")
        except ConstitutionError as e:
            got = {v.rule_id for v in e.violations}
            if expect_rule and expect_rule in got:
                print(f"  ✅ {label:38s} -> {expect_rule}")
            elif expect_rule:
                print(f"  ❌ {label}: expected {expect_rule}, got {sorted(got)}"); fails += 1
            else:
                print(f"  ❌ {label}: unexpected {sorted(got)}"); fails += 1

    mk = lambda **kw: TradeState(**{**dict(direction=1, entry=200.0, qty=100, price=201.0,
                                           sl=199.0, tp1=201.0, tp2=202.14, trail_dist=0.5), **kw})

    print("one fixture per rule id:")
    check("C001/C002 negative qty state", lambda: enforce(mk(qty=-1)), "C002")
    check("C003 long stop loosening",
          lambda: enforce(mk(), Action("MOVE_SL", sl=198.0)), "C003")
    check("C003 short stop loosening",
          lambda: enforce(mk(direction=-1, sl=201.0, tp1=199.0, tp2=197.86),
                          Action("MOVE_SL", sl=202.0)), "C003")
    check("C004 clock runs backwards",
          lambda: enforce(mk(now_et="11:00", last_now_et="11:00"), now_et="10:00"), "C004")
    check("C005 duplicate reduction",
          lambda: enforce(mk(), Action("TAKE_PARTIAL", fraction=0.5),
                          applied_keys=frozenset({"TAKE_PARTIAL:0.5:0"})), "C005")
    check("C006 fraction out of bounds",
          lambda: enforce(mk(), Action("TAKE_PARTIAL", fraction=1.5)), "C006")
    check("C006 fraction zero",
          lambda: enforce(mk(), Action("TAKE_PARTIAL", fraction=0.0)), "C006")
    check("C007 action after EXIT_ALL",
          lambda: enforce(mk(), actions=[Action("EXIT_ALL"), Action("MOVE_SL", sl=200.0)]), "C007")
    check("C008 action on CLOSED",
          lambda: enforce(mk(stage=Stage.CLOSED), Action("MOVE_SL", sl=200.0)), "C008")
    def _nan_state():
        # TradeState.__post_init__ already refuses NaN at construction — good.
        # validate_state guards the OTHER path: a state that became NaN later.
        st = mk(); st.sl = float("nan"); enforce(st)
    check("C009 NaN in state (post-construction)", _nan_state, "C009")
    check("C009 unknown action kind", lambda: enforce(mk(), Action("SELL_EVERYTHING")), "C009")

    print("\nlegal play must NOT fire (property test):")
    random.seed(17)
    fired = runs = 0
    for name in INTENT:
        for d in (1, -1):
            for _ in range(90):
                runs += 1
                s = TradeState(direction=d, entry=200.0, qty=100, price=200.0,
                               sl=200.0 - d, tp1=200.0 + d, tp2=200.0 + d * 2.14,
                               trail_dist=0.5, **policy_params(name, {"flatten_at_et": "15:45"}))
                px, keys = 200.0, set()
                for _ in range(50):
                    px += random.gauss(0, 0.35)
                    s.price = px
                    s.urgent = random.choice([None, None, "tighten"])
                    acts = decide_exit(s)
                    try:
                        enforce(s, actions=acts)
                        for a in acts:
                            enforce(s, a, applied_keys=frozenset(keys))
                            if a.kind == "TAKE_PARTIAL":
                                keys.add(idempotency_key(s, a))
                            apply_action(s, a)
                    except ConstitutionError as e:
                        fired += 1
                        print(f"    ❌ fired on legal play: {e}")
                        break
                    if any(a.kind == "EXIT_ALL" for a in acts):
                        break
    print(f"  {runs} paths, spurious violations: {fired}")
    if fired:
        fails += 1
    else:
        print("  ✅ constitution silent on every legal path")

    print("\nC007 permutation test:")
    a_exit, a_move, a_part = (Action("EXIT_ALL"), Action("MOVE_SL", sl=201.0),
                              Action("TAKE_PARTIAL", fraction=0.5))
    import itertools
    bad = good = 0
    for perm in itertools.permutations([a_exit, a_move, a_part]):
        illegal = perm.index(a_exit) != len(perm) - 1
        try:
            enforce(mk(), actions=list(perm)); raised = False
        except ConstitutionError:
            raised = True
        if raised == illegal:
            good += 1
        else:
            bad += 1
    print(f"  {good}/{good + bad} orderings classified correctly")
    if bad:
        fails += 1

    print(f"\n{'✅ ALL PASS' if not fails else f'❌ {fails} FAILURE(S)'}")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    print(__doc__)
    print("rules:")
    for k, v in RULES.items():
        print(f"  {k}  {v}")
    print("\nrun with --self-test")
