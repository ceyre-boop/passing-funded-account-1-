# ONE_DAY_PASS — the honest math and the actual loophole
2026-08-03, 05:00 ET · for the Instant Pro $10k (3% daily / 6% max / payout on demand) · rule #1: I_AM_A_GOOD_TRADER.md — the plan assumes the trader takes the shot; the system's job is structure, timing, discipline.

## The second-grader version

To pass in one day you need to make 6% without ever being down 3%. That means one trade: bet your whole daily allowance (3%) on something that pays double (2:1). If it wins, you're at +6%. If it loses, the day is over.

A bet that pays double is won about **1 time in 3** by luck. A really good trader on a really good day might win it **4.5 times in 10**. Nobody honest wins it more than half the time — that would make them the best trader alive, every day.

So: **"more likely than not to pass in ONE day" — no. Not you, not anyone, and no statistics can bless a single day anyway (significance needs many tries; one day is one coin flip).**

**But here is what the dumb humans miss.** The account costs almost nothing and you can buy another one. The prize (a funded account paying real money) is worth 50-100× the fee. You don't need to win the flip. You need to be allowed to keep flipping. Nobody flips a fair coin 5 times without getting at least one head very often — that chance is 87%.

| attempts | zero edge (31%/shot) | modest edge (40%) | top-tier (45%) |
|---|---|---|---|
| 1 day | 31% | 40% | 45% |
| 2 days | 52% | 64% | 70% |
| 3 days | 67% | 78% | 83% |
| 5 days | 85% | 92% | 95% |

**Crossing "more likely than not" happens on attempt #2 — even with zero edge.** The BOGO promo literally hands you attempt #2 free. Edge doesn't decide IF you get funded; it decides how many fees you burn first. This is the same structural truth SANITY_AUDIT.md proved for 30-day evals — free retries are the engine, the strategy is the fuel efficiency.

Chart: `dashboard/one_shot_ladder.png`.

## Ruled out, one by one (asked and answered)

- **">50% in one day, statistically certified"** — impossible to certify. n=1 has no p-value. Ruled out by arithmetic, not by pessimism.
- **Finding "the one edge in tomorrow's markets" by backtest tonight** — this sandbox has daily bars only. One-day tactics live inside the day (intraday paths); EVAL_LAB already showed daily-bar technicals are luck-indistinguishable, and FIRM_FIT's event study showed daily-bar news-surprise trading has no significant edge. Any intraday "edge" I claimed to have found tonight would be the exact curve-fitting the audit banned. **The edge for a one-day shot is the trader's read. That's rule #1, and it's also just the truth.**
- **Grinding many small trades in one day** — worse, not better. Each extra trade after a win risks giving back the +6%; each after a loss is revenge-trading into a dead day. One shot maximizes p per attempt. Quality not quantity is mathematically right here.

## The playbook (today: Monday 2026-08-03)

**The week:** ISM Manufacturing 10:00 ET today (exp 54.0 vs 53.3 prior; prices paid exp 70.0 vs 73.0). JOLTS Tue 10:00. ADP Wed 8:15, ISM Services Wed 10:00. **Jobs report Friday 8:30 — the week's biggest scheduled volatility.** Calendar note: "Alta — First live trade" is already scheduled Tue 10:00 with the 5-gate protocol.

**Per attempt, hard rules:**
1. Buy the account before the open. Confirm at checkout: minimum trading days before first payout (the classic instant-account gotcha — if it's 3-5 days, the mission becomes "hit +6% day one, sit flat the minimum days, then payout"), consistency rule, max-profit-per-day cap, trailing vs static on the 6%.
2. No trade 9:30–9:55. The open is other people's chaos.
3. The day's one shot comes off the scheduled catalyst (today: 10:00 ISM) or a setup the trader rates top-decile. Structure: stop = 2.8% of account (buffer under the 3% limit for spread/slip), target = +6% total, i.e. ~2.1:1.
4. One trade. Stop hit → platform closed, done, next attempt another day. Target hit → request payout / stop trading. No third outcome.
5. No setup by ~11:30 → no trade. An unused day costs nothing; a forced trade costs an attempt.
6. Log the shot in the journal either way (decision_logger discipline unchanged).

**Budget the ladder before attempt #1:** decide now it's (say) 4 fees max. That's the whole campaign risk. Expected fees to funded: zero-edge ≈ 3.2 × fee; at 40% ≈ 2.5 × fee; BOGO halves it.

## What this file is not
Not a claim of edge. Not a probability blessed by backtest. The 31% column is the only number here that's structural fact; the 40/45% columns are assumptions about the trader, and they only get proven one journaled shot at a time.
