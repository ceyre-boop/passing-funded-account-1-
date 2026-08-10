#!/usr/bin/env python3
"""SHADOW POLICY TOURNAMENT — spec 014 (shadow half).

Feed the SAME recorded cycle stream the authoritative path saw through every
candidate policy, each on its own state copy, and record what each WOULD have
done. Only the selected policy ever produced an execution intent; shadow output
is `ShadowAction`, a type that physically cannot travel the execution path:

  - it has no `kind` field; ACCESSING `.kind` raises `ShadowContainment`, and
    every authoritative consumer (apply_action, the constitution, broker
    translation) reads `.kind` first
  - its serialized form carries `"shadow": true` and `hypothetical_kind`, so a
    log line can never be mistaken for an authoritative action

Counterfactual fills happen at the recorded cycle price — the identical
convention the live runner experienced. Each policy folds the stream strictly
forward; the prefix property is tested, which is what "no future data" means.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional

from stockfish_exit import (TradeState, apply_actions, decide_exit,
                            policy_params)


class ShadowContainment(RuntimeError):
    """A shadow action reached an execution surface. That path does not exist."""


@dataclass(frozen=True)
class ShadowAction:
    hypothetical_kind: str
    policy_name: str
    sl: Optional[float] = None
    fraction: Optional[float] = None
    reason: str = ""

    @property
    def kind(self):
        raise ShadowContainment(
            f"shadow action ({self.policy_name}:{self.hypothetical_kind}) asked for "
            ".kind — only authoritative actions have execution identity. A shadow "
            "policy holds no order pathway, by type, not by convention.")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["shadow"] = True
        return d


@dataclass
class ShadowResult:
    policy_name: str
    policy_params: dict                     # resolved mechanics = version identity
    actions: List[List[ShadowAction]]       # per cycle
    realized_r: float                       # counterfactual, cycle-price fills
    closed: bool
    final_sl: float
    final_stage: str


def _state_for(plan: dict, policy: str) -> TradeState:
    base = {k: plan[k] for k in ("direction", "entry", "qty", "sl", "tp1",
                                 "tp2", "trail_dist")}
    params = policy_params(policy, plan)
    return TradeState(price=plan["entry"],
                      goal_fraction=float(plan.get("goal_fraction", 0.5)),
                      flatten_at_et=plan.get("flatten_at_et"),
                      **base, **params)


def run_shadow(plan: dict, cycles: List[tuple], policies: List[str]
               ) -> dict[str, ShadowResult]:
    """Every policy over the identical stream. Deterministic; strictly forward."""
    risk = abs(float(plan["entry"]) - float(plan["sl"]))
    if risk <= 0:
        raise ValueError("plan risk (|entry - sl|) must be positive")

    out: dict[str, ShadowResult] = {}
    for name in policies:
        st = _state_for(plan, name)
        params = policy_params(name, plan)
        keys: set = set()
        realized, held = 0.0, 1.0
        per_cycle: List[List[ShadowAction]] = []
        closed = False
        for price, now_et in cycles:
            if closed:
                per_cycle.append([])
                continue
            st.price = float(price)
            st.now_et = now_et
            acts = decide_exit(st)
            row = []
            for a in acts:
                # counterfactual accounting BEFORE the fold, at the cycle price
                if a.kind == "TAKE_PARTIAL":
                    realized += held * a.fraction * (st.price - st.entry) * st.direction
                    held -= held * a.fraction
                elif a.kind == "EXIT_ALL":
                    realized += held * (st.price - st.entry) * st.direction
                    held = 0.0
                    closed = True
                row.append(ShadowAction(hypothetical_kind=a.kind, policy_name=name,
                                        sl=a.sl, fraction=a.fraction,
                                        reason=a.reason))
            apply_actions(st, acts, keys)
            per_cycle.append(row)
        if not closed and cycles:
            realized += held * (float(cycles[-1][0]) - st.entry) * st.direction
        out[name] = ShadowResult(policy_name=name, policy_params=params,
                                 actions=per_cycle, realized_r=realized / risk,
                                 closed=closed, final_sl=st.sl,
                                 final_stage=st.stage.value)
    return out


if __name__ == "__main__":
    print(__doc__)
    print("run the suite: pytest daytrade/test_shadow_regret.py -v")
