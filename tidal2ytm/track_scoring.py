from __future__ import annotations

from dataclasses import dataclass

from .models import SourceTrack
from .text import similarity
from .version_tokens import qualifier_similarity, split_base_qualifiers

DURATION_ABS_TOL_SEC = 4
DURATION_REL_TOL = 0.02


@dataclass
class TrackFit:
    overall: float
    title_similarity: float
    artist_similarity: float
    album_similarity: float
    version_similarity: float
    track_num_match: bool
    duration_delta_sec: int | None


def duration_ok(source_sec: int, candidate_sec: int | None) -> bool:
    if source_sec <= 0:
        return True
    if candidate_sec is None:
        return False
    delta = abs(candidate_sec - source_sec)
    return delta <= max(DURATION_ABS_TOL_SEC, int(source_sec * DURATION_REL_TOL))


def _artist_sim(source_artists: list[str], candidate_artists: list[str]) -> float:
    if not source_artists or not candidate_artists:
        return 0.0
    scores = [similarity(s, c) for s in source_artists for c in candidate_artists]
    return max(scores) if scores else 0.0


def score_track_fit(
    source: SourceTrack,
    candidate_title: str,
    candidate_artists: list[str],
    candidate_album: str,
    candidate_duration_sec: int | None,
    candidate_track_num: int | None,
) -> TrackFit:
    src_base, src_quals = split_base_qualifiers(source.title)
    if source.version:
        src_quals = (*src_quals, source.version.casefold())
    cand_base, cand_quals = split_base_qualifiers(candidate_title)
    title_sim = similarity(src_base, cand_base)
    version_sim = qualifier_similarity(src_quals, cand_quals)
    artist_sim = _artist_sim(source.artists or [source.artist], candidate_artists)
    album_sim = similarity(source.album, candidate_album) if candidate_album else 0.0
    delta: int | None = None
    if candidate_duration_sec is not None:
        delta = abs(candidate_duration_sec - source.duration_sec)
    pos_match = (
        source.track_num > 0
        and candidate_track_num is not None
        and source.track_num == candidate_track_num
    )
    overall = title_sim * 0.45 + artist_sim * 0.2 + album_sim * 0.2 + version_sim * 0.15
    if pos_match:
        overall = min(1.0, overall + 0.08)
    return TrackFit(
        overall=overall,
        title_similarity=title_sim,
        artist_similarity=artist_sim,
        album_similarity=album_sim,
        version_similarity=version_sim,
        track_num_match=pos_match,
        duration_delta_sec=delta,
    )
