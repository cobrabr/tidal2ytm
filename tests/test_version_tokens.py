from __future__ import annotations

from tidal2ytm.version_tokens import qualifier_similarity, split_base_qualifiers


def test_split_base_qualifiers() -> None:
    base, quals = split_base_qualifiers("Ember Fall (Live) [Remaster]")
    assert base == "Ember Fall"
    assert "live" in quals
    assert "remaster" in quals


def test_qualifier_similarity_empty_vs_present() -> None:
    assert qualifier_similarity((), ("live",)) < 1.0


def test_qualifier_similarity_same_set() -> None:
    assert qualifier_similarity(("live",), ("live",)) == 1.0
