from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from tidal2ytm.ytm_sink import add_track_to_library


class FakeYTM:
    """Typed fake for get_watch_playlist / edit_song_library_status."""

    def __init__(self, watch: dict[str, Any], edit_result: dict[str, Any] | None = None) -> None:
        self._watch = watch
        self._edit_result = (
            edit_result if edit_result is not None else {"status": "STATUS_SUCCEEDED"}
        )
        self.edit_calls: list[list[str]] = []

    def get_watch_playlist(self, **kwargs: Any) -> dict[str, Any]:
        return self._watch

    def edit_song_library_status(self, tokens: list[str]) -> dict[str, Any]:
        self.edit_calls.append(tokens)
        return self._edit_result


def test_sink_already_in_library_returns_true() -> None:
    yt = FakeYTM({"tracks": [], "feedbackTokens": {}})
    assert add_track_to_library(yt, "AAAAAAAAAAA", delay=0) is True  # type: ignore[arg-type]


def test_sink_failed_status_returns_false() -> None:
    yt = FakeYTM({"tracks": [{"feedbackTokens": {"add": "token123"}}]}, {"status": "FAILED"})
    assert add_track_to_library(yt, "AAAAAAAAAAA", "Ember Fall", delay=0) is False  # type: ignore[arg-type]


def test_ytm_sink_dry_run_does_not_call_api(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    assert add_track_to_library(yt, "CCCCCCCCCCC", "Muddle", dry_run=True) is True
    yt.get_watch_playlist.assert_not_called()


def test_ytm_sink_no_video_id_returns_false(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    assert add_track_to_library(yt, "", "Muddle", dry_run=False) is False


def test_ytm_sink_no_add_token_means_already_in_library(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {}}]}
    assert add_track_to_library(yt, "CCCCCCCCCCC", "Muddle", dry_run=False) is True


def test_ytm_sink_success_calls_edit(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "token123"}}]}
    yt.edit_song_library_status.return_value = {"status": "STATUS_SUCCEEDED"}
    assert add_track_to_library(yt, "CCCCCCCCCCC", "Muddle", dry_run=False) is True
    yt.edit_song_library_status.assert_called_once_with(["token123"])


def test_ytm_sink_exception_returns_false(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    yt.get_watch_playlist.side_effect = Exception("boom")
    assert add_track_to_library(yt, "CCCCCCCCCCC", "Muddle", dry_run=False) is False


def test_ytm_sink_empty_tracks_means_already_in_library(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": []}
    assert add_track_to_library(yt, "CCCCCCCCCCC", "Muddle", dry_run=False) is True


def test_ytm_sink_malformed_watch_payload_returns_false(monkeypatch: Any) -> None:
    monkeypatch.setattr("tidal2ytm.ytm_sink.time.sleep", lambda _: None)  # pyright: ignore[reportUnknownLambdaType]
    yt = MagicMock()
    yt.get_watch_playlist.return_value = None
    # malformed shape must degrade to False, never KeyError/AttributeError
    assert add_track_to_library(yt, "CCCCCCCCCCC", "Muddle", dry_run=False) is False
