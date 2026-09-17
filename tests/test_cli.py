from __future__ import annotations

import contextlib
import datetime
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import tidal2ytm.cli as cli_mod
import tidal2ytm.paths as paths


def test_cli_help_and_status_offline(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    # status without plan file should not require network (offline)
    missing = tmp_path / "nonexistent.toml"
    monkeypatch.setattr(paths, "PLAN_FILE", missing)
    monkeypatch.setattr(cli_mod, "PLAN_FILE", missing)
    cli_mod.cmd_status(MagicMock(artist=None, album=None))
    out = capsys.readouterr().out
    assert "No transfer plan" in out or "Transfer plan" in out


def test_cli_main_parses_help(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "--help"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 0


def test_cli_main_planning_abort_exits_cleanly(monkeypatch: Any, capsys: Any) -> None:
    import tidal2ytm.planning as planning_mod

    monkeypatch.setattr(sys, "argv", ["tidal2ytm"])

    def _no_login() -> MagicMock:
        raise AssertionError("startup must not log in")

    monkeypatch.setattr(cli_mod, "_tidal_login", _no_login)

    def _abort(**kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(planning_mod, "run_planning", _abort)
    cli_mod.main()
    assert "ended" in capsys.readouterr().out


def test_cli_bare_skips_startup_login(monkeypatch: Any, capsys: Any) -> None:
    import tidal2ytm.planning as planning_mod

    monkeypatch.setattr(sys, "argv", ["tidal2ytm"])

    def _no_login() -> MagicMock:
        raise AssertionError("startup must not log in")

    monkeypatch.setattr(cli_mod, "_tidal_login", _no_login)

    def _noop(**kwargs: Any) -> None:
        del kwargs

    monkeypatch.setattr(planning_mod, "run_planning", _noop)
    cli_mod.main()
    assert "Connecting to Tidal" not in capsys.readouterr().out


def test_wait_status_prints_plain_text_without_tty(capsys: Any) -> None:
    ran = False
    with cli_mod.wait_status("Reticulating splines"):
        ran = True
    assert ran
    assert "Reticulating splines…" in capsys.readouterr().out


def test_wait_status_renders_message_on_tty(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    ran = False
    with cli_mod.wait_status("Reticulating splines"):
        ran = True
    assert ran
    assert "Reticulating splines" in capsys.readouterr().out


def test_wait_status_uses_yellow_dots_spinner(monkeypatch: Any) -> None:
    import rich.console

    seen: dict[str, Any] = {}

    class _FakeStatus:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: Any) -> bool:
            del args
            return False

    class _FakeConsole:
        def status(
            self, text: object, *, spinner: object = None, spinner_style: object = None
        ) -> _FakeStatus:
            seen.update(text=text, spinner=spinner, spinner_style=spinner_style)
            return _FakeStatus()

    monkeypatch.setattr(rich.console, "Console", _FakeConsole)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    with cli_mod.wait_status("Reticulating splines"):
        pass
    assert seen == {
        "text": "Reticulating splines…",
        "spinner": "dots",
        "spinner_style": "yellow",
    }


def test_tidal_login_persists_refreshed_token(tmp_path: Path, monkeypatch: Any) -> None:
    import datetime
    from types import SimpleNamespace

    token_file = tmp_path / "tidal_token.json"
    token_file.write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "stale-access",
                "refresh_token": "refresh-token",
                "expiry_time": "2026-09-06T00:08:46.324213",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_mod, "TIDAL_TOKEN_FILE", token_file)

    fresh_expiry = datetime.datetime(2026, 9, 20, tzinfo=datetime.UTC)

    class _RefreshingSession:
        def __init__(self) -> None:
            self.token_type = "Bearer"  # noqa: S105
            self.access_token = "stale-access"  # noqa: S105
            self.refresh_token = "refresh-token"  # noqa: S105
            self.expiry_time: Any = None
            self.country_code: Any = None
            self.user: Any = SimpleNamespace(id=4242)

        def load_oauth_session(
            self,
            token_type: Any,
            access_token: Any,
            refresh_token: Any,
            expiry_time: Any = None,
        ) -> bool:
            del token_type, access_token, refresh_token, expiry_time
            # simulate tidalapi refreshing the expired token in memory
            self.access_token = "fresh-access"  # noqa: S105
            self.expiry_time = fresh_expiry
            return True

        def check_login(self) -> bool:
            return True

    monkeypatch.setattr("tidalapi.Session", _RefreshingSession)
    cli_mod._tidal_login()  # pyright: ignore[reportPrivateUsage]
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["access_token"] == "fresh-access"  # noqa: S105
    assert saved["expiry_time"] == fresh_expiry.isoformat()
    assert saved["refresh_token"] == "refresh-token"  # noqa: S105


def test_tidal_login_skips_network_when_token_fresh(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    import webbrowser

    import tidalapi.user as tidal_user

    far = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(days=4)
    ).isoformat()
    original = {
        "token_type": "Bearer",
        "access_token": "fresh-access",
        "refresh_token": "refresh-token",
        "expiry_time": far,
        "user_id": 4242,
        "country_code": "US",
    }
    token_file = tmp_path / "tidal_token.json"
    token_file.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(cli_mod, "TIDAL_TOKEN_FILE", token_file)
    monkeypatch.setattr(cli_mod, "DATA_DIR", tmp_path)

    class _StrictSession:
        def load_oauth_session(self, *args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            raise AssertionError("network login attempted for a fresh token")

        def check_login(self) -> bool:
            raise AssertionError("network check attempted for a fresh token")

    made: dict[str, Any] = {}

    class _StubUser:
        def __init__(self, session: Any, user_id: Any) -> None:
            made.update(session=session, user_id=user_id)
            self.id = user_id

    monkeypatch.setattr("tidalapi.Session", _StrictSession)
    monkeypatch.setattr(tidal_user, "LoggedInUser", _StubUser)
    opened: list[str] = []

    def _record_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", _record_open)
    sess = cli_mod._tidal_login()  # pyright: ignore[reportPrivateUsage]
    assert "expired" not in capsys.readouterr().out
    assert opened == []
    assert sess.access_token == "fresh-access"  # noqa: S105  # pyright: ignore[reportUnknownMemberType]
    assert made["user_id"] == 4242
    assert made["session"] is sess
    assert sess.country_code == "US"  # pyright: ignore[reportUnknownMemberType]
    assert json.loads(token_file.read_text(encoding="utf-8")) == original


def test_tidal_login_takes_full_path_when_token_near_expiry(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    import webbrowser
    from types import SimpleNamespace

    near = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(minutes=10)
    ).isoformat()
    token_file = tmp_path / "tidal_token.json"
    token_file.write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "valid-access",
                "refresh_token": "refresh-token",
                "expiry_time": near,
                "user_id": 4242,
                "country_code": "US",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_mod, "TIDAL_TOKEN_FILE", token_file)
    monkeypatch.setattr(cli_mod, "DATA_DIR", tmp_path)

    calls: list[str] = []

    class _CachedSession:
        """Mirrors tidalapi: stores expiry verbatim (a str), no refresh."""

        def __init__(self) -> None:
            self.token_type = "Bearer"  # noqa: S105
            self.access_token = "stale"  # noqa: S105
            self.refresh_token = "stale"  # noqa: S105
            self.expiry_time: Any = None
            self.country_code: Any = None
            self.user: Any = SimpleNamespace(id=4242)

        def load_oauth_session(
            self,
            token_type: Any,
            access_token: Any,
            refresh_token: Any,
            expiry_time: Any = None,
        ) -> bool:
            calls.append("load")
            self.token_type = token_type
            self.access_token = access_token
            self.refresh_token = refresh_token
            self.expiry_time = expiry_time
            self.country_code = "US"
            return True

        def check_login(self) -> bool:
            calls.append("check")
            return True

    monkeypatch.setattr("tidalapi.Session", _CachedSession)
    opened: list[str] = []

    def _record_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", _record_open)
    cli_mod._tidal_login()  # pyright: ignore[reportPrivateUsage]
    assert calls == ["load", "check"]
    assert "expired" not in capsys.readouterr().out
    assert opened == []
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["access_token"] == "valid-access"  # noqa: S105
    assert saved["expiry_time"] == near
    assert saved["user_id"] == 4242
    assert saved["country_code"] == "US"


def test_tidal_login_no_login_returns_none_for_stale_token(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """login=False never touches the network: a stale cache yields None silently."""
    stale = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=1)
    ).isoformat()
    token_file = tmp_path / "tidal_token.json"
    token_file.write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "stale-access",
                "refresh_token": "refresh-token",
                "expiry_time": stale,
                "user_id": 4242,
                "country_code": "US",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_mod, "TIDAL_TOKEN_FILE", token_file)

    class _StrictSession:
        def load_oauth_session(self, *args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            raise AssertionError("network login attempted with login=False")

        def check_login(self) -> bool:
            raise AssertionError("network check attempted with login=False")

        def login_oauth(self) -> Any:
            raise AssertionError("browser login attempted with login=False")

    monkeypatch.setattr("tidalapi.Session", _StrictSession)
    assert cli_mod._tidal_login(login=False) is None  # pyright: ignore[reportPrivateUsage]
    assert capsys.readouterr().out == ""


def test_tidal_login_no_login_returns_none_when_token_missing(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.setattr(cli_mod, "TIDAL_TOKEN_FILE", tmp_path / "missing.json")

    class _StrictSession:
        def login_oauth(self) -> Any:
            raise AssertionError("browser login attempted with login=False")

    monkeypatch.setattr("tidalapi.Session", _StrictSession)
    assert cli_mod._tidal_login(login=False) is None  # pyright: ignore[reportPrivateUsage]
    assert capsys.readouterr().out == ""


def test_tidal_login_no_login_returns_session_when_token_fresh(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    import tidalapi.user as tidal_user

    far = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(days=4)
    ).isoformat()
    token_file = tmp_path / "tidal_token.json"
    token_file.write_text(
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
    monkeypatch.setattr(cli_mod, "TIDAL_TOKEN_FILE", token_file)

    class _StrictSession:
        def load_oauth_session(self, *args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            raise AssertionError("network login attempted for a fresh token")

        def check_login(self) -> bool:
            raise AssertionError("network check attempted for a fresh token")

    class _StubUser:
        def __init__(self, session: Any, user_id: Any) -> None:
            del session
            self.id = user_id

    monkeypatch.setattr("tidalapi.Session", _StrictSession)
    monkeypatch.setattr(tidal_user, "LoggedInUser", _StubUser)
    sess = cli_mod._tidal_login(login=False)  # pyright: ignore[reportPrivateUsage]
    assert sess is not None
    assert sess.access_token == "fresh-access"  # noqa: S105  # pyright: ignore[reportUnknownMemberType]
    assert capsys.readouterr().out == ""


def test_cli_status_offline_no_plan_prints_message(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    missing = tmp_path / "missing.toml"
    monkeypatch.setattr(paths, "PLAN_FILE", missing)
    monkeypatch.setattr(cli_mod, "PLAN_FILE", missing)
    cli_mod.cmd_status(MagicMock(artist=None, album=None))
    out = capsys.readouterr().out
    assert "No transfer plan" in out


def test_cli_status_with_plan_prints_meta(
    isolated_data_dir: Path, monkeypatch: Any, capsys: Any
) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
    import tidal2ytm.plan_io as plan_io

    plan = {
        "meta": {
            "generated_at": "2026-08-29T00:00:00",
            "total_tracks": 1,
            "transferred": 0,
            "pending": 1,
            "needs_review": 0,
            "skip": 0,
            "failed": 0,
        },
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
    plan_io.save_plan(plan, plan_path)
    monkeypatch.setattr(paths, "PLAN_FILE", plan_path)
    monkeypatch.setattr(cli_mod, "PLAN_FILE", plan_path)
    cli_mod.cmd_status(MagicMock(artist=None, album=None))
    out = capsys.readouterr().out
    assert "Transfer plan" in out or "Total tracks" in out


def test_cli_status_scoped_counts(isolated_data_dir: Path, monkeypatch: Any, capsys: Any) -> None:
    plan_path = isolated_data_dir / "transfer_plan.toml"
    import tidal2ytm.plan_io as plan_io

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
                                "title": "S1",
                                "status": "pending",
                                "yt_video_id": "AAAAAAAAAAA",
                            },
                            {
                                "tidal_id": 2,
                                "title": "S2",
                                "status": "transferred",
                                "yt_video_id": "BBBBBBBBBBB",
                            },
                        ],
                    }
                ],
            },
            {
                "name": "C",
                "match_id": "c",
                "albums": [
                    {
                        "name": "D",
                        "match_id": "c/d",
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
    monkeypatch.setattr(paths, "PLAN_FILE", plan_path)
    monkeypatch.setattr(cli_mod, "PLAN_FILE", plan_path)
    cli_mod.cmd_status(MagicMock(artist="a", album=None))
    out = capsys.readouterr().out
    assert "Total tracks" in out


def test_cli_transfer_scope_mutually_exclusive_required() -> None:
    # verify that the transfer parser uses mutually_exclusive_group(required=True)
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_t = sub.add_parser("transfer")
    scope = p_t.add_mutually_exclusive_group(required=True)
    scope.add_argument("--track", metavar="VIDEO_ID")
    scope.add_argument("--album", metavar="MATCH_ID")
    scope.add_argument("--artist", metavar="MATCH_ID")
    scope.add_argument("--all", action="store_true")
    # ensure required=True enforced
    with pytest.raises(SystemExit):
        parser.parse_args(["transfer"])
    # multiple scopes should fail
    with pytest.raises(SystemExit):
        parser.parse_args(["transfer", "--track", "AAAAAAAAAAA", "--all"])
    # exactly one should parse
    args = parser.parse_args(["transfer", "--track", "AAAAAAAAAAA"])
    assert args.track == "AAAAAAAAAAA"

    # also verify real cli parser has required=True by inspecting it
    import inspect

    src = inspect.getsource(cli_mod.main)
    assert "mutually_exclusive_group(required=True)" in src


def test_cli_main_transfer_requires_scope(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "transfer"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 2  # argparse error


def test_cli_main_transfer_rejects_multiple_scopes(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "transfer", "--track", "AAAAAAAAAAA", "--all"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 2


def test_cli_main_review_filters(monkeypatch: Any) -> None:
    # review with status filter should delegate to run_review
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "review", "--needs-review"])
    with patch("tidal2ytm.review.run_review"):
        monkeypatch.setattr("tidal2ytm.cli._tidal_login", lambda: MagicMock())
        monkeypatch.setattr("tidal2ytm.cli._ytm_login", lambda: MagicMock())
        # avoid needing real plan file by mocking run_review directly via cmd_review path
        # call main and verify run_review called with correct filter
        with contextlib.suppress(SystemExit):
            cli_mod.main()
        # Instead test cmd_review mapping directly
    # direct cmd_review test
    with patch("tidal2ytm.review.run_review") as mock:
        cli_mod.cmd_review(
            MagicMock(
                needs_review=True,
                pending=False,
                failed=False,
                skip=False,
                transferred=False,
                all_statuses=False,
                artist=None,
                album=None,
            )
        )
        mock.assert_called_once()
        assert mock.call_args.kwargs["status_filter"] is not None


def test_cli_transfer_dry_run_flag(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "transfer", "--all", "--dry-run"])
    with (
        patch("tidal2ytm.cli._ytm_login", return_value=MagicMock()),
        patch("tidal2ytm.transfer.run_transfer") as mock_t,
    ):
        cli_mod.main()
        mock_t.assert_called_once()
        assert mock_t.call_args.kwargs["dry_run"] is True


def test_cli_main_unknown_command_exits(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "unknown"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 2


def test_cli_help_and_subcommand_help(monkeypatch: Any) -> None:
    for args in [
        ["tidal2ytm", "transfer", "--help"],
        ["tidal2ytm", "review", "--help"],
        ["tidal2ytm", "status", "--help"],
    ]:
        monkeypatch.setattr(sys, "argv", args)
        with pytest.raises(SystemExit) as e:
            cli_mod.main()
        assert e.value.code == 0


def test_cli_bare_invokes_planning(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm"])
    with (
        patch("tidal2ytm.cli._tidal_login", return_value=MagicMock()),
        patch("tidal2ytm.planning.run_planning") as mock_p,
    ):
        cli_mod.main()
        mock_p.assert_called_once()
        assert "tidal_session" not in mock_p.call_args.kwargs


def test_cli_plan_removed(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "plan"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 2
