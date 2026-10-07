#!/usr/bin/env python3
"""Spec 026 fault-injection driver — regime.py, scorecard.py, splits.py.

For every invariant row: apply the fault, confirm the NAMED test goes RED,
revert byte-identical, confirm GREEN. A green suite alone is not evidence; this
loop is the repo's actual Definition of VERIFIED (CLAUDE.md): "deliberate
violation of that invariant makes the suite fail".

TWO HARNESS BUGS THIS DRIVER DOES NOT REPRODUCE
-----------------------------------------------
The 2026-08-17 audit of `mutation_check_gate2_wiring.py` found both, and every
earlier driver still has them:

  1. A stale anchor (`PATCH ERROR: 0 matches`) was appended to the row list and
     counted as a failure, but `continue`d BEFORE the print — so the console
     showed every row as "ok" while the exit code said 1. An invariant whose
     anchor had drifted was silently no longer being verified. Here the PATCH
     ERROR prints loudly, as its own line, naming the anchor.
  2. A driver that crashes between writing a mutation and restoring it leaves
     FAULTED SOURCE on disk, which is exactly how `return 0.0` got committed
     into `execution_policy.pending_exposure` and shipped a red suite. This
     driver refuses to start if the tree under daytrade/ is already dirty, so a
     leftover mutation can never be mistaken for the baseline.

Run drivers SEQUENTIALLY ONLY — they mutate overlapping modules.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"

RG, SC, SP = (DT / f for f in ("regime.py", "scorecard.py", "splits.py"))

# (test, module, old, new, fault description)
MUTATIONS = [
    # ---------------------------------------------------------- regime.py
    ("test_regime.py::test_frame_reaching_past_now_et_is_refused", RG,
     'if last_ts.strftime("%H:%M") != ctx.now_et:', "if False:",
     "THE no-lookahead guard: let a frame containing bars after now_et be classified"),
    ("test_regime.py::test_unordered_frame_raises", RG,
     "if not idx.is_monotonic_increasing:", "if False:",
     "accept a time-reversed frame"),
    ("test_regime.py::test_zero_atr_refuses_rather_than_turning_thresholds_into_coin_flips", RG,
     "if atr5 is None or atr5 <= 0:", "if atr5 is None:",
     "allow a zero ATR — every threshold is in ATR units, so 0 makes '< 0.6*atr' impossible"),
    ("test_regime.py::test_nan_ohlc_is_corruption_not_forward_filled", RG,
     'if bars_5m[["Open", "High", "Low", "Close"]].isna().any().any():', "if False:",
     "accept NaN OHLC instead of calling a hole corruption"),
    ("test_regime.py::test_absent_pools_are_skipped_never_compared_against_zero", RG,
     "        lvl = pools.get(name)\n        if lvl is None:\n            continue\n        hit = [i for i, v in enumerate(highs) if float(v) > lvl]",
     "        lvl = pools.get(name) or 0.0\n        hit = [i for i, v in enumerate(highs) if float(v) > lvl]",
     "coerce an unavailable pool to 0.0 — every bar then 'sweeps' it (CLAUDE.md rule 3)"),
    ("test_regime.py::test_confidence_below_floor_falls_back_to_the_safe_regime", RG,
     "    if conf < CONF_FLOOR:", "    if False:",
     "remove the safe-default confidence floor (APEX #2 — never act on a guess)"),
    ("test_regime.py::test_incomplete_opening_range_does_not_fire_the_breakout_rule", RG,
     '    or_complete = len(or_bars) >= 6', '    or_complete = True',
     "call an incomplete opening range complete, firing the breakout rule pre-10:00"),
    ("test_regime.py::test_volume_average_excludes_today", RG,
     '    mask = (idx.strftime("%H:%M") == now_et) & (idx.date < day)',
     '    mask = (idx.strftime("%H:%M") == now_et)',
     "let today's own volume into its own baseline average (self-referential ratio)"),
    ("test_regime.py::test_daily_atr_uses_only_sessions_strictly_before_today", RG,
     "    prior = sorted({d for d in idx.date if d < day})[-(n + 1):]",
     "    prior = sorted({d for d in idx.date if d <= day})[-(n + 1):]",
     "include TODAY in the daily ATR — lookahead through the back door"),
    ("test_regime.py::test_policy_emits_only_v3_intent_names", RG,
     '    "CONTINUATION": "RIDE",      # let it run; participation is the point',
     '    "CONTINUATION": "TRAIL_WIDE",',
     "emit a renamed-away legacy policy name nothing downstream reads"),
    ("test_regime.py::test_atr_is_the_MEAN_true_range_not_the_max", RG,
     "    return _tr_series(bars).rolling(n).mean()",
     "    return _tr_series(bars).rolling(n).max()",
     "swap the ATR mean for a max (the furnace-era min->max class of fault)"),

    # -------------------------------------------------------- scorecard.py
    ("test_scorecard.py::test_lookahead_is_refused_at_the_boundary", SC,
     "if first <= ts:", "if False:",
     "let the forward window overlap the bar being graded"),

    # ----------------------------------------------------------- splits.py
    ("test_splits.py::test_sealed_002_requires_a_rule_version", SP,
     '    if not rule_version:\n        raise SealedSplitError(\n            "SEALED-002 needs rule_version=',
     '    if False:\n        raise SealedSplitError(\n            "SEALED-002 needs rule_version=',
     "read SEALED-002 without recording which rules produced the number"),
    ("test_splits.py::test_sealed_002_requires_an_unseal_reason", SP,
     '    if not unseal_reason:\n        raise SealedSplitError(\n            "SEALED-002 needs unseal_reason=',
     '    if False:\n        raise SealedSplitError(\n            "SEALED-002 needs unseal_reason=',
     "allow an accidental, unlogged holdout read"),
]


def run_test(test_id: str) -> bool:
    for pyc in (DT / "__pycache__", ROOT / "__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    r = subprocess.run([sys.executable, "-B", "-m", "pytest", f"daytrade/{test_id}",
                        "-q", "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return r.returncode == 0


def _tree_is_clean() -> tuple[bool, str]:
    r = subprocess.run(["git", "status", "--porcelain", "daytrade/"],
                       cwd=ROOT, capture_output=True, text=True, timeout=10)
    dirty = r.stdout.strip()
    return (not dirty), dirty


def main() -> int:
    clean, dirty = _tree_is_clean()
    if not clean:
        print("REFUSING TO RUN — daytrade/ is dirty:\n" + dirty)
        print("\nA driver that mutates a file already carrying uncommitted changes "
              "cannot restore a known-good baseline, and a crash mid-run would "
              "leave faulted source indistinguishable from your edits. That is how "
              "`return 0.0` reached execution_policy.pending_exposure and shipped a "
              "red suite (audit 2026-08-17). Commit or stash, then re-run.")
        return 2

    originals = {p: p.read_text() for p in {RG, SC, SP}}
    rows, fails = [], 0
    try:
        for test_id, mod, old, new, desc in MUTATIONS:
            src = originals[mod]
            n = src.count(old)
            if n != 1:
                # LOUD. The pre-existing drivers `continue` before this print,
                # which is how a drifted anchor silently stops verifying.
                print(f"FAIL  {test_id}  [{desc}]\n"
                      f"      PATCH ERROR: anchor matched {n} times in "
                      f"{mod.name} (expected exactly 1)\n"
                      f"      anchor: {old[:90]!r}", flush=True)
                rows.append((test_id, desc, f"PATCH ERROR: {n} matches"))
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

    out = ["# Spec 026 — mutation evidence (regime / scorecard / splits)",
           "",
           "Every invariant of the newly-built reward pathway, fault-injected end to",
           "end: the no-lookahead guards on both sides of the loop, the ATR-unit",
           "refusals, the never-coerce-None-to-zero rule, the safe-default confidence",
           "floor, the v3 policy vocabulary, and the SEALED-002 unseal conditions.",
           "Each fault -> named test RED -> restore byte-identical -> GREEN.",
           "",
           "| test | fault applied | result |", "|---|---|---|"]
    out += [f"| `{t}` | {d} | {res} |" for t, d, res in rows]
    out += ["", f"**{len(rows) - fails}/{len(rows)} rows verified.**"]
    if fails:
        print(f"\n{fails} row(s) FAILED — log NOT written; committed evidence preserved")
        return 1
    (ROOT / "specs" / "026_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows)}/{len(rows)} rows verified -> specs/026_MUTATION_LOG.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
