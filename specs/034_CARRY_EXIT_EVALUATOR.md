# 034 — CARRY-EXIT-V1 `[SPEC]`

**Component:** `daytrade/carry_exit.py`, `daytrade/carry_reconcile.py`
**Status:** built 2026-08-20, against the 10,188-row FX state ledger.
**Closes:** stack exam **SF-3** (the one FAIL) — structurally, not by tuning.

## Why a second evaluator and not six more fields

SF-3 measured it: `TradeState` has 23 fields, every one intraday, and no term
for swap accrual, days held, weekend exposure, rate differential, or
financing. Extending it would produce one type complete for NEITHER position
and would break the property the exam passes at ceiling — one `decide_exit`,
39 callers, byte-identical replays. So `futures-exit-v1` stays frozen and
correct for what it prices; `carry-exit-v1` is a separate engine with its own
state type, and a test asserts neither type contains the other's terms.

`frozen_policy.policy("FX_CARRY")` **raises**, quoting SF-3 — the guardrail
that makes borrowing the intraday opponent fail loudly.

## What it prices that the intraday engine cannot

`swap_paid_r` (weekends bill three days — 72% of sealed trades cross one),
`net_r = gross_r − financing` (holding is never free), `carry_flipped` (the
reason for the trade is gone), and a daily-bar ATR trail. The exit vocabulary
is **read from the sealed record, not invented**: the 411 v015 trades exit for
exactly four reasons, so the engine expresses those four and
`apply_carry_action` refuses any other.

## Reconciliation — a completeness test, and it FAILED usefully

Replaying all 411 sealed trades through the engine:

| sealed reason | n | engine reproduced | verdict |
|---|---|---|---|
| stop | 9 | 89% | expressible |
| time | 205 | 85% | expressible |
| trailing_stop | 118 | 1–2% | **missing term** |
| reversal | 79 | 0% | **missing term** |

Overall 44.8% exit-reason match, median day delta 0.

**Precedence was tested, not assumed.** The hypothesis that the trail should
outrank the time stop (sealed trailing exits hold a median 7 days, longer
than the 5-day stop) was made a declared switch and measured both ways:
44.8% vs 44.0%. **Precedence is not the missing piece** — and measuring
rather than assuming prevented "fixing" the wrong thing.

## The missing terms, named rather than tuned away

1. **Entry-signal state.** The record's `reversal` (median hold 1 day) is a
   *signal* flip, not a rate-differential flip. The engine has no term for
   the entry signal because that logic lives in the general repo. Ceiling
   without it: 81% (332/411).
2. **The true stop-placement rule.** `risk_pct` is position-sizing risk and
   may not equal stop distance; the derived stop fires early on 48 of the 118
   trailing trades. The real rule is not in this repo.
3. **The trail specification.** 2×ATR14 on daily bars is a declared guess.

None of these is fixed by tuning, and tuning is forbidden here regardless:
the evaluator is the yardstick everything else is measured against, and an
evaluator fitted to the record stops being one.

## Invariants

- I53: `CarryState` and `TradeState` share no terms; neither is a superset.
- I54: financing is subtracted from position value; a weekend bills 3 days.
- I55: a stale rate read (JPY/AUD lag 60–80 days) never triggers a reversal.
- I56: precedence is stop → reversal → time/trail, with the last pair a
  declared, measurable switch.
- I57: `MOVE_SL` may never loosen; the catastrophic stop is never removed.
- I58: an exit reason outside the sealed vocabulary is refused.
- I59: the frozen checkpoint refuses to price a carry position.

M52–M58 all killed (`034_MUTATION_LOG.md`).

## Out of scope

Trading anything; tuning any parameter to raise the match rate; importing the
entry signal (general repo); changing any gate.
