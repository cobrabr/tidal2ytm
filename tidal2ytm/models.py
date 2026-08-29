from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MatchMethod(str, Enum):
    ISRC     = "isrc"
    DURATION = "duration"
    FUZZY    = "fuzzy"
    NONE     = "none"


class TrackStatus(str, Enum):
    PENDING      = "pending"
    TRANSFERRED  = "transferred"
    SKIP         = "skip"
    FAILED       = "failed"
    NEEDS_REVIEW = "needs_review"


@dataclass
class ConfidenceBreakdown:
    overall: float
    title_similarity: Optional[float] = None
    artist_similarity: Optional[float] = None
    album_similarity: Optional[float] = None
    duration_delta_sec: Optional[int] = None
    summary: Optional[str] = None


@dataclass
class SourceTrack:
    tidal_id: int
    title: str
    artist: str
    artists: list[str]
    album: str
    album_id: int
    album_year: Optional[int]
    duration_sec: int
    isrc: Optional[str]
    track_num: int
    disc_num: int
    version: Optional[str]

    @property
    def year(self) -> Optional[int]:
        return self.album_year


@dataclass
class MatchResult:
    source: SourceTrack
    yt_video_id: Optional[str]        # bare 11-char ID; never a URL
    yt_title: Optional[str]
    yt_artist: Optional[str]
    yt_album: Optional[str]
    yt_album_track_num: Optional[int]
    yt_isrc: Optional[str]
    yt_duration_sec: Optional[int]
    match_method: MatchMethod
    confidence: ConfidenceBreakdown
    status: TrackStatus
    review_reason: Optional[str] = None


@dataclass
class AlbumGroup:
    name: str
    year: Optional[int]
    match_id: str        # e.g. "jethro-tull/war-child"
    tracks: list[MatchResult] = field(default_factory=list)


@dataclass
class ArtistGroup:
    name: str
    match_id: str        # e.g. "jethro-tull"
    albums: list[AlbumGroup] = field(default_factory=list)
