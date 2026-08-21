"""Spec 035 — a verdict must survive being asked of half the record.

The audit guarded the within-run coin flip (Monte Carlo error) and printed
confident verdicts across an ACROSS-sample one: when the ledger grew from 438
to 674 labeled sessions, TREND_UP and TREND_DOWN swapped verdicts outright.
These tests hold the guard that catches that.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ontology_audit as oa

DAYS = [f"2026-01-{d:02d}" for d in range(1, 41)]
SYMS = ["AAA", "BBB", "CCC", "DDD"]
FAST = dict(k=8, n_perm=400)


def _world(effect_days, effect_size, base_noise=0.05, label="X"):
    """Half the (symbol, day) keys carry the label. The label's effect lives
    only on `effect_days` — everywhere else the two groups are identical."""
    rng = random.Random(7)
    outcomes, labels = {}, {}
    for day in DAYS:
        for i, sym in enumerate(SYMS):
            key = (sym, day)
            marked = i % 2 == 0
            labels[key] = {label} if marked else set()
            r = rng.gauss(0, base_noise)
            if marked and day in effect_days:
                r += effect_size
            outcomes[key] = r
    return outcomes, labels


def _pooled_p(outcomes, labels, label="X", n_perm=2000):
    keys = list(outcomes)
    a = [outcomes[k] for k in keys if label in labels[k]]
    b = [outcomes[k] for k in keys if label not in labels[k]]
    return oa.perm_p(a, b, random.Random(11), n_perm)[0]


def test_unstable_label_is_not_reported_as_carves():
    """M40. An effect carried by three days out of forty clears p<0.05 pooled
    (p=0.013) and must NOT be called CARVES — a random half of the record
    reproduces it in 1 of 8 draws, which is what noise produces."""
    outcomes, labels = _world(set(DAYS[:3]), effect_size=40.0)
    p = _pooled_p(outcomes, labels)
    assert p < 0.05, f"setup broken: pooled p={p} is not significant"
    stab = oa.subsample_stability(
        outcomes, labels, "X", random.Random(oa.SEED), **FAST)
    assert stab["n_eval"] == 8, stab
    assert stab["stability_p"] >= oa.STABILITY_ALPHA, stab
    assert oa.classify(p, False, stab) == "UNSTABLE"


def test_stable_label_survives_subsampling():
    """The mirror image: an effect present on EVERY day reproduces in every
    half-sample and must still be reported CARVES."""
    outcomes, labels = _world(set(DAYS), effect_size=1.0)
    p = _pooled_p(outcomes, labels)
    assert p < 0.05
    stab = oa.subsample_stability(
        outcomes, labels, "X", random.Random(oa.SEED), **FAST)
    assert stab["rate"] == 1.0, stab
    assert oa.classify(p, False, stab) == "CARVES"


def test_rare_label_reports_untested_not_stable():
    """M41/I-035-2. Too rare to split: the rate is null and the verdict says
    UNTESTED. It is never coerced to a number and never treated as stable."""
    outcomes, labels = _world(set(), effect_size=0.0)
    for i, key in enumerate(sorted(outcomes)):
        labels[key] = {"X"} if i < 5 else set()      # 5 marked of 160
    stab = oa.subsample_stability(
        outcomes, labels, "X", random.Random(oa.SEED), **FAST)
    assert stab["rate"] is None and stab["stability_p"] is None, stab
    assert stab["n_eval"] == 0, stab
    assert oa.classify(0.001, False, stab) == "UNSTABLE_UNTESTED"


def test_bonferroni_set_excludes_unstable():
    """M42. A tiny p does not buy a seat in the surviving set."""
    tested = [{"label": "U", "p_value": 0.0001, "verdict": "UNSTABLE"},
              {"label": "T", "p_value": 0.0001, "verdict": "UNSTABLE_UNTESTED"},
              {"label": "C", "p_value": 0.0001, "verdict": "CARVES"},
              {"label": "D", "p_value": 0.40, "verdict": "DECORATION"}]
    assert [r["label"] for r in oa.surviving(tested)] == ["C"]


def test_stability_is_grouped_by_day_not_by_row():
    """Day-grouping doctrine: a half-sample is half the DAYS. If it sampled
    rows instead, both groups would appear in every day and the outlier-day
    pathology above would be invisible."""
    outcomes, labels = _world(set(DAYS[:3]), effect_size=40.0)
    seen = set()
    orig = random.Random.sample

    def spy(self, population, k):
        seen.add(tuple(sorted(population))[:1])
        return orig(self, population, k)

    random.Random.sample = spy
    try:
        oa.subsample_stability(outcomes, labels, "X",
                               random.Random(oa.SEED), **FAST)
    finally:
        random.Random.sample = orig
    # the population sampled from is days, not (symbol, day) keys
    assert seen == {(DAYS[0],)}, seen


def test_noise_null_is_five_percent_not_an_arbitrary_floor():
    """The reproduction rate is scored against what a label with NO effect
    would produce (it still clears p<0.05 in ~5% of halves), not against a
    number someone picked. 2/8 is noise; 3/8 is not."""
    assert oa.binom_tail(2, 8, oa.NOISE_CARVE_RATE) >= oa.STABILITY_ALPHA
    assert oa.binom_tail(3, 8, oa.NOISE_CARVE_RATE) < oa.STABILITY_ALPHA
    assert oa.binom_tail(0, 8, oa.NOISE_CARVE_RATE) > 0.999


def test_three_of_eight_beats_noise_and_is_not_penalised_by_a_majority_floor():
    """The seam between the two candidate rules. An effect that reproduces in
    3 of 8 halves is 8x the noise rate (vs-noise p=0.006) and IS a joint — a
    "must carve in most halves" floor would throw it away. This is the test
    that distinguishes the principled null from an arbitrary threshold, and
    without it M43 survives.
    """
    outcomes, labels = _world(set(DAYS[:4]), effect_size=20.0)
    p = _pooled_p(outcomes, labels)
    assert p < 0.05, f"setup broken: pooled p={p}"
    stab = oa.subsample_stability(
        outcomes, labels, "X", random.Random(oa.SEED), **FAST)
    assert (stab["carved"], stab["n_eval"]) == (3, 8), stab
    assert stab["rate"] < 0.5, "must sit BELOW a majority floor to discriminate"
    assert stab["stability_p"] < oa.STABILITY_ALPHA, stab
    assert oa.classify(p, False, stab) == "CARVES"


def test_audit_reads_tune_split_only():
    """I-035-3 / M45. The audit publishes a verdict, so it may not read the
    sealed holdout. It called load_sessions() directly until 2026-08-21;
    once the ledger was backfilled to 90 days that silently put 40
    post-boundary days into a published measurement.
    """
    import types
    import splits

    class FakeSession:
        def __init__(self, day):
            self.day = day

    span = [FakeSession(splits.TUNE_END.replace(day=1)),
            FakeSession(splits.TUNE_END),
            FakeSession(splits.TUNE_END.replace(year=splits.TUNE_END.year + 1))]
    orig = oa.load_sessions
    oa.load_sessions = lambda sym, tf, allow_fetch=False: span
    try:
        got = oa.audit_sessions("ANY")
    finally:
        oa.load_sessions = orig
    assert got, "filter returned nothing — setup broken"
    assert all(s.day <= splits.TUNE_END for s in got), \
        f"sealed holdout leaked into the audit: {[s.day for s in got]}"
    assert len(got) == 2, got
