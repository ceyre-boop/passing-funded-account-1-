# 002 — SURVIVAL PLANNER  `daytrade/survival.py`   `[SPEC]`
Plan for the worst, hope for the best — computed before the order exists, not
felt after the loss. Pure arithmetic, no market data, no dependencies. Small
file, high value, useful the morning it lands.

## The question it answers, out loud, before every entry
> "If this loses, where am I, is the day still salvageable, and how many days
> back to on-track at goal?"

## Contract
```python
@dataclass(frozen=True)
class Campaign:
    account_size: float
    daily_goal_pct: float          # 1.0-2.0 — ONE number per account, forever
    cushion_remaining: float       # eval drawdown room, dollars
    day_pnl_so_far: float
    consecutive_red_days: int
    cooloff_until: Optional[str]   # from streak.json

@dataclass(frozen=True)
class Proposal:
    risk_dollars: float            # what this trade loses if stopped
    target_dollars: float          # what it makes at TP2 (the day goal)

@dataclass(frozen=True)
class SurvivalCheck:
    verdict: Literal["GO", "SIZE_DOWN", "NO_TRADE"]
    size_multiplier: float         # <= 1.0 ALWAYS. never scales up. ever.
    worst_case_balance: float
    cushion_after_loss: float
    days_to_recover_at_goal: float
    still_on_track: bool
    reason: str                    # one plain sentence, printed before entry

def check(campaign: Campaign, proposal: Proposal) -> SurvivalCheck: ...
```

## Rules, in priority order (first hit wins)
```python
def check(c, p):
    goal = c.account_size * c.daily_goal_pct / 100

    # 1. cooloff is absolute — the friction model says the whole ladder rests on it
    if c.cooloff_until and today() < c.cooloff_until:
        return NO_TRADE("cooloff active until {c.cooloff_until}")
    if c.consecutive_red_days >= 2:
        return NO_TRADE("two consecutive red days -> 5-day cooloff starts now")

    # 2. a loss must never break the eval
    if p.risk_dollars >= c.cushion_remaining:
        return NO_TRADE("a stop-out ends the account; no setup is worth that")
    if p.risk_dollars > 0.5 * c.cushion_remaining:
        return SIZE_DOWN(mult=0.5*c.cushion_remaining/p.risk_dollars,
                         reason="one loss would eat over half the remaining cushion")

    # 3. one shot per day — already green means done (doctrine: take the day, leave)
    if c.day_pnl_so_far >= goal:
        return NO_TRADE("daily goal already banked. the day is won. stop.")

    # 4. after a loss today, bet 2 is SMALLER with wider room (doctrine, not revenge)
    if c.day_pnl_so_far < 0:
        return SIZE_DOWN(mult=BET2_MULT,          # 0.5, fixed, never adjusted upward
                         reason="bet 2: smaller size, wider room, not a recovery bet")

    # 5. clean
    return GO(mult=1.0, reason=...)
```

## The sentence it must print (this is the actual deliverable)
```
SURVIVAL: risk $140 -> worst case $24,860, cushion $860 left, 1 day back to
on-track at $300/day goal. Still on track: YES. Verdict: GO (1.0x).
```
If a human reads that and hesitates, the system did its job.

## Invariants — enforce with assertions, fail loud
- `size_multiplier <= 1.0`, always. There is no code path that scales size up.
  Not after a loss, not after a win, not on high confidence. **Bet 2 is smaller
  than bet 1 — this is the single rule that separates the doctrine from tilt.**
- `daily_goal_pct` is read from account config, never computed from recent P&L.
  A goal that moves to catch up is how accounts die.
- Regime confidence does NOT enter this calculation. Survival math is
  independent of how good the setup looks — that is the entire point.

## `[SKETCH]` — not now
- **Multi-day recovery pathing** (if we're down 3 days, what's the optimal
  sequence of goals to get back on track without raising risk?). Interesting,
  and the ladder math is the right tool for it, but it needs the campaign to
  actually have history first. Revisit after ~20 logged days.
- **Kelly-style sizing off measured win rate.** Tempting and premature: it needs
  a real win-rate estimate, which needs ~50 shots (see NEXT.md). Until then,
  fixed fractional risk is correct and honest. The quant repo already has a
  bounded quarter-Kelly engine to port when the day comes — do not rewrite it.
