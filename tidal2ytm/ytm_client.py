"""
ytm_client.py — Authenticated YTMusic client construction.

The TVHTML5 default client context plus the patched POST that swaps WEB_REMIX
and strips auth headers for unauthenticated read endpoints (/search?, /player?)
is load-bearing; see docs/superpowers/plans/2026-09-17-fix-all-review-findings.md
(Task 11) before touching this logic.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import requests
from tidalapi.exceptions import TidalAPIError
from ytmusicapi import YTMusic

from . import paths as paths_mod
from .auth import read_client_secret
from .errors import PlanNotFoundError

# Same narrow auth/network failure surface as auth.py and cli.py (same list in all three).
_AUTH_ERRORS: tuple[type[BaseException], ...] = (  # pyright: ignore[reportUnknownVariableType]
    OSError,
    ValueError,
    KeyError,
    TypeError,
    requests.RequestException,
    TidalAPIError,
)


def _patched_post(yt: YTMusic, original_post: Callable[..., Any]) -> Callable[..., Any]:
    """POST wrapper: strip auth headers and use WEB_REMIX for read endpoints.

    Load-bearing: see the module docstring before changing the client-context
    swap or the header stripping.
    """

    def patched_post(url: str, *args: Any, **kwargs: Any) -> Any:
        is_unauth = "/search?" in url or "/player?" in url
        if is_unauth:
            import copy
            import time

            if "headers" in kwargs:
                headers = kwargs["headers"].copy()
                headers.pop("authorization", None)
                headers.pop("X-Goog-Request-Time", None)
                kwargs["headers"] = headers

            original_client = yt.context["context"]["client"].copy()
            yt.context["context"]["client"].update(
                {
                    "clientName": "WEB_REMIX",
                    "clientVersion": "1." + time.strftime("%Y%m%d", time.gmtime()) + ".01.00",
                }
            )

            if "json" in kwargs and isinstance(kwargs["json"], dict):
                kwargs["json"] = copy.deepcopy(kwargs["json"])  # pyright: ignore[reportUnknownArgumentType]
                body = kwargs["json"]  # pyright: ignore[reportUnknownVariableType]
                if "context" in body and "client" in body["context"]:
                    body["context"]["client"].update(  # pyright: ignore[reportUnknownMemberType]
                        {
                            "clientName": "WEB_REMIX",
                            "clientVersion": "1."
                            + time.strftime("%Y%m%d", time.gmtime())
                            + ".01.00",
                        }
                    )
            try:
                return original_post(url, *args, **kwargs)
            finally:
                yt.context["context"]["client"].update(original_client)
        else:
            return original_post(url, *args, **kwargs)

    return patched_post


class YTMClient:
    """Builds a logged-in YTMusic client with the TVHTML5/WEB_REMIX patched session."""

    def __init__(self, *, auth_file: Path | None = None, data_dir: Path | None = None) -> None:
        """Path overrides default to the live ``paths`` module at login time."""
        self._auth_file = auth_file
        self._data_dir = data_dir

    def login(self) -> YTMusic:
        """Build the client from the cached OAuth token and patch its session.

        Raises PlanNotFoundError with printed guidance when the token file or
        the client secrets file is missing or the token is expired/revoked.
        """
        from ytmusicapi import OAuthCredentials

        auth_file = self._auth_file if self._auth_file is not None else paths_mod.YTM_AUTH_FILE
        data_dir = self._data_dir if self._data_dir is not None else paths_mod.DATA_DIR

        if not os.path.exists(auth_file):
            print(
                f"'{auth_file}' not found.\n"
                "Run once to create it:\n"
                "  uv run tidal2ytm auth\n"
                "Or follow the OAuth setup at:\n"
                "  https://console.cloud.google.com/apis/credentials "
                "(TVs and Limited Input devices)"
            )
            raise PlanNotFoundError(
                f"'{auth_file}' not found. Run once to create it:\n  uv run tidal2ytm auth"
            )

        # Locate the client secrets file via the single shared reader.
        try:
            client_id, client_secret = read_client_secret(data_dir)
        except FileNotFoundError as exc:
            raise PlanNotFoundError(str(exc)) from exc
        except RuntimeError as exc:
            raise PlanNotFoundError(str(exc)) from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PlanNotFoundError(f"Error reading client secrets file: {exc}") from exc

        creds = OAuthCredentials(client_id, client_secret)  # pyright: ignore[reportUnknownArgumentType]
        yt = YTMusic(str(auth_file), oauth_credentials=creds)

        # Probe the token immediately so an expired/revoked refresh token surfaces
        # here with a clear message rather than as a cryptic KeyError mid-run.
        try:
            _ = yt._token.access_token  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
        except _AUTH_ERRORS:
            print(
                "Error: YouTube Music authentication token is expired or revoked.\n"
                "Re-authenticate by running:\n"
                "\n"
                "  uv run tidal2ytm auth --re-auth"
            )
            raise PlanNotFoundError(
                "Error: YouTube Music authentication token is expired or revoked.\n"
                "Re-authenticate by running:\n\n  uv run tidal2ytm auth --re-auth"
            ) from None

        # Use TVHTML5 clientName by default so authenticated calls succeed.
        yt.context["context"]["client"].update(
            {"clientName": "TVHTML5", "clientVersion": "7.20230924.01.00"}
        )

        # Patch _session.post to strip auth headers and use WEB_REMIX for read endpoints.
        original_post = cast(Callable[..., Any], yt._session.post)  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage]
        yt._session.post = _patched_post(yt, original_post)  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage, reportAttributeAccessIssue]

        return yt
