from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import tidal2ytm.plan_io as plan_io
import tidal2ytm.transfer as transfer_mod


def _seed_plan(path: Path, tracks: list[dict[str, Any]]) -> None:
    plan: dict[str, Any] = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [{"name": "B", "match_id": "a/b", "tracks": tracks}],
            }
        ],
    }
    plan_io.save_plan(plan, path)


def test_run_transfer_scope_and_per_track_save(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [
            {
                "tidal_id": 1,
                "title": "Song",
                "status": "pending",
                "yt_video_id": "dQw4w9WgXcQ",
                "confidence": {"overall": 0.9},
            },
            {
                "tidal_id": 2,
                "title": "Other",
                "status": "needs_review",
                "yt_video_id": "AAAAAAAAAAA",
                "confidence": {"overall": 0.2},
            },
        ],
    )
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    yt.edit_song_library_status.return_value = {"status": "STATUS_SUCCEEDED"}
    transfer_mod.run_transfer(
        yt,
        track_id="dQw4w9WgXcQ",
        album_match_id=None,
        artist_match_id=None,
        all_tracks=False,
        dry_run=False,
        include_needs_review=False,
        plan_path=plan_path,
    )
    loaded = plan_io.load_plan(plan_path)
    assert any(
        t["yt_video_id"] == "dQw4w9WgXcQ" and t["status"] == "transferred"
        for t in plan_io.iter_tracks(loaded)
    )
    assert any(t["status"] == "needs_review" for t in plan_io.iter_tracks(loaded))
    # meta recomputed
    assert loaded["meta"]["transferred"] == 1


def test_transfer_per_track_save_and_meta(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [
            {"tidal_id": 10, "title": "S1", "status": "pending", "yt_video_id": "AAAAAAAAAAA"},
            {"tidal_id": 11, "title": "S2", "status": "pending", "yt_video_id": "BBBBBBBBBBB"},
        ],
    )
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    yt.edit_song_library_status.return_value = {"status": "STATUS_SUCCEEDED"}
    # capture save_plan calls to ensure per-track save
    original_save = plan_io.save_plan
    calls: list[int] = []

    def counting_save(plan: dict[str, Any], path: Path) -> None:
        calls.append(1)
        return original_save(plan, path)

    with (
        patch("tidal2ytm.transfer.save_plan", side_effect=counting_save),
        patch("tidal2ytm.transfer.update_plan_meta", wraps=plan_io.update_plan_meta) as mock_meta,
    ):
        transfer_mod.run_transfer(
            yt,
            track_id=None,
            album_match_id="a/b",
            artist_match_id=None,
            all_tracks=False,
            dry_run=False,
            include_needs_review=False,
            plan_path=plan_path,
        )
        assert mock_meta.call_count == 2

    assert len(calls) == 2
    loaded = plan_io.load_plan(plan_path)
    assert all(t["status"] == "transferred" for t in plan_io.iter_tracks(loaded))


def test_transfer_needs_review_skipped_without_flag(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [{"tidal_id": 1, "title": "Low", "status": "needs_review", "yt_video_id": "AAAAAAAAAAA"}],
    )
    yt = MagicMock()
    transfer_mod.run_transfer(
        yt,
        track_id=None,
        album_match_id=None,
        artist_match_id=None,
        all_tracks=True,
        dry_run=False,
        include_needs_review=False,
        plan_path=plan_path,
    )
    yt.get_watch_playlist.assert_not_called()
    loaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(loaded))["status"] == "needs_review"


def test_transfer_needs_review_included_with_flag(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [{"tidal_id": 1, "title": "Low", "status": "needs_review", "yt_video_id": "AAAAAAAAAAA"}],
    )
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    yt.edit_song_library_status.return_value = {"status": "STATUS_SUCCEEDED"}
    # mock the warning prompt input
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "")  # pyright: ignore[reportUnknownLambdaType]
    transfer_mod.run_transfer(
        yt,
        track_id=None,
        album_match_id=None,
        artist_match_id=None,
        all_tracks=True,
        dry_run=False,
        include_needs_review=True,
        plan_path=plan_path,
    )
    yt.get_watch_playlist.assert_called_once()
    loaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(loaded))["status"] == "transferred"


def test_transfer_dry_run_no_status_change(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [{"tidal_id": 5, "title": "Song", "status": "pending", "yt_video_id": "dQw4w9WgXcQ"}],
    )
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    transfer_mod.run_transfer(
        yt,
        track_id="dQw4w9WgXcQ",
        album_match_id=None,
        artist_match_id=None,
        all_tracks=False,
        dry_run=True,
        include_needs_review=False,
        plan_path=plan_path,
    )
    loaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(loaded))["status"] == "pending"
    yt.edit_song_library_status.assert_not_called()


