"""The ops the sidecar answers. Every one is a question about the outside world.

Nothing here computes a score, a rank, a certainty, a plan row, a status, or a
count. Those are the host's job, and a value produced here would be a second
implementation of a rule that must exist in exactly one place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from tidal2ytm import auth

_LOG = logging.getLogger(__name__)

# Narrow on purpose. A corrupt cache file is a state to report, not a transport
# or programming bug to swallow: a broad `except Exception` here would turn a
# real bug into a silent "not signed in".
_CACHE_READ_ERRORS = (OSError, ValueError, TypeError)

# What one provider's answer is: whether the session works, and when its
# credentials lapse. `expires_at` is `None` or an ISO-8601 UTC string and nothing
# else, because that is what the host's `AuthStatus` deserialises and what
# `src/lib/types.ts` widens the contract into. Written down once here so
# narrowing an expiry is a compile error rather than a field that quietly widens
# back to whatever the token file happened to hold.
_ProviderState = dict[str, bool | str | None]


def _signed_out(reason: Exception) -> _ProviderState:
    _LOG.info("token cache unusable, reporting signed out: %s", reason)
    return {"valid": False, "expires_at": None}


def _cached(path: Path) -> dict[str, object]:
    """Parse a token cache file, refusing anything that is not a JSON object.

    The values are `object`, not `Any`. `json.loads` hands back `Any`, and an
    `Any` flows through every downstream expression without a single complaint
    from pyright — which is how `expires_at` came to be forwarded as a raw epoch
    integer while every gate stayed green. `object` is what `json.loads` actually
    promises, and it forces each value through a function that decides.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"token cache is not a JSON object: {path}")
    return cast("dict[str, object]", data)


def _tidal_state() -> _ProviderState:
    try:
        cached = _cached(auth.paths.TIDAL_TOKEN_FILE)
        return {
            "valid": auth.tidal_cache_valid(auth.paths.TIDAL_TOKEN_FILE),
            # `auth.py` writes this as an ISO string, so the pass-through is a
            # guard rather than a conversion: a cache file holding anything else
            # would be forwarded as-is and fail the host's `expires_at`, taking
            # YouTube Music's status down with the answer. See
            # `auth.tidal_expiry_iso`.
            "expires_at": auth.tidal_expiry_iso(cached.get("expiry_time")),
        }
    except _CACHE_READ_ERRORS as exc:
        return _signed_out(exc)


def _ytm_state() -> _ProviderState:
    try:
        cached = _cached(auth.paths.YTM_AUTH_FILE)
        return {
            "valid": auth.ytm_cache_valid(auth.paths.YTM_AUTH_FILE),
            # Normalised rather than passed through: `ytmusicapi` stores epoch
            # seconds, and a raw integer fails the host's `expires_at`.
            "expires_at": auth.ytm_expiry_iso(cached.get("expires_at")),
        }
    except _CACHE_READ_ERRORS as exc:
        return _signed_out(exc)


def auth_status() -> dict[str, _ProviderState]:
    """Report whether each provider has a usable cached token.

    Token material never leaves this process: the host gets a boolean and an
    expiry, nothing else. Validity itself is decided by `tidal2ytm.auth`, which
    stays the single implementation of that rule.
    """
    return {"tidal": _tidal_state(), "ytm": _ytm_state()}
