"""RULE 1 GUARD — "one implementation": every decision path goes through
`stockfish_exit.decide_exit`, and the harness that replays it is
`daytrade.ceiling.simulate` (plus the live cockpit, `daytrade.runner.run`).

This exists because that rule was policy, not code, and a policy with no
test gets violated silently: a subagent once wrote `simulate_with_vol()`, a
parallel reimplementation of the per-bar decision loop, because the
canonical simulator could not pass `atr`/`vol_k` at the time. Its numbers
were incomparable to every other measurement in the repo for days before
anyone noticed (see `ceiling.py::simulate`'s own comment on this). This test
gives the rule teeth: it AST-walks the source tree looking for any function
that both (a) calls `stockfish_exit.decide_exit` and (b) drives it through a
loop that steps price and applies actions — i.e. a second per-bar/per-cycle
decision-replay loop — and fails unless that exact function is named in
ALLOWLIST below, with a written reason.

SCOPE. Only `stockfish_exit.decide_exit` is tracked. `sovereign/forex/
exit_machine.py` defines its own, unrelated `decide_exit` for the FX carry
lane (a different strategy, a different subsystem, a different rule-1 of its
own) — matching on the bare name would flag `sovereign/forex/fast_backtester.py`
and `sovereign/execution/forex_exit_manager.py` for a violation they do not
commit, so this scanner resolves `decide_exit` back to its IMPORT SOURCE
(`from stockfish_exit import decide_exit`, or `import stockfish_exit` +
`stockfish_exit.decide_exit(...)`) before counting a call.

DETECTION. "Drives it through a loop" is decided per FUNCTION (not per
statement) by:
  1. the function calls a name that resolves to stockfish_exit's decide_exit
     (module-level OR function-local import, either binding form above), AND
  2. somewhere inside the function there is a `for` or `while` loop whose own
     body assigns `<something>.price = ...` or calls `apply_action(s)` /
     `apply_actions(s)` — the actual signature of a loop that steps a
     TradeState through price and lets the engine decide, rather than a loop
     that merely happens to be nearby.
`for`/`while` are both in scope on purpose: a backtest replay loops with
`for` over recorded bars, but the live cockpit's decision loop is `while
True:` over polled quotes, and both are "one implementation" violations if a
second one appears with either shape.

Deliberately NOT in scope: bare module-level `if __name__ == "__main__":`
demo/smoke blocks (e.g. stockfish_exit.py's own 8-tick print demo) — they are
not inside a function, are not a harness anything else runs, and produce no
trade-outcome metric anyone could compare against another loop's.

The test asserts the discovered set equals ALLOWLIST exactly: an
undocumented finding fails (new/rogue loop), and a stale allowlist entry the
scanner no longer finds also fails (dead permission is worse than none —
same "no silent, unused exemptions" spirit as data/agent/wiring_allowlist.yaml's
"every entry MUST have a non-empty reason" convention, which this mirrors).
"""
from __future__ import annotations

import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Directories the task specifies, plus the repo root (root is NOT walked
# recursively — that would re-walk every dir below again and also pull in
# unrelated trees like data/, Plans/, docs/, .claude/worktrees/; "the repo
# root" means the .py files sitting directly in it).
SCAN_DIRS = ("daytrade", "scripts", "sovereign", "backtester", "execution")
EXCLUDE_DIR_NAMES = {".git", ".claude", "__pycache__"}

STOCKFISH_MODULE = "stockfish_exit"
APPLY_CALL_NAMES = {"apply_action", "apply_actions"}


def _is_test_file(path: Path) -> bool:
    name = path.name
    return name.startswith("test_") or name.endswith("_test.py")


def _iter_scan_files():
    """Every .py under SCAN_DIRS (recursive) plus direct root-level .py
    files, excluding .git/.claude/__pycache__ and test files themselves."""
    seen: set[Path] = set()

    def _emit(p: Path):
        if _is_test_file(p):
            return
        rp = p.resolve()
        if rp in seen:
            return
        seen.add(rp)
        yield p

    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if any(part in EXCLUDE_DIR_NAMES for part in p.parts):
                continue
            yield from _emit(p)

    for p in sorted(ROOT.glob("*.py")):
        yield from _emit(p)


def _stockfish_bindings(stmts) -> tuple[set[str], set[str]]:
    """Scan a flat list of statements (module body, or one function's body)
    for imports that bind a name to `stockfish_exit.decide_exit` itself, or
    to the `stockfish_exit` module (for `module.decide_exit(...)` calls).
    Only looks at these TOP-LEVEL statements — matches normal Python scoping:
    a function-local import is visible only inside that function."""
    decide_exit_names: set[str] = set()
    module_alias_names: set[str] = set()
    for stmt in stmts:
        if isinstance(stmt, ast.ImportFrom):
            mod = stmt.module or ""
            if mod == STOCKFISH_MODULE or mod.endswith("." + STOCKFISH_MODULE):
                for alias in stmt.names:
                    if alias.name == "decide_exit":
                        decide_exit_names.add(alias.asname or alias.name)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name == STOCKFISH_MODULE or alias.name.endswith("." + STOCKFISH_MODULE):
                    module_alias_names.add(alias.asname or alias.name.split(".")[-1])
    return decide_exit_names, module_alias_names


def _calls_decide_exit(func: ast.AST, decide_exit_names: set[str],
                       module_alias_names: set[str]) -> bool:
    for n in ast.walk(func):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name) and f.id in decide_exit_names:
            return True
        if (isinstance(f, ast.Attribute) and f.attr == "decide_exit"
                and isinstance(f.value, ast.Name) and f.value.id in module_alias_names):
            return True
    return False


