from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import tidal2ytm.plan as plan_mod
import tidal2ytm.plan_io as plan_io
from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, SourceTrack, TrackStatus


def _src(
    tidal_id: int,
    title: str,
    artist: str,
    album: str,
    album_id: int,
    year: int | None,
    duration: int = 200,
    isrc: str | None = None,
    track_num: int = 1,
    disc_num: int = 1,
) -> SourceTrack:
    return SourceTrack(
        tidal_id=tidal_id,
        title=title,
        artist=artist,
        artists=[artist],
        album=album,
        album_id=album_id,
        album_year=year,
        duration_sec=duration,
        isrc=isrc,
        track_num=track_num,
        disc_num=disc_num,
        version=None,
    )


def _match_result(src: SourceTrack, yt_video_id: str, overall: float = 0.85) -> MatchResult:
    return MatchResult(
        source=src,
        yt_video_id=yt_video_id,
        yt_title=src.title,
        yt_artist=src.artist,
        yt_album=src.album,
        yt_album_track_num=src.track_num,
        yt_isrc=src.isrc,
        yt_duration_sec=src.duration_sec,
        match_method=MatchMethod.DURATION,
        confidence=ConfidenceBreakdown(overall=overall),
        status=TrackStatus.PENDING,
    )


# ---------------------------------------------------------------------------
# grouping sort + slug dedup
# ---------------------------------------------------------------------------


def test_group_by_artist_album_sort_and_slug_dedup() -> None:
    # artists should be sorted A→Z case-insensitive; albums by (year, name); tracks by disc/track
    r1 = _match_result(_src(1, "Song B", "Z Artist", "Album B", 20, 2020), "AAAAAAAAAAA")
    r2 = _match_result(_src(2, "Song A", "A Artist", "Album A", 10, 2019), "BBBBBBBBBBB")
    r3 = _match_result(
        _src(3, "Song C", "A Artist", "Album A", 10, 2019, track_num=2), "CCCCCCCCCCC"
    )
    groups = plan_mod.group_by_artist_album([r1, r2, r3])
    assert [g.name for g in groups] == ["A Artist", "Z Artist"]
    # A Artist's album
    assert groups[0].albums[0].name == "Album A"
    assert [t.source.tidal_id for t in groups[0].albums[0].tracks] == [2, 3]


def test_group_by_artist_album_year_tie_breaker() -> None:
    # same year -> album name A→Z
    r1 = _match_result(_src(1, "T1", "Same Artist", "Zebra", 10, 2020), "AAAAAAAAAAA")
    r2 = _match_result(_src(2, "T2", "Same Artist", "Apple", 11, 2020), "BBBBBBBBBBB")
    groups = plan_mod.group_by_artist_album([r1, r2])
    assert [a.name for a in groups[0].albums] == ["Apple", "Zebra"]


def test_group_by_artist_album_slug_dedup() -> None:
    # two distinct artist names that slug to same value -> dedup with -2
    r1 = _match_result(_src(1, "T1", "Test Artist", "War Child", 10, 2020), "AAAAAAAAAAA")
    r2 = _match_result(_src(2, "T2", "Test-Artist", "War Child", 11, 2021), "BBBBBBBBBBB")
    groups = plan_mod.group_by_artist_album([r1, r2])
    slugs = [g.match_id for g in groups]
    # both slug to test-artist, one should be deduped
    assert slugs[0] != slugs[1]
    assert "test-artist" in slugs[0]
    assert "test-artist-2" in slugs


def test_group_by_artist_album_album_slug_dedup_within_artist() -> None:
    # same artist, two albums with names that slug identically -> dedup
    r1 = _match_result(_src(1, "T1", "Same Artist", "War Child", 10, 2020), "AAAAAAAAAAA")
    r2 = _match_result(_src(2, "T2", "Same Artist", "war child!!", 11, 2021), "BBBBBBBBBBB")
    groups = plan_mod.group_by_artist_album([r1, r2])
    assert len(groups) == 1
    album_ids = [a.match_id for a in groups[0].albums]
    assert album_ids[0] != album_ids[1]
    assert album_ids[0].startswith("same-artist/")
    assert album_ids[1].endswith("-2")


# ---------------------------------------------------------------------------
# run_plan merge semantics
# ---------------------------------------------------------------------------


