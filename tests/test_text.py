from tidal2ytm.text import normalize, similarity


def test_normalize_strips_accents_and_punctuation():
    assert normalize("Beyoncé!") == "beyonce"


def test_similarity_empty_side_is_zero():
    assert similarity("", "anything") == 0.0
    assert similarity("", "") == 0.0


def test_similarity_identical_is_one():
    assert similarity("Song", "Song") == 1.0
