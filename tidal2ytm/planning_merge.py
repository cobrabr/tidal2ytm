"""
planning_merge.py — Pure match/merge helpers for the interactive planning TUI.

No TUI and no network: callers supply MatchResults and answer prompts.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from .models import MatchResult, SourceTrack, TrackStatus
from .slugs import album_slug, artist_slug, dedup_slugs

MergeAction = Literal["skip-transferred", "add-new", "keep-same", "ask"]


def match_result_to_track_dict(result: MatchResult) -> dict[str, Any]:
    """Convert a MatchResult to the flat dict stored in the TOML plan (moved from plan.py)."""
    src = result.source
    conf = result.confidence
    track: dict[str, Any] = {
        "tidal_id": src.tidal_id,
        "title": src.title,
        "artist": src.artist,
        "tidal_album": src.album,
        "tidal_isrc": src.isrc or "",
        "tidal_duration_sec": src.duration_sec,
        "tidal_track_num": src.track_num,
        "yt_video_id": result.yt_video_id or "",
        "yt_title": result.yt_title or "",
        "yt_artist": result.yt_artist or "",
        "yt_album": result.yt_album or "",
        "yt_album_track_num": result.yt_album_track_num or 0,
        "yt_isrc": result.yt_isrc or "",
        "yt_duration_sec": result.yt_duration_sec or 0,
        "match_method": result.match_method.value,
        "status": result.status.value,
    }
    if result.review_reason:
        track["review_reason"] = result.review_reason
    conf_dict: dict[str, Any] = {"overall": conf.overall}
    if conf.summary:
        conf_dict["summary"] = conf.summary
    if conf.title_similarity is not None:
        conf_dict["title_similarity"] = conf.title_similarity
    if conf.artist_similarity is not None:
        conf_dict["artist_similarity"] = conf.artist_similarity
    if conf.album_similarity is not None:
        conf_dict["album_similarity"] = conf.album_similarity
    if conf.duration_delta_sec is not None:
        conf_dict["duration_delta_sec"] = conf.duration_delta_sec
    track["confidence"] = conf_dict
    return track


def unmatched_track_dict(src: SourceTrack, reason: str) -> dict[str, Any]:
    """Track dict for a pick that could not be matched (match error or no candidates)."""
    return {
        "tidal_id": src.tidal_id,
        "title": src.title,
        "artist": src.artist,
        "tidal_album": src.album,
        "tidal_isrc": src.isrc or "",
        "tidal_duration_sec": src.duration_sec,
        "tidal_track_num": src.track_num,
        "yt_video_id": "",
        "yt_title": "",
        "yt_artist": "",
        "yt_album": "",
        "yt_album_track_num": 0,
        "yt_isrc": "",
        "yt_duration_sec": 0,
        "match_method": "none",
        "status": TrackStatus.NEEDS_REVIEW.value,
        "review_reason": reason,
        "confidence": {"overall": 0.0, "summary": reason},
    }


def iter_selection_ordered(selection: dict[int, SourceTrack]) -> list[SourceTrack]:
    """Legacy plan order: artist A-Z, album year then name, disc then track."""
    return sorted(
        selection.values(),
        key=lambda t: (
            t.artist.casefold(),
            t.album_year or 9999,
            t.album.casefold(),
            t.disc_num,
            t.track_num,
        ),
    )


def classify_track(existing: dict[str, Any] | None, new_video_id: str | None) -> MergeAction:
    """Pure merge policy: transferred is sacred, identical video IDs stay silent."""
    if existing is None:
        return "add-new"
    if existing.get("status") == TrackStatus.TRANSFERRED.value:
        return "skip-transferred"
    if (existing.get("yt_video_id") or "") == (new_video_id or ""):
        return "keep-same"
    return "ask"


def insert_track(
    plan: dict[str, Any], track_dict: dict[str, Any], album_year: int | None
) -> tuple[str, str]:
    """Insert into the hierarchy, reusing existing match_ids; returns (artist, album)."""
    artist_name: str = track_dict["artist"]
    album_name: str = track_dict["tidal_album"]
    artists = cast("list[dict[str, Any]]", plan.get("artists") or [])
    plan["artists"] = artists
    artist_entry: dict[str, Any] | None = next(
        (a for a in artists if a.get("name") == artist_name), None
    )
    if artist_entry is None:
        taken = [a.get("match_id", "") for a in artists]
        slug = dedup_slugs([*taken, artist_slug(artist_name)])[-1]
        artist_entry = {"name": artist_name, "match_id": slug, "albums": []}
        artists.append(artist_entry)
        artists.sort(key=lambda a: str(a.get("name", "")).casefold())
    albums = cast("list[dict[str, Any]]", artist_entry.get("albums") or [])
    artist_entry["albums"] = albums
    album_entry: dict[str, Any] | None = next(
        (a for a in albums if a.get("name") == album_name), None
    )
    if album_entry is None:
        taken_album = [str(a.get("match_id", "")).split("/")[-1] for a in albums]
        aslug = dedup_slugs([*taken_album, album_slug(album_name)])[-1]
        album_entry = {
            "name": album_name,
            "match_id": f"{artist_entry['match_id']}/{aslug}",
            "tracks": [],
        }
        if album_year is not None:
            album_entry["year"] = album_year
        albums.append(album_entry)
        albums.sort(key=lambda a: (a.get("year") or 9999, str(a.get("name", "")).casefold()))
    tracks = cast("list[dict[str, Any]]", album_entry.get("tracks") or [])
    album_entry["tracks"] = tracks
    tracks.append(track_dict)
    # No re-sort: the stored dict carries no disc field, so insertion order
    # (legacy plan order from iter_selection_ordered) is the order.
    return (str(artist_entry["match_id"]), str(album_entry["match_id"]))
