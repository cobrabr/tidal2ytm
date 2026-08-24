"""
transfer.py — Plan executor for tidal2ytm.

Reads from the TOML transfer plan and adds tracks to the YTM library,
scoped by --track, --album, --artist, or --all.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ytmusicapi import YTMusic

from .models import TrackStatus
from .paths import PLAN_FILE
from .plan_io import (
    find_album_by_match_id,
    find_artist_by_match_id,
    find_track_by_video_id,
    iter_tracks,
    iter_tracks_filtered,
    load_plan,
    save_plan,
    update_plan_meta,
    update_track_in_plan,
)
from .ytm_sink import add_track_to_library


def _warn_needs_review(console) -> None:
    from rich.panel import Panel

    warning = (
        "[bold yellow]⚠  WARNING: --include-needs-review is active[/bold yellow]\n\n"
        "Low-confidence matches will be transferred without\n"
        "verification. These may be wrong versions, wrong\n"
        "recordings, or unrelated songs with the same title.\n\n"
        "Run [bold]tidal2ytm review[/bold] to resolve low-confidence tracks\n"
        "before transferring.\n\n"
        "Press Enter to continue, or Ctrl+C to abort."
    )
    console.print(Panel(warning, border_style="red"))
    try:
        input()
    except KeyboardInterrupt:
        console.print("\nAborted.")
        sys.exit(0)


def run_transfer(
    yt: YTMusic,
    *,
    track_id: str | None = None,
    album_match_id: str | None = None,
    artist_match_id: str | None = None,
    all_tracks: bool = False,
    dry_run: bool = False,
    include_needs_review: bool = False,
    plan_path: Path = PLAN_FILE,
) -> None:
    from rich.console import Console
    console = Console()

    if not plan_path.exists():
        console.print(
            "[red]Error:[/red] No transfer plan found. Run [bold]tidal2ytm plan[/bold] first."
        )
        sys.exit(1)

    plan = load_plan(plan_path)

    # Resolve scope
    if track_id is not None:
        track = find_track_by_video_id(plan, track_id)
        if track is None:
            console.print(f"[red]Error:[/red] No track with video ID '{track_id}' found in plan.")
            sys.exit(1)
        in_scope = [track]
    elif album_match_id is not None:
        album = find_album_by_match_id(plan, album_match_id)
        if album is None:
            console.print(f"[red]Error:[/red] No album with match_id '{album_match_id}' found in plan.")
            sys.exit(1)
        in_scope = list(album.get("tracks", []))
    elif artist_match_id is not None:
        artist = find_artist_by_match_id(plan, artist_match_id)
        if artist is None:
            console.print(f"[red]Error:[/red] No artist with match_id '{artist_match_id}' found in plan.")
            sys.exit(1)
        in_scope = []
        for album in artist.get("albums", []):
            in_scope.extend(album.get("tracks", []))
    elif all_tracks:
        in_scope = list(iter_tracks(plan))
    else:
        # Should not reach here if CLI validates scope
        console.print(
            "[red]Error:[/red] Specify a scope:\n"
            "  --track  <youtube-video-id>         (11-char YouTube ID)\n"
            "  --album  <artist/album-slug>        e.g. jethro-tull/war-child\n"
            "  --artist <artist-slug>              e.g. jethro-tull\n"
            "  --all\n\n"
            "Run [bold]tidal2ytm status[/bold] to see available match IDs."
        )
        sys.exit(1)

    # NOOP detection
    terminal_statuses = {TrackStatus.TRANSFERRED.value, TrackStatus.SKIP.value}
    if in_scope and all(t.get("status") in terminal_statuses for t in in_scope):
        console.print("Nothing to do — all tracks in scope are already transferred or skipped.")
        sys.exit(0)

    # include-needs-review warning
    if include_needs_review:
        _warn_needs_review(console)

    # Transfer loop
    transferred = failed = skipped_review = skipped_done = 0

    for track in in_scope:
        status = track.get("status", TrackStatus.PENDING.value)
        title = track.get("title", "")
        tidal_id = track.get("tidal_id", 0)
        video_id = track.get("yt_video_id", "")

        if status in (TrackStatus.TRANSFERRED.value, TrackStatus.SKIP.value):
            skipped_done += 1
            continue

        if status == TrackStatus.NEEDS_REVIEW.value and not include_needs_review:
            console.print(f"  [yellow]⚠ Skipping (needs_review):[/yellow] {title}")
            skipped_review += 1
            continue

        if not video_id:
            console.print(f"  [red]✗ No video ID:[/red] {title}")
            continue

        console.print(f"  Adding: {title} → {video_id}")
        ok = add_track_to_library(yt, video_id, title, dry_run=dry_run)

        if dry_run:
            # No status updates in dry-run mode
            continue

        if ok:
            update_track_in_plan(plan, tidal_id, {"status": TrackStatus.TRANSFERRED.value})
            update_plan_meta(plan)
            save_plan(plan, plan_path)
            transferred += 1
        else:
            update_track_in_plan(plan, tidal_id, {"status": TrackStatus.FAILED.value})
            update_plan_meta(plan)
            save_plan(plan, plan_path)
            failed += 1

    console.print(
        f"\n[bold]Done.[/bold]  "
        f"Transferred: {transferred}  "
        f"Failed: {failed}  "
        f"Skipped (needs review): {skipped_review}  "
        f"Skipped (done): {skipped_done}"
    )