def _loop_steps_and_applies(loop: ast.AST) -> bool:
    """True if this single loop's own body assigns `.price` or calls
    apply_action(s) — the tell of a loop that steps a TradeState through
    price and applies the engine's actions."""
    for n in ast.walk(loop):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in APPLY_CALL_NAMES:
                return True
        elif isinstance(n, (ast.Assign, ast.AugAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr == "price":
                    return True
    return False


def _scan_file(path: Path) -> list[str]:
    """Qualified names ('rel/path.py::func') of every function in this file
    that both calls stockfish_exit.decide_exit and contains a loop that
    steps price / applies actions."""
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []

    mod_de_names, mod_alias_names = _stockfish_bindings(tree.body)

    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_de_names, local_alias_names = _stockfish_bindings(node.body)
        de_names = mod_de_names | local_de_names
        alias_names = mod_alias_names | local_alias_names
        if not de_names and not alias_names:
            continue
        if not _calls_decide_exit(node, de_names, alias_names):
            continue
        drives = any(_loop_steps_and_applies(n) for n in ast.walk(node)
                    if isinstance(n, (ast.For, ast.While)))
        if drives:
            rel = path.relative_to(ROOT).as_posix()
            findings.append(f"{rel}::{node.name}")
    return findings


def discover() -> set[str]:
    found: set[str] = set()
    for f in _iter_scan_files():
        found.update(_scan_file(f))
    return found


# ---------------------------------------------------------------------------
# ALLOWLIST — every function the scan is expected to find, with a written
# reason. An entry with an empty reason fails the test (see module docstring
# — mirrors data/agent/wiring_allowlist.yaml's "every entry MUST have a
# non-empty reason" convention). A finding NOT in this list fails the test.
# An entry in this list the scan no longer finds ALSO fails the test — a
# stale exemption is a silent, unverified hole, not a safe default.
# ---------------------------------------------------------------------------
ALLOWLIST: dict[str, str] = {
    "daytrade/ceiling.py::simulate": (
        "The canonical replay harness (spec 008). Every ceiling/backtest "
        "measurement in the repo goes through this one simulate() — this is "
        "the harness named in ruling 1 itself."
    ),
    "daytrade/runner.py::run": (
        "The live cockpit. Fetches one quote per cycle, feeds it to "
        "decide_exit exactly once, applies the result via apply_actions. "
        "This IS the production decision path (a `while True:` poll, not a "
        "backtest `for`), not a competing one."
    ),
    "daytrade/backtest.py::replay_session": (
        "Spec 005's DoD replay: feeds a recorded LIVE session log back "
        "through decide_exit/apply_actions and diffs the resulting actions "
        "against what the runner actually logged. Per its own module "
        "docstring this IS 'the test that proves the architecture' (ruling "
        "1) — it delegates every decision to decide_exit with no local "
        "decision logic of its own, so it is verification, not a second "
        "implementation."
    ),
    "daytrade/shadow.py::run_shadow": (
        "Spec 014's shadow policy tournament: replays the SAME recorded "
        "cycle stream through every candidate policy on its own state copy, "
        "purely to record what each WOULD have done — ShadowAction cannot "
        "reach an execution surface (accessing .kind raises). Delegates to "
        "decide_exit/apply_actions; its accounting basis is explicitly "
        "documented in the module docstring as non-comparable to ceiling.py's "
        "('review finding 9'), which is exactly the discipline that was "
        "missing when simulate_with_vol()'s numbers went unnoticed."
    ),
    "daytrade/four_books.py::run_book": (
        "Spec 023's four-books harness: replays session bars through the "
        "SAME Stockfish exit mechanics under four operator-discretion "
        "regimes. Module docstring states the constraint directly — 'the "
        "engine is imported, never re-implemented (rule 1)'. Only entry "
        "permission and the urgency channel differ between books; decisions "
        "all come from decide_exit/apply_actions."
    ),
    "daytrade/stockfish_constitution.py::_self_test": (
        "The constitution's own property/fuzz test (run via `--self-test`): "
        "random-walks price through decide_exit across every policy and "
        "direction to prove no C00x rule ever fires on the engine's own "
        "legal output. It verifies the constitution's rules, computes no "
        "trading result, and lives beside the rules it tests rather than as "
        "a parallel backtest."
    ),
}


def test_no_second_decide_exit_loop():
    empty_reason = sorted(k for k, v in ALLOWLIST.items() if not v or not v.strip())
    assert not empty_reason, (
        f"ALLOWLIST entries must have a non-empty reason, got empty for: {empty_reason}"
    )

    discovered = discover()
    allowlisted = set(ALLOWLIST)

    unexplained = sorted(discovered - allowlisted)
    assert not unexplained, (
        "RULE 1 VIOLATION (\"one implementation\") — the function(s) below "
        "both call stockfish_exit.decide_exit and drive it through their own "
        "loop that steps price and applies actions. That is a second "
        "implementation of the per-bar decision loop — the exact failure "
        "mode that made simulate_with_vol()'s numbers incomparable to every "
        "other measurement in the repo for days.\n"
        "Fix: route the loop through daytrade/ceiling.py::simulate (backtest/"
        "replay) or daytrade/runner.py::run (live), OR — only if this is "
        "genuinely a distinct, legitimate, already-reviewed replay/"
        "verification path that delegates every decision to decide_exit — "
        "add it to ALLOWLIST in this file WITH A WRITTEN REASON.\n"
        f"  found, not allowlisted: {unexplained}"
    )

    stale = sorted(allowlisted - discovered)
    assert not stale, (
        "ALLOWLIST entries the scanner no longer finds — remove them, a "
        "stale exemption is a silent, unverified hole:\n"
        f"  {stale}"
    )


if __name__ == "__main__":
    for name in sorted(discover()):
        print(name)
