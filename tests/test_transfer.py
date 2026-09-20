from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import tidal2ytm.plan_io as plan_io
import tidal2ytm.transfer as transfer_mod
from tidal2ytm.errors import InvalidScopeError, PlanNotFoundError


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
                "yt_video_id": "CCCCCCCCCCC",
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
        track_id="CCCCCCCCCCC",
        album_match_id=None,
        artist_match_id=None,
        all_tracks=False,
        dry_run=False,
        include_needs_review=False,
        plan_path=plan_path,
    )
    loaded = plan_io.load_plan(plan_path)
    assert any(
        t["yt_video_id"] == "CCCCCCCCCCC" and t["status"] == "transferred"
        for t in plan_io.iter_tracks(loaded)
    )
    assert any(t["status"] == "needs_review" for t in plan_io.iter_tracks(loaded))
    # meta recomputed
    assert loaded["meta"]["transferred"] == 1


def test_transfer_batched_save_and_meta(isolated_data_dir: Path, monkeypatch: Any) -> None:
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
    # batched saves: in-memory updates during the loop, single save at the end
    original_save = plan_io.save_with_meta
    calls: list[int] = []

    def counting_save(plan: dict[str, Any], path: Path) -> None:
        calls.append(1)
        return original_save(plan, path)

    with patch("tidal2ytm.transfer.save_with_meta", side_effect=counting_save):
        counts: Any = transfer_mod.run_transfer(
            yt,
            track_id=None,
            album_match_id="a/b",
            artist_match_id=None,
            all_tracks=False,
            dry_run=False,
            include_needs_review=False,
            plan_path=plan_path,
        )
        assert counts.transferred == 2

    assert len(calls) == 1
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
        [{"tidal_id": 5, "title": "Song", "status": "pending", "yt_video_id": "CCCCCCCCCCC"}],
    )
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    transfer_mod.run_transfer(
        yt,
        track_id="CCCCCCCCCCC",
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


def test_transfer_counts_empty_video_id(isolated_data_dir: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [
            {"tidal_id": 1, "title": "NoVideo", "status": "pending", "yt_video_id": ""},
            {
                "tidal_id": 2,
                "title": "HasVideo",
                "status": "pending",
                "yt_video_id": "AAAAAAAAAAA",
            },
        ],
    )
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    yt.edit_song_library_status.return_value = {"status": "STATUS_SUCCEEDED"}
    counts: Any = transfer_mod.run_transfer(
        yt,
        track_id=None,
        album_match_id=None,
        artist_match_id=None,
        all_tracks=True,
        dry_run=False,
        include_needs_review=False,
        plan_path=plan_path,
    )
    assert counts.total == counts.transferred + counts.failed + counts.skipped
    assert counts.total == 2
    assert counts.transferred == 1
    assert counts.skipped == 1


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
    counts = transfer_mod.run_transfer(
        yt,
        track_id=None,
        album_match_id=None,
        artist_match_id=None,
        all_tracks=True,
        dry_run=False,
        include_needs_review=False,
        plan_path=plan_path,
    )
    assert counts.transferred == 0 and counts.failed == 0
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
    with pytest.raises(InvalidScopeError):
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


@pytest.mark.parametrize("scope", ["track", "all"])
def test_transfer_no_plan_exits(isolated_data_dir: Path, tmp_path: Path, scope: str) -> None:
    # A missing plan raises before any scope work, regardless of scope.
    missing = tmp_path / "missing.toml"
    yt = MagicMock()
    with pytest.raises(PlanNotFoundError):
        transfer_mod.run_transfer(
            yt,
            track_id="AAAAAAAAAAA" if scope == "track" else None,
            album_match_id=None,
            artist_match_id=None,
            all_tracks=scope == "all",
            dry_run=False,
            include_needs_review=False,
            plan_path=missing,
        )


def test_duplicate_video_id_scope_raises(isolated_data_dir: Path) -> None:
    from tidal2ytm.errors import InvalidScopeError

    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [
            {"tidal_id": 1, "title": "First", "status": "pending", "yt_video_id": "AAAAAAAAAAA"},
            {"tidal_id": 2, "title": "Second", "status": "pending", "yt_video_id": "AAAAAAAAAAA"},
        ],
    )
    yt = MagicMock()
    with pytest.raises(InvalidScopeError):
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


def test_failing_save_mid_loop_keeps_partial_state(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [
            {"tidal_id": 1, "title": "S1", "status": "pending", "yt_video_id": "AAAAAAAAAAA"},
            {"tidal_id": 2, "title": "S2", "status": "pending", "yt_video_id": "BBBBBBBBBBB"},
            {"tidal_id": 3, "title": "S3", "status": "pending", "yt_video_id": "CCCCCCCCCCC"},
        ],
    )
    yt = MagicMock()
    # T1 succeeds, T2 fails (triggers the crash-safe save), T3 succeeds in memory
    with patch("tidal2ytm.transfer.add_track_to_library", side_effect=[True, False, True]):
        calls = {"n": 0}
        real_save = plan_io.save_plan

        def flaky_save(plan: dict[str, Any], path: Path) -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            real_save(plan, path)

        monkeypatch.setattr(plan_io, "save_plan", flaky_save)
        with pytest.raises(OSError):
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
    # the first (crash-safe) save persisted: T1 transferred, T2 failed, T3 pending
    loaded = plan_io.load_plan(plan_path)
    statuses = {t["tidal_id"]: t["status"] for t in plan_io.iter_tracks(loaded)}
    assert statuses == {1: "transferred", 2: "failed", 3: "pending"}
    assert calls["n"] == 2
    meta = loaded["meta"]
    assert meta["transferred"] == 1 and meta["failed"] == 1 and meta["pending"] == 1
    assert meta["total_tracks"] == 3


def test_transfer_include_needs_review_aborts_on_eof(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [{"tidal_id": 1, "title": "Low", "status": "needs_review", "yt_video_id": "AAAAAAAAAAA"}],
    )
    yt = MagicMock()

    def _eof(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    counts: Any = transfer_mod.run_transfer(
        yt,
        track_id=None,
        album_match_id=None,
        artist_match_id=None,
        all_tracks=True,
        dry_run=False,
        include_needs_review=True,
        plan_path=plan_path,
    )
    assert counts.transferred == 0
    yt.get_watch_playlist.assert_not_called()
    loaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(loaded))["status"] == "needs_review"


def test_transfer_include_needs_review_declines_on_n(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
    _seed_plan(
        plan_path,
        [{"tidal_id": 1, "title": "Low", "status": "needs_review", "yt_video_id": "AAAAAAAAAAA"}],
    )
    yt = MagicMock()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")  # pyright: ignore[reportUnknownLambdaType]
    counts: Any = transfer_mod.run_transfer(
        yt,
        track_id=None,
        album_match_id=None,
        artist_match_id=None,
        all_tracks=True,
        dry_run=False,
        include_needs_review=True,
        plan_path=plan_path,
    )
    assert counts.transferred == 0
    yt.get_watch_playlist.assert_not_called()
    loaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(loaded))["status"] == "needs_review"
