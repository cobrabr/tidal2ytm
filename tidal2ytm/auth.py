from __future__ import annotations

import datetime
import json
import logging
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import requests
import tidalapi
from tidalapi.exceptions import TidalAPIError
from ytmusicapi import OAuthCredentials, YTMusic
from ytmusicapi.setup import setup_oauth

from . import paths

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from typing import Any

# A cached token this far from expiry is trusted without a network round-trip.
TOKEN_FRESH_MARGIN = datetime.timedelta(minutes=30)

# Narrow auth/network failure surface shared with cli.py (same list in both files).
# OSError covers file reads + FileNotFoundError; ValueError covers bad JSON/expiry;
# KeyError/TypeError cover malformed token shapes; RequestException covers transport;
# TidalAPIError covers tidal auth rejections. Anything else (AttributeError,
# programming errors) propagates instead of degrading into "re-authenticate".
_AUTH_ERRORS: tuple[type[BaseException], ...] = (  # pyright: ignore[reportUnknownVariableType]
    OSError,
    ValueError,
    KeyError,
    TypeError,
    requests.RequestException,
    TidalAPIError,
)

_LOG = logging.getLogger(__name__)


def _log_auth_failure(exc: BaseException) -> None:
    """Log an auth failure; connection-level breakage escalates to CRITICAL."""
    if isinstance(exc, requests.ConnectionError):
        _LOG.critical("auth connection failure: %s", exc)
    else:
        _LOG.error("auth failure: %s", exc)