def test_transfer_terminal_noop_when_all_done(isolated_data_dir: Path) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [
            {"tidal_id": 1, "title": "Done", "status": "transferred", "yt_video_id": "AAAAAAAAAAA"},
            {"tidal_id": 2, "title": "Skip", "status": "skip", "yt_video_id": "BBBBBBBBBBB"},
        ],
    )
    yt = MagicMock()
    with pytest.raises(SystemExit) as e:
        transfer_mod.run_transfer(
            yt,
            track_id=None,
            album_match_id=None,
            artist_match_id=None,
            all_tracks=True,
            dry_run=False,
            include_needs_review=False,
            plan_path=plan_path,
        )
    assert e.value.code == 0
    yt.get_watch_playlist.assert_not_called()


def test_transfer_scope_album_and_artist(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    plan_path = isolated_data_dir / "transfer_plan.toml"
    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "Artist A",
                "match_id": "artist-a",
                "albums": [
                    {
                        "name": "Album X",
                        "match_id": "artist-a/album-x",
                        "tracks": [
                            {
                                "tidal_id": 1,
                                "title": "S1",
                                "status": "pending",
                                "yt_video_id": "AAAAAAAAAAA",
                            }
                        ],
                    },
                    {
                        "name": "Album Y",
                        "match_id": "artist-a/album-y",
                        "tracks": [
                            {
                                "tidal_id": 2,
                                "title": "S2",
                                "status": "pending",
                                "yt_video_id": "BBBBBBBBBBB",
                            }
                        ],
                    },
                ],
            },
            {
                "name": "Artist B",
                "match_id": "artist-b",
                "albums": [
                    {
                        "name": "Album Z",
                        "match_id": "artist-b/album-z",
                        "tracks": [
                            {
                                "tidal_id": 3,
                                "title": "S3",
                                "status": "pending",
                                "yt_video_id": "CCCCCCCCCCC",
                            }
                        ],
                    }
                ],
            },
        ],
    }
    plan_io.save_plan(plan, plan_path)
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    yt.edit_song_library_status.return_value = {"status": "STATUS_SUCCEEDED"}

    # --album scope should only transfer one track
    transfer_mod.run_transfer(
        yt,
        track_id=None,
        album_match_id="artist-a/album-x",
        artist_match_id=None,
        all_tracks=False,
        dry_run=False,
        include_needs_review=False,
        plan_path=plan_path,
    )
    loaded = plan_io.load_plan(plan_path)
    assert (
        next(t for t in plan_io.iter_tracks(loaded) if t["tidal_id"] == 1)["status"]
        == "transferred"
    )
    assert next(t for t in plan_io.iter_tracks(loaded) if t["tidal_id"] == 2)["status"] == "pending"
    assert next(t for t in plan_io.iter_tracks(loaded) if t["tidal_id"] == 3)["status"] == "pending"

    # --artist scope
    yt.reset_mock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    transfer_mod.run_transfer(
        yt,
        track_id=None,
        album_match_id=None,
        artist_match_id="artist-b",
        all_tracks=False,
        dry_run=False,
        include_needs_review=False,
        plan_path=plan_path,
    )
    loaded = plan_io.load_plan(plan_path)
    assert (
        next(t for t in plan_io.iter_tracks(loaded) if t["tidal_id"] == 3)["status"]
        == "transferred"
    )


def test_transfer_missing_track_id_exits(isolated_data_dir: Path) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [{"tidal_id": 1, "title": "Song", "status": "pending", "yt_video_id": "AAAAAAAAAAA"}],
    )
    yt = MagicMock()
    with pytest.raises(SystemExit) as e:
        transfer_mod.run_transfer(
            yt,
            track_id="ZZZZZZZZZZZ",
            album_match_id=None,
            artist_match_id=None,
            all_tracks=False,
            dry_run=False,
            include_needs_review=False,
            plan_path=plan_path,
        )
    assert e.value.code == 1


def test_transfer_no_plan_exits(isolated_data_dir: Path, tmp_path: Path) -> None:
    # use a nonexistent plan path
    missing = tmp_path / "missing.toml"
    yt = MagicMock()
    with pytest.raises(SystemExit) as e:
        transfer_mod.run_transfer(
            yt,
            track_id="AAAAAAAAAAA",
            album_match_id=None,
            artist_match_id=None,
            all_tracks=False,
            dry_run=False,
            include_needs_review=False,
            plan_path=missing,
        )
    assert e.value.code == 1


def test_transfer_failed_status_saved(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [{"tidal_id": 77, "title": "Fail", "status": "pending", "yt_video_id": "AAAAAAAAAAA"}],
    )
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    # make edit_song_library_status fail -> ytm_sink returns False
    # -> transfer should mark failed
    with patch("tidal2ytm.transfer.add_track_to_library", return_value=False):
        transfer_mod.run_transfer(
            yt,
            track_id="AAAAAAAAAAA",
            album_match_id=None,
            artist_match_id=None,
            all_tracks=False,
            dry_run=False,
            include_needs_review=False,
            plan_path=plan_path,
        )
    loaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(loaded))["status"] == "failed"
    assert loaded["meta"]["failed"] == 1
