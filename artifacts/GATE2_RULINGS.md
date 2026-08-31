# Gate 2 — seven layers, seven statuses, no undecideds

2026-08-30, engine `bf013ed7727cfaa0` = **SF-FROZEN-004**.

**Standing rule, applied throughout:** every R comparison prints the incumbent's
absolute R. Where all arms are negative, the header says so.

| # | layer | status | the reason it is decided |
|---|---|---|---|
| 1 | catastrophic | **ACTIVE** | the plan stop; never removed, never loosened |
| 2 | breakeven | **ACTIVE** | arms at TP1 |
| 3 | profit_lock | **ACTIVE** | arms at TP2 |
| 4 | trail | **ACTIVE — tested, survived on the declared bar** | see below |
| 5 | volatility | **AVAILABLE, opt-in, no k selected** | see below |
| 6 | thesis | **UNBLOCKED IN THE ENGINE, INPUT MISSING** | see below |
| 7 | time_decay | **OUT** | see below |

## 4 — trail: ACTIVE, and now actually tested

First real SPRT, candidate = trailing disabled, opponent = SF-FROZEN-004 shipped
`CASH_INDEX`, SPY tune lane, 2,116 episodes, δ=0.10, α=.05, β=.20:

- incumbent (trail 1.5) **TOTAL R −172.71** (−0.0816/trade)
- candidate (no trail) **TOTAL R −119.00** (−0.0562/trade)
- absolute delta **+53.71 R**
- **ACCEPT_H0 · stop 91 of 2,116 · deviation 23.0% · mean ΔR +0.0254**

BOTH ARMS NEGATIVE. Trail-off is better — a fourth independent line after
MECH-001 (p=0.116), the oracle declining to trail on 22 of 24, and widening ×3
improving results. But it does not clear the declared δ, and deviation of 23%
confirms the candidate genuinely acted, so this is a real null and not a hollow
one. **`trail` stays ACTIVE because the test said so, not because nobody looked.**

δ=0.10 asks trail-off to erase more than the incumbent's entire per-trade loss.
Whether that is the right bar is a separate pre-registration. **It was not
changed after seeing the result.**

## 5 — volatility: available, no k selected

Built as placement (`entry − direction·k·ATR`, set once, never moved, never reads
`hwm`). Reachable from `ceiling.simulate` as of SF-FROZEN-004. Full-population
sweep, 2,116 entries at every k, zero crashes, **all arms negative**:

| k | base | 0.5 | 0.75 | 1.0 | 1.25 | 1.5 | 2.0 | 2.5 | 3.0 |
|---|---|---|---|---|---|---|---|---|---|
| TOTAL R | −119.00 | −132.91 | **−101.17** | −105.95 | −119.08 | −119.16 | −121.47 | −118.75 | −118.28 |

Best is k=0.75 at **+17.83 R** over baseline (+0.0084/trade) — and still a loss.
Eight k values is eight draws. **No k is selected and none may be until one
clears the gate.** The earlier table showing k=1.5 winning was survivorship from
the StageError crashes and is superseded.

## 6 — thesis: the engine no longer blocks it; the input does

Previously dark for two reasons conflated into one. They are now separated:

- **Engine side: FIXED.** `thesis_sl` in `Stage.ENTERED` used to raise
  `StageError`; it now emits `MOVE_SL 98 → 99.5`. Demonstrated, tested, and
  fault-injected.
- **Input side: STILL MISSING.** `regime.py` raises `NotImplementedError` by
  design, so no caller supplies an invalidation level.

Status is therefore **not** "dark" — it is *available and unsupplied*. The
unblocking path is regime as a **magnitude conditioner**, not a direction
classifier, because ten event studies say direction is null and magnitude is not.
That is a build, and it is Phase 2.

## 7 — time_decay: OUT

`flatten_at_et` is already ACTIVE, already configurable, and already the time
instrument. A tightening *schedule* would be a second time instrument competing
with the first, with no evidence to set its parameter from. Best-fixed configs do
show time-flatten mattering and differing by instrument (NVDA `fl11:00`, QQQ
`fl15:45`, SPY `flNone`) — but best-of-396 is a selection artifact and explicitly
not a result.

**Re-opening requires** a counterfactual showing a schedule beats the cliff on
days that reached TP1. Nobody has produced one.

## The finding that outranks all seven

Every comparison on this page is between losing policies. The incumbent loses
−0.0816/trade; the best thing found today loses −0.0478. **Exits redistribute
what the entry hands them.** This is the ceiling result — 3 of 24 days any config
could win — reappearing on 2,116 episodes instead of 24. Optimising here selects
the least-bad loser. The standing rule exists so that never again reads as
progress.
