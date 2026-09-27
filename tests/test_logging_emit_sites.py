from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _capture_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, level: str = "DEBUG"
) -> Path | None:
    """Isolate logging config: point paths at tmp, reset handlers, set level."""
    from tidal2ytm import logging_setup
    from tidal2ytm import paths as paths_mod

    monkeypatch.setattr(paths_mod, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(logging_setup, "_run_file_path", None)
    monkeypatch.setattr(logging_setup, "_handler", None)
    root = logging.getLogger()
    # Remove only our run-file handler; caplog attaches its own handler to
    # root and must survive, or every caplog assertion would see zero records.
    for h in root.handlers[:]:
        if getattr(h, "_tidal2ytm_run", False):
            root.removeHandler(h)
    return logging_setup.setup_logging(level)


def test_patched_post_logs_endpoint_and_redacts_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tidal2ytm.ytm_client import _patched_post  # pyright: ignore[reportPrivateUsage]

    _capture_setup(tmp_path, monkeypatch, "DEBUG")

    yt = MagicMock()
    yt.context = {"context": {"client": {"clientName": "TVHTML5"}}}
    captured: dict[str, Any] = {}

    def original_post(url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["payload"] = kwargs.get("json")
        return {"trackingParams": "x"}

    patched = _patched_post(yt, original_post)
    with caplog.at_level(logging.DEBUG, logger="tidal2ytm.ytm_client"):
        patched(
            "https://music.youtube.com/youtubei/v1/search?prettyPrint=false",
            json={"context": {"client": {}}, "query": "apple", "access_token": "sekrit"},
        )
    messages = [r.getMessage() for r in caplog.records if r.name == "tidal2ytm.ytm_client"]
    assert any("POST /youtubei/v1/search" in m and "ok" in m for m in messages)
    assert not any("sekrit" in m for m in messages)


def test_patched_post_connection_failure_is_critical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import requests

    from tidal2ytm.ytm_client import _patched_post  # pyright: ignore[reportPrivateUsage]

    _capture_setup(tmp_path, monkeypatch, "INFO")
    yt = MagicMock()
    yt.context = {"context": {"client": {"clientName": "TVHTML5"}}}

    def original_post(url: str, *args: Any, **kwargs: Any) -> Any:
        raise requests.ConnectionError("down")

    patched = _patched_post(yt, original_post)
    with (
        caplog.at_level(logging.CRITICAL, logger="tidal2ytm.ytm_client"),
        pytest.raises(requests.ConnectionError),
    ):
        patched("https://music.youtube.com/youtubei/v1/player?a=1")
    assert any(
        r.levelno == logging.CRITICAL and "connection-level" in r.getMessage()
        for r in caplog.records
        if r.name == "tidal2ytm.ytm_client"
    )


def test_get_favorite_tracks_logs_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tidal2ytm.tidal_source import get_favorite_tracks

    _capture_setup(tmp_path, monkeypatch, "INFO")
    session = MagicMock()
    page_track = MagicMock()
    page_track.id = 7
    page_track.name = "Apple"
    page_track.duration = 200
    page_track.isrc = None
    page_track.track_num = 1
    page_track.volume_num = 1
    page_track.version = None
    page_track.artists = []
    page_track.artist = None
    page_track.album = None
    session.user.favorites.tracks.side_effect = [[page_track], []]
    with caplog.at_level(logging.INFO, logger="tidal2ytm.tidal_source"):
        tracks = get_favorite_tracks(session)
    assert len(tracks) == 1
    assert any("favorite tracks" in r.getMessage().lower() for r in caplog.records)


def test_resolve_album_group_logs_verdict_and_abstain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tidal2ytm import album_matching as am
    from tidal2ytm.models import SourceTrack

    _capture_setup(tmp_path, monkeypatch, "DEBUG")
    src = SourceTrack(
        tidal_id=1,
        title="Apple",
        artist="Wren",
        artists=["Wren"],
        album="Apple",
        album_id=11,
        album_year=1971,
        duration_sec=200,
        isrc=None,
        track_num=1,
        disc_num=1,
        version=None,
    )
    yt = MagicMock()
    yt.search.return_value = [
        {"browseId": "B1", "title": "Apple", "artist": "Wren", "year": "1971"}
    ]
    yt.get_album.return_value = {
        "title": "Apple",
        "tracks": [
            {
                "title": "Apple",
                "artists": [{"name": "Wren"}],
                "trackNumber": 1,
                "duration_seconds": 200,
                "videoId": "AAAAAAAAAAA",
            }
        ],
    }
    with caplog.at_level(logging.DEBUG, logger="tidal2ytm.album_matching"):
        results = am.resolve_album_group([src], yt, {}, delay=0.0)
    assert results and results[0].yt_video_id == "AAAAAAAAAAA"
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        r.levelno == logging.INFO and "resolved" in r.getMessage().lower() for r in caplog.records
    )
    assert any("candidate" in m.lower() for m in messages)  # DEBUG scoring detail


def test_resolve_album_group_abstains_at_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tidal2ytm import album_matching as am
    from tidal2ytm.models import SourceTrack

    _capture_setup(tmp_path, monkeypatch, "INFO")
    src = SourceTrack(
        tidal_id=1,
        title="Apple",
        artist="Wren",
        artists=["Wren"],
        album="Apple",
        album_id=11,
        album_year=1971,
        duration_sec=200,
        isrc=None,
        track_num=1,
        disc_num=1,
        version=None,
    )
    with patch.object(am, "ALBUM_SEARCH_LIMIT", 10):
        yt = MagicMock()
        yt.search.return_value = []
        with caplog.at_level(logging.INFO, logger="tidal2ytm.album_matching"):
            results = am.resolve_album_group([src], yt, {}, delay=0.0)
    assert results[0].yt_video_id is None
    assert any(
        r.levelno == logging.WARNING and "abstained" in r.getMessage().lower()
        for r in caplog.records
    )


def test_match_track_logs_search_and_candidate_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tidal2ytm import matcher
    from tidal2ytm.models import SourceTrack

    _capture_setup(tmp_path, monkeypatch, "DEBUG")
    src = SourceTrack(
        tidal_id=2,
        title="Apple",
        artist="Wren",
        artists=["Wren"],
        album="Grove",
        album_id=12,
        album_year=1971,
        duration_sec=200,
        isrc=None,
        track_num=1,
        disc_num=1,
        version=None,
    )
    yt = MagicMock()
    yt.search.return_value = [
        {
            "videoId": "BBBBBBBBBBB",
            "title": "Apple",
            "artists": [{"name": "Wren"}],
            "album": {"name": "Grove"},
            "duration_seconds": 200,
        }
    ]
    with caplog.at_level(logging.DEBUG, logger="tidal2ytm.matcher"):
        result = matcher.match_track(src, yt)
    assert result.yt_video_id == "BBBBBBBBBBB"
    assert any("track search" in r.getMessage().lower() for r in caplog.records)


def test_planning_fallback_routing_logged_at_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tests.test_planning import _src  # pyright: ignore[reportPrivateUsage]
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.models import (
        ConfidenceBreakdown,
        MatchMethod,
        MatchResult,
        TrackStatus,
    )
    from tidal2ytm.planning import PlanningSession

    _capture_setup(tmp_path, monkeypatch, "INFO")
    src = _src(41, "Ember Fall", "Vesper Vale", "Ashen Light", 77, year=2001, track=1, duration=200)
    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[src], selection={src.tidal_id: src}
    )

    def fake_resolve(
        sources: list[Any], yt: Any, cache: dict[str, Any], delay: float = 0.3
    ) -> list[Any]:
        return [
            MatchResult(
                source=s,
                yt_video_id=None,
                yt_title=None,
                yt_artist=None,
                yt_album=None,
                yt_album_track_num=None,
                yt_isrc=None,
                yt_duration_sec=None,
                match_method=MatchMethod.NONE,
                confidence=ConfidenceBreakdown(overall=0.0, summary="No album resolve"),
                status=TrackStatus.NEEDS_REVIEW,
                review_reason="No album resolve",
            )
            for s in sources
        ]

    fallback = MatchResult(
        source=src,
        yt_video_id="AAAAAAAAAAA",
        yt_title="Ember Fall",
        yt_artist="Vesper Vale",
        yt_album="Ashen Light",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.95, summary="title=0.99, artist=1.00"),
        status=TrackStatus.PENDING,
    )
    with (
        patch.object(planning_mod, "resolve_album_group", side_effect=fake_resolve),
        patch.object(planning_mod, "match_track", return_value=fallback),
        caplog.at_level(logging.INFO, logger="tidal2ytm.planning"),
    ):
        planning_mod.run_match_action(session, MagicMock(), input_fn=lambda _: "Y")
    assert any(
        r.levelno == logging.WARN and "fallback" in r.getMessage().lower()
        for r in caplog.records
        if r.name == "tidal2ytm.planning"
    )


