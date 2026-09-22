from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import sys
import webbrowser
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, Literal, cast, overload

import requests
import tidalapi
from tidalapi.exceptions import TidalAPIError
from ytmusicapi import YTMusic

from . import auth as auth_mod
from .errors import Tidal2YtmError
from .paths import DATA_DIR, PLAN_FILE, TIDAL_TOKEN_FILE, YTM_AUTH_FILE
from .style import STATUS_STYLE

if TYPE_CHECKING:
    from tidalapi.session import Session

# Same narrow auth/network failure surface as auth.py (same list in both files).
_AUTH_ERRORS: tuple[type[BaseException], ...] = (  # pyright: ignore[reportUnknownVariableType]
    OSError,
    ValueError,
    KeyError,
    TypeError,
    requests.RequestException,
    TidalAPIError,
)


def _save_tidal_token(session: Session) -> None:
    expiry_time = session.expiry_time
    # tidalapi stores back whatever expiry we passed to load_oauth_session,
    # which may be the ISO string from the file rather than a datetime.
    if isinstance(expiry_time, datetime.datetime) and expiry_time.tzinfo is None:
        expiry_time = expiry_time.replace(tzinfo=datetime.UTC)
    expiry_str = (
        expiry_time.isoformat() if isinstance(expiry_time, datetime.datetime) else expiry_time
    )
    user = session.user
    with open(TIDAL_TOKEN_FILE, "w") as f:
        json.dump(
            {
                "token_type": session.token_type,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expiry_time": expiry_str,
                "user_id": user.id if user is not None else None,
                "country_code": session.country_code,
            },
            f,
            indent=2,
        )


@contextlib.contextmanager
def wait_status(thing: str) -> Generator[None, None, None]:
    """Show a '<thing>…' wait spinner on TTY, plain print otherwise."""
    from .style import (
        SPINNER_NAME,
        SPINNER_REFRESH_PER_SECOND,
        SPINNER_SPEED,
        SPINNER_STYLE,
    )

    text = f"{thing}…"
    if sys.stdout.isatty():
        from rich.console import Console

        with Console().status(
            text,
            spinner=SPINNER_NAME,
            spinner_style=SPINNER_STYLE,
            speed=SPINNER_SPEED,
            refresh_per_second=SPINNER_REFRESH_PER_SECOND,
        ):
            yield
    else:
        print(text)
        yield


@overload
def _tidal_login(*, login: Literal[True] = ...) -> Session: ...  # pyright: ignore[reportUnusedFunction]
@overload
def _tidal_login(*, login: Literal[False]) -> Session | None: ...  # pyright: ignore[reportUnusedFunction]
def _tidal_login(*, login: bool = True) -> Session | None:  # pyright: ignore[reportUnusedFunction]
    """Build a Tidal session; with login=False return None instead of logging in.

    The planning TUI uses login=False at startup so a stale or missing token
    never triggers a slow login or a browser flow before the first menu.
    """
    session = tidalapi.Session()  # pyright: ignore[reportPrivateImportUsage]
    if os.path.exists(TIDAL_TOKEN_FILE):
        token_data: dict[str, Any] = {}
        try:
            with open(TIDAL_TOKEN_FILE) as f:
                loaded: Any = json.load(f)
                if isinstance(loaded, dict):
                    token_data = cast(dict[str, Any], loaded)
        except (OSError, ValueError):
            pass
        expiry_time = auth_mod.parse_token_expiry(token_data.get("expiry_time"))
        user_id = token_data.get("user_id")
        country_code = token_data.get("country_code")
        token_type = token_data.get("token_type")
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        has_full_schema = (
            expiry_time is not None
            and isinstance(user_id, int)
            and isinstance(country_code, str)
            and country_code != ""
            and isinstance(token_type, str)
            and token_type != ""
            and isinstance(access_token, str)
            and access_token != ""
            and isinstance(refresh_token, str)
            and refresh_token != ""
        )
        if (
            has_full_schema
            and expiry_time is not None
            and isinstance(user_id, int)
            and auth_mod.token_usable(expiry_time, margin=auth_mod.TOKEN_FRESH_MARGIN)
        ):
            # Token is far from expiring: hydrate the session locally instead
            # of the validating round-trip. A revoked token still surfaces on
            # first use via tidalapi's reactive refresh.
            from tidalapi.user import LoggedInUser

            session.token_type = token_type
            session.access_token = access_token
            session.refresh_token = refresh_token
            session.expiry_time = expiry_time
            session.country_code = country_code
            session.locale = "en_US"
            session.user = LoggedInUser(session, user_id)
            return session
        if not login:
            return None
        with contextlib.suppress(*_AUTH_ERRORS):
            if (
                isinstance(token_type, str)
                and isinstance(access_token, str)
                and isinstance(refresh_token, str)
            ):
                session.load_oauth_session(
                    token_type,
                    access_token,
                    refresh_token,
                    expiry_time,
                )
                if session.check_login():
                    # Persist the (possibly refreshed) token so the next launch
                    # skips the refresh round-trip while it is still valid.
                    _save_tidal_token(session)
                    return session
        print("Cached token expired. Re-authenticating…")
    if not login:
        return None

    link_login, login_future = session.login_oauth()
    url = f"https://{link_login.verification_uri_complete}"
    print(f"Opening Tidal authorization URL in your browser: {url}")
    webbrowser.open(url)
    login_future.result()

    _save_tidal_token(session)
    return session


