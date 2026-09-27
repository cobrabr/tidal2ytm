from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tidal2ytm import cli as cli_mod


def test_main_sets_up_logging_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[str] = []

    def fake_setup(level_name: str) -> None:
        recorded.append(level_name)
        return None

    def fake_resolve(flag: str | None, env: str | None) -> str:
        return flag or "INFO"

    parser_stub = MagicMock()
    parser_stub.parse_args.return_value = MagicMock(
        command="status", func=MagicMock(), log_level="DEBUG"
    )
    with (
        patch.object(cli_mod, "setup_logging", side_effect=fake_setup),
        patch.object(cli_mod, "resolve_level", side_effect=fake_resolve),
        patch.object(cli_mod.argparse, "ArgumentParser", return_value=parser_stub),
    ):
        cli_mod.main()
    assert recorded == ["DEBUG"]


def test_invalid_env_level_is_hard_error(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setenv("TIDAL2YTM_LOG_LEVEL", "chatty")
    with pytest.raises(SystemExit):
        cli_mod.main()


def test_log_level_flag_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[str] = []

    def fake_setup(level_name: str) -> None:
        recorded.append(level_name)
        return None

    def fake_resolve(flag: str | None, env: str | None) -> str:
        return (flag or env or "INFO").upper()

    monkeypatch.setattr(cli_mod.sys, "argv", ["tidal2ytm", "--log-level", "debug", "status"])
    with (
        patch.object(cli_mod, "setup_logging", side_effect=fake_setup),
        patch.object(cli_mod, "resolve_level", side_effect=fake_resolve),
        patch.object(cli_mod, "cmd_status"),
    ):
        cli_mod.main()
    assert recorded == ["DEBUG"]
