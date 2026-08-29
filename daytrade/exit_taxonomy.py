#!/usr/bin/env python3
"""Exit mechanism taxonomy — Stockfish's opening book (spec 044).

A chess engine is strong because of a move generator that enumerates every
legal move plus an evaluation that prunes to a handful, while the discarded
moves stay enumerable rather than invisible. `daytrade/stockfish_exit.py`
already builds every stop layer, active or not, and each dark one carries the
reason it is dark. What was missing was a completeness claim and an
enforcement — this module is that: a checkable registry of every exit
mechanism this repo has considered, its status, and (for the falsified ones)
where the kill is recorded.

PHASE A — THIS MODULE DOES NOT TOUCH `stockfish_exit.py`. It does not import
it at module scope in any way that would change its content, and it must
never be edited alongside it. `frozen_policy.engine_sha256()` is a content
hash of that file; this module exists precisely so a future mechanism gets
proposed against a registry instead of by re-reading the source and hoping.

THE PARAMETER FIREWALL (spec 044): a mechanism's `definition` describes what
it IS — vocabulary, safe to write from a textbook. The PARAMETER (the
multiple, the lookback, the minute) is an empirical claim about a specific
market and era and must never be embedded in the definition string or
defaulted anywhere. Enforced here as: no standalone numeric literal (a digit
not preceded by a letter) in any `definition`.

THE LIBRARY MAY GROW. THE MENU MAY NOT. Adding an entry to `MECHANISMS` does
not add it to Stockfish's selectable set — only replacing something in the
pinned-opponent comparison (spec 033) does that.

AMENDMENT 1 (2026-08-29) — MECHANISMS vs CLAIMS ABOUT MECHANISMS. The original
cut of this module tried to fold `MECHANISMS.json`'s falsified claims
(MECH-001..004) into `Mechanism` rows carrying `status="FALSIFIED"`. That is a
category error: "a wider trailing stop beats static" is not a mechanism — the
`trail` mechanism is `ACTIVE` and always was; what died is a CLAIM about how
to configure it. `MECH-004` isn't about any single mechanism at all — it's a
claim about the *selection process* over the whole menu. `FalsifiedClaim` /
`FALSIFIED_CLAIMS` below are the second, sibling structure that fixes this;
`about=()` on a claim is meaningful (a process-level claim), not a gap.

KNOWN LEDGER DISCREPANCY, NOT RECONCILED HERE: `MECH-001` ("a wider trailing
stop captures more of the winning tail") is `status="proposed"` in
`MECHANISMS.json` even though `029_MUTATION_LOG.md:21-35` records "Trailing
genuinely HURTS" and the `trail_mult` config it wrote was removed — in prose
it reads exactly like a kill. Per Amendment 1: do not edit `MECHANISMS.json`
to reconcile this and do not add MECH-001 to `FALSIFIED_CLAIMS` on that
prose reading alone. I60a binds on the ledger's own `status` field, so this
taxonomy reports what the ledger says a kill is, not what an agent infers one
to be. Colin's ruling to make, not this module's.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mechanism:
    name: str
    family: str          # STOP_PLACEMENT | SCALING | TIME | INVALIDATION | TARGET
    definition: str      # what it IS. No standalone numeric literal.
    requires: tuple      # TradeState field names it needs
    status: str          # ACTIVE | DARK | FALSIFIED | OUT_OF_SCOPE
    reason: str          # required non-empty unless ACTIVE
    evidence: object      # MECHANISMS.json id, spec file, or mutation-log ref, or None
    constitution: tuple  # which C00x constrain it


@dataclass(frozen=True)
class FalsifiedClaim:
    """A killed CLAIM ABOUT one or more mechanisms (or about the selection
    process itself) — distinct from a `Mechanism`, which is the move, not a
    claim about how to configure or select among moves. See AMENDMENT 1."""
    mech_id: str          # MECH-00x, must resolve to a real MECHANISMS.json id
    claim: str             # the hypothesis as originally stated
    outcome: str            # what killed it, and where that is recorded
    about: tuple            # mechanism names it concerns; MAY be empty for a
                            # process-level claim (e.g. MECH-004)


MECHANISMS = (
    # ------------------------------------------------------------ STOP_PLACEMENT
    Mechanism(
        name="catastrophic",
        family="STOP_PLACEMENT",
        definition="the original plan stop, sized before entry; never removed, never loosened",
        requires=("catastrophic_sl",),
        status="ACTIVE",
        reason="",
        evidence=None,
        constitution=("C003",),
    ),
    Mechanism(
        name="breakeven",
        family="STOP_PLACEMENT",
        definition="once the trade reaches an armed fraction of the first target, the stop moves to entry so the day cannot go red",
        # NOTE: spec 044's move-list table names `tp1_done` here, but that is a
        # derived @property (state.stage >= PROTECTED), not a TradeState
        # dataclass field, so it fails I62's literal "real key of
        # TradeState.__dataclass_fields__" test. `stage` is the actual field
        # that property reads. See builder report for spec 044 on this swap.
        requires=("entry", "stage", "be_arm_frac"),
        status="ACTIVE",
        reason="",
        evidence=None,
        constitution=("C003",),
    ),
    Mechanism(
        name="profit_lock",
        family="STOP_PLACEMENT",
        definition="once the day goal is banked, the stop rides forward to the first target so the banked gain cannot give back below it",
        # NOTE: same swap as breakeven above — `tp2_done` is a derived
        # @property (state.stage >= SCALED); `stage` is the real field.
        requires=("tp1", "stage"),
        status="ACTIVE",
        reason="",
        evidence=None,
        constitution=("C003",),
    ),
    Mechanism(
        name="trail",
        family="STOP_PLACEMENT",
        definition="the stop rides a fixed distance behind the favorable extreme reached since entry",
        requires=("hwm", "trail_dist", "trail_mult"),
        status="ACTIVE",
        reason="",
        evidence=None,
        constitution=("C003",),
    ),
    Mechanism(
        name="volatility",
        family="STOP_PLACEMENT",
        definition="the stop sits a defensible multiple of average true range below the favorable extreme, wide enough to survive normal noise",
        requires=("atr", "hwm"),
        status="DARK",
        reason="needs ATR on TradeState and a defensible multiple; no caller supplies it. "
               "See Phase B of the plan.",
        evidence=None,
        constitution=("C003",),
    ),
    Mechanism(
        name="thesis",
        family="STOP_PLACEMENT",
        definition="the stop sits at the price level that would invalidate the entry thesis",
        requires=("thesis_sl",),
        status="DARK",
        reason="needs an invalidation level; regime.py raises at import (spec 001). Must be "
               "derived mechanically on the Stockfish side — ContextDirective structurally "
               "refuses any stop_price.",
        evidence=None,
        constitution=("C003",),
    ),
    Mechanism(
        name="time_decay",
        family="STOP_PLACEMENT",
        definition="the stop tightens on a schedule as the session clock runs down toward the flatten deadline",
        requires=("now_et", "flatten_at_et"),
        status="DARK",
        reason="today time pressure is a cliff, not a schedule. Prerequisites: the 3-of-24 "
               "entry problem addressed, plus a counterfactual showing schedule beats cliff "
               "on days that reached the first target.",
        evidence=None,
        constitution=("C003",),
    ),
    Mechanism(
        name="structural",
        family="STOP_PLACEMENT",
        definition="the stop sits beyond the most recent swing point in price structure",
        requires=(),
        status="DARK",
        reason="no swing detector exists; regime.py is the natural home.",
        evidence=None,
        constitution=("C003",),
    ),

    # ------------------------------------------------------------------ SCALING
    Mechanism(
        name="ladder_partial",
        family="SCALING",
        definition="a fraction of the position is reduced at each target rung on the ladder",
        requires=("goal_fraction",),
        status="ACTIVE",
        reason="",
        evidence=None,
        constitution=("C001",),
    ),
    Mechanism(
        name="scale_in",
        family="SCALING",
        definition="quantity is added to a working position as it moves favorably",
        requires=(),
        status="OUT_OF_SCOPE",
        reason="C001 forbids any qty increase. Structurally unavailable, not merely unused.",
        evidence=None,
        constitution=("C001",),
    ),
    Mechanism(
        name="structural_scale",
        family="SCALING",
        definition="a fraction of the position is reduced at prior price structure rather than at a fixed multiple of risk",
        requires=(),
        status="DARK",
        reason="scaling at prior structure rather than an R multiple; same blocker as the "
               "structural stop — no swing detector exists.",
        evidence=None,
        constitution=("C001",),
    ),

    # --------------------------------------------------------------------- TIME
    Mechanism(
        name="session_flatten",
        family="TIME",
        definition="the whole position exits at a clock deadline regardless of price",
        requires=("flatten_at_et",),
        status="ACTIVE",
        reason="",
        evidence=None,
        constitution=(),
    ),
    Mechanism(
        name="event_flatten",
        family="TIME",
        definition="the whole position exits ahead of a known scheduled catalyst",
        requires=("flatten_at_et",),
        status="ACTIVE",
        reason="",
        evidence=None,
        constitution=(),
    ),
    Mechanism(
        name="max_hold",
        family="TIME",
        definition="the position exits once it has been held longer than a maximum duration",
        requires=(),
        status="OUT_OF_SCOPE",
        reason="a carry-lane concept. I53: CarryState and TradeState share no terms; neither "
               "is a superset.",
        evidence=None,
        constitution=(),
    ),

    # ------------------------------------------------------------- INVALIDATION
    Mechanism(
        name="urgency_exit",
        family="INVALIDATION",
        definition="AlphaZero's urgent-exit signal flattens the whole position immediately, outranking every other rule",
        requires=("urgent",),
        status="ACTIVE",
        reason="",
        evidence=None,
        constitution=("C007",),
    ),
    Mechanism(
        name="urgency_tighten",
        family="INVALIDATION",
        definition="AlphaZero's urgent-tighten signal halves the trail distance",
        requires=("urgent", "trail_mult"),
        status="ACTIVE",
        reason="the channel exists and is wired; what was killed is the claim that it adds R. "
               "Value claim FALSIFIED — see MECH-003.",
        evidence="MECH-003",
        constitution=("C007",),
    ),
    Mechanism(
        name="thesis_break",
        family="INVALIDATION",
        definition="the position exits when the entry thesis is invalidated",
        requires=("thesis_sl",),
        status="DARK",
        reason="same blocker as the thesis stop — needs an invalidation level from the "
               "classifier; regime.py is a stub.",
        evidence=None,
        constitution=(),
    ),
    Mechanism(
        name="correlation_break",
        family="INVALIDATION",
        definition="the position exits when a correlated asset breaks a level that undermines the thesis",
        requires=(),
        status="OUT_OF_SCOPE",
        reason="no supplier; would need a cross-asset feed the engine does not have.",
        evidence=None,
        constitution=(),
    ),

    # -------------------------------------------------------------------- TARGET
    Mechanism(
        name="fixed_r",
        family="TARGET",
        definition="targets are set as fixed multiples of the initial risk",
        requires=("tp1", "tp2"),
        status="ACTIVE",
        reason="",
        evidence=None,
        constitution=(),
    ),
    Mechanism(
        name="atr_target",
        family="TARGET",
        definition="targets are set as a defensible multiple of average true range from entry",
        requires=("atr",),
        status="DARK",
        reason="same ATR blocker as the volatility stop — needs ATR on TradeState and no "
               "caller supplies it.",
        evidence=None,
        constitution=(),
    ),
    Mechanism(
        name="measured_move",
        family="TARGET",
        definition="targets are set by projecting a prior swing's magnitude from a breakout point",
        requires=(),
        status="DARK",
        reason="needs structure detection — no swing detector exists.",
        evidence=None,
        constitution=(),
    ),
)


# --------------------------------------------------------------- falsified claims
#
# Claims ABOUT mechanisms (or about the selection process itself), as distinct
# from the mechanisms (moves) in MECHANISMS above. AMENDMENT 1 to spec 044.
# I60a binds this tuple against every `status == "killed"` id in
# MECHANISMS.json — currently MECH-002, MECH-003, MECH-004. MECH-001 stays
# out deliberately; see the module docstring's KNOWN LEDGER DISCREPANCY note.
FALSIFIED_CLAIMS = (
    FalsifiedClaim(
        mech_id="MECH-002",
        claim="protection earlier than TP1 plus a hard midday deadline beats riding the full session",
        outcome="killed — sealed holdout read 2026-08-17: -0.107 R/trade on 98 sealed "
                "entries vs +0.114 on the tune split (data/daytrade/sealed_read_futures_v1.json). "
                "NOT_VALIDATED, retired by its own pre-registration.",
        about=("breakeven", "session_flatten"),
    ),
    FalsifiedClaim(
        mech_id="MECH-003",
        claim="AlphaZero adds value by timing interrupts — a well-timed tighten or exit at "
              "shock onset protects R the fixed policy would give back",
        outcome="killed — perfect-hindsight tighten gave ~0 uplift in every cell, perfect "
                "exit ~0 uplift in 11 of 12. Channel retired in code "
                "(alpha_operator.EMISSION_MODE = 'log-only').",
        about=("urgency_tighten",),
    ),
    FalsifiedClaim(
        mech_id="MECH-004",
        claim="the right exit configuration is knowable from entry-time price features, so a "
              "per-day config choice beats one fixed policy",
        outcome="killed — spec 025 returned NO_SUPERSEDE; oracle_audit returned "
                "NOTHING_QUOTABLE twice. Not about any single mechanism: a claim about the "
                "selection process over the whole menu.",
        about=(),
    ),
)


def _by_family():
    families = {}
    for m in MECHANISMS:
        families.setdefault(m.family, []).append(m)
    return families


if __name__ == "__main__":
    families = _by_family()
    for family in ("STOP_PLACEMENT", "SCALING", "TIME", "INVALIDATION", "TARGET"):
        entries = families.get(family, [])
        print(f"\n{family}")
        for m in entries:
            marker = {"ACTIVE": "ACTIVE  ", "DARK": "dark    ",
                      "FALSIFIED": "DEAD    ", "OUT_OF_SCOPE": "n/a     "}.get(m.status, m.status)
            print(f"  {marker} {m.name:20s} {m.reason or m.definition}")
    total = len(MECHANISMS)
    counts = {}
    for m in MECHANISMS:
        counts[m.status] = counts.get(m.status, 0) + 1
    print(f"\n{total} mechanisms: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))

    print(f"\nFALSIFIED CLAIMS ({len(FALSIFIED_CLAIMS)})")
    for c in FALSIFIED_CLAIMS:
        about = ", ".join(c.about) if c.about else "(process-level — no single mechanism)"
        print(f"  {c.mech_id}  about: {about}\n    claim: {c.claim}\n    outcome: {c.outcome}")
