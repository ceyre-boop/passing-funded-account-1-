#!/usr/bin/env python3
"""Card 017 fault injection (Gate 6). Break the ledger's record-before-outcome
structure, the grading, and each promotion gate; confirm RED, restore, GREEN.
-> specs/017_MUTATION_LOG.md."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"
FC = DT / "forecast.py"

MUTATIONS = [
    # ---- the ledger's structure ----
    ("test_forecast.py::test_forecast_validates", FC,
     "if abs(total - 1.0) > PROB_TOL:", "if False:",
     "accept a non-distribution"),
    ("test_forecast.py::test_forecast_validates", FC,
     "if unknown:", "if False:",
     "accept unknown scenarios in a forecast"),
    ("test_forecast.py::test_outcome_cannot_enter_before_horizon", FC,
     'if _aware(r.resolved_at, "resolved_at") < horizon_end:', "if False:",
     "let an outcome enter before the horizon (the card's core seal)"),
    ("test_forecast.py::test_claims_are_made_once_and_resolved_once", FC,
     "if f.forecast_id in self._forecasts:", "if False:",
     "record the same claim twice"),
    ("test_forecast.py::test_claims_are_made_once_and_resolved_once", FC,
     "if r.forecast_id in self._resolutions:", "if False:",
     "re-litigate a resolved outcome"),
    ("test_forecast.py::test_resolution_never_edits_the_original_claim", FC,
     "        self._resolutions[r.forecast_id] = r",
     "        self._resolutions[r.forecast_id] = r\n"
     "        self._forecasts[r.forecast_id] = None",
     "let resolution clobber the original claim"),
    # ---- grading ----
    ("test_forecast.py::test_brier_and_baseline_on_identical_case", FC,
     "    names = set(f.scenario_probs) | {r.outcome_scenario}\n"
     "    return sum((f.scenario_probs.get(n, 0.0)",
     "    names = set(f.scenario_probs)\n"
     "    return sum((f.scenario_probs.get(n, 0.0)",
     "brier ignores a surprise outcome outside the forecast's vocabulary"),
    ("test_forecast.py::test_brier_and_baseline_on_identical_case", FC,
     "    p = 1.0 / len(names)", "    p = 0.0",
     "baseline stops being uniform on the identical case"),
    ("test_forecast.py::test_false_urgent_and_missed_shock_rates", FC,
     "false_urgent = (sum(1 for _, r in urgent if not r.shock_occurred)",
     "false_urgent = (sum(1 for _, r in urgent if r.shock_occurred)",
     "invert the false-urgent definition"),
    # ---- the promotion gate ----
    ("test_forecast.py::test_brier_must_be_strictly_better", FC,
     "if not (challenger.brier < incumbent.brier):", "if False:",
     "let a tied challenger through"),
    ("test_forecast.py::test_one_failed_gate_rejects_even_a_brilliant_challenger", FC,
     "if failed:", "if False:",
     "promote despite failed gates"),
    ("test_forecast.py::test_failed_gate_list_is_complete_not_first_only", FC,
     '    if not (incumbent.oos and challenger.oos):\n        failed.append("OOS_REQUIRED")',
     '    if not (incumbent.oos and challenger.oos):\n'
     '        return PromotionDecision(False, ("OOS_REQUIRED",), None, "first-only")',
     "report only the first failed gate"),
    ("test_forecast.py::test_regime_instability_and_missing_bucket_reject", FC,
     "elif c > per_regime_incumbent[regime] + thresholds.regime_stability_tolerance:",
     "elif False:",
     "ignore regime instability"),
    ("test_forecast.py::test_regime_instability_and_missing_bucket_reject", FC,
     "if c is None:", "if False:",
     "ignore a missing regime bucket"),
    ("test_forecast.py::test_gates_with_no_data_fail_not_pass", FC,
     "if not per_regime_incumbent or not per_regime_challenger:", "if False:",
     "regime gate passes vacuously with no data (review finding 6)"),
    ("test_forecast.py::test_gates_with_no_data_fail_not_pass", FC,
     "if challenger.worst_policy_regret is None:", "if False:",
     "tail gate passes vacuously with no regret data (review finding 6)"),
    ("test_forecast.py::test_brier_and_baseline_on_identical_case", FC,
     "    p = 1.0 / len(names)", "    p = 0.5",
     "constant-0.5 baseline masquerading as uniform — indistinguishable on "
     "2-name cases, caught by the 3-name numeric pin (review finding 7)"),
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

    out = ["# Card 017 — mutation evidence (Gate 6)",
           "",
           "The record-before-outcome seal, claim/resolution immutability, the",
           "identical-case baseline, and every promotion gate fault-injected:",
           "fault -> named test RED -> restore byte-identical -> GREEN. Note the",
           "structural guarantee no mutation can test: promotion_decision takes",
           "no return/PnL parameter at all — 'but it made more money' is",
           "unrepresentable by signature.",
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

    (ROOT / "specs" / "017_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/017_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
