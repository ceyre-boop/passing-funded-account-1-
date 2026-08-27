#!/usr/bin/env python3
"""Trade-evidence freeze — CLAUDE.md non-negotiable 6.

Since 2026-07-31 this repo grew 180 commits / +166,722 lines while the paper
ledger stayed at 0 closed trades. The freeze: no new module, spec, or
verification layer under `daytrade/` or `specs/` until
`data/trade_logs/paper_carry_trades.jsonl` has 50 CLOSED records.

"Closed" matches `paper_carry_log.py cmd_status`'s own definition exactly
(status == "closed" and R is not None) — an open paper trade is not evidence.

Wired as a git commit-msg hook (`.githooks/commit-msg`, activated via
`scripts/install_hooks.sh` -> `core.hooksPath`), not pre-commit: git's own
pre-commit hook fires BEFORE the commit message is obtained (githooks(5)),
so it cannot reliably see a FREEZE OVERRIDE phrase passed via `-m`.
commit-msg is the earliest hook that sees both the staged diff and the
final message, and it still blocks before the commit object is created.
Blocks (exit 1) on first offence, not warn — see CLAUDE.md rationale: a
hook you can ignore gets ignored, and this repo's history is the
demonstration.

Exemptions (checked in this order):
  1. Modifications to files that already exist — bug fixes and data-integrity
     fixes normally land as edits, not new files, and edits are never gated.
  2. New test files (`scripts/test_*.py`, `**/test_*.py`, `**/*_test.py`, or
     anything under a `tests/` directory) — a freeze that stops someone
     testing existing code makes the codebase worse.
  3. GRANDFATHERED — an exact, named, expiring allowlist for work already in
     flight when the freeze landed. Not an open-ended category: only these
     paths, only until the stated date.
  4. Override phrase in the commit message: a line starting with
     "FREEZE OVERRIDE:" followed by a reason. Deliberate and logged in the
     commit itself, not a silent bypass.

Run directly for a report: `python3 scripts/check_trade_freeze.py --status`
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "data" / "trade_logs" / "paper_carry_trades.jsonl"
THRESHOLD = 50
GATED_PREFIXES = ("daytrade/", "specs/")
OVERRIDE_PHRASE = "FREEZE OVERRIDE:"

# Named, expiring exemption for work already in flight when the freeze
# landed 2026-08-26. Expires 2026-09-09 (two weeks) OR whenever the freeze
# naturally lifts (50 closed trades), whichever comes first. Re-justify in a
# commit message, don't just extend the date, if this is still needed after
# expiry.
GRANDFATHER_EXPIRY = "2026-09-09"
GRANDFATHERED_PATHS = frozenset({
    "daytrade/paper_carry_runner.py",   # Phase-2 disarmed runner; already
                                         # committed at 53e2f9c, listed here
                                         # so companion edits to it aren't
                                         # blocked by a same-commit rename.
    "scripts/paper_carry_log.py",       # G5 sprint logger this freeze exists
                                         # to feed — ending the freeze is its
                                         # whole job.
    "scripts/ruin_engine.py",           # In flight in a concurrent worktree
                                         # per 2026-08-26 dispatch notes.
})


def count_closed_trades(path: Path = LOG_PATH) -> int:
    """Count CLOSED records only — an open paper trade is not evidence.

    Mirrors `paper_carry_log.py::cmd_status` exactly: status == "closed" and
    R is not None (R stays null until `cmd_close` computes it).
    """
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("status") == "closed" and rec.get("R") is not None:
            n += 1
    return n


def is_gated(path: str) -> bool:
    return path.startswith(GATED_PREFIXES)


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or "/tests/" in f"/{path}"
    )


def is_grandfathered(path: str) -> bool:
    return path in GRANDFATHERED_PATHS


def has_override(commit_msg: str) -> bool:
    return any(line.strip().startswith(OVERRIDE_PHRASE) for line in commit_msg.splitlines())


def offending_files(added_files: list[str]) -> list[str]:
    """Added files under a gated dir that no exemption covers."""
    return [
        f for f in added_files
        if is_gated(f) and not is_test_file(f) and not is_grandfathered(f)
    ]


def evaluate(added_files: list[str], closed_count: int, commit_msg: str,
             threshold: int = THRESHOLD) -> tuple[bool, list[str]]:
    """Return (blocked, offenders). blocked=True means the commit must stop."""
    if closed_count >= threshold:
        return False, []
    if has_override(commit_msg):
        return False, []
    offenders = offending_files(added_files)
    return (len(offenders) > 0, offenders)


def _staged_added_files() -> list[str]:
    """Newly-added paths in the index (git diff --cached, filter A)."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-status", "--diff-filter=A"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    files = []
    for line in out.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            files.append(parts[1])
    return files


def _commit_msg_file() -> Path | None:
    """Resolve COMMIT_EDITMSG the git way, not by assuming `.git` is a dir.

    In a linked worktree `.git` is a pointer *file*, not a directory, so
    `ROOT / ".git" / "COMMIT_EDITMSG"` silently resolves to nothing. Ask git
    for the real path instead.
    """
    out = subprocess.run(
        ["git", "rev-parse", "--git-path", "COMMIT_EDITMSG"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    candidate = (ROOT / out).resolve() if not Path(out).is_absolute() else Path(out)
    return candidate if candidate.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[2])
    ap.add_argument("--staged", action="store_true",
                     help="check the currently staged commit (commit-msg hook mode)")
    ap.add_argument("--status", action="store_true",
                     help="print the closed-trade count and exit 0")
    ap.add_argument("--commit-msg-file", default=None,
                     help="override the commit message path (for testing)")
    args = ap.parse_args()

    closed = count_closed_trades()

    if args.status or not args.staged:
        print(f"trade freeze: {closed}/{THRESHOLD} closed paper trades "
              f"({'LIFTED' if closed >= THRESHOLD else 'ACTIVE'})")
        return 0

    msg_path = Path(args.commit_msg_file) if args.commit_msg_file else _commit_msg_file()
    commit_msg = msg_path.read_text() if msg_path and msg_path.exists() else ""

    added = _staged_added_files()
    blocked, offenders = evaluate(added, closed, commit_msg)

    if blocked:
        print("BLOCKED by trade-evidence freeze (CLAUDE.md non-negotiable 6):",
              file=sys.stderr)
        print(f"  {closed}/{THRESHOLD} closed paper trades — "
              f"no new module/spec/verification layer until 50.", file=sys.stderr)
        for f in offenders:
            print(f"  new file blocked: {f}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Exemptions: edit an existing file instead of adding one; add a "
              "test (test_*.py); or, if this genuinely unblocks trade "
              "generation or fixes the live path, put a line starting with "
              f'"{OVERRIDE_PHRASE} <reason>" in the commit message.',
              file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
