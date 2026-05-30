from __future__ import annotations
import time
from ytmusicapi import YTMusic
from ytmusicapi.enums import LikeStatus
from .models import MatchResult


def save_to_library(yt: YTMusic, result: MatchResult, dry_run: bool = False) -> bool:
    if result.needs_review and not result.confirmed:
        return False
    vid = result.override_video_id or result.yt_video_id
    if not vid:
        return False
    if dry_run:
        print(f"[DRY RUN] Would save: {result.source.title} \u2192 {vid}")
        return True
    try:
        yt.rate_song(vid, LikeStatus.LIKE)
        time.sleep(0.3)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save {result.source.title}: {e}")
        return False
