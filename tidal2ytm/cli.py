from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import webbrowser
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import tidalapi
from ytmusicapi import YTMusic

from . import auth as auth_mod
from .paths import DATA_DIR, PLAN_FILE, TIDAL_TOKEN_FILE, YTM_AUTH_FILE

if TYPE_CHECKING:
    from tidalapi.session import Session


def _tidal_login() -> Session:
    session = tidalapi.Session()  # pyright: ignore[reportPrivateImportUsage]
    if os.path.exists(TIDAL_TOKEN_FILE):
        with open(TIDAL_TOKEN_FILE) as f:
            token_data = json.load(f)
        with contextlib.suppress(Exception):
            session.load_oauth_session(
                token_data["token_type"],
                token_data["access_token"],
                token_data["refresh_token"],
                token_data.get("expiry_time"),
            )
            if session.check_login():
                return session
        print("Cached token expired. Re-authenticating…")

    link_login, login_future = session.login_oauth()
    url = f"https://{link_login.verification_uri_complete}"
    print(f"Opening Tidal authorization URL in your browser: {url}")
    webbrowser.open(url)
    login_future.result()

    with open(TIDAL_TOKEN_FILE, "w") as f:
        json.dump(
            {
                "token_type": session.token_type,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expiry_time": session.expiry_time.isoformat() if session.expiry_time else None,
            },
            f,
            indent=2,
        )
    return session


def _ytm_login() -> YTMusic:  # noqa: C901
    if not os.path.exists(YTM_AUTH_FILE):
        print(
            f"'{YTM_AUTH_FILE}' not found.\n"
            "Run once to create it:\n"
            "  uv run tidal2ytm auth\n"
            "Or follow the OAuth setup at:\n"
            "  https://console.cloud.google.com/apis/credentials "
            "(TVs and Limited Input devices)"
        )
        sys.exit(1)

    # Locate the client secrets file in data/
    secret_files = list(DATA_DIR.glob("client_secret_*.json"))
    if not secret_files:
        print(
            "Error: Google Cloud client secrets JSON file not found in 'data/' "
            "directory.\n"
            "Run `uv run tidal2ytm auth` for setup, or download the client "
            "secrets JSON from Google Cloud Console and save it in 'data/' "
            "(e.g. 'data/client_secret_<details>.json')."
        )
        sys.exit(1)

    client_secret_file = secret_files[0]
    client_id = None
    client_secret = None
    try:
        with open(client_secret_file, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
            for key in ["installed", "web"]:
                if key in data:
                    client_id = data[key].get("client_id")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    client_secret = data[key].get("client_secret")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    break
            if not client_id or not client_secret:
                for val in data.values():  # pyright: ignore[reportUnknownVariableType]
                    if isinstance(val, dict) and "client_id" in val and "client_secret" in val:
                        client_id = val["client_id"]  # pyright: ignore[reportUnknownVariableType]
                        client_secret = val["client_secret"]  # pyright: ignore[reportUnknownVariableType]
                        break
    except Exception as e:
        print(f"Error reading client secrets file '{client_secret_file.name}': {e}")
        sys.exit(1)

    if not client_id or not client_secret:
        print(
            f"Error: Could not parse 'client_id' and 'client_secret' "
            f"from '{client_secret_file.name}'."
        )
        sys.exit(1)

    from ytmusicapi import OAuthCredentials

    creds = OAuthCredentials(
        cast(str, client_id),  # pyright: ignore[reportUnknownArgumentType]
        cast(str, client_secret),  # pyright: ignore[reportUnknownArgumentType]
    )
    yt = YTMusic(str(YTM_AUTH_FILE), oauth_credentials=creds)

    # Probe the token immediately so an expired/revoked refresh token surfaces
    # here with a clear message rather than as a cryptic KeyError mid-run.
    try:
        _ = yt._token.access_token  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
    except (KeyError, Exception):
        print(
            "Error: YouTube Music authentication token is expired or revoked.\n"
            "Re-authenticate by running:\n"
            "\n"
            "  uv run tidal2ytm auth --re-auth"
        )
        sys.exit(1)

    # Use TVHTML5 clientName by default so authenticated calls succeed.
    yt.context["context"]["client"].update(
        {"clientName": "TVHTML5", "clientVersion": "7.20230924.01.00"}
    )

    # Patch _session.post to strip auth headers and use WEB_REMIX for read endpoints.
    original_post = cast(Callable[..., Any], yt._session.post)  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage]

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

    yt._session.post = patched_post  # pyright: ignore[reportUnknownMemberType, reportPrivateUsage, reportAttributeAccessIssue]

    return yt


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_plan(args: argparse.Namespace) -> None:
    from .plan import run_plan

    run_plan(_tidal_login(), _ytm_login(), plan_path=PLAN_FILE, force=args.force)