# Public seam for the planning TUI and tests; keeps the underscore name private.
tidal_login = _tidal_login


def _ytm_login() -> YTMusic:
    """Thin wrapper over YTMClient using this module's (patchable) path globals."""
    from .ytm_client import YTMClient

    return YTMClient(auth_file=YTM_AUTH_FILE, data_dir=DATA_DIR).login()


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_transfer(args: argparse.Namespace) -> None:
    from .transfer import run_transfer

    # Determine scope
    track_id = getattr(args, "track", None)
    album_id = getattr(args, "album", None)
    artist_id = getattr(args, "artist", None)
    all_tracks = getattr(args, "all", False)

    with wait_status("Authenticating with YouTube Music"):
        yt = _ytm_login()
    run_transfer(
        yt,
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

    quit_app = run_review(
        status_filter=status_filter,
        artist_match_id=getattr(args, "artist", None),
        album_match_id=getattr(args, "album", None),
        plan_path=PLAN_FILE,
    )
    if quit_app:
        # Confirmed app-level quit from inside the review TUI.
        raise SystemExit(0)


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
    from .plan_io import iter_tracks_filtered, load_plan, update_plan_meta

    console = Console()

    if not PLAN_FILE.exists():
        console.print("No transfer plan found. Run [bold]tidal2ytm[/bold] to build one first.")
        return

    plan: dict[str, Any] = load_plan(PLAN_FILE)
    meta: dict[str, Any] = plan.get("meta", {})  # type: ignore[assignment]

    import os

    mtime = os.path.getmtime(PLAN_FILE)
    last_updated = datetime.datetime.fromtimestamp(mtime).astimezone().strftime("%Y-%m-%d %H:%M")

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
        # Recompute from tracks instead of trusting possibly-stale [meta].
        update_plan_meta(plan)
        meta = plan.get("meta", {})
        total = meta.get("total_tracks", 0)
        counts = {
            TrackStatus.TRANSFERRED.value: meta.get("transferred", 0),
            TrackStatus.PENDING.value: meta.get("pending", 0),
            TrackStatus.NEEDS_REVIEW.value: meta.get("needs_review", 0),
            TrackStatus.SKIP.value: meta.get("skip", 0),
            TrackStatus.FAILED.value: meta.get("failed", 0),
        }
        console.print(f"Total tracks: {total}")

    status_order = (
        TrackStatus.TRANSFERRED.value,
        TrackStatus.PENDING.value,
        TrackStatus.NEEDS_REVIEW.value,
        TrackStatus.SKIP.value,
        TrackStatus.FAILED.value,
    )
    console.print()
    for status in status_order:
        style = STATUS_STYLE.get(status, "")
        count = counts.get(status, 0)
        line = Text(f"  {status + ':':16} {count:>4}")
        line.stylize(style)
        console.print(line)

    # Show needs_review detail
    nr_count = counts.get(TrackStatus.NEEDS_REVIEW.value, 0)
    if nr_count:
        console.print(f"\n[cyan]needs_review ({nr_count}):[/cyan]")
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
    sub = parser.add_subparsers(dest="command", required=False)

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
    if args.command is None:
        from .planning import run_planning

        try:
            run_planning(plan_path=PLAN_FILE)
        except (KeyboardInterrupt, EOFError):
            print("\nPlanning session ended.")
        return
    try:
        args.func(args)
    except Tidal2YtmError as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
