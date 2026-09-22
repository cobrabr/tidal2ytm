from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import tidal2ytm.cli as cli_mod
import tidal2ytm.paths as paths
from tidal2ytm.errors import PlanNotFoundError
from tidal2ytm.models import TrackStatus


def test_cli_help_and_status_offline(tmp_path: Path, monkeypatch: Any) -> None:
    # status without plan file must stay offline-safe (no network, no crash)
    missing = tmp_path / "nonexistent.toml"
    monkeypatch.setattr(paths, "PLAN_FILE", missing)
    monkeypatch.setattr(cli_mod, "PLAN_FILE", missing)
    cli_mod.cmd_status(MagicMock(artist=None, album=None))


def test_cli_main_parses_help(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "--help"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 0


def test_cli_main_planning_abort_exits_cleanly(monkeypatch: Any) -> None:
    import tidal2ytm.planning as planning_mod

    monkeypatch.setattr(sys, "argv", ["tidal2ytm"])

    def _no_login() -> MagicMock:
        raise AssertionError("startup must not log in")

    monkeypatch.setattr(cli_mod, "tidal_login", _no_login)

    def _abort(**kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(planning_mod, "run_planning", _abort)
    # KeyboardInterrupt absorbed at the CLI boundary: main() returns normally.
    cli_mod.main()


def test_wait_status_prints_plain_text_without_tty(capsys: Any) -> None:
    ran = False
    with cli_mod.wait_status("Reticulating splines"):
        ran = True
    assert ran
    assert "Reticulating splines…" in capsys.readouterr().out


def test_wait_status_uses_centralized_spinner(monkeypatch: Any) -> None:
    from tidal2ytm import style as style_mod

    seen: dict[str, str] = {}

    class _FakeStatus:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen.update(
                {
                    k: v
                    for k, v in kwargs.items()
                    if k in ("spinner", "spinner_style", "speed", "refresh_per_second")
                }
            )

        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: Any) -> bool:
            return False

    class _FakeConsole:
        def status(self, *args: Any, **kwargs: Any) -> _FakeStatus:
            return _FakeStatus(*args, **kwargs)

    monkeypatch.setattr(sys, "stdout", _TtyStdout())
    monkeypatch.setattr("rich.console.Console", _FakeConsole)
    with cli_mod.wait_status("Reticulating splines"):
        pass
    assert seen["spinner"] == style_mod.SPINNER_NAME
    assert seen["speed"] == style_mod.SPINNER_SPEED
    assert seen["refresh_per_second"] == style_mod.SPINNER_REFRESH_PER_SECOND


class _TtyStdout:
    def isatty(self) -> bool:
        return True

    def write(self, _s: str) -> int:
        return 0

    def flush(self) -> None:
        return None


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
    assert "Total tracks: 1" in out
    assert "pending:" in out and "transferred:" in out


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
    assert "Total tracks (scoped): 2" in out


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
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "review", "--needs-review"])
    with patch("tidal2ytm.review.run_review", return_value=False) as mock:
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
        assert mock.call_args.kwargs["status_filter"] is TrackStatus.NEEDS_REVIEW


def test_cli_transfer_dry_run_flag(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "transfer", "--all", "--dry-run"])
    with (
        patch("tidal2ytm.cli._ytm_login", return_value=MagicMock()),
        patch("tidal2ytm.transfer.run_transfer") as mock_t,
    ):
        cli_mod.main()
        mock_t.assert_called_once()
        assert mock_t.call_args.kwargs["dry_run"] is True


def test_cli_bare_invokes_planning(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm"])

    def _no_login() -> MagicMock:
        raise AssertionError("startup must not log in (login is deferred to planning)")

    with (
        patch("tidal2ytm.cli.tidal_login", _no_login),
        patch("tidal2ytm.planning.run_planning") as mock_p,
    ):
        cli_mod.main()
        mock_p.assert_called_once()
        assert "tidal_session" not in mock_p.call_args.kwargs


def test_ytm_login_rejects_multiple_client_secrets(tmp_path: Path) -> None:
    import time

    from tidal2ytm.ytm_client import YTMClient

    auth_file = tmp_path / "ytm_auth.json"
    auth_file.write_text(
        json.dumps({"expires_at": int(time.time()) + 3600, "refresh_token": "rt"}),
        encoding="utf-8",
    )
    (tmp_path / "client_secret_a.json").write_text(
        json.dumps({"installed": {"client_id": "a", "client_secret": "a-sec"}}),
        encoding="utf-8",
    )
    (tmp_path / "client_secret_b.json").write_text(
        json.dumps({"installed": {"client_id": "b", "client_secret": "b-sec"}}),
        encoding="utf-8",
    )
    with pytest.raises(PlanNotFoundError):
        YTMClient(auth_file=auth_file, data_dir=tmp_path).login()


# ---------------------------------------------------------------------------
# cli.tidal_login — the public Tidal session seam (token cache / refresh)
# ---------------------------------------------------------------------------


def _utc_in(**kwargs: float) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) + datetime.timedelta(**kwargs)


def _open_ok(url: str) -> bool:
    del url
    return True


