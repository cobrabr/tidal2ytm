"""Normalize YouTube Music payloads into the shapes the domain core reads.

`contracts/candidate.json`, `contracts/album_summary.json` and
`contracts/album_detail.json` are the one place those shapes are written down,
and both the Python and the Rust suites assert against them.

The domain core never sees a YTM field name. This module is where a YTM field
name stops, so a rename upstream touches this file and nothing else — which is
also why it is the Python side rather than the Rust side, since request and
response shaping is the code that cannot be ported without reimplementing two
unaudited API clients.

These three operations are shaped but not dispatched, because the sidecar has no
YTM client: `ops.py` answers `auth.status` and nothing else. `protocol.py`'s
`dispatch` gains nothing here and `protocol::REQUIRED_CAPABILITIES` stays
`["auth.status"]` — adding the three names would make the host's handshake
reject a sidecar that does not advertise them, and the sidecar this project
ships is that one.

Every normalizer returns `dict[str, object]`, never `dict[str, Any]`.
`json.loads` hands back `Any`, and an `Any` flows through every downstream
expression without a single complaint from pyright — which is how `expires_at`
came to be forwarded as a raw epoch integer while every gate stayed green.
`object` is what `json.loads` actually promises, and it forces each value
through a function that decides.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import cast

__all__ = ["normalize_album_detail", "normalize_album_summaries", "normalize_candidates"]


def _mappings(value: object) -> list[Mapping[str, object]]:
    """The mapping entries of a YTM list field, dropping anything that is not one.

    Every list this module reads came out of a JSON parse of somebody else's
    response, so a missing field, a `null`, and a list of strings all have to
    land on the same answer rather than raising inside the sidecar's read loop.
    """
    if not isinstance(value, list):
        return []
    entries = cast("list[object]", value)
    return [cast("Mapping[str, object]", item) for item in entries if isinstance(item, Mapping)]


def _text(value: object) -> str:
    """A YTM text field as a string, empty when absent.

    `str()` rather than an `isinstance` check because the ported scorer reads
    these the same way, and a field that arrives as a number would otherwise be
    dropped by a check the Python never made.
    """
    return "" if value is None else str(value)


def _optional_int(value: object) -> int | None:
    """A YTM numeric field as an integer, or `None` when it is not one.

    `None` and never `0`, because both callers compare the value rather than
    test it for presence: `_album_meta_score` would score "1970" and "we were
    never told" identically, and `duration_ok` would treat second 0 as a real
    duration. A bool is refused because `bool` is an `int` in Python, and
    reading it as year 1 or second 1 turns a fact about the payload into a fact
    about the catalogue.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _artist_names(raw: Mapping[str, object]) -> list[str]:
    """Artist names for a search hit or a sibling entry.

    Live search hits carry an `artists` list; older fixtures and some siblings
    carry a single `artist` string, and both score, so both are read. Empty
    names are dropped rather than kept as blanks that would then be compared as
    if they were names.
    """
    listed = _mappings(raw.get("artists"))
    names = [_text(entry.get("name")) for entry in listed]
    names = [name for name in names if name]
    if names:
        return names
    single = _text(raw.get("artist"))
    return [single] if single else []


def _mapping(value: object) -> Mapping[str, object]:
    """`value` as a mapping, or an empty one.

    A nested object that is absent, or is not an object, has no fields — which
    is what the caller would get from an empty dict anyway.
    """
    if isinstance(value, Mapping):
        return cast("Mapping[str, object]", value)
    return {}


def _candidate(raw: Mapping[str, object]) -> dict[str, object]:
    album = _mapping(raw.get("album"))
    return {
        "video_id": _text(raw.get("videoId")),
        "title": _text(raw.get("title")),
        "artists": _artist_names(raw),
        "album": _text(album.get("name")) or None,
        "album_track_num": _optional_int(raw.get("trackNumber")),
        "duration_sec": _optional_int(raw.get("duration_seconds")),
    }


def _album_summary(raw: Mapping[str, object]) -> dict[str, object]:
    names = _artist_names(raw)
    return {
        "album_id": _text(raw.get("browseId")),
        "title": _text(raw.get("title")),
        # One name where the Python takes a maximum over the whole list: this is
        # deviation 3 from the design spec's letter, so multi-artist album
        # matching is unexercised through the envelope until a real client can
        # supply the full list.
        "artist": names[0] if names else "",
        "year": _optional_int(raw.get("year")),
    }


def normalize_candidates(raw: list[Mapping[str, object]]) -> list[dict[str, object]]:
    """`search.songs` hits, in domain field names."""
    return [_candidate(item) for item in raw]


def normalize_album_summaries(raw: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """`search.albums` hits, or one album's `other_versions` shelf."""
    return [_album_summary(item) for item in raw]


def normalize_album_detail(raw: Mapping[str, object]) -> dict[str, object]:
    """`album.fetch`: one album with the two shelves ranking reads off it.

    The summary comes from the album's own fields, and the tracks and siblings
    are normalized through the same two functions the search answers use, so a
    track cannot be shaped one way on the search path and another on the fetch
    path.
    """
    return {
        "summary": _album_summary(raw),
        "tracks": normalize_candidates(_mappings(raw.get("tracks"))),
        "other_versions": normalize_album_summaries(_mappings(raw.get("other_versions"))),
    }
