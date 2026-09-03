# "If 1 in 5 evaluations pass and I trade it well, am I profitable?"

Asked 2026-09-03. Answered from the real contracts in
`data/propfirm/firm_contracts.yaml`, with the zero-edge control printed beside
every P(pass) as CLAUDE.md requires.

Model: trade-level random walk, risk 0.5% of balance per trade, 2R payoff
target, win rate solved so pre-cost expectancy equals the stated edge. Evaluation
runs the real phase targets and drawdown types; the funded account withdraws at
+5% and dies at max DD. 20,000 sims per evaluation cell.

**Assumption not sourced from the contracts file: 80% profit split.** No split is
recorded in `firm_contracts.yaml`. Everything below scales roughly linearly in it.

## Answer: no — not on anything currently measured. But the reason is not the one expected.

## 1. The "1 in 5" assumption contains no skill

Zero edge, zero cost — a literal coin flip against the real rules:

| firm | P(pass) | cost to fund | E[payout] | EV |
|---|---|---|---|---|
| CTI 1-Step (5% **trailing**) | 23.49% | $1,626 | $2,595 | **+$970** |
| Alpha Swing (10% static) | 32.39% | $1,513 | $8,099 | **+$6,586** |
| FTMO Swing (10% static) | 32.68% | $1,533 | $8,508 | **+$6,975** |

A zero-edge trader passes **1 in 3–4**, not 1 in 5. The assumed pass rate is
*below* the coin-flip rate. It is not an optimistic assumption.

And at zero cost the scheme is **EV-positive with no edge at all** — because a
funded account is a free option: you keep 80% of the upside and the firm absorbs
everything past your drawdown. That structure is real and is not a mistake.

## 2. Cost per trade decides everything, and the cliff is a cliff

Zero edge throughout. Only execution cost changes:

| cost/trade | CTI P(pass) | CTI EV | FTMO P(pass) | FTMO EV |
|---|---|---|---|---|
| 0.00R | 23.49% | +$970 | 32.68% | **+$6,975** |
| 0.02R | 19.06% | +$241 | 22.48% | +$4,166 |
| **0.05R** | 14.32% | −$960 | 12.60% | **+$508** ← knife edge |
| 0.10R | 8.65% | −$3,185 | 3.67% | **−$11,277** |
| 0.15R | 5.08% | −$6,852 | 0.98% | −$49,606 |
| 0.20R | 2.70% | −$13,675 | 0.24% | **−$207,602** |

**Breakeven is ~0.05R per trade.** Above it the free option is gone.

## 3. What edge is required at a realistic 0.10R cost (FTMO)

| edge/trade | P(pass) | cost to fund | E[payout] | EV |
|---|---|---|---|---|
| −0.10R | 0.15% | $334,000 | $1,078 | −$332,922 |
| −0.05R | 0.83% | $60,727 | $1,622 | −$59,105 |
| **0.00R** | 3.90% | $12,846 | $2,436 | **−$10,410** |
| +0.05R | 12.90% | $3,884 | $4,427 | **+$543** |
| +0.10R | 32.23% | $1,554 | $8,647 | +$7,092 |
| +0.20R | 79.47% | $630 | $41,772 | +$41,142 |

Required edge at realistic cost: **≈ +0.05R per trade minimum.**

## 4. What we have actually measured

Nothing that supplies +0.05R. The only measured result clearing its detection
floor is `LOWFLOAT_BASE_RATE.md` — a 5.81× lift screen (54× above floor) whose
own breakeven payoff is 16.3×, i.e. **not yet a positive-expectancy rule**. Every
entry and exit test in this repo to date is null.

So the honest statement: **the required input is +0.05R and the measured input is
unestablished.** Not negative — unestablished.

## 5. The trap this exposes

The instrument class the recent work targets — $1–$10 NASDAQ names — is where
cost per R is *worst*. Spreads there routinely run 0.5–2% of price; against a 5%
stop that is **0.10–0.40R per round trip**, the bottom rows of the table. The
method is aimed at exactly the instruments the EV model says cannot carry it.

**Consequence: the highest-leverage variable is execution cost, not setup
quality.** Halving cost per R moves EV further than any entry refinement yet
attempted. That is a testable, unglamorous, and currently unexplored lane.

## 6. Also note

- **Trailing DD is a trap.** CTI's 5% trailing is worse than FTMO's 10% static at
  every cost level, despite the one-step structure looking easier.
- Model assumes the firm pays, and that re-attempts are unlimited. Neither is
  guaranteed and neither is modelled.
- Monte Carlo cells carry ±MC noise; the 3.67%/3.90% pair in the two tables is
  the same cell across two runs.
