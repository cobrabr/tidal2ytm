from __future__ import annotations

from unittest.mock import MagicMock

from tidal2ytm.matcher import _similarity, match_track
from tidal2ytm.models import MatchMethod, TrackStatus


def _yt_with_candidates(candidates, song_detail=None):
    yt = MagicMock()
    yt.search.return_value = candidates
    yt.get_song.return_value = song_detail or {}
    return yt


def test_matcher_isrc_via_candidate() -> None:
    track = {
        "title": "Bungle",
        "artists": ["Jethro Tull"],
        "album": "War Child",
        "duration": 221,
        "isrc": "USABC1234567",
    }
    cand = {
        "videoId": "dQw4w9WgXcQ",
        "title": "Bungle in the Jungle",
        "artists": [{"name": "Jethro Tull"}],
        "album": {"name": "War Child"},
        "duration_seconds": 221,
        "isrc": "USABC1234567",
    }
    yt = _yt_with_candidates([cand])
    res = match_track(track, yt)
    assert res.match_method == MatchMethod.ISRC and res.confidence.overall == 1.0


def test_matcher_isrc_via_get_song_fallback() -> None:
    track = {
        "title": "Bungle",
        "artists": ["Jethro Tull"],
        "album": "War Child",
        "duration": 221,
        "isrc": "USABC1234567",
    }
    cand = {
        "videoId": "dQw4w9WgXcQ",
        "title": "Bungle",
        "artists": [{"name": "Jethro Tull"}],
        "album": {"name": "War Child"},
        "duration_seconds": 221,
    }
    yt = _yt_with_candidates(
        [cand], song_detail={"microformat": {"microformatDataRenderer": {"isrc": "USABC1234567"}}}
    )
    res = match_track(track, yt)
    assert res.match_method == MatchMethod.ISRC


def test_matcher_duration_boundary_4s_pass_5s_fail() -> None:
    track = {
        "title": "Chariots",
        "artists": ["Vangelis"],
        "album": "Chariots",
        "duration": 209,
        "isrc": None,
    }
    cand_4s = {
        "videoId": "AAAAAAAAAAA",
        "title": "Chariots of Fire",
        "artists": [{"name": "Vangelis"}],
        "album": {"name": "Chariots"},
        "duration_seconds": 213,
    }
    cand_5s = {
        "videoId": "BBBBBBBBBBB",
        "title": "Chariots of Fire",
        "artists": [{"name": "Vangelis"}],
        "album": {"name": "Chariots"},
        "duration_seconds": 214,
    }
    yt = _yt_with_candidates([cand_4s])
    res1 = match_track(track, yt)
    # 4s delta within tolerance → pending with high confidence
    # (DURATION or FUZZY depending on album similarity)
    assert res1.status == TrackStatus.PENDING
    assert res1.match_method in (MatchMethod.DURATION, MatchMethod.FUZZY)
    assert res1.confidence.overall >= 0.70
    yt2 = _yt_with_candidates([cand_5s])
    res2 = match_track(track, yt2)
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
    res = match_track(track, yt)
    assert res.status == TrackStatus.NEEDS_REVIEW


def test_matcher_fuzzy_prefers_closest_album() -> None:
    track = {"title": "Song", "artists": ["A"], "album": "War Child", "duration": 200, "isrc": None}
    c1 = {
        "videoId": "AAAAAAAAAAA",
        "title": "Song",
        "artists": [{"name": "A"}],
        "album": {"name": "War Child"},
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
    res = match_track(track, yt)
    assert res.yt_video_id in ("AAAAAAAAAAA", "BBBBBBBBBBB")
    # closest album should win when both pass duration; assert the war-child candidate wins
    assert res.yt_video_id == "AAAAAAAAAAA"


def test_matcher_similarity_threshold() -> None:
    # verify threshold 0.70 edge: identical strings 1.0, unrelated <0.70
    assert _similarity("War Child", "War Child") == 1.0
    assert _similarity("War Child", "Different Album") < 0.70
    assert _similarity("Song", "Song") == 1.0
    # ensure matcher threshold constant is 0.70
    from tidal2ytm.matcher import CONFIDENCE_THRESHOLD

    assert CONFIDENCE_THRESHOLD == 0.70
