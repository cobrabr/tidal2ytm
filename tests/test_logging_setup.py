from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, cast

import pytest
import requests

from tidal2ytm import logging_setup


@pytest.fixture(autouse=True)
def _reset_logging_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    """setup_logging owns process globals; each test starts from a clean slate."""
    monkeypatch.setattr(logging_setup, "_run_file_path", None)
    monkeypatch.setattr(logging_setup, "_handler", None)
    root = logging.getLogger()
    for h in root.handlers[:]:
        if getattr(h, "_tidal2ytm_run", False):
            root.removeHandler(h)
    yield
    for h in root.handlers[:]:
        if getattr(h, "_tidal2ytm_run", False):
            root.removeHandler(h)


def test_resolve_level_accepts_all_names() -> None:
    for name in ("OFF", "CRITICAL", "ERROR", "WARN", "INFO", "DEBUG"):
        assert logging_setup.resolve_level(name.lower(), None) == name


def test_resolve_level_invalid_raises() -> None:
    with pytest.raises(ValueError):
        logging_setup.resolve_level("TRACE", None)


def test_resolve_level_precedence() -> None:
    assert logging_setup.resolve_level("DEBUG", "CRITICAL") == "DEBUG"
    assert logging_setup.resolve_level(None, "CRITICAL") == "CRITICAL"
    assert logging_setup.resolve_level(None, None) == "INFO"


def test_setup_off_attaches_no_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = logging_setup.setup_logging("OFF")
    assert path is None
    assert not (tmp_path / "logs").exists()
    root = logging.getLogger()
    assert (
        all(getattr(h, "_tidal2ytm_run", False) is False for h in root.handlers)
        or not root.handlers
    )


def test_setup_creates_timestamped_run_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = logging_setup.setup_logging("INFO")
    assert path is not None
    assert path.parent.name == "logs"
    assert re.fullmatch(r"\d{8}-\d{6}-\d{6}\.log", path.name)
    logging.getLogger("some.third.party").warning("transport signal")
    logging.getLogger("tidal2ytm.probe").info("our record")
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert "transport signal" in text
    assert "our record" in text
    assert "tidal2ytm.probe" in text


def test_setup_reuses_run_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    first = logging_setup.setup_logging("INFO")
    second = logging_setup.setup_logging("DEBUG")
    assert first is not None and first == second


def test_setup_unwritable_dir_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="logs"):
        logging_setup.setup_logging("INFO")


def test_log_call_logs_ok_and_reraises_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    logging_setup.setup_logging("INFO")
    logger = logging.getLogger("tidal2ytm.test")

    def ok() -> str:
        return "done"

    with caplog.at_level(logging.INFO, logger="tidal2ytm.test"):
        assert logging_setup.log_call(logger, "POST /x", ok) == "done"
    assert any("POST /x" in r.getMessage() and "ms" in r.getMessage() for r in caplog.records)

    def boom() -> str:
        raise ValueError("nope")

    with caplog.at_level(logging.ERROR, logger="tidal2ytm.test"), pytest.raises(ValueError):
        logging_setup.log_call(logger, "POST /y", boom)
    assert any(r.levelno == logging.ERROR and "POST /y" in r.getMessage() for r in caplog.records)


def test_log_call_connection_failure_is_critical(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("tidal2ytm.test")

    def unreachable() -> str:
        raise requests.ConnectionError("down")

    with (
        caplog.at_level(logging.CRITICAL, logger="tidal2ytm.test"),
        pytest.raises(requests.ConnectionError),
    ):
        logging_setup.log_call(logger, "GET /y", unreachable)
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_redact_payload_strips_token_fields() -> None:
    payload: dict[str, Any] = {
        "access_token": "sekrit",
        "refresh_token": "sekrit2",
        "Authorization": "Bearer sekrit3",
        "clientName": "WEB_REMIX",
        "nested": {"access_token": "sekrit4", "safe": 1},
    }
    out = logging_setup.redact_payload(payload)
    out_d = cast(dict[str, Any], out)
    assert out_d["clientName"] == "WEB_REMIX"
    assert out_d["access_token"] == "[redacted]"  # noqa: S105
    assert out_d["Authorization"] == "[redacted]"
    assert cast(dict[str, Any], out_d["nested"])["access_token"] == "[redacted]"  # noqa: S105
    assert cast(dict[str, Any], out_d["nested"])["safe"] == 1