def test_planio_ytmsink_emit_sites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tidal2ytm import plan_io, ytm_sink

    _capture_setup(tmp_path, monkeypatch, "DEBUG")

    plan: dict[str, Any] = {"meta": {}, "artists": []}
    with caplog.at_level(logging.DEBUG, logger="tidal2ytm.plan_io"):
        plan_io.save_plan(plan, tmp_path / "transfer_plan.toml")
    assert any(r.name == "tidal2ytm.plan_io" and r.levelno == logging.DEBUG for r in caplog.records)

    yt = MagicMock()

    def fake_get_watch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"feedbackTokens": {"add": "tok"}}

    def fake_edit(*args: Any, **kwargs: Any) -> bool:
        return True

    yt.get_watch_playlist.side_effect = fake_get_watch
    yt.edit_song_library_status.side_effect = fake_edit
    with caplog.at_level(logging.INFO, logger="tidal2ytm.ytm_sink"):
        ok = ytm_sink.add_track_to_library(yt, "AAAAAAAAAAA", delay=0.0)
    assert ok is True
    assert any(
        r.name == "tidal2ytm.ytm_sink" and "library add ok" in r.getMessage().lower()
        for r in caplog.records
    )


def test_patched_post_logs_webremix_context_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tidal2ytm.ytm_client import _patched_post  # pyright: ignore[reportPrivateUsage]

    _capture_setup(tmp_path, monkeypatch, "DEBUG")

    yt = MagicMock()
    yt.context = {"context": {"client": {"clientName": "TVHTML5"}}}

    def original_post(url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"trackingParams": "x"}

    patched = _patched_post(yt, original_post)
    with caplog.at_level(logging.DEBUG, logger="tidal2ytm.ytm_client"):
        patched("https://music.youtube.com/youtubei/v1/search?prettyPrint=false")
        patched("https://music.youtube.com/youtubei/v1/next?prettyPrint=false")
    messages = [r.getMessage() for r in caplog.records if r.name == "tidal2ytm.ytm_client"]
    assert any("POST /youtubei/v1/search" in m and "[WEB_REMIX]" in m for m in messages)
    assert not any("/youtubei/v1/next" in m and "[WEB_REMIX]" in m for m in messages)
