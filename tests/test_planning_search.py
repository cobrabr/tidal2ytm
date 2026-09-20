from __future__ import annotations

from tidal2ytm.models import SourceTrack
from tidal2ytm.planning_search import (
    parse_tidal_link,
    resolve_album_link,
    search_library,
)


def _track(tidal_id: int, title: str, artist: str, album: str, album_id: int = 1) -> SourceTrack:
    return SourceTrack(
        tidal_id=tidal_id,
        title=title,
        artist=artist,
        artists=[artist],
        album=album,
        album_id=album_id,
        album_year=1971,
        duration_sec=200,
        isrc=None,
        track_num=1,
        disc_num=1,
        version=None,
    )


def test_general_search_substring_case_insensitive() -> None:
    tracks = [_track(1, "Apple", "Wren", "Apple"), _track(2, "Azure", "Lark", "Azure")]
    hits, direct = search_library(tracks, "apple")
    assert [t.tidal_id for t in hits] == [1]
    assert direct is True


def test_exact_matches_exclude_fuzzy() -> None:
    tracks = [
        _track(1, "Lantern", "Blashen", "Pamphlet"),
        _track(2, "Ash", "Fern", "Pond"),
    ]
    hits, direct = search_library(tracks, "blashen")
    assert [t.tidal_id for t in hits] == [1]
    assert direct is True


def test_contains_search_lists_all_with_exact_first() -> None:
    tracks = [_track(1, "Lovely Day", "Bill", "Day"), _track(2, "Love", "Bill", "Night")]
    hits, direct = search_library(tracks, "love")
    assert [t.tidal_id for t in hits] == [2, 1]
    assert direct is True


def test_fuzzy_fallback_excludes_perfect_scores() -> None:
    tracks = [_track(1, "Ash", "Fern", "Pond")]
    hits, direct = search_library(tracks, "clashen")
    assert [t.tidal_id for t in hits] == [1]
    assert direct is False


def test_empty_query_returns_everything() -> None:
    tracks = [_track(1, "A", "B", "C"), _track(2, "D", "E", "F")]
    hits, direct = search_library(tracks, "")
    assert [t.tidal_id for t in hits] == [1, 2]
    assert direct is True


def test_parse_tidal_links() -> None:
    assert parse_tidal_link("https://tidal.com/browse/track/7654321") == ("track", 7654321)
    assert parse_tidal_link("https://listen.tidal.com/album/8765432") == ("album", 8765432)
    assert parse_tidal_link("apple") is None


def test_resolve_album_link_against_library() -> None:
    tracks = [
        _track(1, "Etude", "Holt", "Fugue", album_id=7),
        _track(2, "Var 1", "Holt", "Fugue", album_id=7),
    ]
    assert [t.tidal_id for t in resolve_album_link(tracks, 7)] == [1, 2]
    assert resolve_album_link(tracks, 888) == []


def test_fuzzy_flag_false_when_library_has_no_match() -> None:
    tracks = [_track(1, "Apple", "Wren", "Apple")]
    hits, is_direct = search_library(tracks, "zzzz no such song")
    assert hits == [] and is_direct is False
