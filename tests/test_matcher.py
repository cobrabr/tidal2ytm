from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tidal2ytm.matcher import CONFIDENCE_THRESHOLD, match_track
from tidal2ytm.models import MatchMethod, SourceTrack, TrackStatus


def _yt_with_candidates(candidates: list[dict[str, Any]]) -> Any:
    yt = MagicMock()
    yt.search.return_value = candidates
    return yt


def _source(data: dict[str, Any]) -> SourceTrack:
    """Matcher-boundary conversion: tests build tracks the way prod code does."""
    return SourceTrack.from_dict({"tidal_id": 31, "album_id": 7, **data})


def test_from_dict_rejects_bad_identity() -> None:
    with pytest.raises(ValueError):
        SourceTrack.from_dict({"tidal_id": "not-a-number", "title": "Ember Fall"})


def test_matcher_makes_no_detail_lookups() -> None:
    """One search call per track; no per-candidate get_song detail lookups."""
    track = _source(
        {
            "title": "Ember Fall",
            "artists": ["Vesper Vale"],
            "album": "Ashen Light",
            "duration": 209,
            "isrc": "USABC1234567",
        }
    )
    cand = {
        "videoId": "AAAAAAAAAAA",
        "title": "Ember Fall",
        "artists": [{"name": "Vesper Vale"}],
        "album": {"name": "Ashen Light"},
        "duration_seconds": 209,
    }
    yt = _yt_with_candidates([cand])
    match_track(track, yt)
    yt.search.assert_called_once()
    yt.get_song.assert_not_called()


def test_matcher_isrc_via_candidate() -> None:
    track = {
        "title": "Muddle",
        "artists": ["Wren"],
        "album": "Cinder Child",
        "duration": 221,
        "isrc": "USABC1234567",
    }
    cand = {
        "videoId": "CCCCCCCCCCC",
        "title": "Muddle in the Puddle",
        "artists": [{"name": "Wren"}],
        "album": {"name": "Cinder Child"},
        "duration_seconds": 221,
        "isrc": "USABC1234567",
    }
    yt = _yt_with_candidates([cand])
    res = match_track(_source(track), yt)
    assert res.match_method == MatchMethod.ISRC and res.confidence.overall == 1.0


def test_matcher_duration_boundary_4s_pass_5s_fail() -> None:
    track = {
        "title": "Ember",
        "artists": ["Vesper"],
        "album": "Ember",
        "duration": 209,
        "isrc": None,
    }
    cand_4s = {
        "videoId": "AAAAAAAAAAA",
        "title": "Ember Glow",
        "artists": [{"name": "Vesper"}],
        "album": {"name": "Ember"},
        "duration_seconds": 213,
    }
    cand_5s = {
        "videoId": "BBBBBBBBBBB",
        "title": "Ember Glow",
        "artists": [{"name": "Vesper"}],
        "album": {"name": "Ember"},
        "duration_seconds": 214,
    }
    yt = _yt_with_candidates([cand_4s])
    res1 = match_track(_source(track), yt)
    # 4s delta within tolerance → pending with high confidence
    # (DURATION or FUZZY depending on album similarity)
    assert res1.status == TrackStatus.PENDING
    assert res1.match_method in (MatchMethod.DURATION, MatchMethod.FUZZY)
    assert res1.confidence.overall >= 0.70
    yt2 = _yt_with_candidates([cand_5s])
    res2 = match_track(_source(track), yt2)
    assert (
        res2.match_method in (MatchMethod.FUZZY, MatchMethod.NONE)
        or res2.status == TrackStatus.NEEDS_REVIEW
    )
    assert res2.status == TrackStatus.NEEDS_REVIEW


def test_matcher_no_candidates_needs_review() -> None:
    track = {
        "title": "Unknown",
        "artists": ["Nobody"],
        "album": "None",
        "duration": 200,
        "isrc": None,
    }
    yt = _yt_with_candidates([])
    res = match_track(_source(track), yt)
    assert res.status == TrackStatus.NEEDS_REVIEW


def test_matcher_fuzzy_prefers_closest_album() -> None:
    track = {
        "title": "Song",
        "artists": ["A"],
        "album": "Cinder Child",
        "duration": 200,
        "isrc": None,
    }
    c1 = {
        "videoId": "AAAAAAAAAAA",
        "title": "Song",
        "artists": [{"name": "A"}],
        "album": {"name": "Cinder Child"},
        "duration_seconds": 200,
    }
    c2 = {
        "videoId": "BBBBBBBBBBB",
        "title": "Song",
        "artists": [{"name": "A"}],
        "album": {"name": "Different Album"},
        "duration_seconds": 200,
    }
    yt = _yt_with_candidates([c2, c1])
    res = match_track(_source(track), yt)
    # closest album should win when both pass duration; assert the war-child candidate wins
    assert res.yt_video_id == "AAAAAAAAAAA"


def test_matcher_threshold_edge_just_below_rejects() -> None:
    # candidate with exact artist/album and equal duration, but a title whose
    # similarity (0.32) drags confidence to 0.694 — just below the 0.70 gate
    track = {
        "title": "Cinder Child",
        "artists": ["Wren"],
        "album": "Cinder Child",
        "duration": 200,
        "isrc": None,
    }
    cand = {
        "videoId": "AAAAAAAAAAA",
        "title": "Paper Lantern",
        "artists": [{"name": "Wren"}],
        "album": {"name": "Cinder Child"},
        "duration_seconds": 200,
    }
    yt = _yt_with_candidates([cand])
    res = match_track(_source(track), yt)
    assert res.status == TrackStatus.NEEDS_REVIEW
    assert res.confidence.overall < CONFIDENCE_THRESHOLD


