# Session 2026-08-29 — live todo (an item is DONE only when its verification step ran)

- [x] Phase 0 discovery + gap list (Plans/cheeky-mixing-locket.md) — approved
- [x] Step 0 inventory guardrail: sovereign/forex/inventory.py, scripts/build_inventory.py — 6 tests green
- [x] Step 1 CARRY-FROZEN-001 checkpoint — verify rc 0; 10 tests; real-path injection (calibration file appears) → rc 1, restored → rc 0
- [x] Step 2 path extractor — n=350, ΣR 34.4062, parity 350/350, cost err 1e-16, 121 units, deterministic hashes; Opus rerun reproduced hashes; 14 tests
- [ ] Step 3 bench + pre-commit — two identical runs; one-param mutation changes the number
- [ ] Step 4 spec 045 prereg (FREEZE OVERRIDE commit) — written before any candidate fit
- [x] Step 5 sprt.py (Forge, gpt-5.6-terra — 5.4 refused by the account) — 17 tests; hand trace re-verified by Opus; Forge injection (d−δ) failed 4, Opus injection (B=ln β/α) failed 8; incumbent-vs-itself → ACCEPT_H0 at step 50 for δ=.25 σ=1
- [ ] Step 6 walk-forward + tablebase — I65/I66/I67/I71 mutations bite
- [ ] Step 7 fill model — BaseFill ≡ _apply_costs on 5 synthetic trades + mutation bites (12 tests); the 350-parity (I68) waits on Step 2
- [ ] Step 8 driver → artifacts/sprt_result.json (base + candidate-pessimistic + permutation)
- [x] Step 9 feature registry — require_as_of raises on NOT-AS-OF / LABEL / UNREGISTERED; flip-a-feature mutation raises (12 tests); loader integration lands with Step 6
- [ ] Step 10 red team → artifacts/redteam.md (git status shows only that file)
- [ ] Step 11 artifacts/session-report.md — seven DoD items; final commit
