from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .logging_setup import log_call
from .models import SourceTrack

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tidalapi.media import Track
    from tidalapi.session import Session

_PAGE_LIMIT = 9999

_LOG = logging.getLogger(__name__)


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
        track_num=t.track_num,
        disc_num=t.volume_num,
        version=t.version,
    )


def fetch_favorite_page(session: Session, offset: int, limit: int) -> list[SourceTrack]:
    """One page of favourites, at the offset and limit asked for, and nothing else.

    No de-duplication here, on purpose: this is the single owner of the
    `Track` → `SourceTrack` mapping, and the sidecar's `favorites.list` is a
    window onto Tidal's list rather than a list of its own. The host asks for one
    page at a time and upserts by `tidal_id`, so a track Tidal lists twice costs
    an idempotent write and nothing else.
    """
    # Session.user is typed as a pre-login union; post-login it exposes favorites.
    user: Any = session.user
    page: list[Track] = user.favorites.tracks(limit=limit, offset=offset)
    return [_to_source_track(t) for t in page]


def iter_favorite_pages(session: Session, limit: int) -> Iterator[list[SourceTrack]]:
    """Every page of favourites, as they arrive, each without a repeat of an earlier one.

    The `seen` set lives in this frame rather than in a caller, because a
    generator's frame persists across its yields: the set therefore spans the
    whole walk without anyone passing it around. That is what keeps the list this
    produces de-duplicated, which is what `get_favorite_tracks` needs.

    A page that is entirely repeats yields nothing and the walk continues, so the
    caller never sees an empty page standing for a page of results.
    """
    seen: set[int] = set()
    offset = 0
    while True:
        page = fetch_favorite_page(session, offset, limit)
        if not page:
            return
        offset += len(page)
        fresh: list[SourceTrack] = []
        for track in page:
            # The API can list the same track id twice; first listing wins.
            if track.tidal_id not in seen:
                seen.add(track.tidal_id)
                fresh.append(track)
        if fresh:
            yield fresh


def get_favorite_tracks(session: Session) -> list[SourceTrack]:
    """The whole favourites list in one call. What the CLI still uses."""
    return log_call(
        _LOG,
        "GET tidal favorites (favorite tracks)",
        lambda: [track for page in iter_favorite_pages(session, _PAGE_LIMIT) for track in page],
    )
