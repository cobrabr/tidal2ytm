"""
plan_io.py — TOML plan serialization, URL normalization, and backup utilities.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tomllib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import tomli_w

from .errors import InvalidScopeError
from .models import TrackStatus
from .paths import PLAN_FILE

logger = logging.getLogger(__name__)

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


def extract_video_id(raw: str) -> str:
    """
    Extract and validate an 11-char YouTube video ID from any of:
      - Bare 11-char ID
      - https://www.youtube.com/watch?v=<ID>[&…]
      - https://m.youtube.com/watch?v=<ID>[&…]
      - https://youtu.be/<ID>[?…]
      - https://www.youtube.com/v/<ID>[?…]
      - https://www.youtube.com/embed/<ID>
      - https://www.youtube.com/shorts/<ID>
      - https://www.youtube.com/live/<ID>
      - https://www.youtube-nocookie.com/embed/<ID>
      - https://music.youtube.com/watch?v=<ID>[&…]

    Host matching is ``www.``/``m.``-insensitive. Raises ValueError if no
    pattern matches.
    """
    raw = raw.strip()
    if _VIDEO_ID_RE.match(raw):
        return raw

    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix) :]

    if host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        # /watch?v=ID  or  /v/ID  or  /embed/ID  or  /shorts/ID  or  /live/ID
        for path_prefix in ("/embed/", "/shorts/", "/live/", "/v/"):
            if parsed.path.startswith(path_prefix):
                vid = parsed.path[len(path_prefix) :].split("/")[0]
                break
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


_extract_video_id = extract_video_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_plan(path: Path) -> dict[str, Any]:
    """Load plan from TOML. Normalizes all yt_video_id values via extract_video_id.

    A stored ID that fails validation is cleared to ``""`` and the track is
    forced to ``needs_review`` (with a warning) instead of raising, so one
    bad hand-edit cannot block loading the whole plan.
    """
    with open(path, "rb") as f:
        plan: dict[str, Any] = tomllib.load(f)

    for track in iter_tracks(plan):
        raw_id = track.get("yt_video_id")
        if raw_id:
            try:
                track["yt_video_id"] = extract_video_id(str(raw_id))
            except ValueError:
                logger.warning(
                    "Clearing invalid yt_video_id %r (tidal_id=%r); forcing needs_review",
                    raw_id,
                    track.get("tidal_id"),
                )
                track["yt_video_id"] = ""
                track["status"] = TrackStatus.NEEDS_REVIEW.value

    return plan


def save_plan(plan: dict[str, Any], path: Path) -> None:
    """Write plan dict to TOML with the standard file header comment.

    The write is atomic: content is rendered to a sibling ``.tmp`` file and
    moved over the target with ``os.replace``, so a mid-write crash cannot
    leave a truncated plan behind. The parent directory is created as needed,
    so no import-time directory setup is required.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = plan.get("meta", {}).get(
        "generated_at", datetime.now().isoformat(timespec="seconds")
    )
    header = _PLAN_HEADER.format(generated_at=generated_at)
    body = tomli_w.dumps(plan)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(header + "\n" + body, encoding="utf-8")
    os.replace(tmp_path, path)


def save_with_meta(plan: dict[str, Any], path: Path = PLAN_FILE) -> None:
    """Recompute ``[meta]`` counts and save the plan atomically in one call."""
    update_plan_meta(plan)
    save_plan(plan, path)


def backup_plan(path: Path) -> Path:
    """
    Byte-copy the plan to a timestamped backup in the same directory.
    Returns the backup path.
    """
    ts = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
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
    """Return the raw track dict for the given yt_video_id, or None.

    The input is normalized via :func:`extract_video_id` first, so bare IDs
    and every accepted URL form all match. Unparseable input matches nothing
    (returns None). Raises InvalidScopeError when two tracks share the same ID.
    """
    try:
        normalized = extract_video_id(video_id)
    except ValueError:
        return None
    found: dict[str, Any] | None = None
    for track in iter_tracks(plan):
        if track.get("yt_video_id") == normalized:
            if found is not None:
                raise InvalidScopeError(f"Multiple tracks share video ID '{normalized}'")
            found = track
    return found


def find_album_by_match_id(plan: dict[str, Any], match_id: str) -> dict[str, Any] | None:
    """Return the raw album dict for the given match_id, or None."""
    for artist in plan.get("artists", []):
        for album in artist.get("albums", []):
            if album.get("match_id") == match_id:
                return album
    return None


def find_album_of_track(plan: dict[str, Any], tidal_id: int) -> str | None:
    """Return the album match_id that owns the given tidal_id, or None.

    Builds the full ``{tidal_id: album_match_id}`` index in a single pass per
    call, so callers never scan the plan per track.
    """
    index: dict[int, str] = {}
    for artist in plan.get("artists", []):
        for album in artist.get("albums", []):
            for track in album.get("tracks", []):
                track_id = track.get("tidal_id")
                if track_id is not None:
                    index[track_id] = str(album.get("match_id", ""))
    return index.get(tidal_id)


def find_artist_by_match_id(plan: dict[str, Any], match_id: str) -> dict[str, Any] | None:
    """Return the raw artist dict for the given match_id, or None."""
    for artist in plan.get("artists", []):
        if artist.get("match_id") == match_id:
            return artist
    return None


def update_track_in_plan(plan: dict[str, Any], tidal_id: int, updates: dict[str, Any]) -> bool:
    """Mutate the track entry in-place with the given field updates.

    Returns True when a track with ``tidal_id`` was found and updated,
    False when no such track exists (plan left untouched).
    """
    track = find_existing_match(plan, tidal_id)
    if track is None:
        return False
    track.update(updates)
    return True


def update_plan_meta(plan: dict[str, Any]) -> None:
    """Recompute and overwrite [meta] counts from current track statuses.

    Raises ValueError on unknown status strings instead of silently dropping
    them from the counts.
    """
    meta = plan.setdefault("meta", {})
    counts: dict[str, int] = {s.value: 0 for s in TrackStatus}
    total = 0
    for track in iter_tracks(plan):
        total += 1
        status = track.get("status", TrackStatus.PENDING.value)
        if status in counts:
            counts[status] += 1
        else:
            raise ValueError(f"Unknown track status: {status!r}")
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
