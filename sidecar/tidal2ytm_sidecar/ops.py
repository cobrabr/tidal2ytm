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

from tidal2ytm import auth, tidal_source
from tidal2ytm.models import SourceTrack

from . import refresh

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


class AuthExpiredError(RuntimeError):
    """Credentials that produced no session, where signing in again is the remedy.

    Raised only where a session was actually wanted and could not be built. It
    is a `RuntimeError` rather than a `BadRequestError` because nothing the host
    sent caused it, and reporting it as a bad request would blame the caller for
    credentials it does not hold.
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
    direction that cannot leave a caller waiting.

    Both branches call the sidecar's own entry point (`auth.sign_in_tidal`,
    `auth.sign_in_ytm`) rather than the CLI's (`auth.run_tidal_auth`,
    `auth.run_ytm_auth`) for the same reason: the CLI half prints to stdout,
    which is this process's IPC channel, and exits on failure. Both are asked to
    sign in even when a cached token is still valid — `begin` is a request to
    start a flow, and answering one with nothing at all is what a person whose
    click did nothing is left staring at.
    """
    if provider == "tidal":
        auth.sign_in_tidal(force=True)
    elif provider == "ytm":
        try:
            auth.require_client_secret(auth.paths.DATA_DIR)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        auth.sign_in_ytm(interactive=False, force=True)
    else:
        raise BadRequestError(f"unknown auth provider: {provider!r}")
    return {"provider": provider}


def _track_wire(track: SourceTrack) -> dict[str, object]:
    """One track as the eleven keys `contracts/favorites_list.json` records.

    A copy rather than `dataclasses.asdict`, because the wire shape is a
    decision — `album_year` and `version` are the two nullable ones — and a
    reflection over the dataclass would publish whatever the dataclass happens to
    hold, including a field the contract does not name.
    """
    return {
        "tidal_id": track.tidal_id,
        "title": track.title,
        "artist": track.artist,
        "artists": track.artists,
        "album": track.album,
        "album_id": track.album_id,
        "album_year": track.album_year,
        "duration_sec": track.duration_sec,
        "track_num": track.track_num,
        "disc_num": track.disc_num,
        "version": track.version,
    }


def favorites_list(offset: int, limit: int) -> dict[str, object]:
    """One page of Tidal favourites, as Tidal reports them.

    Raw source tracks and nothing derived: no score, no key, no status, no count
    and no ordering. Every one of those is the host's, computed in Rust, and a
    value produced here would be a second implementation of a rule that must
    exist in exactly one place.

    The host owns the loop and the page is the unit of work, so exactly one
    request is issued — never a walk to the end of the favourites behind the
    host's back, which would make the offset it asked for meaningless.
    """
    if offset < 0:
        raise BadRequestError(f"`favorites.list` needs a non-negative offset, got {offset}")
    if limit < 1:
        # A limit of zero is not an empty page, it is an unbounded fetch loop in
        # the caller's `while not page_complete`, and one page of zero tracks
        # reports itself as complete, so it would end it having fetched nothing.
        raise BadRequestError(f"`favorites.list` needs a positive limit, got {limit}")
    try:
        session = refresh.load_session(auth.paths.TIDAL_TOKEN_FILE)
    except refresh.RefreshFailedError as exc:
        # The one thing the host can act on is "sign in again", so the failure is
        # reported as the kind that offers a sign-in rather than as a generic one.
        raise AuthExpiredError("Tidal credentials could not be refreshed; sign in again.") from exc
    tracks = tidal_source.fetch_favorite_page(session, offset, limit)
    return {
        "tracks": [_track_wire(track) for track in tracks],
        # The raw page length, which is the mapped length only because nothing was
        # de-duplicated on the way here. If de-duplication ever moves into this
        # op, a full page that lost a repeat reads as short, the host stops a page
        # early, and the rest of someone's favourites is silently never fetched.
        "page_complete": len(tracks) < limit,
    }
