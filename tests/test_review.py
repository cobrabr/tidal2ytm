from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import tidal2ytm.review as review_mod
from tidal2ytm.models import TrackStatus


def test_review_confidence_color_and_navigation(tmp_path: Path) -> None:
    # verbatim thresholds from brief: 0.9 -> green, 0.3 -> red
    assert review_mod._confidence_color(0.9) == "green"  # pyright: ignore[reportPrivateUsage]
    assert review_mod._confidence_color(0.3) == "red"  # pyright: ignore[reportPrivateUsage]
    # additional thresholds to lock behaviour
    assert review_mod._confidence_color(1.0) == "blue"  # pyright: ignore[reportPrivateUsage]
    assert review_mod._confidence_color(0.75) == "yellow"  # pyright: ignore[reportPrivateUsage]
    assert review_mod._confidence_color(0.69) == "red"  # pyright: ignore[reportPrivateUsage]
    assert review_mod._confidence_color(0.86) == "green"  # pyright: ignore[reportPrivateUsage]
    # cursors on empty filtered list should not raise and return cursor (0)
    empty_session = review_mod.ReviewSession(
        plan={"artists": []},
        plan_path=tmp_path / "plan.toml",
        backup_done=False,
        cursor=0,
        filtered_tracks=[],
        track_context={},
    )
    assert review_mod._next_album_cursor(empty_session) == 0  # pyright: ignore[reportPrivateUsage]
    assert review_mod._prev_album_cursor(empty_session) == 0  # pyright: ignore[reportPrivateUsage]
    assert review_mod._next_artist_cursor(empty_session) == 0  # pyright: ignore[reportPrivateUsage]
    assert review_mod._prev_artist_cursor(empty_session) == 0  # pyright: ignore[reportPrivateUsage]


def test_review_confidence_color_boundaries() -> None:
    assert review_mod._confidence_color(1.0) == "blue"  # pyright: ignore[reportPrivateUsage]
    assert review_mod._confidence_color(0.85) == "yellow"  # pyright: ignore[reportPrivateUsage]  # >0.85 green, >=0.70 yellow
    assert review_mod._confidence_color(0.851) == "green"  # pyright: ignore[reportPrivateUsage]
    assert review_mod._confidence_color(0.70) == "yellow"  # pyright: ignore[reportPrivateUsage]
    assert review_mod._confidence_color(0.69) == "red"  # pyright: ignore[reportPrivateUsage]


def test_build_track_context_and_navigation(isolated_data_dir: Path) -> None:
    plan = {
        "artists": [
            {
                "name": "Artist A",
                "match_id": "artist-a",
                "albums": [
                    {
                        "name": "Album X",
                        "match_id": "artist-a/album-x",
                        "tracks": [{"tidal_id": 1}, {"tidal_id": 2}],
                    },
                    {
                        "name": "Album Y",
                        "match_id": "artist-a/album-y",
                        "tracks": [{"tidal_id": 3}],
                    },
                ],
            },
            {
                "name": "Artist B",
                "match_id": "artist-b",
                "albums": [
                    {"name": "Album Z", "match_id": "artist-b/album-z", "tracks": [{"tidal_id": 4}]}
                ],
            },
        ]
    }
    filtered = [{"tidal_id": 1}, {"tidal_id": 2}, {"tidal_id": 3}, {"tidal_id": 4}]
    ctx = review_mod._build_track_context(plan, filtered)  # pyright: ignore[reportPrivateUsage]
    # pos_in_album checks
    assert ctx[1]["pos_in_album"] == 1 and ctx[1]["total_in_album"] == 2
    assert ctx[2]["pos_in_album"] == 2 and ctx[2]["total_in_album"] == 2
    assert ctx[3]["pos_in_album"] == 1 and ctx[3]["total_in_album"] == 1
    assert ctx[4]["artist_match_id"] == "artist-b"

    session = review_mod.ReviewSession(
        plan=plan,
        plan_path=isolated_data_dir / "transfer_plan.toml",
        backup_done=False,
        cursor=0,
        filtered_tracks=filtered,
        track_context=ctx,
    )
    # from track 0 (album-x), next album should be index 2 (album-y)
    assert review_mod._next_album_cursor(session) == 2  # pyright: ignore[reportPrivateUsage]
    # next artist from 0 should be index 3 (artist-b)
    assert review_mod._next_artist_cursor(session) == 3  # pyright: ignore[reportPrivateUsage]

    # prev album from index 2 should go to 0
    session.cursor = 2
    assert review_mod._prev_album_cursor(session) == 0  # pyright: ignore[reportPrivateUsage]
    # prev artist from 3 should go to 0
    session.cursor = 3
    assert review_mod._prev_artist_cursor(session) == 0  # pyright: ignore[reportPrivateUsage]

    # cursors at boundaries stay
    session.cursor = 3
    assert review_mod._next_album_cursor(session) == 3  # pyright: ignore[reportPrivateUsage]
    assert review_mod._next_artist_cursor(session) == 3  # pyright: ignore[reportPrivateUsage]
    session.cursor = 0
    assert review_mod._prev_album_cursor(session) == 0  # pyright: ignore[reportPrivateUsage]
    assert review_mod._prev_artist_cursor(session) == 0  # pyright: ignore[reportPrivateUsage]


