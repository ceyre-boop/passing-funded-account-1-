# Session 2026-08-29 — live todo (an item is DONE only when its verification step ran)

- [x] Phase 0 discovery + gap list (Plans/cheeky-mixing-locket.md) — approved
- [x] Step 0 inventory guardrail: sovereign/forex/inventory.py, scripts/build_inventory.py — 6 tests green
- [x] Step 1 CARRY-FROZEN-001 checkpoint — verify rc 0; 10 tests; real-path injection (calibration file appears) → rc 1, restored → rc 0
- [ ] Step 2 path extractor → artifacts/carry_paths.parquet — halts unless n=350, round(ΣR,2)=34.41; parity test
- [ ] Step 3 bench + pre-commit — two identical runs; one-param mutation changes the number
- [ ] Step 4 spec 045 prereg (FREEZE OVERRIDE commit) — written before any candidate fit
- [ ] Step 5 sprt.py (Forge) — hand-computed fixture; incumbent-vs-itself → ACCEPT_H0 at computed step
- [ ] Step 6 walk-forward + tablebase — I65/I66/I67/I71 mutations bite
- [ ] Step 7 fill model — BaseFill ≡ _apply_costs on the 350 (I68)
- [ ] Step 8 driver → artifacts/sprt_result.json (base + candidate-pessimistic + permutation)
- [ ] Step 9 feature registry (minimal) — flip → raise
- [ ] Step 10 red team → artifacts/redteam.md (git status shows only that file)
- [ ] Step 11 artifacts/session-report.md — seven DoD items; final commit
