# Closed-Loop Simulator: Sim-Mode Execution Lifecycle Logging

## Context

An audit of the Stockfish/AlphaZero execution chain found the trade logger (`sovereign/intelligence/decision_logger.py`) is structurally disconnected from both engines:

1. **Stockfish trades are never logged.** `daytrade/runner.py` decides exits, enforces Constitution rules, and persists a full decision-event history to `trade_events.py`'s JSONL log — but nothing calls `decision_logger.py`. There is no durable trade record outside the event log.
2. **Forex logs at scan time, not fill time.** `ForexSpecialist.run()` calls `log_forex_decision()` inside its scan/approval loop, before any broker interaction exists. Repeated scans of the same candidate create duplicate records. (Further investigation found there is *no broker call anywhere in `sovereign/forex/`* — the real order code, `execution/funderpro_executor.py`, is wired for ICT signals only and has no connection to `ForexSpecialist`. Building that bridge is a separate, larger project — not today's scope.)
3. **No stable trade_id correlates the chain.** `runner.py` mints `trade_id = f"{symbol}-{session_date}"` — date-only, so a second same-day trade on the same symbol collides (a limitation already documented in `specs/012_STOCKFISH_EVENT_MEMORY.md`). `decision_logger.py`'s `update_outcome()` falls back to fuzzy pair+timestamp matching. `find_recorded_outcome()` already reads a `trade_id` key from stored JSON expecting one to exist — but nothing writes it today.

The target for today is explicitly **a closed-loop simulator, not live model training**: correlate AlphaZero's pre-entry context, Stockfish's mechanical exit decisions, and a simulated fill/close into one record per trade, tagged `mode="sim"`, cleanly segregated from `shadow`/`paper`/`replay`/`live`. The goal is 20-50 clean, correlated records to seed a future offline walk-forward evaluation — no learning, tuning, or promotion happens today.

**Decided:** `log_sim_entry`/the sim-exit updater fire unconditionally on every `runner.py` run (no `--sim` CLI flag gate) — simpler, and duplicate/junk runs are harmless since idempotency is keyed on `trade_id`, not run count.

## Chain being built

```
AlphaZero pre-entry snapshot (bias.json, best-effort regime context)
          ↓ trade_id
Stockfish deterministic exit-event sequence (trade_events.py — UNCHANGED)
          ↓ trade_id
simulated fill (cycle/replay price), realized R, closing action/reason
          ↓
one closed, correlated decision_logger.py record, mode="sim"
```

## Implementation

### 1. `sovereign/intelligence/decision_logger.py` — extend, don't replace

**`DecisionRecord`** gains new `Optional`/defaulted fields (backward compatible — existing `log_ict_decision`/`log_forex_decision` callers unaffected):

```python
trade_id:                Optional[str]  = None   # correlation key
mode:                    str            = "live" # "sim" | "shadow" | "paper" | "replay" | "live"
order_id:                Optional[str]  = None

planned_entry:           Optional[float] = None
simulated_fill_price:    Optional[float] = None
simulated_fill_ts:       Optional[str]   = None
fill_slippage:           Optional[float] = None

strategy_version:        Optional[str]   = None
model_version:           Optional[str]   = None

alphazero_snapshot:      dict[str, Any]  = field(default_factory=dict)
stockfish_plan:           dict[str, Any] = field(default_factory=dict)
stockfish_initial_policy: Optional[str]  = None

events_path:              Optional[str]  = None
forecast_id:               Optional[str] = None
evidence_ids:               list[str]    = field(default_factory=list)

closing_action:            Optional[str] = None
closing_reason:            Optional[str] = None
```

`mode="sim"` is the chosen vocabulary — confirmed via grep that `"sim"` is unused anywhere in `daytrade/*.py` today, avoiding collision with `broker.py`'s `--broker shadow` and `shadow.py`'s policy-tournament engine (both already overload "shadow"), and `regret.py`'s `slippage_basis="paper"` label.

**New functions:**

- `log_sim_entry(*, trade_id, pair, direction, planned_entry, simulated_fill_price, stop_loss, tp1, tp2, strategy_version, model_version, alphazero_snapshot, stockfish_plan, stockfish_initial_policy, events_path, system="STOCKFISH", entry_timestamp, simulated_fill_ts, ...) -> DecisionRecord`
  Idempotent: looks up an existing OPEN-or-closed record by `trade_id` first (via new `find_open_sim_record`); if found, no-op and return it. Otherwise builds a `DecisionRecord(mode="sim", outcome=None, ...)` and calls existing `_safe_append`. `system="STOCKFISH"` is a new value alongside today's `ICT`/`FOREX`.

- `update_outcome_by_trade_id(*, trade_id, outcome, r_realized, exit_timestamp, closing_action, closing_reason, ...) -> bool`
  Exact-match sibling of `update_outcome()` — same read-all/rewrite-month-file mechanism as `_update_outcome_in_month`, but matches on `trade_id` equality instead of fuzzy pair+timestamp. Only updates records with `outcome in (None, "OPEN")`. Leave `update_outcome`/`_update_outcome_in_month` completely untouched.

- `find_open_sim_record(*, trade_id, pair, system=None) -> Optional[dict]`
  Read-only idempotency lookup for `log_sim_entry` — same scan pattern as the existing `find_recorded_outcome()` but also matches still-open records (that function currently filters to closed-only).

- `mint_trade_id(symbol: str, entry_ts: str) -> str`
  Pure helper: `f"{symbol}-{entry_ts}"`. Single source of the trade_id format, called from `runner.py` so the value threaded into `trade_events.py` and `decision_logger.py` is generated identically in one place.

### 2. `daytrade/runner.py` — three insertion points, event-append-before-apply sequence unchanged

**a) Trade_id minting (line ~374-375):** currently mints unconditionally at loop entry using date-only. Move minting into the fresh-open branch (line ~439, where `state_from_plan(plan)` is called) using a full ISO timestamp: `trade_id = mint_trade_id(symbol, datetime.now(timezone.utc).isoformat())`. This fixes the documented same-day collision as a byproduct. On `--resume` (line ~406-428), do **not** re-mint — pull `trade_id = events[0].trade_id` from the reconstructed event stream, so the sim record written before a crash is still found by the trade_id after resume. File naming (`events_{session_date}_{symbol}.jsonl`) stays date-based and untouched — only the `trade_id` *value* inside records changes.