def test_review_backup_on_first_write(isolated_data_dir: Path) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
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
                                "status": "pending",
                                "yt_video_id": "AAAAAAAAAAA",
                                "confidence": {"overall": 0.5},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    import tidal2ytm.plan_io as plan_io

    plan_io.save_plan(plan, plan_path)
    loaded = plan_io.load_plan(plan_path)
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    ctx = review_mod._build_track_context(loaded, filtered)  # pyright: ignore[reportPrivateUsage]
    session = review_mod.ReviewSession(
        plan=loaded,
        plan_path=plan_path,
        backup_done=False,
        cursor=0,
        filtered_tracks=filtered,
        track_context=ctx,
    )
    track = filtered[0]
    # first decision should trigger backup_plan
    with patch("tidal2ytm.review.backup_plan", wraps=plan_io.backup_plan) as mock_backup:
        review_mod._apply_decision(session, track, TrackStatus.SKIP.value)  # pyright: ignore[reportPrivateUsage]
        mock_backup.assert_called_once_with(plan_path)
        assert session.backup_done is True
        # second decision should NOT trigger backup again
        mock_backup.reset_mock()
        review_mod._apply_decision(session, track, TrackStatus.PENDING.value)  # pyright: ignore[reportPrivateUsage]
        mock_backup.assert_not_called()
    # ensure file reflects last status
    reloaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(reloaded))["status"] == "pending"


def test_review_apply_decision_updates_plan_and_meta(isolated_data_dir: Path) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
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
                                "tidal_id": 42,
                                "title": "Song",
                                "status": "needs_review",
                                "yt_video_id": "AAAAAAAAAAA",
                                "confidence": {"overall": 0.2},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    import tidal2ytm.plan_io as plan_io

    plan_io.save_plan(plan, plan_path)
    loaded = plan_io.load_plan(plan_path)
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    ctx = review_mod._build_track_context(loaded, filtered)  # pyright: ignore[reportPrivateUsage]
    session = review_mod.ReviewSession(
        plan=loaded,
        plan_path=plan_path,
        backup_done=True,
        cursor=0,
        filtered_tracks=filtered,
        track_context=ctx,
    )
    track = filtered[0]
    review_mod._apply_decision(  # pyright: ignore[reportPrivateUsage]
        session, track, TrackStatus.PENDING.value, {"yt_video_id": "BBBBBBBBBBB"}
    )
    assert track["status"] == "pending"
    assert track["yt_video_id"] == "BBBBBBBBBBB"
    reloaded = plan_io.load_plan(plan_path)
    rt = next(plan_io.iter_tracks(reloaded))
    assert rt["status"] == "pending"
    assert rt["yt_video_id"] == "BBBBBBBBBBB"
    assert reloaded["meta"]["pending"] == 1


