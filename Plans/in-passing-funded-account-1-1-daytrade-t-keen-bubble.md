# Fix the two CI failures blocking the science loop

## Context
The `science loop` workflow has failed every run since at least 2026-09-24 (last: run 36205722722): `2 failed, 863 passed, 5 skipped`. The pytest step gates the loop, so no science has run in CI.

1. `daytrade/test_datasource.py::test_forced_source_that_cannot_serve_raises`: `get_source("ES=F", prefer="alpaca")` builds `AlpacaSource()` with no args. In CI there's no `.env` and no env keys, so `__init__` (`daytrade/datasource.py:134`) raises "ALPACA_API_KEY / ALPACA_SECRET_KEY not found" before it reaches the `supports()` "forced" branch (`datasource.py:247`). The test passes locally only because the laptop's `.env` has real keys.
2. `daytrade/test_macro_calendar.py::test_real_repo_calendars_are_currently_fresh`: `data/daytrade/macro_calendar.json` has `generated_at` 2026-08-28, which is 29+ days against `FRED_STALE_DAYS = 14`. The FOMC file (verified 2026-08-28) is within its 150-day limit. Both limits stay as they are.

## Where the work happens: a clean worktree from `origin/main`
Local `main` is 3 commits ahead of origin (Colin's 09-02/09-03 research commits) and has unrelated dirty and staged files (operator records, ledger, `docs/data.json`). I won't touch any of that. Instead:
- `git fetch`, then `git worktree add <scratchpad>/ci-fix origin/main -b ci-fix`.
- The worktree has no `.env`, just like CI, so the Alpaca failure reproduces locally before the fix.
- Push `ci-fix` to `origin main` as a fast-forward. Colin's 3 local commits stay local and unpushed.

## Changes
1. **Test fix, test-level (not the workflow).** In `daytrade/test_datasource.py`, give `test_forced_source_that_cannot_serve_raises` a `monkeypatch` arg and set dummy keys with `monkeypatch.setenv("ALPACA_API_KEY", "k")` and `monkeypatch.setenv("ALPACA_SECRET_KEY", "s")`. This is the same pattern as `test_alpaca_refuses_non_equity_symbols` at line 48. Add a one-line comment saying the dummy keys get past credential validation so the forced-source branch is what gets tested. No real keys. Assertion unchanged.
   - I chose the test-level fix over a workflow env var because it keeps the test self-contained and behaving the same on every machine.
2. **Calendar refresh.** In the worktree, run `FRED_API_KEY=<value from main repo .env> python3 scripts/build_macro_calendar.py`. The value is exported inline and never printed. Commit the regenerated `data/daytrade/macro_calendar.json` only. The script fails loudly and writes atomically, so a partial file can't land.
3. One commit covering both files, pushed to `origin main`.

## Verification (pass/fail signals)
- **Before the fix, in the worktree:** `python3 -m pytest daytrade/test_datasource.py::test_forced_source_that_cannot_serve_raises` must FAIL with "Regex pattern did not match". This confirms the repro matches CI.
- **After the fix:** the same test passes. `python3 -c "from daytrade import macro_calendar as m; print(m.freshness())"` shows `stale: False` and `fred_age_days: 0`.
- **Full suite in the worktree (CI-equivalent, no .env):** `python3 -m pytest -q` shows 0 failed.
- **Science loop gate:** after the push, run `gh workflow run science.yml`, then `gh run watch` until it finishes. It must report `conclusion: success`, which means both the pytest step and `science_loop.py` exit 0 (instruments not failed, no integrity abort). I'll report the run ID and the summary tail.
- **Cleanup:** remove the worktree afterwards.
