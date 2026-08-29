"""Phase 3 — a feature that is not as-of computable cannot enter a backtest state; the loader raises."""
import pytest

from sovereign.forex import feature_registry as fr

STATE_KEYS = ("unrealized_r_net", "t", "weekend_next")


def test_state_keys_are_admissible():
    feats = fr.require_as_of(STATE_KEYS)
    assert [f.name for f in feats] == list(STATE_KEYS)


@pytest.mark.parametrize("bad", ["sentiment", "polygon_sentiment", "alpha_operator_bias", "cb_calendar_days_to_next"])
def test_contaminated_or_unimplemented_feature_refused(bad):
    with pytest.raises(fr.ProvenanceError, match="NOT-AS-OF"):
        fr.require_as_of(STATE_KEYS + (bad,))


@pytest.mark.parametrize("label", ["terminal_r", "incumbent_r_net", "absorbed_by"])
def test_label_cannot_be_a_state_key(label):
    with pytest.raises(fr.ProvenanceError, match="LABEL"):
        fr.require_as_of(STATE_KEYS + (label,))


def test_unregistered_feature_refused():
    with pytest.raises(fr.ProvenanceError, match="UNREGISTERED"):
        fr.require_as_of(STATE_KEYS + ("something_new",))


def test_all_offenders_listed_at_once():
    with pytest.raises(fr.ProvenanceError) as ei:
        fr.require_as_of(("sentiment", "terminal_r", "nope"))
    m = str(ei.value)
    assert "NOT-AS-OF 'sentiment'" in m and "LABEL 'terminal_r'" in m and "UNREGISTERED 'nope'" in m


def test_flipping_a_state_feature_off_makes_the_loader_raise(monkeypatch):
    """The mutation the brief asks for: declare a used feature not-as-of → the same call now raises."""
    flipped = fr.Feature("weekend_next", False, "flipped for the test")
    monkeypatch.setitem(fr.REGISTRY, "weekend_next", flipped)
    with pytest.raises(fr.ProvenanceError, match="weekend_next"):
        fr.require_as_of(STATE_KEYS)


def test_modeled_features_are_reported_not_gated():
    assert fr.modeled_features(("swap_r", "t", "close")) == ("swap_r",)
    fr.require_as_of(("swap_r",))  # modeled but as-of computable: admissible, flagged in the report
