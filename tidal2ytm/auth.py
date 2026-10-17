from __future__ import annotations

import datetime
import json
import logging
import sys
import time
import webbrowser
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


def _write_synthetic_client_secret(data_dir: Path, client_id: str, client_secret: str) -> Path:
    path = data_dir / "client_secret_pasted.json"
    path.write_text(
        json.dumps(
            {"installed": {"client_id": client_id, "client_secret": client_secret}}, indent=2
        ),
        encoding="utf-8",
    )
    return path


def _ensure_client_secret(
    data_dir: Path, client_id: str | None, client_secret: str | None
) -> tuple[str, str]:
    """Return a usable client-id/secret pair, writing or reading as needed."""
    if client_id and client_secret:
        _write_synthetic_client_secret(data_dir, client_id, client_secret)
        return (client_id, client_secret)
    try:
        return read_client_secret(data_dir)
    except FileNotFoundError:
        _LOG.error("auth failure: no client_secret_*.json in data/")
        print(
            "Missing client_secret_*.json in data/. Get one at "
            "https://console.cloud.google.com/apis/credentials "
            "-> Create Credentials -> OAuth client ID -> "
            "TVs and Limited Input devices, save it to data/, "
            "or pass --client-id/--client-secret.",
            file=sys.stderr,
        )
        sys.exit(1)
    except RuntimeError as exc:
        _LOG.error("auth failure: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _LOG.error("auth failure: could not parse client secret: %s", exc)
        print(
            "Error: Could not parse 'client_id' and 'client_secret' "
            f"from client_secret_*.json: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


def run_ytm_auth(
    *, client_id: str | None = None, client_secret: str | None = None, force: bool = False
) -> Path:
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

    client_id, client_secret = _ensure_client_secret(data_dir, client_id, client_secret)

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
        print(
            "Error: YouTube Music authentication token is expired or revoked.\n"
            "Re-run with: tidal2ytm auth --re-auth",
            file=sys.stderr,
        )
        sys.exit(1)
    _LOG.info("ytm auth: token cached")
    return auth_file


def _validated_tidal_credentials(
    data: MutableMapping[str, Any],
) -> tuple[str, str, str, datetime.datetime, int, str] | None:
    """Return full tidal credentials when all five keys validate, else None."""
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
    return (token_type, access_token, refresh_token, expiry, user_id, country_code)


def _tidal_expiry_iso(expiry: object) -> object:
    """Render session expiry as an aware ISO string, passing strings through."""
    if isinstance(expiry, datetime.datetime):
        aware = expiry if expiry.tzinfo is not None else expiry.replace(tzinfo=datetime.UTC)
        return aware.isoformat()
    return expiry


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
        token_type, access_token, refresh_token, expiry, _user_id, _country = creds
        if token_usable(expiry, margin=TOKEN_FRESH_MARGIN):
            _LOG.info("tidal auth: cached token valid")
            return True
        try:
            session.load_oauth_session(
                token_type,
                access_token,
                refresh_token,
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
                "expiry_time": _tidal_expiry_iso(token_expiry),
                "user_id": user_id,
                "country_code": country_code,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return token_file
