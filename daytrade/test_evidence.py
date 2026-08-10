"""Card 015 — evidence objects and the event lifecycle.

Core rule under test: a duplicate article updates provenance but can NEVER
raise a group's urgency. Fault rows in specs/015_016_MUTATION_LOG.md
(driver: mutation_check_015_016.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evidence import (Evidence, EvidenceError, EvidenceStore, TTL_MIN,
                      canonical_group, reliability_from)

NOW = datetime(2026, 8, 7, 14, 30, tzinfo=timezone.utc)
ISO = NOW.isoformat()


def ev(headline: str, *, eid: str = "e1", etype: str = "PRODUCT",
       scope: str = "symbol", symbols: tuple = ("NVDA",),
       severity: float = 0.8, direction: str = "bullish",
       source: str = "reuters", first_seen: str = ISO) -> Evidence:
    return Evidence(evidence_id=eid, evidence_type=etype, scope=scope,
                    symbols=symbols, headline=headline, source=source,
                    source_time=ISO, first_seen=first_seen,
                    severity=severity, direction=direction,
                    reliability=0.9, reliability_basis="table:v1")


# ---------------------------------------------------------------- the object

def test_evidence_validates():
    with pytest.raises(EvidenceError):
        ev("x", etype="VIBES")
    with pytest.raises(EvidenceError):
        ev("x", scope="galaxy")
    with pytest.raises(EvidenceError):
        ev("x", severity=1.5)
    with pytest.raises(EvidenceError):
        ev("x", direction="sideways-ish")
    with pytest.raises(EvidenceError):
        ev("x", scope="symbol", symbols=())
    with pytest.raises(EvidenceError):                  # naive timestamp
        Evidence(evidence_id="e", evidence_type="PRODUCT", scope="symbol",
                 symbols=("NVDA",), headline="x", source="s",
                 source_time="2026-08-07T14:30:00", first_seen=ISO,
                 severity=0.5, direction="bullish", reliability=0.9,
                 reliability_basis="table:v1")


def test_unrated_source_is_explicit_never_defaulted():
    r, basis = reliability_from({"reuters": 0.9}, "v1", "some_blog")
    assert r is None and basis == "unrated"
    r, basis = reliability_from({"reuters": 0.9}, "v1", "reuters")
    assert r == 0.9 and basis == "table:v1"
    with pytest.raises(EvidenceError):                  # None must SAY unrated
        Evidence(evidence_id="e", evidence_type="PRODUCT", scope="symbol",
                 symbols=("NVDA",), headline="x", source="s", source_time=ISO,
                 first_seen=ISO, severity=0.5, direction="bullish",
                 reliability=None, reliability_basis="table:v1")


def test_canonicalization_groups_the_same_story():
    a = canonical_group("NVDA Unveils New Chip!")
    b = canonical_group("  nvda unveils new chip ")
    c = canonical_group("NVDA delays new chip")
    assert a == b and a != c


# ---------------------------------------------------------------- lifecycle

def test_lifecycle_is_forward_only_and_idempotent():
    st = EvidenceStore()
    gid = st.add(ev("NVDA unveils new chip"))
    assert st.view(gid, now=NOW).state == "REPORTED"
    st.advance_state(gid, "CONFIRMED")
    st.advance_state(gid, "CONFIRMED")                  # idempotent
    st.advance_state(gid, "DIGESTED")
    with pytest.raises(EvidenceError):
        st.advance_state(gid, "RUMOR")                  # backwards never
    with pytest.raises(EvidenceError):
        st.advance_state(gid, "PLASMA")


def test_rumor_to_confirmation_raises_urgency():
    st = EvidenceStore()
    gid = st.add(ev("chatter about NVDA supply deal", etype="RUMOR_SOCIAL"))
    u_rumor = st.view(gid, now=NOW).urgency
    st.advance_state(gid, "CONFIRMED")
    u_confirmed = st.view(gid, now=NOW).urgency
    assert st.view(gid, now=NOW).state == "CONFIRMED"
    assert u_confirmed > u_rumor                        # new INFORMATION moves it


def test_duplicates_update_provenance_but_never_raise_urgency():
    """The card's core rule: ten recaps of one story are one fact."""
    st = EvidenceStore()
    gid = st.add(ev("NVDA unveils new chip", eid="e1"))
    before = st.view(gid, now=NOW)
    for i in range(9):
        dup_id = st.add(ev("NVDA Unveils New Chip!", eid=f"dup{i}",
                           severity=1.0,               # louder recap, same story
                           first_seen=(NOW + timedelta(minutes=i)).isoformat()))
        assert dup_id == gid
    after = st.view(gid, now=NOW)
    assert after.count == 10                            # provenance updated
    assert after.urgency <= before.urgency              # urgency NEVER raised
    assert after.state == before.state


def test_stale_recap_carries_no_urgency():
    st = EvidenceStore()
    gid = st.add(ev("NVDA unveils new chip", etype="RUMOR_SOCIAL"))  # ttl 60m
    at_ttl = NOW + timedelta(minutes=TTL_MIN["RUMOR_SOCIAL"])
    assert st.view(gid, now=at_ttl).fresh is True       # inclusive boundary
    past = at_ttl + timedelta(seconds=1)
    v = st.view(gid, now=past)
    assert v.fresh is False and v.urgency == 0.0


def test_digested_story_steers_nothing():
    st = EvidenceStore()
    gid = st.add(ev("NVDA unveils new chip", severity=1.0))
    st.advance_state(gid, "DIGESTED")
    assert st.view(gid, now=NOW).urgency == 0.0


def test_conflict_is_surfaced_never_averaged():
    st = EvidenceStore()
    gid = st.add(ev("NVDA export licence ruling", direction="bullish"))
    st.add(ev("NVDA Export Licence Ruling", eid="e2", direction="bearish"))
    v = st.view(gid, now=NOW)
    assert v.conflicted is True and v.direction == "mixed"


# ------------------------------------------------------------------- scoping

def test_scoped_delivery_nvda_vs_market():
    st = EvidenceStore()
    st.add(ev("NVDA unveils new chip", symbols=("NVDA",)))
    st.add(ev("Fed surprises with emergency meeting", eid="e2", etype="MACRO",
              scope="market", symbols=()))
    nvda = st.relevant_to("NVDA", now=NOW, index_linked=True)
    assert len(nvda) == 2                               # its story + the market's
    tsla_unlinked = st.relevant_to("TSLA", now=NOW, index_linked=False)
    assert tsla_unlinked == []                          # neither story is its problem
    tsla_linked = st.relevant_to("TSLA", now=NOW, index_linked=True)
    assert len(tsla_linked) == 1                        # market story only
    assert tsla_linked[0].most_specific_scope == "market"


def test_replay_at_same_as_of_is_byte_stable():
    def build():
        st = EvidenceStore()
        st.add(ev("NVDA unveils new chip"))
        gid = st.add(ev("NVDA Unveils New Chip!", eid="e2"))
        st.advance_state(gid, "CONFIRMED")
        st.add(ev("Fed emergency meeting", eid="e3", etype="MACRO",
                  scope="market", symbols=()))
        return [v.__dict__ for v in st.relevant_to("NVDA", now=NOW,
                                                   index_linked=True)]
    assert build() == build()
