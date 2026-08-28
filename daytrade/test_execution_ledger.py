#!/usr/bin/env python3
"""Invariant tests for the execution ledger and its correlation to Stockfish.

Every test here is written so that DELIBERATELY BREAKING the invariant it names
makes it fail — the repo's VERIFIED definition (CLAUDE.md), not "a test exists".
The negative cases are as load-bearing as the positive ones: the whole point of
this card is that the previous attempt's `except: continue` made a duplicate
write look like a success.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sovereign.intelligence import execution_ledger as el  # noqa: E402
from sovereign.intelligence.execution_ledger import (  # noqa: E402
    DuplicateEntryConflict, DuplicateEventDetected, LedgerCorruption, LedgerError,
    SimulatedFill, close_executed_trade, find_executed_trade, log_correction,
    log_executed_trade, log_scan_candidate, mark_superseded, mint_trade_id,
    realized_r, realized_r_multileg, record_partial_exit,
)

T0 = "2026-08-12T13:30:00+00:00"
T1 = "2026-08-12T14:05:00+00:00"


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    """Every test gets its own ledger root. Nothing here touches real data."""
    monkeypatch.setattr(el, "LEDGER_ROOT", tmp_path / "decision_logs")
    return tmp_path / "decision_logs"


def _fill(price=204.0, qty=100.0, when=T0, commission=0.0, **kw):
    return SimulatedFill(price=price, qty=qty, filled_at=when,
                         basis="sim:test", commission=commission, **kw)


def _open(trade_id="NVDA-2026-08-12", mode="sim", price=204.0, stop=202.0,
          qty=100.0, commission=0.0, direction=+1, entry_timestamp=T0, **kw):
    return log_executed_trade(
        trade_id=trade_id, mode=mode, system="DAYTRADE", pair="NVDA",
        direction=direction, entry_fill=_fill(price=price, qty=qty, commission=commission),
        initial_stop=stop, strategy_version="stockfish-v3",
        entry_timestamp=entry_timestamp, **kw)


def _lines(ledger_root, mode="sim"):
    d = ledger_root / mode
    return [l for p in sorted(d.glob("*.jsonl")) for l in p.read_text().splitlines() if l.strip()]


# ─── ISC-1..7 schema, identity, isolation ────────────────────────────────────

def test_trade_id_matches_the_runner_scheme_exactly():
    """ISC-6: the join only works if both sides mint the same string."""
    symbol, session_date = "NVDA", datetime(2026, 8, 12).date()
    runner_side = f"{symbol}-{session_date}"          # daytrade/runner.py
    assert mint_trade_id(symbol, session_date) == runner_side


def test_ledger_root_is_absolute_and_cwd_independent():
    """The defect: LEDGER_ROOT used to be `Path("data/decision_logs")`, a
    relative path resolved against the process CWD. runner.py launched from
    inside daytrade/ (as operator_tick.sh does — `cd "$REPO/daytrade"`) then
    wrote real trade rows into daytrade/data/decision_logs/ instead of
    data/decision_logs/: silent scattering, not a crash.

    Proven here by actually importing the module from two different process
    CWDs (repo root, and daytrade/) and asserting the resolved LEDGER_ROOT is
    byte-identical either way — an in-process `os.chdir` would not exercise
    the real bug, since it never touched `Path(__file__)`.
    """
    import subprocess
    repo_root = Path(__file__).resolve().parents[1]
    probe = ("import sys; sys.path.insert(0, %r); "
             "from sovereign.intelligence.execution_ledger import LEDGER_ROOT; "
             "print(LEDGER_ROOT)") % str(repo_root)

    from_root = subprocess.run([sys.executable, "-c", probe], cwd=repo_root,
                               capture_output=True, text=True, check=True)
    from_daytrade = subprocess.run([sys.executable, "-c", probe],
                                   cwd=repo_root / "daytrade",
                                   capture_output=True, text=True, check=True)

    assert from_root.stdout.strip() == from_daytrade.stdout.strip(), (
        "LEDGER_ROOT resolved differently depending on the launch directory: "
        f"root={from_root.stdout.strip()!r} daytrade={from_daytrade.stdout.strip()!r}")
    assert from_root.stdout.strip() == str(repo_root / "data" / "decision_logs")


def test_mint_trade_id_is_deterministic():
    a = mint_trade_id("NVDA", "2026-08-12")
    b = mint_trade_id("NVDA", "2026-08-12")
    assert a == b


@pytest.mark.parametrize("bad", ["", "   ", " NVDA-1", "NVDA-1 ", None, 7])
def test_blank_or_unstable_trade_id_is_refused(bad):
    """ISC-13: a join key with drifting whitespace is not a join key."""
    with pytest.raises(LedgerError):
        el._check_trade_id(bad)


def test_unknown_mode_raises_never_defaults():
    """ISC-4."""
    with pytest.raises(LedgerError, match="unknown mode"):
        _open(mode="backtest")


def test_each_mode_writes_to_its_own_directory(isolated_ledger):
    """ISC-7 + the acceptance criterion: sim is never blended with paper."""
    _open(trade_id="NVDA-1", mode="sim")
    _open(trade_id="NVDA-1", mode="paper")
    assert len(_lines(isolated_ledger, "sim")) == 1
    assert len(_lines(isolated_ledger, "paper")) == 1
    # Same trade_id, different lanes, and a sim lookup cannot see the paper row.
    assert find_executed_trade("NVDA-1", "sim")["mode"] == "sim"
    assert find_executed_trade("NVDA-1", "paper")["mode"] == "paper"


def test_sim_rows_carry_the_sim_label(isolated_ledger):
    rec = _open(mode="sim")
    assert rec["mode"] == "sim"
    assert rec["mode"] not in el.REAL_MONEY_MODES


# ─── ISC-8..14 idempotency — the card's first named test ─────────────────────

def test_duplicate_confirmed_entry_fill_produces_one_record(isolated_ledger):
    """CARD TEST 1: repeated delivery of the same trade_id → exactly one record."""
    first = _open()
    for _ in range(5):
        again = _open()
        assert again["trade_id"] == first["trade_id"]
        assert again["recorded_at"] == first["recorded_at"]   # the SAME row, not a new one
    assert len(_lines(isolated_ledger)) == 1


def test_duplicate_delivery_appends_nothing(isolated_ledger):
    """ISC-10: byte-level proof, not just a count of parsed records."""
    _open()
    path = next((isolated_ledger / "sim").glob("*.jsonl"))
    before = path.read_bytes()
    _open()
    assert path.read_bytes() == before


def test_conflicting_redelivery_raises_rather_than_picking_a_winner():
    """ISC-11: same id, different entry facts is a real problem, not a duplicate."""
    _open(stop=202.0)
    with pytest.raises(DuplicateEntryConflict, match="different"):
        _open(stop=201.0)


def test_corrupt_line_raises_and_is_never_skipped(isolated_ledger):
    """ISC-12 — THE defect that broke the previous attempt.

    A skipped corrupt line makes an existing trade look absent, and the next
    'idempotent' write then creates a duplicate. Deliberately reverting
    `_read_records` to `except json.JSONDecodeError: continue` makes this fail.
    """
    _open(trade_id="NVDA-1")
    path = next((isolated_ledger / "sim").glob("*.jsonl"))
    path.write_text("{not json\n" + path.read_text())
    with pytest.raises(LedgerCorruption):
        find_executed_trade("NVDA-1", "sim")


def test_entry_with_zero_risk_is_refused():
    """ISC-20 at the entry boundary: an unscoreable trade is not recorded."""
    with pytest.raises(LedgerError, match="initial risk is zero"):
        _open(price=204.0, stop=204.0)


def test_entry_requires_a_typed_confirmed_fill():
    """A dict is not evidence that a fill was confirmed."""
    with pytest.raises(LedgerError, match="SimulatedFill"):
        log_executed_trade(
            trade_id="NVDA-1", mode="sim", system="DAYTRADE", pair="NVDA",
            direction=+1, entry_fill={"price": 204.0, "qty": 100},
            initial_stop=202.0, strategy_version="v", entry_timestamp=T0)


def test_strategy_version_is_required():
    with pytest.raises(LedgerError, match="strategy_version"):
        log_executed_trade(
            trade_id="NVDA-1", mode="sim", system="DAYTRADE", pair="NVDA",
            direction=+1, entry_fill=_fill(), initial_stop=202.0,
            strategy_version="", entry_timestamp=T0)


def test_oversize_payload_is_refused_not_truncated():
    """Same contract as trade_events: truncation changes what the record claims."""
    with pytest.raises(LedgerError, match="max 512"):
        _open(alphazero_snapshot={"thesis": "x" * 513})


# ─── ISC-15..23 close semantics + R arithmetic ───────────────────────────────

def test_entry_then_close_updates_that_exact_record(isolated_ledger):
    """CARD TEST 2: entry then close updates the same row with realized R."""
    _open(trade_id="NVDA-1", price=204.0, stop=202.0, qty=100.0)
    _open(trade_id="NVDA-2", price=300.0, stop=299.0, qty=10.0)   # a bystander

    closed = close_executed_trade(
        trade_id="NVDA-1", mode="sim",
        exit_fill=_fill(price=208.0, qty=100.0, when=T1),
        exit_reason="TP2: day goal hit", exit_timestamp=T1)

    assert closed["trade_id"] == "NVDA-1"
    assert closed["outcome"] == "WIN"
    assert closed["r_realized"] == pytest.approx((208.0 - 204.0) / 2.0)   # +2R
    assert closed["exit_timestamp"] == T1
    assert closed["exit_reason"] == "TP2: day goal hit"
    assert closed["exit_fill"]["price"] == 208.0

    assert len(_lines(isolated_ledger)) == 2                  # updated, not appended
    assert find_executed_trade("NVDA-2", "sim")["outcome"] == "OPEN"   # bystander untouched


def test_loss_is_labelled_and_signed(isolated_ledger):
    _open(price=204.0, stop=202.0, qty=100.0)
    closed = close_executed_trade(
        trade_id="NVDA-2026-08-12", mode="sim",
        exit_fill=_fill(price=202.0, qty=100.0, when=T1),
        exit_reason="stop hit", exit_timestamp=T1)
    assert closed["outcome"] == "LOSS"
    assert closed["r_realized"] == pytest.approx(-1.0)


def test_r_uses_the_actual_fill_not_the_planned_entry():
    """ISC-17. Plan said 204; the fill was 204.50. R must use 204.50."""
    _open(trade_id="NVDA-1", price=204.50, stop=202.0, qty=100.0,
          stockfish_plan={"entry": 204.0})
    closed = close_executed_trade(
        trade_id="NVDA-1", mode="sim", exit_fill=_fill(price=209.50, qty=100.0, when=T1),
        exit_reason="exit", exit_timestamp=T1)
    assert closed["r_realized"] == pytest.approx((209.50 - 204.50) / 2.50)
    # ... and NOT the planned-entry answer, which would be a different number.
    assert closed["r_realized"] != pytest.approx((209.50 - 204.0) / 2.0)


def test_commissions_reduce_realized_r(isolated_ledger):
    """ISC-18."""
    _open(trade_id="NVDA-1", price=204.0, stop=202.0, qty=100.0, commission=10.0)
    closed = close_executed_trade(
        trade_id="NVDA-1", mode="sim",
        exit_fill=_fill(price=208.0, qty=100.0, when=T1, commission=10.0),
        exit_reason="exit", exit_timestamp=T1)
    # gross 400, costs 20, risk 200 -> 1.9R, strictly less than the costless 2.0R
    assert closed["r_realized"] == pytest.approx(1.90)
    assert closed["costs"]["total_commission"] == pytest.approx(20.0)


def test_slippage_is_not_double_counted():
    """ISC-19. Slippage is already inside the fill price; subtracting it again
    is the easiest arithmetic error in this module."""
    clean = realized_r(entry_price=204.0, initial_stop=202.0, exit_price=208.0,
                       qty=100, direction="LONG", commissions=0.0)
    # A fill that records slippage provenance must produce the SAME R, because
    # `price` is already the effective fill.
    f = SimulatedFill(price=204.0, qty=100, filled_at=T0, basis="sim:test",
                      intended_price=203.90, slippage_per_share=0.10)
    assert f.slippage_per_share == 0.10
    same = realized_r(entry_price=f.price, initial_stop=202.0, exit_price=208.0,
                      qty=f.qty, direction="LONG", commissions=f.commission)
    assert same == pytest.approx(clean)


def test_scaled_out_trade_scores_every_leg(isolated_ledger):
    """THE defect the live probe caught. A trade that banks 50% at +0.9R and then
    stops its runner is NOT a -2R loss.

    entry 204 / stop 202 -> 1R = $2/share on 100 shares = $200 risk
      leg 1: 50 @ 205.8  -> +90
      leg 2: 50 @ 200.0  -> -200
      net -110 / 200     -> -0.55R
    Deleting the partial leg makes this report -2.0R, which is the bug.
    """
    _open(trade_id="NVDA-1", price=204.0, stop=202.0, qty=100.0)
    record_partial_exit(trade_id="NVDA-1", mode="sim",
                        fill=_fill(price=205.8, qty=50.0, when=T1),
                        reason="TP2: day goal banked", leg_key="TAKE_PARTIAL:0.5:1")
    closed = close_executed_trade(
        trade_id="NVDA-1", mode="sim",
        exit_fill=_fill(price=200.0, qty=50.0, when=T1),
        exit_reason="stop hit", exit_timestamp=T1)
    assert closed["r_realized"] == pytest.approx(-0.55)
    assert closed["r_realized"] != pytest.approx(-2.0)      # the single-leg answer
    assert len(closed["partial_exits"]) == 1


def test_partial_leg_is_idempotent_on_its_key(isolated_ledger):
    """A crash-retry must not book one partial twice — a double-counted leg
    silently changes R with nothing to show for it."""
    _open(trade_id="NVDA-1", qty=100.0)
    for _ in range(3):
        rec = record_partial_exit(trade_id="NVDA-1", mode="sim",
                                  fill=_fill(price=205.8, qty=50.0, when=T1),
                                  reason="TP2", leg_key="TAKE_PARTIAL:0.5:1")
    assert len(rec["partial_exits"]) == 1


def test_unreconciled_legs_raise_rather_than_mis_score():
    """A missing leg shifts R in a direction that depends on which leg is
    missing — so it must never be silently scored."""
    _open(trade_id="NVDA-1", price=204.0, stop=202.0, qty=100.0)
    with pytest.raises(LedgerError, match="exit legs total"):
        close_executed_trade(trade_id="NVDA-1", mode="sim",
                             exit_fill=_fill(price=200.0, qty=50.0, when=T1),
                             exit_reason="stop hit", exit_timestamp=T1)


def test_partial_before_entry_raises():
    with pytest.raises(LedgerError, match="cannot precede"):
        record_partial_exit(trade_id="GHOST", mode="sim", fill=_fill(when=T1),
                            reason="x", leg_key="k")


def test_short_direction_r_is_signed_correctly():
    r = realized_r(entry_price=204.0, initial_stop=206.0, exit_price=200.0,
                   qty=100, direction="SHORT")
    assert r == pytest.approx(2.0)


def test_zero_risk_r_raises_instead_of_inf():
    """ISC-20: inf/NaN would poison every aggregate that touches it."""
    with pytest.raises(LedgerError, match="undefined"):
        realized_r(entry_price=204.0, initial_stop=204.0, exit_price=208.0,
                   qty=100, direction="LONG")


def test_closing_an_unknown_trade_id_raises():
    """ISC-21: a close with no entry must never fabricate the entry."""
    with pytest.raises(LedgerError, match="no open record"):
        close_executed_trade(trade_id="GHOST-1", mode="sim",
                             exit_fill=_fill(when=T1), exit_reason="x")


def test_identical_reclose_is_a_noop(isolated_ledger):
    """ISC-22: the classic retry, same rule trade_events applies to duplicates."""
    _open(trade_id="NVDA-1")
    args = dict(trade_id="NVDA-1", mode="sim",
                exit_fill=_fill(price=208.0, qty=100.0, when=T1),
                exit_reason="exit", exit_timestamp=T1)
    a = close_executed_trade(**args)
    b = close_executed_trade(**args)
    assert a["r_realized"] == b["r_realized"]
    assert len(_lines(isolated_ledger)) == 1


def test_conflicting_reclose_raises(isolated_ledger):
    """ISC-23: an outcome is observed once, not re-litigated."""
    _open(trade_id="NVDA-1")
    close_executed_trade(trade_id="NVDA-1", mode="sim",
                         exit_fill=_fill(price=208.0, qty=100.0, when=T1),
                         exit_reason="exit", exit_timestamp=T1)
    with pytest.raises(LedgerError, match="already closed"):
        close_executed_trade(trade_id="NVDA-1", mode="sim",
                             exit_fill=_fill(price=210.0, qty=100.0, when=T1),
                             exit_reason="different exit", exit_timestamp=T1)


def test_close_requires_a_reason():
    _open(trade_id="NVDA-1")
    with pytest.raises(LedgerError, match="exit_reason"):
        close_executed_trade(trade_id="NVDA-1", mode="sim",
                             exit_fill=_fill(when=T1), exit_reason="  ")


# ─── ISC-30..32 scan candidates are not executed trades ──────────────────────

def _imported_and_called(path: Path) -> tuple[set[str], set[str]]:
    """Names actually imported and actually called, via the AST.

    Deliberately NOT a substring scan: this file and the modules it guards both
    discuss the forbidden names in prose, and a grep-based structural test that
    trips over its own documentation is a test nobody keeps.
    """
    import ast
    tree = ast.parse(path.read_text())
    imported, called = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Call):
            f = node.func
            called.add(f.id if isinstance(f, ast.Name)
                       else f.attr if isinstance(f, ast.Attribute) else "")
    return imported, called


def test_forex_specialist_no_longer_logs_executed_decisions():
    """ISC-30: the scan path must not reach the executed-trade log."""
    imported, called = _imported_and_called(
        Path(__file__).resolve().parents[1] / "sovereign" / "forex" / "forex_specialist.py")
    assert "log_forex_decision" not in imported
    assert "log_forex_decision" not in called
    assert "log_scan_candidate" in called


def test_candidates_live_outside_every_executed_lane(isolated_ledger):
    """ISC-31/32: a candidate can never be counted as a trade."""
    log_scan_candidate(system="FOREX", pair="GBPUSD=X", direction="LONG",
                       entry_level=1.27, stop_loss=1.26, as_of=T0,
                       rationale=["irp_z=+1.8"])
    assert len(_lines(isolated_ledger, "candidates")) == 1
    for mode in el.MODES:
        assert _lines(isolated_ledger, mode) == []
    rec = json.loads(_lines(isolated_ledger, "candidates")[0])
    assert rec["record_kind"] == "candidate"
    assert "r_realized" not in rec and "outcome" not in rec


# ─── ISC-30..32 session date: replay keys on the tape, never the wall clock ──

def test_replay_session_date_comes_from_the_plan_not_wall_clock():
    """Defect 2: a replay run must key trade_id/session on the tape's own
    date. Replaying a 2026-08-21 tape today must produce 2026-08-21, not
    today's date — this is the actual bug that minted NVDA-2026-08-22 for a
    session that traded 2026-08-21 bars."""
    import runner
    got = runner.resolve_session_date(live=False, plan={"_session": "2026-08-21"})
    assert got == datetime(2026, 8, 21).date()


def test_replay_without_session_field_fails_loud_not_now():
    """CLAUDE.md rule 5: an unresolvable session date must raise, never
    silently fall back to datetime.now()."""
    import runner
    with pytest.raises(ValueError, match="_session"):
        runner.resolve_session_date(live=False, plan={"symbol": "NVDA"})


def test_replay_with_malformed_session_field_fails_loud():
    import runner
    with pytest.raises(ValueError, match="not an ISO date"):
        runner.resolve_session_date(live=False, plan={"_session": "not-a-date"})


def test_live_session_date_still_uses_wall_clock():
    """The live path must be unchanged: wall clock IS the session date for a
    genuine live run — there is no tape to read a date from."""
    import runner
    got = runner.resolve_session_date(live=True, plan={})
    assert got == datetime.now(runner.ET).date()


# ─── ISC-33 basis: a closed record's fills always carry real provenance ─────

def test_closed_trade_fills_carry_a_real_basis_string(isolated_ledger):
    """Defect 3, as a regression: entry_fill.basis and exit_fill.basis must
    never be null/empty on a persisted record — that string is the only thing
    that keeps a nominal-geometry R from being misread later as a real
    fill-to-fill result. SimulatedFill already refuses to construct with an
    empty basis (ISC-14-ish), but this pins it on the ACTUAL PERSISTED record,
    end to end, matching the documented format
    'sim:<what>;costs=<declared|none-declared>'."""
    _open(trade_id="NVDA-1", price=204.0, stop=202.0, qty=100.0)
    closed = close_executed_trade(
        trade_id="NVDA-1", mode="sim",
        exit_fill=_fill(price=208.0, qty=100.0, when=T1),
        exit_reason="TP2: day goal hit", exit_timestamp=T1)

    entry_basis = closed["entry_fill"]["basis"]
    exit_basis = closed["exit_fill"]["basis"]
    assert entry_basis and isinstance(entry_basis, str)
    assert exit_basis and isinstance(exit_basis, str)

    # And on disk — not just in the returned dict.
    row = json.loads(_lines(isolated_ledger)[-1])
    assert row["entry_fill"]["basis"]
    assert row["exit_fill"]["basis"]


# ─── ISC-27..29 end-to-end: AlphaZero → entry → Stockfish events → exit ──────

def test_end_to_end_replay_produces_one_closed_correlated_record(tmp_path, monkeypatch,
                                                                isolated_ledger):
    """CARD TEST 3 — the acceptance criterion, executed.

    AlphaZero context → simulated entry → Stockfish event history → simulated
    exit → exactly ONE closed record, joined to the event log by trade_id.
    """
    import runner

    monkeypatch.setattr(runner, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "LOGDIR", tmp_path)
    bias = tmp_path / "bias.json"
    bias.write_text(json.dumps({
        "ts": T0, "bias": 0.35, "urgency": "none", "n_headlines": 12,
        "model": "keyword-valence v1 (placeholder, unvalidated)"}))
    monkeypatch.setattr(runner, "BIAS", bias)
    monkeypatch.setattr(runner, "DIRECTIVES", tmp_path / "directives.json")

    session_date = datetime.now(runner.ET).date()
    plan = runner.load_plan_dict = {
        "symbol": "NVDA", "direction": 1, "entry": 204.0, "qty": 100,
        "sl": 202.0, "tp1_r": 0.45, "tp2_r": 0.9, "trail_r": 0.4,
        "exit_policy": "DEFAULT",
        "sim_costs": {"commission_per_share": 0.005, "slippage_per_share": 0.0},
        # required since runner.run keys a replay's trade_id/session off the
        # plan, never off wall clock — see runner.py's session_date.
        "_session": str(session_date),
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    plan = runner.load_plan(plan_path)

    # A price path that runs the full ladder and ends on a stop-out EXIT_ALL.
    prices = [204.2, 204.9, 205.8, 206.4, 200.0]
    rc = runner.run(plan, interval=0, once=False, replay=prices, max_stale=180,
                    require_bias=False, broker=None, ledger_mode="sim")
    assert rc == 0

    trade_id = f"NVDA-{session_date}"

    # --- one closed record, in the sim lane only ---
    rows = _lines(isolated_ledger, "sim")
    assert len(rows) == 1, f"expected exactly one lifecycle row, got {len(rows)}"
    rec = json.loads(rows[0])
    assert rec["trade_id"] == trade_id
    assert rec["mode"] == "sim"
    assert rec["outcome"] in ("WIN", "LOSS", "SCRATCH")
    assert rec["r_realized"] is not None
    assert rec["exit_timestamp"] and rec["exit_reason"]

    # --- the join actually holds against Stockfish's own event log ---
    from trade_events import JsonlEventLog
    events = JsonlEventLog(tmp_path / f"events_{session_date}_NVDA.jsonl").load()
    assert events, "the Stockfish event log should exist"
    assert {e.trade_id for e in events} == {trade_id}

    # --- AlphaZero's pre-entry context is on the record, with provenance ---
    az = rec["alphazero_snapshot"]
    assert az["bias"]["available"] is True
    assert az["bias"]["model_version"] == "keyword-valence v1 (placeholder, unvalidated)"
    assert az["bias"]["produced_at"] == T0
    assert az["bias"]["bias"] == 0.35

    # --- Stockfish's plan, initial risk and exit policy are on the record ---
    assert rec["exit_policy"] == "DEFAULT"
    assert rec["risk_per_share"] == pytest.approx(2.0)
    assert rec["stockfish_plan"]["tp2"] == pytest.approx(plan["tp2"])

    # --- nothing leaked into a real-money lane ---
    for mode in el.REAL_MONEY_MODES:
        assert _lines(isolated_ledger, mode) == []


def test_evidence_ids_carried_from_symbol_scoped_directive(tmp_path, monkeypatch,
                                                            isolated_ledger):
    """The join `execution_ledger.evidence_ids` <-> `operator/evidence.jsonl`
    (the fields the observed-predicted roadmap names as unpopulated on both
    sides) is closed at the runner's entry boundary: it carries the ids the
    active, THIS-symbol-scoped directive already cited, and only those —
    another symbol's citations must never leak into this trade's row."""
    import runner

    monkeypatch.setattr(runner, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "LOGDIR", tmp_path)
    monkeypatch.setattr(runner, "BIAS", tmp_path / "no_bias.json")

    directives_path = tmp_path / "directives.json"
    directives_path.write_text(json.dumps([
        {
            "directive_id": "dir-nvda-1",
            "scope": {"symbols": ["NVDA"]},
            "model_version": "alphazero/test",
            "evidence_ids": ["op-nvda-ev0", "op-nvda-ev1"],
        },
        {
            "directive_id": "dir-amd-1",
            "scope": {"symbols": ["AMD"]},
            "model_version": "alphazero/test",
            "evidence_ids": ["op-amd-ev0"],
        },
    ]))
    monkeypatch.setattr(runner, "DIRECTIVES", directives_path)

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({
        "symbol": "NVDA", "direction": 1, "entry": 204.0, "qty": 100, "sl": 202.0,
        "tp1_r": 0.45, "tp2_r": 0.9, "trail_r": 0.4, "exit_policy": "DEFAULT",
        "_session": "2026-08-12"}))
    plan = runner.load_plan(plan_path)

    assert runner.run(plan, interval=0, once=False, replay=[204.2, 200.0],
                      max_stale=180, require_bias=False, broker=None,
                      ledger_mode="sim") == 0

    rows = _lines(isolated_ledger, "sim")
    assert len(rows) == 1
    rec = json.loads(rows[0])
    assert rec["evidence_ids"] == ["op-nvda-ev0", "op-nvda-ev1"]
    assert "op-amd-ev0" not in rec["evidence_ids"]


def test_missing_directives_yields_empty_evidence_ids_not_fabricated(
        tmp_path, monkeypatch, isolated_ledger):
    """No directive scoped to this symbol at entry time means an honest empty
    list — never a defaulted/fabricated id (CLAUDE.md rule 3)."""
    import runner

    monkeypatch.setattr(runner, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "LOGDIR", tmp_path)
    monkeypatch.setattr(runner, "BIAS", tmp_path / "no_bias.json")
    monkeypatch.setattr(runner, "DIRECTIVES", tmp_path / "no_dir.json")

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({
        "symbol": "NVDA", "direction": 1, "entry": 204.0, "qty": 100, "sl": 202.0,
        "tp1_r": 0.45, "tp2_r": 0.9, "trail_r": 0.4, "exit_policy": "DEFAULT",
        "_session": "2026-08-12"}))
    plan = runner.load_plan(plan_path)

    assert runner.run(plan, interval=0, once=False, replay=[204.2, 200.0],
                      max_stale=180, require_bias=False, broker=None,
                      ledger_mode="sim") == 0

    rows = _lines(isolated_ledger, "sim")
    rec = json.loads(rows[0])
    assert rec["evidence_ids"] == []


def test_second_same_day_trade_is_refused_loudly(tmp_path, monkeypatch, isolated_ledger):
    """The session-level trade_id limitation, asserted rather than left to
    surface as a traceback mid-session.

    The Stockfish event log rotates a CLOSED trade aside and permits the
    doctrine's bet-2 the same day; the ledger has one row per trade_id. The
    collision must be refused at the entry boundary with the remedy named.
    """
    import runner
    monkeypatch.setattr(runner, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "LOGDIR", tmp_path)
    monkeypatch.setattr(runner, "BIAS", tmp_path / "no_bias.json")
    monkeypatch.setattr(runner, "DIRECTIVES", tmp_path / "no_dir.json")

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({
        "symbol": "NVDA", "direction": 1, "entry": 204.0, "qty": 100, "sl": 202.0,
        "tp1_r": 0.45, "tp2_r": 0.9, "trail_r": 0.4, "exit_policy": "DEFAULT",
        "_session": "2026-08-12"}))
    plan = runner.load_plan(plan_path)

    assert runner.run(plan, interval=0, once=False, replay=[204.2, 200.0],
                      max_stale=180, require_bias=False, broker=None,
                      ledger_mode="sim") == 0
    with pytest.raises(RuntimeError, match="already holds a CLOSED trade"):
        runner.run(plan, interval=0, once=False, replay=[204.2, 200.0],
                   max_stale=180, require_bias=False, broker=None, ledger_mode="sim")


def test_ledger_off_leaves_the_runner_byte_identical(tmp_path, monkeypatch, isolated_ledger):
    """Anti: --ledger-mode off must not change a single engine decision."""
    import runner
    monkeypatch.setattr(runner, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "LOGDIR", tmp_path)
    monkeypatch.setattr(runner, "BIAS", tmp_path / "no_bias.json")
    monkeypatch.setattr(runner, "DIRECTIVES", tmp_path / "no_dir.json")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({
        "symbol": "NVDA", "direction": 1, "entry": 204.0, "qty": 100, "sl": 202.0,
        "tp1_r": 0.45, "tp2_r": 0.9, "trail_r": 0.4, "exit_policy": "DEFAULT",
        "_session": "2026-08-12"}))
    plan = runner.load_plan(plan_path)
    assert runner.run(plan, interval=0, once=False, replay=[204.2, 205.8, 200.0],
                      max_stale=180, require_bias=False, broker=None,
                      ledger_mode=None) == 0
    assert not (isolated_ledger / "sim").exists()      # nothing written at all


def test_missing_bias_is_labelled_unavailable_not_empty(tmp_path, monkeypatch):
    """ISC-25 — the defect that killed the previous attempt, as an invariant.

    An absent bias.json must produce an explicit reason. Returning `{}` would
    make "AlphaZero said nothing" and "we failed to read AlphaZero" identical.
    """
    import runner
    monkeypatch.setattr(runner, "BIAS", tmp_path / "nope.json")
    monkeypatch.setattr(runner, "DIRECTIVES", tmp_path / "nope2.json")
    snap = runner.alphazero_snapshot("NVDA")
    assert snap["bias"]["available"] is False
    assert "does not exist" in snap["bias"]["reason"]
    assert snap["directives"]["available"] is False
    assert snap["regime"]["available"] is False
    assert "regime.py is not built" in snap["regime"]["reason"]


def test_unreadable_bias_is_labelled_not_swallowed(tmp_path, monkeypatch):
    import runner
    bad = tmp_path / "bias.json"
    bad.write_text("{not json")
    monkeypatch.setattr(runner, "BIAS", bad)
    monkeypatch.setattr(runner, "DIRECTIVES", tmp_path / "nope.json")
    snap = runner.alphazero_snapshot("NVDA")
    assert snap["bias"]["available"] is False
    assert "unreadable" in snap["bias"]["reason"]


# ─── ISC-33 the engine is untouched ──────────────────────────────────────────

def test_ledger_cannot_reach_the_exit_engine():
    """ISC-33/Anti: the ledger records, it never decides. If it ever imports
    stockfish_exit, a second decision path has become possible."""
    imported, called = _imported_and_called(
        Path(__file__).resolve().parents[1] / "sovereign" / "intelligence" / "execution_ledger.py")
    assert not any("stockfish" in m for m in imported), f"ledger imports {imported}"
    assert "decide_exit" not in called
    assert "apply_action" not in called and "apply_actions" not in called


# ─── content-level duplicate detection — NVDA-2026-08-21/-22, hardened ───────
#
# The incident: two trade_ids (a correct one and a wall-clock artifact,
# 52a24e3) recorded the SAME replayed event. DuplicateEntryConflict never
# fired because it keys on trade_id, and these two ids differ. These tests
# use the real numbers from that incident: SHORT, entry 216.06, exit 214.975,
# stop 216.35 — 1R = 0.29, gross = (216.06-214.975)*100 = 108.5 -> R≈3.3534.

_DUP_KW = dict(price=216.06, stop=216.35, qty=100.0, direction=-1)


def test_content_duplicate_within_window_raises_in_sim(isolated_ledger):
    """CARD TEST: the exact shape of NVDA-2026-08-21/-22, replayed. This must
    raise in a non-real-money mode — sim/shadow/replay lose nothing by
    refusing, and this is precisely the bug class the guard exists to catch.
    Deliberately deleting the `_content_duplicates` check in
    close_executed_trade makes this test fail: the second close would
    succeed silently instead of raising, exactly like the real incident.
    """
    # Real incident shape: BOTH rows carry a same-day entry_timestamp (the
    # fill's own wall clock, never touched by the bug) — only the trade_id
    # differed, minted from session_date instead of the tape date (52a24e3).
    _open(trade_id="NVDA-2026-08-21", entry_timestamp="2026-08-22T20:00:00+00:00", **_DUP_KW)
    close_executed_trade(trade_id="NVDA-2026-08-21", mode="sim",
                         exit_fill=_fill(price=214.975, qty=100.0, when="2026-08-22T20:20:00+00:00"),
                         exit_reason="TP hit", exit_timestamp="2026-08-22T20:20:00+00:00")

    _open(trade_id="NVDA-2026-08-22", entry_timestamp="2026-08-22T20:07:58+00:00", **_DUP_KW)
    with pytest.raises(DuplicateEventDetected, match="content-level duplicate"):
        close_executed_trade(trade_id="NVDA-2026-08-22", mode="sim",
                             exit_fill=_fill(price=214.975, qty=100.0, when="2026-08-22T20:25:38+00:00"),
                             exit_reason="TP hit", exit_timestamp="2026-08-22T20:25:38+00:00")

    # And it was never written: the sim row for -22 stays OPEN.
    assert find_executed_trade("NVDA-2026-08-22", "sim")["outcome"] == "OPEN"


def test_content_duplicate_outside_window_does_not_raise(isolated_ledger):
    """A genuinely distinct trade that happens to share every price, days
    later, is outside the drift window this bug class can produce and is not
    flagged — the window exists to catch the bug, not to ban coincidence."""
    _open(trade_id="NVDA-2026-08-10", entry_timestamp="2026-08-10T20:00:00+00:00", **_DUP_KW)
    close_executed_trade(trade_id="NVDA-2026-08-10", mode="sim",
                         exit_fill=_fill(price=214.975, qty=100.0, when="2026-08-10T20:25:00+00:00"),
                         exit_reason="TP hit", exit_timestamp="2026-08-10T20:25:00+00:00")

    _open(trade_id="NVDA-2026-08-20", entry_timestamp="2026-08-20T20:00:00+00:00", **_DUP_KW)
    closed = close_executed_trade(trade_id="NVDA-2026-08-20", mode="sim",
                                  exit_fill=_fill(price=214.975, qty=100.0, when="2026-08-20T20:25:00+00:00"),
                                  exit_reason="TP hit", exit_timestamp="2026-08-20T20:25:00+00:00")
    assert closed["outcome"] == "WIN"
    assert "possible_duplicate_of" not in closed


def test_content_duplicate_in_real_money_mode_is_flagged_never_blocked(isolated_ledger):
    """CARD TEST: in paper/live, the trade already happened. Refusing to
    record it would be the non-negotiable #3 violation this ledger exists to
    prevent, so the guard records it and flags it instead of raising."""
    _open(trade_id="NVDA-2026-08-21", mode="paper",
          entry_timestamp="2026-08-22T20:00:00+00:00", **_DUP_KW)
    close_executed_trade(trade_id="NVDA-2026-08-21", mode="paper",
                         exit_fill=_fill(price=214.975, qty=100.0, when="2026-08-22T20:20:00+00:00"),
                         exit_reason="TP hit", exit_timestamp="2026-08-22T20:20:00+00:00")

    _open(trade_id="NVDA-2026-08-22", mode="paper",
          entry_timestamp="2026-08-22T20:07:58+00:00", **_DUP_KW)
    closed2 = close_executed_trade(trade_id="NVDA-2026-08-22", mode="paper",
                                   exit_fill=_fill(price=214.975, qty=100.0, when="2026-08-22T20:25:38+00:00"),
                                   exit_reason="TP hit", exit_timestamp="2026-08-22T20:25:38+00:00")
    assert closed2["outcome"] == "WIN"                       # written, not blocked
    assert closed2["possible_duplicate_of"] == ["NVDA-2026-08-21"]
    assert "possible_duplicate_reason" in closed2


# ─── mark_superseded / log_correction — the fix, not just the guard ─────────

def test_mark_superseded_flags_metadata_only(isolated_ledger):
    """Every substantive field is untouched — only metadata is added."""
    _open(trade_id="NVDA-2026-08-22", entry_timestamp="2026-08-22T20:07:58+00:00", **_DUP_KW)
    before = close_executed_trade(trade_id="NVDA-2026-08-22", mode="sim",
                                  exit_fill=_fill(price=214.975, qty=100.0, when="2026-08-22T20:25:38+00:00"),
                                  exit_reason="TP hit", exit_timestamp="2026-08-22T20:25:38+00:00")

    after = mark_superseded(trade_id="NVDA-2026-08-22", mode="sim",
                            superseded_by="NVDA-2026-08-21",
                            reason="wall-clock trade_id artifact predating 52a24e3")

    assert after["superseded"] is True
    assert after["superseded_by"] == "NVDA-2026-08-21"
    assert after["superseded_reason"] == "wall-clock trade_id artifact predating 52a24e3"
    assert "superseded_at" in after
    for k in ("r_realized", "outcome", "entry_fill", "exit_fill", "stop_loss",
              "exit_timestamp", "exit_reason", "trade_id", "mode"):
        assert after[k] == before[k], f"{k} changed — substantive field was touched"


def test_mark_superseded_is_idempotent(isolated_ledger):
    """A retried correction is a no-op, same discipline as record_partial_exit."""
    _open(trade_id="NVDA-1", **_DUP_KW)
    close_executed_trade(trade_id="NVDA-1", mode="sim", exit_fill=_fill(price=214.975, when=T1),
                         exit_reason="x", exit_timestamp=T1)
    args = dict(trade_id="NVDA-1", mode="sim", superseded_by="NVDA-2", reason="dup")
    a = mark_superseded(**args)
    b = mark_superseded(**args)
    assert a == b
    assert len(_lines(isolated_ledger)) == 1


def test_mark_superseded_conflicting_correction_raises(isolated_ledger):
    """A supersession is decided once, not re-litigated — same shape as
    conflicting reclose."""
    _open(trade_id="NVDA-1", **_DUP_KW)
    close_executed_trade(trade_id="NVDA-1", mode="sim", exit_fill=_fill(price=214.975, when=T1),
                         exit_reason="x", exit_timestamp=T1)
    mark_superseded(trade_id="NVDA-1", mode="sim", superseded_by="NVDA-2", reason="dup")
    with pytest.raises(LedgerError, match="already marked superseded"):
        mark_superseded(trade_id="NVDA-1", mode="sim", superseded_by="NVDA-3", reason="dup")


def test_mark_superseded_requires_a_closed_row(isolated_ledger):
    _open(trade_id="NVDA-1", **_DUP_KW)
    with pytest.raises(LedgerError, match="not closed"):
        mark_superseded(trade_id="NVDA-1", mode="sim", superseded_by="NVDA-2", reason="dup")


def test_mark_superseded_unknown_trade_id_raises(isolated_ledger):
    with pytest.raises(LedgerError, match="does not exist"):
        mark_superseded(trade_id="GHOST", mode="sim", superseded_by="NVDA-2", reason="dup")


def test_log_correction_matches_repo_idiom(isolated_ledger):
    """Shape matches the two DECISION_LOG/CORRECTION marker rows already in
    data/decision_logs/decisions_2026_08.jsonl (spec_021_log_correction), and
    it is never scored (r_realized/outcome stay None, so build_cockpit's
    `r_realized is None: continue` and gap_log's `outcome in (None, "OPEN")`
    both already skip it without any special-casing)."""
    _open(trade_id="NVDA-2026-08-22", **_DUP_KW)
    close_executed_trade(trade_id="NVDA-2026-08-22", mode="sim",
                         exit_fill=_fill(price=214.975, when=T1), exit_reason="x", exit_timestamp=T1)
    n_before = len(_lines(isolated_ledger))

    rec = log_correction(mode="sim", system="DAYTRADE",
                         superseded_trade_id="NVDA-2026-08-22",
                         superseding_trade_id="NVDA-2026-08-21",
                         reason="wall-clock trade_id artifact predating 52a24e3",
                         as_of=T1)

    assert rec["pair"] == "DECISION_LOG"
    assert rec["direction"] == "CORRECTION"
    assert rec["risk_pct"] == 0
    assert rec["record_kind"] == "correction"
    assert rec["superseded_trade_id"] == "NVDA-2026-08-22"
    assert rec["superseding_trade_id"] == "NVDA-2026-08-21"
    assert rec["r_realized"] is None and rec["outcome"] is None

    lines = _lines(isolated_ledger)
    assert len(lines) == n_before + 1                          # appended, not rewritten
    original = json.loads(lines[0])
    assert original["trade_id"] == "NVDA-2026-08-22"           # untouched by the append


# ─── superseded rows: readable, but excluded from every aggregating reader ──

def _inject_duplicate_row(ledger_root, mode, source_trade_id, new_trade_id, when):
    """Simulate a pre-existing duplicate the way the real one arose: written
    directly, bypassing the guard added in this change (the guard did not
    exist when 52a24e3's row was written)."""
    src = find_executed_trade(source_trade_id, mode)
    dup = dict(src)
    dup["trade_id"] = new_trade_id
    el._append_durable(el.ledger_path(mode, when), dup)


def test_superseded_excluded_from_gap_aggregation_but_readable(isolated_ledger):
    import gap_log

    _open(trade_id="NVDA-2026-08-21", mode="sim", entry_timestamp=T0, **_DUP_KW)
    close_executed_trade(trade_id="NVDA-2026-08-21", mode="sim",
                         exit_fill=_fill(price=214.975, when=T1), exit_reason="x", exit_timestamp=T1)
    _inject_duplicate_row(isolated_ledger, "sim", "NVDA-2026-08-21", "NVDA-2026-08-22", T1)
    mark_superseded(trade_id="NVDA-2026-08-22", mode="sim",
                    superseded_by="NVDA-2026-08-21", reason="wall-clock artifact")

    _open(trade_id="NVDA-2026-08-21", mode="paper", entry_timestamp=T0, **_DUP_KW)
    close_executed_trade(trade_id="NVDA-2026-08-21", mode="paper",
                         exit_fill=_fill(price=214.975, when=T1), exit_reason="x", exit_timestamp=T1)

    gaps, _ = gap_log.compute_gaps(gap_log.load_ledger_rows("sim"), gap_log.load_ledger_rows("paper"))
    matched_ids = {g.trade_id for g in gaps}
    assert "NVDA-2026-08-21" in matched_ids
    assert "NVDA-2026-08-22" not in matched_ids                # excluded from scoring

    # still readable, still visible as history:
    row = find_executed_trade("NVDA-2026-08-22", "sim")
    assert row is not None and row["superseded"] is True
    assert row["r_realized"] is not None                       # the number is not erased


def test_superseded_visible_in_cockpit_but_not_counted(isolated_ledger, monkeypatch):
    import build_cockpit
    monkeypatch.setattr(build_cockpit, "LEDGER_ROOT", isolated_ledger)

    _open(trade_id="NVDA-2026-08-21", mode="sim", entry_timestamp=T0, **_DUP_KW)
    close_executed_trade(trade_id="NVDA-2026-08-21", mode="sim",
                         exit_fill=_fill(price=214.975, when=T1), exit_reason="x", exit_timestamp=T1)
    _inject_duplicate_row(isolated_ledger, "sim", "NVDA-2026-08-21", "NVDA-2026-08-22", T1)
    mark_superseded(trade_id="NVDA-2026-08-22", mode="sim",
                    superseded_by="NVDA-2026-08-21", reason="wall-clock artifact")

    rows = build_cockpit.closed_trades()
    by_id = {r["trade_id"]: r for r in rows}
    assert set(by_id) == {"NVDA-2026-08-21", "NVDA-2026-08-22"}         # both visible
    assert by_id["NVDA-2026-08-22"]["superseded"] is True
    assert by_id["NVDA-2026-08-21"]["superseded"] is False
    counted = [r for r in rows if not r["superseded"]]                  # the page's "N closed"
    assert len(counted) == 1
