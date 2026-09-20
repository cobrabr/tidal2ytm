from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tidal2ytm.tidal_source import get_liked_tracks


@dataclass
class FakeArtist:
    name: str | None = ""


@dataclass
class FakeAlbum:
    name: str = ""
    id: int = -1
    year: int | None = None


@dataclass
class FakeTrack:
    id: int = 0
    name: str = ""
    artist: FakeArtist | None = None
    artists: list[FakeArtist] = field(default_factory=list[FakeArtist])
    album: FakeAlbum | None = None
    duration: int = 0
    isrc: str | None = None
    track_num: int = 0
    volume_num: int = 0
    version: str | None = None


class FakeFavorites:
    """Scripted result pages; asserts the per-page limit contract."""

    def __init__(self, pages: list[list[Any]]) -> None:
        self._pages = pages

    def tracks(self, limit: int = 9999, offset: int = 0) -> list[Any]:
        assert limit == 9999
        if not self._pages:
            return []
        return self._pages.pop(0)


class FakeUser:
    def __init__(self, favorites: FakeFavorites) -> None:
        self.favorites = favorites


class FakeSession:
    def __init__(self, favorites: FakeFavorites) -> None:
        self.user = FakeUser(favorites)


class BareTrack:
    """Track-like object missing the expected attributes."""

    def __init__(self) -> None:
        self.id = 7


def _session_with(pages: list[list[Any]]) -> FakeSession:
    return FakeSession(FakeFavorites(pages))


def test_tidal_source_year_missing_and_artist_name_none() -> None:
    track = FakeTrack(
        id=999,
        name="Ember Fall",
        artist=FakeArtist(name=None),
        artists=[],
        album=FakeAlbum(name="Ashen Light", id=123, year=None),
        duration=200,
        track_num=1,
        volume_num=1,
    )
    result = get_liked_tracks(_session_with([[track]]))  # type: ignore[arg-type]
    assert result[0].tidal_id == 999 and result[0].album_year is None
    assert result[0].artist == ""


def test_tidal_source_artist_none_object() -> None:
    track = FakeTrack(
        id=1000,
        name="Hollow Crown",
        artist=None,
        artists=[],
        album=FakeAlbum(name="Gloaming", id=456, year=2020),
        duration=180,
        isrc="USABC1234567",
        track_num=2,
        volume_num=1,
    )
    result = get_liked_tracks(_session_with([[track]]))  # type: ignore[arg-type]
    assert result[0].tidal_id == 1000
    assert result[0].artist == ""
    assert result[0].album_year == 2020


def test_tidal_source_handles_multiple_tracks() -> None:
    t1 = FakeTrack(
        id=1,
        name="Cinder Path",
        artist=FakeArtist(name="Vesper Vale"),
        artists=[],
        album=FakeAlbum(name="Ashen Light", id=1, year=2021),
        duration=200,
        track_num=1,
        volume_num=1,
    )
    t2 = FakeTrack(
        id=2,
        name="Mire Song",
        artist=FakeArtist(name="Blashen Moor"),
        artists=[],
        album=FakeAlbum(name="Fen Hymns", id=2, year=None),
        duration=210,
        track_num=2,
        volume_num=1,
    )
    result = get_liked_tracks(_session_with([[t1, t2]]))  # type: ignore[arg-type]
    assert [t.tidal_id for t in result] == [1, 2]
    # Field mapping across multiple tracks, not just identity.
    assert result[0].artist == "Vesper Vale"
    assert result[0].album == "Ashen Light"
    assert result[0].album_year == 2021
    assert result[0].track_num == 1
    assert result[1].artist == "Blashen Moor"
    assert result[1].album == "Fen Hymns"
    assert result[1].album_year is None
    assert result[1].track_num == 2


def test_tidal_source_propagates_attribute_error() -> None:
    session = _session_with([[BareTrack()]])
    with pytest.raises(AttributeError):
        get_liked_tracks(session)  # type: ignore[arg-type]


def test_tidal_source_paginates_until_exhausted() -> None:
    t1 = FakeTrack(id=1, name="Cinder Path")
    t2 = FakeTrack(id=2, name="Mire Song")
    t3 = FakeTrack(id=3, name="Hollow Crown")
    result = get_liked_tracks(_session_with([[t1, t2], [t3]]))  # type: ignore[arg-type]
    assert [t.tidal_id for t in result] == [1, 2, 3]


def test_tidal_source_empty_favourites_returns_empty_list() -> None:
    assert get_liked_tracks(_session_with([])) == []  # type: ignore[arg-type]


def test_tidal_source_api_error_propagates() -> None:
    class BoomFavorites:
        def tracks(self, limit: int = 9999, offset: int = 0) -> list[Any]:
            del limit, offset
            raise RuntimeError("api down")

    with pytest.raises(RuntimeError):
        get_liked_tracks(FakeSession(BoomFavorites()))  # type: ignore[arg-type]