def _patch_token_file(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    # cli.py binds TIDAL_TOKEN_FILE at import time; redirect it to the isolated dir
    monkeypatch.setattr(cli_mod, "TIDAL_TOKEN_FILE", data_dir / "tidal_token.json")


def _write_tidal_token(data_dir: Path, expiry: datetime.datetime, **overrides: Any) -> None:
    payload: dict[str, Any] = {
        "token_type": "Bearer",
        "access_token": "cached-access",
        "refresh_token": "cached-refresh",
        "expiry_time": expiry.isoformat(),
        "user_id": 4242,
        "country_code": "US",
    }
    payload.update(overrides)
    (data_dir / "tidal_token.json").write_text(json.dumps(payload), encoding="utf-8")


class _FakeTidalSession:
    def __init__(self) -> None:
        self.token_type = ""
        self.access_token = ""
        self.refresh_token = ""
        self.expiry_time: Any = None
        self.country_code = ""
        self.locale = ""
        self.user: Any = None
        self.load_calls: list[tuple[str, str, str, Any]] = []
        self.login_calls = 0
        self.checked = False

    def load_oauth_session(
        self, token_type: str, access_token: str, refresh_token: str, expiry_time: Any
    ) -> bool:
        self.load_calls.append((token_type, access_token, refresh_token, expiry_time))
        self.token_type = token_type
        # mark the refresh so the persisted cache proves the round-trip happened
        self.access_token = access_token + "-refreshed"
        self.refresh_token = refresh_token
        self.expiry_time = _utc_in(days=30)
        return True

    def check_login(self) -> bool:
        self.checked = True
        return True

    def login_oauth(self) -> Any:
        self.login_calls += 1
        self.token_type = "Bearer"  # noqa: S105
        self.access_token = "browser-access"  # noqa: S105
        self.refresh_token = "browser-refresh"  # noqa: S105
        self.expiry_time = _utc_in(days=30)
        self.country_code = "US"
        self.user = SimpleNamespace(id=4242)
        link = SimpleNamespace(verification_uri_complete="example.com/verify")
        future = SimpleNamespace(result=lambda: None)
        return link, future


class _FakeLoggedInUser:
    def __init__(self, session: Any, user_id: int) -> None:
        self.session = session
        self.id = user_id


def test_tidal_login_hydrates_session_from_fresh_cache(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_token_file(monkeypatch, isolated_data_dir)
    _write_tidal_token(isolated_data_dir, _utc_in(days=4))
    fake = _FakeTidalSession()
    monkeypatch.setattr("tidalapi.Session", lambda: fake)
    monkeypatch.setattr("tidalapi.user.LoggedInUser", _FakeLoggedInUser)

    session = cli_mod.tidal_login()

    assert session is fake
    assert fake.access_token == "cached-access"  # noqa: S105
    assert fake.user.id == 4242
    # fresh token: no validating round-trip and no browser flow
    assert fake.load_calls == []
    assert fake.checked is False
    assert fake.login_calls == 0


def test_tidal_login_refreshes_near_expiry_token_and_persists(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_token_file(monkeypatch, isolated_data_dir)
    _write_tidal_token(isolated_data_dir, _utc_in(minutes=10))
    fake = _FakeTidalSession()
    monkeypatch.setattr("tidalapi.Session", lambda: fake)
    monkeypatch.setattr("webbrowser.open", _open_ok)

    session = cli_mod.tidal_login()

    assert session is fake
    assert len(fake.load_calls) == 1
    assert fake.load_calls[0][1] == "cached-access"
    assert fake.checked is True
    data = json.loads((isolated_data_dir / "tidal_token.json").read_text(encoding="utf-8"))
    assert data["access_token"] == "cached-access-refreshed"  # noqa: S105
    assert isinstance(data["expiry_time"], str)


def test_tidal_login_without_cache_and_login_false_returns_none(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_token_file(monkeypatch, isolated_data_dir)
    fake = _FakeTidalSession()
    monkeypatch.setattr("tidalapi.Session", lambda: fake)

    assert cli_mod.tidal_login(login=False) is None
    assert fake.login_calls == 0


def test_tidal_login_stale_cache_with_login_false_returns_none(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_token_file(monkeypatch, isolated_data_dir)
    _write_tidal_token(isolated_data_dir, _utc_in(hours=-1))
    fake = _FakeTidalSession()
    monkeypatch.setattr("tidalapi.Session", lambda: fake)

    assert cli_mod.tidal_login(login=False) is None
    assert fake.load_calls == []
    assert fake.checked is False
    assert fake.login_calls == 0


def test_tidal_login_corrupt_cache_falls_through_to_browser_login(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_token_file(monkeypatch, isolated_data_dir)
    (isolated_data_dir / "tidal_token.json").write_text("not valid json {{{", encoding="utf-8")
    fake = _FakeTidalSession()
    monkeypatch.setattr("tidalapi.Session", lambda: fake)
    opened: list[str] = []

    def _record_url(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", _record_url)

    session = cli_mod.tidal_login()

    assert session is fake
    assert fake.login_calls == 1
    assert opened != []
    data = json.loads((isolated_data_dir / "tidal_token.json").read_text(encoding="utf-8"))
    assert data["access_token"] == "browser-access"  # noqa: S105


def test_tidal_login_incomplete_cache_forces_fresh_login(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_token_file(monkeypatch, isolated_data_dir)
    _write_tidal_token(isolated_data_dir, _utc_in(days=4), refresh_token=None)
    fake = _FakeTidalSession()
    monkeypatch.setattr("tidalapi.Session", lambda: fake)
    monkeypatch.setattr("webbrowser.open", _open_ok)

    session = cli_mod.tidal_login()

    assert session is fake
    # incomplete schema: no validating refresh, straight to the browser flow
    assert fake.load_calls == []
    assert fake.checked is False
    assert fake.login_calls == 1