**b) Sim-entry write** — immediately after `event_log.append(event_log_open[0])` (line ~448), still inside the fresh-open branch:
```python
az_snapshot = _read_alphazero_snapshot()   # new helper, near read_urgency()
try:
    from sovereign.intelligence.decision_logger import log_sim_entry
    log_sim_entry(
        trade_id=trade_id, pair=symbol,
        direction="LONG" if s.direction > 0 else "SHORT",
        planned_entry=float(plan["entry"]), simulated_fill_price=float(plan["entry"]),
        stop_loss=s.sl, tp1=s.tp1, tp2=s.tp2,
        strategy_version=SOURCE_VERSION, model_version=az_snapshot.get("model"),
        alphazero_snapshot=az_snapshot,
        stockfish_plan={k: v for k, v in plan.items() if not k.startswith("_")},
        stockfish_initial_policy=s.exit_policy, events_path=str(events_path),
        entry_timestamp=entry_ts_iso, simulated_fill_ts=entry_ts_iso,
    )
except Exception as e:
    print(f"# !! decision_logger sim-entry failed (trade continues): {e}")
```
`planned_entry == simulated_fill_price` at MVP — no slippage model yet; the field exists for later use.

**c) Sim-exit write** — after `apply_actions(...)` completes for a cycle whose `actions` include `EXIT_ALL` (state/price already reflect the closing cycle):
```python
if any(a.kind == "EXIT_ALL" for a in actions):
    exit_action = next(a for a in actions if a.kind == "EXIT_ALL")
    risk_per_share = float(plan.get("risk_per_share") or abs(plan["entry"] - plan["sl"]))
    realized_r = ((price - plan["entry"]) * s.direction) / risk_per_share if risk_per_share else 0.0
    outcome = "WIN" if realized_r > 0 else ("LOSS" if realized_r < 0 else "OPEN")
    try:
        from sovereign.intelligence.decision_logger import update_outcome_by_trade_id
        update_outcome_by_trade_id(
            trade_id=trade_id, outcome=outcome, r_realized=realized_r,
            exit_timestamp=datetime.now(timezone.utc).isoformat(),
            closing_action=exit_action.kind, closing_reason=exit_action.reason,
        )
    except Exception as e:
        print(f"# !! decision_logger close update failed (trade continues): {e}")
```
Simulated fill/exit price = the cycle's quote/replay price already bound in the loop — mirrors the precedent in `shadow.py`'s counterfactual-fill formula (no new fill-simulation model needed).

**New helper, near `read_urgency()`:** `_read_alphazero_snapshot() -> dict` — reads `bias.json`, defensively handles both the v1 placeholder schema (`ts, bias, urgency, n_headlines, detail, model`) and the richer spec-009 schema seen on disk today (adds `symbol, mode, conviction, suggested_urgency, urgency_armed, thesis, ...`). Captures `ts/bias/urgency/n_headlines/model/conviction/suggested_urgency/urgency_armed/thesis` (thesis truncated to ~500 chars) and, best-effort, a `regime` dict from `regime_vector.compute()`. Deliberately excludes long free-text fields (`day_plan`, `important_news`, `schedule`) to avoid bloating the sim corpus. Never raises — a snapshot failure must not block the entry write.

### 3. `sovereign/forex/forex_specialist.py` — stop scan-time writes

