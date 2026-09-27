from __future__ import annotations

import logging
import time
from typing import Any

import requests
from ytmusicapi.exceptions import YTMusicError

from .logging_setup import log_call
from .models import ConfidenceBreakdown, MatchMethod, MatchResult, SourceTrack, TrackStatus
from .text import similarity
from .track_scoring import TrackFit, duration_ok, score_track_fit
from .version_tokens import split_base_qualifiers

ALBUM_SEARCH_LIMIT = 10
ALBUM_FETCH_LIMIT = 3
ALBUM_VERSION_FETCH_LIMIT = 3
ALBUM_MARGIN_EPS = 0.05
# Near-certain winners ignore a close second: at this score both the album
# metadata and the track fits are excellent, so hesitation only loses matches.
ALBUM_CERTAIN_THRESHOLD = 0.95

_LOG = logging.getLogger(__name__)


def group_by_album_id(tracks: list[SourceTrack]) -> dict[int, list[SourceTrack]]:
    groups: dict[int, list[SourceTrack]] = {}
    for t in tracks:
        groups.setdefault(t.album_id, []).append(t)
    return groups


def _candidate_artist_names(candidate: dict[str, Any]) -> list[str]:
    """Artist names for a search hit or sibling entry.

    Live search hits carry an `artists` list; older fixtures and some
    siblings carry a single `artist` string. Both shapes score.
    """
    names = [str(a.get("name", "")) for a in candidate.get("artists", [])]
    names = [n for n in names if n]
    if names:
        return names
    single = str(candidate.get("artist", ""))
    return [single] if single else []


def _version_term(source_quals: tuple[str, ...], cand_quals: tuple[str, ...]) -> float:
    """Non-exact version-marker agreement between source and candidate albums.

    Qualifiers compare as word sets so "deluxe version" still matches
    "deluxe" (subset) while "remix" versus "live" scores near zero. Both
    sides unmarked is agreement; a marker on only one side is a mismatch.
    """
    if not source_quals and not cand_quals:
        return 1.0
    if not source_quals or not cand_quals:
        return 0.0
    s_words = {w for q in source_quals for w in q.split()}
    c_words = {w for q in cand_quals for w in q.split()}
    if s_words == c_words:
        return 1.0
    if s_words <= c_words or c_words <= s_words:
        return 0.8
    overlap = len(s_words & c_words) / len(s_words | c_words)
    return 0.2 + 0.6 * overlap


def _album_meta_score(source: SourceTrack, candidate: dict[str, Any]) -> float:
    src_base, src_quals = split_base_qualifiers(source.album)
    cand_base, cand_quals = split_base_qualifiers(str(candidate.get("title", "")))
    title_sim = similarity(src_base, cand_base)
    version_term = _version_term(src_quals, cand_quals)
    names = _candidate_artist_names(candidate)
    artist_sim = max([similarity(source.artist, n) for n in names], default=0.0)
    year_score = 0.5
    try:
        cand_year = int(str(candidate.get("year", "")))
        if source.album_year is not None:
            year_score = 1.0 / (1.0 + abs(cand_year - source.album_year))
    except (TypeError, ValueError):
        year_score = 0.5
    return title_sim * 0.45 + artist_sim * 0.25 + year_score * 0.1 + version_term * 0.2


def _review_results(
    sources: list[SourceTrack], summary: str, overall: float = 0.0
) -> list[MatchResult]:
    return [
        MatchResult(
            source=s,
            yt_video_id=None,
            yt_title=None,
            yt_artist=None,
            yt_album=None,
            yt_album_track_num=None,
            yt_isrc=None,
            yt_duration_sec=None,
            match_method=MatchMethod.NONE,
            confidence=ConfidenceBreakdown(overall=overall, summary=summary),
            status=TrackStatus.NEEDS_REVIEW,
            review_reason=summary,
        )
        for s in sources
    ]


