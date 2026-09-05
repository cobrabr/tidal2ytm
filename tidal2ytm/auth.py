from __future__ import annotations

import contextlib
import json
import sys
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

import tidalapi
from ytmusicapi import OAuthCredentials, YTMusic
from ytmusicapi.setup import setup_oauth

from . import paths

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from typing import Any


def _read_client_secret(data_dir: Path) -> tuple[str, str]:
    raw: MutableMapping[str, Any] = json.loads(
        next(iter(data_dir.glob("client_secret_*.json"))).read_text(encoding="utf-8")
    )
    for key in ("installed", "web"):
        if key in raw:
            return str(raw[key]["client_id"]), str(raw[key]["client_secret"])
    for val in raw.values():
        if isinstance(val, dict) and "client_id" in val and "client_secret" in val:
            return str(val["client_id"]), str(val["client_secret"])  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
    print(
        "Error: Could not parse 'client_id' and 'client_secret' from client_secret_*.json.",
        file=sys.stderr,
    )
    sys.exit(1)


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
        secret_files = list(data_dir.glob("client_secret_*.json"))
        if secret_files:
            with contextlib.suppress(Exception):
                cid, csec = _read_client_secret(data_dir)
                yt = YTMusic(
                    str(auth_file),
                    oauth_credentials=OAuthCredentials(cid, csec),  # pyright: ignore[reportUnknownArgumentType]
                )
                _ = yt._token.access_token  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
                return auth_file

    if client_id and client_secret:
        _write_synthetic_client_secret(data_dir, client_id, client_secret)
    else:
        if not list(data_dir.glob("client_secret_*.json")):
            print(
                "Missing client_secret_*.json in data/. Get one at "
                "https://console.cloud.google.com/apis/credentials "
                "-> Create Credentials -> OAuth client ID -> "
                "TVs and Limited Input devices, save it to data/, "
                "or pass --client-id/--client-secret.",
                file=sys.stderr,
            )
            sys.exit(1)
        client_id, client_secret = _read_client_secret(data_dir)

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
    except Exception:
        print(
            "Error: YouTube Music authentication token is expired or revoked.\n"
            "Re-run with: tidal2ytm auth --re-auth",
            file=sys.stderr,
        )
        sys.exit(1)
    return auth_file


def run_tidal_auth(*, force: bool = False) -> Path:
    data_dir = paths.DATA_DIR
    token_file = paths.TIDAL_TOKEN_FILE
    data_dir.mkdir(parents=True, exist_ok=True)
    session = tidalapi.Session()  # pyright: ignore[reportPrivateImportUsage]
    if not force and token_file.exists():
        with contextlib.suppress(Exception):
            token_data: MutableMapping[str, Any] = json.loads(
                token_file.read_text(encoding="utf-8")
            )
            session.load_oauth_session(
                token_data["token_type"],
                token_data["access_token"],
                token_data["refresh_token"],
                token_data.get("expiry_time"),
            )
            if session.check_login():
                return token_file
        print("Cached Tidal token expired. Re-authenticating...")

    link_login, login_future = session.login_oauth()
    url = f"https://{link_login.verification_uri_complete}"
    print(f"Opening Tidal authorization URL in your browser: {url}")
    webbrowser.open(url)
    login_future.result()

    token_file.write_text(
        json.dumps(
            {
                "token_type": session.token_type,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expiry_time": session.expiry_time.isoformat() if session.expiry_time else None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return token_file
