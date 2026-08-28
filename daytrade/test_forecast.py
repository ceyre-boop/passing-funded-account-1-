"""Card 017 — forecast ledger, grading, and the promotion gate as code.

The sealed-fixture DoD: an outcome cannot enter a forecast before its horizon;
the baseline is scored on identical cases; a challenger failing ONE gate is
rejected even if everything else looks glorious; promotion mutates no history.
Fault rows in specs/017_MUTATION_LOG.md (driver: mutation_check_017.py).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from forecast import (Forecast, ForecastError, ForecastLedger,
                      PromotionDecision, PromotionThresholds, Resolution,
                      baseline_brier, brier, promotion_decision)

T0 = datetime(2026, 8, 7, 14, 30, tzinfo=timezone.utc)


def forecast(fid: str = "f1", *, model: str = "az-1", probs: dict | None = None,
             direction: str = "up", interrupt: str | None = None,
             confidence: float = 0.7, horizon: int = 60,
             evidence: tuple = ("ev1",)) -> Forecast:
    return Forecast(forecast_id=fid, model_version=model, prompt_version="p1",
                    as_of=T0.isoformat(), symbol="NVDA", horizon_min=horizon,
                    scenario_probs=probs or {"bull_continuation": 0.7,
                                             "range_consolidation": 0.3},
                    direction=direction, recommendation=None,
                    interrupt=interrupt, confidence=confidence,
                    evidence_ids=evidence)


def resolution(fid: str = "f1", *, outcome: str = "bull_continuation",
               direction: str = "up", shock: bool = False, stale: bool = False,
               regret: float | None = -0.1, minutes_after: int = 60) -> Resolution:
    return Resolution(forecast_id=fid,
                      resolved_at=(T0 + timedelta(minutes=minutes_after)).isoformat(),
                      outcome_scenario=outcome, outcome_direction=direction,
                      shock_occurred=shock, was_stale=stale,
                      policy_regret_r=regret)


def graded_ledger(model: str, n: int, *, hit_rate: float,
                  stale_every: int = 0) -> ForecastLedger:
    led = ForecastLedger()
    hits = round(n * hit_rate)
    for i in range(n):
        led.record(forecast(f"{model}-{i}", model=model))
        led.resolve(resolution(
            f"{model}-{i}",
            outcome="bull_continuation" if i < hits else "range_consolidation",
            direction="up" if i < hits else "flat",
            stale=bool(stale_every and i % stale_every == 0)))
    return led


# ------------------------------------------------------------------- ledger

def test_forecast_validates():
    with pytest.raises(ForecastError):
        forecast(probs={"bull_continuation": 0.7})               # sums to 0.7
    with pytest.raises(ForecastError):
        forecast(probs={"melt_up": 1.0})                         # unknown scenario
    with pytest.raises(ForecastError):
        forecast(direction="sideways")
    with pytest.raises(ForecastError):
        forecast(horizon=0)
    with pytest.raises(ForecastError):
        Forecast(forecast_id="f", model_version="m", prompt_version="p",
                 as_of="2026-08-07T14:30:00", symbol="NVDA", horizon_min=60,
                 scenario_probs={"bull_continuation": 1.0}, direction="up",
                 recommendation=None, interrupt=None, confidence=0.5)  # naive ts


def test_outcome_cannot_enter_before_horizon():
    led = ForecastLedger()
    led.record(forecast(horizon=60))
    with pytest.raises(ForecastError, match="before its resolution horizon"):
        led.resolve(resolution(minutes_after=59))
    led.resolve(resolution(minutes_after=60))                    # at horizon: fine


def test_claims_are_made_once_and_resolved_once():
    led = ForecastLedger()
    led.record(forecast())
    with pytest.raises(ForecastError):
        led.record(forecast())                                   # same id again
    led.resolve(resolution())
    with pytest.raises(ForecastError):
        led.resolve(resolution(direction="down"))                # re-litigation
    with pytest.raises(ForecastError):
        led.resolve(resolution("ghost"))                         # unknown claim


def test_resolution_never_edits_the_original_claim():
    led = ForecastLedger()
    f = forecast()
    led.record(f)
    before = led.forecast("f1").to_dict()
    led.resolve(resolution(outcome="range_consolidation", direction="flat"))
    assert led.forecast("f1").to_dict() == before
    assert led.forecast("f1") == f


# ------------------------------------------------------------------ grading

def test_brier_and_baseline_on_identical_case():
    f = forecast(probs={"bull_continuation": 0.7, "range_consolidation": 0.3})
    r = resolution()
    # (0.7-1)^2 + (0.3-0)^2 = 0.09 + 0.09 = 0.18 ; uniform: (0.5-1)^2+(0.5)^2 = 0.5
    assert brier(f, r) == pytest.approx(0.18)
    assert baseline_brier(f, r) == pytest.approx(0.5)
    # a surprise outcome outside the forecast's vocabulary costs full marks
    r2 = resolution(outcome="risk_event", direction="down")
    assert brier(f, r2) == pytest.approx(0.7 ** 2 + 0.3 ** 2 + 1.0)
    # the baseline on the 3-name surprise case is uniform over THREE names —
    # (1/3)^2 * 2 + (2/3)^2. Pinned numerically because a constant-0.5 baseline
    # is indistinguishable from uniform on 2-name cases (review finding 7).
    assert baseline_brier(f, r2) == pytest.approx(2 / 9 + 4 / 9)


def test_grade_scores_baseline_on_identical_cases_and_counts_open():
    led = graded_ledger("az-1", 10, hit_rate=0.8)
    led.record(forecast("open-1", model="az-1"))                 # never resolved
    rep = led.grade("az-1", oos=True)
    assert rep.n_resolved == 10 and rep.n_open == 1
    assert rep.brier < rep.baseline_brier                        # 80% hits beat uniform
    assert rep.directional_accuracy == pytest.approx(0.8)
    assert rep.explainability == 1.0
    with pytest.raises(ForecastError):
        led.grade("nobody", oos=True)


# ------------------------------------------------------- unresolvable state

def test_mark_unresolvable_contract():
    """spec 024 review: a forecast whose window can never produce an outcome
    gets a terminal state, never a guessed resolution."""
    led = ForecastLedger()
    with pytest.raises(ForecastError, match="unknown forecast"):
        led.mark_unresolvable("ghost", "window has no tradable session",
                              at=T0.isoformat())

    led.record(forecast("f1"))
    with pytest.raises(ForecastError, match="non-empty reason"):
        led.mark_unresolvable("f1", "", at=T0.isoformat())
    with pytest.raises(ForecastError):                            # naive ts
        led.mark_unresolvable("f1", "window has no tradable session", at="2026-08-07")

    led.mark_unresolvable("f1", "window has no tradable session", at=T0.isoformat())
    assert led.is_unresolvable("f1")
    with pytest.raises(ForecastError, match="already marked unresolvable"):
        led.mark_unresolvable("f1", "window has no tradable session", at=T0.isoformat())

    # already-resolved forecasts cannot retroactively become unresolvable
    led.record(forecast("f2"))
    led.resolve(resolution("f2"))
    with pytest.raises(ForecastError, match="already resolved"):
        led.mark_unresolvable("f2", "window has no tradable session", at=T0.isoformat())

    # and the reverse: an unresolvable forecast can never be resolve()'d with
    # a fabricated outcome — resolve() itself refuses it as an unknown pair
    # only in the sense that mark_unresolvable never touches _resolutions,
    # so a later resolve() call is a plain, valid path if evidence ever
    # showed up — the seal is on grade()'s accounting, not on resolve()'s
    # door. What must never happen is the reverse order (asserted above).


def test_grade_excludes_unresolvable_from_pairs_and_open():
    """n_unresolvable must be excluded from BOTH `pairs` and `n_open`
    (spec 024 review) — a forecast that can never be scored is not "still
    open", and it must never contaminate the resolved-pairs population."""
    led = graded_ledger("az-1", 10, hit_rate=0.8)
    led.record(forecast("stuck-1", model="az-1"))
    led.record(forecast("stuck-2", model="az-1"))
    led.record(forecast("open-1", model="az-1"))          # genuinely still open

    before = led.grade("az-1", oos=True)
    assert before.n_resolved == 10 and before.n_open == 3 and before.n_unresolvable == 0

    led.mark_unresolvable("stuck-1", "window has no tradable session", at=T0.isoformat())
    led.mark_unresolvable("stuck-2", "window has no tradable session", at=T0.isoformat())
    after = led.grade("az-1", oos=True)

    assert after.n_resolved == 10                          # pairs untouched
    assert after.n_open == 1                                # only the genuine one left
    assert after.n_unresolvable == 2
    # the two reclassified rows moved OUT of n_open, not double-counted
    assert after.n_open + after.n_unresolvable + after.n_resolved == \
        before.n_open + before.n_unresolvable + before.n_resolved


