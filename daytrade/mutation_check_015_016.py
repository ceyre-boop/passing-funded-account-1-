#!/usr/bin/env python3
"""Cards 015+016 fault injection (Gate 5). Break the evidence lifecycle rules,
the authority registry, and the runner's directive wiring; confirm the named
test goes RED, restore byte-identical, confirm GREEN.
-> specs/015_016_MUTATION_LOG.md."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DT = ROOT / "daytrade"
EV, CD, RN = DT / "evidence.py", DT / "context_directive.py", DT / "runner.py"

MUTATIONS = [
    # ---- 015: evidence ----
    ("test_evidence.py::test_evidence_validates", EV,
     "if self.evidence_type not in EVIDENCE_TYPES:", "if False:",
     "accept unknown evidence types"),
    ("test_evidence.py::test_unrated_source_is_explicit_never_defaulted", EV,
     'if self.reliability is None and self.reliability_basis != "unrated":',
     "if False:",
     "let a None reliability travel without its 'unrated' label"),
    ("test_evidence.py::test_unrated_source_is_explicit_never_defaulted", EV,
     'return None, "unrated"', 'return 0.5, "unrated"',
     "default an unrated source to 0.5 (the zero-fill the card forbids)"),
    ("test_evidence.py::test_canonicalization_groups_the_same_story", EV,
     're.sub(r"[^\\w\\s]", "", text.lower())', 're.sub(r"[^\\w\\s]", "", text)',
     "stop lowercasing in canonicalization (same story splits into two)"),
    ("test_evidence.py::test_lifecycle_is_forward_only_and_idempotent", EV,
     "if _STATE_ORDER[new_state] < _STATE_ORDER[g.state]:", "if False:",
     "let the lifecycle run backwards"),
    ("test_evidence.py::test_duplicates_update_provenance_but_never_raise_urgency", EV,
     "urgency = (_STATE_URGENCY[g.state] * lead.severity) if fresh else 0.0",
     "urgency = (_STATE_URGENCY[g.state] * max(m.severity for m in g.members)) if fresh else 0.0",
     "let a louder recap raise the group's urgency (the card's core rule)"),
    ("test_evidence.py::test_stale_recap_carries_no_urgency", EV,
     "fresh = age_min <= TTL_MIN[lead.evidence_type]",
     "fresh = age_min < TTL_MIN[lead.evidence_type]",
     "flip the inclusive freshness boundary"),
    ("test_evidence.py::test_digested_story_steers_nothing", EV,
     '"DIGESTED": 0.0,', '"DIGESTED": 0.5,',
     "give a digested story residual urgency"),
    ("test_evidence.py::test_conflict_is_surfaced_never_averaged", EV,
     'conflicted = "bullish" in directions and "bearish" in directions',
     "conflicted = False",
     "hide the conflict"),
    ("test_evidence.py::test_scoped_delivery_nvda_vs_market", EV,
     'elif v.most_specific_scope == "market" and index_linked:', "elif True:",
     "deliver every story to every symbol"),
    # ---- 016: abstention + registry ----
    ("test_directive_authority.py::test_abstention_rejects_unknown_reason_and_anonymity", CD,
     "if self.reason not in ABSTENTION_REASONS:", "if False:",
     "accept an unknown abstention reason"),
    ("test_directive_authority.py::test_unpromoted_grants_cap_at_recommend", CD,
     "if level > UNPROMOTED_CAP and not promotion_ref:", "if False:",
     "grant interrupt authority without a promotion_ref"),
    ("test_directive_authority.py::test_rollback_is_explicit_and_audited", CD,
     "for g in self._trail:", "for g in self._trail[:1]:",
     "derive granted_level from the first grant instead of the whole trail"),
    # ---- 016: runner wiring ----
    ("test_directive_authority.py::test_runner_tightens_on_valid_directive_and_logs_merged_urgency", RN,
     "urgents=(urgency, directive_urgency))", "urgents=(urgency, None))",
     "drop the directive channel from the urgency merge"),
    ("test_directive_authority.py::test_runner_tightens_on_valid_directive_and_logs_merged_urgency", RN,
     '"urgent": merged_urgency, "bias_note": bias_note,',
     '"urgent": urgency, "bias_note": bias_note,',
     "log the pre-merge urgency (replay diff would lie)"),
    ("test_directive_authority.py::test_runner_refuses_emergency_from_unpromoted_authority", RN,
     "dec = evaluate(ds, ReceiverContext(symbol=symbol))",
     "dec = evaluate(ds, ReceiverContext(symbol=symbol, granted_level=4))",
     "runner grants itself interrupt authority"),
    ("test_directive_authority.py::test_runner_malformed_directives_steer_nothing", RN,
     'return None, f"DIRECTIVES UNREADABLE ({e}) — steering nothing"',
     'return "tighten", f"DIRECTIVES UNREADABLE ({e}) — steering nothing"',
     "let a malformed directives file steer the position"),
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

    out = ["# Cards 015+016 — mutation evidence (Gate 5)",
           "",
           "Evidence lifecycle (dup-never-raises-urgency, forward-only states,",
           "inclusive freshness, explicit unrated, conflict surfacing, scoped",
           "delivery), the authority registry (unpromoted cap, audited rollback),",
           "and the runner's directive wiring (merge, replay-faithful logging,",
           "default authority, malformed-file refusal) all fault-injected:",
           "fault -> named test RED -> restore -> GREEN.",
           "",
           "| test | fault applied | result |", "|---|---|---|"]
    out += [f"| `{t}` | {d} | {res} |" for t, d, res in rows]
    out += ["", f"**{len(rows) - fails}/{len(rows)} rows verified.**"]
    (ROOT / "specs" / "015_016_MUTATION_LOG.md").write_text("\n".join(out) + "\n")
    print(f"\n{len(rows) - fails}/{len(rows)} rows verified -> specs/015_016_MUTATION_LOG.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
