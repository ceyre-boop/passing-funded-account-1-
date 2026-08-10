#!/usr/bin/env python3
"""Card 014 portfolio-guards fault injection (Gate 7). Break each guard and the
no-discovery rule; confirm RED, restore, GREEN.
-> specs/014_GUARDS_MUTATION_LOG.md."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"
PG = DT / "portfolio_guard.py"

MUTATIONS = [
    ("test_portfolio_guard.py::test_limits_validate_and_breaker_ordering_is_enforced", PG,
     "if self.emergency_flatten_r <= self.daily_loss_lock_r:", "if False:",
     "allow a breaker that makes the daily lock unreachable"),
    ("test_portfolio_guard.py::test_limits_validate_and_breaker_ordering_is_enforced", PG,
     "if not math.isfinite(self.open_risk_r) or self.open_risk_r < 0:",
     "if False:",
     "accept negative open risk as a fact"),
    ("test_portfolio_guard.py::test_nan_never_disarms_the_guards", PG,
     "if not math.isfinite(v) or v <= 0:", "if v <= 0:",
     "let a NaN limit silently disarm the backstop (review finding 3)"),
    ("test_portfolio_guard.py::test_nan_never_disarms_the_guards", PG,
     "if not math.isfinite(realized_today_r):", "if False:",
     "let a NaN day sail past both breakers (review finding 3)"),
    ("test_portfolio_guard.py::test_g001_total_open_risk", PG,
     "if total > limits.max_total_open_risk_r:", "if False:",
     "stop enforcing total open risk"),
    ("test_portfolio_guard.py::test_g002_per_symbol_exposure", PG,
     "if r > limits.max_per_symbol_exposure_r:", "if False:",
     "stop enforcing per-symbol exposure"),
    ("test_portfolio_guard.py::test_g003_correlated_exposure_only_for_supplied_groups", PG,
     "correlated_groups = correlated_groups or {}",
     'correlated_groups = correlated_groups or {"semis": ["NVDA", "AMD"]}',
     "invent a correlation group the caller never supplied"),
    ("test_portfolio_guard.py::test_g003_correlated_exposure_only_for_supplied_groups", PG,
     "if r > limits.max_correlated_exposure_r:", "if False:",
     "stop enforcing supplied correlated exposure"),
    ("test_portfolio_guard.py::test_g004_unprotected_count", PG,
     "if unprotected > limits.max_unprotected_count:", "if False:",
     "stop counting unprotected positions"),
    ("test_portfolio_guard.py::test_g005_daily_loss_lock_at_boundary", PG,
     "if realized_today_r <= -limits.daily_loss_lock_r:",
     "if realized_today_r < -limits.daily_loss_lock_r:",
     "flip the inclusive lock boundary"),
    ("test_portfolio_guard.py::test_g006_emergency_flatten_outranks_lockout", PG,
     '        action = "FLATTEN_ALL"        # outranks LOCKOUT',
     '        pass                          # outranks LOCKOUT',
     "let LOCKOUT outrank the emergency flatten"),
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
    src0 = PG.read_text()
    rows, fails = [], 0
    try:
        for test_id, mod, old, new, desc in MUTATIONS:
            if src0.count(old) != 1:
                rows.append((test_id, desc, f"PATCH ERROR: {src0.count(old)} matches"))
                fails += 1
                continue
            mod.write_text(src0.replace(old, new))
            red = not run_test(test_id)
            mod.write_text(src0)
            green = run_test(test_id)
            ok = red and green
            fails += 0 if ok else 1
            rows.append((test_id, desc,
                         "RED under fault, GREEN after revert" if ok
                         else f"FAILED (red={red}, green-after-revert={green})"))
            print(("ok  " if ok else "FAIL") + f"  {test_id}  [{desc}]", flush=True)
    finally:
        PG.write_text(src0)

    out = ["# Card 014 portfolio guards — mutation evidence (Gate 7)",
           "",
           "Every guard, the inclusive lock boundary, the breaker ordering rule,",
           "and the no-invented-correlations rule fault-injected. The live-broker",
           "boundary test pins broker.py's existing paper-host refusal and is",
           "deliberately NOT mutated — the transport is live-path code this card",
           "does not touch.",
           "",
           "| test | fault applied | result |", "|---|---|---|"]
    out += [f"| `{t}` | {d} | {res} |" for t, d, res in rows]
    out += ["", f"**{len(rows) - fails}/{len(rows)} rows verified.**"]
    if fails:
        # Adversarial review finding 5: a failing or concurrent run must
        # never clobber committed evidence. Fix, then re-run. Also never
        # run two drivers concurrently — they mutate the same modules.
        print(f"\n{fails} row(s) FAILED — log NOT written; committed "
              "evidence preserved")
        return 1

    (ROOT / "specs" / "014_GUARDS_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/014_GUARDS_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
