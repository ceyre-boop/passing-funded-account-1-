#!/usr/bin/env python3
"""PAPER CARRY RUNNER — Phase 2 of Plans/THE_BIG_PLAN.md, built disarmed.

Wraps `scripts/paper_carry_log.py`'s open/close primitives (which "add no
signal logic: it records what the existing scan produced" — same rule here)
with the three risk guards Phase 1 built, in THE_BIG_PLAN's stated order:

    streak.py cooloff (spec 004)  ->  portfolio_guard.py Gate 7 (spec 014)
    ->  survival.py (spec 002)

A refusal from ANY of the three makes an OPEN structurally impossible: this
module's `open_paper_trade()` returns a REFUSED record and never reaches the
line that writes `data/trade_logs/paper_carry_trades.jsonl` or calls
`sovereign.intelligence.decision_logger`. It is not "logged and allowed
through anyway" — the write is unreachable code on that path, the same shape
as `Broker.send()` refusing a malformed intent before any HTTP call exists to
make.

WHAT THIS MODULE DOES NOT DO
-----------------------------
1. Generate a signal. The caller supplies the proposed trade (pair,
   direction, entry, stop, risk_pct, qty) exactly as `paper_carry_log.py`
   already requires — this module only gates and logs it.
2. Touch a broker. There is no FX broker integration anywhere on this path.
   `sovereign/execution/forex_exit_manager.py`'s OANDA bridge import is
   lazy and reachable only from that file's own `--run` CLI branch, never
   from here. "Paper" means exactly what `paper_carry_log.py` already means:
   a logged, human- or scan-supplied fill price, never an order sent
   anywhere. That keeps this module inside CLAUDE.md rule 9 (no live-order
   access in agent environments) BY CONSTRUCTION — there is nothing here
   capable of placing one, not a policy promising not to.

ARMING — matches `sovereign/execution/forex_exit_manager.py` /
`daytrade/broker.py`'s three-coordinated-edits seriousness:
    1. Flip `ARMED = False` to `True` below — a reviewable diff.
    2. Pass `--yes` on the CLI (or `assume_yes=True` to the Python API) —
       the per-invocation confirmation.
    3. Type 'send' at the interactive prompt, unless step 2 already supplied
       `--yes` (mirrors `Broker.assume_yes` / `Broker._confirm`).
All three must hold or nothing is written. The module ships with (1) false,
so by default every call is SHADOW: the gate still runs in full and prints
its verdict, but `paper_carry_trades.jsonl` and the decision log are never
touched.

TWO UNRESOLVED DOCTRINE INPUTS — REQUIRED CLI ARGUMENTS, NEVER DEFAULTED
--------------------------------------------------------------------------
No ratified carry-lane value exists anywhere in this repo for either of the
below, so this module refuses to invent one (CLAUDE.md rule 3/5) and instead
requires the caller to supply it explicitly, every run:
  --daily-goal-pct         survival.py's Campaign.daily_goal_pct, spec-bound
                            to [1.0, 2.0]. spec 002 was written for the
                            intraday daytrade cockpit's $-per-day doctrine;
                            carry trades hold a median 6 days (411-trade
                            sealed record) so "already banked today's goal,
                            stop" is a daytrade-shaped rule with no carry
                            analogue on record.
  --target-dollars          survival.Proposal.target_dollars. Carry exits on
                            time/trailing/reversal/stop, not a fixed TP, so
                            there is no natural per-trade target to declare.
  --max-total-open-risk-r / --max-per-symbol-risk-r / --max-correlated-risk-r
  --max-unprotected          portfolio_guard.py's G001-G004 fields. No
                            per-symbol/correlated exposure caps for the
                            4-pair carry book have been ratified.
  --cushion-remaining / --day-pnl-so-far
                            dollar terms survival.py needs; there is no
                            live paper-account equity ledger yet (Phase 2
                            scope is the execution path, not a P&L tracker),
                            so the caller states them.

ONE VALUE THIS MODULE DOES DERIVE, NOT INVENT: `resolve_r_limits()` computes
G005/G006 (daily loss lock / emergency flatten, in R) from two numbers
already measured and sourced elsewhere in this repo — the firm contract's
real daily_dd/max_dd (`data/propfirm/firm_contracts.yaml`) and the sealed
curve's own `max_safe_risk` (`scripts/drawdown_margin.py`, quoted in
NEXT.md/THE_BIG_PLAN as 0.328% for cti_1step) as the $-per-R reference. It is
optional — pass `--daily-loss-lock-r`/`--emergency-flatten-r` to override.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import streak  # noqa: E402
import survival  # noqa: E402
from portfolio_guard import (GuardError, PortfolioLimits,  # noqa: E402
                             PositionSnapshot, check as portfolio_check)

from sovereign.propfirm.firm_contracts import FirmContract, load_contract  # noqa: E402

LOG_PATH = ROOT / "data" / "trade_logs" / "paper_carry_trades.jsonl"

# ═══════════════════════════════════════════════════════════════════════════
#  THE TOGGLE — flipping this to True is step 1 of 3 of arming this module.
# ═══════════════════════════════════════════════════════════════════════════
ARMED = False


class GateRefused(RuntimeError):
    """A guard refused the action. Never downgraded to a warning."""


# ------------------------------------------------------------------ inputs

@dataclass(frozen=True)
class TradeProposal:
    """One proposed carry trade, exactly the shape paper_carry_log.py's
    `open` subcommand already takes. This module invents none of it."""
    pair: str
    direction: str          # LONG | SHORT
    entry: float
    stop: float
    risk_pct: float
    qty: float

    def __post_init__(self):
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"direction must be LONG or SHORT, got {self.direction!r}")
        if self.risk_pct <= 0:
            raise ValueError(f"risk_pct must be positive: {self.risk_pct}")
        if self.qty <= 0:
            raise ValueError(f"qty must be positive: {self.qty}")


@dataclass(frozen=True)
class GuardInputs:
    """Everything the three guards need that this module will not derive or
    default (see module docstring: 'two unresolved doctrine inputs'). Every
    field here is a required constructor argument — there is no dataclass
    default hiding a fabricated number."""
    account_size: float
    daily_goal_pct: float
    target_dollars: float
    cushion_remaining: float
    day_pnl_so_far: float
    max_total_open_risk_r: float
    max_per_symbol_exposure_r: float
    max_correlated_exposure_r: float
    max_unprotected_count: int
    daily_loss_lock_r: float
    emergency_flatten_r: float
    correlated_groups: Optional[dict] = None


@dataclass(frozen=True)
class GateVerdict:
    allowed: bool
    size_multiplier: float
    reasons: tuple
    survival_check: object          # survival.SurvivalCheck | None
    guard_verdict: object           # portfolio_guard.GuardVerdict | None


# ------------------------------------------------------------- ledger reads

def _read_ledger(path: Path = LOG_PATH) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_ledger(records: list, path: Path = LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def open_positions_from_ledger(records: list) -> list:
    return [r for r in records if r.get("status") == "open"]


def positions_as_snapshots(open_records: list) -> list:
    """An open trade carries exactly 1R of open risk to its stop, by the
    definition of R (`compute_r` divides pnl_frac by risk_frac) — this is
    read off the record, not estimated. `protected=True` always: every
    record in this ledger was created with an explicit stop (paper_carry_log
    requires --stop), so there is no unprotected-carry-trade shape to
    represent."""
    return [PositionSnapshot(symbol=r["pair"], open_risk_r=1.0, protected=True)
            for r in open_records]


def realized_today_r(records: list, today: date) -> float:
    """Sum of R for trades that CLOSED today. 0.0 is a real, explicit value
    (no trade closed today), never a stand-in for a missing measurement."""
    today_iso = today.isoformat()
    return sum(r["R"] for r in records
               if r.get("status") == "closed" and r.get("exit_date") == today_iso
               and r.get("R") is not None)


# --------------------------------------------------------------- the gate

def gate_open(proposal: TradeProposal, guard_in: GuardInputs, *,
              streak_state: "streak.StreakState", open_records: list,
              today: date) -> GateVerdict:
    """Pure. THE_BIG_PLAN's order: cooloff absolute -> Gate 7 -> survival.
    Returns allowed=False the instant any guard refuses; never evaluates a
    later guard's ALLOW as license to ignore an earlier guard's refusal."""
    # 1. cooloff — absolute, spec 004 / friction_ladder.py's whole premise.
    if streak.blocks_trading(streak_state, today):
        return GateVerdict(False, 0.0,
                           (f"cooloff active until {streak_state.cooloff_until}",),
                           None, None)

    # 2. Gate 7 — current book PLUS the proposed position, so a trade that
    # would itself breach a limit is refused before it exists, not after.
    existing = positions_as_snapshots(open_records)
    prospective = existing + [PositionSnapshot(proposal.pair, 1.0, True)]
    limits = PortfolioLimits(
        max_total_open_risk_r=guard_in.max_total_open_risk_r,
        max_per_symbol_exposure_r=guard_in.max_per_symbol_exposure_r,
        max_correlated_exposure_r=guard_in.max_correlated_exposure_r,
        max_unprotected_count=guard_in.max_unprotected_count,
        daily_loss_lock_r=guard_in.daily_loss_lock_r,
        emergency_flatten_r=guard_in.emergency_flatten_r,
    )
    gv = portfolio_check(limits, prospective,
                          realized_today_r=realized_today_r(open_records, today),
                          correlated_groups=guard_in.correlated_groups)
    if gv.violations:
        return GateVerdict(False, 0.0, (gv.why,), None, gv)

    # 3. survival — the pre-trade "risk $X -> worst case $Y" sentence.
    campaign = survival.Campaign(
        account_size=guard_in.account_size,
        daily_goal_pct=guard_in.daily_goal_pct,
        cushion_remaining=guard_in.cushion_remaining,
        day_pnl_so_far=guard_in.day_pnl_so_far,
        consecutive_red_days=streak_state.consecutive_red_days,
        cooloff_until=streak_state.cooloff_until,
    )
    sp = survival.Proposal(risk_dollars=proposal.risk_pct * guard_in.account_size,
                           target_dollars=guard_in.target_dollars)
    sc = survival.check(campaign, sp)
    if sc.verdict == "NO_TRADE":
        return GateVerdict(False, 0.0, (sc.reason,), sc, gv)
    return GateVerdict(True, sc.size_multiplier, (sc.reason,), sc, gv)


# ------------------------------------------------------------- R-limit derivation

def resolve_r_limits(firm_key: str, *, series: str = "sealed") -> tuple:
    """Derive (daily_loss_lock_r, emergency_flatten_r) from the firm's real
    daily_dd/max_dd and the sealed curve's own measured max_safe_risk
    (scripts/drawdown_margin.py) as the $-per-R reference — two numbers
    already sourced elsewhere in this repo, never invented here. Raises if
    max_safe_risk is 0 (curve breaches at any risk) — a 0 reference makes
    the ratio undefined, not infinite, so this refuses rather than divides.
    """
    from scripts.carry_buy_gate import build_series, load_oos, load_sealed  # noqa
    from scripts.drawdown_margin import max_safe_risk  # noqa

    contract = load_contract(firm_key)
    trades = load_sealed() if series == "sealed" else load_oos()
    _idx, vi, vw, vopen = build_series(trades, contract.costs.swap_haircut_r_per_day,
                                       center=False)
    ref_risk = max_safe_risk(vi, vw, vopen, contract)
    if ref_risk <= 0:
        raise GateRefused(
            f"{firm_key}: max_safe_risk is {ref_risk} — the realized curve breaches "
            "the floor at any positive risk, so there is no valid $-per-R reference "
            "to derive G005/G006 from. Supply --daily-loss-lock-r/--emergency-flatten-r "
            "by hand, or do not run this firm's book paper-forward yet.")
    if contract.daily_dd is None:
        # Matches drawdown_margin.py's own stance verbatim: "not reported as a
        # pass — reported as absent, because a margin against a rule that does
        # not exist is not evidence of anything." Falling back to the
        # NO_DAILY_LIMIT_PCT=1.0 convention (as to_prop_cfg does) would derive
        # a daily_loss_lock_r WIDER than emergency_flatten_r — a lock that
        # fires after the flatten it is supposed to precede — so this refuses
        # instead of fabricating a number PortfolioLimits would reject anyway.
        raise GateRefused(
            f"{firm_key} has no daily_dd — there is no rule to derive G005 "
            "(daily loss lock) from. Supply --daily-loss-lock-r by hand if this "
            "firm's book should still carry an advisory intraday lock, or accept "
            "that G005 is structurally absent for this contract.")
    daily_loss_lock_r = contract.daily_dd.pct / ref_risk
    emergency_flatten_r = contract.max_dd.pct / ref_risk
    if emergency_flatten_r <= daily_loss_lock_r:
        # PortfolioLimits.__post_init__ would raise this anyway; surfaced
        # here with the derivation attached so the cause is legible.
        raise GateRefused(
            f"{firm_key}: derived emergency_flatten_r ({emergency_flatten_r:g}) does "
            f"not sit beyond daily_loss_lock_r ({daily_loss_lock_r:g}) — the contract's "
            "daily_dd is not strictly tighter than its max_dd once scaled by the same "
            "reference risk. Not a valid pair of breakers; fix the inputs, do not paper "
            "over it here.")
    return daily_loss_lock_r, emergency_flatten_r


# --------------------------------------------------------------- arming ritual

def _confirm(desc: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    print("\n" + "-" * 68)
    print("  ARMING — this writes a REAL entry to paper_carry_trades.jsonl")
    print("  and sovereign.intelligence.decision_logger. Nothing is sent to")
    print("  any broker — there is none on this path — but the record is")
    print("  durable and feeds G5.")
    print(f"  {desc}")
    print("-" * 68)
    try:
        ok = input("  type 'send' to confirm this write: ").strip().lower() == "send"
    except EOFError:
        ok = False
    if not ok:
        print("  not confirmed — nothing written\n")
    return ok


# --------------------------------------------------------------- open / close

def open_paper_trade(proposal: TradeProposal, guard_in: GuardInputs, *,
                      firm_key: str = "cti_1step", today: Optional[date] = None,
                      armed: bool = ARMED, assume_yes: bool = False,
                      log_path: Path = LOG_PATH,
                      streak_path: Path = streak.STATE_PATH,
                      mechanisms: Optional[list] = None) -> dict:
    """Gate a proposed carry entry and, only if every guard clears AND the
    module is armed AND confirmed, write it. Returns a status record in
    every outcome — REFUSED, SHADOW, DECLINED_BY_USER, or SENT — never a
    silent no-op (CLAUDE.md non-negotiable #3: an unlogged trade is silent
    data loss, and a decision this module made and did not report is the
    same failure)."""
    today = today or date.today()
    state = streak.load_state(streak_path)
    records = _read_ledger(log_path)
    open_records = open_positions_from_ledger(records)

    verdict = gate_open(proposal, guard_in, streak_state=state,
                        open_records=open_records, today=today)
    rec_common = {"proposal": {"pair": proposal.pair, "direction": proposal.direction,
                                "entry": proposal.entry, "stop": proposal.stop,
                                "risk_pct": proposal.risk_pct, "qty": proposal.qty},
                  "gate": {"allowed": verdict.allowed,
                           "size_multiplier": verdict.size_multiplier,
                           "reasons": list(verdict.reasons)}}

    if not verdict.allowed:
        return {**rec_common, "status": "REFUSED", "sent": False}

    desc = (f"OPEN {proposal.pair} {proposal.direction} @ {proposal.entry} "
            f"stop {proposal.stop} qty {proposal.qty} "
            f"({verdict.size_multiplier:g}x size) — {verdict.reasons[0]}")

    if not armed:
        print(f"           [shadow] WOULD {desc}")
        return {**rec_common, "status": "SHADOW", "sent": False}

    if not _confirm(desc, assume_yes):
        return {**rec_common, "status": "DECLINED_BY_USER", "sent": False}

    import uuid
    trade_id = uuid.uuid4().hex[:10]
    sized_qty = proposal.qty * verdict.size_multiplier
    mechs = mechanisms or []
    rec = dict(id=trade_id, status="open", pair=proposal.pair,
              direction=proposal.direction, entry=proposal.entry,
              stop=proposal.stop, risk_pct=proposal.risk_pct * verdict.size_multiplier,
              qty=sized_qty, entry_date=today.isoformat(), R=None, mechanisms=mechs,
              size_multiplier=verdict.size_multiplier, firm=firm_key)
    records.append(rec)
    _write_ledger(records, log_path)

    from sovereign.intelligence.decision_logger import log_forex_decision
    log_forex_decision(pair=proposal.pair, direction=proposal.direction,
                       entry_level=proposal.entry, stop_loss=proposal.stop,
                       hold_days=0, risk_pct=proposal.risk_pct * verdict.size_multiplier,
                       signal_layers=["paper_carry_runner_phase2"] + mechs,
                       extra=dict(paper_trade_id=trade_id, qty=sized_qty,
                                  mechanisms=mechs, gate_reasons=list(verdict.reasons)))
    return {**rec_common, "status": "SENT", "sent": True, "trade_id": trade_id}


def close_paper_trade(trade_id: str, exit_price: float, *,
                      exit_date: Optional[date] = None, reason: str = "manual",
                      firm_key: str = "cti_1step", armed: bool = ARMED,
                      assume_yes: bool = False, log_path: Path = LOG_PATH) -> dict:
    """Closes are never refused by a guard: portfolio_guard's own vocabulary
    is 'LOCKOUT: no NEW risk' / 'FLATTEN_ALL: everything closes' — a close
    reduces risk, so it is exactly the action those guards want to see, not
    one they exist to block. Still gated by the same ARMED ritual as open,
    because it is still a durable write this module makes on its own."""
    exit_date = exit_date or date.today()
    records = _read_ledger(log_path)
    match = [r for r in records if r["id"] == trade_id]
    if not match:
        raise GateRefused(f"no open trade with id {trade_id!r}")
    rec = match[0]
    if rec["status"] == "closed":
        raise GateRefused(f"trade {trade_id} is already closed")

    hold = (exit_date - date.fromisoformat(rec["entry_date"])).days
    if hold < 0:
        raise GateRefused("exit date precedes entry date")

    contract = load_contract(firm_key)
    from scripts.paper_carry_log import compute_r  # noqa: E402 — one R formula
    r = compute_r(rec["direction"], rec["entry"], rec["stop"], exit_price, hold,
                 contract.costs.swap_haircut_r_per_day)

    desc = f"CLOSE {trade_id} ({rec['pair']}) @ {exit_price} R={r:+.3f} reason={reason}"
    if not armed:
        print(f"           [shadow] WOULD {desc}")
        return {"id": trade_id, "status": "SHADOW", "sent": False, "R": round(r, 6)}
    if not _confirm(desc, assume_yes):
        return {"id": trade_id, "status": "DECLINED_BY_USER", "sent": False}

    rec.update(status="closed", exit=exit_price, exit_date=exit_date.isoformat(),
              hold_days=hold, R=round(r, 6), exit_reason=reason)
    _write_ledger(records, log_path)

    from sovereign.intelligence.decision_logger import update_outcome
    update_outcome(pair=rec["pair"], entry_timestamp=rec["entry_date"],
                   outcome="WIN" if r > 0 else "LOSS", r_realized=r,
                   exit_timestamp=exit_date.isoformat(), system="FOREX")
    return {"id": trade_id, "status": "SENT", "sent": True, "R": round(r, 6)}


if __name__ == "__main__":
    print(__doc__)
    print("run the suite: pytest daytrade/test_paper_carry_runner.py -v")