def test_unresolvable_forecasts_leave_brier_and_calibration_bit_identical():
    """THE regression: grade()'s brier and calibration_error must be
    bit-identical whether or not any forecast has ever been marked
    unresolvable. forecast.py's grade() builds every one of briers/base/
    dir_hits/cal from `pairs`, which is built ONLY from self._resolutions —
    stuck-OPEN rows never touched that math before this change, and marking
    them unresolvable must not touch it either. This is pinned numerically,
    not just compared before/after in the same process, so a future edit
    that quietly perturbs the math cannot pass by accident."""
    led = graded_ledger("az-1", 10, hit_rate=0.8)
    led.record(forecast("stuck-1", model="az-1"))
    led.record(forecast("stuck-2", model="az-1"))
    led.record(forecast("stuck-3", model="az-1"))
    led.record(forecast("stuck-4", model="az-1"))

    before = led.grade("az-1", oos=True)
    # Pinned to forecast.py's actual current output for graded_ledger("az-1",
    # 10, hit_rate=0.8): 8 hits scoring brier 0.18 each, 2 misses (forecast
    # "bull_continuation" 0.7 vs outcome "range_consolidation") scoring
    # 0.7**2 + (1-0.3)**2 = 0.49 + 0.49 = 0.98 each -> mean 0.34.
    assert before.brier == pytest.approx((8 * 0.18 + 2 * 0.98) / 10)
    assert before.n_open == 4 and before.n_unresolvable == 0

    for fid in ("stuck-1", "stuck-2", "stuck-3", "stuck-4"):
        led.mark_unresolvable(fid, "window has no tradable session", at=T0.isoformat())
    after = led.grade("az-1", oos=True)

    assert after.brier == before.brier                     # bit-identical
    assert after.calibration_error == before.calibration_error
    assert after.baseline_brier == before.baseline_brier
    assert after.directional_accuracy == before.directional_accuracy
    assert after.n_open == 0 and after.n_unresolvable == 4  # only these moved


