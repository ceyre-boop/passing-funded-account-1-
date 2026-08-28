#!/usr/bin/env python3
"""Mechanical baseline plan writer — the OR-break plan, from the same entry
rule the furnace uses (ceiling.find_entry, one implementation).

Purpose: prereg R-band scoring needs a plan with entry/sl on file (spec 024's
resolver refuses to invent R — "no plan = R undefined" is honest but leaves
the gradebook empty). This writes data/daytrade/plan.json mechanically once
the opening range has formed and broken, giving every judgment that day a
real R geometry to be scored against.

Rules:
  - Runs from the tick; does nothing before 10:00 ET, nothing twice a day,
    nothing if today's session has no OR break yet.
  - qty is a NOMINAL 1.0 — this plan prices R geometry for scoring; it is
    not an order and the runner is not pointed at it during the soak.
  - Overwrites only a plan it wrote itself (marker field); a hand-written
    plan.json is never touched.

SURVIVAL WIRING (spec 002, added when survival.py/streak.py were wired onto
the live path): every plan this writer produces carries a `_survival` block
and an `armed` flag, honest about what it could and couldn't check.

`cooloff_until` / `consecutive_red_days` DO have an honest live-path source —
`data/streak.json` via `streak.load_state()` — even while empty/default,
because "no red days recorded" is the true state of an account that hasn't
had a session close logged yet, not a fabricated one.

`account_size` / `daily_goal_pct` / `cushion_remaining` / `day_pnl_so_far` do
NOT currently have an honest live-path source. As of 2026-08-26 (see
`NEXT.md`) there is no funded daytrade account open — the Tradeify campaign
in `MONDAY_OPEN.md` is superseded and the NVDA opening-range-break cockpit is
"finished as an edge source for now." `qty` in this plan is also NOMINAL
(1.0), not a real position size, so even a per-share `risk` figure here is
not a real dollar exposure to check against a real cushion. Rather than
invent an account_size or a cushion number, this module reads an optional
operator-maintained `data/daytrade/campaign_config.json` — if that file
exists and is complete, survival.check() runs for real; if it doesn't
(today), the `_survival` block says so explicitly and `armed` is False. This
mirrors streak.py's SKIP-with-reason discipline: absence of a real cushion is
never treated as an infinite one.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bars import load_partial_session, BarDataError                # noqa: E402
from ceiling import find_entry, TP1_R, TRIGGER_END                 # noqa: E402
import streak as streak_mod                                        # noqa: E402
import survival as survival_mod                                    # noqa: E402

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "data" / "daytrade" / "plan.json"
MARKER = "mechanical-or-break-v1"
CAMPAIGN_CONFIG = ROOT / "data" / "daytrade" / "campaign_config.json"
_CAMPAIGN_FIELDS = ("account_size", "daily_goal_pct", "cushion_remaining",
                     "day_pnl_so_far")


def _load_campaign_config() -> dict | None:
    """Read the operator-maintained account-state config, if one exists.

    Returns None (never a fabricated dict) when the file is absent, unreadable,
    or missing any required field — those are the CLAUDE.md rule-3 cases this
    function refuses to silently paper over with a zero or a guess.
    """
    if not CAMPAIGN_CONFIG.exists():
        return None
    try:
        cfg = json.loads(CAMPAIGN_CONFIG.read_text())
    except json.JSONDecodeError:
        return None
    if any(cfg.get(f) is None for f in _CAMPAIGN_FIELDS):
        return None
    return cfg


def _survival_block(e, risk_dollars: float) -> dict:
    """Best-effort SurvivalCheck for this plan's geometry. Always returns an
    honest dict — `available: False` with a plain reason when a real check
    cannot be run, never a fabricated verdict."""
    streak_state = streak_mod.load_state()
    campaign_cfg = _load_campaign_config()
    if campaign_cfg is None:
        return {
            "available": False,
            "reason": (
                "no honest account_size/cushion_remaining/day_pnl_so_far "
                "source on the live path today (no funded daytrade account "
                f"currently open; add {CAMPAIGN_CONFIG.relative_to(ROOT)} to "
                "enable this check) — survival verdict withheld, not "
                "fabricated"
            ),
            "cooloff_until": streak_state.cooloff_until,
            "consecutive_red_days": streak_state.consecutive_red_days,
        }
    try:
        campaign = survival_mod.Campaign(
            account_size=campaign_cfg["account_size"],
            daily_goal_pct=campaign_cfg["daily_goal_pct"],
            cushion_remaining=campaign_cfg["cushion_remaining"],
            day_pnl_so_far=campaign_cfg["day_pnl_so_far"],
            consecutive_red_days=streak_state.consecutive_red_days,
            cooloff_until=streak_state.cooloff_until,
        )
        proposal = survival_mod.Proposal(
            risk_dollars=risk_dollars,
            target_dollars=campaign_cfg["account_size"]
            * campaign_cfg["daily_goal_pct"] / 100,
        )
    except ValueError as exc:
        return {"available": False, "reason": f"invalid campaign config: {exc}"}
    sc = survival_mod.check(campaign, proposal)
    return {
        "available": True,
        "verdict": sc.verdict,
        "size_multiplier": sc.size_multiplier,
        "worst_case_balance": sc.worst_case_balance,
        "cushion_after_loss": sc.cushion_after_loss,
        "days_to_recover_at_goal": sc.days_to_recover_at_goal,
        "still_on_track": sc.still_on_track,
        "reason": sc.reason,
        "sentence": survival_mod.sentence(campaign, proposal, sc),
    }


def main(symbol: str = "NVDA") -> int:
    now = datetime.now(ET)
    today = str(now.date())
    if now.strftime("%H:%M") < "10:00":
        print("  before 10:00 ET — opening range not complete, no plan")
        return 0
    if PLAN.exists():
        try:
            cur = json.loads(PLAN.read_text())
        except json.JSONDecodeError:
            print(f"  !! {PLAN.name} unreadable — refusing to touch it")
            return 1
        if cur.get("_writer") != MARKER:
            print("  hand-written plan on file — never overwritten")
            return 0
        if cur.get("_session") == today:
            return 0                        # today's plan already written
    try:
        sess = load_partial_session(symbol, now.date(), TRIGGER_END, "5m")
    except BarDataError as e:
        print(f"  !! {e}")
        return 1
    if sess is None:
        print(f"  no complete session for {today} in cache yet")
        return 0
    e = find_entry(sess)
    if e is None:
        print(f"  {today}: no OR break (yet) — no plan")
        return 0

    survival = _survival_block(e, e.risk)
    refused = survival.get("available") and survival["verdict"] == "NO_TRADE"
    # `armed` is the plan's own honest self-report: True only when a real
    # survival check ran and cleared it. No check ran (available=False) or a
    # NO_TRADE verdict both mean the plan is NOT armed — this is still not an
    # order either way, but a refused/unchecked plan must never render the
    # same as a cleared one.
    armed = bool(survival.get("available")) and not refused
    PLAN.write_text(json.dumps({
        "_writer": MARKER, "_session": today,
        "_note": "R-geometry plan for prereg scoring; NOT an order",
        # Same scheme as daytrade/runner.py's `trade_id = f"{symbol}-{session_date}"`
        # and alpha_operator.py's `_session_trade_id()` — one id, threaded, not a
        # third scheme this plan would otherwise need reconciling against later.
        "trade_id": f"{symbol}-{today}",
        "symbol": symbol, "direction": "long" if e.direction > 0 else "short",
        "entry": e.entry, "sl": e.stop, "qty": 1.0,
        "tp1": e.tp1, "tp2": e.tp2, "trail_dist": e.trail_dist,
        "exit_policy": "REFUSED_NO_TRADE" if refused else "DEFAULT",
        "armed": armed,
        "_survival": survival,
    }, indent=1))
    if refused:
        print(f"  plan REFUSED by survival check: {survival['reason']}")
    elif not survival.get("available"):
        print(f"  plan written UNARMED (survival check unavailable): "
              f"{survival['reason']}")
    print(f"  plan written: {symbol} {'LONG' if e.direction > 0 else 'SHORT'} "
          f"entry {e.entry:.2f} sl {e.stop:.2f} (risk {e.risk:.2f}) — {e.ts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "NVDA"))
