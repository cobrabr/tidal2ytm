from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import SourceTrack

if TYPE_CHECKING:
    from tidalapi.media import Track
    from tidalapi.session import Session

_PAGE_LIMIT = 9999


def _to_source_track(t: Track) -> SourceTrack:
    """Direct typed mapping; a missing attribute raises AttributeError (never swallowed)."""
    album = t.album
    artist = t.artist
    album_name = ""
    album_id = -1
    album_year: int | None = None
    if album is not None:
        album_name = album.name or ""
        if album.id is not None:
            album_id = album.id
        album_year = album.year
    return SourceTrack(
        tidal_id=t.id,
        title=t.name,
        artist=artist.name or "" if artist is not None else "",
        artists=[a.name or "" for a in t.artists or []],
        album=album_name,
        album_id=album_id,
        album_year=album_year,
        duration_sec=t.duration,
        isrc=t.isrc,
        track_num=t.track_num,
        disc_num=t.volume_num,
        version=t.version,
    )


def get_liked_tracks(session: Session) -> list[SourceTrack]:
    results: list[SourceTrack] = []
    offset = 0
    # Session.user is typed as a pre-login union; post-login it exposes favorites.
    user: Any = session.user
    while True:
        page: list[Track] = user.favorites.tracks(limit=_PAGE_LIMIT, offset=offset)
        if not page:
            break
        results.extend(_to_source_track(t) for t in page)
        offset += len(page)
    return results
