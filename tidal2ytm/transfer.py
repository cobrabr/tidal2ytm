"""
transfer.py — Plan executor for tidal2ytm.

Reads from the TOML transfer plan and adds tracks to the YTM library,
scoped by --track, --album, --artist, or --all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ytmusicapi import YTMusic

from .errors import InvalidScopeError, PlanNotFoundError
from .models import TrackStatus
from .paths import PLAN_FILE
from .plan_io import (
    backup_plan,
    find_album_by_match_id,
    find_artist_by_match_id,
    find_track_by_video_id,
    iter_tracks,
    load_plan,
    save_with_meta,
    update_track_in_plan,
)
from .ytm_sink import add_track_to_library


@dataclass
class TransferCounts:
    """Outcome tallies for one ``run_transfer`` call, over in-scope tracks only.

    ``skipped`` aggregates every non-transferred, non-failed track: already
    done, needs-review (without the flag), empty video ID, and dry-run passes.
    The ``skipped_*`` fields break that aggregate down, except dry-run passes
    which fold into the aggregate only.
    """

    total: int = 0
    transferred: int = 0
    failed: int = 0
    skipped: int = 0
    skipped_review: int = 0
    skipped_done: int = 0
    skipped_empty: int = 0


def _warn_needs_review(console: Any) -> bool:
    """Show the --include-needs-review warning; False when the user aborts."""
    from rich.panel import Panel

    warning = (
        "[bold yellow]⚠  WARNING: --include-needs-review is active[/bold yellow]\n\n"
        "Low-confidence matches will be transferred without\n"
        "verification. These may be wrong versions, wrong\n"
        "recordings, or unrelated songs with the same title.\n\n"
        "Run [bold]tidal2ytm review[/bold] to resolve low-confidence tracks\n"
        "before transferring.\n\n"
        "Press Enter to continue, n to decline, or Ctrl+C to abort."
    )
    console.print(Panel(warning, border_style="red"))
    try:
        reply = input()
    except (KeyboardInterrupt, EOFError):
        console.print("\nAborted.")
        return False
    if reply.strip().lower() in ("n", "no"):
        console.print("\nAborted.")
        return False
    return True


def _resolve_scope(
    plan: dict[str, Any],
    *,
    track_id: str | None,
    album_match_id: str | None,
    artist_match_id: str | None,
    all_tracks: bool,
) -> list[dict[str, Any]]:
    """Tracks selected by exactly one scope flag; raises InvalidScopeError otherwise."""
    if track_id is not None:
        track = find_track_by_video_id(plan, track_id)
        if track is None:
            raise InvalidScopeError(f"Error: No track with video ID '{track_id}' found in plan.")
        return [track]
    if album_match_id is not None:
        album = find_album_by_match_id(plan, album_match_id)
        if album is None:
            raise InvalidScopeError(
                f"Error: No album with match_id '{album_match_id}' found in plan."
            )
        return list(album.get("tracks", []))
    if artist_match_id is not None:
        artist = find_artist_by_match_id(plan, artist_match_id)
        if artist is None:
            raise InvalidScopeError(
                f"Error: No artist with match_id '{artist_match_id}' found in plan."
            )
        in_scope: list[dict[str, Any]] = []
        for album in artist.get("albums", []):
            in_scope.extend(album.get("tracks", []))
        return in_scope
    if all_tracks:
        return list(iter_tracks(plan))
    # Should not reach here if CLI validates scope
    raise InvalidScopeError(
        "Error: Specify a scope:\n"
        "  --track  <youtube-video-id>         (11-char YouTube ID)\n"
        "  --album  <artist/album-slug>        e.g. jethro-tull/war-child\n"
        "  --artist <artist-slug>              e.g. jethro-tull\n"
        "  --all\n\n"
        "Run tidal2ytm status to see available match IDs."
    )


def _transfer_one(
    yt: YTMusic,
    plan: dict[str, Any],
    track: dict[str, Any],
    counts: TransferCounts,
    *,
    dry_run: bool,
    include_needs_review: bool,
    console: Any,
) -> bool:
    """Handle one in-scope track; True when the plan was mutated."""
    status = track.get("status", TrackStatus.PENDING.value)
    title = track.get("title", "")
    tidal_id = track.get("tidal_id", 0)
    video_id = track.get("yt_video_id", "")

    if status in (TrackStatus.TRANSFERRED.value, TrackStatus.SKIP.value):
        counts.skipped_done += 1
        counts.skipped += 1
        return False

    if status == TrackStatus.NEEDS_REVIEW.value and not include_needs_review:
        console.print(f"  [cyan]⚠ Skipping (needs_review):[/cyan] {title}")
        counts.skipped_review += 1
        counts.skipped += 1
        return False

    if not video_id:
        console.print(f"  [red]✗ No video ID:[/red] {title}")
        counts.skipped_empty += 1
        counts.skipped += 1
        return False

    console.print(f"  Adding: {title} → {video_id}")
    ok = add_track_to_library(yt, video_id, title, dry_run=dry_run)

    if dry_run:
        # No status updates in dry-run mode
        counts.skipped += 1
        return False

    if ok:
        update_track_in_plan(plan, tidal_id, {"status": TrackStatus.TRANSFERRED.value})
        counts.transferred += 1
    else:
        update_track_in_plan(plan, tidal_id, {"status": TrackStatus.FAILED.value})
        counts.failed += 1
    return True


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
) -> TransferCounts:
    from rich.console import Console

    console = Console()

    if not plan_path.exists():
        raise PlanNotFoundError("Error: No transfer plan found. Run tidal2ytm to build one first.")

    plan: dict[str, Any] = load_plan(plan_path)

    in_scope = _resolve_scope(
        plan,
        track_id=track_id,
        album_match_id=album_match_id,
        artist_match_id=artist_match_id,
        all_tracks=all_tracks,
    )

    counts = TransferCounts(total=len(in_scope))

    # NOOP detection
    terminal_statuses = {TrackStatus.TRANSFERRED.value, TrackStatus.SKIP.value}
    if in_scope and all(t.get("status") in terminal_statuses for t in in_scope):
        console.print("Nothing to do — all tracks in scope are already transferred or skipped.")
        return counts

    # include-needs-review warning
    if include_needs_review and not _warn_needs_review(console):
        return counts

    # Transfer loop: mutate the plan in memory, persist in batches.
    mutated = False
    saved_after_first_failure = False

    # One backup before mutating, so an interrupted run can be restored.
    will_mutate = any(
        t.get("status") not in terminal_statuses
        and (t.get("status") != TrackStatus.NEEDS_REVIEW.value or include_needs_review)
        for t in in_scope
    )
    if not dry_run and will_mutate:
        backup_plan(plan_path)

    for track in in_scope:  # type: ignore[assignment]
        failed_before = counts.failed
        if not _transfer_one(
            yt,
            plan,
            track,
            counts,
            dry_run=dry_run,
            include_needs_review=include_needs_review,
            console=console,
        ):
            continue
        mutated = True
        if counts.failed > failed_before and not saved_after_first_failure:
            # Persist partial progress so it survives a crash mid-loop.
            save_with_meta(plan, plan_path)
            saved_after_first_failure = True

    if mutated:
        save_with_meta(plan, plan_path)

    console.print(
        f"\n[bold]Done.[/bold]  "
        f"Transferred: {counts.transferred}  "
        f"Failed: {counts.failed}  "
        f"Skipped (needs review): {counts.skipped_review}  "
        f"Skipped (done): {counts.skipped_done}"
    )
    return counts
