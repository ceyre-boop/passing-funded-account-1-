# PROMPTS.md — the words to say

Written 2026-08-26. The repo has grown +166,722 lines across 180 commits since
2026-07-31 against 2 logged shots and 0 paper trades. That ratio is the failure
mode, not a symptom of it. Every prompt below is chosen because it either
produces a number that changes a decision, or produces a trade. Nothing here
adds a verification layer.

## The reframe these are built on

Passing an evaluation is not a trading problem. It is a **survival-probability**
problem with three inputs — edge distribution, bet size, and the firm's two
thresholds — and one output: P(reach target before ruin). Signal quality is the
input you have been optimizing. Bet size is the input that dominates the answer,
and it is currently unratified (`CLAUDE.md`: "eval sizing is NOT ratified yet").

Two objective functions, not one:

| Phase | Maximize | Implication |
|---|---|---|
| Evaluation | P(target before drawdown) | Sizing is a ruin problem. Interior optimum. |
| Funded | E[payout] given the account is free to lose | Sizing is a Kelly problem. Much larger. |

Running one sizing rule across both phases is the single most common way a
funded account is lost. They are opposite problems.

**The lever you own that Renaissance cannot buy:** capacity. An edge that holds
a few million dollars is worthless to a $10B fund and perfectly good to you.
This argues for more small uncorrelated bets in unglamorous corners — not for
better machinery around one bet.

**The metric that makes it passive:** decisions required of Colin per week.
Drive it to zero. Every discretionary hook is a place the system needs you.

---

## 1. The ruin engine — build this before anything else

> Build `scripts/ruin_engine.py`. Given a firm contract from
> `data/propfirm/firm_contracts.yaml`, a per-trade return distribution
> bootstrapped from `data/proof/backtest_trades_v015_2015_2024.csv`, and a
> sizing rule expressed as risk-per-trade in percent, run Monte Carlo to
> convergence and report P(pass), P(ruin), P(neither / still open), E[days to
> resolution], and the full drawdown path distribution. Sweep risk-per-trade
> from 0.10% to 3.00% in 0.05% steps and print the frontier as a table plus a
> single chart. Model the real mechanics: trailing vs static drawdown per the
> contract's `max_dd.type`, daily loss limits where the contract has one, the
> `swap_haircut_r_per_day` cost, and the median 6-day hold. Bootstrap in blocks,
> not IID — carry returns are autocorrelated and IID sampling will overstate
> P(pass).
>
> Acceptance: `python3 scripts/ruin_engine.py --firm cti_1step` prints a
> frontier table where P(pass) rises then falls as size increases. If it is
> monotonic, the drawdown mechanics are wrong — find the bug before reporting.

Why this one first: it converts "what size?" from an opinion into a number, and
it retires `COLIN_V1.md`'s unsourced 92%.

## 2. The control that makes the number honest

> Extend `scripts/ruin_engine.py` with `--control random`. Same sizing, same
> contract, same costs, same holding period — but replace the signal with a
> coinflip having zero expectancy. Print the control's P(pass) directly beside
> the edge's P(pass) in every output, never separately. Add
> `--control shuffled` which keeps the real return magnitudes but shuffles their
> signs, destroying the edge while preserving the fat tails.
>
> Acceptance: no P(pass) figure can be emitted anywhere in this repo without its
> control printed on the same line. Add a test that fails if the reporting
> function is called without a control value.

If the edge's P(pass) is close to the coinflip's, then sizing is what passes
evaluations, not the edge — and that is a finding worth more than the edge.

## 3. Two sizing rules, derived separately

> `sovereign/risk/layers/prop.py` currently applies one ceiling. Split it into
> `eval_size()` and `funded_size()`. `eval_size()` takes its number from the
> ruin engine's frontier argmax for the specific contract in play.
> `funded_size()` solves a different problem: maximize expected payout given the
> firm's profit split, the payout schedule, and the fact that the account costs
> nothing to lose once funded — subject to the funded drawdown rule. Show the
> two numbers side by side in the daily verdict page. They should differ
> substantially; if they don't, one of the two objective functions is
> mis-specified.
>
> Acceptance: a test asserting `eval_size() != funded_size()` for the same
> contract and edge, with a comment explaining the economics of the gap.

## 4. Observation rate — the only fix for n=411

> The sample-size ceiling is set by bets per unit time, not by code. Audit every
> uncorrelated bet this repo could be taking and is not: pairs beyond the four,
> the swing carry lane running concurrently with the daytrade cockpit, and
> shorter-horizon variants of the same carry signal. For each candidate, report
> pairwise correlation to the existing v015 return series and the marginal
> effect on portfolio-level P(pass) using the ruin engine from #1 — a bet that
> raises trade count but correlates 0.9 adds nothing. Rank by marginal P(pass)
> gain per unit of added complexity. Recommend the top two only.
>
> Acceptance: a correlation matrix and a ranked table. No implementation in this
> pass — the output is a decision, not a diff.

## 5. Get the observation engine running tonight

> `data/trade_logs/paper_carry_trades.jsonl` has zero rows. Build the loop that
> fills it: on every gate evaluation, whether or not the gate passes, write a
> paper trade record with entry, size per the eval rule, the gate state that
> produced it, and a null outcome. Have the existing resolver close them against
> the tape on horizon. Schedule it. Then report, every morning in the verdict
> page, the running paper record: n, win rate, mean R, and current
> P(pass) implied by the live sample rather than the 2015–2024 backtest.
>
> Acceptance: the file has rows within 24 hours, and one deliberately corrupted
> record makes the resolver fail loudly rather than skip it.

This is the highest-value item in the repo and it is not a verification layer.
It is the thing that turns n=411 into n growing.

## 6. Drive the decision surface to zero

> Enumerate every point in the live path where the system requires a human
> decision, a human confirmation, or a human interpretation — including reading
> `daily_verdict.html` and deciding. For each, record what information the human
> supplies that the system does not have. Where the answer is "none," the
> decision is ceremonial and should be removed. Where the answer is real, name
> the data feed that would close it. Output a table ranked by frequency ×
> irreplaceability, and a single number: decisions required per week.
>
> Acceptance: the number is stated explicitly and tracked in `NEXT.md` weekly.
> Passive income is that number reaching zero, not a feeling about the system.

## 7. The freeze

> Add to `CLAUDE.md` under Non-negotiables: no new module, spec, or verification
> layer is built in this repo until the paper trade ledger has 50 closed records.
> Bug fixes to the live path are exempt. Data-integrity fixes that unblock trade
> generation are exempt. Everything else waits. Enforce with a pre-commit hook
> that counts rows in `paper_carry_trades.jsonl` and blocks new files under
> `daytrade/` and `specs/` below the threshold, with a documented override
> phrase for the exemptions.
>
> Acceptance: attempting to add a new spec file with fewer than 50 records is
> blocked, and the override works when invoked deliberately.

---

## Order

1, 2, 5 are the same evening's work and unlock everything else. 3 needs 1. 4
needs 1 and 2. 6 and 7 are cheap and permanent. 7 should arguably be first,
because it is the one that stops the bleeding.
