#!/usr/bin/env python3
"""FORECAST LEDGER + MODEL PROMOTION — spec 017.

Every AlphaZero prediction is recorded BEFORE its outcome is known and graded
after — against a dumb baseline, on identical cases (the repo's rule 3). The
promotion gate is CODE: nobody, including the model's biggest fan after three
great trades, gets to look at a hot week and declare the challenger promoted.

Structural guarantees:
  - `resolve()` refuses any resolution timestamped before as_of + horizon —
    an outcome physically cannot enter a forecast early
  - the original claim is frozen; resolution is a separate record
  - `promotion_decision()` does not accept returns as an input, so "but it
    made more money" is unrepresentable, not merely discouraged
  - promotion emits an explicit event whose ref is what the 016 authority
    registry demands for any grant above the unpromoted cap
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

from scenarios import ALL_SCENARIOS

PROB_TOL = 1e-6
DIRECTIONS = ("up", "down", "flat")
URGENT_INTERRUPTS = frozenset({"INVALIDATE", "EMERGENCY"})


class ForecastError(RuntimeError):
    """Malformed forecast, impossible resolution, or a gate being argued with."""


def _aware(ts: str, field_: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        raise ForecastError(f"{field_}={ts!r} is not ISO-8601") from e
    if dt.tzinfo is None:
        raise ForecastError(f"{field_}={ts!r} is timezone-naive")
    return dt


@dataclass(frozen=True)
class Forecast:
    forecast_id: str
    model_version: str
    prompt_version: str
    as_of: str                       # ISO tz-aware — the claim's timestamp
    symbol: str
    horizon_min: int
    scenario_probs: dict             # scenario name -> prob, sums to 1
    direction: str                   # up | down | flat
    recommendation: Optional[str]    # policy name, or None
    interrupt: Optional[str]         # None | TIGHTEN | REDUCE_RISK | INVALIDATE | EMERGENCY
    confidence: float
    evidence_ids: tuple = ()
    # Set only when the emission-time RTH gate shrank the claimed horizon to
    # fit inside the tradable session (spec 024 review, 2026-08-2x): the
    # grader must be able to see this was not a free `horizon_clamped_from`
    # minute claim. None on every forecast the gate left untouched.
    horizon_clamped_from: Optional[int] = None

    def __post_init__(self):
        if not self.forecast_id or not self.model_version:
            raise ForecastError("forecast_id and model_version are required")
        _aware(self.as_of, "as_of")
        if self.horizon_min <= 0:
            raise ForecastError(f"horizon_min {self.horizon_min} must be positive")
        if (self.horizon_clamped_from is not None
                and self.horizon_clamped_from <= self.horizon_min):
            raise ForecastError(
                f"horizon_clamped_from {self.horizon_clamped_from} must exceed "
                f"the clamped horizon_min {self.horizon_min} — a clamp that "
                "didn't shrink anything is not a clamp")
        if self.direction not in DIRECTIONS:
            raise ForecastError(f"unknown direction {self.direction!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ForecastError(f"confidence {self.confidence} outside [0, 1]")
        unknown = set(self.scenario_probs) - set(ALL_SCENARIOS)
        if unknown:
            raise ForecastError(f"unknown scenario(s) {sorted(unknown)}")
        total = sum(self.scenario_probs.values())
        if abs(total - 1.0) > PROB_TOL:
            raise ForecastError(f"scenario probs sum to {total:.6f}, not 1.0 — "
                                "not normalising for you")

    @property
    def top_prob(self) -> float:
        return max(self.scenario_probs.values())

    @property
    def is_urgent(self) -> bool:
        return self.interrupt in URGENT_INTERRUPTS

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Resolution:
    forecast_id: str
    resolved_at: str                 # ISO tz-aware
    outcome_scenario: str
    outcome_direction: str
    shock_occurred: bool
    was_stale: bool
    policy_regret_r: Optional[float] = None    # from 014's regret deltas

    def __post_init__(self):
        _aware(self.resolved_at, "resolved_at")
        if self.outcome_scenario not in ALL_SCENARIOS:
            raise ForecastError(f"unknown outcome scenario {self.outcome_scenario!r}")
        if self.outcome_direction not in DIRECTIONS:
            raise ForecastError(f"unknown outcome direction {self.outcome_direction!r}")


def brier(f: Forecast, r: Resolution) -> float:
    """Multi-class Brier over the forecast's OWN scenario vocabulary."""
    names = set(f.scenario_probs) | {r.outcome_scenario}
    return sum((f.scenario_probs.get(n, 0.0) - (1.0 if n == r.outcome_scenario
                                                else 0.0)) ** 2
               for n in names)


