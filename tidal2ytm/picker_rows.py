"""
picker_rows.py — Pure picker row model: flattening, toggles, viewport.

No I/O and no rendering: callers supply hits and a selection mapping and
consume the resulting ListRow table.
"""

from __future__ import annotations

from collections.abc import Container, Sequence
from dataclasses import dataclass

from .models import SourceTrack

__all__ = [
    "VA_LABEL",
    "ListRow",
    "PickerView",
    "build_rows",
    "scrollbar_thumb",
    "toggle_row",
    "toggle_scope",
    "toggle_select",
    "visible_window",
]

VA_LABEL = "Various Artists"


@dataclass
class ListRow:
    """One navigable picker row: an artist/album header or a single track."""

    kind: str  # "artist" | "album" | "disc" | "track"
    artist: str = ""
    album: str = ""
    track: SourceTrack | None = None
    indent: int = 0
    members: tuple[SourceTrack, ...] = ()
    show_artist: bool = False


@dataclass
class PickerView:
    """Everything one picker frame needs to render: title, rows, and state."""

    title: str
    title_term: str
    rows: list[ListRow]
    cursor: int
    selection: dict[int, SourceTrack]
    grouping: str
    hits_total: int
    height: int
    clearable: bool
    notice: str = ""
    committed: frozenset[int] = frozenset()


def toggle_select(selection: dict[int, SourceTrack], track: SourceTrack) -> bool:
    """Toggle one track; returns True when the track is now selected."""
    if track.tidal_id in selection:
        del selection[track.tidal_id]
        return False
    selection[track.tidal_id] = track
    return True


def _album_sort_key(t: SourceTrack) -> tuple[int, int, str]:
    """Albums by year ascending, unknown years last, then name."""
    return (t.album_year is None, t.album_year or 0, t.album.lower())


def _album_rows(artist: str, bt: list[SourceTrack], rows: list[ListRow]) -> None:
    """Append one album header plus its tracks, adding disc headers when multi-disc."""
    album = bt[0].album
    show_artist = artist == VA_LABEL
    rows.append(ListRow("album", artist=artist, album=album, indent=2, members=tuple(bt)))
    discs: dict[int, list[SourceTrack]] = {}
    for t in bt:
        discs.setdefault(t.disc_num, []).append(t)
    if len(discs) == 1:
        for t in sorted(bt, key=lambda t: (t.disc_num, t.track_num)):
            rows.append(
                ListRow(
                    "track",
                    artist=t.artist,
                    album=t.album,
                    track=t,
                    indent=4,
                    show_artist=show_artist,
                )
            )
        return
    for disc_num in sorted(discs):
        dt = sorted(discs[disc_num], key=lambda t: t.track_num)
        rows.append(ListRow("disc", artist=artist, album=album, indent=4, members=tuple(dt)))
        for t in dt:
            rows.append(
                ListRow(
                    "track",
                    artist=t.artist,
                    album=t.album,
                    track=t,
                    indent=6,
                    show_artist=show_artist,
                )
            )


def build_rows(
    hits: list[SourceTrack], grouping: str, compilations: Container[int] = frozenset()
) -> list[ListRow]:
    """Flatten hits into sorted navigable rows: artists alpha, albums by year,
    tracks by track-list order inside albums, by title otherwise. Disc headers
    appear only inside multi-disc albums when grouping by album. Compilation
    albums group under Various Artists with artist-prefixed tracks."""
    if grouping == "none":
        ordered = sorted(hits, key=lambda t: t.title.lower())
        return [
            ListRow("track", artist=t.artist, album=t.album, track=t, show_artist=True)
            for t in ordered
        ]
    rows: list[ListRow] = []
    artists: dict[str, list[SourceTrack]] = {}
    for t in hits:
        artists.setdefault(VA_LABEL if t.album_id in compilations else t.artist, []).append(t)
    for artist in sorted(artists, key=str.lower):
        atracks = artists[artist]
        rows.append(ListRow("artist", artist=artist, members=tuple(atracks)))
        if grouping == "artist":
            for t in sorted(atracks, key=lambda t: t.title.lower()):
                rows.append(
                    ListRow(
                        "track",
                        artist=t.artist,
                        album=t.album,
                        track=t,
                        indent=2,
                        show_artist=artist == VA_LABEL,
                    )
                )
            continue
        albums: dict[str, list[SourceTrack]] = {}
        for t in atracks:
            albums.setdefault(t.album, []).append(t)
        ordered_albums = sorted(albums.values(), key=lambda bt: _album_sort_key(bt[0]))
        for bt in ordered_albums:
            _album_rows(artist, bt, rows)
    return rows


def _toggle_members(selection: dict[int, SourceTrack], members: list[SourceTrack]) -> None:
    if all(t.tidal_id in selection for t in members):
        for t in members:
            del selection[t.tidal_id]
    else:
        for t in members:
            selection[t.tidal_id] = t


def toggle_row(selection: dict[int, SourceTrack], row: ListRow) -> None:
    """Space on a row: flip a track, or select-all-or-clear a header group."""
    if row.kind == "track":
        assert row.track is not None
        toggle_select(selection, row.track)
    else:
        _toggle_members(selection, list(row.members))


def toggle_scope(
    selection: dict[int, SourceTrack],
    hits: list[SourceTrack],
    artist: str | None = None,
    album: str | None = None,
) -> None:
    """Bulk toggle: all shown, or one artist/album slice (select-all-or-clear)."""
    members = [
        t
        for t in hits
        if (artist is None or t.artist == artist) and (album is None or t.album == album)
    ]
    _toggle_members(selection, members)


def visible_window(rows: Sequence[object], cursor: int, height: int) -> tuple[int, int]:
    """Viewport [start, end) of row indexes keeping the cursor visible."""
    n = len(rows)
    if n <= height:
        return (0, n)
    start = min(max(cursor - height + 1, 0), n - height)
    return (start, start + height)


def scrollbar_thumb(total: int, height: int, start: int) -> int | None:
    """Thumb offset within a height-line rail for the window starting at start.

    None when every row fits (no overflow, so no rail).
    """
    if total <= height or height <= 1:
        return None
    first = min(max(start, 0), total - height)
    return round(first / (total - height) * (height - 1))
