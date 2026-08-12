# Plan — Spec 021 trustworthy, start the real clock (1 week)

## Context

The carry buy gate (spec 021) is the thing standing between this repo and buying
a real prop-firm evaluation. `daily_verdict.html` currently reads 4/5 green, 1
red — the 5th gate (G5, the 80-trade paper sprint) hasn't started, and every
calendar day it hasn't started adds a day to the timeline regardless of what
else gets built this week. Colin verified two real defects against the live
CTI rules page and the codebase this session: the CTI 1-Step contract encodes
the wrong drawdown type (`static` when the real rule is `trailing`), and the
OOS artifact (`data/oos_trades_2025_2026.json`) has no committed writer script
in this repo — a provenance gap the code already flags on its own restore
list. Plus: every other implemented card in this repo (012 through 019, the
Gate 2 wiring) has a committed, independently-rerunnable mutation driver;
spec 021's `021_MUTATION_LOG.md` is real, caught-fault evidence but was
produced by an inline/ad-hoc process, not a script anyone else can rerun.
`paper_carry_log.py`'s `--risk` argument is currently a label with nothing to
validate it against. And a Python 3.12 f-string syntax leaked into
`daytrade/splits.py`, breaking the test suite import chain on the machine's
actual Python 3.10/3.11.

The week's discipline, per Colin's framing: close these five real defects,
then open trade #1 of the paper sprint — nothing else. No cockpit feature
work until spec 021 is bought or honestly blocked.

## Non-negotiables carried over from CLAUDE.md

