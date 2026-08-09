# 010 — GOOGLE SHEETS BRIDGE  `daytrade/sheets_push.py`   `[SPEC]`
Colin deployed an Apps Script web app on 2026-08-09 as a write endpoint into the
Jarvis tracker sheet. This spec says what it should carry — and what it must
never carry.

## What it's good for
A place to *look at* measurements that already exist. The repo's data lives in
CSVs that are fine for code and terrible for eyeballing over coffee. A sheet is
a real improvement for: the shot ledger, the regime scorecard, the news
scorecard, streak/campaign state, and the ceiling numbers.

## ⛔ What it must NOT carry — and why this matters more than the feature
The pasted blueprint pushes a **top-K-by-Sharpe leaderboard** from a
million-strategy sweep. **Do not build that.** It is the single most efficient
multiple-testing machine it is possible to construct, and this repo exists
because that exact mistake already cost us once (SANITY_AUDIT.md).

Run a million strategies, sort by Sharpe, surface the top 100: those 100 are
selected *because* they had the best noise. A 2.85 Sharpe out of a million tries
is what pure randomness produces routinely — the expected max Sharpe of a million
zero-edge strategies is large, and nothing about the leaderboard tells you which
side of that you're on. Feeding those rows to an LLM for an "executive breakdown"
then produces confident prose about noise, which is worse than the raw number
because it *reads* like analysis.

If a strategy sweep ever gets surfaced, it must arrive with, on the same row:
- the **number of configurations tried** (K),
- the **zero-edge control** score from the identical sweep on mean-centered data,
- the **out-of-sample** result on data the sweep never touched,
- and the **selection rule**, pre-registered, in writing.

A row without all four does not go in the sheet. That is not a style preference;
it is spec/README rule 3 applied to the one place it's easiest to violate.

## What v1 should actually push
```python
def push(rows: list[dict], kind: str) -> dict:
    """POST to the Apps Script endpoint. kind in {shot, regime_score, news_score,
    streak, ceiling}. Endpoint URL + shared secret from .env, never hardcoded."""
```
Priority order, matching what already exists or is next:
1. **shot ledger** rows — every trade and every no-trade, as they happen.
2. **news scorecard** rows — Claude's bias calls, graded, with the three baselines
   alongside (always-flat, yesterday's direction, coin flip).
3. **regime scorecard** — once spec 004 exists. Per-regime lift leading, overall
   accuracy as decoration.
4. **streak/campaign state** — one row per session close.
5. **ceiling numbers** — one row, re-pushed only when the measurement is re-run.

Push is **one-way and append-only**. The sheet is a display surface, never an
input. Nothing in `daytrade/` may ever read from it — a spreadsheet cell someone
edited by hand is not a measurement, and a system that reads its own dashboard
back in is a system that can be fooled by its own display.

## Security — the deployment as configured is an open write endpoint
`Who has access: Anyone` + `Execute as: Me` means **anyone holding the URL can
append arbitrary rows to the sheet**, unauthenticated. The URL has been pasted
into a chat transcript.

Minimum fix before anything real gets pushed:
1. Add a shared secret to `doPost` and reject any payload without it:
   ```javascript
   const SECRET = PropertiesService.getScriptProperties().getProperty('INGEST_SECRET');
   if (!data.secret || data.secret !== SECRET) {
     return ContentService.createTextOutput(JSON.stringify({status:"denied"}))
                          .setMimeType(ContentService.MimeType.JSON);
   }
   ```
   Set `INGEST_SECRET` under Project Settings → Script Properties (not in code).
2. **Redeploy as a new version** — the pasted deployment ID should be treated as
   burned. New deployment, new URL, secret required.
3. Store URL + secret in `.env` (gitignored). Never in a spec, never in a commit,
   never in a chat.
4. Validate on arrival: reject rows whose shape doesn't match `kind`. An endpoint
   that appends whatever it's handed will eventually be handed garbage.

Worst case if skipped is low — it's a spreadsheet, not an account — but the habit
is the point, and the same habit protects the Alpaca and Anthropic keys.

## Still not unblocked
This bridge is **not** the ALPHAZERO news brain. `=CLAUDE(...)` in a sheet is the
Claude for Sheets add-on running interactively; spec 009 needs
`ANTHROPIC_API_KEY` in `.env` so `news_claude.py` can call the API from a
5-minute loop with no human present. Different mechanism, still the blocking item
in MANUAL_STEPS.md.

## `[SKETCH]` — later
- **Sheet-side charts** off the pushed scorecards (calibration curve, per-regime
  lift over time). Nice, and it's the natural home for them — but per APEX #3,
  after the measurements are real, not before.
