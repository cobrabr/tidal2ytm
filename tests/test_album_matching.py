from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from tidal2ytm.album_matching import group_by_album_id, resolve_album_group
from tidal2ytm.models import SourceTrack


def _src(tidal_id: int, album_id: int, title: str, num: int) -> SourceTrack:
    return SourceTrack.from_dict(
        {
            "tidal_id": tidal_id,
            "album_id": album_id,
            "title": title,
            "artists": ["Vesper Vale"],
            "album": "Ashen Light",
            "duration": 200,
            "track_num": num,
        }
    )


def test_groups_are_independent() -> None:
    tracks = [_src(1, 7, "Ember Fall", 1), _src(2, 9, "Cinder Hymn", 1)]
    groups = group_by_album_id(tracks)
    assert set(groups) == {7, 9}


def test_unknown_year_resolves_without_penalty() -> None:
    group_a = [_src(1, 7, "Shared Hymn", 1)]
    yt = MagicMock()
    yt.search.return_value = [
        {"browseId": "MPRE_A", "title": "Ashen Light", "artist": "Vesper Vale", "year": ""},
    ]
    yt.get_album.return_value = {
        "title": "Ashen Light",
        "tracks": [
            {
                "videoId": "AAAAAAAAAAA",
                "title": "Shared Hymn",
                "artists": [{"name": "Vesper Vale"}],
                "duration_seconds": 200,
            },
        ],
    }
    results = resolve_album_group(group_a, yt, {}, delay=0.0)
    assert results[0].yt_video_id == "AAAAAAAAAAA"


def test_overlapping_groups_do_not_collapse() -> None:
    group_a = [_src(1, 7, "Shared Hymn", 1), _src(2, 7, "Only In A", 2)]
    yt = MagicMock()
    yt.search.return_value = [
        {"browseId": "MPRE_A", "title": "Ashen Light", "artist": "Vesper Vale", "year": "2001"},
        {"browseId": "MPRE_B", "title": "Quartz Sea", "artist": "Vesper Vale", "year": "2005"},
    ]
    yt.get_album.side_effect = [
        {
            "title": "Ashen Light",
            "tracks": [
                {
                    "videoId": "AAAAAAAAAAA",
                    "title": "Shared Hymn",
                    "artists": [{"name": "Vesper Vale"}],
                    "duration_seconds": 200,
                },
                {
                    "videoId": "BBBBBBBBBBB",
                    "title": "Only In A",
                    "artists": [{"name": "Vesper Vale"}],
                    "duration_seconds": 200,
                },
            ],
        },
        {
            "title": "Quartz Sea",
            "tracks": [
                {
                    "videoId": "CCCCCCCCCCC",
                    "title": "Shared Hymn",
                    "artists": [{"name": "Vesper Vale"}],
                    "duration_seconds": 200,
                },
            ],
        },
    ]
    results = resolve_album_group(group_a, yt, {}, delay=0.0)
    assert {r.yt_video_id for r in results} == {"AAAAAAAAAAA", "BBBBBBBBBBB"}


def test_failed_candidate_fetch_falls_through_to_next() -> None:
    from ytmusicapi.exceptions import YTMusicServerError

    group_a = [_src(1, 7, "Shared Hymn", 1)]
    yt = MagicMock()
    yt.search.return_value = [
        {"browseId": "MPRE_STALE", "title": "Ashen Light", "artist": "Vesper Vale", "year": "2001"},
        {"browseId": "MPRE_GOOD", "title": "Ashen Light", "artist": "Vesper Vale", "year": "2001"},
    ]
    yt.get_album.side_effect = [
        YTMusicServerError("stale browseId"),
        {
            "title": "Ashen Light",
            "tracks": [
                {
                    "videoId": "AAAAAAAAAAA",
                    "title": "Shared Hymn",
                    "artists": [{"name": "Vesper Vale"}],
                    "duration_seconds": 200,
                },
            ],
        },
    ]
    results = resolve_album_group(group_a, yt, {}, delay=0.0)
    assert results[0].yt_video_id == "AAAAAAAAAAA"
    assert yt.get_album.call_count == 2


def _deluxe_src(tidal_id: int, title: str, num: int) -> SourceTrack:
    return SourceTrack.from_dict(
        {
            "tidal_id": tidal_id,
            "album_id": 7,
            "title": title,
            "artists": ["Vesper Vale"],
            "album": "Ashen Light (Deluxe Version)",
            "duration": 200,
            "track_num": num,
        }
    )


