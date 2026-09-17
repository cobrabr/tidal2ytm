from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import tidal2ytm.auth as auth


def test_run_ytm_auth_skips_if_valid(isolated_data_dir: Path, monkeypatch: Any) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text('{"access_token":"tok"}', encoding="utf-8")
    with (
        patch("tidal2ytm.auth.YTMusic") as mock_ytm_cls,
        patch("tidal2ytm.auth.setup_oauth") as mock_setup,
    ):
        mock_ytm_cls.return_value._token.access_token = "tok"  # noqa: S105
        result = auth.run_ytm_auth(force=False)
        assert result == isolated_data_dir / "ytm_auth.json"
        mock_setup.assert_not_called()
        mock_ytm_cls.assert_called_once()


def test_run_ytm_auth_writes_synthetic_client_secret_when_pasted(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    for f in isolated_data_dir.glob("client_secret_*.json"):
        f.unlink()
    with patch("tidal2ytm.auth.setup_oauth") as mock_setup:
        mock_setup.return_value = None
        with patch("tidal2ytm.auth.YTMusic") as mock_ytm_cls:
            mock_ytm_cls.return_value._token.access_token = "tok"  # noqa: S105
            auth.run_ytm_auth(
                client_id="id123",
                client_secret="sec123",  # noqa: S106
                force=True,
            )
    assert (
        any((isolated_data_dir / f).exists() for f in ["client_secret_id123.json"])
        or len(list(isolated_data_dir.glob("client_secret_*.json"))) >= 1
    )


def test_run_tidal_auth_opens_browser_and_writes_token(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("webbrowser.open", lambda _: True)  # pyright: ignore[reportUnknownLambdaType]
    mock_session = MagicMock()
    mock_session.token_type = "Bearer"  # noqa: S105
    mock_session.access_token = "at"  # noqa: S105
    mock_session.refresh_token = "rt"  # noqa: S105
    mock_session.expiry_time = datetime.datetime(2026, 8, 29)
    mock_future = MagicMock()
    mock_future.result.return_value = None
    mock_session.login_oauth.return_value = (
        MagicMock(verification_uri_complete="example.com/verify"),
        mock_future,
    )
    with patch("tidal2ytm.auth.tidalapi.Session", return_value=mock_session):
        result = auth.run_tidal_auth(force=True)
        assert result == isolated_data_dir / "tidal_token.json"
        assert (isolated_data_dir / "tidal_token.json").exists()


def test_run_tidal_auth_returns_cached_token_when_fresh(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    import webbrowser

    far = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(days=4)
    ).isoformat()
    (isolated_data_dir / "tidal_token.json").write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "fresh-access",
                "refresh_token": "refresh-token",
                "expiry_time": far,
                "user_id": 4242,
                "country_code": "US",
            }
        ),
        encoding="utf-8",
    )

    class _StrictSession:
        def load_oauth_session(self, *args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            raise AssertionError("network login attempted for a fresh token")

    monkeypatch.setattr("tidalapi.Session", _StrictSession)
    opened: list[str] = []

    def _record_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", _record_open)
    result = auth.run_tidal_auth(force=False)
    assert result == isolated_data_dir / "tidal_token.json"
    assert opened == []
    import tidal2ytm.cli as cli

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)  # noqa: F841  # pyright: ignore[reportUnusedVariable]
    with patch.object(sys, "argv", ["tidal2ytm", "auth", "--help"]):
        try:
            cli.main()
        except SystemExit as e:
            assert e.code == 0


def _utc_in(days: float) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(days=days)


def test_token_valid_accepts_only_future_expiry() -> None:
    assert auth.token_valid(_utc_in(4)) is True
    assert auth.token_valid(_utc_in(-1)) is False
    assert auth.token_valid(None) is False


def test_tidal_cache_valid_checks_expiry_offline(isolated_data_dir: Path) -> None:
    token_file = isolated_data_dir / "tidal_token.json"
    assert auth.tidal_cache_valid(token_file) is False  # missing
    token_file.write_text("not valid json", encoding="utf-8")
    assert auth.tidal_cache_valid(token_file) is False  # corrupt
    token_file.write_text("{}", encoding="utf-8")
    assert auth.tidal_cache_valid(token_file) is False  # no expiry
    token_file.write_text(json.dumps({"expiry_time": _utc_in(-1).isoformat()}))
    assert auth.tidal_cache_valid(token_file) is False  # expired
    token_file.write_text(json.dumps({"expiry_time": _utc_in(4).isoformat()}))
    assert auth.tidal_cache_valid(token_file) is True  # fresh


def test_ytm_cache_valid_checks_expiry_offline(isolated_data_dir: Path) -> None:
    import time

    auth_file = isolated_data_dir / "ytm_auth.json"
    assert auth.ytm_cache_valid(auth_file) is False  # missing
    auth_file.write_text("not valid json", encoding="utf-8")
    assert auth.ytm_cache_valid(auth_file) is False  # corrupt
    auth_file.write_text("{}", encoding="utf-8")
    assert auth.ytm_cache_valid(auth_file) is False  # no expiry
    auth_file.write_text(json.dumps({"expires_at": int(time.time()) - 10}))
    assert auth.ytm_cache_valid(auth_file) is False  # expired
    auth_file.write_text(json.dumps({"expires_at": int(time.time()) + 3600}))
    assert auth.ytm_cache_valid(auth_file) is True  # fresh
