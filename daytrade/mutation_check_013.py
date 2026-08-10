#!/usr/bin/env python3
"""Card 013 fault injection. Break each planner rule and ledger invariant,
confirm the named test goes RED, restore byte-identical, confirm GREEN.
Evidence -> specs/013_MUTATION_LOG.md."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"
EP = DT / "execution_policy.py"

MUTATIONS = [
    # ---- planner ----
    ("test_execution_policy.py::test_planner_never_changes_the_requested_reduction", EP,
     'ExecutionPlan(intent.intent_id, "AGGRESSIVE_LIMIT", intent.qty, far,',
     'ExecutionPlan(intent.intent_id, "AGGRESSIVE_LIMIT", intent.qty * 0.5, far,',
     "planner resizes the requested reduction (the forbidden second engine)"),
    ("test_execution_policy.py::test_urgent_exit_goes_market", EP,
     'if intent.urgency == "exit":', "if False:",
     "urgent exit stops going MARKET"),
    ("test_execution_policy.py::test_bad_quotes_are_refused_never_guessed", EP,
     "if q.age_s > MAX_QUOTE_AGE_S:", "if False:",
     "price an order on a stale quote"),
    ("test_execution_policy.py::test_bad_quotes_are_refused_never_guessed", EP,
     "if q.bid <= 0 or q.ask <= 0 or q.bid > q.ask:", "if False:",
     "accept a crossed/empty quote"),
    ("test_execution_policy.py::test_bad_quotes_are_refused_never_guessed", EP,
     "if intent.qty <= 0:", "if False:",
     "plan a zero-quantity order"),
    ("test_execution_policy.py::test_tight_spread_crosses_wide_spread_sits", EP,
     "if spread <= slippage_budget:", "if True:",
     "always cross the spread regardless of budget"),
    # ---- ledger ----
    ("test_execution_policy.py::test_unknown_order_and_unknown_event_raise", EP,
     "if e.order_id in self._orders:", "if False:",
     "accept the same broker order id twice"),
    ("test_execution_policy.py::test_unknown_order_and_unknown_event_raise", EP,
     "if e.fill_id is None:", "if False:",
     "accept an unidentifiable fill"),
    ("test_execution_policy.py::test_oversubmission_beyond_remaining_raises", EP,
     "if e.qty > remaining + 1e-9:", "if False:",
     "submit more than the intent's remaining quantity"),
    ("test_execution_policy.py::test_overfill_is_reconciliation_failure_not_absorbed", EP,
     "if o.filled + e.qty > o.submitted + 1e-9:", "if False:",
     "absorb an over-fill"),
    ("test_execution_policy.py::test_duplicate_fill_id_identical_is_retry_different_is_corruption", EP,
     "if prior == (e.qty, e.price):", "if True:",
     "treat a conflicting duplicate fill as a harmless retry"),
    ("test_execution_policy.py::test_late_fill_racing_cancel_is_honored_within_submitted", EP,
     'if o.status == "CANCELED":\n                # late fill racing the cancel',
     'if False:\n                # late fill racing the cancel',
     "stop shrinking canceled qty on a late fill (books go negative)"),
    ("test_execution_policy.py::test_full_fill_completes_partial_does_not", EP,
     "if abs(self.filled(intent_id) - rec.intent.qty) <= 1e-9:", "if True:",
     "mark an intent FILLED from submission alone"),
    ("test_execution_policy.py::test_cancel_releases_exposure_and_retry_resubmits_remainder", EP,
     "return rec.intent.qty - self.filled(intent_id)", "return 0.0",
     "report zero pending exposure while quantity is still owed"),
    # ---- bridge to 012 ----
    ("test_execution_policy.py::test_reduction_payloads_submission_alone_completes_nothing", EP,
     'if led.status(intent.intent_id) != "FILLED":', "if False:",
     "emit the FILL payload for a merely-submitted reduction"),
]


def run_test(test_id: str) -> bool:
    for pyc in (DT / "__pycache__", ROOT / "__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    r = subprocess.run([sys.executable, "-B", "-m", "pytest", f"daytrade/{test_id}", "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True)
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

    out = ["# Card 013 — mutation evidence (Gate 3)",
           "",
           "Planner rules (never resize, refuse bad quotes, urgency mapping) and",
           "every ledger quantity invariant fault-injected: fault applied -> named",
           "test RED -> module restored byte-identical -> GREEN. The seeded",
           "property walk additionally asserts the invariants after every event",
           "of 200-step random legal sequences.",
           "",
           "| test | fault applied | result |", "|---|---|---|"]
    out += [f"| `{t}` | {d} | {res} |" for t, d, res in rows]
    out += ["", f"**{len(rows) - fails}/{len(rows)} rows verified.**"]
    (ROOT / "specs" / "013_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/013_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
