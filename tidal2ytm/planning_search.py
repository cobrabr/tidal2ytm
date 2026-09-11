"""planning_search.py — Pure library search for the interactive planning TUI.

No I/O and no logins: every function filters an in-memory liked-tracks list.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from .matcher import _normalize, _similarity  # pyright: ignore[reportPrivateUsage]
from .models import SourceTrack
from .paths import DATA_DIR

SearchMode = Literal["general", "artists", "albums", "songs"]

FUZZY_CUTOFF = 0.6

_TIDAL_LINK_RE = re.compile(r"tidal\.com/(?:browse/)?(track|album)/(\d+)", re.IGNORECASE)


def _named_haystacks(track: SourceTrack, mode: SearchMode) -> list[tuple[str, str]]:
    """Searchable fields with names, for score tracing."""
    if mode == "general":
        fields: list[Any] = [track.title, track.artist, *track.artists, track.album]
        names = ["title", "artist", *[f"artist{i}" for i in range(len(track.artists))], "album"]
    elif mode == "artists":
        fields = [track.artist, *track.artists, track.title, track.version or ""]
        names = ["artist", *[f"artist{i}" for i in range(len(track.artists))], "title", "version"]
    elif mode == "albums":
        fields = [track.album]
        names = ["album"]
    else:
        fields = [track.title, track.version or ""]
        names = ["title", "version"]
    return [(name, f) for name, f in zip(names, fields, strict=True) if f]


def _log_scores(query: str, mode: SearchMode, scored: list[tuple[str, float, SourceTrack]]) -> None:
    """Append per-track best-field scores for diagnosing surprising hits."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"--- query={query!r} mode={mode} ---\n"]
    for name, score, t in scored:
        if score >= 0.5:
            value = dict(_named_haystacks(t, mode)).get(name, "")
            lines.append(
                f'score={score:.3f} field={name} value="{value}"'
                f" :: {t.artist} - {t.title} [{t.album}]\n"
            )
    with open(DATA_DIR / "search_debug.log", "a", encoding="utf-8") as fh:
        fh.writelines(lines)


def search_library(
    tracks: list[SourceTrack], query: str, mode: SearchMode
) -> tuple[list[SourceTrack], bool]:
    """Containing matches first (exact hits top), else the fuzzy fallback below
    1.0. The flag reports direct results: False means the fuzzy fallback ran."""
    q = _normalize(query)
    if not q:
        return list(tracks), True
    scored: list[tuple[float, SourceTrack]] = []
    traced: list[tuple[str, float, SourceTrack]] = []
    contained: set[int] = set()
    for t in tracks:
        fields = _named_haystacks(t, mode)
        if any(q in _normalize(h) for _, h in fields):
            contained.add(t.tidal_id)
        name, best = max(
            ((name, _similarity(h, query)) for name, h in fields),
            key=lambda pair: pair[1],
            default=("", 0.0),
        )
        scored.append((best, t))
        traced.append((name, best, t))
    if os.environ.get("TIDAL2YTM_DEBUG"):
        _log_scores(query, mode, traced)
    if contained:
        ordered = [t for score, t in scored if t.tidal_id in contained and score == 1.0]
        ordered += [t for score, t in scored if t.tidal_id in contained and score != 1.0]
        return ordered, True
    fuzzy = sorted(
        ((score, t) for score, t in scored if FUZZY_CUTOFF <= score < 1.0),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return [t for _, t in fuzzy], not fuzzy


def parse_tidal_link(raw: str) -> tuple[str, int] | None:
    """Return ("track"|"album", numeric id) for Tidal URLs, else None."""
    m = _TIDAL_LINK_RE.search(raw.strip())
    if not m:
        return None
    return (m.group(1).lower(), int(m.group(2)))


def resolve_track_link(tracks: list[SourceTrack], tidal_id: int) -> SourceTrack | None:
    for t in tracks:
        if t.tidal_id == tidal_id:
            return t
    return None


def resolve_album_link(tracks: list[SourceTrack], album_id: int) -> list[SourceTrack]:
    """Canonical album rule: the liked songs of that exact album, never the album entity."""
    return [t for t in tracks if t.album_id == album_id]