def test_non_latin_title_does_not_score_one() -> None:
    track = {
        "title": "音楽",
        "artists": ["アーティスト"],
        "album": "アルバム",
        "duration": 200,
        "isrc": None,
    }
    cand = {
        "videoId": "AAAAAAAAAAA",
        "title": "音楽",
        "artists": [{"name": "アーティスト"}],
        "album": {"name": "アルバム"},
        "duration_seconds": 200,
    }
    yt = _yt_with_candidates([cand])
    result = match_track(_source(track), yt)
    assert result.confidence.overall < 1.0


def test_unknown_source_duration_skips_duration_gate() -> None:
    track = {
        "title": "Ember",
        "artists": ["Blashen"],
        "album": "Ash",
        "duration": 0,
        "isrc": None,
    }
    cand = {
        "videoId": "BBBBBBBBBBB",
        "title": "Ember",
        "artists": [{"name": "Blashen"}],
        "album": {"name": "Ash"},
        "duration_seconds": 213,
    }
    yt = _yt_with_candidates([cand])
    assert match_track(_source(track), yt).status is TrackStatus.PENDING


def test_deluxe_suffix_album_is_needs_review() -> None:
    # "(Deluxe Version)" pushes album similarity below WRONG_ALBUM_THRESHOLD,
    # so a different pressing needs review even with perfect title/artist.
    track = {
        "title": "Ember",
        "artists": ["Vesper Vale"],
        "album": "Ashen Light",
        "duration": 213,
        "isrc": None,
    }
    cand = {
        "videoId": "DDDDDDDDDDD",
        "title": "Ember",
        "artists": [{"name": "Vesper Vale"}],
        "album": {"name": "Ashen Light (Deluxe Version)"},
        "duration_seconds": 213,
    }
    yt = _yt_with_candidates([cand])
    result = match_track(_source(track), yt)
    assert result.status == TrackStatus.NEEDS_REVIEW
    assert result.review_reason is not None and result.review_reason.startswith("Wrong album match")


def test_exact_album_is_pending() -> None:
    track = {
        "title": "Ember",
        "artists": ["Vesper Vale"],
        "album": "Ashen Light",
        "duration": 213,
        "isrc": None,
    }
    cand = {
        "videoId": "DDDDDDDDDDD",
        "title": "Ember",
        "artists": [{"name": "Vesper Vale"}],
        "album": {"name": "Ashen Light"},
        "duration_seconds": 213,
    }
    yt = _yt_with_candidates([cand])
    result = match_track(_source(track), yt)
    assert result.status == TrackStatus.PENDING


def test_isrc_miss_falls_back_to_duration_over_fuzzy_title() -> None:
    # Candidate B: exact title/artist/album but wrong duration (fails the gate).
    # Candidate A: candidate-metadata ISRC mismatch + right duration — the ISRC
    # miss must fall through to the duration strategy instead of letting the
    # fuzzy-title candidate win.
    track = {
        "title": "Ember Fall",
        "artists": ["Vesper Vale"],
        "album": "Ashen Light",
        "duration": 209,
        "isrc": "USABC1234567",
    }
    cand_fuzzy_wrong_dur = {
        "videoId": "BBBBBBBBBBB",
        "title": "Ember Fall",
        "artists": [{"name": "Vesper Vale"}],
        "album": {"name": "Ashen Light"},
        "duration_seconds": 300,
    }
    cand_isrc_miss_right_dur = {
        "videoId": "AAAAAAAAAAA",
        "title": "Ember Fall (Live)",
        "artists": [{"name": "Vesper Vale"}],
        "album": {"name": "Quartz Sea"},
        "duration_seconds": 209,
        "isrc": "USZZZ9999999",
    }
    yt = _yt_with_candidates([cand_fuzzy_wrong_dur, cand_isrc_miss_right_dur])
    res = match_track(_source(track), yt)
    assert res.yt_video_id == "AAAAAAAAAAA"
    assert res.match_method == MatchMethod.DURATION


def test_matcher_candidates_missing_keys_are_skipped() -> None:
    track = {
        "title": "Ember",
        "artists": ["Vesper"],
        "album": "Ash",
        "duration": 200,
        "isrc": None,
    }
    cands = [
        {"title": "Ember"},  # no videoId
        {
            "videoId": "AAAAAAAAAAA",
            "title": "Ember",
            "artists": [{"name": "Vesper"}],
            "album": {"name": "Ash"},  # no duration_seconds
        },
    ]
    yt = _yt_with_candidates(cands)
    res = match_track(_source(track), yt)
    assert res.yt_video_id is None
    assert res.match_method == MatchMethod.NONE
    assert res.status == TrackStatus.NEEDS_REVIEW


def test_matcher_search_errors_propagate() -> None:
    track = {
        "title": "Ember",
        "artists": ["Vesper"],
        "album": "Ash",
        "duration": 200,
        "isrc": None,
    }
    yt = MagicMock()
    yt.search.side_effect = RuntimeError("search down")
    with pytest.raises(RuntimeError):
        match_track(_source(track), yt)  # type: ignore[arg-type]