def _try_fetch_album(
    yt: Any, cache: dict[str, Any], browse_id: str, delay: float
) -> dict[str, Any] | None:
    """Fetch one album's tracklist; None when the candidate is unreachable.

    One stale or region-blocked album must not sink the group: the remaining
    candidates still score, and total failure surfaces as "No album resolve".
    Only expected fetch failures are absorbed here; anything else propagates
    to the group-level fallback.
    """
    if browse_id in cache:
        _LOG.debug("album cache hit: %s", browse_id)
        return cache[browse_id]
    try:
        detail: dict[str, Any] = log_call(
            _LOG, f"album fetch: {browse_id}", yt.get_album, browse_id
        )
    except (YTMusicError, requests.RequestException):
        _LOG.error("album fetch skipped (unreachable): %s", browse_id)
        return None
    cache[browse_id] = detail
    if delay > 0:
        time.sleep(delay)
    return detail


def _sibling_year(sib: dict[str, Any], parent: dict[str, Any]) -> Any:
    """Sibling year when parseable, else the parent candidate's year."""
    try:
        int(str(sib.get("year", "")))
        return sib.get("year")
    except (TypeError, ValueError):
        return parent.get("year", "")


def _harvest_sibling_candidates(
    details: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(parent, sibling) pairs from each fetched album's other_versions shelf.

    Capped per group: sibling tracklist fetches stay bounded no matter how
    many alternates an album page lists.
    """
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for cand, detail in details:
        others: list[dict[str, Any]] = detail.get("other_versions") or []
        if others:
            titles = ", ".join(str(s.get("title", "")) for s in others)
            _LOG.debug("album other versions for %r: %s", str(detail.get("title", "")), titles)
        for sib in others:
            if str(sib.get("browseId", "")):
                pairs.append((cand, sib))
    return pairs[:ALBUM_VERSION_FETCH_LIMIT]


def _sibling_entry(parent: dict[str, Any], sib: dict[str, Any]) -> dict[str, Any]:
    """Search-hit-shaped candidate so siblings rank through the same scorer."""
    artists: list[dict[str, Any]] = sib.get("artists") or parent.get("artists") or []
    entry: dict[str, Any] = {
        "browseId": str(sib.get("browseId", "")),
        "title": str(sib.get("title", "") or parent.get("title", "")),
        "year": _sibling_year(sib, parent),
    }
    if artists:
        entry["artists"] = artists
    elif parent.get("artist"):
        entry["artist"] = parent.get("artist")
    return entry


def _fetch_album_details(
    yt: Any,
    cache: dict[str, Any],
    album_cands: list[dict[str, Any]],
    delay: float,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    details: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for cand in album_cands:
        browse_id = str(cand.get("browseId", ""))
        if not browse_id:
            continue
        detail = _try_fetch_album(yt, cache, browse_id, delay)
        if detail is None:
            continue
        _LOG.debug(
            "album fetched: %r (%s)", str(detail.get("title", "")), str(detail.get("year", ""))
        )
        details.append((cand, detail))
    return details


def _dedupe_details(
    details: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Collapse pool entries for the same album before ranking.

    Other-versions shelves routinely list the album itself (or a sibling
    lists an album already pooled under another browseId). Without this,
    identical twins tie at the top and trip the close-call abstention.
    The first entry wins: search hits precede harvested siblings.
    """
    seen: set[tuple[str, tuple[str, ...]]] = set()
    unique: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for cand, detail in details:
        title_key = " ".join(str(detail.get("title", "")).casefold().replace("&", " and ").split())
        key = (title_key, tuple(_candidate_artist_names(cand)))
        if key in seen:
            _LOG.debug("album duplicate skipped: %r", str(detail.get("title", "")))
            continue
        seen.add(key)
        unique.append((cand, detail))
    return unique


def _best_track_fit(
    source: SourceTrack, tracks: list[dict[str, Any]], album_title: str
) -> tuple[TrackFit | None, dict[str, Any] | None]:
    best_fit: TrackFit | None = None
    best_row: dict[str, Any] | None = None
    for t in tracks:
        fit = score_track_fit(
            source,
            str(t.get("title", "")),
            [a.get("name", "") for a in t.get("artists", [])],
            album_title,
            t.get("duration_seconds"),
            t.get("trackNumber"),
        )
        if not duration_ok(source.duration_sec, t.get("duration_seconds")):
            continue
        if best_fit is None or fit.overall > best_fit.overall:
            best_fit = fit
            best_row = t
    return best_fit, best_row


def _rank_candidates(
    first: SourceTrack,
    sources: list[SourceTrack],
    details: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[float, dict[str, Any], dict[str, Any]]]:
    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for cand, detail in details:
        meta = _album_meta_score(first, cand)
        fits: list[float] = []
        for s in sources:
            best_fit, _ = _best_track_fit(s, detail.get("tracks", []), str(detail.get("title", "")))
            fits.append(best_fit.overall if best_fit is not None else 0.0)
        fit_mean = sum(fits) / len(fits) if fits else 0.0
        score = meta * 0.4 + fit_mean * 0.6
        _LOG.debug(
            "album candidate %r score %.3f (meta %.3f, fit %.3f)",
            str(cand.get("title", "")),
            score,
            meta,
            fit_mean,
        )
        scored.append((score, cand, detail))
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored


def _map_winner_tracks(sources: list[SourceTrack], winner: dict[str, Any]) -> list[MatchResult]:
    winner_tracks: list[dict[str, Any]] = winner.get("tracks", [])
    results: list[MatchResult] = []
    for s in sources:
        best_fit, best_row = _best_track_fit(s, winner_tracks, str(winner.get("title", "")))
        if best_fit is None or best_row is None:
            _LOG.warning("album abstained: no in-album fit")
            results.extend(_review_results([s], "No in-album fit"))
            continue
        results.append(
            MatchResult(
                source=s,
                yt_video_id=best_row.get("videoId"),
                yt_title=best_row.get("title"),
                yt_artist=(best_row.get("artists") or [{}])[0].get("name"),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                yt_album=winner.get("title"),
                yt_album_track_num=best_row.get("trackNumber"),
                yt_isrc=None,
                yt_duration_sec=best_row.get("duration_seconds"),
                match_method=MatchMethod.ALBUM,
                confidence=ConfidenceBreakdown(
                    overall=best_fit.overall,
                    title_similarity=best_fit.title_similarity,
                    artist_similarity=best_fit.artist_similarity,
                    album_similarity=best_fit.album_similarity,
                    duration_delta_sec=best_fit.duration_delta_sec,
                    version_similarity=best_fit.version_similarity,
                    track_num_match=best_fit.track_num_match,
                    album_coherence=1.0,
                    summary=(
                        f"title={best_fit.title_similarity:.2f}, "
                        f"artist={best_fit.artist_similarity:.2f}, "
                        f"album={best_fit.album_similarity:.2f}"
                    ),
                ),
                status=TrackStatus.PENDING,
            )
        )
    return results


def resolve_album_group(
    sources: list[SourceTrack],
    yt: Any,
    cache: dict[str, Any],
    delay: float = 0.3,
) -> list[MatchResult]:
    first = sources[0]
    # Search the simplified base title: qualifiers (Deluxe, Remaster, …)
    # poison YTM album search — "X (Deluxe)" can return nothing where "X"
    # returns the album. The full title still drives scoring afterwards.
    base_album, _ = split_base_qualifiers(first.album)
    query = f"{base_album or first.album} {first.artist}".strip()
    album_cands: list[dict[str, Any]] = log_call(
        _LOG, f"album search: {query}", yt.search, query, filter="albums", limit=ALBUM_SEARCH_LIMIT
    )
    details = _fetch_album_details(yt, cache, album_cands[:ALBUM_FETCH_LIMIT], delay)
    for parent, sib in _harvest_sibling_candidates(details):
        details.extend(_fetch_album_details(yt, cache, [_sibling_entry(parent, sib)], delay))
    details = _dedupe_details(details)
    if not details:
        _LOG.warning("album abstained: no album resolve")
        return _review_results(sources, "No album resolve")
    scored = _rank_candidates(first, sources, details)
    top = scored[0][0]
    if (
        top < ALBUM_CERTAIN_THRESHOLD
        and len(scored) > 1
        and (top - scored[1][0]) < ALBUM_MARGIN_EPS
    ):
        _LOG.warning("album abstained: close album call")
        return _review_results(sources, "Close album call", overall=scored[0][0])
    _, _, winner = scored[0]
    _LOG.info("album resolved: %s", winner.get("title"))
    return _map_winner_tracks(sources, winner)
