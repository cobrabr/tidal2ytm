from __future__ import annotations

import argparse
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
    mock_session.expiry_time.isoformat.return_value = "2026-08-29T00:00:00"
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


def test_auth_cli_flags_registered(monkeypatch: Any) -> None:
    import tidal2ytm.cli as cli

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)  # noqa: F841  # pyright: ignore[reportUnusedVariable]
    with patch.object(sys, "argv", ["tidal2ytm", "auth", "--help"]):
        try:
            cli.main()
        except SystemExit as e:
            assert e.code == 0
