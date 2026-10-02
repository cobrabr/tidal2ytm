"""The ops the sidecar answers. Every one is a question about the outside world.

Nothing here computes a score, a rank, a certainty, a plan row, a status, or a
count. Those are the host's job, and a value produced here would be a second
implementation of a rule that must exist in exactly one place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from tidal2ytm import auth

_LOG = logging.getLogger(__name__)

# Narrow on purpose. A corrupt cache file is a state to report, not a transport
# or programming bug to swallow: a broad `except Exception` here would turn a
# real bug into a silent "not signed in".
_CACHE_READ_ERRORS = (OSError, ValueError, TypeError)


def _signed_out(reason: Exception) -> dict[str, Any]:
    _LOG.info("token cache unusable, reporting signed out: %s", reason)
    return {"valid": False, "expires_at": None}


def _cached(path: Path) -> dict[str, Any]:
    """Parse a token cache file, refusing anything that is not a JSON object."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"token cache is not a JSON object: {path}")
    return cast("dict[str, Any]", data)


def _tidal_state() -> dict[str, Any]:
    try:
        cached = _cached(auth.paths.TIDAL_TOKEN_FILE)
        return {
            "valid": auth.token_usable(auth.parse_token_expiry(cached.get("expiry_time"))),
            "expires_at": cached.get("expiry_time"),
        }
    except _CACHE_READ_ERRORS as exc:
        return _signed_out(exc)


def _ytm_state() -> dict[str, Any]:
    try:
        cached = _cached(auth.paths.YTM_AUTH_FILE)
        return {
            "valid": auth.ytm_cache_valid(auth.paths.YTM_AUTH_FILE),
            "expires_at": cached.get("expires_at"),
        }
    except _CACHE_READ_ERRORS as exc:
        return _signed_out(exc)


def auth_status() -> dict[str, Any]:
    """Report whether each provider has a usable cached token.

    Token material never leaves this process: the host gets a boolean and an
    expiry, nothing else. Validity itself is decided by `tidal2ytm.auth`, which
    stays the single implementation of that rule.
    """
    return {"tidal": _tidal_state(), "ytm": _ytm_state()}
