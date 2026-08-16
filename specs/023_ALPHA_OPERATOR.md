# 023 — ALPHA OPERATOR `[SPEC]`

**Component:** `daytrade/alpha_operator.py` (operator + resolver CLI),
`daytrade/four_books.py` (comparison harness)
**Status:** `[SPEC]` — written 2026-08-15, before the code, per Ruling 1 and the
022 lesson (no more retroactive specs).
**Depends on:** 015 (Evidence), 016 (directives/authority), 017 (Forecast),
018 (ContextDirective contract), 009 (news_claude call/budget machinery),
022 (execution ledger, `mode="sim"`).
**Closes:** Gate 5's producer half and Gate 6 (a forecast producer exists).

---

## What it is

The autonomous AlphaZero research desk. Today the repo has the complete
firewall — bounded directives, authority registry, forecast promotion gates,
a runner directive channel that is WIRED — and **no producer on the other
side of it**. `news_claude.py` is a manually-invoked CLI; `directives.json`
is only ever written by tests; EvidenceStore and ForecastLedger are verified
modules with zero callers.

`alpha_operator.py` is that producer. It runs on deterministic triggers,
builds an evidence packet, asks Claude for a structured judgment, and seals
one typed record per run — forecast recorded **before** price resolution,
evidence persisted, and (when warranted) one bounded ContextDirective written
to `data/daytrade/directives.json` for the runner's existing channel.

`four_books.py` answers the only question that matters about Claude
discretion: does it add edge over the stable baseline, or just variance?
Four parallel books over identical data, all `mode="sim"`.

## Rulings restated (binding)

1. AlphaZero communicates meaning; Stockfish controls mechanics. The operator
   NEVER places orders, computes quantities, or moves stops. Its only output
   channels are: sealed records, forecasts, evidence, and `ContextDirective`
   payloads that the 018 envelope already refuses to widen.
2. The operator is UNPROMOTED. It may express TIGHTEN / REDUCE_RISK
   (authority 1). An EXIT judgment is recorded verbatim in the sealed record
   and the forecast, but the emitted directive is capped at TIGHTEN — the
   exact `--allow-urgency` discipline from spec 009. INVALIDATE/EMERGENCY
   authority is earned through the 017 promotion gate, never configured.
3. Fail loud. Missing POLYGON_API_KEY disables the news trigger with a
   printed reason — never a silent empty headline list. Malformed model
   output raises; nothing defaults to zero.
