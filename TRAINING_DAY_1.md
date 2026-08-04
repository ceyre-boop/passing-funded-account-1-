# TRAINING DAY 1 — Tue 2026-08-04, Alpaca paper, NVDA only
Written the night before, per doctrine. One shot. Ledger row two.

**Goal of the day is not P&L. It is one honest row in the ledger and one
end-to-end run of the whole chain under live conditions.** The system has been
proven in replay; it has never been driven by a real morning.

---

## Before the open

```bash
cd ~/passing-funded-account-1- && git pull
python3 daytrade/broker.py --symbol NVDA        # connectivity, read-only, places nothing
```
Expect: account `PA3SIVO6WURP`, status ACTIVE, NVDA flat, no working stop.

Sanity-check the policy block you committed last night:
```bash
cat data/daytrade/policy_today.json
```

### Levels that matter, from the real cache
| | |
|---|---|
| Mon 8/3 close | **206.69** (open 198.50, high 208.74, low 198.09) |
| Mon opening range | 198.09 – 202.73 |
| Fri 7/31 close | 200.81 — **PDH 201.97 / PDL 194.95**, tomorrow's lower pools |
| median 5m bar range | **$0.49** — this is what sets your stop distance |

Monday broke clean through 202.70 and closed near the highs. Tomorrow is a
**pre-catalyst day: AMD reports Tuesday after the close.** The reaction is
Wednesday, not tomorrow.

---

## The rule (unchanged, THE_SHOT adapted to NVDA equity)

1. **09:30–10:00** — opening range forms. Mark high and low. **No orders.**
2. **Trigger** — first 5-minute bar that CLOSES outside the OR, before 11:00.
   Close above OR-high = long, below OR-low = short. **Direction comes from the
   break, never from an opinion about AMD.**
3. **Entry** — market, on that bar's close, in Alpaca paper.
4. **Stop** — far side of the trigger bar.
5. **No break by 11:00 → no shot. That is a complete, successful day.**

## Sizing — read this, the geometry changed

With a $0.49 median 5m range, the stop is naturally tight — roughly $0.50/share.
At 2.14R that means the $300 day goal needs about **250–300 shares**, not 100.
Pick qty so `risk_per_share × qty` is the dollar risk you actually accept, then
check the goal it implies. `newplan.py` prints both. If they disagree with your
intent, fix it before starting the runner.

**Survival math is yours to do in your head tomorrow** — `survival.py` is a stub.
Say the sentence out loud before entering: *"if this loses I'm down $X, and I'm
still fine because Y."* If you can't finish that sentence, don't take the trade.

---

## At the break

```bash
python3 daytrade/newplan.py --entry <fill> --stop <far side> --qty <shares> --direction long|short
```
It prints the full ladder back. **Read the echo.** A transposed digit in the stop
is a real trade with a real wrong risk.

Then:
```bash
python3 daytrade/runner.py --broker armed --interval 15
```
It confirms once, interactively, showing account equity and the first order. Type
`send`. From there hands off the keyboard — that is the entire point.

---

## Why these exit params tomorrow

`policy_today.json` sets `be_arm_frac 0.25`, `trail_mult null`, `flatten_at_et 11:00`.

That is not a guess. Monday's ceiling measurement found the oracle reached for
**early breakeven and a hard flatten, and essentially never for trailing** — 17
of its 24 picks were exactly this shape, and it is not one of the three shipped
policies. It is also your own doctrine.

**Honest label: n=24 entries, 3 winners. This is a hypothesis, not a validated
edge.** Tomorrow tests it live at n=1. One row proves nothing either way; it
starts the count.

---

## The urgent-channel drill (do it once, deliberately)

**Do not start `alphazero_bias.py --loop` while a position is open.** The v1
brain is an unvalidated keyword table, and a stray headline containing "probe" or
"halt" would flatten a live position on no evidence. `bias.json` is hand-set to
`urgency: none` for tomorrow.

Mid-session, with the runner going, drill it under control:
```bash
python3 -c "import json,pathlib; p=pathlib.Path('data/daytrade/bias.json'); d=json.loads(p.read_text()); d['urgency']='tighten'; p.write_text(json.dumps(d,indent=1))"
# watch the next runner line: trail distance halves, reason says 'tightened'
# then put it back:
python3 -c "import json,pathlib; p=pathlib.Path('data/daytrade/bias.json'); d=json.loads(p.read_text()); d['urgency']='none'; p.write_text(json.dumps(d,indent=1))"
```
Only escalate to `urgency: exit` if you actually want to be flat. It flattens
immediately and outranks everything.

---

## After the close

1. **Log ledger row two** in `data/shot_ledger.csv` — win, loss, or no-shot.
   A no-shot day still gets a row; the denominator matters as much as the wins.
2. Keep the session log: `data/daytrade/session_2026-08-04.jsonl`.
3. Run the DoD diff on the real session — the first time it runs on live data
   rather than a replay:
   ```bash
   python3 daytrade/backtest.py --replay-session data/daytrade/session_2026-08-04.jsonl
   ```
   **Empty output is the pass.** A non-empty diff means the runner decided
   something the engine did not, and that is a real architecture bug found by a
   real session — exactly what this day is for.
4. Grade the news call in `data/daytrade/news_scorecard.csv`. Row 1 is already
   there: Monday's "quiet consolidation below $202" call, graded FALSE against a
   +4.1% day. No exemption for being an LLM.
5. **Check AMD's print after the close.** That sets Wednesday.

---

## What is deliberately not working tomorrow, so it is not a surprise

| stub | consequence tomorrow |
|---|---|
| `regime.py` | no automatic exit policy — you pick it, by hand, before the open |
| `survival.py` | the "if this loses, where am I" math is yours, in your head |
| `streak.py` | cooloff is not enforced by code — you enforce it |
| `scorecard.py` | regime calls are not being collected yet |
| `news_claude.py` | bias.json is hand-written, not generated |

Every one of those raises on import with its contract in the docstring. Nothing
on tomorrow's critical path is a stub.

---

## The one thing worth remembering

Monday's real finding was not the prize number. It was **3 of 24**: on 21 of 24
days, no exit policy — perfect, with hindsight, across 396 configurations — could
make money, because the entry gave it nothing to work with.

The entry is the binding constraint, and the entry is yours. That is the thing
with the least data on it and the most leverage in it. Which is why tomorrow is
for trading, not building.