def baseline_brier(f: Forecast, r: Resolution) -> float:
    """The dumb baseline on the IDENTICAL case: uniform over the same names."""
    names = set(f.scenario_probs) | {r.outcome_scenario}
    p = 1.0 / len(names)
    return sum((p - (1.0 if n == r.outcome_scenario else 0.0)) ** 2
               for n in names)


def climatology_brier(f: Forecast, r: Resolution, base_rates: dict) -> float:
    """The baseline that actually costs something to beat.

    THE HOLE THIS CLOSES
        `baseline_brier` is UNIFORM. A forecaster that emits the unconditional
        empirical frequency of each scenario — using zero information about the
        day, the tape, or the news — beats uniform essentially forever, is
        perfectly calibrated BY CONSTRUCTION, has a constant per-regime score,
        and never says EXIT. So it passes the Brier gate, the calibration gate,
        the regime-stability gate and the tail-regret gate simultaneously.

        Beating uniform is a test of arithmetic. Beating climatology is a test
        of information, and that is the thing the gate is supposed to be for.
    """
    names = set(f.scenario_probs) | {r.outcome_scenario} | set(base_rates)
    total = sum(base_rates.get(n, 0.0) for n in names)
    if total <= 0:
        return baseline_brier(f, r)          # no history yet: fall back, honestly
    return sum((base_rates.get(n, 0.0) / total
                - (1.0 if n == r.outcome_scenario else 0.0)) ** 2
               for n in names)


def base_rates_from(pairs) -> dict:
    """Unconditional outcome frequencies over the resolved set — the
    climatology a zero-information forecaster would emit."""
    counts: dict = {}
    for _, r in pairs:
        counts[r.outcome_scenario] = counts.get(r.outcome_scenario, 0) + 1
    n = sum(counts.values())
    return {k: v / n for k, v in counts.items()} if n else {}


def skill_vs(model: list, reference: list) -> float:
    """Brier skill score: 1 - mean(model)/mean(reference). >0 means the model
    carries information the reference does not. 0 means it does not."""
    ref = sum(reference) / len(reference) if reference else 0.0
    if ref <= 0:
        return 0.0
    return 1.0 - (sum(model) / len(model)) / ref


def skill_ci_low(model: list, reference: list, *, n_boot: int = 2000,
                 alpha: float = 0.05, seed: int = 20260901) -> float:
    """Lower bound of a percentile bootstrap CI on the skill score.

    Deterministic seed: a gate whose verdict moves between runs is not a gate.
    Paired resampling — model and reference are scored on the SAME cases, so
    they must be resampled together or the CI is wrong."""
    import random
    if not model or len(model) != len(reference):
        return float("-inf")
    rng = random.Random(seed)
    n = len(model)
    idx = range(n)
    scores = []
    for _ in range(n_boot):
        pick = [rng.choice(idx) for _ in idx]
        scores.append(skill_vs([model[i] for i in pick],
                               [reference[i] for i in pick]))
    scores.sort()
    return scores[int(alpha * n_boot)]


