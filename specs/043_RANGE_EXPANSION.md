# 043 — RANGE EXPANSION: is a scheduled macro event a REGIME CONDITIONER?

**Status: [SPEC] — PRE-REGISTERED 2026-08-28, BEFORE any number was computed.**
Written to disk before the study script existed. Nothing in this file may be
altered after a result is seen. If a prediction below turns out wrong, the
prediction stays as written and the result is reported as a falsification.

---

## Why this study exists

Prior work in this repo established that scheduled macro events (08:30 ET
releases, FOMC) produce a large, real, replicated price IMPULSE in SPY — and
that the impulse is DIRECTIONLESS. Three pre-registered attempts to trade its
direction all failed:

- bracket harvest died on costs
  (`data/daytrade/event_study/spy_bracket_harvest_study_summary.json`);
- pre-FOMC drift was underpowered;
- the second-wave/continuation test came back null despite confirmed elevated
  volume (`data/daytrade/event_study/spy_splash_continuation_study_summary.json`).

So we pivot. Stop using the event as a directional trigger. Test whether it is
a **regime conditioner**: does the calendar tell us the day's *magnitude* will
be larger, regardless of direction?

---

## PRE-REGISTRATION — FIXED

### PRIMARY STATISTIC

For each day,

    expansion = RTH_true_range / ATR20_prior

where:

- `RTH_true_range` = (max high − min low) over 09:30–16:00 ET regular trading
  hours ONLY.
- `ATR20_prior` = the 20-day average true range computed from sessions
  STRICTLY BEFORE that day. It must use no bar from the day being measured.
  This is the load-bearing anti-lookahead property — a test must FAIL if a
  same-day bar leaks in.

### PRIMARY COMPARISON

Mean `expansion` on scheduled-macro-event days vs mean `expansion` on matched
non-event control days. One-sided (expansion is the predicted direction, fixed
in advance by the hypothesis, not chosen after looking).

### WHY NORMALIZED, NOT RAW RANGE — the central confound

Macro events cluster inside high-volatility regimes. A raw-range comparison
would find a large "effect" that is nothing but volatility autocorrelation: vol
was already elevated before the event and merely persisted. Dividing by
ATR20_prior asks the only question that matters — is the day bigger *than its
own recent baseline predicted*. If raw range is reported anywhere it is
reported explicitly labelled as confounded and never as the primary.

### ECONOMIC FLOOR, REGISTERED IN ADVANCE

An expansion ratio below **1.10** is NOT economically useful even if
statistically significant — it cannot move a profit target or an ORB threshold
beyond noise and slippage. If the result is significant but under 1.10, the
report must say plainly that it is significant and useless. This is not to be
softened.

### RTH ONLY, impulse excluded

The 08:30 ET impulse is outside the 09:30 open by construction. This is
deliberate — measuring a window containing the impulse would just re-measure
the known result.

### SECONDARY (exploratory; BH-corrected within its own family; may NEVER be promoted to primary)

1. ORB (opening range breakout) payoff conditional on event day vs control day.
2. Range decomposition: 09:30–12:00 expansion vs 12:00–16:00 expansion — does
   the elevation persist all day or decay?
3. Split by event type (FOMC vs 08:30 data releases) if the calendar supports
   it.

### PREDICTIONS, recorded before the run

- Expansion is elevated, likely in the 1.05–1.25 band.
- It clears the 1.10 floor for FOMC and possibly not for routine 08:30
  releases.
- The elevation decays through the day, so the 09:30–12:00 half is larger than
  the afternoon half.

If any of these is wrong, it is reported as wrong. A failed prediction is a
finding, and softening it is the one unforgivable outcome here.

---

## OPERATIONAL DEFINITIONS — also fixed before the run

These make the statistic above computable. They are registered here, before the
script was written, and are as binding as the section above.

### Instrument and bars

SPY 5-minute bars from `data/daytrade/bars_premarket/SPY_5m.parquet` (Alpaca
SIP, raw, 2016-01-01 forward — the same cache every sibling study in this
family reads, via the same direct-parquet read, never through
`bars.load_sessions`). The `data/daytrade/bars_extended/` cache holds NVDA
only and cannot serve this study.

### RTH session

Bar START timestamps 09:30 through 15:55 inclusive — 78 bars, covering
09:30–16:00. A day missing ANY of those 78 bars is EXCLUDED with a printed
reason and never interpolated (half days therefore drop out by construction),
matching `bars.load_sessions`' own completeness discipline.

- `rth_high` = max High over the 78 bars; `rth_low` = min Low.
- `rth_range` = `rth_high − rth_low` (points). `RTH_true_range` in the primary
  statistic IS this quantity, exactly as pre-registered above.
- `rth_close` = Close of the 15:55 bar.

