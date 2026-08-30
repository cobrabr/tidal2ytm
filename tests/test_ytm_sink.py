from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from tidal2ytm.ytm_sink import add_track_to_library


def test_ytm_sink_dry_run_does_not_call_api(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    assert add_track_to_library(yt, "dQw4w9WgXcQ", "Bungle", dry_run=True) is True
    yt.get_watch_playlist.assert_not_called()


def test_ytm_sink_no_video_id_returns_false(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    assert add_track_to_library(yt, "", "Bungle", dry_run=False) is False


def test_ytm_sink_no_add_token_returns_false(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {}}]}
    assert add_track_to_library(yt, "dQw4w9WgXcQ", "Bungle", dry_run=False) is False


def test_ytm_sink_success_calls_edit(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "token123"}}]}
    yt.edit_song_library_status.return_value = {"status": "STATUS_SUCCEEDED"}
    assert add_track_to_library(yt, "dQw4w9WgXcQ", "Bungle", dry_run=False) is True
    yt.edit_song_library_status.assert_called_once_with(["token123"])


def test_ytm_sink_exception_returns_false(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    yt.get_watch_playlist.side_effect = Exception("boom")
    assert add_track_to_library(yt, "dQw4w9WgXcQ", "Bungle", dry_run=False) is False


def test_ytm_sink_no_tracks_returns_false(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": []}
    assert add_track_to_library(yt, "dQw4w9WgXcQ", "Bungle", dry_run=False) is False