def test_deluxe_sibling_wins_over_regular() -> None:
    group = [_deluxe_src(1, "Shared Hymn", 1), _deluxe_src(2, "Only On Deluxe", 14)]
    yt = MagicMock()
    yt.search.return_value = [
        {
            "browseId": "MPRE_REG",
            "title": "Ashen Light",
            "artists": [{"name": "Vesper Vale"}],
            "year": "2001",
        },
    ]
    regular = {
        "title": "Ashen Light",
        "year": "2001",
        "tracks": [
            {
                "videoId": "RRRRRRRRRRR",
                "title": "Shared Hymn",
                "artists": [{"name": "Vesper Vale"}],
                "duration_seconds": 200,
            },
        ],
        "other_versions": [
            {
                "title": "Ashen Light (Deluxe Edition)",
                "browseId": "MPRE_DLX",
                "artists": [{"name": "Vesper Vale"}],
                "year": "2001",
            },
        ],
    }
    deluxe = {
        "title": "Ashen Light (Deluxe Edition)",
        "year": "2001",
        "tracks": [
            {
                "videoId": "AAAAAAAAAAA",
                "title": "Shared Hymn",
                "artists": [{"name": "Vesper Vale"}],
                "duration_seconds": 200,
            },
            {
                "videoId": "BBBBBBBBBBB",
                "title": "Only On Deluxe",
                "artists": [{"name": "Vesper Vale"}],
                "duration_seconds": 200,
            },
        ],
    }
    yt.get_album.side_effect = [regular, deluxe]
    results = resolve_album_group(group, yt, {}, delay=0.0)
    assert {r.yt_video_id for r in results} == {"AAAAAAAAAAA", "BBBBBBBBBBB"}
    assert all(r.match_method.value == "album" for r in results)


def test_version_qualifier_subset_scores_high() -> None:
    from tidal2ytm.album_matching import _version_term  # pyright: ignore[reportPrivateUsage]

    assert _version_term(("deluxe version",), ("deluxe",)) == 0.8
    assert _version_term(("deluxe",), ("deluxe edition",)) == 0.8
    assert _version_term(("deluxe",), ("deluxe",)) == 1.0
    assert _version_term((), ()) == 1.0
    assert _version_term(("deluxe",), ()) == 0.0
    assert _version_term(("remix",), ("live",)) < 0.8


def test_sibling_fetches_are_capped() -> None:
    group = [_deluxe_src(1, "Shared Hymn", 1)]
    yt = MagicMock()
    yt.search.return_value = [
        {
            "browseId": "MPRE_REG",
            "title": "Ashen Light",
            "artists": [{"name": "Vesper Vale"}],
            "year": "2001",
        },
    ]
    sibs = [
        {
            "title": f"Ashen Light (Take {n})",
            "browseId": f"MPRE_S{n}",
            "artists": [{"name": "Vesper Vale"}],
            "year": "2001",
        }
        for n in range(9)
    ]
    yt.get_album.side_effect = [
        {"title": "Ashen Light", "year": "2001", "tracks": [], "other_versions": sibs},
        *[{"title": f"Ashen Light (Take {n})", "year": "2001", "tracks": []} for n in range(9)],
    ]
    resolve_album_group(group, yt, {}, delay=0.0)
    assert yt.get_album.call_count <= 1 + 3


def test_detail_without_siblings_still_resolves() -> None:
    group_a = [_src(1, 7, "Shared Hymn", 1)]
    yt = MagicMock()
    yt.search.return_value = [
        {
            "browseId": "MPRE_A",
            "title": "Ashen Light",
            "artists": [{"name": "Vesper Vale"}],
            "year": "",
        },
    ]
    yt.get_album.return_value = {
        "title": "Ashen Light",
        "tracks": [
            {
                "videoId": "AAAAAAAAAAA",
                "title": "Shared Hymn",
                "artists": [{"name": "Vesper Vale"}],
                "duration_seconds": 200,
            },
        ],
    }
    results = resolve_album_group(group_a, yt, {}, delay=0.0)
    assert results[0].yt_video_id == "AAAAAAAAAAA"


def test_self_listing_shelf_does_not_force_close_call() -> None:
    group_a = [_src(1, 7, "Shared Hymn", 1)]
    yt = MagicMock()
    yt.search.return_value = [
        {
            "browseId": "MPRE_A",
            "title": "Ashen Light",
            "artists": [{"name": "Vesper Vale"}],
            "year": "2001",
        },
    ]
    yt.get_album.return_value = {
        "title": "Ashen Light",
        "year": "2001",
        "tracks": [
            {
                "videoId": "AAAAAAAAAAA",
                "title": "Shared Hymn",
                "artists": [{"name": "Vesper Vale"}],
                "duration_seconds": 200,
            },
        ],
        "other_versions": [
            {
                "title": "Ashen Light",
                "browseId": "MPRE_A2",
                "artists": [{"name": "Vesper Vale"}],
                "year": "2001",
            },
        ],
    }
    results = resolve_album_group(group_a, yt, {}, delay=0.0)
    assert results[0].yt_video_id == "AAAAAAAAAAA"
    assert results[0].match_method.value == "album"