def parse_token_expiry(raw: object) -> datetime.datetime | None:
    """Parse the cached `expiry_time` to an aware UTC datetime, if possible."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def token_usable(
    expiry: datetime.datetime | None,
    margin: datetime.timedelta = datetime.timedelta(0),
) -> bool:
    """True when `expiry` is further than `margin` in the future (aware compare)."""
    if expiry is None:
        return False
    candidate = expiry
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=datetime.UTC)
    now = datetime.datetime.now(datetime.UTC)
    return candidate - now > margin


def _refresh_token_present(data: MutableMapping[str, Any]) -> bool:
    """True when the cache holds a refresh token worth keeping.

    Both providers write the key under the same name, and for both of them it is
    the thing that outlives the access token: a Tidal access token lasts hours
    and a YouTube Music one days, while the refresh token beside each lasts
    months.
    """
    refresh = data.get("refresh_token")
    return isinstance(refresh, str) and bool(refresh.strip())


def _cache_status(unexpired: bool, refreshable: bool) -> str:
    """The three states every provider reports, from two facts it can read offline.

    Offline on purpose: the GUI asks for this on every Home mount, so anything
    that reached for the network here would put a round-trip in front of the
    first screen. `refreshable` is therefore a statement about credentials on
    disk, not a promise that a refresh has been performed.
    """
    if unexpired:
        return "valid"
    if refreshable:
        return "refreshable"
    return "signed_out"


def _tidal_cache_facts(token_file: Path) -> tuple[bool, bool]:
    """`(unexpired, refreshable)` from a cached Tidal token file. Never raises."""
    try:
        data: MutableMapping[str, Any] = json.loads(token_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (False, False)
    if not isinstance(data, dict):
        return (False, False)
    unexpired = token_usable(parse_token_expiry(data.get("expiry_time")))
    return (unexpired, _refresh_token_present(data))


def tidal_cache_valid(token_file: Path) -> bool:
    """Offline check: cached Tidal credentials are unexpired or refreshable. Never raises."""
    unexpired, refreshable = _tidal_cache_facts(token_file)
    return unexpired or refreshable


def tidal_cache_state(token_file: Path) -> str:
    """`"valid"`, `"refreshable"` or `"signed_out"` for the cached Tidal token."""
    return _cache_status(*_tidal_cache_facts(token_file))


def _ytm_cache_facts(auth_file: Path) -> tuple[bool, bool]:
    """`(unexpired, refreshable)` from a cached YouTube Music token file. Never raises."""
    try:
        data: MutableMapping[str, Any] = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (False, False)
    if not isinstance(data, dict):
        return (False, False)
    try:
        expires_at = int(data.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    return (expires_at > int(time.time()), _refresh_token_present(data))


def ytm_cache_valid(auth_file: Path) -> bool:
    """Offline check: cached YTM credentials are unexpired or refreshable. Never raises.

    A non-expired `expires_at` counts, and so does a stored `refresh_token`
    (refreshable credentials stay valid offline even when expired).
    """
    unexpired, refreshable = _ytm_cache_facts(auth_file)
    return unexpired or refreshable


def ytm_cache_state(auth_file: Path) -> str:
    """`"valid"`, `"refreshable"` or `"signed_out"` for the cached YTM token."""
    return _cache_status(*_ytm_cache_facts(auth_file))


def client_secret_present() -> bool:
    """True when a Google Cloud OAuth client file sits in the data directory.

    A question about the directory rather than about the credential's contents,
    so it cannot fail on a file that exists but does not parse. Whether that
    file is usable is `read_client_secret`'s question to answer, and one that
    only matters once someone actually tries to sign in.
    """
    return len(sorted(paths.DATA_DIR.glob("client_secret_*.json"))) > 0


def tidal_expiry_iso(raw: object) -> str | None:
    """The Tidal token's `expiry_time`, but only when it is one the GUI can use.

    `expiry_time` is written as an ISO string, so this is a guard rather than a
    conversion, and the string is returned as it stands rather than re-rendered:
    re-rendering would drop the microseconds the writer emits and change an
    answer that was already correct.

    `parse_token_expiry` is the one place that decides what a Tidal expiry string
    is, so anything it cannot read is `None` here too. That matters because the
    two providers reach one wire field, and an `expiry_time` holding a number or a
    nested object would be forwarded verbatim — failing the host's `expires_at`
    and taking YouTube Music's status down with the answer.
    """
    if isinstance(raw, str) and parse_token_expiry(raw) is not None:
        return raw
    return None


def ytm_expiry_iso(raw: object) -> str | None:
    """Render YouTube Music's cached `expires_at` as an ISO-8601 UTC string.

    `ytmusicapi` records it as epoch seconds, and that is not what the GUI's field
    means. The contract records the *signed-out* sample, so its `expires_at` is
    `null`; the `string` half of the wire's type comes from `Widen` in
    `src/lib/types.ts` and from `Option<String>` in the host's `AuthStatus` — the
    contract fixes the key set, not the values. So the wire says string-or-null,
    and this is the half of that contract YouTube Music does not meet on its own:
    `tidal_expiry_iso` is its counterpart for the Tidal side.

    Reporting the raw integer made the Rust host refuse the whole answer
    (`invalid type: integer, expected a string`), and a signed-in YouTube Music
    user saw an error on the only screen the application had.

    `None` for anything that is not a number, including `bool`, which is an `int`
    and would otherwise be read as the epoch second 0 or 1.
    """
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    try:
        return datetime.datetime.fromtimestamp(raw, tz=datetime.UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        # An epoch the platform cannot represent is a fact about the cache file,
        # not a transport or programming bug: report no expiry and leave
        # `ytm_cache_valid` to decide what it means for the session.
        return None


def read_client_secret(data_dir: Path) -> tuple[str, str]:
    """Read the single `client_secret_*.json` in `data_dir`, sorted and strict."""
    files = sorted(data_dir.glob("client_secret_*.json"))
    if not files:
        raise FileNotFoundError(f"No client_secret_*.json in {data_dir}")
    if len(files) > 1:
        names = ", ".join(f.name for f in files)
        raise RuntimeError(f"Multiple client_secret_*.json files in {data_dir}: {names}")
    raw: MutableMapping[str, Any] = json.loads(files[0].read_text(encoding="utf-8"))
    for key in ("installed", "web"):
        if key in raw:
            return str(raw[key]["client_id"]), str(raw[key]["client_secret"])
    for val in raw.values():
        if isinstance(val, dict) and "client_id" in val and "client_secret" in val:
            return str(val["client_id"]), str(val["client_secret"])  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
    raise ValueError(f"Could not parse 'client_id' and 'client_secret' from {files[0].name}.")


_read_client_secret = read_client_secret


# What the CLI has always printed when there is no OAuth client to sign in with,
# and now also what the sidecar sends over the wire. It is here, beside the
# exception that carries it, because it is the only thing standing between the
# failure and a person who does not know a Google Cloud client is what YouTube
# Music wants — and the GUI shows the same text to someone who has never opened
# a terminal.
_MISSING_CLIENT_FILE_HINT = (
    "Missing client_secret_*.json in data/. Get one at "
    "https://console.cloud.google.com/apis/credentials "
    "-> Create Credentials -> OAuth client ID -> "
    "TVs and Limited Input devices, save it to data/, "
    "or pass --client-id/--client-secret."
)


def require_client_secret(data_dir: Path) -> tuple[str, str]:
    """The client id/secret pair in `data_dir`, or `ValueError` saying why there is none.

    One exception type whatever went wrong, so a caller that is not a terminal
    — the sidecar answering `auth.begin`, which cannot print and exit — has a
    single thing to catch. `read_client_secret` keeps its own narrow types,
    because its callers are callers with a question about a directory.
    """
    try:
        return read_client_secret(data_dir)
    except FileNotFoundError:
        raise ValueError(_MISSING_CLIENT_FILE_HINT) from None
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f"Could not parse 'client_id' and 'client_secret' from client_secret_*.json: {exc}"
        ) from exc


def _write_synthetic_client_secret(data_dir: Path, client_id: str, client_secret: str) -> Path:
    path = data_dir / "client_secret_pasted.json"
    path.write_text(
        json.dumps(
            {"installed": {"client_id": client_id, "client_secret": client_secret}}, indent=2
        ),
        encoding="utf-8",
    )
    return path


class YtmAuthError(RuntimeError):
    """A YouTube Music sign-in that could not be completed.

    An ordinary exception on purpose. The sidecar answers `auth.begin` on a
    request thread of a process that serves the rest of the GUI, and a
    `SystemExit` there is a `BaseException`: it ends the thread, the request is
    never answered, and the host's invoke — which waits for this without a
    timeout, because the op waits on a person — never settles.
    """


def sign_in_ytm(
    *, client_id: str | None = None, client_secret: str | None = None, force: bool = False
) -> Path:
    """Sign in to YouTube Music, or raise `YtmAuthError` saying why not.

    Runs the OAuth flow, which opens a browser and returns only when a person
    has finished with it — so a caller on the other side of a pipe has to be able
    to answer other requests meanwhile.
    """
    data_dir = paths.DATA_DIR
    auth_file = paths.YTM_AUTH_FILE
    data_dir.mkdir(parents=True, exist_ok=True)

    if not force and auth_file.exists():
        try:
            cid, csec = read_client_secret(data_dir)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            pass
        else:
            try:
                yt = YTMusic(
                    str(auth_file),
                    oauth_credentials=OAuthCredentials(cid, csec),  # pyright: ignore[reportUnknownArgumentType]
                )
                _ = yt._token.access_token  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
            except _AUTH_ERRORS as exc:
                _log_auth_failure(exc)
            else:
                _LOG.info("ytm auth: cached token valid")
                return auth_file

    if client_id and client_secret:
        _write_synthetic_client_secret(data_dir, client_id, client_secret)
    else:
        try:
            client_id, client_secret = require_client_secret(data_dir)
        except ValueError as exc:
            raise YtmAuthError(str(exc)) from exc

    setup_oauth(
        open_browser=True,
        filepath=str(auth_file),
        client_id=client_id,
        client_secret=client_secret,
    )

    creds = OAuthCredentials(client_id, client_secret)
    yt = YTMusic(str(auth_file), oauth_credentials=creds)
    try:
        _ = yt._token.access_token  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
    except _AUTH_ERRORS as exc:
        _log_auth_failure(exc)
        raise YtmAuthError(
            "YouTube Music authentication token is expired or revoked.\n"
            "Re-run with: tidal2ytm auth --re-auth"
        ) from exc
    _LOG.info("ytm auth: token cached")
    return auth_file


def run_ytm_auth(
    *, client_id: str | None = None, client_secret: str | None = None, force: bool = False
) -> Path:
    """`sign_in_ytm` for a terminal: a refusal is a message and an exit code.

    The whole difference from the function it wraps is what happens when the
    sign-in cannot be done, and that difference is why it exists. At a keyboard a
    person has to be told and left alone; inside the sidecar, the same refusal has
    to stay an exception, because the caller asked over a pipe and is waiting.
    """
    try:
        return sign_in_ytm(client_id=client_id, client_secret=client_secret, force=force)
    except YtmAuthError as exc:
        _LOG.error("auth failure: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


@dataclass(frozen=True)
class TidalCredentials:
    """The six keys a Tidal token file holds, each read as the type it must be.

    Public because two callers need the same reading of the same file: the CLI's
    cached-token check, and the sidecar's `favorites.list`, which refreshes and
    writes the file back. One implementation of "what the six keys must look
    like" is the only way the two cannot disagree about a token file.
    """

    token_type: str
    access_token: str
    refresh_token: str
    expiry_time: datetime.datetime | None
    user_id: int
    country_code: str


def _validated_tidal_credentials(
    data: MutableMapping[str, Any],
) -> TidalCredentials | None:
    """Return full tidal credentials when all six keys validate, else None."""
    try:
        token_type = data.get("token_type")
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expiry = parse_token_expiry(data.get("expiry_time"))
        user_id = data.get("user_id")
        country_code = data.get("country_code")
    except (AttributeError, TypeError):
        return None
    if not isinstance(token_type, str) or not token_type:
        return None
    if not isinstance(access_token, str) or not access_token:
        return None
    if not isinstance(refresh_token, str) or not refresh_token:
        return None
    if expiry is None:
        return None
    if not isinstance(user_id, int):
        return None
    if not isinstance(country_code, str) or not country_code:
        return None
    return TidalCredentials(token_type, access_token, refresh_token, expiry, user_id, country_code)


def read_tidal_credentials(token_file: Path) -> TidalCredentials | None:
    """The cached Tidal credentials, or `None` when the file does not hold all six.

    `None` rather than an exception, because absent, unreadable, non-JSON and
    incomplete all mean one thing: there is nothing here to sign in with. Every
    caller answers that the same way, and a caller that raised instead would have
    to decide what an exception means.

    The six keys are this project's shape and not `tidalapi`'s:
    `Session.save_session_to_file` writes five of them wrapped in `{"data": ...}`,
    and a file written by it fails this reading.
    """
    try:
        data: MutableMapping[str, Any] = json.loads(token_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return _validated_tidal_credentials(data)


def render_tidal_expiry(expiry: object) -> str | None:
    """Render a session's expiry as the ISO string a token file stores.

    The writer's counterpart to `tidal_expiry_iso`, which is the reader's guard:
    between them they are what keeps one string in `expiry_time`, so a caller that
    read a token and a caller that wrote one agree. A datetime without a zone is
    read as UTC, because `load_oauth_session` stores the value verbatim and a
    naive one would otherwise be written as local time.
    """
    if isinstance(expiry, datetime.datetime):
        aware = expiry if expiry.tzinfo is not None else expiry.replace(tzinfo=datetime.UTC)
        return aware.isoformat()
    return tidal_expiry_iso(expiry)


def _try_cached_tidal_token(
    session: tidalapi.Session,  # pyright: ignore[reportPrivateImportUsage]
    token_file: Path,
) -> bool:
    """Reuse the cached Tidal token when it validates; True when login is unneeded."""
    try:
        token_data: MutableMapping[str, Any] = json.loads(token_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        token_data = {}
    if not isinstance(token_data, dict):
        token_data = {}
    creds = _validated_tidal_credentials(token_data)
    if creds is not None:
        expiry = creds.expiry_time
        if token_usable(expiry, margin=TOKEN_FRESH_MARGIN):
            _LOG.info("tidal auth: cached token valid")
            return True
        try:
            session.load_oauth_session(
                creds.token_type,
                creds.access_token,
                creds.refresh_token,
                expiry,
            )
            if session.check_login():
                _LOG.info("tidal auth: cached token refreshed")
                return True
        except _AUTH_ERRORS as exc:
            _log_auth_failure(exc)
    else:
        # Legacy/incomplete cache: try a best-effort refresh when the
        # three OAuth fields are present, else fall through to login.
        token_type = token_data.get("token_type")
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        raw_expiry = token_data.get("expiry_time")
        if (
            isinstance(token_type, str)
            and isinstance(access_token, str)
            and isinstance(refresh_token, str)
        ):
            try:
                session.load_oauth_session(
                    token_type,
                    access_token,
                    refresh_token,
                    parse_token_expiry(raw_expiry),
                )
                if session.check_login():
                    _LOG.info("tidal auth: cached token refreshed")
                    return True
            except _AUTH_ERRORS as exc:
                _log_auth_failure(exc)
    print("Cached Tidal token expired. Re-authenticating…")
    return False


def run_tidal_auth(*, force: bool = False) -> Path:
    data_dir = paths.DATA_DIR
    token_file = paths.TIDAL_TOKEN_FILE
    data_dir.mkdir(parents=True, exist_ok=True)
    session = tidalapi.Session()  # pyright: ignore[reportPrivateImportUsage]
    if not force and token_file.exists() and _try_cached_tidal_token(session, token_file):
        return token_file

    link_login, login_future = session.login_oauth()
    url = f"https://{link_login.verification_uri_complete}"
    print(f"Opening Tidal authorization URL in your browser: {url}")
    webbrowser.open(url)
    try:
        login_future.result()
    except _AUTH_ERRORS as exc:
        _log_auth_failure(exc)
        raise
    _LOG.info("tidal auth: login ok")

    user = session.user
    user_id = user.id if user is not None and isinstance(user.id, int) else None
    country_raw = session.country_code
    country_code = country_raw if isinstance(country_raw, str) else None
    token_expiry = session.expiry_time
    token_file.write_text(
        json.dumps(
            {
                "token_type": session.token_type,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                # load_oauth_session stores expiry verbatim, so it may
                # already be the ISO string from the file.
                "expiry_time": render_tidal_expiry(token_expiry),
                "user_id": user_id,
                "country_code": country_code,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return token_file
