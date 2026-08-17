# 026 — STOCKFISH CONDITIONAL COMPETENCE `[SPEC]`

**Component:** `daytrade/competence_report.py`, urgency-schedule hook in
`ceiling.simulate`
**Status:** built 2026-08-17 (card written same session as code — noted; the
measurement is diagnostic/read-only, tune split only, no live-path change).
**Origin:** Colin's reframe: Stockfish is not a standalone alpha source; its
job is flawless execution of the exit given AlphaZero's context. Measure
conditional competence — where the engine's vocabulary leaks R under a fixed
policy, and how much each AlphaZero output CHANNEL could recover.

## Method

Per tune-split entry: shipped-policy R, best-in-vocabulary R (396-config
hindsight oracle), and two counterfactual AlphaZero signals injected through
the REAL channel (`st.urgent` via a schedule on the simulator): perfect
`exit` and perfect `tighten` from shock onset. Days labeled from the tape
(with_trade / against_trade / failed_breakout / range / risk_event) using the
operator's outcome constants. All oracle/az numbers are hindsight upper
bounds, labeled, never expected results.

## Findings (2026-08-17 run — 336 entries, 134k sims, 85s)

1. **Total vocabulary leak: +0.67 R/trade** (oracle 396-config per-day pick
   vs shipped). The engine's reachable-but-untaken exits are worth ~5× the
   shipped policies' entire mean. This is THE AlphaZero prize, priced.
2. **The interrupt channel cannot reach it.** Perfect-hindsight `exit` at
   shock onset recovers ≈0 in 11 of 12 cells (+0.355 only in
   CASH_INDEX/risk_event); perfect `tighten` — the operator's ENTIRE current
   authority — recovers ≈0 everywhere. Training AlphaZero to fire better
   interrupts optimizes a channel worth almost nothing.
3. **The leak lives in per-day CONFIG selection** — biggest cells:
   risk_event (+1.1..1.5 R), futures failed_breakout (+1.16), with_trade
   (~+0.7: winners under-harvested), against_trade (~+0.5: the oracle exits
   losers early via bank-early/flatten shapes). That is the
   `policy_candidate` channel: 018 already carries `recommendation`, the
   runner already logs-but-does-not-apply it (`policy_candidate=None`
   hardcoded), and applying it requires authority level 2 — the 017
   promotion gate. The wiring seam identified in the first session
   assessment is exactly where the money is.
4. **Within a config, the engine does not leak.** The oracle is defined over
   the engine's own vocabulary; the gap is choice, not execution. Stockfish
   executes; the open problem is which config today — AlphaZero's designed
   job, requiring calendar time in the soak to learn honestly.

## Consequence for training

AlphaZero's scoreable output that matters is the RECOMMENDATION (policy
family for the day), not the interrupt. The soak's sealed records already
capture verdict+forecast; the promotion path to authority 2 is the road the
+0.67 prize sits behind. Interrupts stay as tail-risk insurance, not the
value channel.

## Addendum — the oracle audit (2026-08-17, same night)

Cross-chat review deflated the +0.67 headline; every move ran same night
(`daytrade/oracle_audit.py`, gates + prediction committed before first run):

- **Shortlist oracle (6 families):** leak drops +0.67 → +0.40. NOON_FLAT is
  the best fixed family on the pooled basket (+0.151).
- **Null oracle: my pre-registered permutation was MIS-DESIGNED** — permuting
  family columns independently destroys the strong within-day correlation
  between families and inflates the null max (+1.74, gate FAIL). Recorded as
  a design error, not evidence; the load-bearing number is the tree bound.
- **Information-limited realizable bound (the number that matters):** a
  depth-3 tree on entry-time features, day-grouped OOF, choosing among the 6
  families: **−0.028 R/trade vs always-NOON_FLAT. DRAWER** — below the
  reviewer's 0.15–0.30 prediction floor. Even risk-event days gain nothing
  (tree +0.227 vs fixed +0.270). OOF oracle-agreement 46% vs 51%
  majority-class — the tree cannot beat "always pick the modal family".
- **Move 4 (calendar rule) rendered moot on this data:** the risk-event cell
  is already captured by the fixed policy; config choice adds ~0 there.
- **Sample size, registered:** per-day σ=0.70 → +0.20 R/trade detectable in
  ~75 trading days. Cheap — but academic while realizable ≈ 0.

**Standing conclusions:**
1. Interrupt channel RETIRED in code (`EMISSION_MODE = "log-only"` in
   alpha_operator.py). Re-arming requires new evidence through this harness.
2. Mechanical policy-selection (features → family) adds ~zero. Every
   mechanical channel is now measured: exits tuned to a validated dead end,
   interrupts zero, mechanical config-choice zero.
3. **AlphaZero's entire remaining case is non-mechanical information** —
   news, narrative, the thing not in a feature column — which no harness
   here can price. Only the soak's sealed judgments accumulate that
   evidence. The oracle study stays what gold-58 was: a ranking instrument
   and a channel-killer, never a target.