def test_review_do_override_parses_url(isolated_data_dir: Path, monkeypatch: Any) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
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
                                "tidal_id": 99,
                                "title": "Song",
                                "status": "needs_review",
                                "yt_video_id": "",
                                "confidence": {"overall": 0.1},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    import tidal2ytm.plan_io as plan_io

    plan_io.save_plan(plan, plan_path)
    loaded = plan_io.load_plan(plan_path)
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    ctx = review_mod._build_track_context(loaded, filtered)  # pyright: ignore[reportPrivateUsage]
    session = review_mod.ReviewSession(
        plan=loaded,
        plan_path=plan_path,
        backup_done=True,
        cursor=0,
        filtered_tracks=filtered,
        track_context=ctx,
    )
    track = filtered[0]
    monkeypatch.setattr("builtins.input", lambda _: "https://youtu.be/dQw4w9WgXcQ")  # pyright: ignore[reportUnknownLambdaType]
    from rich.console import Console

    console = Console()
    review_mod._do_override(console, session, track)  # pyright: ignore[reportPrivateUsage]
    assert track["yt_video_id"] == "dQw4w9WgXcQ"
    assert track["status"] == "pending"


def test_review_do_override_rejects_invalid_then_accepts(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
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
                                "tidal_id": 100,
                                "title": "Song",
                                "status": "needs_review",
                                "yt_video_id": "",
                                "confidence": {"overall": 0.1},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    import tidal2ytm.plan_io as plan_io

    plan_io.save_plan(plan, plan_path)
    loaded = plan_io.load_plan(plan_path)
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    ctx = review_mod._build_track_context(loaded, filtered)  # pyright: ignore[reportPrivateUsage]
    session = review_mod.ReviewSession(
        plan=loaded,
        plan_path=plan_path,
        backup_done=True,
        cursor=0,
        filtered_tracks=filtered,
        track_context=ctx,
    )
    track = filtered[0]
    inputs = iter(["not-a-url", "dQw4w9WgXcQ"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))  # pyright: ignore[reportUnknownLambdaType]
    from rich.console import Console

    console = Console()
    review_mod._do_override(console, session, track)  # pyright: ignore[reportPrivateUsage]
    assert track["yt_video_id"] == "dQw4w9WgXcQ"


def test_review_run_no_plan_exits(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(SystemExit) as e:
        review_mod.run_review(plan_path=missing)
    assert e.value.code == 1


def test_review_run_no_tracks_match_returns(capsys: Any, isolated_data_dir: Path) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
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
                                "status": "pending",
                                "yt_video_id": "AAAAAAAAAAA",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    import tidal2ytm.plan_io as plan_io

    plan_io.save_plan(plan, plan_path)
    # filter for status that does not exist
    review_mod.run_review(status_filter=TrackStatus.NEEDS_REVIEW, plan_path=plan_path)
    out = capsys.readouterr().out
    assert "No tracks" in out


def test_review_navigation_cursors_realistic(isolated_data_dir: Path) -> None:
    # ensure cursors work when multiple albums/artists
    plan = {
        "artists": [
            {
                "name": "AA",
                "match_id": "aa",
                "albums": [
                    {
                        "name": "B1",
                        "match_id": "aa/b1",
                        "tracks": [{"tidal_id": 1}, {"tidal_id": 2}],
                    }
                ],
            },
            {
                "name": "BB",
                "match_id": "bb",
                "albums": [{"name": "B2", "match_id": "bb/b2", "tracks": [{"tidal_id": 3}]}],
            },
        ]
    }
    filtered = [{"tidal_id": 1}, {"tidal_id": 2}, {"tidal_id": 3}]
    ctx = review_mod._build_track_context(plan, filtered)  # pyright: ignore[reportPrivateUsage]
    session = review_mod.ReviewSession(
        plan=plan,
        plan_path=isolated_data_dir / "transfer_plan.toml",
        backup_done=False,
        cursor=1,
        filtered_tracks=filtered,
        track_context=ctx,
    )
    # cursor 1 is still album aa/b1, next album should be 2
    assert review_mod._next_album_cursor(session) == 2  # pyright: ignore[reportPrivateUsage]
    # prev album from 2 should be 0
    session.cursor = 2
    assert review_mod._prev_album_cursor(session) == 0  # pyright: ignore[reportPrivateUsage]
