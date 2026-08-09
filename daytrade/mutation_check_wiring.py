#!/usr/bin/env python3
"""Constitution-wiring fault injection. Break each C004/C005/C007 threading
point in stockfish_exit.py, confirm the named wiring test goes RED, restore
byte-identical, confirm GREEN. Companion to mutation_check_019.py; evidence in
specs/WIRING_MUTATION_LOG.md."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SE = ROOT / "daytrade" / "stockfish_exit.py"

MUTATIONS = [
    ("test_constitution_wiring.py::test_c004_backwards_clock_refused_through_apply_action",
     "now_et=state.now_et)", "now_et=None)",
     "stop forwarding the clock into enforce()"),
    ("test_constitution_wiring.py::test_c007_action_after_exit_all_refused_through_apply_actions",
     "enforce(state, action, applied_keys=applied_keys, actions=batch,",
     "enforce(state, action, applied_keys=applied_keys, actions=None,",
     "stop forwarding the batch into enforce()"),
    ("test_constitution_wiring.py::test_c005_replayed_reduction_refused_through_apply_actions",
     "if key is not None and applied_keys is not None:", "if False:",
     "stop recording applied reduction keys in apply_actions"),
    ("test_constitution_wiring.py::test_c005_replayed_reduction_refused_through_apply_actions",
     "applied_keys=frozenset(applied_keys or ()), batch=actions)",
     "applied_keys=frozenset(), batch=actions)",
     "record keys but stop passing them to apply_action"),
]


def run_test(test_id: str) -> bool:
    for pyc in (ROOT / "daytrade" / "__pycache__", ROOT / "__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    r = subprocess.run([sys.executable, "-B", "-m", "pytest", f"daytrade/{test_id}", "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    src = SE.read_text()
    rows, fails = [], 0
    try:
        for test_id, old, new, desc in MUTATIONS:
            if src.count(old) != 1:
                rows.append((test_id, desc, f"PATCH ERROR: {src.count(old)} matches"))
                fails += 1
                continue
            SE.write_text(src.replace(old, new))
            red = not run_test(test_id)
            SE.write_text(src)
            green = run_test(test_id)
            ok = red and green
            fails += 0 if ok else 1
            rows.append((test_id, desc,
                         "RED under fault, GREEN after revert" if ok
                         else f"FAILED (red={red}, green-after-revert={green})"))
            print(("ok  " if ok else "FAIL") + f"  {test_id}  [{desc}]", flush=True)
    finally:
        SE.write_text(src)

    out = ["# Constitution wiring — mutation evidence",
           "",
           "Each C004/C005/C007 threading point deliberately broken; the named",
           "live-path test in test_constitution_wiring.py confirmed RED, module",
           "restored, test confirmed GREEN.",
           "",
           "| test | fault applied | result |", "|---|---|---|"]
    out += [f"| `{t}` | {d} | {res} |" for t, d, res in rows]
    out += ["", f"**{len(rows) - fails}/{len(rows)} rows verified.**"]
    (ROOT / "specs" / "WIRING_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/WIRING_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
