# Cost-model reopen rule — pre-registration

**Date:** 2026-09-01 · **Governs:** `artifacts/FLOOR_TEST_RESULT.md` closures
· **Frozen constants remain:** `az/floor_params.py` @ `3b10e8b`, untouched.

---

## 0. Disclosure first, because it changes the rule

**The cost sensitivity has already been computed and published.** I have seen which
families clear at which constants. A reopen rule keyed to an in-sample threshold I
have already evaluated is not a pre-registration — it is a post-hoc justification
wearing one.

This is the same contamination the κ/`k_stop` freeze was built to prevent, arriving
from the other direction. It cannot be undone by writing the rule carefully. It can
only be handled by requiring, for any reopen, **evidence I have not seen.**

That is the load-bearing clause in §3.

## 1. The constant is sourced from the market, not from our table

| component | value | source |
|---|---|---|
| IBKR commission, tiered | $0.0035 / share | published fee schedule |
| SPY half-spread, marketable | ≈ $0.012 | 1–2¢ quoted spread |
| per side | ≈ $0.0155 | |
| **round trip** | **≈ $0.031** | |

Retail market impact on SPY is negligible; institutional $25M VWAP executes around
0.15 bps all-in. **`REALISTIC_COST = $0.031/share`** is therefore declared from
external, citable quantities. It was not chosen because of what it does to any
family — and the check on that claim is that it does *not* do the convenient thing
(§4).

The frozen `SLIP_BPS = 2.0` (≈$0.0776/share at SPY prices, 15.5× the half-spread,
66% of modelled cost) remains defensible **as a deliberately pessimistic upper
bound**. It is not defensible as the sole constant setting an economic floor, which
is what it silently became.

## 2. Both conditions, in the order §5 fixes them

A family is reopened only if, at `REALISTIC_COST`:

```
(b) FLOOR >= MDE(sigma_R, N)        falsifiability — checked FIRST
(a) E[R_net] >= FLOOR               economics
```

**Lowering cost is not monotonically favourable.** Cost sets the floor, so a cheaper
constant lowers the floor — and a floor beneath the detection limit means the study
is *untestable at that N* and closes **unrun**. A cheaper cost can move a family from
one closure to a different closure. The first version of the sweep showed only (a)
and therefore implied a pass where condition (b) had already failed; that was a
defect in the reporting and is corrected.

## 3. A reopen requires evidence not yet seen

Because §0 applies, clearing both conditions **in the published sample is necessary
and not sufficient.** A reopened family must additionally clear both conditions on
**event days not used in the original test**, with the split declared before the
recomputation runs.

A family that clears in-sample and has no admissible out-of-sample split is recorded
as **REOPENABLE — AWAITING OOS**, not as reopened. That is a real state, and it is
where the honest answer lands when the sample is already spent.

## 4. What this rule actually does — stated before it is used as an argument

The sweep is public, so this is verification, not prediction. It is recorded here
because a rule that only ever produces the convenient answer is not a rule:

- **`spy_fomc_double_splash`** — the closure most expected to reopen, at −6.8% from
  its frozen floor — **does not reopen.** At `$0.031` its floor (0.2948) falls
  **below** its MDE (0.4533) at N=84. Condition (b) fails. The closure survives, for
  a different and stricter reason than the one on record: not *failed economics* but
  *untestable at this sample size*. It clears both conditions only in a narrow
  $0.05–$0.08 band, which is a knife-edge artifact and not a result.
- **`spy_macro_decay`** — clears both conditions at `$0.031` (floor 0.3743, MDE
  0.1159, E[R] +0.6331). N = 1,009, so its falsifiability margin is wide. **This is
  the family whose status genuinely depends on the cost constant**, and it is not
  the one that was expected to be.

Per §3, macro_decay is therefore **REOPENABLE — AWAITING OOS**, not reopened.

## 5. What does not change

`az/floor_params.py` stays frozen at `3b10e8b`. `scripts/floor_test.py`'s default
path is unchanged and still computes the pre-registered result exactly — the sweep
asserts its frozen row reproduces the published floor to 1e-9. No verdict in
`FLOOR_TEST_RESULT.md` is edited. `--sweep` reports; it does not adjudicate.

## 6. Verification

```bash
python3 scripts/floor_test.py            # unchanged, pre-registered path
python3 scripts/floor_test.py --sweep    # both conditions, all constants
```
