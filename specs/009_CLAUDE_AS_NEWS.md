# 009 — CLAUDE AS THE NEWS BRAIN  `daytrade/news_claude.py`   `[SPEC]`
Replaces ALPHAZERO's keyword-valence placeholder with an actual LLM reading the
tape's context. Colin's call, and it is the cheapest path to a real bias signal.

## The two clocks, stated plainly (Colin's framing, correct)
- **STOCKFISH runs when in a trade.** It exists to answer "given current state,
  how do we exit." No position, no job. Manual start is CORRECT here — it is
  gated on Colin taking a trade, which is a human act by design.
- **ALPHAZERO runs every 5 minutes, open to close, position or not.** It is
  building the read and the scorecard data. This one must NOT depend on Colin
  remembering — see the launchd section.

## What Claude produces

### Morning (once, ~08:45 ET) — the daily brief and bias
```python
def morning_read(symbol="NVDA") -> DailyBias:
    """One call. Input: overnight headlines, prior close/range, today's calendar,
    analyst consensus for the quarter, prior-day regime summary.
    Output: structured, never prose."""
```
```jsonc
{
  "date": "2026-08-04", "symbol": "NVDA",
  "bias": 0.35,                   // -1..+1 what shareholders appear to think
  "conviction": 0.6,              // how strongly the evidence points
  "thesis": "one sentence Colin reads in 5 seconds",
  "analyst_consensus_eps": 1.24,  // what the street expects
  "claude_projected_eps": 1.31,   // Claude's own read — the growth/decline signal
  "key_levels_in_the_news": [...],// prices the narrative is anchored on
  "watch_for": ["...", "..."],    // what would CHANGE this read
  "model": "claude-<version>", "prompt_version": "news-v1"
}
```
`watch_for` is the important field — it is what the 5-minute loop checks against.

### Every 5 minutes — the delta check
```python
def delta_check(morning: DailyBias, new_headlines) -> Delta:
    """Cheap and boring by design. Almost always: nothing changed.
    Only escalates when something in watch_for actually happened."""
```
```jsonc
{ "changed": false, "urgency": "none", "why": "no new material headlines" }
// or
{ "changed": true,  "urgency": "tighten", "bias_delta": -0.4,
  "why": "export-restriction headline hits the China revenue thesis directly" }
```
Exactly Colin's loop: *did anything change? No → stay. No → stay. Yes, big →
this changes how we exit.*

**Cost/latency discipline:** the 5-minute call only runs if the headline set
actually changed since the last check (hash the set). An unchanged world costs
nothing. Budget: one morning call plus a handful of delta calls per session.

## Precedence (unchanged)
`urgency` still outranks `exit_policy`. A news shock flattens regardless of how
good the regime looks. A stale news read (older than N minutes) degrades to
`urgency: "none"` loudly — it must never keep steering an open trade on old
information.

## THE RULE THAT MAKES THIS HONEST
**Claude's bias calls get scored exactly like the classifier's.** No exemption
for being an LLM. Every `morning_read` gets graded at session close: did the day
resolve in the direction of the bias, and was `conviction` calibrated? Append to
`data/news_scorecard.csv`, same discipline as spec 004 — with baselines:
- **always-flat** (bias 0 every day)
- **yesterday's direction** (naive momentum)
- **coin flip**

If Claude's bias can't beat those over ~40 sessions, it is decoration and the
system says so out loud. An LLM producing a confident-sounding paragraph is the
single easiest way to smuggle an unvalidated edge into this repo, and this repo
exists because that already happened once.

**Also required:** `prompt_version` on every record. A prompt change is a rule
change — it commits BEFORE the next session (APEX #5) and it resets the scorecard
lineage. Silently editing the prompt after a bad day is exactly the same sin as
retuning rules after a loss.

## The launchd gap — the fix Colin identified
Spec 006 assumed Colin starts the loop. He is right that some manual steps are
irreducible (keys, connections, taking the trade) — but the ALPHAZERO loop is not
one of them, and every session it silently doesn't run is scorecard data that
never comes back.

```
~/Library/LaunchAgents/com.alta.alphazero.plist
  RunAtLoad: false
  StartCalendarInterval: weekdays 09:25 ET  -> `alphazero_bias.py --loop 300 --until 16:05`
  StandardOutPath / StandardErrorPath -> data/daytrade/alphazero_daemon.log
```
Plus a **heartbeat**: the loop writes `last_run` into bias.json every cycle. If
the morning brief sees a stale heartbeat from the previous session, it says so at
the top in plain language — a silently dead collector is worse than no collector,
because the gap is invisible until you go looking for data that isn't there.

## `[SKETCH]` — not yet
- **Claude reading the CHART.** Vision on a 5m screenshot, describing structure
  in doctrine terms. Plausible, genuinely interesting, and completely unvalidated
  — it also risks the classifier and the LLM agreeing because they are looking at
  the same picture, which would read as confirmation while being correlation.
  Needs its own spec and its own scorecard lineage.
- **Feeding the news scorecard back into prompt selection automatically.** Same
  gate as every other learning loop: pre-registered protocol, months of samples,
  both baselines. Humans change prompts by hand, in commits, until then.
