#!/usr/bin/env python3
"""Card 020 fault injection. Break each spine invariant, compute() hardening
path, the C004 midnight refusal, and the runner/ceiling caller threading;
confirm the named test goes RED, restore byte-identical, confirm GREEN.
Evidence -> specs/020_MUTATION_LOG.md."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"
TA, SE, CD, SC, RV, RN, CL = (DT / f for f in (
    "test_az_spine.py", "stockfish_exit.py", "context_directive.py",
    "stockfish_constitution.py", "regime_vector.py", "runner.py", "ceiling.py"))

MUTATIONS = [
    # ---- invariant 1: unavailable never becomes a number ----
    # HARNESS-SCOPED: the spine helper is the only regime consumer until 016
    # builds the production caller; 016 must inherit a production-side row.
    ("test_az_spine.py::test_unavailable_regime_never_becomes_a_number", TA,
     "        require(vec, name)",
     "        vec.available().get(name, 0.0)",
     "spine consumes available() with a default instead of require() [harness-scoped]"),
    # ---- invariant 2: stale state steers nothing (check-removal AND the
    #      spec's named comparator-flip fault, both points of use) ----
    ("test_az_spine.py::test_stale_state_cannot_influence_decision", SE,
     "if not scenario_set.is_fresh(now):", "if False:",
     "stop checking scenario freshness at the point of use"),
    ("test_az_spine.py::test_stale_state_cannot_influence_decision", SE,
     "if not scenario_set.is_fresh(now):", "if scenario_set.is_fresh(now):",
     "FLIP the scenario freshness comparator (the spec's named fault)"),
    ("test_az_spine.py::test_stale_state_cannot_influence_decision", CD,
     'if _parse_ts(d.expires_at, "expires_at") <= now:', "if False:",
     "stop rejecting expired directives"),
    ("test_az_spine.py::test_stale_state_cannot_influence_decision", CD,
     'if _parse_ts(d.expires_at, "expires_at") <= now:',
     'if _parse_ts(d.expires_at, "expires_at") > now:',
     "FLIP the directive expiry comparator (the spec's named fault)"),
    # ---- channel arbitration (resolve_channels — engine mechanics) ----
    ("test_az_spine.py::test_resolve_channels_urgency_is_most_protective", SE,
     "key=lambda u: URGENCY_RANK[u])", "key=lambda u: -URGENCY_RANK[u])",
     "pick the LEAST protective urgency"),
    ("test_az_spine.py::test_resolve_channels_candidate_only_tightens", SE,
     "chosen = min((policy, policy_candidate),",
     "chosen = max((policy, policy_candidate),",
     "let the directive candidate LOOSEN the policy"),
    ("test_az_spine.py::test_directive_candidate_cannot_loosen_policy_end_to_end", SE,
     "if policy_candidate is not None:", "if False:",
     "ignore the directive candidate entirely (tightening lost)"),
    # ---- invariant 3: oscillation cannot loosen protection ----
    ("test_az_spine.py::test_thesis_oscillation_cannot_loosen_protection", SE,
     "tighter = (eff.level > state.sl if state.direction > 0 else eff.level < state.sl)",
     "tighter = True",
     "make the tighten channel symmetric (emit the widened candidate on recovery)"),
    # ---- C004 midnight refusal ----
    ("test_constitution_wiring.py::test_c004_midnight_crossing_refused_by_design", SC,
     '''f"clock went backwards: {prev} -> {now_et} — the session clock is "
                "same-day by design; a midnight crossing means an overnight "
                "position, which this day-trade engine refuses", _rev(state))]''',
     'f"clock went backwards: {prev} -> {now_et}", _rev(state))]',
     "drop the deliberate session-boundary wording from the C004 refusal"),
    ("test_constitution_wiring.py::test_c004_midnight_crossing_refused_by_design", SC,
     'if _et_minutes(now_et, "now_et") < _et_minutes(prev, "last_now_et"):',
     "if False:",
     "disable the backwards-clock refusal itself"),
    # ---- compute() hardening, one row per named path + trend (review F2) ----
    ("test_regime_compute.py::test_flat_tape_yields_unavailable_not_computed_zero", RV,
     'put("trend_strength", None, "unavailable", f"slope undefined: {e}")',
     'put("trend_strength", 0.0, "computed", f"slope undefined: {e}")',
     "trend fabricates a computed 0.0 on a zero-ATR tape"),
    ("test_regime_compute.py::test_zero_mean_price_raises", RV,
     "if px <= 0:", "if False:",
     "accept a zero mean price instead of raising"),
    ("test_regime_compute.py::test_zero_median_history_makes_expansion_unavailable", RV,
     '''put("volatility_expansion", None, "unavailable",
                f"expansion undefined''',
     '''put("volatility_expansion", 0.0, "computed",
                f"expansion undefined''',
     "expansion fabricates a computed 0.0 when undefined"),
    ("test_regime_compute.py::test_flat_tape_yields_unavailable_not_computed_zero", RV,
     'put("mean_reversion_pressure", None, "unavailable",',
     'put("mean_reversion_pressure", 0.0, "computed",',
     "stretch fabricates a computed 0.0 on a zero-ATR tape"),
    ("test_regime_compute.py::test_flat_tape_yields_unavailable_not_computed_zero", RV,
     '        raise RegimeError("zero return variance — constant series")',
     "        return 0.0",
     "autocorrelation fabricates a computed 0.0 on constant returns"),
    ("test_regime_compute.py::test_constant_index_makes_correlation_unavailable_only", RV,
     'put("cross_asset_correlation", None, "unavailable",',
     'put("cross_asset_correlation", 0.0, "computed",',
     "correlation fabricates a computed 0.0 on a constant series"),
    # ---- caller threading (runner + ceiling; backtest row lives in the wiring driver) ----
    ("test_constitution_wiring.py::test_runner_replay_threads_one_session_lifetime_key_set", RN,
     "apply_actions(s, actions, applied_reduction_keys)",
     "apply_actions(s, actions, set())",
     "runner: fresh key set per cycle instead of the session-lifetime set"),
    ("test_constitution_wiring.py::test_ceiling_simulate_threads_keys_and_batch", CL,
     "if key is not None:", "if False:",
     "ceiling: stop recording the applied partial's key"),
    ("test_constitution_wiring.py::test_ceiling_simulate_threads_keys_and_batch", CL,
     "batch=acts)", "batch=None)",
     "ceiling: stop passing the batch into apply_action"),
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
    modules = {m for _, m, _, _, _ in MUTATIONS}
    originals = {p: p.read_text() for p in modules}
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

    out = ["# Card 020 — mutation evidence (Gate 1)",
           "",
           "Every promoted-spec requirement fault-injected: the three spine",
           "invariants (including the spec's named comparator-flip faults), the",
           "engine-side channel arbitration (resolve_channels), the five ruled",
           "compute() silent-zero paths PLUS trend_strength (same species, found",
           "in adversarial review), the C004 midnight session-boundary refusal",
           "(wording AND the refusal itself), and the runner/ceiling caller",
           "threading (backtest's row lives in WIRING_MUTATION_LOG.md). Each",
           "fault applied -> named test RED -> module restored byte-identical ->",
           "GREEN.",
           "",
           "Known residuals carried forward, not silently dropped: the invariant-1",
           "row is harness-scoped until 016 builds the production regime consumer;",
           "a malformed HISTORY session with zero mean price still raises a raw",
           "ZeroDivisionError rather than RegimeError (today's session is hardened;",
           "history-side belongs to the compute-coverage backlog line in 020).",
           ""]
    out += [
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

    (ROOT / "specs" / "020_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/020_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
