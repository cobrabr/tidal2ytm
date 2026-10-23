"""Refresh a lapsed Tidal access token and write the result back.

`tidalapi` provides nothing this code can use. `load_oauth_session` refreshes
when Tidal says the access token has expired, but it keeps the result in memory
and never writes it down; `login_session_file` is the only path that both
refreshes and writes, and it writes a five-key `{"key": {"data": ...}}` shape
that is not this project's token file, so the next read finds no credentials —
and when the file cannot be loaded at all it falls through to an interactive
login, which on a sidecar answering a request over a pipe means blocking on a
human with nobody to answer.

So the refresh and the write-back are done here, against the six keys
`tidal2ytm.auth` guarantees. The file is never deleted on any path: a dropped
connection is not grounds for throwing away a refresh token that outlives the
access token by months.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import requests
import tidalapi
from tidalapi.exceptions import AuthenticationError

from tidal2ytm import auth

if TYPE_CHECKING:
    from tidalapi.session import Session

_LOG = logging.getLogger(__name__)

# The one HTTP status that means "these credentials are refused" rather than
# "Tidal is unavailable". `tidalapi` raises `AuthenticationError` when the token
# endpoint rejects the refresh token; when the retry still comes back
# unauthorized it re-raises the raw `requests.HTTPError` instead, and that is the
# only one of its statuses this module reads. A transport failure, a 404 and a
# 500 propagate: this project keeps those out of the credential path rather than
# degrading them into "signed out", because a person told to sign in again over a
# network blip loses their session standing and achieves nothing.
_UNAUTHORIZED = 401


class RefreshFailedError(RuntimeError):
    """The cached credentials did not produce a usable Tidal session.

    Not a statement about the file on disk, which is what `auth.status`'s
    `signed_out` is. This one is about a session that could not be built from
    that file, and it means the same thing to a caller either way: there is
    nothing to fetch with, and signing in again is the remedy. `ops` reports it
    as `auth_expired`, which is the kind the host offers a sign-in for.
    """


def _is_unauthorized(exc: requests.HTTPError) -> bool:
    response = exc.response
    return response is not None and response.status_code == _UNAUTHORIZED


def _load(session: Session, creds: auth.TidalCredentials) -> None:
    """Hand `creds` to the session, converting a refusal into `RefreshFailedError`.

    `load_oauth_session` returning `False` is Tidal saying the credentials did
    not produce a session, which is the same answer as a raised refusal and has
    no usable session to inspect afterwards.
    """
    try:
        loaded = session.load_oauth_session(
            creds.token_type,
            creds.access_token,
            creds.refresh_token,
            creds.expiry_time,
        )
    except AuthenticationError as exc:
        raise RefreshFailedError("Tidal refused the cached refresh token") from exc
    except requests.HTTPError as exc:
        if not _is_unauthorized(exc):
            raise
        raise RefreshFailedError("Tidal refused the cached access token") from exc
    if not loaded:
        raise RefreshFailedError("Tidal did not accept the cached credentials")


def load_session(token_file: Path) -> Session:
    """A Tidal session from the cached token, refreshed if that token had lapsed.

    Refreshed, and written back, only when the `expiry_time` read from the file
    was already in the past. A token that is still good is left alone on disk: it
    is loaded, used, and never rewritten, so a fetch that spans many pages does
    not write credentials once per page.
    """
    creds = auth.read_tidal_credentials(token_file)
    if creds is None:
        raise RefreshFailedError(f"no usable Tidal token cache at {token_file}")
    session = tidalapi.Session()  # pyright: ignore[reportPrivateImportUsage]
    _load(session, creds)
    if not auth.token_usable(creds.expiry_time):
        _LOG.info("tidal access token had lapsed; refreshed and written back")
        persist_session(token_file, session)
    return session


def persist_session(token_file: Path, session: Session) -> None:
    """Write the session's tokens into `token_file`, all six keys and no others.

    `user_id` and `country_code` come from the file rather than the session:
    they identify who these credentials belong to, they do not change when an
    access token is refreshed, and reading them back is what keeps a partially
    understood session from blanking them. A file that cannot be read that way is
    a refusal to write, not a reason to write a half-file over it.

    A session carrying no token or no expiry is refused for the same reason:
    writing one of those would replace a working refresh token with a broken
    one, which is the damage this module exists to prevent.
    """
    creds = auth.read_tidal_credentials(token_file)
    if creds is None:
        raise RefreshFailedError(f"no usable Tidal token cache at {token_file}")
    token_type = session.token_type
    access_token = session.access_token
    refresh_token = session.refresh_token
    expiry = auth.render_tidal_expiry(session.expiry_time)
    if not token_type or not access_token or not refresh_token or expiry is None:
        raise RefreshFailedError("the refreshed session carries no token to write back")
    token_file.write_text(
        json.dumps(
            {
                "token_type": token_type,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expiry_time": expiry,
                "user_id": creds.user_id,
                "country_code": creds.country_code,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
