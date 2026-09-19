from __future__ import annotations

from typing import Any

from tidal2ytm.models import (
    ConfidenceBreakdown,
    MatchMethod,
    MatchResult,
    SourceTrack,
    TrackStatus,
)
from tidal2ytm.planning_merge import (
    classify_track,
    insert_track,
    iter_selection_ordered,
    match_result_to_track_dict,
)


def _src(tidal_id: int = 1) -> SourceTrack:
    return SourceTrack(
        tidal_id=tidal_id,
        title="Apple",
        artist="Wren",
        artists=["Wren"],
        album="Apple",
        album_id=11,
        album_year=1971,
        duration_sec=200,
        isrc=None,
        track_num=1,
        disc_num=1,
        version=None,
    )


def _result() -> MatchResult:
    return MatchResult(
        source=_src(),
        yt_video_id="AAAAAAAAAAA",
        yt_title="Apple",
        yt_artist="Wren",
        yt_album="Apple",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.9),
        status=TrackStatus.PENDING,
    )


def test_track_dict_conversion() -> None:
    d = match_result_to_track_dict(_result())
    assert d["tidal_id"] == 1 and d["yt_video_id"] == "AAAAAAAAAAA"
    assert d["match_method"] == "fuzzy" and d["status"] == "pending"


def test_classify_new_existing_same_transferred() -> None:
    assert classify_track(None, "AAAAAAAAAAA") == "add-new"
    assert classify_track({"status": "pending", "yt_video_id": ""}, "AAAAAAAAAAA") == "ask"
    assert (
        classify_track({"status": "pending", "yt_video_id": "AAAAAAAAAAA"}, "AAAAAAAAAAA")
        == "keep-same"
    )
    assert classify_track({"status": "pending", "yt_video_id": ""}, None) == "ask"
    assert (
        classify_track({"status": "transferred", "yt_video_id": "AAAAAAAAAAA"}, "BBBBBBBBBBB")
        == "skip-transferred"
    )


def test_insert_creates_slugs_and_reuses_them() -> None:
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    d = match_result_to_track_dict(_result())
    artist_id, album_id = insert_track(plan, d, 1971)
    assert artist_id == "wren" and album_id == "wren/apple"
    d2 = dict(d, tidal_id=2)
    artist_id2, album_id2 = insert_track(plan, d2, 1971)
    assert (artist_id2, album_id2) == (artist_id, album_id)
    assert len(plan["artists"][0]["albums"][0]["tracks"]) == 2


def test_selection_ordering() -> None:
    import dataclasses

    b = dataclasses.replace(_src(2), artist="Alder", album="Tin", album_year=1992)
    a = dataclasses.replace(_src(1), artist="Wren", album="Apple", album_year=1971)
    assert [t.tidal_id for t in iter_selection_ordered({1: a, 2: b})] == [2, 1]


def test_insert_preserves_insertion_order() -> None:
    # The stored dict carries no disc field, so insertion order
    # (legacy plan order from the caller) is the order.
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    d2 = dict(match_result_to_track_dict(_result()), tidal_id=2, tidal_track_num=5)
    d1 = dict(match_result_to_track_dict(_result()), tidal_id=1, tidal_track_num=1)
    insert_track(plan, d2, 1971)
    insert_track(plan, d1, 1971)
    got = [t["tidal_id"] for t in plan["artists"][0]["albums"][0]["tracks"]]
    assert got == [2, 1]


def test_merge_rehomes_track_on_artist_rename_case_insensitive() -> None:
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    insert_track(plan, match_result_to_track_dict(_result()), 1971)
    renamed = dict(match_result_to_track_dict(_result()), tidal_id=2, artist="WREN")
    insert_track(plan, renamed, 1971)
    assert [a["name"] for a in plan["artists"]] == ["Wren"]  # no duplicate node


def test_merge_rehomes_track_on_album_rename_case_insensitive() -> None:
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    insert_track(plan, match_result_to_track_dict(_result()), 1971)
    renamed = dict(match_result_to_track_dict(_result()), tidal_id=2, tidal_album="APPLE")
    insert_track(plan, renamed, 1971)
    albums = plan["artists"][0]["albums"]
    assert [a["name"] for a in albums] == ["Apple"]  # no duplicate node


def test_classify_double_empty_video_id_asks_not_keeps() -> None:
    assert classify_track({"status": "pending", "yt_video_id": ""}, "") == "ask"
    assert classify_track({"status": "pending", "yt_video_id": ""}, None) == "ask"


def test_classify_track_skip_and_needs_review_branches() -> None:
    # skip is not sacred (unlike transferred); needs_review follows video-ID logic
    assert classify_track({"status": "skip", "yt_video_id": "AAAAAAAAAAA"}, "BBBBBBBBBBB") == "ask"
    assert (
        classify_track({"status": "needs_review", "yt_video_id": "AAAAAAAAAAA"}, "AAAAAAAAAAA")
        == "keep-same"
    )
    assert (
        classify_track({"status": "needs_review", "yt_video_id": "AAAAAAAAAAA"}, "BBBBBBBBBBB")
        == "ask"
    )


def test_insert_track_dedups_slugs_plan_level() -> None:
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    d1 = match_result_to_track_dict(_result())
    artist1, album1 = insert_track(plan, d1, 1971)
    assert (artist1, album1) == ("wren", "wren/apple")
    # same album slug from a differently-spelled album name → -2, then -3
    d2 = dict(match_result_to_track_dict(_result()), tidal_id=2, tidal_album="Apple!")
    artist2, album2 = insert_track(plan, d2, 1971)
    assert (artist2, album2) == ("wren", "wren/apple-2")
    d3 = dict(match_result_to_track_dict(_result()), tidal_id=3, tidal_album="Apple?")
    artist3, album3 = insert_track(plan, d3, 1971)
    assert (artist3, album3) == ("wren", "wren/apple-3")
    # same artist slug from a differently-spelled artist name → -2
    d4 = dict(match_result_to_track_dict(_result()), tidal_id=4, artist="Wren?")
    artist4, album4 = insert_track(plan, d4, 1971)
    assert (artist4, album4) == ("wren-2", "wren-2/apple")
    albums = plan["artists"][0]["albums"]
    assert [a["match_id"] for a in albums] == ["wren/apple", "wren/apple-2", "wren/apple-3"]
    assert [a["name"] for a in plan["artists"]] == ["Wren", "Wren?"]
