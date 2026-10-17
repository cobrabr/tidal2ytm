from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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
    secret_path = isolated_data_dir / "client_secret_pasted.json"
    assert secret_path.exists()
    data = json.loads(secret_path.read_text(encoding="utf-8"))
    assert data["installed"]["client_id"] == "id123"
    assert data["installed"]["client_secret"] == "sec123"  # noqa: S105


def test_run_tidal_auth_opens_browser_and_writes_token(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("webbrowser.open", lambda _: True)  # pyright: ignore[reportUnknownLambdaType]
    mock_session = MagicMock()
    mock_session.token_type = "Bearer"  # noqa: S105
    mock_session.access_token = "at"  # noqa: S105
    mock_session.refresh_token = "rt"  # noqa: S105
    mock_session.expiry_time = datetime.datetime(2026, 8, 29)
    mock_session.country_code = "US"
    mock_session.user.id = 4242
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

    far = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=4)).isoformat()
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


def _utc_in(days: float) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=days)


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


def test_token_usable_margin() -> None:
    now = datetime.datetime.now(datetime.UTC)
    assert auth.token_usable(now + datetime.timedelta(minutes=31)) is True
    assert (
        auth.token_usable(
            now + datetime.timedelta(minutes=29),
            margin=datetime.timedelta(minutes=30),
        )
        is False
    )
    assert auth.token_usable(now - datetime.timedelta(seconds=1)) is False
    assert auth.token_usable(None) is False


