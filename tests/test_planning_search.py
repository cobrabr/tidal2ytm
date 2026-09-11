from __future__ import annotations

from pathlib import Path
from typing import Any

from tidal2ytm.models import SourceTrack
from tidal2ytm.planning_search import (
    parse_tidal_link,
    resolve_album_link,
    resolve_track_link,
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
    hits, direct = search_library(tracks, "apple", "general")
    assert [t.tidal_id for t in hits] == [1]
    assert direct is True


def test_songs_mode_ignores_artist_text() -> None:
    tracks = [_track(1, "Azure", "Larkspur", "Azure")]
    hits, direct = search_library(tracks, "lark", "songs")
    assert hits == []
    assert direct is True


def test_albums_mode_matches_album_name() -> None:
    tracks = [_track(1, "Apple", "Wren", "Apple")]
    hits, direct = search_library(tracks, "apple", "albums")
    assert [t.tidal_id for t in hits] == [1]
    assert direct is True


def test_fuzzy_tier_tolerates_typo() -> None:
    tracks = [_track(1, "Starling", "Wren", "Drum")]
    hits, direct = search_library(tracks, "starlink", "songs")
    assert [t.tidal_id for t in hits] == [1]
    assert direct is False


def test_exact_matches_exclude_fuzzy() -> None:
    tracks = [
        _track(1, "Lantern", "Blashen", "Pamphlet"),
        _track(2, "Ash", "Fern", "Pond"),
    ]
    hits, direct = search_library(tracks, "blashen", "general")
    assert [t.tidal_id for t in hits] == [1]
    assert direct is True


def test_contains_search_lists_all_with_exact_first() -> None:
    tracks = [_track(1, "Lovely Day", "Bill", "Day"), _track(2, "Love", "Bill", "Night")]
    hits, direct = search_library(tracks, "love", "general")
    assert [t.tidal_id for t in hits] == [2, 1]
    assert direct is True


def test_fuzzy_fallback_excludes_perfect_scores() -> None:
    tracks = [_track(1, "Ash", "Fern", "Pond")]
    hits, direct = search_library(tracks, "clashen", "general")
    assert [t.tidal_id for t in hits] == [1]
    assert direct is False


def test_empty_query_returns_everything() -> None:
    tracks = [_track(1, "A", "B", "C"), _track(2, "D", "E", "F")]
    hits, direct = search_library(tracks, "", "general")
    assert [t.tidal_id for t in hits] == [1, 2]
    assert direct is True


def test_search_debug_logs_field_scores(monkeypatch: Any, tmp_path: Path) -> None:
    from tidal2ytm import planning_search as search_mod

    monkeypatch.setattr(search_mod, "DATA_DIR", tmp_path)
    monkeypatch.setenv("TIDAL2YTM_DEBUG", "1")
    tracks = [_track(1, "Lantern", "Blashen", "Pamphlet")]
    search_mod.search_library(tracks, "blashen", "general")
    log = (tmp_path / "search_debug.log").read_text()
    assert "score=1.000" in log and "field=artist" in log and "Lantern" in log
    assert 'value="Blashen"' in log


def test_search_debug_off_writes_nothing(monkeypatch: Any, tmp_path: Path) -> None:
    from tidal2ytm import planning_search as search_mod

    monkeypatch.setattr(search_mod, "DATA_DIR", tmp_path)
    monkeypatch.delenv("TIDAL2YTM_DEBUG", raising=False)
    tracks = [_track(1, "Lantern", "Blashen", "Pamphlet")]
    search_mod.search_library(tracks, "blashen", "general")
    assert not (tmp_path / "search_debug.log").exists()


def test_parse_tidal_links() -> None:
    assert parse_tidal_link("https://tidal.com/browse/track/7654321") == ("track", 7654321)
    assert parse_tidal_link("https://listen.tidal.com/album/8765432") == ("album", 8765432)
    assert parse_tidal_link("apple") is None


def test_resolve_links_against_library() -> None:
    tracks = [
        _track(1, "Etude", "Holt", "Fugue", album_id=7),
        _track(2, "Var 1", "Holt", "Fugue", album_id=7),
    ]
    hit = resolve_track_link(tracks, 1)
    assert hit is not None and hit.title == "Etude"
    assert resolve_track_link(tracks, 999) is None
    assert [t.tidal_id for t in resolve_album_link(tracks, 7)] == [1, 2]
    assert resolve_album_link(tracks, 888) == []