@dataclass(frozen=True)
class ScoreReport:
    model_version: str
    oos: bool                        # label supplied by the grader, required by the gate
    n_resolved: int
    n_open: int
    n_unresolvable: int               # sealed by mark_unresolvable(); never
                                       # graded, never counted as still-open —
                                       # a forecast whose window can never
                                       # produce an outcome (spec 024 review)
    brier: float
    baseline_brier: float                 # uniform — a test of arithmetic
    climatology_brier: float              # base rates — a test of information
    skill_vs_climatology: float           # 1 - brier/climatology; >0 = informative
    skill_ci_low: float                   # bootstrap lower bound; gate needs >0
    prompt_version: str                   # part of the artefact's identity
    directional_accuracy: float
    calibration_error: float
    false_urgent_rate: float
    missed_shock_rate: float
    stale_rate: float
    policy_regret_mean: Optional[float]
    worst_policy_regret: Optional[float]
    explainability: float            # fraction of forecasts carrying evidence ids

    def to_dict(self) -> dict:
        return asdict(self)


class ForecastLedger:
    """Append-only. Claims frozen at record time; resolutions separate."""

    def __init__(self):
        self._forecasts: dict[str, Forecast] = {}
        self._resolutions: dict[str, Resolution] = {}
        self._unresolvable: dict[str, dict] = {}

    def record(self, f: Forecast) -> None:
        if f.forecast_id in self._forecasts:
            raise ForecastError(f"forecast {f.forecast_id!r} already recorded — "
                                "a claim is made once")
        self._forecasts[f.forecast_id] = f

    def resolve(self, r: Resolution) -> None:
        f = self._forecasts.get(r.forecast_id)
        if f is None:
            raise ForecastError(f"resolution for unknown forecast {r.forecast_id!r}")
        if r.forecast_id in self._resolutions:
            raise ForecastError(f"{r.forecast_id!r} already resolved — an outcome "
                                "is observed once, not re-litigated")
        horizon_end = _aware(f.as_of, "as_of") + timedelta(minutes=f.horizon_min)
        if _aware(r.resolved_at, "resolved_at") < horizon_end:
            raise ForecastError(
                f"{r.forecast_id!r}: resolution at {r.resolved_at} is before the "
                f"horizon ends ({horizon_end.isoformat()}) — an outcome cannot "
                "enter a forecast before its resolution horizon")
        self._resolutions[r.forecast_id] = r

    def forecast(self, forecast_id: str) -> Forecast:
        return self._forecasts[forecast_id]

    def mark_unresolvable(self, forecast_id: str, reason: str, *, at: str) -> None:
        """Seal a forecast as permanently unable to produce an outcome — e.g.
        its [as_of, as_of+horizon] window never touches a tradable RTH
        session, so the bars it would be scored against can never exist.

        Deliberately NEVER routes through resolve(): an unresolvable claim
        gets no outcome_scenario, inferred or otherwise. Injecting a fabricated
        outcome here would corrupt the Brier sum resolve() exists to protect
        (spec 024 review, 2026-08-2x)."""
        f = self._forecasts.get(forecast_id)
        if f is None:
            raise ForecastError(
                f"cannot mark unknown forecast {forecast_id!r} unresolvable")
        if forecast_id in self._resolutions:
            raise ForecastError(
                f"{forecast_id!r} already resolved — an outcome exists, it "
                "cannot retroactively become unresolvable")
        if forecast_id in self._unresolvable:
            raise ForecastError(
                f"{forecast_id!r} already marked unresolvable — sealed once")
        if not reason:
            raise ForecastError("mark_unresolvable requires a non-empty reason")
        _aware(at, "at")
        self._unresolvable[forecast_id] = {"reason": reason, "at": at}

    def is_unresolvable(self, forecast_id: str) -> bool:
        return forecast_id in self._unresolvable

    def grade(self, model_version: str, *, oos: bool,
              prompt_version: Optional[str] = None) -> ScoreReport:
        """Score every RESOLVED forecast of one model. Pure over the ledger.

        `prompt_version` joins the partition when supplied. Model identity alone
        was not enough: the prompt IS part of the artefact under test, so a
        rewritten prompt inheriting the old track record grades one thing on
        another thing's evidence. Left optional so existing callers keep working
        and get the old, wider partition explicitly rather than by accident."""
        fs_all = [f for f in self._forecasts.values()
                  if f.model_version == model_version
                  and (prompt_version is None
                       or f.prompt_version == prompt_version)]
        # Unresolvable forecasts are excluded from BOTH the resolved pairs
        # (already true — they were never in self._resolutions) AND n_open —
        # a forecast that can never produce an outcome is not "still open",
        # it is a separate terminal state (spec 024 review).
        fs = [f for f in fs_all if f.forecast_id not in self._unresolvable]
        pairs = [(f, self._resolutions[f.forecast_id]) for f in fs
                 if f.forecast_id in self._resolutions]
        n_open = len(fs) - len(pairs)
        n_unresolvable = len(fs_all) - len(fs)
        if not pairs:
            raise ForecastError(f"{model_version!r}: nothing resolved to grade")

        briers = [brier(f, r) for f, r in pairs]
        base = [baseline_brier(f, r) for f, r in pairs]
        rates = base_rates_from(pairs)
        clim = [climatology_brier(f, r, rates) for f, r in pairs]
        dir_hits = [1.0 if f.direction == r.outcome_direction else 0.0
                    for f, r in pairs]

        # calibration on the top-scenario probability, 0.1-wide bins
        bins: dict[int, list] = {}
        for f, r in pairs:
            hit = 1.0 if max(f.scenario_probs, key=f.scenario_probs.get) \
                == r.outcome_scenario else 0.0
            bins.setdefault(min(9, int(f.top_prob * 10)), []).append(
                (f.top_prob, hit))
        cal = sum(len(v) * abs(sum(p for p, _ in v) / len(v)
                               - sum(h for _, h in v) / len(v))
                  for v in bins.values()) / len(pairs)

        urgent = [(f, r) for f, r in pairs if f.is_urgent]
        calm = [(f, r) for f, r in pairs if not f.is_urgent]
        false_urgent = (sum(1 for _, r in urgent if not r.shock_occurred)
                        / len(urgent)) if urgent else 0.0
        missed = (sum(1 for _, r in calm if r.shock_occurred)
                  / len(calm)) if calm else 0.0
        regrets = [r.policy_regret_r for _, r in pairs
                   if r.policy_regret_r is not None]

        return ScoreReport(
            model_version=model_version, oos=oos,
            n_resolved=len(pairs), n_open=n_open, n_unresolvable=n_unresolvable,
            brier=sum(briers) / len(briers),
            baseline_brier=sum(base) / len(base),
            climatology_brier=sum(clim) / len(clim),
            skill_vs_climatology=skill_vs(briers, clim),
            skill_ci_low=skill_ci_low(briers, clim),
            prompt_version=(prompt_version or ""),
            directional_accuracy=sum(dir_hits) / len(dir_hits),
            calibration_error=cal,
            false_urgent_rate=false_urgent, missed_shock_rate=missed,
            stale_rate=sum(1 for _, r in pairs if r.was_stale) / len(pairs),
            policy_regret_mean=(sum(regrets) / len(regrets)) if regrets else None,
            worst_policy_regret=min(regrets) if regrets else None,
            explainability=sum(1 for f, _ in pairs if f.evidence_ids) / len(pairs))


