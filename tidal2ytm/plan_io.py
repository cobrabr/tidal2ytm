"""
plan_io.py — TOML plan serialization, URL normalization, and backup utilities.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import tomllib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import tomli_w

from .models import TrackStatus

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_PLAN_HEADER = """\
# tidal2ytm transfer plan
# Generated: {generated_at}
#
# status:       pending | transferred | skip | failed | needs_review
# match_method: isrc | duration | fuzzy | none
#
# To fix a bad match:    edit yt_video_id (bare 11-char YouTube ID) and set status = "pending"
# To skip a track:       set status = "skip"
# To retry a failed one: set status = "pending"
# To reject any match:   set yt_video_id = "" and status = "needs_review"
#
# --album and --artist flags use the match_id values shown below.
# --track uses the yt_video_id directly (11-char YouTube ID).
"""


def _extract_video_id(raw: str) -> str:
    """
    Extract and validate an 11-char YouTube video ID from any of:
      - Bare 11-char ID
      - https://www.youtube.com/watch?v=<ID>[&…]
      - https://youtu.be/<ID>[?…]
      - https://www.youtube.com/v/<ID>[?…]
      - https://music.youtube.com/watch?v=<ID>[&…]

    Raises ValueError if no pattern matches.
    """
    raw = raw.strip()
    if _VIDEO_ID_RE.match(raw):
        return raw

    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    if host in ("youtube.com", "music.youtube.com"):
        # /watch?v=ID  or  /v/ID
        if parsed.path.startswith("/v/"):
            vid = parsed.path[3:].split("/")[0]
        else:
            qs = parse_qs(parsed.query)
            vid = (qs.get("v") or [""])[0]
    elif host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0].split("?")[0]
    else:
        raise ValueError(f"Cannot parse YouTube video ID from: {raw}")

    if _VIDEO_ID_RE.match(vid):
        return vid
    raise ValueError(f"Cannot parse YouTube video ID from: {raw}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_plan(path: Path) -> dict[str, Any]:
    """Load plan from TOML. Normalizes all yt_video_id values via _extract_video_id."""
    with open(path, "rb") as f:
        plan: dict[str, Any] = tomllib.load(f)

    for track in iter_tracks(plan):
        raw_id = track.get("yt_video_id")
        if raw_id:
            with contextlib.suppress(ValueError):
                track["yt_video_id"] = _extract_video_id(str(raw_id))

    return plan


def save_plan(plan: dict[str, Any], path: Path) -> None:
    """Write plan dict to TOML with the standard file header comment."""
    generated_at = plan.get("meta", {}).get(
        "generated_at", datetime.now().isoformat(timespec="seconds")
    )
    header = _PLAN_HEADER.format(generated_at=generated_at)
    body = tomli_w.dumps(plan)
    path.write_text(header + "\n" + body, encoding="utf-8")


def backup_plan(path: Path) -> Path:
    """
    Byte-copy the plan to a timestamped backup in the same directory.
    Returns the backup path.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.parent / f"transfer_plan.{ts}.toml"
    shutil.copy2(path, backup_path)
    return backup_path


def find_existing_match(plan: dict[str, Any], tidal_id: int) -> dict[str, Any] | None:
    """Return the raw track dict for the given tidal_id, or None."""
    for track in iter_tracks(plan):
        if track.get("tidal_id") == tidal_id:
            return track
    return None


def find_track_by_video_id(plan: dict[str, Any], video_id: str) -> dict[str, Any] | None:
    """Return the raw track dict for the given yt_video_id, or None."""
    for track in iter_tracks(plan):
        if track.get("yt_video_id") == video_id:
            return track
    return None


def find_album_by_match_id(plan: dict[str, Any], match_id: str) -> dict[str, Any] | None:
    """Return the raw album dict for the given match_id, or None."""
    for artist in plan.get("artists", []):
        for album in artist.get("albums", []):
            if album.get("match_id") == match_id:
                return album
    return None


def find_artist_by_match_id(plan: dict[str, Any], match_id: str) -> dict[str, Any] | None:
    """Return the raw artist dict for the given match_id, or None."""
    for artist in plan.get("artists", []):
        if artist.get("match_id") == match_id:
            return artist
    return None


def update_track_in_plan(plan: dict[str, Any], tidal_id: int, updates: dict[str, Any]) -> None:
    """Mutate the track entry in-place with the given field updates."""
    track = find_existing_match(plan, tidal_id)
    if track is not None:
        track.update(updates)


def update_plan_meta(plan: dict[str, Any]) -> None:
    """Recompute and overwrite [meta] counts from current track statuses."""
    meta = plan.setdefault("meta", {})
    counts: dict[str, int] = {s.value: 0 for s in TrackStatus}
    total = 0
    for track in iter_tracks(plan):
        total += 1
        status = track.get("status", TrackStatus.PENDING.value)
        if status in counts:
            counts[status] += 1
    meta["total_tracks"] = total
    meta["transferred"] = counts[TrackStatus.TRANSFERRED.value]
    meta["pending"] = counts[TrackStatus.PENDING.value]
    meta["needs_review"] = counts[TrackStatus.NEEDS_REVIEW.value]
    meta["skip"] = counts[TrackStatus.SKIP.value]
    meta["failed"] = counts[TrackStatus.FAILED.value]


def iter_tracks(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Flat iterator over all track dicts in artist→album→track order."""
    for artist in plan.get("artists", []):
        for album in artist.get("albums", []):
            yield from album.get("tracks", [])


def iter_tracks_filtered(
    plan: dict[str, Any],
    *,
    status: TrackStatus | None = None,
    artist_match_id: str | None = None,
    album_match_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Filtered flat iterator. All filters are ANDed."""
    for artist in plan.get("artists", []):
        if artist_match_id and artist.get("match_id") != artist_match_id:
            continue
        for album in artist.get("albums", []):
            if album_match_id and album.get("match_id") != album_match_id:
                continue
            for track in album.get("tracks", []):
                if status and track.get("status") != status.value:
                    continue
                yield track
