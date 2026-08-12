#!/usr/bin/env python3
"""SPLITS — the tune/sealed boundary. Single source of truth, imported everywhere.

THE FLAW THIS EXISTS TO PREVENT
-------------------------------
Spec 001's definition of done tunes the classifier over 60 sessions against
Colin's eye. Spec 005's job 2 then reports accuracy over those same 60 sessions.
Tuning and evaluating on identical data with ~35 free parameters does not produce
a measurement; it produces a number that will always look good. SANITY_AUDIT.md
is in this repo because that class of mistake already cost us once.

So the sessions are split ONCE, here, by date, and the boundary is a committed
constant. Everything that tunes, eyeballs, or measures a ceiling runs on the
TUNE split. The sealed split produces exactly one number, one time, after
`rule_version` is frozen.

WHY A DATE AND NOT A COUNT
--------------------------
yfinance serves a rolling window — "the oldest 40 of the last 60 sessions" names
a different set every morning, so a count-based split silently reshuffles the
holdout overnight. The boundary is therefore a literal date, pinned against the
frozen bar cache (see bars.py), and it is not edited. Ever.

IF THE RULES CHANGE AFTER THE SEAL IS BROKEN
--------------------------------------------
The holdout is burned. It does not get reused "just to check". A new holdout is
cut from sessions that did not exist at tuning time, and the old number is
retired with its `rule_version` attached.
"""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# THE BOUNDARY. Written 2026-08-03 against the 60-session NVDA 5m cache
# (2026-05-07 .. 2026-08-03). The 40th-oldest session is 2026-07-06.
# NEVER EDIT THIS LINE. Cutting a new holdout means adding a NEW constant below
# it with the date it was cut and why the previous one was burned.
TUNE_END = date(2026, 7, 6)
# ---------------------------------------------------------------------------

SEAL_LOG = ROOT / "data" / "daytrade" / "holdout_unseals.log"


class SealedSplitError(RuntimeError):
    """An attempt to read the holdout without meeting the conditions."""


def tune_sessions(sessions):
    """The 40 oldest. Everything that tunes or measures runs here."""
    return [s for s in sessions if s.day <= TUNE_END]


def sealed_sessions(sessions, *, unseal_reason: str = None,
                    rule_version: str = None, force: bool = False):
    """The held-out 20. Raises unless the seal conditions are genuinely met.

    Conditions:
      1. an explicit unseal_reason — no accidental reads
      2. a rule_version — the number is meaningless without knowing what produced it
      3. that rule_version is committed to git and unchanged for >= 1 commit,
         so the rules were frozen BEFORE the holdout was looked at, not after

    `force` bypasses condition 3 only (for a first-run bootstrap), still logs,
    and still requires 1 and 2. It exists so the check can never be quietly
    deleted for being inconvenient.
    """
    if not unseal_reason:
        raise SealedSplitError(
            "the sealed split needs unseal_reason=. It produces ONE number, ONCE, "
            "after rule_version is frozen. If you are tuning, you want tune_sessions().")
    if not rule_version:
        raise SealedSplitError(
            "the sealed split needs rule_version= — a holdout number without the "
            "rules that produced it cannot be interpreted later")

    frozen, detail = _rules_frozen(rule_version)
    if not frozen and not force:
        raise SealedSplitError(
            f"rules are not frozen: {detail}. Commit the rule_version and let it "
            "stand for at least one commit before reading the holdout. "
            "Pass force=True only for a deliberate bootstrap.")

    _log_unseal(unseal_reason, rule_version, forced=not frozen)
    return [s for s in sessions if s.day > TUNE_END]


def _rules_frozen(rule_version: str) -> tuple[bool, str]:
    """Is the working tree clean, and does rule_version appear in a commit?"""
    try:
        dirty = subprocess.run(["git", "status", "--porcelain", "daytrade/"],
                               cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        if dirty:
            return False, f"uncommitted changes under daytrade/ ({len(dirty.splitlines())} file(s))"
        hit = subprocess.run(["git", "log", "-1", "--format=%H", "-S", rule_version, "--", "daytrade/"],
                             cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        if not hit:
            return False, f"rule_version {rule_version!r} appears in no commit under daytrade/"
        return True, f"frozen at {hit[:8]}"
    except Exception as e:                       # git unavailable — refuse, don't assume
        return False, f"could not verify via git: {e!r}"


def _log_unseal(reason: str, rule_version: str, forced: bool) -> None:
    """Every read of the holdout is recorded. A quiet second look is the failure."""
    from datetime import datetime, timezone
    SEAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SEAL_LOG.open("a") as fh:
        tag = "FORCED\t" if forced else ""
        fh.write(f"{datetime.now(timezone.utc).isoformat()}\t{rule_version}\t"
                 f"{tag}{reason}\n")
    n = sum(1 for _ in SEAL_LOG.open())
    if n > 1:
        print(f"  !! HOLDOUT READ #{n}. It was designed to be read once. Every "
              f"additional read makes the number weaker. See {SEAL_LOG}")


def describe(sessions) -> str:
    t, s = tune_sessions(sessions), [x for x in sessions if x.day > TUNE_END]
    return (f"{len(sessions)} sessions | TUNE {len(t)} "
            f"({t[0].day}..{t[-1].day}) | SEALED {len(s)} "
            f"({s[0].day}..{s[-1].day}) — boundary {TUNE_END}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from bars import load_sessions
    ss = load_sessions("NVDA", "5m")
    print(describe(ss))
    try:
        sealed_sessions(ss)
    except SealedSplitError as e:
        print(f"\nguard works — sealed_sessions() refused:\n  {e}")
