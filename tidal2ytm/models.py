from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MatchMethod(StrEnum):
    ISRC = "isrc"
    DURATION = "duration"
    FUZZY = "fuzzy"
    NONE = "none"


class TrackStatus(StrEnum):
    PENDING = "pending"
    TRANSFERRED = "transferred"
    SKIP = "skip"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


@dataclass
class ConfidenceBreakdown:
    overall: float
    title_similarity: float | None = None
    artist_similarity: float | None = None
    album_similarity: float | None = None
    duration_delta_sec: int | None = None
    summary: str | None = None


def _to_int(v: Any, default: int) -> int:
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


@dataclass
class SourceTrack:
    tidal_id: int
    title: str
    artist: str
    artists: list[str]
    album: str
    album_id: int
    album_year: int | None
    duration_sec: int
    isrc: str | None
    track_num: int
    disc_num: int
    version: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceTrack:
        """Strict boundary: tidal_id must coerce to a positive int, else ValueError."""
        try:
            tidal_id = int(data.get("tidal_id", 0))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid tidal_id: {data.get('tidal_id')!r}") from exc
        if tidal_id <= 0:
            raise ValueError(f"invalid tidal_id: {data.get('tidal_id')!r}")
        raw_artists = data.get("artists")
        if raw_artists is None:
            raw_artist = data.get("artist")
            artists: list[str] = [raw_artist] if raw_artist else []
        elif isinstance(raw_artists, str):
            artists = [raw_artists]
        else:
            artists = list(raw_artists) if raw_artists else []
        artist = artists[0] if artists else data.get("artist", "") or ""
        album = data.get("album", "")
        duration_raw = data.get("duration_sec")
        if duration_raw is None:
            duration_raw = data.get("duration", 0)
        return cls(
            tidal_id=tidal_id,
            title=data.get("title", ""),
            artist=artist,
            artists=artists,
            album=album,
            album_id=_to_int(data.get("album_id", -1), -1),
            album_year=data.get("album_year"),
            duration_sec=_to_int(duration_raw, 0),
            isrc=data.get("isrc"),
            track_num=_to_int(data.get("track_num", 0), 0),
            disc_num=_to_int(data.get("disc_num", data.get("volume_num", 0)), 0),
            version=data.get("version"),
        )


@dataclass
class MatchResult:
    source: SourceTrack
    yt_video_id: str | None  # bare 11-char ID; never a URL
    yt_title: str | None
    yt_artist: str | None
    yt_album: str | None
    yt_album_track_num: int | None
    yt_isrc: str | None
    yt_duration_sec: int | None
    match_method: MatchMethod
    confidence: ConfidenceBreakdown
    status: TrackStatus
    review_reason: str | None = None
