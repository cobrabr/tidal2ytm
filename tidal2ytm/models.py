from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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

    @property
    def year(self) -> int | None:
        return self.album_year


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


@dataclass
class AlbumGroup:
    name: str
    year: int | None
    match_id: str  # e.g. "jethro-tull/war-child"
    tracks: list[MatchResult] = field(default_factory=list)


@dataclass
class ArtistGroup:
    name: str
    match_id: str  # e.g. "jethro-tull"
    albums: list[AlbumGroup] = field(default_factory=list)
