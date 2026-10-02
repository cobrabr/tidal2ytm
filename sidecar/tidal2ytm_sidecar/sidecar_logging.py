"""Sidecar logging setup. Records go to stderr because stdout is the IPC channel."""

from __future__ import annotations

import os

from tidal2ytm.logging_setup import LOG_LEVELS, setup_stderr_logging

__all__ = ["LOG_LEVELS", "level_from_env", "setup_stderr_logging"]

ENV_VAR = "TIDAL2YTM_LOG_LEVEL"


def level_from_env() -> str:
    """The level the host set at spawn. An unrecognised value is a hard error.

    Falling back to INFO here would hide the fact that the host sent something
    the sidecar cannot honour.
    """
    raw = os.environ.get(ENV_VAR, "INFO").strip().upper()
    if raw not in LOG_LEVELS:
        raise ValueError(f"Unknown log level in {ENV_VAR}: {raw!r}")
    return raw
