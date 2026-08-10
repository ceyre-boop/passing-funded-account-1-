#!/usr/bin/env python3
"""Card 014 (shadow/regret) fault injection. Break containment, the no-future
fold, the version identity, and each regret metric; confirm the named test goes
RED, restore byte-identical, confirm GREEN. -> specs/014_MUTATION_LOG.md."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"
SH, RG = DT / "shadow.py", DT / "regret.py"

_KIND_OLD = '''    @property
    def kind(self):
        raise ShadowContainment('''
_KIND_NEW = '''    @property
    def kind(self):
        return self.hypothetical_kind
        raise ShadowContainment('''

MUTATIONS = [
    # ---- containment ----
    ("test_shadow_regret.py::test_shadow_action_kind_access_raises", SH,
     _KIND_OLD, _KIND_NEW,
     "let .kind on a shadow action return a value instead of raising"),
    ("test_shadow_regret.py::test_shadow_action_cannot_reach_apply_action", SH,
     _KIND_OLD, _KIND_NEW,
     "same fault — the execution funnel must also refuse it"),
    ("test_shadow_regret.py::test_shadow_serialization_is_unmistakable", SH,
     'd["shadow"] = True', 'd["shadow"] = False',
     "serialize a shadow action without its marker"),
    # ---- the tournament ----
    ("test_shadow_regret.py::test_prefix_property_no_future_data", SH,
     "st.price = float(price)",
     "st.price = max(float(price), float(cycles[-1][0]))",
     "fold peeks at the future (max with the final price)"),
    ("test_shadow_regret.py::test_policies_genuinely_diverge", SH,
     "def _state_for(plan: dict, policy: str) -> TradeState:\n"
     "    base = {k: plan[k] for k in (\"direction\", \"entry\", \"qty\", \"sl\", \"tp1\",\n"
     "                                 \"tp2\", \"trail_dist\")}\n"
     "    params = policy_params(policy, plan)",
     "def _state_for(plan: dict, policy: str) -> TradeState:\n"
     "    base = {k: plan[k] for k in (\"direction\", \"entry\", \"qty\", \"sl\", \"tp1\",\n"
     "                                 \"tp2\", \"trail_dist\")}\n"
     "    params = policy_params(\"DEFEND\", plan)",
     "every policy silently runs as DEFEND (tournament measures nothing)"),
    ("test_shadow_regret.py::test_shadow_records_policy_version_identity", SH,
     "        params = policy_params(name, plan)", "        params = {}",
     "drop the policy version identity from results"),
    ("test_shadow_regret.py::test_bad_plan_risk_refused", SH,
     "if risk <= 0:", "if False:",
     "accept a zero-risk plan"),
    # ---- regret ----
    ("test_shadow_regret.py::test_grade_open_trade_refused", RG,
     "if not closed:", "if False:",
     "grade an open trade"),
    ("test_shadow_regret.py::test_report_never_crowns_a_winner", RG,
     "    def to_dict(self) -> dict:\n        return asdict(self)",
     "    def to_dict(self) -> dict:\n"
     "        d = asdict(self)\n"
     "        d[\"winner\"] = max(self.counterfactual_delta_r,\n"
     "                          key=self.counterfactual_delta_r.get, default=None)\n"
     "        return d",
     "crown the hindsight winner in the report"),
    ("test_shadow_regret.py::test_grade_metrics_match_hand_computation", RG,
     "dd = min(dd, x - peak)", "dd = min(dd, x)",
     "drawdown stops measuring peak-to-trough"),
    ("test_shadow_regret.py::test_grade_metrics_match_hand_computation", RG,
     "giveback_r=mfe_r - realized_r,", "giveback_r=mfe_r,",
     "giveback forgets what was realized"),
    ("test_shadow_regret.py::test_grade_metrics_match_hand_computation", RG,
     "t0, t1 = cycles[0][1], cycles[-1][1]", "t0, t1 = cycles[0][1], cycles[0][1]",
     "hold time collapses to zero"),
    ("test_shadow_regret.py::test_grade_metrics_match_hand_computation", RG,
     'slippage_r=0.0, slippage_basis="paper",', 'slippage_r=0.0, slippage_basis="",',
     "slippage loses its 'paper' label (a zero must say why it is zero)"),
    ("test_shadow_regret.py::test_grade_metrics_match_hand_computation", RG,
     "deltas = {name: r.realized_r - realized_r",
     "deltas = {name: r.realized_r",
     "counterfactual deltas stop being deltas"),
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

    out = ["# Card 014 (shadow/regret) — mutation evidence (Gate 4)",
           "",
           "Containment (type-level no-execution), the no-future-data fold, the",
           "policy version identity, and every regret metric fault-injected:",
           "fault applied -> named test RED -> module restored byte-identical ->",
           "GREEN. Portfolio guards (the card's third product) land at Gate 7",
           "with their own rows.",
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

    (ROOT / "specs" / "014_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/014_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
