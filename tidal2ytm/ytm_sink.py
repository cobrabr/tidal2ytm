from __future__ import annotations
import time
from ytmusicapi import YTMusic


def add_track_to_library(yt: YTMusic, video_id: str, title: str, dry_run: bool = False) -> bool:
    """
    Adds a single track to the authenticated user's YTM library.
    Returns True on success, False on any failure.

    Uses get_watch_playlist to retrieve the feedbackToken required by
    edit_song_library_status — rate_song/LikeStatus.LIKE only thumbs-up
    a track and does NOT add it to the library.
    """
    if not video_id:
        return False
    if dry_run:
        print(f"[DRY RUN] Would add to library: {title} → {video_id}")
        return True
    try:
        watch = yt.get_watch_playlist(videoId=video_id, limit=1)
        tracks = watch.get("tracks") or []
        if not tracks:
            print(f"[ERROR] get_watch_playlist returned no tracks for: {title} ({video_id})")
            return False
        feedback_tokens = tracks[0].get("feedbackTokens") or {}
        add_token = feedback_tokens.get("add")
        if not add_token:
            print(f"[ERROR] No library-add token available for: {title} ({video_id})")
            return False
        yt.edit_song_library_status([add_token])
        time.sleep(0.3)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to add {title} to library: {e}")
        return False
