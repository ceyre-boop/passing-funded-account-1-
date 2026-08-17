# STOCKFISH × ALPHAZERO Dashboard — static GitHub Pages from main

## Context

Colin wants a finished (not draft) single-page dashboard showing off exactly
what this session built — the spec-023 operator and spec-024 discipline layer
— deployed as GitHub Pages from main. Repo is already public
(`ceyre-boop/passing-funded-account-1-`), so Pages works directly. Refresh
decision (asked, answered): **auto-push once after each session close** via
the existing launchd tick lane.

## Deliverables

1. **`daytrade/build_dashboard_data.py`** (new, stdlib-only) — aggregates
   `records.jsonl`, `forecasts.jsonl`, `yield.jsonl`, latest `books-*.json`,
   `llm_spend.jsonl` into one `docs/data.json` (atomic write). Fail loud on
   corrupt lines (SystemExit naming file:line — I10 doctrine); absent files
   render `"available": false` honest empty states, never fake zeros. Static
   `DISCIPLINES` (7 cards w/ mechanism + invariant/mutation chips) and
   `VERIFICATION` (290 tests, 26/26 mutations, 7-gate map) constants live in
   the generator so the page stays dumb.

2. **`docs/index.html`** (new, fully self-contained: inline CSS/JS, hand-
   rolled SVG charts, zero CDNs) + `docs/.nojekyll`. Dark trading-terminal
   aesthetic: bg `#07090c`, panels `#0d1117`, phosphor green `#3fdc97` /
   amber `#e8b44a` / ice blue `#58a6ff`, monospace numerals with
   tabular-nums, 12-col grid collapsing at 760px, scanline hero texture,
   pulsing status dot, stroke-only glowing SVG. Model text rendered via
   `textContent` only (untrusted). Section order:
   - **Hero**: "PASSING A FUNDED ACCOUNT" / "STOCKFISH × ALPHAZERO —
     mechanics decide, meaning advises"; pulsing `● SHADOW SOAK` chip; stat
     strip `290 TESTS · 26/26 MUTATIONS KILLED · 0 DIRECTIVES EMITTED ·
     $x/$5.00` — containment is the hero metric.
   - **Latest judgment**: verdict badge + confidence bar; bull/base/bear
     triptych; invalidators; pre-registration band on a [-5,+5] axis bar;
     packet_as_of / max_data_ts proof line.
   - **Telemetry row**: verdict donut · spend meter + per-day bars ·
     forecasts open/resolved + prereg in-band dial.
   - **Four books table**: veto highlighted as BOOK OF RECORD; yield callout;
     fingerprint footnote.
   - **Yield sparkline** (honest dashed empty state until rows exist).
   - **Discipline layer**: 7 cards with invariant (I11–I22) and mutation
     (M17–M26) chips + status pills.
   - **Verification scoreboard**: 26-square mutation kill grid (M9/M26 amber
     outline + tooltip for the survived/false-kill stories); gates pipeline.
   - **Footer**: generated_at + honesty line ("shadow soak; no live fills;
     single-seat, certification owed") + repo link.

3. **`operator_tick.sh` post-close publish block** — inside the existing
   `set +e` region: at `ET_HM >= 1625`, if `.dashboard_published` stamp
   (in `data/daytrade/operator/`, never shipped) ≠ today, run the generator;
   on success stamp, `git add docs/data.json`, commit only if changed, push.
   At most one publish per ET day; a push failure echoes, never kills the
   resolver exit accounting.

4. **Pages enablement (one-time)**:
   `gh api -X POST repos/ceyre-boop/passing-funded-account-1-/pages -f "source[branch]=main" -f "source[path]=/docs"`
   (PUT if it already exists), then verify `.html_url`.

## data.json contract (abridged)

Keys: `generated_at`, `session` (mode/shadow/last_record_ts/records_total),
`verdicts` (counts, abstain_reasons, series), `latest_judgment` (both_sides,
invalidators, pre_registration, abstention, emission_refused, packet proof),
`spend` (cap 5.0, total, by_day), `forecasts` (open/resolved/prereg rates),
`yield_curve`, `four_books` (roles, yield_delta_r), `disciplines[7]`,
`verification` (tests/mutations/gates), `containment`
(directives_emitted — expected 0 in soak; non-zero renders as an alert).

## Verification

1. Run generator against real data; inspect `docs/data.json`.
2. `python3 -m http.server -d docs` + Interceptor screenshot — verify hero,
   charts, honest empty states, and the containment stat reading 0.
3. Full pytest suite still green (generator has no operator imports).
4. Simulate the tick publish guard (stamp logic) without pushing; then real
   commit + push + Pages enablement + `curl` the live URL until it serves.
