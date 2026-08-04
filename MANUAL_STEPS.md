# MANUAL_STEPS.md — the things only Colin can do
Updated 2026-08-04. Code can't do these. Every one of them either blocks a build
item or is a security hole that's been open too long. Ordered by what unblocks
the most.

---

## 🔴 BLOCKING — spec 009 (Claude as news brain) cannot be built without this
### 1. Anthropic API key for `news_claude.py`
Nobody has flagged this yet and it is the single dependency standing between the
plan and a real ALPHAZERO brain. Spec 009 has Claude reading NVDA news every
5 minutes on your machine — that is API calls from a script, not this chat.

- Get a key: console.anthropic.com → API Keys → create one, name it `alta-newsbrain`
- Add to the repo's `.env` (already gitignored, mode 600):
  ```
  ANTHROPIC_API_KEY=sk-ant-...
  ```
- Set a **spend limit** on the key while you're in there. The 5-minute loop is
  cheap by design (only fires when the headline set actually changes) but a bug
  in the loop shouldn't be able to run up a bill.
- Cost estimate to sanity-check against: one morning read + ~5-10 delta checks
  per session. Pennies a day, not dollars.

**Until this exists, ALPHAZERO's brain stays the keyword-valence placeholder** —
which is the thing already labeled unvalidated and which produced yesterday's
graded-FALSE call. This is the highest-leverage manual step on the list.

---

## 🔴 SECURITY — open since 2026-08-03, still open
### 2. Alpaca account password + MFA
The account password is in the local Claude Code transcript. Rotating the API
keys did not address that. Change the password at the Alpaca dashboard, turn on
MFA. The account ID also appeared in chat — treat it as semi-public.

### 3. GitHub PAT rotation
The token pasted into the Cowork session on 2026-08-02 has been used for every
push since. It's scoped to one repo and expires in ~30 days, but it has been
sitting in a chat transcript all week. Revoke it, issue a fresh one, and this
time put it in the local git credential helper instead of pasting it anywhere.
- github.com → Settings → Developer settings → Personal access tokens → revoke
- New token, same single-repo scope
- `git remote set-url origin https://github.com/ceyre-boop/passing-funded-account-1-.git`
  then let the credential helper store it on first push

**Do not paste the new one into any chat, including this one.** If a cloud
session needs to push, it can hand you the commit instead.

---

## 🟡 UNBLOCKS DATA COLLECTION — every day this waits is data that never comes back
### 4. launchd job for the ALPHAZERO loop
Spec 009's fix for the manual-start gap. The loop must run open-to-close whether
or not you remember, because it's building the scorecard.

```bash
# ~/Library/LaunchAgents/com.alta.alphazero.plist
# StartCalendarInterval: weekdays 09:25 ET
# ProgramArguments: python3 <repo>/daytrade/alphazero_bias.py --loop 300 --until 16:05
launchctl load ~/Library/LaunchAgents/com.alta.alphazero.plist
launchctl list | grep alta          # verify it's registered
```
Claude Code can write the plist — you have to `launchctl load` it and confirm it
fires tomorrow at 09:25. Check `data/daytrade/alphazero_daemon.log` after.

Caveat worth knowing: **don't load this while a position is open** until the news
brain is real. The v1 keyword table would flatten a live trade on a headline
containing "probe" or "halt". Either finish step 1 first, or set the loop to
write `urgency: none` unconditionally until it does.

### 5. The 2-bust cooloff calendar block
Still not on the calendar. The friction model showed the whole ladder rests on
this rule holding, and the point is that it's decided by calm-you in advance, not
by 2am-you after two reds. Make it a real recurring-capable event with the rule
written in the notes: *"if two consecutive busts are logged, cooloff starts that
day, 5 trading days, no exceptions."*

---

## 🟢 WHEN YOU GET THERE — not blocking anything today
### 6. The funded account itself
Still unbought as far as this repo knows. When you do: confirm BOGO at checkout
(that's the 52-70% vs 77-91% campaign-odds difference), confirm trailing vs
static drawdown, confirm minimum trading days before first payout, and screenshot
the confirmation into the repo.

### 7. Tradeify-sized plan.json alongside the Alpaca one
Alpaca paper is $103k. A real Select eval is $25k with a $1,000 trailing floor.
Same dollar goal, very different fraction of cushion. Write the $25k-sized
version so the position size you get used to seeing is the one that'll be real.

---

## Today's data, logged
Ledger row 2 is in: **setup appeared, shot not taken.** Recorded honestly as a
hesitation, not a discipline pass — the distinction matters because the
trigger-rate denominator and the "did I follow my own rules" question are
different measurements and blurring them makes both useless.

Today's tape is also a **hand-labeled ground-truth example for spec 001**: NVDA
printed the manipulation pattern (sweep, reclaim, FVG retrace, R:R 2.5 toward
219) in the 10:30-11:30 MORNING block — exactly where the time priors say
manipulation is most likely. Saved to `data/regime_examples/` so the classifier
can be checked against a day you read live and called correctly.
