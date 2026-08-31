# Operational Design Domain — Combined Entry/Exit Trading System v0.1

**Owner:** Colin | **Date:** 2026-08-31 | **Status:** DRAFT — T0 SEALED

**System:** AlphaZero entry-selection layer + frozen Stockfish exit core, operated as one autonomy stack.

---

## 0. One-line scope

> This system is permitted to take risk only when **a pre-registered, SPRT-accepted positive-expectancy region has been demonstrated and is currently satisfied**, on **SPY and QQQ, regular session**, and is **flat/idle otherwise.**

**Current resolution of that line: never.** No such region exists as of this version. Two decisive nulls, one per engine. This is not a placeholder to be filled in later with optimism — T0 is sealed and unreachable until §6 change control opens it on evidence.

The ODD is being written _before_ the edge deliberately. Writing it after means writing bounds around whatever you happened to find, which is how an envelope becomes a post-hoc description of a lucky sample.

---

## 1. Envelope

Bounds below are **proposed defaults**, not measured ones. Each needs its value replaced by a measured distribution before it gates anything. Where a value is written as a percentile, the percentile is the commitment; the number it resolves to is data.

| Dimension                               | In-domain                                                                                               | Out-of-domain                                                                                                         | Measured by                                                          | Checked when         |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | -------------------- |
| **Instrument universe**                 | SPY, QQQ only                                                                                           | Everything else, explicitly including the 73-session cross-asset union, single names, options, futures, crypto        | Hardcoded allowlist, hash-pinned at import; mismatch is a hard fault | pre-session          |
| **Liquidity floor**                     | Spread at touch ≤ 2× trailing-20d median; depth at touch ≥ 50% of trailing-20d median                   | Either breached                                                                                                       | L1 book snapshot at decision timestamp                               | pre-trade            |
| **Volatility regime**                   | RV20 within [P10, P90] of trailing 2y; IV/RV within [P10, P90]                                          | Outside band either direction                                                                                         | Realized vol from adjusted bars; IV from chain                       | pre-trade            |
| **Trend/chop regime**                   | **Not in envelope — no measured dependence exists**                                                     | —                                                                                                                     | Logged, not gated (see note below)                                   | pre-trade (log only) |
| **Session / time-of-day**               | 09:45–15:30 ET, regular session                                                                         | First 15 min, last 30 min, extended hours, any session with insufficient forward path remaining to grade an exit      | Exchange clock                                                       | continuous           |
| **Calendar state**                      | No CPI, FOMC, NFP, opex, holiday, or half-day                                                           | Any of the above, and the session following FOMC                                                                      | Economic calendar, pinned vendor                                     | pre-session          |
| **Correlation / cross-asset**           | SPY–QQQ 20d correlation ≥ 0.80                                                                          | Below 0.80                                                                                                            | Rolling correlation on adjusted closes                               | pre-session          |
| **Data freshness & integrity**          | Heartbeat < 2s; zero bar gaps in trailing session; two-vendor agreement within tolerance on last close  | Any breach                                                                                                            | Pipeline monitor + vendor cross-check                                | continuous           |
| **Position state**                      | Flat, or one open exposure                                                                              | Any second concurrent exposure. **SPY and QQQ concurrently are ONE exposure, not two** — same beta, different weights | Position ledger                                                      | pre-trade            |
| **Account state**                       | Drawdown from peak ≤ pre-declared limit; margin utilization ≤ 25%                                       | Either breached                                                                                                       | Broker reconciliation                                                | continuous           |
| **Execution conditions**                | Realized slippage within tolerance of model over trailing K fills; fill rate ≥ threshold; venue nominal | Any breach                                                                                                            | Fill log vs model                                                    | continuous           |
| **Exit-core domain coverage** ← _added_ | Frozen exit core in-domain for the **entire expected holding window**, evaluated at entry               | Exit core out-of-domain at entry, or projected to exit its domain before the position would close                     | Exit-core precondition check, run at entry time                      | pre-trade            |

**Rule: unknown ≠ in-domain. Missing measurement = out-of-domain.**

Note on trend/chop: the template asks for a bound, but there is no measured trend dependence in this system. Inventing a band would be exactly the failure the rule above forbids. It is logged every session so the dependence can be _measured_ later, and until then its absence from the envelope is itself a restriction — the system is not permitted to claim regime-awareness it doesn't have.

---

## 1b. The handoff is part of the domain

This is the dimension a two-engine system needs and a single-engine template doesn't have.

The two engines have **separate domains**, and the system's real domain is the intersection — not the union, and not the entry engine's domain alone.

Failure mode: AlphaZero opens a position while conditions sit inside AZ's envelope but outside the frozen exit core's. The result is an orphaned position — live risk with no graded exit policy authorized to manage it. This is precisely the handoff failure that autonomy systems die on. The car doesn't crash while driving; it crashes in the two seconds after it decides it can no longer drive.

**Rules:**

1. **No entry without exit authorization.** The exit core's preconditions are evaluated _at entry time_, over the expected holding window, not at exit time. An entry that cannot be handed off is illegal — masked at candidate generation (Gate 2 discipline), not scored and rejected.
2. **The exit core never inherits a position it did not authorize.** If an orphan somehow exists, it is a T3 event, not a management problem.
3. **The exit core is frozen and stays frozen.** It does not adapt to accommodate an entry outside its domain. Widening the exit core to accept more entries is a change to _that_ engine's ODD and runs its own §6 process.
4. **Direction of authority is one-way.** The exit core can refuse an entry. The entry layer can never override an exit.

---

## 2. Preconditions gate

ALL must be TRUE to open. ANY false → no new risk; existing positions follow §3.

