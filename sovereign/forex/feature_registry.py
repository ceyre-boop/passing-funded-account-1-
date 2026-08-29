"""sovereign/forex/feature_registry.py — every feature declares whether it was computable as-of the bar.

Session brief Phase 3: any LLM-derived feature scored on historical text is
look-ahead-contaminated (a model trained through 2025 already knows what a 2019
print did to the tape). That is fatal and silent, so it is enforced structurally:
a feature may enter a backtest state only if its registry entry says
`as_of_computable=True`. Anything else makes the loader RAISE. Unregistered
names raise too — an unknown feature is not "probably fine".

Two kinds of entries:
  * state features — may be state keys or inputs (`as_of_computable=True`)
  * labels — realized outcomes (`terminal_r`, `incumbent_r_net`): training targets,
    never state keys. They are registered `as_of_computable=False` on purpose so a
    future seat cannot key a policy on the answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class ProvenanceError(RuntimeError):
    """A feature that cannot be computed as-of the bar tried to enter a backtest. Never a warning."""


@dataclass(frozen=True)
class Feature:
    name: str
    as_of_computable: bool
    source: str
    modeled: bool = False       # a model output (static swap table) rather than an observation
    label: bool = False         # realized outcome — training target, never a state key
    note: str = ""


def _f(name, as_of, source, **kw) -> Feature:
    return Feature(name, as_of, source, **kw)


REGISTRY: dict[str, Feature] = {f.name: f for f in (
    # --- path features from the rig's own arrays (scripts/carry_tablebase_paths.py) ---
    _f("t", True, "bars held, counted from the fill bar"),
    _f("close", True, "spot_cache daily close"),
    _f("open_next", True, "spot_cache next-day open — used only to PRICE a delayed fill, never as a state key",
       note="known only after the decision; allowed as a fill price, not a state input"),
    _f("atr_pct", True, "signal_engine._compute_atr_pct at the bar"),
    _f("signal", True, "signal_engine carry signal at the bar (nominal-vintage rates, spec 039 caveat)"),
    _f("hold_today", True, "signal frame hold_days at the bar"),
    _f("weekend_next", True, "index gap to the next bar >= 3 calendar days"),
    _f("unrealized_r_gross", True, "direction*(close-entry)/risk_dist"),
    _f("unrealized_r_net", True, "unrealized_r_gross net of BaseFill cost fractions"),
    _f("incumbent_action", True, "exit_machine.decide_exit output at the bar — the incumbent's own rule"),
    # --- labels: realized outcomes. Targets only. ---
    _f("terminal_r", False, "net R at the absorbing bar", label=True, note="LABEL — the answer, never a key"),
    _f("incumbent_r_net", False, "the incumbent's realized net R", label=True, note="LABEL"),
    _f("absorbed_by", False, "which forced terminal ended the path", label=True, note="LABEL"),
    # --- FX state vector (data/carry/fx_state.jsonl) ---
    _f("rate_diff", True, "FRED policy-rate differential, nominal vintage (31-45d publication lag not modelled)",
       note="spec 039: as-of the observation date, NOT the publication date — usable, flagged"),
    _f("swap_r", True, "fx_state.swap_per_day from a static table (swap_calibration.json absent)", modeled=True),
    _f("cb_calendar_days_to_next", False, "unimplemented — 100% null in fx_state.jsonl"),
    _f("positioning_extreme", False, "unimplemented — 100% null in fx_state.jsonl"),
    # --- LLM / news-derived: look-ahead-contaminated on historical text ---
    _f("sentiment", False, "polygon_news / news_claude sentiment", note="model-scored historical text"),
    _f("polygon_sentiment", False, "polygon news sentiment field", note="model-scored historical text"),
    _f("alpha_operator_bias", False, "daytrade/alpha_operator packet — Claude-read news", note="forward-collected only (MECH-005)"),
    _f("context_directive", False, "daytrade/context_directive recommendation", note="forward-collected only"),
    _f("event_risk", False, "macro calendar weight from AlphaZero's packet", note="forward-collected only"),
)}


def require_as_of(names: Iterable[str], *, context: str = "backtest state") -> tuple[Feature, ...]:
    """Every name must be registered AND as-of computable AND not a label. Raises listing all offenders."""
    bad: list[str] = []
    out: list[Feature] = []
    for n in names:
        f = REGISTRY.get(n)
        if f is None:
            bad.append(f"UNREGISTERED {n!r}")
        elif f.label:
            bad.append(f"LABEL {n!r} cannot be a {context} input ({f.note or 'realized outcome'})")
        elif not f.as_of_computable:
            bad.append(f"NOT-AS-OF {n!r}: {f.source}" + (f" — {f.note}" if f.note else ""))
        else:
            out.append(f)
    if bad:
        raise ProvenanceError(f"feature registry refused {context}:\n  " + "\n  ".join(bad))
    return tuple(out)


def modeled_features(names: Iterable[str]) -> tuple[str, ...]:
    """Which of these are model outputs rather than observations — for the report, not a gate."""
    return tuple(n for n in names if n in REGISTRY and REGISTRY[n].modeled)