### ATR20_prior

True range of session *i* uses the standard Wilder definition against the
PRIOR session's RTH close:

    TR_i = max( H_i − L_i, |H_i − C_{i−1}|, |L_i − C_{i−1}| )

`ATR20_prior(d)` = mean of `TR_i` over the 20 complete sessions immediately
preceding `d` in the cached session list. Every bar entering it is timestamped
strictly before `d`'s own session. A day with fewer than 20 prior complete
sessions available (or whose 20-session window cannot reach a prior close) has
NO expansion value and is excluded — never back-filled, never computed on a
short window.

### REGISTERED DENOMINATOR ROBUSTNESS (not a second primary)

`TR` includes the overnight gap; the numerator (`rth_range`) does not. That
asymmetry biases `expansion` DOWNWARD, which matters against a 1.10 floor. So
a second denominator is registered here in advance:

    ATR20_prior_rth = mean of the prior 20 sessions' own `rth_range`

`expansion_rth = rth_range / ATR20_prior_rth` is computed and reported beside
the primary. The PRIMARY remains the Wilder-TR version. `expansion_rth` is a
robustness reading, is labelled as such, and cannot be promoted.

### Study span

The event/control labelling span is bounded by the span over which the event
calendar is COMPLETE. Outside it, a day cannot be verified to be a non-event
day, and a contaminated control pool is worse than a short one. The historical
FRED release calendar available to this repo
(`data/daytrade/event_study/fred_historical_release_dates.json`, built by
`scripts/build_macro_event_study.py`) spans 2024-01-02 → 2026-08-25. The study
span is that intersection with the bar cache. ATR20_prior for the first days of
the span legitimately reads sessions from BEFORE the span — that is prior data,
not out-of-span labelling, and is correct.

### Event days

Union of:

- the six FRED-tracked scheduled releases (CPI, PPI, Employment Situation,
  GDP, Personal Income and Outlays, Initial Claims) — same set and same
  `RELEASES` ids every sibling study uses; and
- SCHEDULED FOMC decision dates from `data/daytrade/fomc_calendar.json`, read
  through `daytrade/macro_calendar.py` and validated by that module's
  `validate_fomc_events` fabrication guard. Any entry flagged
  `unscheduled: true` is EXCLUDED by construction — the pool/ripple thesis is
  about foreknowledge, and a surprise meeting violates the premise.

### Control pool and matching

Same convention the sibling studies already use: a control day is a session in
the span that is not itself an event day for ANY tracked source, restricted to
the weekdays actually present in the event population. The siblings
deliberately do NOT buffer adjacent days and state the trade-off openly
(weekly Initial Claims would otherwise make nearly every Wednesday–Friday
"near an event" and collapse the pool); that trade-off biases the
event-vs-control gap TOWARD zero and is therefore conservative.

Because the adjacency risk is real, a **BUFFERED-CONTROL ROBUSTNESS ARM** is
registered here in advance: the same primary comparison recomputed against a
control pool that additionally excludes any session within ±1 trading day of
an event day. It is a robustness reading, not a second primary. The primary is
only read as clean if the buffered arm agrees in sign.

### Tests, null, power

- Welch two-sample t (unequal variance), one-sided in the pre-registered
  direction (event > control), with 95% CIs on both means — the same `_welch`
  helper the siblings use.
- Permutation null: 2000 reshuffles of WHICH days carry the event label,
  without replacement, over the pooled day population; each day's own
  expansion value never changes, only the labelling. Fixed seed. Reported
  beside the parametric p.
- POWER: minimum detectable effect at one-sided 95% / 80% power, using this
  repo's existing convention (`daytrade/mechanisms.mde`, Z_ALPHA=1.645,
  Z_POWER=0.842) in its two-sample form,
  `MDE = (Z_a + Z_b) * sigma_pooled * sqrt(1/n_ev + 1/n_ctrl)`, reported in
  expansion units AND as the ratio it corresponds to. A null with the MDE
  printed is a measured absence; a null without it is blind.

### Secondary operational definitions

1. **ORB.** Opening range = high/low of 09:30–10:00 (6 bars, starts 09:30–09:55).
   `orb_width = or_high − or_low`. From the 10:00 bar onward, the FIRST bar
   whose High > `or_high` (long) or Low < `or_low` (short) triggers entry at
   that boundary price; a bar breaking both sides is recorded as ambiguous and
   assigned NO trade (never resolved by assuming an order). Stop = the opposite
   OR boundary; no target; exit at the 15:55 Close if the stop is not hit.
   Payoff is expressed in R where 1R = `orb_width`. GROSS of costs, labelled
   gross — the bracket-harvest study already showed costs are what kills this
   family, so a gross number here is a diagnostic, not a tradeable claim.