def cmd_transfer(args: argparse.Namespace) -> None:
    from .transfer import run_transfer

    # Determine scope
    track_id = getattr(args, "track", None)
    album_id = getattr(args, "album", None)
    artist_id = getattr(args, "artist", None)
    all_tracks = getattr(args, "all", False)

    run_transfer(
        _ytm_login(),
        track_id=track_id,
        album_match_id=album_id,
        artist_match_id=artist_id,
        all_tracks=all_tracks,
        dry_run=args.dry_run,
        include_needs_review=args.include_needs_review,
        plan_path=PLAN_FILE,
    )


def cmd_review(args: argparse.Namespace) -> None:
    from .models import TrackStatus
    from .review import run_review

    # Status filter
    status_filter = None
    for flag, ts in [
        ("needs_review", TrackStatus.NEEDS_REVIEW),
        ("pending", TrackStatus.PENDING),
        ("failed", TrackStatus.FAILED),
        ("skip", TrackStatus.SKIP),
        ("transferred", TrackStatus.TRANSFERRED),
    ]:
        if getattr(args, flag, False):
            status_filter = ts
            break

    run_review(
        status_filter=status_filter,
        artist_match_id=getattr(args, "artist", None),
        album_match_id=getattr(args, "album", None),
        plan_path=PLAN_FILE,
    )


def cmd_auth(args: argparse.Namespace) -> None:
    do_ytm = not getattr(args, "tidal_only", False)
    do_tidal = not getattr(args, "ytm_only", False)
    force = bool(getattr(args, "re_auth", False))
    if do_ytm:
        auth_mod.run_ytm_auth(
            client_id=getattr(args, "client_id", None),
            client_secret=getattr(args, "client_secret", None),
            force=force,
        )
    if do_tidal:
        auth_mod.run_tidal_auth(force=force)