def test_ampersand_variant_does_not_force_close_call() -> None:
    src = SourceTrack.from_dict(
        {
            "tidal_id": 1,
            "album_id": 7,
            "title": "Shared Hymn",
            "artists": ["Vesper Vale"],
            "album": "Karma and Effect",
            "duration": 200,
            "track_num": 1,
        }
    )
    yt = MagicMock()
    yt.search.return_value = [
        {
            "browseId": "MPRE_A",
            "title": "Karma and Effect",
            "artists": [{"name": "Vesper Vale"}],
            "year": "2004",
        },
    ]
    yt.get_album.side_effect = [
        {
            "title": "Karma and Effect",
            "year": "2004",
            "tracks": [
                {
                    "videoId": "AAAAAAAAAAA",
                    "title": "Shared Hymn",
                    "artists": [{"name": "Vesper Vale"}],
                    "duration_seconds": 200,
                },
            ],
            "other_versions": [
                {
                    "title": "Karma & Effect",
                    "browseId": "MPRE_A2",
                    "artists": [{"name": "Vesper Vale"}],
                },
            ],
        },
        {
            "title": "Karma & Effect",
            "year": "2004",
            "tracks": [
                {
                    "videoId": "AAAAAAAAAAA",
                    "title": "Shared Hymn",
                    "artists": [{"name": "Vesper Vale"}],
                    "duration_seconds": 200,
                },
            ],
        },
    ]
    results = resolve_album_group([src], yt, {}, delay=0.0)
    assert results[0].yt_video_id == "AAAAAAAAAAA"
    assert results[0].match_method.value == "album"


def test_album_search_uses_base_title() -> None:
    yt = MagicMock()
    queries: list[str] = []
    search_hits = [
        {
            "browseId": "MPRE_A",
            "title": "Ashen Light (Deluxe Edition)",
            "artists": [{"name": "Vesper Vale"}],
            "year": "2001",
        },
    ]

    def fake_search(query: str, **kwargs: Any) -> list[Any]:
        queries.append(query)
        return search_hits

    yt.search.side_effect = fake_search
    yt.get_album.return_value = {
        "title": "Ashen Light (Deluxe Edition)",
        "year": "2001",
        "tracks": [
            {
                "videoId": "AAAAAAAAAAA",
                "title": "Shared Hymn",
                "artists": [{"name": "Vesper Vale"}],
                "duration_seconds": 200,
            },
        ],
    }
    src_qualified = SourceTrack.from_dict(
        {
            "tidal_id": 1,
            "album_id": 7,
            "title": "Shared Hymn",
            "artists": ["Vesper Vale"],
            "album": "Ashen Light (Deluxe)",
            "duration": 200,
            "track_num": 1,
            "album_year": 2001,
        }
    )
    results = resolve_album_group([src_qualified], yt, {}, delay=0.0)
    assert queries == ["Ashen Light Vesper Vale"]
    assert results[0].yt_video_id == "AAAAAAAAAAA"


def test_near_certain_winner_ignores_close_second() -> None:
    src = SourceTrack.from_dict(
        {
            "tidal_id": 1,
            "album_id": 7,
            "title": "Shared Hymn",
            "artists": ["Vesper Vale"],
            "album": "Ashen Light (Deluxe)",
            "duration": 200,
            "track_num": 1,
            "album_year": 2001,
        }
    )
    yt = MagicMock()
    yt.search.return_value = [
        {
            "browseId": "MPRE_A",
            "title": "Ashen Light (Deluxe Edition)",
            "artists": [{"name": "Vesper Vale"}],
            "year": "2001",
        },
        {
            "browseId": "MPRE_B",
            "title": "Ashen Light (Deluxe Remaster)",
            "artists": [{"name": "Vesper Vale"}],
            "year": "2001",
        },
    ]

    def fake_get_album(browse_id: str) -> dict[str, Any]:
        title = (
            "Ashen Light (Deluxe Edition)"
            if browse_id == "MPRE_A"
            else "Ashen Light (Deluxe Remaster)"
        )
        return {
            "title": title,
            "year": "2001",
            "tracks": [
                {
                    "videoId": "AAAAAAAAAAA" if browse_id == "MPRE_A" else "BBBBBBBBBBB",
                    "title": "Shared Hymn",
                    "artists": [{"name": "Vesper Vale"}],
                    "duration_seconds": 200,
                },
            ],
        }

    yt.get_album.side_effect = fake_get_album
    results = resolve_album_group([src], yt, {}, delay=0.0)
    assert results[0].yt_video_id == "AAAAAAAAAAA"
    assert results[0].match_method.value == "album"