Delete the `try: from sovereign.intelligence.decision_logger import log_forex_decision ... except Exception: logger.debug(...)` block inside `ForexSpecialist.run()`'s `for sig in entry_signals:` loop (its return value is never captured, so removal has no downstream effect). Replace with a one-line comment noting the scan-time write was removed per audit finding, and that a real fill-confirmation path (bridging to `execution/funderpro_executor.py`, currently unwired for Forex) is required before Forex can produce sim/live trade records — that bridge is explicitly out of scope today. `report.tradeable.append(...)` is unaffected; candidates still flow normally.

Before deleting, confirm no hidden test depends on it: `grep -rn "log_forex_decision" sovereign/ daytrade/`.

### 4. Tests — `daytrade/test_sim_lifecycle.py`

Reuse `test_runner_event_wiring.py`'s `tmp_path`/`monkeypatch` fixture pattern (patch `runner.LOGDIR`, `runner.EVENTS_DIR`, `runner.DIRECTIVES`, and `decision_logger.LOG_DIR`).

1. **`test_duplicate_fill_writes_one_record`** — call `log_sim_entry()` twice with the identical `trade_id`; assert the decisions JSONL has exactly one line for that `trade_id`.
2. **`test_open_close_updates_same_record`** — `log_sim_entry()` then `update_outcome_by_trade_id()`; assert still one line, now with `outcome`/`r_realized`/`closing_action`/`closing_reason`/`exit_timestamp` populated; assert `find_open_sim_record()` returns `None` after close.
3. **`test_end_to_end_replay_correlated_record`** (required E2E) — build a `PLAN`/`REPLAY` fixture (mirroring `test_runner_event_wiring.py`) whose price path deterministically triggers `EXIT_ALL`; parametrize over both bias.json schema variants. Run `runner.run(dict(PLAN), interval=0, once=False, replay=REPLAY)`. Assert: (a) `trade_events.py`'s JSONL has one `POSITION_OPENED` + one `POSITION_CLOSED`, same `trade_id`; (b) decisions JSONL has exactly one record with that `trade_id`, `mode="sim"`, populated `outcome`/`r_realized`/`closing_action`/`closing_reason`/`alphazero_snapshot`/`stockfish_plan`/`stockfish_initial_policy`; (c) the decision record's `trade_id` equals the event log's `trade_id` — the literal correlation proof.

### 5. Spec doc — `specs/022_CLOSED_LOOP_SIMULATOR.md`

Per repo convention (spec-first for `daytrade/` changes, `specs/README.md` build-order table), a short `[SPEC]` doc covering: problem (the three audit findings), non-goals (no live training/promotion, no ForexSpecialist→broker bridge, no new AlphaZero grading — `forecast.py`'s `ForecastLedger` already owns that, MAE/MFE is a stretch goal not required today), trade_id format and single minting point, the `DecisionRecord` schema additions, the four new functions, the three runner.py insertion points (with the explicit note that event-append-before-apply ordering is unchanged), the ForexSpecialist deletion, the `mode="sim"` vocabulary choice and why it avoids the `shadow`/`paper` collision, and the test/verification plan below. Add a row to `specs/README.md`.

## Verification

1. **Syntax**: `python3 -c "import ast; ast.parse(open('daytrade/runner.py').read())"` (and same for `decision_logger.py`, `forex_specialist.py`).
2. **Unit**: `cd daytrade && python3 -m pytest test_sim_lifecycle.py test_runner_event_wiring.py test_trade_events.py test_constitution_wiring.py -v` — new tests pass, existing Gate-2 wiring tests unaffected (proves append-before-apply ordering wasn't disturbed).
3. **Forex regression**: `grep -rn "log_forex_decision" sovereign/ daytrade/` shows zero call sites left in `forex_specialist.py`'s `run()`.
4. **Manual replay**: `python3 daytrade/runner.py --plan data/daytrade/plan.json --replay <sample_prices.json> --once` against a scratch data dir; confirm `events_*.jsonl` has one `POSITION_OPENED` with the new long-form `trade_id`, and `data/decision_logs/decisions_<month>.jsonl` gains exactly one line with `"mode": "sim"` and the matching `trade_id`. Re-run with `--resume` and confirm no duplicate decision record is created.
5. **Cross-reference check**: for a handful of closed sim records, diff `trade_id` against the corresponding `events_*.jsonl` file's `POSITION_CLOSED` event and confirm `closing_reason` matches the event's `payload.reason` verbatim.

## Out of scope (today)

- ForexSpecialist → `execution/funderpro_executor.py` broker bridge (Forex has no fill-confirmation path yet; today's fix only stops the premature scan-time write).
- Any live model training, tuning, or promotion — `forecast.py`'s `ForecastLedger` already handles independent AlphaZero grading; not touched today.
- MAE/MFE tracking (`regret.py` has the primitive; wiring it into the sim lifecycle is a stretch goal, not required).
- Migrating or reconciling any historical/pre-existing decision_logger records.
