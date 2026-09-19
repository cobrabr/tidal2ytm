"""planning_search.py — Pure library search for the interactive planning TUI.

No I/O and no logins: every function filters an in-memory liked-tracks list.
"""

from __future__ import annotations

import logging
import re

from .models import SourceTrack
from .text import normalize, similarity

logger = logging.getLogger(__name__)

FUZZY_CUTOFF = 0.6

_TIDAL_LINK_RE = re.compile(r"tidal\.com/(?:browse/)?(track|album)/(\d+)", re.IGNORECASE)


def _named_haystacks(track: SourceTrack) -> list[tuple[str, str]]:
    """Searchable fields with names, for score tracing."""
    fields: list[str] = [track.title, track.artist, *track.artists, track.album]
    names = ["title", "artist", *[f"artist{i}" for i in range(len(track.artists))], "album"]
    return [(name, f) for name, f in zip(names, fields, strict=True) if f]


def search_library(tracks: list[SourceTrack], query: str) -> tuple[list[SourceTrack], bool]:
    """Containing matches first (exact hits top), else the fuzzy fallback below
    1.0. The flag reports direct results: False means the fuzzy fallback ran."""
    q = normalize(query)
    if not q:
        return list(tracks), True
    scored: list[tuple[float, SourceTrack]] = []
    contained: set[int] = set()
    for t in tracks:
        fields = _named_haystacks(t)
        if any(q in normalize(h) for _, h in fields):
            contained.add(t.tidal_id)
        _, best = max(
            ((name, similarity(h, query)) for name, h in fields),
            key=lambda pair: pair[1],
            default=("", 0.0),
        )
        scored.append((best, t))
    result: list[SourceTrack]
    direct: bool
    if contained:
        ordered = [t for score, t in scored if t.tidal_id in contained and score == 1.0]
        ordered += [t for score, t in scored if t.tidal_id in contained and score != 1.0]
        result, direct = ordered, True
    else:
        fuzzy = sorted(
            ((score, t) for score, t in scored if FUZZY_CUTOFF <= score < 1.0),
            key=lambda pair: pair[0],
            reverse=True,
        )
        result, direct = [t for _, t in fuzzy], False
    logger.debug("search scores query=%r hits=%d", query, len(result))
    return result, direct


def parse_tidal_link(raw: str) -> tuple[str, int] | None:
    """Return ("track"|"album", numeric id) for Tidal URLs, else None."""
    m = _TIDAL_LINK_RE.search(raw.strip())
    if not m:
        return None
    return (m.group(1).lower(), int(m.group(2)))


def resolve_album_link(tracks: list[SourceTrack], album_id: int) -> list[SourceTrack]:
    """Canonical album rule: the liked songs of that exact album, never the album entity."""
    return [t for t in tracks if t.album_id == album_id]
