from __future__ import annotations

import time
from typing import Any, cast

from ytmusicapi import YTMusic


def add_track_to_library(
    yt: YTMusic,
    video_id: str,
    title: str = "",
    dry_run: bool = False,
    delay: float = 0.3,
) -> bool:
    """
    Adds a single track to the authenticated user's YTM library.
    Returns True on success, False on any failure.

    Uses get_watch_playlist to retrieve the feedbackToken required by
    edit_song_library_status — rate_song/LikeStatus.LIKE only thumbs-up
    a track and does NOT add it to the library. A missing add token means
    the track is already in the library (True). A non-success status from
    edit_song_library_status is a failure (False).
    """
    if not video_id:
        return False
    if dry_run:
        print(f"[DRY RUN] Would add to library: {title} → {video_id}")
        return True
    try:
        watch: Any = yt.get_watch_playlist(videoId=video_id, limit=1)
        tracks: Any = watch.get("tracks") or []
        if tracks:
            feedback_tokens: Any = tracks[0].get("feedbackTokens") or {}
        else:
            feedback_tokens = watch.get("feedbackTokens") or {}
        add_token: Any = feedback_tokens.get("add")
        if not add_token:
            # Already in the library — nothing to add.
            return True
        result: Any = yt.edit_song_library_status([add_token])
        if isinstance(result, dict):
            payload = cast(dict[str, Any], result)
            status = cast(str | None, payload.get("status"))
            if status is not None and status != "STATUS_SUCCEEDED":
                print(f"[ERROR] Library-add failed for: {title} ({video_id}): {status}")
                return False
        if delay > 0:
            time.sleep(delay)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to add {title} to library: {e}")
        return False