- [ ] every §1 dimension evaluated, none out-of-domain
- [ ] data pipeline heartbeat < 2 seconds old
- [ ] no unresolved reconciliation break from prior session
- [ ] realized slippage over last K trades within tolerance
- [ ] **exit core in-domain for full expected holding window** (§1b)
- [ ] frozen exit-core checkpoint hash matches the pinned reference
- [ ] entry policy version matches the version that cleared its pre-registration
- [ ] the current pre-registration is unexpired and has not been amended since acceptance
- [ ] sealed holdout remains sealed, or its single authorized unseal is logged
- [ ] **T0 unseal authorization present** — absent in v0.1, so this gate cannot pass

---

## 3. Minimal risk maneuver

| Trigger                                                     | MRM                                                                                                            | Who can override               |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| Data staleness > threshold                                  | Halt new entries, hold existing under exit core, alert                                                         | Nobody — automatic             |
| Vendor disagreement on price                                | Halt new entries, hold existing, alert                                                                         | Colin, logged, same session    |
| Vol regime exits band                                       | Hand existing position to exit core with no re-entry permitted this session                                    | Nobody                         |
| Correlation breaks (SPY–QQQ < 0.80)                         | Halt new entries — the pair rule's premise has failed                                                          | Colin, logged                  |
| Unscheduled halt / limit state                              | Flat at reopen, no re-entry same session                                                                       | Nobody                         |
| Drawdown breach                                             | Flat, lock out N sessions, require written review before re-arm                                                | Nobody — review is mandatory   |
| Execution quality breach                                    | Halt, manual re-arm required                                                                                   | Colin, after root cause logged |
| **Exit core exits its own domain while a position is open** | Flat at next liquid window. Do not wait for the exit core's normal signal — its signal is no longer authorized | Nobody                         |
| **Orphaned position detected**                              | Flat immediately, T3, full incident review                                                                     | Nobody                         |
| Entry/exit version mismatch detected                        | Flat, T3, manual re-arm                                                                                        | Nobody                         |

**Default when a trigger is ambiguous: the more conservative MRM.**

Second default, specific to this system: when the _entry_ layer and the _exit_ core disagree about whether conditions are in-domain, the exit core wins. It is the frozen, verified component. The entry layer is the one that has never passed a gate.

---

## 4. Degradation ladder

- **T0 Nominal** — full size, all setups. **SEALED IN v0.1.** Entry condition: a pre-registered edge has cleared two-sided SPRT under the §6 process, with the pair rule and sub-period stability requirements satisfied. Not currently attainable.
- **T1 Restricted** — reduced size, subset of setups. Entry condition: all §2 preconditions true, and the operating policy has completed its §6 observation period at T1 size.
- **T2 Defensive** — manage existing only, no new risk. Entry condition: any single §1 dimension out-of-domain, or any single §2 precondition false, with no MRM demanding flat.
- **T3 Halt** — flat, manual re-arm required. Entry condition: drawdown breach, execution breach, orphaned position, version mismatch, or two or more simultaneous T2 conditions.

**Re-entry to a higher tier requires:** the triggering condition measured back in-domain continuously for one full session, plus a written root-cause entry in §5, plus a delay of one session at the lower tier. T3 → T1 additionally requires manual re-arm; there is no automatic path out of T3, and no path from T3 to T0 at all — it routes through T1.

Ratchet rule: tiers degrade automatically and instantly. They recover slowly and manually. Asymmetry is the point.

---

## 5. Disengagement log

The primary health metric.

| Date | Tier at time | What the system did/wanted | What I would have done | Delta | Root cause | ODD change? |
| ---- | ------------ | -------------------------- | ---------------------- | ----- | ---------- | ----------- |

**Target:** disengagements per 100 decisions, trending down.

**Rule: three disengagements with the same root cause = ODD defect, not a judgment call.**

Two additions for a two-engine stack:

- Every row records **which engine** wanted what. A disengagement where the two engines disagreed with each other is a different defect class from one where both agreed and you overrode them, and mixing them hides the handoff failures.
- Log disengagements at **T2 and T3 as well**, not just when live. A system that would have done the wrong thing while it was halted is still telling you the envelope is wrong — and right now, with T0 sealed, these are the only rows you can generate. Paper disengagements are the whole dataset at this stage.

---

## 6. Change control

**The ODD only widens on evidence, never on a good week.**

| Field              | Requirement                                                                                                                                                                     |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Proposed change    | Written before any supporting table is generated                                                                                                                                |
| Evidence required  | Pre-registered hypothesis, two-sided SPRT accept, pair rule satisfied on both SPY and QQQ, sign agreement across all four sub-periods with magnitude clearing in at least three |
| Sample size        | Declared as **N_days**. Candidate rows reported separately, labeled `not the sample size`                                                                                       |
| Observation period | Minimum one full sub-period equivalent at T1 size before T0 is considered                                                                                                       |

- Any widening runs at T1 size for N sessions before T0.
- Log every version; never edit in place.
- **Narrowing requires no evidence and takes effect immediately.** Only widening is gated. An ODD you can't tighten instantly is not a safety artifact.
- The exit core's ODD and the entry layer's ODD are versioned **separately** and widened **separately**. A joint widening — both engines' domains expanded in the same change — is prohibited, because it makes the resulting behavior unattributable to either engine.
- Unsealing T0 is itself a change-control event, requiring everything in the table above plus an explicit written statement of what result authorized it and against which frozen commit.

---

## Version log

| Version | Date       | Change                                          | Authorized by |
| ------- | ---------- | ----------------------------------------------- | ------------- |
| v0.1    | 2026-08-31 | Initial draft. T0 sealed. No edge demonstrated. | —             |
