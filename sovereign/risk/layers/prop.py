"""Layer 7 — PROP REAL-TIME CEILING. The layer that passes or blows the challenge.

Returns the maximum risk_pct on THIS trade such that, if its stop is hit AND every currently-open
position hits its stop SIMULTANEOUSLY (the correlated worst case), resulting equity still stays above
BOTH the daily-loss floor and the max-drawdown floor (static or trailing per config), with a safety
buffer. If even zero additional risk would breach, returns 0.

Pure function of RiskState — no broker needed (the live path still calls PropRiskManager directly).

---

EVAL vs FUNDED — two opposite optimization problems, split below
------------------------------------------------------------------
`prop_ceiling` above is the real-time floor: a per-trade, per-state hard cap that
must always bind, in both phases. It says nothing about what risk level is
actually GOOD to run — that is a policy question, and evaluation and funded
trading want opposite answers to it:

  phase       maximize                          shape
  ----------  --------------------------------  ---------------------------
  evaluation  P(target before drawdown)          ruin problem, interior optimum
  funded      E[payout] given free-to-lose        Kelly problem, much larger

`eval_size()` reads the pre-registered survival frontier from
`scripts/ruin_engine.py` (`data/agent/ruin_engine_frontier.json`) and reports
the argmax risk-per-trade for a single, no-rebuy eval attempt — plus the
plateau around it, because the peak there is empirically flat, not a point
(0.45-0.60% on cti_1step sits inside one shared Wilson interval).

`funded_size()` solves the opposite problem: the growth-optimal (full, not
fractional, Kelly) risk fraction computed from the sealed edge stats, capped
by the SAME `max_dd.pct` field `prop_ceiling` already treats as the binding
floor — so this split narrows the objective, it does not loosen the existing
guard. Translating that fraction into an expected-payout number needs a
profit split and a payout cadence; `data/propfirm/firm_contracts.yaml`
carries neither field today, so `funded_size()` refuses outright (raises
`MissingContractInput`) rather than inventing either one (repo rule 3).

Neither function is wired into anything that places an order — both are
"compute and display only" per the unratified-sizing constraint in CLAUDE.md.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from sovereign.propfirm.firm_contracts import CONTRACTS_PATH, load_contract
from sovereign.risk.kelly_math import fractional_kelly, hoeffding_win_rate

_REPO_ROOT = Path(__file__).resolve().parents[3]
RUIN_FRONTIER_PATH = _REPO_ROOT / "data" / "agent" / "ruin_engine_frontier.json"
SEALED_TRADES_PATH = _REPO_ROOT / "data" / "proof" / "backtest_trades_v015_2015_2024.csv"


class MissingContractInput(ValueError):
    """`funded_size()` needs a contract field the yaml does not supply.

    Repo rule 3: never let a missing input become a number. This is the
    loud-refusal path, not a fallback — the caller gets exactly the field
    name(s) that are absent, not a guess.
    """

    def __init__(self, firm_key: str, missing_fields: list[str]):
        self.firm_key = firm_key
        self.missing_fields = list(missing_fields)
        super().__init__(
            f"{firm_key}: funded_size() cannot run — missing contract field(s) "
            f"{', '.join(self.missing_fields)} in "
            f"data/propfirm/firm_contracts.yaml. Refusing to invent a number."
        )


def prop_ceiling(signal, state, cfg) -> float:
    p = cfg["prop"]
    acct = float(p["account_size"])
    equity = float(state.equity)
    buffer_abs = float(p["safety_buffer_pct"]) * acct

    # Daily-loss floor: day-start equity minus today's loss budget.
    today_pnl = state.daily_realized_pnl + state.daily_open_pnl
    day_start_equity = equity - today_pnl
    daily_loss_budget = float(p["daily_loss_limit_pct"]) * acct
    daily_floor = day_start_equity - daily_loss_budget

    # Max-drawdown floor (trailing from peak, or static from start).
    if p.get("drawdown_type", "trailing") == "trailing":
        dd_floor = float(state.peak_equity) * (1.0 - float(p["max_drawdown_pct"]))
    else:
        dd_floor = float(state.starting_balance) * (1.0 - float(p["max_drawdown_pct"]))

    # The binding floor is the highest one we must stay above, plus the safety buffer.
    binding_floor = max(daily_floor, dd_floor) + buffer_abs

    # Total dollar loss we can absorb from here before breaching.
    loss_budget_now = equity - binding_floor
    if loss_budget_now <= 0:
        return 0.0  # already at the edge — no new risk permitted

    # Worst case: all open positions stop out simultaneously (correlated to 1).
    open_risk_dollars = sum(float(getattr(pos, "risk_pct_at_entry", 0.0))
                            for pos in state.open_positions) * equity

    this_trade_budget = loss_budget_now - open_risk_dollars
    if this_trade_budget <= 0:
        return 0.0  # open positions already consume the entire budget

    return max(0.0, this_trade_budget / equity)


# ============================================================================
# EVAL_SIZE — ruin-avoidance argmax (single, no-rebuy eval attempt)
# ============================================================================

def load_ruin_frontier(path: Path | str | None = None) -> dict:
    """Read the pre-registered `scripts/ruin_engine.py` output. Never
    recomputes it here — a second implementation of the frontier sweep that
    silently disagreed with the pinned one would be worse than none
    (same discipline `scripts/test_ruin_engine.py` already enforces)."""
    p = Path(path) if path is not None else RUIN_FRONTIER_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"{p}: no ruin-engine frontier on disk — run "
            f"`python3 scripts/ruin_engine.py --firm <firm>` first"
        )
    with open(p) as f:
        return json.load(f)


def eval_size(firm_key: str, frontier: dict | None = None) -> dict:
    """Argmax P(pass) over the ruin_engine risk sweep for `firm_key` —
    the ruin-avoidance objective. Takes NO profit_split, NO payout schedule,
    and NO contract object: the eval-phase question is only "does this
    attempt clear the target before the drawdown floor," which the sweep
    already answers per risk level.

    The frontier's own Wilson interval (`p_pass_lo`/`p_pass_hi`, already
    computed by ruin_engine.py — never re-derived here) is used to find every
    risk level statistically indistinguishable from the single argmax cell.
    Reporting only the argmax point would be spuriously precise: on
    cti_1step the peak is a PLATEAU (0.45%-0.60% span 74.1/74.2/73.6/75.6%,
    all inside one shared confidence interval), not a single best number.
    """
    frontier = frontier if frontier is not None else load_ruin_frontier()

    if frontier.get("firm") != firm_key:
        raise ValueError(
            f"frontier on disk is for {frontier.get('firm')!r}, not "
            f"{firm_key!r} — run `scripts/ruin_engine.py --firm {firm_key}` "
            f"first, or pass a matching frontier dict explicitly"
        )

    rows = frontier.get("rows") or []
    if not rows:
        raise ValueError(f"{firm_key}: frontier has no rows")

    best = max(rows, key=lambda r: r["edge"]["p_pass"])
    best_lo = best["edge"]["p_pass_lo"]
    best_hi = best["edge"]["p_pass_hi"]

    # Plateau: every risk whose Wilson interval overlaps the argmax cell's.
    plateau = sorted(
        r["edge"]["risk"] for r in rows
        if r["edge"]["p_pass_hi"] >= best_lo and r["edge"]["p_pass_lo"] <= best_hi
    )

    return {
        "firm": firm_key,
        "objective": "argmax P(pass), single no-rebuy eval attempt "
                      "(scripts/ruin_engine.py frontier)",
        "argmax_risk_pct": best["edge"]["risk"],
        "argmax_p_pass": best["edge"]["p_pass"],
        "argmax_p_pass_ci": [best_lo, best_hi],
        "plateau_risk_lo_pct": plateau[0],
        "plateau_risk_hi_pct": plateau[-1],
        "plateau_n_cells": len(plateau),
        "recommended_risk_pct": round((plateau[0] + plateau[-1]) / 2.0, 4),
        "note": (
            "The peak is a plateau, not a point — every risk level listed "
            "overlaps the argmax cell's Wilson interval and is statistically "
            "indistinguishable from it. recommended_risk_pct is the plateau "
            "midpoint, not a precision claim."
        ),
        "series": frontier.get("series"),
        "horizon_days": frontier.get("horizon_days"),
    }


# ============================================================================
# FUNDED_SIZE — growth-optimal (Kelly) sizing for a free-to-lose account
# ============================================================================

def _edge_stats_from_sealed_trades(path: Path | str | None = None) -> dict:
    """Real win_rate / avg_win_r / avg_loss_r from the sealed v015 record —
    computed here, never re-sourced from a cache. `risk_adjusted_pnl_pct` in
    the CSV is pnl_pct * risk_pct (both columns present, not an R-multiple);
    the R-multiple Kelly needs is pnl_pct / risk_pct."""
    p = Path(path) if path is not None else SEALED_TRADES_PATH
    if not p.exists():
        raise FileNotFoundError(f"{p}: sealed trade record not found")
    with open(p, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{p}: sealed trade record has no rows")

    r_multiples = [float(r["pnl_pct"]) / float(r["risk_pct"]) for r in rows]
    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r <= 0]
    if not wins or not losses:
        raise ValueError(f"{p}: need at least one win and one loss to size Kelly")

    return {
        "n": len(r_multiples),
        "win_rate": len(wins) / len(r_multiples),
        "avg_win_r": sum(wins) / len(wins),
        "avg_loss_r": abs(sum(losses) / len(losses)),
    }


def _raw_contract_yaml(firm_key: str, path: Path | str | None = None) -> dict:
    """Read a firm's entry straight off the yaml (not the FirmContract
    dataclass, which does not model profit_split/payout_schedule at all —
    those fields do not exist anywhere in this repo's contract model yet)."""
    p = Path(path) if path is not None else CONTRACTS_PATH
    with open(p) as f:
        raw = yaml.safe_load(f)
    if firm_key not in raw:
        raise KeyError(f"unknown firm {firm_key!r}; known: {sorted(raw)}")
    return raw[firm_key]