def test_false_urgent_and_missed_shock_rates():
    led = ForecastLedger()
    # deliberately ASYMMETRIC counts (2/3 vs 1/3): a symmetric fixture lets an
    # inverted definition land on the same number — caught by the fault loop
    led.record(forecast("u1", interrupt="EMERGENCY"))
    led.resolve(resolution("u1", shock=False))                   # cried wolf
    led.record(forecast("u2", interrupt="EMERGENCY"))
    led.resolve(resolution("u2", shock=False))                   # cried wolf again
    led.record(forecast("u3", interrupt="EMERGENCY"))
    led.resolve(resolution("u3", shock=True))                    # right to scream
    led.record(forecast("c1"))
    led.resolve(resolution("c1", shock=True))                    # slept through it
    led.record(forecast("c2"))
    led.resolve(resolution("c2", shock=False))
    rep = led.grade("az-1", oos=True)
    assert rep.false_urgent_rate == pytest.approx(2 / 3)
    assert rep.missed_shock_rate == pytest.approx(0.5)


# ------------------------------------------------------------- the gate

def reports(n_challenger: int = 60):
    inc = graded_ledger("inc", 60, hit_rate=0.6).grade("inc", oos=True)
    ch = graded_ledger("ch", n_challenger, hit_rate=0.8).grade("ch", oos=True)
    return inc, ch


def test_promotion_all_gates_pass():
    inc, ch = reports()
    d = promotion_decision(inc, ch, PromotionThresholds(),
                           per_regime_incumbent={"trend": 0.5, "chop": 0.6},
                           per_regime_challenger={"trend": 0.4, "chop": 0.55},
                           ref="promo-001")
    assert d.promoted and d.failed_gates == () and d.promotion_ref == "promo-001"


def test_one_failed_gate_rejects_even_a_brilliant_challenger():
    """The card's sealed scenario: better Brier, better everything — except one
    gate. Rejected, with the gate NAMED. Aggregate return is not even a
    parameter of promotion_decision, so it cannot argue back."""
    inc, ch = reports()
    assert ch.brier < inc.brier                                  # genuinely better
    bad_cal = replace(ch, calibration_error=0.5)
    d = promotion_decision(inc, bad_cal, PromotionThresholds(),
                           per_regime_incumbent={"trend": 0.5},
                           per_regime_challenger={"trend": 0.4},
                           ref="promo-002")
    assert not d.promoted and d.promotion_ref is None
    assert "CALIBRATION" in d.failed_gates


