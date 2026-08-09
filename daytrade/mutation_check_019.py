#!/usr/bin/env python3
"""Card 019 fault-injection driver. For every spec row: apply the fault column's
mutation, confirm the named test goes RED, revert, confirm GREEN. Emits a
markdown evidence table. Never leaves a mutation behind: originals restored in
a finally block, byte-identical."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"

SC, TH, RV, SE = (DT / f for f in ("scenarios.py", "thesis.py",
                                   "regime_vector.py", "stockfish_exit.py"))

# (row/test, module, old, new, fault description)
MUTATIONS = [
    # ---- scenarios.py ----
    ("test_scenarios.py::test_prob_must_sum_to_one", SC,
     "if abs(total - 1.0) > PROB_TOLERANCE:", "if False:",
     "disable the probability-sum check"),
    ("test_scenarios.py::test_rejects_negative_prob", SC,
     "if not (0.0 <= v <= 1.0):", "if not (v <= 1.0):",
     "remove the >= 0 bound"),
    ("test_scenarios.py::test_rejects_prob_above_one", SC,
     "if not (0.0 <= v <= 1.0):", "if not (0.0 <= v):",
     "remove the <= 1 bound"),
    ("test_scenarios.py::test_rejects_nan_and_inf", SC,
     "if not (0.0 <= v <= 1.0):", "if False:",
     "remove the range check prob/confidence NaN-rejection rides on"),
    ("test_scenarios.py::test_rejects_nan_and_inf", SC,
     "if not math.isfinite(self.expected_duration_min) or self.expected_duration_min <= 0:",
     "if self.expected_duration_min <= 0:",
     "remove the isfinite guard on duration (the pre-019 gap, reintroduced)"),
    ("test_scenarios.py::test_rejects_unknown_scenario_name", SC,
     "if self.name not in ALL_SCENARIOS:", "if False:",
     "remove the name-membership check"),
    ("test_scenarios.py::test_rejects_duplicate_scenario_names", SC,
     "if len(names) != len(set(names)):", "if False:",
     "remove the dedup check"),
    ("test_scenarios.py::test_rejects_empty_set", SC,
     "if not self.scenarios:", "if False:",
     "remove the non-empty check"),
    ("test_scenarios.py::test_rejects_missing_invalidation_or_evidence", SC,
     "if not self.invalidation:", "if False:",
     "remove the required-invalidation check"),
    ("test_scenarios.py::test_freshness_at_exact_boundary_is_fresh", SC,
     "return self.age_min(now) <= self.max_age_min",
     "return self.age_min(now) < self.max_age_min",
     "flip <= to < in is_fresh()"),
    ("test_scenarios.py::test_decisive_picks_top_scenario_policy", SE,
     "    top = scenario_set.top",
     "    top = sorted(scenario_set.scenarios, key=lambda s: -s.probability)[1]",
     "swap top for the second-highest scenario"),
    ("test_scenarios.py::test_indecisive_picks_most_conservative_represented_policy", SE,
     "pick = min(usable, key=lambda p: CONSERVATISM.get(p, 99))",
     "pick = max(usable, key=lambda p: CONSERVATISM.get(p, 99))",
     "fallback to the LEAST conservative policy"),
    ("test_scenarios.py::test_never_averages_into_unrepresented_policy", SE,
     'return top.recommended_policy, (f"{top.name} at {top.probability:.0%} "',
     'return "DEFAULT", (f"{top.name} at {top.probability:.0%} "',
     "return an interpolated/middle policy absent from the set"),
    ("test_scenarios.py::test_unreadable_is_flat_three_way", SC,
     "probs = [p, p, round(1.0 - 2 * p, 6)]",
     "probs = [0.5, 0.25, 0.25]",
     "skew one weight in unreadable()"),
    # ---- thesis.py ----
    ("test_thesis.py::test_legal_transitions_only", TH,
     "elif fired_weak:", "elif False:",
     "remove the weakening precedence branch"),
    ("test_thesis.py::test_idempotent_on_repeated_observation", TH,
     "if new != self.state:", "if True:",
     "record a transition on every call, including no-ops"),
    ("test_thesis.py::test_invalidated_is_sticky", TH,
     "if self.state in TERMINAL:", "if False:",
     "remove TERMINAL gating in evaluate()"),
    ("test_thesis.py::test_expired_is_sticky", TH,
     "if self.state in TERMINAL:", "if False:",
     "same mutation, EXPIRED row"),
    ("test_thesis.py::test_invalidation_outranks_expiry_when_both_fire", TH,
     '''if fired_inv:
            new, why = "THESIS_INVALIDATED", f"invalidator(s) fired: {', '.join(fired_inv)}"
        elif self._expired(now_et):
            new, why = "THESIS_EXPIRED", f"past {self.expires_at_et} ET"''',
     '''if self._expired(now_et):
            new, why = "THESIS_EXPIRED", f"past {self.expires_at_et} ET"
        elif fired_inv:
            new, why = "THESIS_INVALIDATED", f"invalidator(s) fired: {', '.join(fired_inv)}"''',
     "swap the invalidation/expiry precedence order"),
    ("test_thesis.py::test_weakening_is_not_sticky", TH,
     'TERMINAL: frozenset[str] = frozenset({"THESIS_INVALIDATED", "THESIS_EXPIRED"})',
     'TERMINAL: frozenset[str] = frozenset({"THESIS_INVALIDATED", "THESIS_EXPIRED", "THESIS_WEAKENING"})',
     "make WEAKENING sticky (terminal)"),
    ("test_thesis.py::test_unknown_observed_condition_key_raises", TH,
     "if unknown:", "if False:",
     "remove the unknown-key check"),
    ("test_thesis.py::test_urgency_for_thesis_covers_all_states", SE,
     'if state == "THESIS_WEAKENING":', "if False:",
     "delete the WEAKENING branch in urgency_for_thesis"),
    # ---- regime_vector.py ----
    ("test_regime_vector.py::test_rejects_unknown_dimension", RV,
     "if self.name not in SPEC:", "if False:",
     "remove the SPEC-membership check"),
    ("test_regime_vector.py::test_rejects_unavailable_with_value", RV,
     "if self.value is not None:", "if False:",
     "remove the unavailable-carries-no-value check"),
    ("test_regime_vector.py::test_rejects_computed_without_value", RV,
     "if self.value is None:", "if False:",
     "remove the valued-source-needs-value check"),
    ("test_regime_vector.py::test_rejects_out_of_range", RV,
     "if not (lo - 1e-9 <= self.value <= hi + 1e-9):", "if False:",
     "remove the range check"),
    ("test_regime_vector.py::test_rejects_nan_explicitly", RV,
     "if math.isnan(self.value):", "if False:",
     "remove the explicit isnan check (accidental range-side-effect must NOT satisfy the test)"),
    ("test_regime_vector.py::test_rejects_incomplete_vector", RV,
     "if missing:", "if False:",
     "remove the completeness check"),
    ("test_regime_vector.py::test_available_returns_source_not_just_value", RV,
     'return {k: d.value for k, d in self.dims.items() if d.source != "unavailable"}',
     'return {k: (d.value if d.source != "unavailable" else 0.0) for k, d in self.dims.items()}',
     "collapse unavailable into 0.0 (the failure mode the row guards)"),
    ("test_regime_vector.py::test_require_raises_on_unavailable", RV,
     'if d.source == "unavailable":', "if False:",
     "remove the unavailable check in require()"),
    ("test_regime_vector.py::test_require_raises_on_missing", RV,
     "if name not in vec.dims:", "if False:",
     "remove the missing-name check in require()"),
    ("test_regime_vector.py::test_require_returns_value_when_computed_or_judged", RV,
     "    return d.value", "    return 0.0",
     "break the happy path (return a constant instead of the value)"),
]


def run_test(test_id: str) -> bool:
    """True == pytest green. Bytecode caches are purged first: a mutation that
    keeps the file size identical within the same mtime second would otherwise
    be masked by a stale .pyc (observed: min->max and block-swap mutations)."""
    import shutil
    for pyc in (DT / "__pycache__", ROOT / "__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    r = subprocess.run([sys.executable, "-B", "-m", "pytest", f"daytrade/{test_id}", "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    originals = {p: p.read_text() for p in {SC, TH, RV, SE}}
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

    out = [
        "# Card 019 — mutation evidence (fault-injection DoD)",
        "",
        "Generated by the 019 fault-injection driver. For every spec row the fault",
        "column's mutation was applied to the module, the named test confirmed RED,",
        "the module restored byte-identical, and the test confirmed GREEN. This loop",
        "is the card's acceptance criterion (019:47-50, 106-117) — a green suite",
        "alone is not evidence.",
        "",
        "| test | fault applied | result |",
        "|---|---|---|",
    ]
    out += [f"| `{t}` | {d} | {res} |" for t, d, res in rows]
    out += ["", f"**{len(rows) - fails}/{len(rows)} rows verified.**"]
    (ROOT / "specs" / "019_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/019_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
