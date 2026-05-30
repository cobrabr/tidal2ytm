from dataclasses import dataclass
from typing import Optional


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


@dataclass
class MatchResult:
    source: SourceTrack
    yt_video_id: Optional[str]
    yt_title: Optional[str]
    yt_artist: Optional[str]
    yt_album: Optional[str]
    yt_duration_sec: Optional[int]
    match_method: str       # "isrc", "duration", "fuzzy", "none"
    confidence: float       # 0.0 – 1.0
    needs_review: bool
    review_reason: Optional[str] = None
    confirmed: bool = False
    skipped: bool = False
    override_video_id: Optional[str] = None
