# Session 2026-08-29 — live todo (an item is DONE only when its verification step ran)

- [x] Phase 0 discovery + gap list (Plans/cheeky-mixing-locket.md) — approved
- [x] Step 0 inventory guardrail: sovereign/forex/inventory.py, scripts/build_inventory.py — 6 tests green
- [x] Step 1 CARRY-FROZEN-001 checkpoint — verify rc 0; 10 tests; real-path injection (calibration file appears) → rc 1, restored → rc 0
- [x] Step 2 path extractor — n=350, ΣR 34.4062, parity 350/350, cost err 1e-16, 121 units, deterministic hashes; Opus rerun reproduced hashes; 14 tests
- [x] Step 3 bench + pre-commit — 34.4061881520, 1727 rows parity, two runs identical, trail×0.5 changes it (×1.0001 does not — recorded); .githooks/pre-commit blocks a mismatch
- [x] Step 4 spec 045 prereg — committed 2cf6ac7 (FREEZE OVERRIDE) + cells filled before any fit; δ=0.1914R σ=0.7580 n_oof=97
- [x] Step 5 sprt.py (Forge, gpt-5.6-terra — 5.4 refused by the account) — 17 tests; hand trace re-verified by Opus; Forge injection (d−δ) failed 4, Opus injection (B=ln β/α) failed 8; incumbent-vs-itself → ACCEPT_H0 at step 50 for δ=.25 σ=1
- [ ] Step 6 walk-forward + tablebase — first build halted on a real §4 defect (period-2 cycle); Amendment 1 e940bc7; rebuild in progress
- [ ] Step 7 fill model — BaseFill ≡ _apply_costs on 5 synthetic trades + mutation bites (12 tests); the 350-parity (I68) waits on Step 2
- [ ] Step 8 driver — written (scripts/carry_exit_sprt.py, 8 invariant tests green); not run until Step 6 lands
- [x] Step 9 feature registry — require_as_of raises on NOT-AS-OF / LABEL / UNREGISTERED; flip-a-feature mutation raises (12 tests); loader integration lands with Step 6
- [ ] Step 10 red team → artifacts/redteam.md (git status shows only that file)
- [ ] Step 11 artifacts/session-report.md — seven DoD items; final commit
