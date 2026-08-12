#!/usr/bin/env python3
"""Spec 021 mutation driver — carry buy gate.

Every row below is a real fault injected into a real source line, bound to a
named test that must go RED under it and GREEN again after revert. Same
red-then-green protocol as mutation_check_012.py.

SCOPE — read this before quoting a count. This is a PARTIAL set: 10 rows
covering the invariants the spec-021 remediation week actually touched
(contract schema, DD type, campaign mechanics, verdict staleness, position
sizing). It is NOT the full 27-row set described in specs/021_CARRY_BUY_GATE.md.
The uncovered rows are listed in UNCOVERED below so the gap is visible rather
than implied-away by a green run.

Phase 3 (diagnose_repro_gap.py) is deliberately absent: it is a runtime
assert invariant, not a fault-injection target, and the log records it as prose.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SP = ROOT / "sovereign" / "propfirm"
DP = ROOT / "data" / "propfirm"

# (test_id, file_to_mutate, old_str, new_str, description)
MUTATIONS = [
    # ---- Phase 1: firm contract schema + DD model -------------------------
    ("sovereign/propfirm/test_firm_contracts.py::test_contracts_load_and_validate",
     DP / "firm_contracts.yaml",
     "max_dd: {type: trailing, pct: 0.05, basis: balance, mark: close}",
     "max_dd: {type: static, pct: 0.05, basis: balance, mark: close}",
     "M1: CTI DD type reverted to static (the 2026-08-12 defect)"),

    ("sovereign/propfirm/test_firm_contracts.py::test_malformed_contract_rejected",
     SP / "firm_contracts.py",
     '_DD_TYPES = ("static", "trailing")',
     '_DD_TYPES = ("static", "trailing", "sideways")',
     "M2: illegal DD enum accepted instead of raising"),

    ("sovereign/propfirm/test_firm_contracts.py::test_no_daily_limit_means_daily_floor_never_binds",
     SP / "firm_contracts.py",
     "NO_DAILY_LIMIT_PCT = 1.0",
     "NO_DAILY_LIMIT_PCT = 0.05",
     "M3: absent daily limit silently becomes a 5% budget"),

    ("sovereign/propfirm/test_firm_contracts.py::test_alpha_swing_two_phase_targets",
     DP / "firm_contracts.yaml",
     "    - target_pct: 0.05\n      min_trading_days: 0\n      max_days: null\n  max_dd: {type: static, pct: 0.10, basis: balance, mark: close}\n  daily_dd: {pct: 0.05, basis: balance, mark: close}\n  permissions: {weekend_hold: true, overnight_hold: true, news_hold: true}\n  costs:\n    fee_usd: 490.0",
     "    - target_pct: 0.10\n      min_trading_days: 0\n      max_days: null\n  max_dd: {type: static, pct: 0.10, basis: balance, mark: close}\n  daily_dd: {pct: 0.05, basis: balance, mark: close}\n  permissions: {weekend_hold: true, overnight_hold: true, news_hold: true}\n  costs:\n    fee_usd: 490.0",
     "M4: alpha_swing phase-2 target transcribed as 10% not 5%"),

    # ---- Phase 2: evaluator / campaign mechanics --------------------------
    ("scripts/test_carry_buy_gate.py::test_trailing_on_cti_flips_verdict",
     SCRIPTS / "carry_buy_gate.py",
     'dd_base = peak if dd.type == "trailing" else 1.0',
     "dd_base = 1.0",
     "E1: trailing DD floor collapsed to static"),

    ("scripts/test_carry_buy_gate.py::test_zero_edge_centering_zeroes_the_mean",
     SCRIPTS / "carry_buy_gate.py",
     "    if center:\n        m = float(np.mean(rs))\n        rs = [r - m for r in rs]",
     "    if False:\n        m = float(np.mean(rs))\n        rs = [r - m for r in rs]",
     "E2: zero-edge control stops centering (control becomes a copy of real)"),

    ("scripts/test_carry_buy_gate.py::test_g5_red_below_80",
     SCRIPTS / "carry_buy_gate.py",
     "G5_MIN_N = 80",
     "G5_MIN_N = 1",
     "E3: paper sprint threshold cut from 80 to 1"),

    ("scripts/test_carry_buy_gate.py::test_g5_red_out_of_band",
     SCRIPTS / "carry_buy_gate.py",
     "G5_R_BAND = 0.25",
     "G5_R_BAND = 99.0",
     "E4: paper-vs-golden R band widened to accept anything"),

    # ---- Phase 4: daily verdict page --------------------------------------
    ("scripts/test_daily_verdict_page.py::test_eight_day_old_green_is_not_ready",
     SCRIPTS / "build_daily_verdict_page.py",
     "STALE_DAYS = 7",
     "STALE_DAYS = 30",
     "V1: stale-state window widened 7d -> 30d"),

    # ---- Phase 5: paper carry log -----------------------------------------
    ("scripts/test_paper_carry_log.py::test_r_short_direction_sign",
     SCRIPTS / "paper_carry_log.py",
     'sign = 1.0 if direction == "LONG" else -1.0',
     "sign = 1.0",
     "P1: SHORT direction sign dropped"),

    ("scripts/test_paper_carry_log.py::test_position_size_reconciliation_rejects_divergent_qty",
     SCRIPTS / "paper_carry_log.py",
     "if rel_diff > 0.01:",
     "if False:",
     "P2: position-size reconciliation check disabled"),
]

# Invariants from specs/021_CARRY_BUY_GATE.md with NO row above. Stated so a
# green run cannot be read as full coverage.
UNCOVERED = [
    "M5-M7: phase_index bounds, mark/basis preservation, open-position budget",
    "E5-E10: deadline handling, min_trading_days, two-phase campaign, bust/restart, "
    "G4 weekend violations, formatter control-pairing",
    "V2-V5: missing gate key, wrong gate names, one-red-gate, verdict-word-alone",
    "P3-P6: haircut import (no local literal), zero-risk rejection, double-close, "
    "G5 closed-only counting",
]


def run_test(test_id: str) -> bool:
    for pyc in ROOT.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    r = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", test_id, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return r.returncode == 0


def main() -> int:
    originals = {Path(m[1]): Path(m[1]).read_text() for m in MUTATIONS}
    rows, fails = [], 0
    try:
        for test_id, mod, old, new, desc in MUTATIONS:
            mod = Path(mod)
            src = originals[mod]
            n = src.count(old)
            if n != 1:
                rows.append((test_id, desc, f"PATCH ERROR: {n} matches for old_str"))
                fails += 1
                print(f"FAIL  {desc}  (patch matched {n} times, need exactly 1)", flush=True)
                continue
            mod.write_text(src.replace(old, new))
            red = not run_test(test_id)
            mod.write_text(src)
            green = run_test(test_id)
            ok = red and green
            fails += 0 if ok else 1
            rows.append((test_id, desc,
                         "RED under fault, GREEN after revert" if ok
                         else f"FAILED (red={red}, green_after_revert={green})"))
            print(("ok    " if ok else "FAIL  ") + f"{desc}", flush=True)
    finally:
        for p, src in originals.items():
            p.write_text(src)

    print()
    if fails:
        print(f"{fails}/{len(rows)} row(s) FAILED — log NOT written, "
              f"committed evidence preserved")
        return 1

    print(f"{len(rows)}/{len(rows)} rows verified RED-then-GREEN.")
    print(f"PARTIAL SET — {len(UNCOVERED)} uncovered invariant groups remain:")
    for u in UNCOVERED:
        print(f"  - {u}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