4. Every claim is sealed before its outcome. `Forecast.as_of` is stamped at
   call time; `resolve` refuses anything before `as_of + horizon_min`
   (enforced by 017's ledger, reused as-is).
5. Spend is capped by the 009 ledger (`llm_spend.jsonl`, shared cap). The
   operator adds calls to the same ledger; it does not get its own budget.

## Triggers (deterministic — Claude runs only when something changed)

| trigger | condition | detection |
|---|---|---|
| `premarket` | first run of the ET day before 09:30 | date stamp in operator state |
| `bar` | a new completed 5m bar exists | last bar ts vs state (bars.py, yfinance, no key) |
| `level` | last close crossed a plan level (entry/sl/tp1/tp2) | sign change vs previous close |
| `news` | headline fingerprint changed | 009's `headline_fingerprint` (needs POLYGON_API_KEY) |
| `position` | runner event stream grew | event file line count vs state |

`--once` evaluates all triggers and runs at most one Claude call (highest
priority: position > news > level > bar > premarket). No trigger fired →
zero API calls, zero cost, state untouched. `--force <trigger>` overrides.

## The sealed record (one per Claude run, append-only JSONL)

`data/daytrade/operator/records.jsonl` — written in full BEFORE any
directive is emitted:

```json
{
  "record_id": "op-YYYYMMDD-HHMMSS-<symbol>",
  "ts": "ISO tz-aware", "trigger": "bar", "symbol": "NVDA",
  "model_version": "alpha-operator-v1/<model>", "prompt_version": "op-1",
  "forecast_id": "...", "trade_id": null,
  "evidence_ids": ["..."],
  "both_sides": {"bull": "...", "base": "...", "bear": "..."},
  "invalidators": ["..."],
  "verdict": "ABSTAIN | ALLOW_BASELINE | TIGHTEN | EXIT",
  "confidence": 0.0, "expires_at": "ISO",
  "suppressed_to": "TIGHTEN | null",
  "directive": { ...ContextDirective.to_dict()... } | null,
  "abstention": { ...Abstention.to_dict()... } | null,
  "cost_usd": 0.0
}
```

Verdict → action mapping (total, no other paths):

| verdict | forecast | directive emitted | note |
|---|---|---|---|
| ABSTAIN | yes (scenario probs still recorded) | none | `Abstention` with a valid 016 reason |
| ALLOW_BASELINE | yes | none | baseline trades untouched |
| TIGHTEN | yes | interrupt=TIGHTEN, authority 1 | |
| EXIT | yes | interrupt=TIGHTEN, authority 1 | suggestion sealed; `suppressed_to` says so |

## Persistence the operator owns (015/017 stores are in-memory by design)

- `data/daytrade/operator/evidence.jsonl` — Evidence.to_dict rows +
  state-advance rows; store rebuilt by replay on load.
- `data/daytrade/operator/forecasts.jsonl` — Forecast.to_dict rows and
  Resolution rows (`{"kind": "forecast"|"resolution", ...}`); ledger rebuilt
  by replay. Both append-only; a corrupt line raises, never skips.
- `data/daytrade/operator/state.json` — trigger dedup state only
  (fingerprints, last bar ts, last event count, last premarket date).
  Losing it causes at most one redundant call, never a wrong record.
- `data/daytrade/directives.json` — the runner's existing input. Written
  atomically (tmp+rename) as a JSON list; every payload round-trips through
  `ContextDirective.from_dict(to_dict())` before the write, so a malformed
  directive cannot reach the file.

## Resolver (the missing learn loop)

`alpha_operator.py resolve` — for every open forecast whose horizon has
passed: compute outcome from bars over `[as_of, as_of + horizon_min]`.
Deterministic mapping, documented in code: direction from net move vs a
±0.15% flat band; `outcome_scenario` from direction + range expansion;
`shock_occurred` when true range over the window exceeds 3× the prior
20-bar median; `was_stale` when the newest bar at call time was older than
15 min. Missing bars for the window → that forecast stays open with a
printed reason (fail loud, resolve nothing partial).

## Four books (`four_books.py`)

Replay one session of 5m bars with the baseline plan's setups. All books see
identical data and identical Stockfish exit mechanics; only entry permission
differs:

| book | rule |
|---|---|
| `baseline` | every baseline setup taken |
| `veto` | setup skipped iff an operator record (verdict TIGHTEN/EXIT, unexpired, in scope) predates entry |
| `select` | only setups an ALLOW_BASELINE record explicitly covers |
| `full` | operator ENTER proposals allowed (v1: same as veto until an entry-proposal schema is ratified — full book is measured, not built, tonight) |

Output: per-book trade list with R multiples (022's `realized_r` math),
summary table, and a JSON artifact `data/daytrade/operator/books-<date>.json`.
Books never write to any ledger mode other than `sim`, and v1 does not write
to the execution ledger at all — it is a measurement harness.

## Invariants (each gets a named test)

- I1: a directive with interrupt above TIGHTEN/REDUCE_RISK or authority > 1
  can never appear in `directives.json` (operator caps + runner refuses).
- I2: every emitted directive round-trips `from_dict(to_dict())` unchanged.
- I3: a sealed record exists on disk before its directive is observable.
- I4: `resolve` never resolves before `as_of + horizon_min` (017 enforced).
- I5: an unchanged world (no trigger) makes zero API calls.
- I6: ABSTAIN emits no directive and carries a valid 016 abstention reason.
- I7: forecasts.jsonl replay reconstructs the ledger byte-equal in grade().
- I8: four books consume identical bar data (asserted by fingerprint).
- I9: EXIT verdicts are sealed verbatim while the emitted directive is
  TIGHTEN with `suppressed_to` set.
- I10: a corrupt persistence line raises on load; nothing is skipped.
