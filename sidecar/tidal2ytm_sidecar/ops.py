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

# What one provider's answer is: how its credentials stand, when they lapse,
# and whether a YouTube Music OAuth client file is there to refresh them with.
# `status` is one of `valid`, `refreshable` or `signed_out` and `expires_at` is
# `None` or an ISO-8601 UTC string and nothing else, because that is what the
# host's `AuthStatus` deserialises and what `src/lib/types.ts` widens the
# contract into. Written down once here so narrowing an expiry is a compile
# error rather than a field that quietly widens back to whatever the token file
# happened to hold.
_ProviderState = dict[str, bool | str | None]


class BadRequestError(ValueError):
    """A request this sidecar understood and is refusing to carry out.

    Its own type rather than a bare `ValueError`, so `protocol.dispatch` can map
    it to the wire's `bad_request` without claiming that every `ValueError` an op
    raises is the caller's mistake: a bug in an op that happened to raise one
    would then be reported as a request the host got wrong, which sends whoever
    is reading it debugging the host.
    """


def _signed_out(reason: Exception, has_client_secret: bool) -> _ProviderState:
    _LOG.info("token cache unusable, reporting signed out: %s", reason)
    return {"status": "signed_out", "expires_at": None, "has_client_secret": has_client_secret}


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
    # Always `True`, and deliberately not read off the disk: Tidal's device
    # authorisation carries no client file, so asking would report a provider
    # that never needed one as lacking it. A uniform struct is what keeps
    # `ProviderState` one type in `src/lib/types.ts` instead of a union the
    # screen has to narrow.
    has_client_secret = True
    try:
        cached = _cached(auth.paths.TIDAL_TOKEN_FILE)
        return {
            "status": auth.tidal_cache_state(auth.paths.TIDAL_TOKEN_FILE),
            # `auth.py` writes this as an ISO string, so the pass-through is a
            # guard rather than a conversion: a cache file holding anything else
            # would be forwarded as-is and fail the host's `expires_at`, taking
            # YouTube Music's status down with the answer. See
            # `auth.tidal_expiry_iso`.
            "expires_at": auth.tidal_expiry_iso(cached.get("expiry_time")),
            "has_client_secret": has_client_secret,
        }
    except _CACHE_READ_ERRORS as exc:
        return _signed_out(exc, has_client_secret)


def _ytm_state() -> _ProviderState:
    # The flag the import dialog hangs on: without a client file there is
    # nothing to refresh with, so a signed-out answer that said otherwise would
    # offer the user a sign-in that cannot start.
    has_client_secret = auth.client_secret_present()
    try:
        cached = _cached(auth.paths.YTM_AUTH_FILE)
        return {
            "status": auth.ytm_cache_state(auth.paths.YTM_AUTH_FILE),
            # Normalised rather than passed through: `ytmusicapi` stores epoch
            # seconds, and a raw integer fails the host's `expires_at`.
            "expires_at": auth.ytm_expiry_iso(cached.get("expires_at")),
            "has_client_secret": has_client_secret,
        }
    except _CACHE_READ_ERRORS as exc:
        return _signed_out(exc, has_client_secret)


def auth_status() -> dict[str, _ProviderState]:
    """Report how each provider's cached credentials stand.

    Token material never leaves this process: the host gets a state, an expiry
    and a boolean, nothing else. Everything is decided offline by
    `tidal2ytm.auth`, which stays the single implementation of those rules —
    nothing here refreshes, because the GUI asks on every Home mount and a
    network round-trip in front of the first screen is not affordable.
    """
    return {"tidal": _tidal_state(), "ytm": _ytm_state()}


def auth_begin(provider: str) -> dict[str, str]:
    """Run one provider's sign-in, and report which provider it was for.

    This does not return until a browser and a person finish, which is safe only
    because the read loop answers each request on a thread of its own: the host
    keeps asking `auth.status` throughout and gets it in five seconds. The
    provider is echoed back so a caller can pair a reply that arrives minutes
    later with the request that asked for it.

    `BadRequestError` for an unknown provider and for YouTube Music with no OAuth
    client file to sign in with, which `protocol.dispatch` reports as the
    caller's mistake rather than the provider's. The credential is asked for here
    rather than left to the flow's own path because that path is the CLI's, and a
    `SystemExit` raised on a request thread ends the sidecar process rather than
    the one request that could not start. It is asked for even when a cached
    token would have answered without a flow: the screen offers a sign-in for a
    provider that is not already signed in, and refusing up front is the
    direction that cannot leave a caller waiting. The sign-in itself is
    `auth.sign_in_ytm` rather than `auth.run_ytm_auth` for the same reason — the
    CLI half exits, and everything else, a rejected login or a provider that is
    down, is left to propagate as the `upstream` failure it is.
    """
    if provider == "tidal":
        auth.run_tidal_auth()
    elif provider == "ytm":
        try:
            auth.require_client_secret(auth.paths.DATA_DIR)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        auth.sign_in_ytm()
    else:
        raise BadRequestError(f"unknown auth provider: {provider!r}")
    return {"provider": provider}
