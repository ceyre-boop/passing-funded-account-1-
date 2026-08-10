#!/usr/bin/env python3
"""Card 012 fault injection. Break each reducer/envelope/persistence invariant,
confirm the named test goes RED, restore byte-identical, confirm GREEN.
Evidence -> specs/012_MUTATION_LOG.md."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"
TE = DT / "trade_events.py"

MUTATIONS = [
    # ---- envelope ----
    ("test_trade_events.py::test_envelope_validates", TE,
     "if self.event_type not in EVENT_TYPES:", "if False:",
     "accept unknown event types"),
    ("test_trade_events.py::test_envelope_validates", TE,
     "if dt.tzinfo is None:", "if False:",
     "accept timezone-naive timestamps"),
    ("test_trade_events.py::test_envelope_validates", TE,
     "if len(obj) > MAX_PAYLOAD_STR:", "if False:",
     "drop the redaction bound on payload strings"),
    ("test_trade_events.py::test_envelope_roundtrips_through_dict", TE,
     "if extra:", "if False:",
     "accept unknown envelope fields in from_dict"),
    # ---- append ----
    ("test_trade_events.py::test_append_is_pure_and_contiguous", TE,
     "if event.sequence > n:", "if False:",
     "accept a sequence gap in append"),
    ("test_trade_events.py::test_append_duplicate_identical_is_noop_different_raises", TE,
     '''if (prior.event_type == event.event_type
                and prior.fingerprint == event.fingerprint):''',
     "if True:",
     "treat a conflicting duplicate as a harmless retry"),
    # ---- reducer ----
    ("test_trade_events.py::test_rebuild_requires_opened_first", TE,
     '''raise EventError(f"seq {e.sequence}: first event must be "
                                 f"POSITION_OPENED, got {e.event_type}")''',
     "pass",
     "drop the dedicated first-event-must-open check"),
    ("test_trade_events.py::test_rebuild_requires_opened_first", TE,
     'if e.event_type == "POSITION_OPENED":\n            raise EventError(f"seq {e.sequence}: POSITION_OPENED twice',
     'if False:\n            raise EventError(f"seq {e.sequence}: POSITION_OPENED twice',
     "allow a second POSITION_OPENED"),
    ("test_trade_events.py::test_rebuild_closed_is_terminal", TE,
     '''if mem.closed:
            raise EventError(f"seq {e.sequence}: {e.event_type} after "''',
     '''if False:
            raise EventError(f"seq {e.sequence}: {e.event_type} after "''',
     "accept events after POSITION_CLOSED"),
    ("test_trade_events.py::test_rebuild_rejects_gap_and_regression", TE,
     "if e.sequence != i:", "if False:",
     "accept a sequence break in rebuild"),
    ("test_trade_events.py::test_rebuild_rejects_unknown_schema_version", TE,
     "if e.schema_version != SCHEMA_VERSION:", "if False:",
     "best-effort parse an unknown schema version"),
    ("test_trade_events.py::test_stop_advance_never_loosens", TE,
     "if looser:", "if False:",
     "let STOP_ADVANCED loosen the stop"),
    ("test_trade_events.py::test_hwm_never_regresses", TE,
     "if not improved:", "if False:",
     "let HWM_UPDATED regress"),
    ("test_trade_events.py::test_partial_fill_requires_submission_and_keys_are_replay_safe", TE,
     "if key not in mem.pending_reduction_keys:", "if False:",
     "accept a fill with no matching submission"),
    ("test_trade_events.py::test_partial_fill_requires_submission_and_keys_are_replay_safe", TE,
     '''if key in mem.applied_reduction_keys:
                raise EventError(
                    f"seq {e.sequence}: fill for already-applied key {key!r} — "
                    "refusing the double-reduce (C005, persisted form)")''',
     '''if False:
                raise EventError(
                    f"seq {e.sequence}: fill for already-applied key {key!r} — "
                    "refusing the double-reduce (C005, persisted form)")''',
     "drop the applied-key double-reduce refusal (message-pinned test)"),
    ("test_trade_events.py::test_partial_fill_requires_submission_and_keys_are_replay_safe", TE,
     "mem.applied_reduction_keys = mem.applied_reduction_keys | {key}",
     "mem.applied_reduction_keys = mem.applied_reduction_keys",
     "stop persisting applied reduction keys (C005 persistence)"),
    # ---- golden path load-bearing pieces ----
    ("test_trade_events.py::test_golden_destroy_state_rebuild_identical_decision", TE,
     "if t in _REVISION_ADVANCING:", "if False:",
     "stop advancing state_revision in the reducer"),
    ("test_trade_events.py::test_golden_destroy_state_rebuild_identical_decision", TE,
     "rev = state.state_revision - sum(1 for a in actions if a.kind in MUTATING)",
     "rev = state.state_revision",
     "translator records keys against the wrong (post-apply) revision"),
    ("test_trade_events.py::test_golden_destroy_state_rebuild_identical_decision", TE,
     "        s.stage = Stage.SCALED         # the engine advances SCALED->RUNNER itself",
     "        pass                           # stage left at ENTERED",
     "adapter forgets the lifecycle stage"),
    ("test_trade_events.py::test_golden_destroy_state_rebuild_identical_decision", TE,
     "    s.sl = mem.sl", "    s.sl = s.sl",
     "adapter forgets the folded stop"),
    # ---- persistence ----
    ("test_trade_events.py::test_jsonl_log_roundtrip_and_torn_tail", TE,
     "if i == len(lines) - 1:", "if False:",
     "treat a torn tail as ordinary mid-log corruption"),
]


def run_test(test_id: str) -> bool:
    for pyc in (DT / "__pycache__", ROOT / "__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    r = subprocess.run([sys.executable, "-B", "-m", "pytest", f"daytrade/{test_id}", "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    originals = {p: p.read_text() for p in {m for _, m, _, _, _ in MUTATIONS}}
    rows, fails = [], 0
    try:
        for test_id, mod, old, new, desc in MUTATIONS:
            src = originals[mod]
            if src.count(old) != 1:
                rows.append((test_id, desc, f"PATCH ERROR: {src.count(old)} matches"))
                fails += 1
                continue
            mod.write_text(src.replace(old, new))
            red = not run_test(test_id)
            mod.write_text(src)
            green = run_test(test_id)
            ok = red and green
            fails += 0 if ok else 1
            rows.append((test_id, desc,
                         "RED under fault, GREEN after revert" if ok
                         else f"FAILED (red={red}, green-after-revert={green})"))
            print(("ok  " if ok else "FAIL") + f"  {test_id}  [{desc}]", flush=True)
    finally:
        for p, src in originals.items():
            p.write_text(src)

    out = ["# Card 012 — mutation evidence (Gate 2)",
           "",
           "Every reducer/envelope/persistence invariant from the promoted spec",
           "fault-injected: fault applied -> named test RED -> module restored",
           "byte-identical -> GREEN. The golden destroy-state->rebuild test has",
           "four rows of its own (revision fold, key-revision reconstruction,",
           "stage adapter, stop adapter) so the crash-recovery claim is not one",
           "monolithic assertion.",
           "",
           "| test | fault applied | result |", "|---|---|---|"]
    out += [f"| `{t}` | {d} | {res} |" for t, d, res in rows]
    out += ["", f"**{len(rows) - fails}/{len(rows)} rows verified.**"]
    (ROOT / "specs" / "012_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/012_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
