from __future__ import annotations

import datetime
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import requests

from .paths import LOGS_DIR, ensure_logs_dir

# Single owner of the CLI level vocabulary; one-to-one onto stdlib levels.
LOG_LEVELS: dict[str, int] = {
    "OFF": logging.CRITICAL + 1,
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

_REDACT_KEYS = frozenset({"access_token", "refresh_token", "authorization", "client_secret"})

# Mutable module state: the per-run file and its handler are fixed for the
# life of the process once set up; tests reset them via monkeypatch.
_handler: logging.Handler | None = None
_run_file_path: Path | None = None


def resolve_level(flag_value: str | None, env_value: str | None) -> str:
    """flag > env > default INFO; raises ValueError on an unknown name."""
    raw = flag_value or env_value or "INFO"
    name = raw.strip().upper()
    if name not in LOG_LEVELS:
        raise ValueError(f"Unknown log level: {raw!r}")
    return name


def _resolve_run_file() -> Path:
    global _run_file_path
    if _run_file_path is None:
        stamp = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y%m%d-%H%M%S-%f")
        _run_file_path = ensure_logs_dir() / f"{stamp}.log"
    return _run_file_path


def setup_logging(level_name: str) -> Path | None:
    """Attach the per-run file handler at the resolved level; None for OFF."""
    name = resolve_level(level_name, None)
    if name == "OFF":
        return None
    level = LOG_LEVELS[name]
    try:
        path = _resolve_run_file()
    except OSError as exc:
        raise RuntimeError(f"Log directory not writable: {LOGS_DIR}") from exc
    root = logging.getLogger()
    global _handler
    if _handler is None:
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s %(message)s"))
        handler._tidal2ytm_run = True  # type: ignore[attr-defined]
        root.addHandler(handler)
        _handler = handler
    root.setLevel(level)
    _handler.setLevel(level)
    return path


def redact_payload(payload: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
    """Replace token-bearing keys with '[redacted]', recursively."""
    if isinstance(payload, dict):
        return {
            k: "[redacted]"
            if str(k).lower() in _REDACT_KEYS
            else (
                redact_payload(cast(dict[str, Any] | list[Any], v))
                if isinstance(v, dict | list)
                else v
            )
            for k, v in payload.items()
        }
    return [
        redact_payload(cast(dict[str, Any] | list[Any], v)) if isinstance(v, dict | list) else v
        for v in payload
    ]


def log_call(
    logger: logging.Logger, endpoint: str, func: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Run one external call, log endpoint + duration + outcome, always propagate.

    The except arms observe and re-raise; nothing is suppressed.
    """
    start = time.perf_counter()
    try:
        result = func(*args, **kwargs)
    except requests.ConnectionError as exc:
        logger.critical("%s connection-level failure: %s", endpoint, exc)
        raise
    except Exception as exc:  # always re-raised below; observer only
        logger.error("%s failed: %s", endpoint, exc)
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info("%s ok (%.0f ms)", endpoint, elapsed_ms)
    return result
