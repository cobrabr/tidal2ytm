from __future__ import annotations

from unittest.mock import MagicMock

from tidal2ytm.tidal_source import get_liked_tracks


def test_tidal_source_year_missing_and_artist_none() -> None:
    session = MagicMock()
    track = MagicMock()
    track.name = "Song"
    track.artist.name = None
    track.album.name = "Album"
    track.album.year = None
    track.album.id = 123
    track.duration = 200
    track.isrc = None
    track.id = 999
    track.year = None
    track.track_num = 1
    track.volume_num = 1
    track.artists = []
    session.user.favorites.tracks.return_value = [track]
    result = get_liked_tracks(session)
    assert result[0].tidal_id == 999 and result[0].year is None
    assert result[0].album_year is None
    assert result[0].artist == ""


def test_tidal_source_artist_none_object() -> None:
    session = MagicMock()
    track = MagicMock()
    track.name = "Song2"
    track.artist = None
    track.album.name = "Album2"
    track.album.year = 2020
    track.album.id = 456
    track.duration = 180
    track.isrc = "USABC1234567"
    track.id = 1000
    track.track_num = 2
    track.volume_num = 1
    track.artists = []
    session.user.favorites.tracks.return_value = [track]
    result = get_liked_tracks(session)
    assert result[0].tidal_id == 1000
    assert result[0].artist == ""
    assert result[0].album_year == 2020


def test_tidal_source_handles_multiple_tracks() -> None:
    session = MagicMock()
    t1 = MagicMock()
    t1.name = "A"
    t1.artist.name = "Artist A"
    t1.artists = []
    t1.album.name = "Album A"
    t1.album.id = 1
    t1.album.year = 2021
    t1.duration = 200
    t1.isrc = None
    t1.id = 1
    t1.track_num = 1
    t1.volume_num = 1
    t2 = MagicMock()
    t2.name = "B"
    t2.artist.name = "Artist B"
    t2.artists = []
    t2.album.name = "Album B"
    t2.album.id = 2
    t2.album.year = None
    t2.duration = 210
    t2.isrc = None
    t2.id = 2
    t2.track_num = 2
    t2.volume_num = 1
    session.user.favorites.tracks.return_value = [t1, t2]
    result = get_liked_tracks(session)
    assert len(result) == 2
    assert result[0].tidal_id == 1
    assert result[1].tidal_id == 2
