"""
plan.py — Matching orchestration and idempotent plan build/merge.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any

from ytmusicapi import YTMusic

from .matcher import match_track
from .models import (
    AlbumGroup,
    ArtistGroup,
    MatchResult,
    TrackStatus,
)
from .plan_io import (
    find_existing_match,
    load_plan,
    save_plan,
    update_plan_meta,
)
from .slugs import artist_slug, dedup_slugs
from .tidal_source import get_liked_tracks

# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def group_by_artist_album(results: list[MatchResult]) -> list[ArtistGroup]:
    """
    Group a flat list of MatchResults into ArtistGroup → AlbumGroup → tracks.

    Sort order: artist A→Z, album year ascending (ties: album name A→Z),
    then disc_num, then track_num.
    """
    from collections import defaultdict

    # artist_name → album_id → list of MatchResult
    by_artist: dict[str, dict[int, list[MatchResult]]] = defaultdict(lambda: defaultdict(list))

    for result in results:
        by_artist[result.source.artist][result.source.album_id].append(result)

    # Build ArtistGroup list, sorted A→Z
    artist_groups: list[ArtistGroup] = []
    for artist_name in sorted(by_artist.keys(), key=str.casefold):
        albums_map = by_artist[artist_name]

        # Build AlbumGroup list per artist, sorted by (year, album name)
        album_groups: list[AlbumGroup] = []
        for _album_id, tracks in albums_map.items():
            # Sort tracks by disc_num, track_num
            tracks_sorted = sorted(tracks, key=lambda r: (r.source.disc_num, r.source.track_num))
            first = tracks_sorted[0].source
            album_groups.append(
                AlbumGroup(
                    name=first.album,
                    year=first.album_year,
                    match_id="",  # filled in after dedup
                    tracks=tracks_sorted,
                )
            )

        album_groups.sort(key=lambda a: (a.year or 9999, a.name.casefold()))

        artist_groups.append(
            ArtistGroup(
                name=artist_name,
                match_id="",  # filled in after dedup
                albums=album_groups,
            )
        )

    # Compute deduplicated artist slugs
    raw_artist_slugs = [artist_slug(ag.name) for ag in artist_groups]
    deduped_artist_slugs = dedup_slugs(raw_artist_slugs)
    for ag, slug in zip(artist_groups, deduped_artist_slugs, strict=False):
        ag.match_id = slug

        # Compute deduplicated album slugs within this artist
        from .slugs import album_slug

        raw_album_slugs = [album_slug(alb.name) for alb in ag.albums]
        deduped_album_slugs = dedup_slugs(raw_album_slugs)
        for alb, aslug in zip(ag.albums, deduped_album_slugs, strict=False):
            alb.match_id = f"{slug}/{aslug}"

    return artist_groups


# ---------------------------------------------------------------------------
# Plan execution
# ---------------------------------------------------------------------------


def _match_result_to_track_dict(result: MatchResult) -> dict[str, Any]:
    """Convert a MatchResult to the flat dict structure stored in the TOML plan."""
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


def run_plan(
    tidal_session: Any,
    yt: YTMusic,
    plan_path: Path,
    force: bool = False,
) -> None:
    """
    Fetch all liked Tidal tracks, match them to YTM, and write/merge the plan file.
    """
    print("Fetching Tidal liked tracks…")
    tracks = get_liked_tracks(tidal_session)
    print(f"Found {len(tracks)} tracks.")

    print("Grouping and computing slugs…")
    # We need to run matching BEFORE grouping to have MatchResult objects.
    # But slugs require the full batch. Strategy: build artist/album slug maps
    # from SourceTracks first, then match in grouped order.

    # --- Build slug maps from source tracks (no matching yet) ---
    from collections import defaultdict

    by_artist_album: dict[str, dict[int, list[Any]]] = defaultdict(  # type: ignore[no-untyped-def]
        lambda: defaultdict(list)
    )
    for t in tracks:
        by_artist_album[t.artist][t.album_id].append(t)

    # Sorted artist names
    sorted_artists = sorted(by_artist_album.keys(), key=str.casefold)
    raw_artist_slugs = [artist_slug(a) for a in sorted_artists]
    deduped_artist_slugs = dedup_slugs(raw_artist_slugs)
    artist_slug_map: dict[str, str] = dict(zip(sorted_artists, deduped_artist_slugs, strict=False))

    # For each artist, sorted albums
    from .slugs import album_slug as _album_slug

    album_match_id_map: dict[int, str] = {}  # album_id → match_id
    for artist_name, ar_slug in artist_slug_map.items():
        albums_for_artist = list(by_artist_album[artist_name].items())

        # Sort by (year, album name)
        def _album_sort_key(item: tuple[Any, list[Any]]) -> tuple[int, str]:
            _album_id, src_tracks = item
            s = src_tracks[0]
            return (s.album_year or 9999, s.album.casefold())  # type: ignore[union-attr]

        albums_for_artist.sort(key=_album_sort_key)
        raw_album_slugs = [
            _album_slug(tracks_list[0].album) for _, tracks_list in albums_for_artist
        ]
        deduped_album_slugs = dedup_slugs(raw_album_slugs)
        for (album_id, _), alb_slug in zip(albums_for_artist, deduped_album_slugs, strict=False):
            album_match_id_map[album_id] = f"{ar_slug}/{alb_slug}"

    # --- Load existing plan ---
    plan: dict[str, Any] = {}
    if plan_path.exists():
        print(f"Loading existing plan from {plan_path}…")
        plan = load_plan(plan_path)

    # Build a dict of artist/album structure keyed by artist slug
    # We'll build the full TOML structure fresh and merge in existing statuses.
    new_artists: list[dict[str, Any]] = []
    new_count = upgraded_count = kept_count = skipped_count = 0

    for artist_name in sorted_artists:
        ar_slug = artist_slug_map[artist_name]
        albums_for_artist = list(by_artist_album[artist_name].items())

        def _album_sort_key2(item: tuple[Any, list[Any]]) -> tuple[int, str]:
            _album_id, src_tracks = item
            s = src_tracks[0]
            return (s.album_year or 9999, s.album.casefold())  # type: ignore[union-attr]

        albums_for_artist.sort(key=_album_sort_key2)

        new_albums: list[dict[str, Any]] = []

        for album_id, album_tracks in albums_for_artist:
            album_match_id = album_match_id_map[album_id]
            first_track = album_tracks[0]
            sorted_tracks = sorted(album_tracks, key=lambda t: (t.disc_num, t.track_num))

            new_track_dicts: list[dict[str, Any]] = []

            for src_track in sorted_tracks:
                existing = find_existing_match(plan, src_track.tidal_id)

                if existing and existing.get("status") == TrackStatus.TRANSFERRED.value:
                    # Skip silently — already transferred
                    skipped_count += 1
                    new_track_dicts.append(existing)
                    continue

                print(f"  Matching: {src_track.artist} - {src_track.title}")
                result = match_track(src_track, yt)
                track_dict = _match_result_to_track_dict(result)

                if existing is None:
                    new_count += 1
                    new_track_dicts.append(track_dict)
                else:
                    existing_conf = existing.get("confidence", {}).get("overall", 0.0)
                    new_conf = result.confidence.overall
                    if new_conf > existing_conf:
                        if force:
                            upgraded_count += 1
                            new_track_dicts.append(track_dict)
                        else:
                            answer = (
                                input(
                                    f"Better match found for '{src_track.title}' "
                                    f"(was: {existing.get('match_method')} @ {existing_conf:.2f}, "
                                    f"now: {result.match_method.value} @ {new_conf:.2f}). "
                                    f"Overwrite? [y/N] "
                                )
                                .strip()
                                .lower()
                            )
                            if answer == "y":
                                upgraded_count += 1
                                new_track_dicts.append(track_dict)
                            else:
                                kept_count += 1
                                new_track_dicts.append(existing)
                    else:
                        kept_count += 1
                        new_track_dicts.append(existing)

            album_entry: dict[str, Any] = {
                "name": first_track.album,
                "match_id": album_match_id,
                "tracks": new_track_dicts,
            }
            if first_track.album_year is not None:
                album_entry["year"] = first_track.album_year
            new_albums.append(album_entry)

        new_artists.append(
            {
                "name": artist_name,
                "match_id": ar_slug,
                "albums": new_albums,
            }
        )

    now_str = datetime.now().isoformat(timespec="seconds")
    tidal_user_id = 0
    with contextlib.suppress(Exception):
        tidal_user_id = tidal_session.user.id

    plan["meta"] = plan.get("meta", {})
    plan["meta"]["generated_at"] = now_str
    plan["meta"]["tidal_user_id"] = tidal_user_id
    plan["artists"] = new_artists

    update_plan_meta(plan)
    save_plan(plan, plan_path)

    print(
        f"\nPlan written to {plan_path}\n"
        f"  {new_count} new  |  {upgraded_count} upgraded  |  "
        f"{kept_count} kept  |  {skipped_count} skipped (transferred)"
    )
