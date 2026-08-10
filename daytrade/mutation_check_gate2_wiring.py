#!/usr/bin/env python3
"""Gate 2 wiring fault injection. Break the runner's event emission — the
append itself, the append-before-apply ordering, the pre-apply revision base,
the resume key threading, the pre-validation gate, and the directory fsync —
confirm the named test goes RED, restore byte-identical, confirm GREEN.
-> specs/GATE2_WIRING_MUTATION_LOG.md."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"
RN, TE = DT / "runner.py", DT / "trade_events.py"

_ORDER_OLD = """        for ev in events[n_before:]:
            event_log.append(ev)

        apply_actions(s, actions, applied_reduction_keys)"""
_ORDER_NEW = """        apply_actions(s, actions, applied_reduction_keys)

        for ev in events[n_before:]:
            event_log.append(ev)"""

_PREVAL_OLD = """        from stockfish_constitution import enforce as _enforce
        _enforce(s, actions=actions)"""
_PREVAL_NEW = """        from stockfish_constitution import enforce as _enforce
        actions_batch_unchecked = actions"""

MUTATIONS = [
    ("test_runner_event_wiring.py::test_event_stream_agrees_with_session_log", RN,
     "for ev in events[n_before:]:", "for ev in []:",
     "stop appending the cycle's events"),
    ("test_runner_event_wiring.py::test_append_happens_before_apply", RN,
     _ORDER_OLD, _ORDER_NEW,
     "append AFTER apply (the crash window the spec forbids)"),
    ("test_runner_event_wiring.py::test_event_stream_agrees_with_session_log", RN,
     "post_apply=False)", "post_apply=True)",
     "wrong revision base at the pre-apply call site (keys record ...:-1)"),
    ("test_runner_event_wiring.py::test_destroy_and_resume_matches_unbroken", RN,
     "applied_reduction_keys: set = set(mem.applied_reduction_keys)",
     "applied_reduction_keys: set = set()",
     "resume with a fresh key set instead of the reconstructed one (C005 lost)"),
    ("test_runner_event_wiring.py::test_constitution_refusal_appends_nothing", RN,
     _PREVAL_OLD, _PREVAL_NEW,
     "drop the pure pre-validation gate (the log can claim refused actions)"),
    ("test_runner_event_wiring.py::test_open_trade_refuses_non_resume_start", RN,
     "elif events:", "elif False:",
     "silently fork an open trade's history on a non-resume start"),
    ("test_runner_event_wiring.py::test_first_append_fsyncs_the_directory", TE,
     "if created:", "if False:",
     "skip the directory fsync on log creation"),
    ("test_runner_event_wiring.py::test_closed_trade_rotates_aside_on_fresh_start", RN,
     "if events and not resume and rebuild(events).closed:", "if False:",
     "refuse a fresh start over a CLOSED trade instead of rotating it aside"),
    ("test_runner_event_wiring.py::test_torn_tail_tolerated_on_resume_refused_otherwise", RN,
     "    except TornTailError:\n        if not resume:\n            raise",
     "    except TornTailError:\n        if True:\n            raise",
     "crash unhelpfully on resume over a torn tail (the exact defended crash)"),
    ("test_runner_event_wiring.py::test_pending_submission_refuses_resume", RN,
     "if mem.pending_reduction_keys:", "if False:",
     "resume past an unreconciled submission (013's window, guessed at)"),
    ("test_runner_event_wiring.py::test_torn_tail_tolerated_on_resume_refused_otherwise", RN,
     "removed = event_log.truncate_torn_tail()      # or the next append would",
     "removed = 0                                   # or the next append would",
     "skip truncating the torn fragment (the next append concatenates onto it)"),
    ("test_runner_event_wiring.py::test_dry_run_carries_the_clock_c004", RN,
     "_enforce(s, _a, applied_keys=frozenset(applied_reduction_keys),\n                     now_et=s.now_et)",
     "_enforce(s, _a, applied_keys=frozenset(applied_reduction_keys))",
     "dry-run drops the clock (review finding 1: false events go durable)"),
    ("test_runner_event_wiring.py::test_per_action_dry_run_catches_what_the_batch_check_cannot", RN,
     "        for _a in actions:",
     "        for _a in []:",
     "drop the per-action dry-run (review finding 2: it was dead weight)"),
    ("test_runner_event_wiring.py::test_resume_the_logs_plan_wins_over_a_doctored_cli_plan", RN,
     "plan = dict(mem.plan)      # the trade's OWN recorded plan wins — the log knows",
     "pass                       # the trade's OWN recorded plan wins — the log knows",
     "resume trusts the CLI plan over the log (review finding 3)"),
    ("test_runner_event_wiring.py::test_newline_terminated_garbage_final_line_is_corruption_not_torn", TE,
     "if i == len(lines) - 1 and not complete:   # torn tail",
     "if i == len(lines) - 1:   # torn tail",
     "grant complete-but-corrupt final lines the tear exemption (finding 4)"),
]


def run_test(test_id: str) -> bool:
    for pyc in (DT / "__pycache__", ROOT / "__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    r = subprocess.run([sys.executable, "-B", "-m", "pytest", f"daytrade/{test_id}", "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
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

    out = ["# Gate 2 wiring — mutation evidence",
           "",
           "The runner's event emission fault-injected end to end: the append",
           "itself, append-before-apply ordering, the pre-apply revision base",
           "(key ...:1 pin), resume key threading through the TP2 kill point,",
           "the pure pre-validation gate, the open-trade fork refusal, and the",
           "directory fsync on creation. Each fault -> named test RED ->",
           "restore byte-identical -> GREEN. Run drivers sequentially only.",
           "",
           "| test | fault applied | result |", "|---|---|---|"]
    out += [f"| `{t}` | {d} | {res} |" for t, d, res in rows]
    out += ["", f"**{len(rows) - fails}/{len(rows)} rows verified.**"]
    if fails:
        print(f"\n{fails} row(s) FAILED — log NOT written; committed evidence preserved")
        return 1
    (ROOT / "specs" / "GATE2_WIRING_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/GATE2_WIRING_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