# ---------------------------------------------------------------- promotion

@dataclass(frozen=True)
class PromotionThresholds:
    min_decisions: int = 50
    skill_ci_low_min: float = 0.0    # bootstrap CI on skill must EXCLUDE zero
    calibration_max: float = 0.15
    regime_stability_tolerance: float = 0.05
    worst_regret_floor_r: float = -2.0      # tail ceiling on policy regret
    stale_rate_max: float = 0.10
    false_urgent_max: float = 0.10
    missed_shock_max: float = 0.25
    explainability_min: float = 0.95


@dataclass(frozen=True)
class PromotionDecision:
    promoted: bool
    failed_gates: tuple              # COMPLETE list, never first-only
    promotion_ref: Optional[str]     # feeds AuthorityRegistry.grant
    why: str


def promotion_decision(incumbent: ScoreReport, challenger: ScoreReport,
                       thresholds: PromotionThresholds, *,
                       per_regime_incumbent: dict, per_regime_challenger: dict,
                       ref: str) -> PromotionDecision:
    """Every gate independent; ALL must pass. Aggregate return is not a
    parameter of this function — that is the design, not an omission."""
    failed = []
    if not (incumbent.oos and challenger.oos):
        failed.append("OOS_REQUIRED")
    if challenger.n_resolved < thresholds.min_decisions:
        failed.append("MIN_DECISIONS")
    if not (challenger.brier < incumbent.brier):        # strictly better
        failed.append("BRIER_NOT_STRICTLY_BETTER")
    # DISCRIMINATION. Beating uniform is arithmetic; beating climatology is
    # information. Without this a zero-information base-rate forecaster passes
    # every other gate on this list simultaneously — perfectly calibrated by
    # construction, constant across regimes, and never urgent.
    if challenger.skill_vs_climatology <= 0.0:
        failed.append("NO_SKILL_VS_CLIMATOLOGY")
    elif challenger.skill_ci_low <= thresholds.skill_ci_low_min:
        failed.append("SKILL_CI_INCLUDES_ZERO")
    if challenger.calibration_error > thresholds.calibration_max:
        failed.append("CALIBRATION")
    # A gate with no data is a FAILED gate, not a passed one — otherwise the
    # regime and tail gates are bypassable by simply not supplying the inputs
    # that arm them (adversarial review finding 6).
    if not per_regime_incumbent or not per_regime_challenger:
        failed.append("REGIME_DATA_MISSING")
    for regime in per_regime_incumbent:
        c = per_regime_challenger.get(regime)
        if c is None:
            failed.append(f"REGIME_MISSING:{regime}")
        elif c > per_regime_incumbent[regime] + thresholds.regime_stability_tolerance:
            failed.append(f"REGIME_UNSTABLE:{regime}")
    if challenger.worst_policy_regret is None:
        failed.append("REGRET_DATA_MISSING")
    # THE TAUTOLOGY TRAP, guarded before it is walked into.
    #
    # When 49 of 50 decisions are in and this gate still reads
    # REGRET_DATA_MISSING, the pressure to define policy_regret_r = 0.0 for
    # capped/suppressed directives will be enormous and technically defensible:
    # a directive that was never emitted cannot have caused regret. But a
    # blanket zero converts a TAIL RISK gate into a gate that can never fail,
    # and a gate that cannot fail is not a gate.
    #
    # An all-zero regret distribution is the signature of that imputation. It is
    # also the honest description of a model that has never influenced a trade —
    # and in that case it has no tail evidence either, so refusing is correct on
    # both readings.
    elif (challenger.policy_regret_mean == 0.0
          and challenger.worst_policy_regret == 0.0):
        failed.append("REGRET_ALL_ZERO_SUSPECT_IMPUTATION")
    elif challenger.worst_policy_regret < thresholds.worst_regret_floor_r:
        failed.append("TAIL_REGRET")
    if challenger.stale_rate > thresholds.stale_rate_max:
        failed.append("STALE_RATE")
    if challenger.false_urgent_rate > thresholds.false_urgent_max:
        failed.append("FALSE_URGENT")
    if challenger.missed_shock_rate > thresholds.missed_shock_max:
        failed.append("MISSED_SHOCK")
    if challenger.explainability < thresholds.explainability_min:
        failed.append("EXPLAINABILITY")

    if failed:
        return PromotionDecision(False, tuple(failed), None,
                                 f"rejected: {', '.join(failed)} — one failed gate "
                                 "rejects, whatever the aggregate return was")
    return PromotionDecision(True, (), ref,
                             f"promoted: all gates passed over "
                             f"{challenger.n_resolved} OOS decisions")


if __name__ == "__main__":
    print(__doc__)
    print("run the suite: pytest daytrade/test_forecast.py -v")