def funded_size(
    firm_key: str,
    *,
    profit_split: float | None = None,
    payout_interval_days: float | None = None,
    edge_stats: dict | None = None,
    contract=None,
    kelly_fraction: float = 1.0,
) -> dict:
    """Growth-optimal risk-per-trade for a FUNDED account, plus the expected
    payout that risk level implies — the opposite objective from
    `eval_size()`. Once funded, further losses cost the trader nothing (the
    fee is sunk, it is the firm's capital) up to the point the account is
    pulled, so the usual capital-preservation case for shrinking to
    quarter-Kelly is weaker here; this runs `kelly_fraction=1.0` (full,
    growth-rate-optimal Kelly) by default — "Kelly problem, much larger,"
    per the task brief — still hard-capped by the contract's own
    `max_dd.pct`, the SAME field `prop_ceiling` treats as the binding floor.
    That cap is a floor-preservation guard, not a suggestion: this function
    can never recommend a risk level `prop_ceiling`'s existing logic
    wouldn't eventually clamp anyway.

    `profit_split` and `payout_interval_days` are required to turn that risk
    level into a dollar expected-payout figure. Neither exists in
    `data/propfirm/firm_contracts.yaml` today (checked directly against the
    raw yaml, not inferred) — the caller must supply an explicit override or
    this raises `MissingContractInput` naming exactly what's absent, rather
    than defaulting to an invented split or cadence.
    """
    contract = contract or load_contract(firm_key)
    raw = _raw_contract_yaml(firm_key)

    if profit_split is None:
        profit_split = raw.get("profit_split")
    payout_schedule = raw.get("payout_schedule") or {}
    if payout_interval_days is None:
        payout_interval_days = payout_schedule.get("interval_days")

    missing = []
    if profit_split is None:
        missing.append("profit_split")
    if payout_interval_days is None:
        missing.append("payout_schedule.interval_days")
    if missing:
        raise MissingContractInput(firm_key, missing)

    if not 0 < profit_split <= 1:
        raise ValueError(f"{firm_key}: profit_split out of range: {profit_split}")
    if payout_interval_days <= 0:
        raise ValueError(
            f"{firm_key}: payout_interval_days must be positive: {payout_interval_days}"
        )

    stats = edge_stats or _edge_stats_from_sealed_trades()
    p_adj = hoeffding_win_rate(stats["win_rate"], stats["n"])

    # Growth-optimal fraction from the real sealed edge stats — full Kelly by
    # default (kelly_fraction=1.0), NOT the quarter-Kelly CLAUDE.md #4
    # prescribes for the eval-phase sizing path. That prescription exists to
    # protect capital that has a cost to lose; a funded account's downside
    # is the firm's, not the trader's, so the argument for shrinking away
    # from the growth-optimal point does not transfer here.
    growth_optimal = fractional_kelly(
        p_adj, stats["avg_win_r"], stats["avg_loss_r"],
        fraction=kelly_fraction, floor=0.0, ceiling=1.0,
    )

    # The funded drawdown rule is still a hard ceiling — reused directly from
    # the contract, the exact field prop_ceiling's own cfg["prop"] carries as
    # "max_drawdown_pct". This is what keeps that ceiling binding: nothing
    # below can ever exceed it.
    dd_ceiling = float(contract.max_dd.pct)
    risk_pct = min(growth_optimal, dd_ceiling)

    expected_r_per_trade = (
        p_adj * stats["avg_win_r"] - (1.0 - p_adj) * stats["avg_loss_r"]
    )
    expected_payout_per_trade_usd = (
        risk_pct * contract.account_size * expected_r_per_trade * profit_split
    )

    return {
        "firm": firm_key,
        "objective": "growth-optimal (Kelly) risk-per-trade, "
                      "E[payout] given a free-to-lose account",
        "kelly_fraction": kelly_fraction,
        "growth_optimal_risk_pct": growth_optimal,
        "max_dd_ceiling_pct": dd_ceiling,
        "recommended_risk_pct": risk_pct,
        "dd_ceiling_binds": growth_optimal > dd_ceiling,
        "edge_stats": stats,
        "hoeffding_win_rate": p_adj,
        "profit_split": profit_split,
        "payout_interval_days": payout_interval_days,
        "expected_r_per_trade": expected_r_per_trade,
        "expected_payout_per_trade_usd": expected_payout_per_trade_usd,
        "note": (
            "Closed-form expected value from sealed per-trade edge stats — "
            "NOT a Monte Carlo survival simulation like eval_size()'s "
            "frontier. No funded-phase ruin_engine equivalent exists yet; "
            "that is a real gap, not a claim of precision this number does "
            "not have."
        ),
    }