- Every decision that changes a locked/pre-registered value goes through
  `sovereign/intelligence/decision_logger.py` (spec 021 P1's own rule).
- No silent zero-defaults; fail loud on unreconciled inputs (non-negotiable 2).
- This is carry-eval only — no new hypotheses, no ICT-lane work.

## Work items, in order

### 1. Fix the CTI 1-Step contract model (G4)

**File:** `data/propfirm/firm_contracts.yaml`, `cti_1step` block (lines 7–22).

Change `max_dd: {type: static, pct: 0.05, basis: balance, mark: close}` →
`type: trailing` (pct/basis/mark unchanged — only the type flips, per Colin's
verification against CTI's live rules page). Update `rules_asof: "2026-08-10"`
→ today's date on that one entry only (the other two firms' entries are
untouched — don't bulk-edit the shared value).

Existing test `sovereign/propfirm/test_firm_contracts.py::test_static_vs_trailing_floor_moves`
already exercises the static/trailing distinction generically — rerun the
full 9-test suite after the edit; no test changes should be needed since this
is a data fix, not a logic fix. Then rerun
`python3 scripts/carry_buy_gate.py --series sealed --update-state` and read
G4's actual output — a false green is not acceptable, whatever the real
answer is, is progress.

**Verification:** `pytest sovereign/propfirm/test_firm_contracts.py -q` green;
`carry_buy_gate.py` G4 output shows `violations: []` (or documents exactly
what's left, if the corrected trailing model surfaces new ones).

### 2. Resolve the OOS artifact provenance gap (G3/G2)

**Files:** `scripts/diagnose_repro_gap.py` (RESTORE_LIST, line ~62),
`data/oos_trades_2025_2026.json` (60-record JSON list, no committed writer).

Search `~/quant` for a generator that produces this file's exact shape
(`entry_date`, `exit_date`, `R`, `hold_days` per record, 2025-01-03 →
2026-06-25, 60 rows). If found: regenerate and byte-diff against the
committed file. Byte-identical → restore the writer script into this repo
(closes the gap for real, log the restoration via `decision_logger` per P1's
"changing anything after results exist" rule — even a provenance fix counts
as touching a locked artifact's story). Diverges → do NOT silently overwrite
the committed sealed/OOS data (non-negotiable 10: do not modify sealed
evaluation data) — log what diverged and why, as a finding, not a fix.

If no generator is found in `~/quant` after a real search (not just the
top-level filename grep already tried): log a `decision_logger` waiver
naming exactly what's unverified (no committed provenance for the 60-trade
OOS series) and why the repo proceeds anyway (spec 021's own Out-of-Scope
section already accepts this as a known, bounded gap). Use
`log_forex_decision` or the nearest applicable logger call (there is no
dedicated waiver function in `decision_logger.py` — confirmed; it's a
regular decision entry with the waiver reasoning in the payload/extra field).

**Verification:** a dated entry exists in `data/decision_logs/decisions_*.jsonl`
(waiver or restoration) that names the OOS provenance question explicitly —
findable by a future session without re-investigating.

### 3. Write `mutation_check_021.py`

**New file:** `daytrade/mutation_check_021.py` (matching the existing driver
location convention, even though 021's suites live under `scripts/` and
`sovereign/propfirm/` — the driver itself can run pytest against any path,
same as the others do via `subprocess.run([..., "-m", "pytest", target, ...])`).

Copy the exact skeleton from `daytrade/mutation_check_012.py` /
`mutation_check_gate2_wiring.py` (confirmed identical structure across all
nine existing drivers):
- `MUTATIONS` list of `(test_id, module_path, old_str, new_str, description)`
  tuples, encoding the fault set that's ALREADY been manually verified and
  recorded in `specs/021_MUTATION_LOG.md` — this is a faithful reproduction of
  known-good rows, not new fault design. Cover Phases 1, 2, 4, 5 (27 rows:
  M1–M7, E1–E10, V1–V5, P1–P5). Phase 3 (`diagnose_repro_gap.py`) has no
  mutation table — it's a runtime `assert` invariant, not a fault-injection
  target; leave it out of `MUTATIONS`, same as the log already does.
- `run_test()` helper: identical bytecode-purge-then-pytest pattern (purge
  `__pycache__` under both the target dir and repo root, run with
  `-B -q --no-header -p no:cacheprovider`, `PYTHONDONTWRITEBYTECODE=1`).
- `main()`: snapshot originals, try/finally restore, red-then-green check per
  row, refuse to write `specs/021_MUTATION_LOG.md` if any row fails
  (`fails > 0` gates out the write — do not touch the currently-committed,
  hand-verified log on any failed run).
- Regenerate `specs/021_MUTATION_LOG.md` on full success, preserving the
  existing Phase 3 prose section verbatim (it's not driver-generated) and
  replacing Phases 1/2/4/5's tables with the live-run output.

Target test files per phase (all already exist, confirmed):
`sovereign/propfirm/test_firm_contracts.py` (Phase 1, targets
`firm_contracts.yaml` + `firm_contracts.py`), `scripts/test_carry_buy_gate.py`
(Phase 2, targets `carry_buy_gate.py`), `scripts/test_daily_verdict_page.py`
(Phase 4, targets `build_daily_verdict_page.py`),
`scripts/test_paper_carry_log.py` (Phase 5, targets `paper_carry_log.py`).

**Verification:** `python3 daytrade/mutation_check_021.py` runs standalone,
reports 27/27 rows `ok`, and `specs/021_MUTATION_LOG.md` is regenerated from
a live run (diff against the current hand-typed version should show the same
substantive content, now driver-sourced).

### 4. Add real position-size math to `paper_carry_log.py`

**File:** `scripts/paper_carry_log.py`, `cmd_open` (lines 60–77) and its
argparse registration (lines 120–127).

Per Colin's choice: add a new required `--qty` argument (the actual position
size/units about to be traded). `cmd_open` then:
1. Loads `account_size` from `load_contract("cti_1step").account_size`
   (matches the existing pattern in `cmd_close`, which already pulls
   `costs.swap_haircut_r_per_day` from the same contract instead of
   re-declaring it — do the same here, don't hardcode 100000.0).
2. Computes `stated_dollar_risk = args.risk * account_size`.
3. Computes `implied_dollar_risk = args.qty * abs(args.entry - args.stop)`.
4. If `abs(implied_dollar_risk - stated_dollar_risk) / stated_dollar_risk`
   exceeds a tight tolerance (e.g. 1%): `raise SystemExit(...)` with a message
   naming both values — refuse, don't log (fail loud, non-negotiable 2).
5. On success, store `qty` in the trade record alongside the existing fields.

**Tests:** add to `scripts/test_paper_carry_log.py` — a passing case (qty
consistent with risk/account/entry/stop) and a refusing case (qty off by
enough to trip the tolerance) — named tests, not just manual verification.

**Mutation row:** add one row to `mutation_check_021.py`'s Phase 5 set
covering this new reconciliation check (e.g. drop the tolerance check
entirely → the new refusal test goes red).

**Verification:** `pytest scripts/test_paper_carry_log.py -q` green including
the two new tests; a manually constructed bad `--qty` for a given
`--risk`/`--entry`/`--stop` is refused at the CLI, not silently logged.

### 5. Fix the Python-version portability bug

**File:** `daytrade/splits.py`, `_log_unseal` (lines 108–118).

Line 114–115's f-string nests a backslash-containing string literal
(`'FORCED\t' if forced else ''`) inside the `{}` expression — PEP 701
(Python 3.12) syntax, breaking import on the machine's actual interpreter
(confirmed via `ceiling.py`/`backtest.py` import chain reaching
`test_constitution_wiring.py` and `test_runner_event_wiring.py`). Fix: pull
the conditional into a local variable before the f-string:

```python
tag = "FORCED\t" if forced else ""
fh.write(f"{datetime.now(timezone.utc).isoformat()}\t{rule_version}\t"
         f"{tag}{reason}\n")
```

Two-line change, no behavior change, removes the version dependency.

**Verification:** `python3 -m pytest daytrade/` runs clean end-to-end on
whatever Python is actually active on the machine (the fix is
version-independent, so this should pass on 3.10 through 3.14+).

### 6. Open trade #1 of the G5 sprint

**Only after 1–5 are done and G1–G4 are legitimately green** (per the
existing verdict page's own red-by-default discipline — don't force this
step early).

Pull a real signal from the live scan (not invented), then:
```
python3 scripts/paper_carry_log.py open \
  --pair <real pair> --direction <real LONG/SHORT> \
  --entry <real price> --stop <real price> --risk 0.01 --qty <real qty>
```

**Verification:** `python3 scripts/carry_buy_gate.py --series sealed
--update-state && python3 scripts/build_daily_verdict_page.py` shows G5 at
1/80; the verdict page's paper-sprint line reflects the real entry.

## Also this week (low-effort, previously flagged, still open)

- Revoke the GitHub personal access token pasted into an earlier session's
  chat. `github.com` → Settings → Developer settings → Personal access
  tokens. This is a manual browser action for Colin — flag it in the
  completion summary, don't attempt it via tooling.

## Explicitly out of scope

No cockpit/daytrade work (Gates 3–7 wiring) this week — separate track,
separate plan already delivered. No new hypotheses. No touching sealed
evaluation data. No bulk-editing `firm_contracts.yaml` beyond the one CTI
`max_dd.type` + `rules_asof` field. No restoring VIX3M/TNX/COT caches or the
sealed CSV's generator (explicitly out of spec 021's own scope, per
`diagnose_repro_gap.py`'s docstring) — only the OOS writer is in scope this
week, and only via search-then-waiver, not a rebuild.

## Verification (end-to-end, run after all 6 items land)

1. `pytest sovereign/propfirm/test_firm_contracts.py scripts/test_carry_buy_gate.py scripts/test_daily_verdict_page.py scripts/test_paper_carry_log.py -q` — all green.
2. `python3 -m pytest daytrade/` — clean on the local interpreter (proves item 5).
3. `python3 daytrade/mutation_check_021.py` — 27+/27+ rows `ok` (26 existing + 1 new sizing row), `specs/021_MUTATION_LOG.md` regenerated.
4. `python3 scripts/carry_buy_gate.py --series sealed --update-state` — read G1–G4 honestly; G4 in particular under the corrected trailing model.
5. `python3 scripts/build_daily_verdict_page.py` — verdict page reflects real gate state, G5 shows 1/80 after step 6.
6. `data/decision_logs/decisions_*.jsonl` — contains the OOS provenance entry (waiver or restoration) from item 2, findable by grep.