def test_run_plan_merge_new_and_kept(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None, raising=False)
    tidal_session = MagicMock()
    tidal_session.user.id = 999
    # seed existing plan with one transferred track
    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "B",
                        "match_id": "a/b",
                        "tracks": [
                            {
                                "tidal_id": 1,
                                "title": "Song",
                                "status": "transferred",
                                "yt_video_id": "AAAAAAAAAAA",
                                "confidence": {"overall": 1.0},
                                "match_method": "isrc",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    plan_io.save_plan(plan, isolated_data_dir / "transfer_plan.toml")
    src1 = _src(1, "Song", "A", "B", 1, 2020)
    src2 = _src(2, "New", "A", "B", 1, 2020)
    with patch("tidal2ytm.plan.get_liked_tracks", return_value=[src1, src2]):
        with patch("tidal2ytm.plan.match_track") as m:
            m.return_value = _match_result(src2, "BBBBBBBBBBB", overall=0.85)
            monkeypatch.setattr("builtins.input", lambda _: "n")
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=False,
            )
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    tids = {t["tidal_id"] for t in plan_io.iter_tracks(loaded)}
    assert tids == {1, 2}
    assert any(
        t["tidal_id"] == 1 and t["status"] == "transferred" for t in plan_io.iter_tracks(loaded)
    )


def test_run_plan_skips_transferred_without_matching(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None, raising=False)
    tidal_session = MagicMock()
    tidal_session.user.id = 1
    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "B",
                        "match_id": "a/b",
                        "tracks": [
                            {
                                "tidal_id": 10,
                                "title": "Done",
                                "status": "transferred",
                                "yt_video_id": "AAAAAAAAAAA",
                                "confidence": {"overall": 1.0},
                                "match_method": "isrc",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    plan_io.save_plan(plan, isolated_data_dir / "transfer_plan.toml")
    src = _src(10, "Done", "A", "B", 1, 2020)
    with patch("tidal2ytm.plan.get_liked_tracks", return_value=[src]):
        with patch("tidal2ytm.plan.match_track") as m:
            m.side_effect = AssertionError("should not match transferred")
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=False,
            )
    # should not have called match_track at all (or at least not for transferred)


def test_run_plan_new_track_added(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None, raising=False)
    tidal_session = MagicMock()
    tidal_session.user.id = 42
    src = _src(99, "New Song", "New Artist", "New Album", 5, 2022)
    with patch("tidal2ytm.plan.get_liked_tracks", return_value=[src]):
        with patch(
            "tidal2ytm.plan.match_track", return_value=_match_result(src, "CCCCCCCCCCC", 0.9)
        ):
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=False,
            )
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    assert any(t["tidal_id"] == 99 for t in plan_io.iter_tracks(loaded))
    assert loaded["meta"]["total_tracks"] == 1
    assert loaded["meta"]["pending"] == 1


def test_run_plan_force_overwrites_better_match(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None, raising=False)
    tidal_session = MagicMock()
    tidal_session.user.id = 1
    # existing pending track with low confidence
    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "B",
                        "match_id": "a/b",
                        "tracks": [
                            {
                                "tidal_id": 5,
                                "title": "Song",
                                "status": "pending",
                                "yt_video_id": "AAAAAAAAAAA",
                                "confidence": {"overall": 0.5},
                                "match_method": "fuzzy",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    plan_io.save_plan(plan, isolated_data_dir / "transfer_plan.toml")
    src = _src(5, "Song", "A", "B", 1, 2020)
    new_res = _match_result(src, "BBBBBBBBBBB", overall=0.9)
    with patch("tidal2ytm.plan.get_liked_tracks", return_value=[src]):
        with patch("tidal2ytm.plan.match_track", return_value=new_res):
            # force should not prompt
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=True,
            )
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    t = next(t for t in plan_io.iter_tracks(loaded) if t["tidal_id"] == 5)
    assert t["yt_video_id"] == "BBBBBBBBBBB"
    assert t["confidence"]["overall"] == 0.9


def test_run_plan_prompt_decline_keeps_existing(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None, raising=False)
    tidal_session = MagicMock()
    tidal_session.user.id = 1
    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "B",
                        "match_id": "a/b",
                        "tracks": [
                            {
                                "tidal_id": 6,
                                "title": "Song",
                                "status": "pending",
                                "yt_video_id": "AAAAAAAAAAA",
                                "confidence": {"overall": 0.5},
                                "match_method": "fuzzy",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    plan_io.save_plan(plan, isolated_data_dir / "transfer_plan.toml")
    src = _src(6, "Song", "A", "B", 1, 2020)
    with patch("tidal2ytm.plan.get_liked_tracks", return_value=[src]):
        with patch(
            "tidal2ytm.plan.match_track", return_value=_match_result(src, "BBBBBBBBBBB", 0.9)
        ):
            monkeypatch.setattr("builtins.input", lambda _: "n")
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=False,
            )
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    t = next(t for t in plan_io.iter_tracks(loaded) if t["tidal_id"] == 6)
    assert t["yt_video_id"] == "AAAAAAAAAAA"
    assert t["confidence"]["overall"] == 0.5


def test_run_plan_prompt_accept_upgrades(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None, raising=False)
    tidal_session = MagicMock()
    tidal_session.user.id = 1
    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "B",
                        "match_id": "a/b",
                        "tracks": [
                            {
                                "tidal_id": 7,
                                "title": "Song",
                                "status": "pending",
                                "yt_video_id": "AAAAAAAAAAA",
                                "confidence": {"overall": 0.5},
                                "match_method": "fuzzy",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    plan_io.save_plan(plan, isolated_data_dir / "transfer_plan.toml")
    src = _src(7, "Song", "A", "B", 1, 2020)
    with patch("tidal2ytm.plan.get_liked_tracks", return_value=[src]):
        with patch(
            "tidal2ytm.plan.match_track", return_value=_match_result(src, "BBBBBBBBBBB", 0.9)
        ):
            monkeypatch.setattr("builtins.input", lambda _: "y")
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=False,
            )
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    t = next(t for t in plan_io.iter_tracks(loaded) if t["tidal_id"] == 7)
    assert t["yt_video_id"] == "BBBBBBBBBBB"


def test_run_plan_kept_when_new_confidence_lower(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None, raising=False)
    tidal_session = MagicMock()
    tidal_session.user.id = 1
    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "B",
                        "match_id": "a/b",
                        "tracks": [
                            {
                                "tidal_id": 8,
                                "title": "Song",
                                "status": "pending",
                                "yt_video_id": "AAAAAAAAAAA",
                                "confidence": {"overall": 0.95},
                                "match_method": "isrc",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    plan_io.save_plan(plan, isolated_data_dir / "transfer_plan.toml")
    src = _src(8, "Song", "A", "B", 1, 2020)
    with patch("tidal2ytm.plan.get_liked_tracks", return_value=[src]):
        with patch(
            "tidal2ytm.plan.match_track", return_value=_match_result(src, "BBBBBBBBBBB", 0.6)
        ) as m:
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=False,
            )
            m.assert_called_once()
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    t = next(t for t in plan_io.iter_tracks(loaded) if t["tidal_id"] == 8)
    # kept existing because new confidence lower
    assert t["yt_video_id"] == "AAAAAAAAAAA"
    assert t["confidence"]["overall"] == 0.95


def test_run_plan_updates_meta(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None, raising=False)
    tidal_session = MagicMock()
    tidal_session.user.id = 123
    src = _src(11, "X", "Artist", "Album", 1, 2020)
    with patch("tidal2ytm.plan.get_liked_tracks", return_value=[src]):
        with patch(
            "tidal2ytm.plan.match_track", return_value=_match_result(src, "AAAAAAAAAAA", 0.8)
        ):
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=False,
            )
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    assert "meta" in loaded
    assert loaded["meta"]["total_tracks"] == 1
    assert "generated_at" in loaded["meta"]


def test_run_plan_handles_missing_year(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None, raising=False)
    tidal_session = MagicMock()
    tidal_session.user.id = 7
    src = _src(20, "NoYear", "Artist", "AlbumNoYear", 2, None)
    with patch("tidal2ytm.plan.get_liked_tracks", return_value=[src]):
        with patch(
            "tidal2ytm.plan.match_track", return_value=_match_result(src, "AAAAAAAAAAA", 0.8)
        ):
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=False,
            )
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    assert loaded["meta"]["total_tracks"] == 1
    # year should be omitted rather than serializing None
    album = loaded["artists"][0]["albums"][0]
    assert "year" not in album or isinstance(album.get("year"), int)
