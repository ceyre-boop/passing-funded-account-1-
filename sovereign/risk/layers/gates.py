"""Layer 0 — HARD GATES. Any gate firing forces final_risk = 0 (halt).

run_gates returns a halt-reason string (the engine treats non-None as halt) or None.
FAIL LOUD: every halt names a specific reason; nothing silently passes.

unarmed_gates() is a SEPARATE, non-raising report: which configured gates could
never fire on THIS call because the state field they read is None (no supplier
in production ever set it). A gate whose input is always absent is functionally
inert — it looks like a safety limit in risk_config.yaml but can never halt
anything. Unlike the Kelly ceiling case in risk_engine.py (a layer *module*
either genuinely absent or present-but-broken, which decide() raises on),
mc_breach_prob is a genuinely absent *input* to an otherwise-working gate — a
different failure mode, so it gets a different, non-raising treatment: it is
reported on every decision instead of raising, so the inertness is visible in
every audit log line rather than only on the rare call that could have halted.
"""
from __future__ import annotations

from typing import Optional


def run_gates(signal, state, cfg) -> Optional[str]:
    g = cfg["gates"]

    # Daily loss limit (realized + open) vs starting balance.
    daily = state.daily_realized_pnl + state.daily_open_pnl
    daily_floor = -(g["daily_loss_limit_pct"] * state.starting_balance)
    if daily <= daily_floor:
        return (f"daily_loss_limit: daily P&L {daily:.0f} <= floor {daily_floor:.0f} "
                f"({g['daily_loss_limit_pct']:.1%} of start)")

    # Max drawdown: halt if within buffer of the floor (use the deeper of static/trailing).
    dd = max(state.drawdown_static, state.drawdown_trailing)
    floor, buf = g["max_dd_floor_pct"], g["max_dd_buffer_pct"]
    if dd >= floor - buf:
        return f"max_drawdown_buffer: dd {dd:.2%} within {buf:.2%} of {floor:.1%} floor"

    # Internal guard (PropRiskManager-equivalent stricter limits, from RiskState).
    ig = g.get("internal_guard", {})
    if ig:
        if daily <= -(ig["daily_loss_pct"] * state.starting_balance):
            return f"internal_guard_daily: daily P&L {daily:.0f} <= -{ig['daily_loss_pct']:.1%} of start"
        if state.drawdown_trailing >= ig["trailing_dd_pct"]:
            return f"internal_guard_trailing_dd: {state.drawdown_trailing:.2%} >= {ig['trailing_dd_pct']:.1%}"

    # System health heartbeat.
    if g.get("require_health_ok", True) and not state.health_ok:
        return "health_not_ok: system health heartbeat is down"

    # Macro threat (AlexandrianLibrary threat_score).
    if state.threat_score >= g["threat_critical"]:
        return f"threat_critical: threat_score {state.threat_score:.2f} >= {g['threat_critical']}"

    # Monte Carlo breach probability.
    if state.mc_breach_prob is not None and state.mc_breach_prob >= g["mc_breach_halt"]:
        return f"mc_breach_prob: {state.mc_breach_prob:.2%} >= halt {g['mc_breach_halt']:.0%}"

    return None


def unarmed_gates(state, cfg) -> list[str]:
    """Report configured gates that cannot fire this call because their input
    is absent (None) on `state`. Called on every decision, halt or not, so
    'this gate cannot fire' never depends on someone remembering to check."""
    g = cfg["gates"]
    unarmed: list[str] = []
    if "mc_breach_halt" in g and state.mc_breach_prob is None:
        unarmed.append(
            f"mc_breach_prob: configured to halt at {g['mc_breach_halt']:.0%} but "
            "state.mc_breach_prob is None — no Monte Carlo simulator supplies this "
            "input in production (only sovereign/risk/test_risk_layers.py sets it); "
            "this gate cannot fire until one does")
    return unarmed


def gate_factor(signal, state, cfg) -> float:
    """Pure-function form per spec: 0.0 to halt, +inf otherwise."""
    import math
    return 0.0 if run_gates(signal, state, cfg) is not None else math.inf