def test_client_secret_sorted_and_strict(isolated_data_dir: Path) -> None:
    for f in isolated_data_dir.glob("client_secret_*.json"):
        f.unlink()
    with pytest.raises(FileNotFoundError):
        auth.read_client_secret(isolated_data_dir)
    (isolated_data_dir / "client_secret_b.json").write_text(
        json.dumps({"installed": {"client_id": "b-id", "client_secret": "b-sec"}}),
        encoding="utf-8",
    )
    (isolated_data_dir / "client_secret_a.json").write_text(
        json.dumps({"installed": {"client_id": "a-id", "client_secret": "a-sec"}}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError):
        auth.read_client_secret(isolated_data_dir)
    (isolated_data_dir / "client_secret_b.json").unlink()
    cid, csec = auth.read_client_secret(isolated_data_dir)
    assert (cid, csec) == ("a-id", "a-sec")


# `require_client_secret` exists for the caller that is not a terminal: the
# sidecar answering `auth.begin`, which catches one exception type, and it has to
# be an ordinary one. Every way the credential can be unusable therefore arrives
# as `ValueError`, carrying text that says which way it was — a caller showing
# that text is the only thing standing between a person and a dead end.
def test_every_unusable_client_secret_arrives_as_one_value_error(
    isolated_data_dir: Path,
) -> None:
    for f in isolated_data_dir.glob("client_secret_*.json"):
        f.unlink()
    with pytest.raises(ValueError, match="client_secret"):
        auth.require_client_secret(isolated_data_dir)

    (isolated_data_dir / "client_secret_a.json").write_text(
        json.dumps({"installed": {"client_id": "a-id", "client_secret": "a-sec"}}),
        encoding="utf-8",
    )
    assert auth.require_client_secret(isolated_data_dir) == ("a-id", "a-sec")

    (isolated_data_dir / "client_secret_b.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Multiple"):
        auth.require_client_secret(isolated_data_dir)
    (isolated_data_dir / "client_secret_b.json").unlink()

    (isolated_data_dir / "client_secret_a.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Could not parse"):
        auth.require_client_secret(isolated_data_dir)


# The terminal's half of the same check is unchanged by any of that: a message
# naming what is missing, and an exit code. `run_ytm_auth` is the interactive
# entry point, so this is where exiting is the contract rather than a library
# raising at its caller.
def test_ytm_auth_without_a_client_secret_exits_with_the_hint(
    isolated_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for f in isolated_data_dir.glob("client_secret_*.json"):
        f.unlink()

    with pytest.raises(SystemExit) as exit_info:
        auth.run_ytm_auth()

    assert exit_info.value.code == 1
    assert "client_secret" in capsys.readouterr().err


# The other half, and the half anything but a keyboard has to call: the flow
# raises. `SystemExit` out of it would end a request thread in the sidecar with
# its request unanswered, and the host's invoke, which waits for this without a
# timeout, would never settle. Pinned here as well as end to end in
# `sidecar/tests/test_runner.py`, because this is the shape the two functions
# have and nothing else asserts it.
def test_the_flow_raises_where_the_cli_entry_point_exits(isolated_data_dir: Path) -> None:
    for f in isolated_data_dir.glob("client_secret_*.json"):
        f.unlink()

    with pytest.raises(auth.YtmAuthError, match="client_secret"):
        auth.sign_in_ytm()


def test_tidal_auth_writer_emits_full_schema(isolated_data_dir: Path, monkeypatch: Any) -> None:
    import webbrowser
    from types import SimpleNamespace

    expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)

    class _FakeSession:
        def __init__(self) -> None:
            self.token_type = "Bearer"  # noqa: S105
            self.access_token = "fresh-access"  # noqa: S105
            self.refresh_token = "fresh-refresh"  # noqa: S105
            self.expiry_time = expiry
            self.country_code = "US"
            self.user: Any = SimpleNamespace(id=4242)

        def login_oauth(self) -> Any:
            link = SimpleNamespace(verification_uri_complete="example.com/verify")
            future = SimpleNamespace(result=lambda: None)
            return link, future

    monkeypatch.setattr("tidalapi.Session", _FakeSession)

    def _noop_open(url: str) -> bool:
        del url
        return True

    monkeypatch.setattr(webbrowser, "open", _noop_open)
    result = auth.run_tidal_auth(force=True)
    data = json.loads(result.read_text(encoding="utf-8"))
    assert isinstance(data["user_id"], int)
    assert isinstance(data["country_code"], str)
    assert isinstance(data["access_token"], str)
    assert isinstance(data["refresh_token"], str)
    assert isinstance(data["expiry_time"], str)


def test_corrupt_token_file_falls_back_to_login_not_crash(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    import webbrowser

    token_file = isolated_data_dir / "tidal_token.json"
    token_file.write_text("not valid json {{{", encoding="utf-8")

    opened: list[str] = []

    def _record_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", _record_open)

    class _LoginSession:
        def __init__(self) -> None:
            from types import SimpleNamespace

            self.token_type = "Bearer"  # noqa: S105
            self.access_token = "new-access"  # noqa: S105
            self.refresh_token = "new-refresh"  # noqa: S105
            self.expiry_time = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)
            self.country_code = "US"
            self.user: Any = SimpleNamespace(id=4242)

        def load_oauth_session(self, *args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            raise AssertionError("corrupt cache must not attempt hydrate")

        def check_login(self) -> bool:
            raise AssertionError("corrupt cache must not check login")

        def login_oauth(self) -> Any:
            from types import SimpleNamespace

            link = SimpleNamespace(verification_uri_complete="example.com/verify")
            future = SimpleNamespace(result=lambda: None)
            return link, future

    monkeypatch.setattr("tidalapi.Session", _LoginSession)
    result = auth.run_tidal_auth(force=False)
    assert opened != []
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["access_token"] == "new-access"  # noqa: S105


def test_ytm_cache_valid_refreshable_counts_as_valid(isolated_data_dir: Path) -> None:
    import time

    auth_file = isolated_data_dir / "ytm_auth.json"
    auth_file.write_text(
        json.dumps({"expires_at": int(time.time()) - 10, "refresh_token": "rt"}),
        encoding="utf-8",
    )
    assert auth.ytm_cache_valid(auth_file) is True


# Tidal's refresh token outlives its access token by months, exactly as
# YouTube Music's does, so counting one makes the cached session usable for the
# length of the login rather than the length of the access token. This is what
# `tidal2ytm.planning.read_auth_presence` asks before offering to sign in.
def test_tidal_cache_valid_refreshable_counts_as_valid(isolated_data_dir: Path) -> None:
    token_file = isolated_data_dir / "tidal_token.json"
    token_file.write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "a",
                "refresh_token": "r",
                "expiry_time": _utc_in(-1).isoformat(),
                "user_id": 1,
                "country_code": "US",
            }
        ),
        encoding="utf-8",
    )
    assert auth.tidal_cache_valid(token_file) is True