def test_brier_must_be_strictly_better():
    """Equal is not better. A challenger that merely ties the incumbent stays
    a challenger — churn without improvement is pure risk."""
    inc, ch = reports()
    tied = replace(ch, brier=inc.brier)
    d = promotion_decision(inc, tied, PromotionThresholds(),
                           per_regime_incumbent={}, per_regime_challenger={},
                           ref="p")
    assert not d.promoted and "BRIER_NOT_STRICTLY_BETTER" in d.failed_gates


def test_failed_gate_list_is_complete_not_first_only():
    inc, ch = reports(n_challenger=10)                           # under min_decisions
    bad = replace(ch, calibration_error=0.5, stale_rate=0.9, oos=False)
    d = promotion_decision(inc, bad, PromotionThresholds(),
                           per_regime_incumbent={}, per_regime_challenger={},
                           ref="promo-003")
    for g in ("OOS_REQUIRED", "MIN_DECISIONS", "CALIBRATION", "STALE_RATE"):
        assert g in d.failed_gates, d.failed_gates


def test_regime_instability_and_missing_bucket_reject():
    inc, ch = reports()
    d = promotion_decision(inc, ch, PromotionThresholds(),
                           per_regime_incumbent={"trend": 0.5, "chop": 0.4},
                           per_regime_challenger={"trend": 0.4, "chop": 0.7},
                           ref="p")
    assert "REGIME_UNSTABLE:chop" in d.failed_gates
    d = promotion_decision(inc, ch, PromotionThresholds(),
                           per_regime_incumbent={"trend": 0.5, "chop": 0.4},
                           per_regime_challenger={"trend": 0.4},
                           ref="p")
    assert "REGIME_MISSING:chop" in d.failed_gates


def test_gates_with_no_data_fail_not_pass():
    """Adversarial-review finding 6: a gate whose arming data is absent must
    FAIL by name — the regime and tail gates cannot be bypassed by omission."""
    inc, ch = reports()
    no_regret = replace(ch, worst_policy_regret=None)
    d = promotion_decision(inc, no_regret, PromotionThresholds(),
                           per_regime_incumbent={}, per_regime_challenger={},
                           ref="p")
    assert not d.promoted
    assert "REGIME_DATA_MISSING" in d.failed_gates
    assert "REGRET_DATA_MISSING" in d.failed_gates


def test_promotion_mutates_no_history():
    led = graded_ledger("ch", 60, hit_rate=0.8)
    before = led.grade("ch", oos=True)
    inc = graded_ledger("inc", 60, hit_rate=0.6).grade("inc", oos=True)
    promotion_decision(inc, before, PromotionThresholds(),
                       per_regime_incumbent={}, per_regime_challenger={},
                       ref="promo-004")
    assert led.grade("ch", oos=True) == before                   # untouched


def test_promotion_ref_feeds_the_authority_registry():
    """The 016/017 interlock: the ref a promotion emits is exactly what the
    registry demands for a grant above the unpromoted cap."""
    from context_directive import AuthorityError, AuthorityRegistry
    inc, ch = reports()
    d = promotion_decision(inc, ch, PromotionThresholds(),
                           per_regime_incumbent={"trend": 0.5},
                           per_regime_challenger={"trend": 0.4},
                           ref="promo-005")
    assert d.promoted
    reg = AuthorityRegistry()
    with pytest.raises(AuthorityError):
        reg.grant("ch", 3, by="colin", reason="no promotion", ts=T0.isoformat())
    reg.grant("ch", 3, by="colin", reason="cleared 017", ts=T0.isoformat(),
              promotion_ref=d.promotion_ref)
    assert reg.granted_level("ch") == 3


# ------------------------------------------------------------ anchor_event

def test_anchor_event_defaults_none_and_round_trips():
    """Ordinary (unanchored) forecasts are unaffected; an anchored one
    carries its tag through to_dict() and back through the constructor —
    same optional-field discipline as horizon_clamped_from/recommendation_
    reason, so historical rows with no anchor_event still load."""
    plain = forecast("f-plain")
    assert plain.anchor_event is None
    anchored = Forecast(forecast_id="f-anchor", model_version="az-1",
                        prompt_version="p1", as_of=T0.isoformat(), symbol="NVDA",
                        horizon_min=60,
                        scenario_probs={"bull_continuation": 0.7,
                                       "range_consolidation": 0.3},
                        direction="up", recommendation=None, interrupt=None,
                        confidence=0.7, evidence_ids=("ev1",),
                        anchor_event="Consumer Price Index@2026-09-10")
    assert anchored.anchor_event == "Consumer Price Index@2026-09-10"
    body = anchored.to_dict()
    assert Forecast(**body).anchor_event == "Consumer Price Index@2026-09-10"
