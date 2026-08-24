from __future__ import annotations
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional
from ytmusicapi import YTMusic
from .models import SourceTrack, MatchResult, MatchMethod, ConfidenceBreakdown, TrackStatus

DURATION_TOLERANCE_SEC = 4
CONFIDENCE_THRESHOLD = 0.70


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _build_query(track: SourceTrack) -> str:
    parts = [track.title, track.artist]
    if track.version:
        parts.append(track.version)
    return " ".join(parts)


def _isrc_from_song_detail(detail: dict) -> Optional[str]:
    if "isrc" in detail:
        return detail["isrc"]
    vd = detail.get("videoDetails", {})
    if "isrc" in vd:
        return vd["isrc"]
    return None


def _isrc_from_candidate(candidate: dict) -> Optional[str]:
    """Try to extract ISRC from search candidate metadata without a get_song call."""
    return candidate.get("isrc")


def _album_track_num_from_candidate(candidate: dict) -> Optional[int]:
    return candidate.get("trackNumber") or candidate.get("album_track_num")


def _build_fuzzy_summary(
    title_sim: float,
    artist_sim: float,
    album_sim: float,
    duration_delta: int,
    wrong_album: bool,
) -> str:
    s = (
        f"title={title_sim:.2f}, artist={artist_sim:.2f}, "
        f"album={album_sim:.2f}, Δdur={duration_delta}s"
    )
    if wrong_album:
        s += " (wrong album?)"
    return s


def match_track(track: SourceTrack, yt: YTMusic) -> MatchResult:
    query = _build_query(track)
    candidates = yt.search(query, filter="songs", limit=10)

    best_video_id: Optional[str] = None
    best_meta: dict = {}
    best_method = MatchMethod.NONE
    best_confidence = 0.0
    best_breakdown: Optional[ConfidenceBreakdown] = None

    for candidate in candidates:
        vid = candidate.get("videoId")
        if not vid:
            continue

        c_title = candidate.get("title", "")
        c_artist = (candidate.get("artists") or [{}])[0].get("name", "")
        c_album_info = candidate.get("album") or {}
        c_album = c_album_info.get("name", "")
        c_dur = candidate.get("duration_seconds")
        c_isrc: Optional[str] = _isrc_from_candidate(candidate)
        c_track_num: Optional[int] = _album_track_num_from_candidate(candidate)

        # Strategy 1: ISRC
        if track.isrc:
            # First try candidate metadata (no extra API call)
            if c_isrc and c_isrc.upper() == track.isrc.upper():
                breakdown = ConfidenceBreakdown(overall=1.0, summary="Exact ISRC match")
                return MatchResult(
                    source=track,
                    yt_video_id=vid,
                    yt_title=c_title,
                    yt_artist=c_artist,
                    yt_album=c_album,
                    yt_album_track_num=c_track_num,
                    yt_isrc=c_isrc,
                    yt_duration_sec=c_dur,
                    match_method=MatchMethod.ISRC,
                    confidence=breakdown,
                    status=TrackStatus.PENDING,
                )
            # Fallback: fetch detail
            try:
                detail = yt.get_song(vid)
                fetched_isrc = _isrc_from_song_detail(detail)
                if fetched_isrc and fetched_isrc.upper() == track.isrc.upper():
                    breakdown = ConfidenceBreakdown(overall=1.0, summary="Exact ISRC match")
                    return MatchResult(
                        source=track,
                        yt_video_id=vid,
                        yt_title=c_title,
                        yt_artist=c_artist,
                        yt_album=c_album,
                        yt_album_track_num=c_track_num,
                        yt_isrc=fetched_isrc,
                        yt_duration_sec=c_dur,
                        match_method=MatchMethod.ISRC,
                        confidence=breakdown,
                        status=TrackStatus.PENDING,
                    )
            except Exception:
                pass

        # Strategy 2+3: Duration + fuzzy
        if c_dur is None:
            continue
        dur_delta = abs(c_dur - track.duration_sec)
        if dur_delta > DURATION_TOLERANCE_SEC:
            continue

        title_sim = _similarity(c_title, track.title)
        artist_sim = _similarity(c_artist, track.artist)
        base_conf = title_sim * 0.6 + artist_sim * 0.4
        album_sim = _similarity(c_album, track.album) if c_album else 0.0
        conf = base_conf * 0.75 + album_sim * 0.25

        if conf > best_confidence:
            best_confidence = conf
            best_video_id = vid
            best_meta = {
                "title": c_title,
                "artist": c_artist,
                "album": c_album,
                "dur": c_dur,
                "isrc": c_isrc,
                "track_num": c_track_num,
            }
            wrong_album = album_sim < 0.5
            best_method = MatchMethod.DURATION if album_sim < 0.3 else MatchMethod.FUZZY
            best_breakdown = ConfidenceBreakdown(
                overall=conf,
                title_similarity=title_sim,
                artist_similarity=artist_sim,
                album_similarity=album_sim,
                duration_delta_sec=dur_delta,
                summary=_build_fuzzy_summary(title_sim, artist_sim, album_sim, dur_delta, wrong_album),
            )

    needs_review = best_confidence < CONFIDENCE_THRESHOLD or best_video_id is None
    reason: Optional[str] = None
    if best_video_id is None:
        reason = "No candidates found"
    elif best_confidence < CONFIDENCE_THRESHOLD:
        reason = f"Low confidence ({best_confidence:.2f})"

    status = TrackStatus.NEEDS_REVIEW if needs_review else TrackStatus.PENDING

    if best_breakdown is None:
        best_breakdown = ConfidenceBreakdown(overall=0.0, summary="No match found")

    return MatchResult(
        source=track,
        yt_video_id=best_video_id,
        yt_title=best_meta.get("title"),
        yt_artist=best_meta.get("artist"),
        yt_album=best_meta.get("album"),
        yt_album_track_num=best_meta.get("track_num"),
        yt_isrc=best_meta.get("isrc"),
        yt_duration_sec=best_meta.get("dur"),
        match_method=best_method,
        confidence=best_breakdown,
        status=status,
        review_reason=reason,
    )
