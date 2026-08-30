from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .models import SourceTrack

if TYPE_CHECKING:
    from tidalapi.session import Session


def get_liked_tracks(session: Session) -> list[SourceTrack]:  # noqa: C901
    raw: Any = cast(Any, session).user.favorites.tracks(limit=9999)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    results: list[SourceTrack] = []
    for t in raw:
        album = getattr(t, "album", None)
        try:
            year = album.year if album is not None else None
        except Exception:
            year = None
        # artist handling — may be None or have None name
        artist_name = ""
        artist_obj = getattr(t, "artist", None)
        if artist_obj is not None:
            try:
                artist_name = getattr(artist_obj, "name", "") or ""
            except Exception:
                artist_name = ""
        # artists list handling — robust to MagicMock / None
        artists: list[str] = []
        raw_artists = getattr(t, "artists", None)
        if raw_artists is not None:
            try:
                # only treat as iterable if it's actually list/tuple
                if isinstance(raw_artists, (list, tuple)):
                    artists = [
                        getattr(a, "name", "")  # pyright: ignore[reportUnknownArgumentType]
                        or ""
                        for a in raw_artists  # pyright: ignore[reportUnknownVariableType]
                    ]
                else:
                    # fallback for MagicMock etc — attempt iteration but guard
                    artists = [
                        getattr(a, "name", "") or ""
                        for a in list(raw_artists)
                        if hasattr(a, "name")
                    ]
            except Exception:
                artists = []
        album_name = ""
        album_id = -1
        if album is not None:
            try:
                album_name = getattr(album, "name", "") or ""
            except Exception:
                album_name = ""
            try:
                album_id_raw = getattr(album, "id", -1)
                album_id = album_id_raw if isinstance(album_id_raw, int) else -1
            except Exception:
                album_id = -1
        tidal_id_raw = getattr(t, "id", 0)
        tidal_id = tidal_id_raw if isinstance(tidal_id_raw, int) else 0
        duration_raw = getattr(t, "duration", 0)
        duration_sec = duration_raw if isinstance(duration_raw, int) else 0
        track_num_raw = getattr(t, "track_num", 0)
        track_num = track_num_raw if isinstance(track_num_raw, int) else 0
        disc_raw = getattr(t, "volume_num", 0)
        disc_num = disc_raw if isinstance(disc_raw, int) else 0
        results.append(
            SourceTrack(
                tidal_id=tidal_id,
                title=getattr(t, "name", "") or "",
                artist=artist_name,
                artists=artists,
                album=album_name,
                album_id=album_id,
                album_year=year,
                duration_sec=duration_sec,
                isrc=getattr(t, "isrc", None),
                track_num=track_num,
                disc_num=disc_num,
                version=getattr(t, "version", None),
            )
        )
    return results
