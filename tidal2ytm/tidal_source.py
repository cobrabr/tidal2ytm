from __future__ import annotations
import tidalapi
from .models import SourceTrack


def get_liked_tracks(session: tidalapi.Session) -> list[SourceTrack]:
    raw: list[tidalapi.media.Track] = session.user.favorites.tracks(limit=9999)
    results: list[SourceTrack] = []
    for t in raw:
        album = t.album
        try:
            year = album.year
        except Exception:
            year = None
        results.append(SourceTrack(
            tidal_id=t.id,
            title=t.name,
            artist=t.artist.name if t.artist else "",
            artists=[a.name for a in t.artists] if t.artists else [],
            album=album.name if album else "",
            album_id=album.id if album else -1,
            album_year=year,
            duration_sec=t.duration,
            isrc=getattr(t, "isrc", None),
            track_num=t.track_num,
            disc_num=t.volume_num,
            version=getattr(t, "version", None),
        ))
    return results
