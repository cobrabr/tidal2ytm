from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tidal2ytm_sidecar import envelope

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def _contract(name: str) -> Any:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _data(name: str) -> Any:
    return _contract(name)["data"]


# YTM shapes, invented: an 11-character video id, two fictional artists and a
# fictional album. `year` is a string on the album and an integer on the
# sibling, because the normalizer has to take both and a corpus that only ever
# showed one would not say so.
_RAW_SONG: dict[str, Any] = {
    "videoId": "kQ7bZ3p1XtA",
    "title": "Paper Tide",
    "artists": [{"name": "Ilse Vandermeer"}, {"name": "Bram Oduya"}],
    "album": {"name": "Lantern Season"},
    "trackNumber": 4,
    "duration_seconds": 217,
}

_RAW_ALBUM: dict[str, Any] = {
    "browseId": "MPREb_00000042",
    "title": "Lantern Season",
    "artists": [{"name": "Ilse Vandermeer"}, {"name": "Bram Oduya"}],
    "year": "2014",
}

_RAW_SIBLING: dict[str, Any] = {
    "browseId": "MPREb_00000043",
    "title": "Lantern Season (Deluxe)",
    "artists": [{"name": "Ilse Vandermeer"}],
    "year": 2016,
}


def test_two_artist_names_survive_an_empty_entry() -> None:
    raw = {
        "videoId": "kQ7bZ3p1XtA",
        "artists": [{"name": "Ilse Vandermeer"}, {"name": ""}, {"name": "Bram Oduya"}],
    }
    assert envelope.normalize_candidates([raw])[0]["artists"] == [
        "Ilse Vandermeer",
        "Bram Oduya",
    ]


# Live search hits carry an `artists` list; some fixtures and siblings carry a
# singular `artist` string. `_candidate_artist_names` reads both, so the
# envelope has to offer both or a candidate that scored under Python loses its
# name here.
def test_a_singular_artist_name_is_used_when_there_is_no_list() -> None:
    assert envelope.normalize_candidates([{"videoId": "kQ7bZ3p1XtA", "artist": "Ilse"}])[0][
        "artists"
    ] == ["Ilse"]


def test_a_payload_with_no_artist_names_yields_an_empty_list() -> None:
    assert envelope.normalize_candidates([{"videoId": "kQ7bZ3p1XtA"}])[0]["artists"] == []


# The same absence on an album is a bare string, not a list, because
# `AlbumSummary.artist` is not an `Option`. Deviation 3 collapsed the list to
# one name, and this is the shape that collapse can produce a stray value in:
# `""` is the only honest answer, since any name here would be compared against
# the source artist as if the album had one.
def test_an_album_with_no_artist_names_yields_an_empty_name() -> None:
    assert envelope.normalize_album_summaries([{"browseId": "MPREb_00000042"}])[0]["artist"] == ""


# `_text` coerces with `str()`, which on a dict or a list produces a Python
# repr. That is fine for a title, which is similarity-compared, and wrong for an
# identifier: a repr would cross the boundary and then fail the host's
# `is_video_id` carrying the corruption and nothing about where it came from.
def test_a_non_string_identifier_is_refused_rather_than_coerced() -> None:
    malformed = {"videoId": {"nested": 1}, "browseId": ["MPREb_00000042"]}
    assert envelope.normalize_candidates([malformed])[0]["video_id"] == ""
    assert envelope.normalize_album_summaries([malformed])[0]["album_id"] == ""


# The counterpart, so the refusal above cannot be satisfied by refusing
# everything: a real identifier is a string and passes through untouched, and a
# free-text field that arrives as a number still converts the way the ported
# scorer reads it.
def test_a_real_identifier_passes_through_untouched() -> None:
    normalized = envelope.normalize_candidates([_RAW_SONG])[0]
    assert normalized["video_id"] == "kQ7bZ3p1XtA"
    assert envelope.normalize_album_summaries([_RAW_ALBUM])[0]["album_id"] == "MPREb_00000042"
    assert (
        envelope.normalize_candidates([{"videoId": "kQ7bZ3p1XtA", "title": 217}])[0]["title"]
        == "217"
    )


# 0 is a year the scorer would compare against, so an unreadable one has to be
# absent rather than zero: "1970" and "we were never told" are not the same
# album.
def test_a_missing_year_is_absent_rather_than_zero() -> None:
    assert envelope.normalize_album_summaries([{"browseId": "MPREb_00000042"}])[0]["year"] is None


def test_an_unparseable_year_is_absent_rather_than_zero() -> None:
    assert envelope.normalize_album_summaries([{"year": "n/a"}])[0]["year"] is None


# The numeric fields arrive as strings more often than as numbers, and the
# Rust side is `Option<i64>`. Passing the string straight through would make the
# host's deserialisation fail and take the whole answer with it, which is what
# `ops._ytm_state` does to an epoch it cannot normalise.
def test_a_numeric_string_is_the_number_it_spells() -> None:
    raw = {"videoId": "kQ7bZ3p1XtA", "trackNumber": "7", "duration_seconds": "195"}
    normalized = envelope.normalize_candidates([raw])[0]
    assert normalized["album_track_num"] == 7
    assert normalized["duration_sec"] == 195


# An album's name is `Option<String>` on the Rust side, so an absent `album`
# object and an empty `name` inside one have to agree — otherwise the host
# reads `Some("")` and treats it as a real album title.
def test_an_absent_album_is_absent_rather_than_an_empty_name() -> None:
    assert envelope.normalize_candidates([{"videoId": "kQ7bZ3p1XtA"}])[0]["album"] is None
    assert (
        envelope.normalize_candidates([{"videoId": "kQ7bZ3p1XtA", "album": {"name": ""}}])[0][
            "album"
        ]
        is None
    )


# A boolean is an `int` in Python. Read as a year it would be 1, and read as a
# duration it would be second 0 or 1 — a fact about the payload mistaken for a
# fact about the catalogue. `ops` refuses the same shape for the same reason.
def test_a_boolean_is_not_a_year_or_a_duration() -> None:
    assert envelope.normalize_album_summaries([{"year": True}])[0]["year"] is None
    assert (
        envelope.normalize_candidates([{"videoId": "kQ7bZ3p1XtA", "duration_seconds": True}])[0][
            "duration_sec"
        ]
        is None
    )


def test_normalize_candidates_matches_the_shared_contract() -> None:
    assert envelope.normalize_candidates([_RAW_SONG]) == [_data("candidate.json")]


def test_normalize_album_summaries_matches_the_shared_contract() -> None:
    assert envelope.normalize_album_summaries([_RAW_ALBUM]) == [_data("album_summary.json")]


def test_normalize_album_detail_matches_the_shared_contract() -> None:
    detail = {**_RAW_ALBUM, "tracks": [_RAW_SONG], "other_versions": [_RAW_SIBLING]}
    assert envelope.normalize_album_detail(detail) == _data("album_detail.json")
