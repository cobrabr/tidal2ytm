from __future__ import annotations

import contextlib
import datetime
import json
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


def tidal_cache_valid(token_file: Path) -> bool:
    """Offline check: cached Tidal token exists and is unexpired. Never raises."""
    try:
        data: MutableMapping[str, Any] = json.loads(token_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    return token_usable(parse_token_expiry(data.get("expiry_time")))


def ytm_cache_valid(auth_file: Path) -> bool:
    """Offline check: cached YTM token exists and is usable. Never raises.

    A non-expired `expires_at` counts, and so does a stored `refresh_token`
    (refreshable credentials stay valid offline even when expired).
    """
    try:
        data: MutableMapping[str, Any] = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    try:
        expires_at = int(data.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at > int(time.time()):
        return True
    refresh = data.get("refresh_token")
    return isinstance(refresh, str) and bool(refresh.strip())


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
            with contextlib.suppress(*_AUTH_ERRORS):
                yt = YTMusic(
                    str(auth_file),
                    oauth_credentials=OAuthCredentials(cid, csec),  # pyright: ignore[reportUnknownArgumentType]
                )
                _ = yt._token.access_token  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
                return auth_file

    if client_id and client_secret:
        _write_synthetic_client_secret(data_dir, client_id, client_secret)
    else:
        try:
            client_id, client_secret = read_client_secret(data_dir)
        except FileNotFoundError:
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
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(
                "Error: Could not parse 'client_id' and 'client_secret' "
                f"from client_secret_*.json: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

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
    except _AUTH_ERRORS:
        print(
            "Error: YouTube Music authentication token is expired or revoked.\n"
            "Re-run with: tidal2ytm auth --re-auth",
            file=sys.stderr,
        )
        sys.exit(1)
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


def run_tidal_auth(*, force: bool = False) -> Path:
    data_dir = paths.DATA_DIR
    token_file = paths.TIDAL_TOKEN_FILE
    data_dir.mkdir(parents=True, exist_ok=True)
    session = tidalapi.Session()  # pyright: ignore[reportPrivateImportUsage]
    if not force and token_file.exists():
        try:
            token_data: MutableMapping[str, Any] = json.loads(
                token_file.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            token_data = {}
        if not isinstance(token_data, dict):
            token_data = {}
        creds = _validated_tidal_credentials(token_data)
        if creds is not None:
            token_type, access_token, refresh_token, expiry, _user_id, _country = creds
            if token_usable(expiry, margin=TOKEN_FRESH_MARGIN):
                return token_file
            with contextlib.suppress(*_AUTH_ERRORS):
                session.load_oauth_session(
                    token_type,
                    access_token,
                    refresh_token,
                    expiry,
                )
                if session.check_login():
                    return token_file
        else:
            with contextlib.suppress(*_AUTH_ERRORS):
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
                    session.load_oauth_session(
                        token_type,
                        access_token,
                        refresh_token,
                        parse_token_expiry(raw_expiry),
                    )
                    if session.check_login():
                        return token_file
        print("Cached Tidal token expired. Re-authenticating…")

    link_login, login_future = session.login_oauth()
    url = f"https://{link_login.verification_uri_complete}"
    print(f"Opening Tidal authorization URL in your browser: {url}")
    webbrowser.open(url)
    login_future.result()

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