def cmd_status(args: argparse.Namespace) -> None:
    from rich.console import Console
    from rich.text import Text

    from .models import TrackStatus
    from .plan_io import iter_tracks_filtered, load_plan

    console = Console()

    if not PLAN_FILE.exists():
        console.print("No transfer plan found. Run [bold]tidal2ytm plan[/bold] first.")
        return

    plan: dict[str, Any] = load_plan(PLAN_FILE)
    meta: dict[str, Any] = plan.get("meta", {})  # type: ignore[assignment]

    import os

    mtime = os.path.getmtime(PLAN_FILE)
    from datetime import datetime

    last_updated = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

    artist_filter = getattr(args, "artist", None)
    album_filter = getattr(args, "album", None)

    console.print(f"Transfer plan: {PLAN_FILE}  (last updated: {last_updated})")

    if artist_filter or album_filter:
        # Scoped: compute counts from filtered tracks
        tracks = list(
            iter_tracks_filtered(
                plan,
                artist_match_id=artist_filter,
                album_match_id=album_filter,
            )
        )
        total = len(tracks)
        from collections import Counter

        counts = Counter(t.get("status", TrackStatus.PENDING.value) for t in tracks)
        console.print(f"Total tracks (scoped): {total}")
    else:
        total = meta.get("total_tracks", 0)
        counts = {
            TrackStatus.TRANSFERRED.value: meta.get("transferred", 0),
            TrackStatus.PENDING.value: meta.get("pending", 0),
            TrackStatus.NEEDS_REVIEW.value: meta.get("needs_review", 0),
            TrackStatus.SKIP.value: meta.get("skip", 0),
            TrackStatus.FAILED.value: meta.get("failed", 0),
        }
        console.print(f"Total tracks: {total}")

    status_styles = {
        TrackStatus.TRANSFERRED.value: "on green",
        TrackStatus.PENDING.value: "",
        TrackStatus.NEEDS_REVIEW.value: "on yellow",
        TrackStatus.SKIP.value: "dim",
        TrackStatus.FAILED.value: "on red",
    }
    console.print()
    for status, style in status_styles.items():
        count = counts.get(status, 0)
        line = Text(f"  {status + ':':16} {count:>4}")
        line.stylize(style)
        console.print(line)

    # Show needs_review detail
    nr_count = counts.get(TrackStatus.NEEDS_REVIEW.value, 0)
    if nr_count:
        console.print(f"\n[yellow]needs_review ({nr_count}):[/yellow]")
        for t in iter_tracks_filtered(
            plan,
            status=TrackStatus.NEEDS_REVIEW,
            artist_match_id=artist_filter,
            album_match_id=album_filter,
        ):
            # find album match_id for this track
            alb_id = ""
            for artist in plan.get("artists", []):
                for album in artist.get("albums", []):
                    for tr in album.get("tracks", []):
                        if tr.get("tidal_id") == t.get("tidal_id"):
                            alb_id = album.get("match_id", "")
            conf_overall = t.get("confidence", {}).get("overall", 0.0)
            vid = t.get("yt_video_id", "")
            method = t.get("match_method", "none")
            console.print(f"  [dim]{alb_id}[/dim]")
            console.print(f"    {t.get('title', '?'):<36} {vid:<13} {method} @ {conf_overall:.2f}")


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tidal2ytm",
        description="Transfer liked tracks from Tidal to YouTube Music, accurately.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- plan ---
    p_plan = sub.add_parser("plan", help="Build or update the transfer plan.")
    p_plan.add_argument(
        "--force", action="store_true", help="Overwrite better matches without prompting."
    )
    p_plan.set_defaults(func=cmd_plan)

    # --- transfer ---
    p_t = sub.add_parser("transfer", help="Transfer tracks to YouTube Music library.")
    scope = p_t.add_mutually_exclusive_group(required=True)
    scope.add_argument("--track", metavar="VIDEO_ID", help="11-char YouTube video ID.")
    scope.add_argument(
        "--album", metavar="MATCH_ID", help="Album match_id (e.g. jethro-tull/war-child)."
    )
    scope.add_argument("--artist", metavar="MATCH_ID", help="Artist match_id (e.g. jethro-tull).")
    scope.add_argument("--all", action="store_true", help="Transfer all pending tracks.")
    p_t.add_argument("--dry-run", action="store_true")
    p_t.add_argument(
        "--include-needs-review",
        action="store_true",
        dest="include_needs_review",
        help="Include low-confidence matches in the transfer.",
    )
    p_t.set_defaults(func=cmd_transfer)

    # --- review ---
    p_r = sub.add_parser("review", help="Review matches interactively.")
    status_group = p_r.add_mutually_exclusive_group()
    status_group.add_argument("--needs-review", action="store_true", dest="needs_review")
    status_group.add_argument("--pending", action="store_true")
    status_group.add_argument("--failed", action="store_true")
    status_group.add_argument("--skip", action="store_true")
    status_group.add_argument("--transferred", action="store_true")
    status_group.add_argument("--all-statuses", action="store_true", dest="all_statuses")
    p_r.add_argument("--artist", metavar="MATCH_ID")
    p_r.add_argument("--album", metavar="MATCH_ID")
    p_r.set_defaults(func=cmd_review)

    # --- status ---
    p_s = sub.add_parser("status", help="Show transfer progress.")
    p_s.add_argument("--artist", metavar="MATCH_ID")
    p_s.add_argument("--album", metavar="MATCH_ID")
    p_s.set_defaults(func=cmd_status)

    # --- auth ---
    p_auth = sub.add_parser("auth", help="Authenticate with Tidal and YouTube Music.")
    auth_group = p_auth.add_mutually_exclusive_group()
    auth_group.add_argument("--ytm-only", action="store_true", help="Only authenticate YTM.")
    auth_group.add_argument("--tidal-only", action="store_true", help="Only authenticate Tidal.")
    p_auth.add_argument(
        "--re-auth", action="store_true", help="Force re-authentication even if cached."
    )
    p_auth.add_argument("--client-id", help="YTM OAuth client ID (bypasses client_secret file).")
    p_auth.add_argument("--client-secret", help="YTM OAuth client secret.")
    p_auth.set_defaults(func=cmd_auth)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