2. **Range decomposition.** `expansion_am` = (max high − min low over bar
   starts 09:30–11:55) / ATR20_prior; `expansion_pm` = same over 12:00–15:55.
   Same denominator for both, so the two halves are directly comparable.
3. **Event-type split.** FOMC-only days (scheduled FOMC) vs 08:30-release days
   with no FOMC. The calendar carries `release_name` and a distinct FOMC
   source, so this arm is supported. No field is invented: if a needed flag
   does not exist, the arm is SKIPPED and said to be skipped.

All secondaries are BH-corrected within the secondary family and within it
only. The primary is never in a BH family with them.

### FAIL LOUD

A missing bar excludes a day with a reason. A missing calendar source refuses
the run. Nothing is defaulted to zero. No value is interpolated, forward-filled
or guessed.

---

# POST-RUN ADDENDUM — a defect in this spec, and what the result actually says

Written by Opus after independently re-checking the agent's numbers. The
registered text above is UNCHANGED and stays on the record as written. Nothing
below re-defines a threshold to make a failing result pass; it records that the
threshold was mis-specified and that the honest consequence is a re-registration,
not a rescue.

## The spec defect

The primary statistic divides an RTH-only numerator by a gap-inclusive
denominator:

- numerator `RTH_true_range` = max High − min Low over 09:30–16:00. No overnight gap.
- denominator `ATR20_prior` = 20-day average TRUE range, which by definition
  includes `|High − prev_Close|` and `|Low − prev_Close|`, i.e. the overnight gap.

So an ordinary day does NOT score 1.0 under this statistic — it scores **0.873**.
The `1.10` economic floor was written as though 1.0 meant "a normal day," and it
does not. The floor was therefore calibrated against the wrong scale, by me, in
advance. That is a spec error, not a data finding.

Evidence it is purely a units artifact: the scale-free LIFT is essentially
identical under both denominators —

| denominator | event | control | lift |
|---|---|---|---|
| ATR20 (registered, gap-inclusive) | 0.9862 | 0.8733 | **1.1293** |
| RTH-only (apples-to-apples) | 1.1242 | 0.9950 | **1.1298** |

## What follows from that — and what does NOT

The lift is the quantity that survives the defect. It is ~1.13.

It does **not** follow that the study passes. Reading a lift-based floor off a
result after seeing it is post-hoc, and post-hoc thresholds are the exact failure
this repo's pre-registration discipline exists to prevent. The registered floor
FAILED. A lift-based floor was never registered. Both facts stand together, and
neither is quotable alone.

## The finding that actually decides this — the buffered arm

Independent of the floor question, the primary comparison carries a compositional
bias:

- control pool, unbuffered: mean 0.8733, n=290
- control pool, ±1 session removed: mean **0.9402**, n=91
- lift 1.129 → **1.049**; one-sided p 0.0062 → **0.260**

Removing event-ADJACENT days RAISES the control mean. That means the unbuffered
control pool is diluted with unusually quiet days sitting next to events, which
depresses the baseline and inflates the lift. The cleaner comparison more than
halves the effect and loses significance.

The buffered arm is itself underpowered (n=91, MDE lift 1.179), so it is not a
refutation. But a point estimate that halves under a bias correction is a bias
signal, not a power signal, and the two must not be conflated.

Note also: events are 244/661 = 37% of sessions here, so ±1 buffering strips 69%
of the control pool. Whether the surviving 91 days are themselves a representative
subset is UNVERIFIED and is a live threat to the buffered arm in both directions.

## Power — the result is knife-edge

MDE at one-sided 95% / 80% power = 0.1112 expansion units = smallest detectable
lift **1.127**. Observed lift **1.129**. The design can detect almost exactly the
effect it reported and nothing smaller. A result sitting on its own detection
threshold is a coin flip on replication.

To detect lift 1.10 needs n_eff ≈ 215 (1.6x current). To detect 1.06 needs
n_eff ≈ 597 (4.5x current).

## Span limitation is the binding constraint, and it is cheap to lift

The study spans 2024-01-02 → 2026-08-25 only, because no `FRED_API_KEY` exists in
this checkout and the cached release calendar covers exactly that. SPY bars go
back to 2016. A FRED key extends the event calendar ~4x, which takes MDE from
lift 1.127 to roughly 1.06 and converts this from a knife-edge into a real test.

## Registered verdict

**NOT ESTABLISHED.** Not "false" — the sign is positive in every arm and the
mechanism is plausible. But: the registered floor failed on a mis-specified
scale, the bias-corrected arm is not significant, and the design is powered to
its own result. Re-register with (a) a matched numerator/denominator, (b) a floor
stated on the LIFT, (c) the buffered pool as PRIMARY not robustness, and (d) the
FRED-extended span. Do not trade this.
