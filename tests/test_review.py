from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from rich.console import Console

import tidal2ytm.plan_io as plan_io
import tidal2ytm.review as review_mod
from tidal2ytm.confidence import color_for
from tidal2ytm.errors import PlanNotFoundError
from tidal2ytm.models import TrackStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _review_track(**overrides: Any) -> dict[str, Any]:
    track: dict[str, Any] = {
        "tidal_id": 1,
        "artist": "Wren",
        "title": "Apple",
        "tidal_album": "Apple",
        "tidal_duration_sec": 200,
        "tidal_isrc": "",
        "tidal_track_num": 1,
        "yt_artist": "Wren",
        "yt_title": "Apple",
        "yt_album": "Apple",
        "yt_duration_sec": 200,
        "yt_isrc": "",
        "yt_album_track_num": 1,
        "yt_video_id": "AAAAAAAAAAA",
        "match_method": "fuzzy",
        "confidence": {"overall": 0.8},
        "status": "needs_review",
        "review_reason": "",
    }
    track.update(overrides)
    return track


def _write_plan_with_tracks(
    isolated_data_dir: Path, tracks: list[dict[str, Any]]
) -> tuple[Path, dict[str, Any]]:
    plan_path = isolated_data_dir / "transfer_plan.toml"
    plan: dict[str, Any] = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [{"name": "B", "match_id": "a/b", "tracks": tracks}],
            }
        ],
    }
    plan_io.save_plan(plan, plan_path)
    return plan_path, plan_io.load_plan(plan_path)


def _make_session(
    plan: dict[str, Any],
    filtered: list[dict[str, Any]],
    plan_path: Path,
    *,
    cursor: int = 0,
    backup_done: bool = False,
    track_context: dict[int, dict[str, Any]] | None = None,
) -> review_mod.ReviewSession:
    return review_mod.ReviewSession(
        plan=plan,
        plan_path=plan_path,
        backup_done=backup_done,
        cursor=cursor,
        filtered_tracks=filtered,
        track_context=track_context if track_context is not None else {},
    )


def _nav_plan() -> dict[str, Any]:
    return {
        "artists": [
            {
                "name": "Artist A",
                "match_id": "artist-a",
                "albums": [
                    {
                        "name": "Album X",
                        "match_id": "artist-a/album-x",
                        "tracks": [{"tidal_id": 1}, {"tidal_id": 2}],
                    },
                    {
                        "name": "Album Y",
                        "match_id": "artist-a/album-y",
                        "tracks": [{"tidal_id": 3}],
                    },
                ],
            },
            {
                "name": "Artist B",
                "match_id": "artist-b",
                "albums": [
                    {"name": "Album Z", "match_id": "artist-b/album-z", "tracks": [{"tidal_id": 4}]}
                ],
            },
        ]
    }


def _nav_ctx() -> dict[int, dict[str, Any]]:
    """Hand-built fixture mirroring the _nav_plan grouping: 2 albums for
    artist-a, 1 for artist-b — the navigation suite's input, not prod logic."""
    return {
        1: {
            "album_match_id": "artist-a/album-x",
            "artist_match_id": "artist-a",
            "album_name": "Album X",
            "pos_in_album": 1,
            "total_in_album": 2,
        },
        2: {
            "album_match_id": "artist-a/album-x",
            "artist_match_id": "artist-a",
            "album_name": "Album X",
            "pos_in_album": 2,
            "total_in_album": 2,
        },
        3: {
            "album_match_id": "artist-a/album-y",
            "artist_match_id": "artist-a",
            "album_name": "Album Y",
            "pos_in_album": 1,
            "total_in_album": 1,
        },
        4: {
            "album_match_id": "artist-b/album-z",
            "artist_match_id": "artist-b",
            "album_name": "Album Z",
            "pos_in_album": 1,
            "total_in_album": 1,
        },
    }


# ---------------------------------------------------------------------------
# Display / confidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.9, "green"),
        (0.3, "red"),
        (1.0, "green"),
        (0.86, "green"),
        (0.851, "green"),
        (0.85, "yellow"),
        (0.75, "yellow"),
        (0.70, "yellow"),
        (0.69, "red"),
    ],
)
def test_color_for_thresholds(value: float, expected: str) -> None:
    assert color_for(value) == expected


@pytest.mark.parametrize("field", ["yt_duration_sec", "tidal_duration_sec"])
def test_unknown_duration_renders_dash_without_warning(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, field: str
) -> None:
    plan_path, _ = _write_plan_with_tracks(isolated_data_dir, [_review_track(**{field: 0})])
    monkeypatch.setattr("tidal2ytm.review._review_readkey", lambda: "q")
    review_mod.run_review(status_filter=TrackStatus.NEEDS_REVIEW, plan_path=plan_path)
    out = capsys.readouterr().out
    assert "—" in out
    assert "⚠" not in out


# ---------------------------------------------------------------------------
# Indexed navigation (via the public KeyRouter)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["n", "p", "N", "P"])
def test_navigation_on_empty_list_keeps_cursor(tmp_path: Path, key: str) -> None:
    session = review_mod.ReviewSession(
        plan={"artists": []},
        plan_path=tmp_path / "plan.toml",
        backup_done=False,
        cursor=0,
        filtered_tracks=[],
        track_context={},
    )
    router = review_mod.KeyRouter(console=Console(), session=session)
    assert router.dispatch(key) is True
    assert session.cursor == 0


def test_step_navigation_cursors(isolated_data_dir: Path) -> None:
    plan = _nav_plan()
    filtered = [{"tidal_id": 1}, {"tidal_id": 2}, {"tidal_id": 3}, {"tidal_id": 4}]
    session = _make_session(
        plan, filtered, isolated_data_dir / "transfer_plan.toml", track_context=_nav_ctx()
    )
    router = review_mod.KeyRouter(console=Console(), session=session)
    # from track 0 (album-x), next album should be index 2 (album-y)
    router.dispatch("n")
    assert session.cursor == 2
    # next artist from 0 should be index 3 (artist-b)
    router.dispatch("N")
    assert session.cursor == 3

    # from track 1 (still album-x), next album should be index 2
    session.cursor = 1
    router.dispatch("n")
    assert session.cursor == 2
    # prev album from index 2 should go to 0
    session.cursor = 2
    router.dispatch("p")
    assert session.cursor == 0
    # prev artist from 3 should go to 0
    session.cursor = 3
    router.dispatch("P")
    assert session.cursor == 0

    # cursors at boundaries stay
    session.cursor = 3
    router.dispatch("n")
    assert session.cursor == 3
    router.dispatch("N")
    assert session.cursor == 3
    session.cursor = 0
    router.dispatch("p")
    assert session.cursor == 0
    router.dispatch("P")
    assert session.cursor == 0


def test_album_jump_uses_index_not_scan(isolated_data_dir: Path) -> None:
    # 500-track plan split into two 250-track albums; next-album from the
    # first track completes without IndexError and lands on the boundary
    plan = {
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "One",
                        "match_id": "a/one",
                        "tracks": [{"tidal_id": i} for i in range(250)],
                    },
                    {
                        "name": "Two",
                        "match_id": "a/two",
                        "tracks": [{"tidal_id": i} for i in range(250, 500)],
                    },
                ],
            }
        ]
    }
    filtered = [{"tidal_id": i} for i in range(500)]
    ctx = {
        i: {
            "album_match_id": "a/one" if i < 250 else "a/two",
            "artist_match_id": "a",
            "pos_in_album": 1,
            "total_in_album": 1,
        }
        for i in range(500)
    }
    session = _make_session(
        plan, filtered, isolated_data_dir / "transfer_plan.toml", track_context=ctx
    )
    router = review_mod.KeyRouter(console=Console(), session=session)
    router.dispatch("n")
    assert session.cursor == 250


# ---------------------------------------------------------------------------
# Decisions + persistence (via the public KeyRouter)
# ---------------------------------------------------------------------------


def test_review_backup_on_first_write(isolated_data_dir: Path) -> None:
    plan_path, loaded = _write_plan_with_tracks(
        isolated_data_dir,
        [
            {
                "tidal_id": 1,
                "title": "Song",
                "status": "pending",
                "yt_video_id": "AAAAAAAAAAA",
                "confidence": {"overall": 0.5},
            }
        ],
    )
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    session = _make_session(loaded, filtered, plan_path, backup_done=False)
    router = review_mod.KeyRouter(console=Console(), session=session)
    # first decision should trigger backup_plan
    with patch("tidal2ytm.review.backup_plan", wraps=plan_io.backup_plan) as mock_backup:
        router.dispatch_decision("s")
        mock_backup.assert_called_once_with(plan_path)
        assert session.backup_done is True
        # second decision should NOT trigger backup again
        mock_backup.reset_mock()
        router.dispatch_decision("a")
        mock_backup.assert_not_called()
    # ensure file reflects last status
    reloaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(reloaded))["status"] == "pending"


def test_dispatch_decision_updates_plan_and_meta(isolated_data_dir: Path) -> None:
    plan_path, loaded = _write_plan_with_tracks(
        isolated_data_dir,
        [
            {
                "tidal_id": 42,
                "title": "Song",
                "status": "needs_review",
                "yt_video_id": "AAAAAAAAAAA",
                "confidence": {"overall": 0.2},
            }
        ],
    )
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    session = _make_session(loaded, filtered, plan_path, backup_done=True)
    router = review_mod.KeyRouter(console=Console(), session=session)
    assert router.dispatch_decision("a") == "pending"
    assert filtered[0]["status"] == "pending"
    reloaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(reloaded))["status"] == "pending"
    assert reloaded["meta"]["pending"] == 1


# ---------------------------------------------------------------------------
# Single dispatch
# ---------------------------------------------------------------------------


def _decision_session(
    isolated_data_dir: Path,
) -> tuple[review_mod.ReviewSession, list[dict[str, Any]]]:
    plan_path, loaded = _write_plan_with_tracks(
        isolated_data_dir,
        [
            {
                "tidal_id": 1,
                "title": "Song",
                "status": "needs_review",
                "yt_video_id": "AAAAAAAAAAA",
                "confidence": {"overall": 0.5},
            },
            {
                "tidal_id": 2,
                "title": "Other",
                "status": "needs_review",
                "yt_video_id": "BBBBBBBBBBB",
                "confidence": {"overall": 0.5},
            },
        ],
    )
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    session = _make_session(loaded, filtered, plan_path, backup_done=True)
    return session, filtered


def test_single_dispatch_handles_decision_keys(isolated_data_dir: Path) -> None:
    session, _ = _decision_session(isolated_data_dir)
    router = review_mod.KeyRouter(console=Console(), session=session)
    # each decision key maps to exactly one persisted status
    assert router.dispatch_decision("a") == "pending"
    assert session.filtered_tracks[0]["status"] == "pending"
    assert router.dispatch_decision("s") == "skip"
    assert session.filtered_tracks[1]["status"] == "skip"


def test_dispatch_routes_navigation_and_quit(isolated_data_dir: Path) -> None:
    session, _ = _decision_session(isolated_data_dir)
    router = review_mod.KeyRouter(console=Console(), session=session)
    assert router.dispatch("k") is True
    assert session.cursor == 1
    # single album: next-album jump stays put
    assert router.dispatch("n") is True
    assert session.cursor == 1
    assert router.dispatch("q") is False


# ---------------------------------------------------------------------------
# Override (via the public KeyRouter "o" action)
# ---------------------------------------------------------------------------


def test_review_do_override_parses_url(isolated_data_dir: Path, monkeypatch: Any) -> None:
    plan_path, loaded = _write_plan_with_tracks(
        isolated_data_dir,
        [
            {
                "tidal_id": 99,
                "title": "Song",
                "status": "needs_review",
                "yt_video_id": "",
                "confidence": {"overall": 0.1},
            }
        ],
    )
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    session = _make_session(loaded, filtered, plan_path, backup_done=True)
    track = filtered[0]
    monkeypatch.setattr("builtins.input", lambda _prompt="": "https://youtu.be/CCCCCCCCCCC")
    router = review_mod.KeyRouter(console=Console(), session=session)
    router.dispatch("o")
    assert track["yt_video_id"] == "CCCCCCCCCCC"
    assert track["status"] == "pending"


def test_review_do_override_rejects_invalid_then_accepts(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    plan_path, loaded = _write_plan_with_tracks(
        isolated_data_dir,
        [
            {
                "tidal_id": 100,
                "title": "Song",
                "status": "needs_review",
                "yt_video_id": "",
                "confidence": {"overall": 0.1},
            }
        ],
    )
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    session = _make_session(loaded, filtered, plan_path, backup_done=True)
    track = filtered[0]
    inputs = iter(["not-a-url", "CCCCCCCCCCC"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    router = review_mod.KeyRouter(console=Console(), session=session)
    router.dispatch("o")
    assert track["yt_video_id"] == "CCCCCCCCCCC"


def test_override_resets_confidence_method_reason(
    isolated_data_dir: Path, monkeypatch: Any
) -> None:
    plan_path, loaded = _write_plan_with_tracks(
        isolated_data_dir,
        [
            {
                "tidal_id": 7,
                "title": "Song",
                "status": "needs_review",
                "yt_video_id": "",
                "match_method": "fuzzy",
                "confidence": {"overall": 0.62},
                "review_reason": "low",
            }
        ],
    )
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    session = _make_session(loaded, filtered, plan_path, backup_done=True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "https://youtu.be/AAAAAAAAAAA")
    router = review_mod.KeyRouter(console=Console(), session=session)
    router.dispatch("o")
    track = filtered[0]
    assert track["yt_video_id"] == "AAAAAAAAAAA"
    assert track["status"] == "pending"
    assert track["match_method"] == "none"
    assert track["confidence"] == {"overall": 0.0}
    assert track["review_reason"] == ""


# ---------------------------------------------------------------------------
# keys.read_key / read_line fallbacks + help pause guard
# ---------------------------------------------------------------------------


def test_read_key_falls_back_to_input(monkeypatch: pytest.MonkeyPatch) -> None:
    import tidal2ytm.keys as keys

    monkeypatch.setattr(keys, "HAS_READCHAR", False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "  k \n")
    assert keys.read_key() == "k"


def test_read_line_passes_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    import tidal2ytm.keys as keys

    monkeypatch.setattr("builtins.input", lambda _prompt="": f"{_prompt}:ok")
    assert keys.read_line("Go") == "Go:ok"


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_help_pause_guards_eof_and_interrupt(
    monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    def _raise(_prompt: str = "") -> str:
        raise error

    monkeypatch.setattr(review_mod, "read_line", _raise)
    session = review_mod.ReviewSession(
        plan={"artists": []},
        plan_path=Path("plan.toml"),
        backup_done=True,
        cursor=0,
        filtered_tracks=[_review_track()],
        track_context={},
    )
    # must not raise out of the help pause
    router = review_mod.KeyRouter(console=Console(record=True, width=120), session=session)
    router.dispatch("?")


# ---------------------------------------------------------------------------
# Context derivation (_build_track_context through the plan structure)
# ---------------------------------------------------------------------------


def test_build_track_context_derives_positions_from_plan(isolated_data_dir: Path) -> None:
    plan = _nav_plan()
    filtered = list(plan_io.iter_tracks_filtered(plan))
    ctx = review_mod._build_track_context(plan, filtered)  # pyright: ignore[reportPrivateUsage]
    # prod derivation must reproduce the hand-built navigation fixture exactly
    assert ctx == _nav_ctx()


def test_build_track_context_positions_relative_to_filtered_subset(
    isolated_data_dir: Path,
) -> None:
    plan = _nav_plan()
    filtered = [t for t in plan_io.iter_tracks_filtered(plan) if t["tidal_id"] in (2, 3)]
    ctx = review_mod._build_track_context(plan, filtered)  # pyright: ignore[reportPrivateUsage]
    # track 2 is alone in its album bucket once track 1 is filtered out
    assert ctx[2]["album_match_id"] == "artist-a/album-x"
    assert ctx[2]["pos_in_album"] == 1
    assert ctx[2]["total_in_album"] == 1
    assert ctx[3]["album_match_id"] == "artist-a/album-y"
    assert ctx[3]["pos_in_album"] == 1
    assert ctx[3]["total_in_album"] == 1


def test_run_review_renders_derived_album_positions(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    plan_path, _ = _write_plan_with_tracks(
        isolated_data_dir,
        [
            _review_track(tidal_id=1, title="Song", yt_video_id="AAAAAAAAAAA"),
            _review_track(tidal_id=2, title="Other", yt_video_id="BBBBBBBBBBB"),
        ],
    )
    monkeypatch.setattr("tidal2ytm.review._review_readkey", lambda: "q")
    review_mod.run_review(status_filter=TrackStatus.NEEDS_REVIEW, plan_path=plan_path)
    out = capsys.readouterr().out
    # Album header with derived match_id is rendered through the full run path.
    assert "a/b" in out


# ---------------------------------------------------------------------------
# Override exhaustion + backup failure
# ---------------------------------------------------------------------------


def test_override_exhausts_invalid_inputs_then_eof_returns_cleanly(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    plan_path, loaded = _write_plan_with_tracks(
        isolated_data_dir,
        [
            {
                "tidal_id": 55,
                "title": "Song",
                "status": "needs_review",
                "yt_video_id": "",
                "confidence": {"overall": 0.1},
            }
        ],
    )
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    session = _make_session(loaded, filtered, plan_path, backup_done=True)
    track = filtered[0]
    replies = iter(["bad-one", "bad-two", "bad-three"])

    def _read_line(_prompt: str = "") -> str:
        try:
            return next(replies)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(review_mod, "read_line", _read_line)
    router = review_mod.KeyRouter(console=Console(record=True, width=120), session=session)
    router.dispatch("o")  # must exit the override loop, not raise
    assert track["yt_video_id"] == ""
    assert track["status"] == "needs_review"
    assert "Could not parse" in capsys.readouterr().out


def test_failing_backup_leaves_decision_unpersisted(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path, loaded = _write_plan_with_tracks(
        isolated_data_dir,
        [_review_track(tidal_id=1)],
    )
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    session = _make_session(loaded, filtered, plan_path, backup_done=False)

    def _boom(path: Path) -> Path:
        del path
        raise OSError("backup failed")

    monkeypatch.setattr(review_mod, "backup_plan", _boom)
    router = review_mod.KeyRouter(console=Console(), session=session)
    with pytest.raises(OSError):
        router.dispatch_decision("s")
    # the decision was applied in memory only — nothing reached the plan file
    reloaded = plan_io.load_plan(plan_path)
    assert next(plan_io.iter_tracks(reloaded))["status"] == "needs_review"
    assert session.backup_done is False


# ---------------------------------------------------------------------------
# run_review entry points
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Review list: album grouping, stacked diff rows, modes
# ---------------------------------------------------------------------------


def _list_ctx() -> dict[int, dict[str, Any]]:
    return {
        1: {
            "album_match_id": "a/one",
            "artist_match_id": "a",
            "album_name": "One",
            "pos_in_album": 1,
            "total_in_album": 2,
        },
        2: {
            "album_match_id": "a/one",
            "artist_match_id": "a",
            "album_name": "One",
            "pos_in_album": 2,
            "total_in_album": 2,
        },
        3: {
            "album_match_id": "a/two",
            "artist_match_id": "a",
            "album_name": "Two",
            "pos_in_album": 1,
            "total_in_album": 1,
        },
    }


def _list_session(isolated_data_dir: Path) -> review_mod.ReviewSession:
    filtered = [
        _review_track(tidal_id=1, title="Song"),
        _review_track(tidal_id=2, title="Other"),
        _review_track(tidal_id=3, title="Third"),
    ]
    return _make_session(
        {"artists": []},
        filtered,
        isolated_data_dir / "transfer_plan.toml",
        backup_done=True,
        track_context=_list_ctx(),
    )


def test_build_review_rows_groups_albums_in_plan_order(isolated_data_dir: Path) -> None:
    session = _list_session(isolated_data_dir)
    rows = review_mod.build_review_rows(session.filtered_tracks, session.track_context)
    assert [(r.kind, r.index) for r in rows] == [
        ("album", -1),
        ("track", 0),
        ("track", 1),
        ("album", -1),
        ("track", 2),
    ]
    assert rows[0].album_match_id == "a/one"
    assert rows[0].album_size == 2
    assert rows[3].album_match_id == "a/two"
    assert rows[3].album_size == 1


def test_review_lines_stack_three_lines_per_track(isolated_data_dir: Path) -> None:
    session = _list_session(isolated_data_dir)
    rows = review_mod.build_review_rows(session.filtered_tracks, session.track_context)
    lines, first_line = review_mod.review_lines(session, rows)
    # 2 album headers + 3 tracks x 3 stacked lines
    assert len(lines) == 2 + 3 * 3
    assert first_line == {0: 1, 1: 4, 2: 8}
    # cursor marker only on the cursor track's head line
    assert not lines[1].plain.startswith("  ")
    assert lines[4].plain.startswith("  ")
    # diff markers on the stacked source/match lines
    assert lines[2].plain.strip().startswith("- Tidal")
    assert lines[3].plain.strip().startswith("+ YTM")


def test_review_lines_flag_album_mismatch(isolated_data_dir: Path) -> None:
    session = _list_session(isolated_data_dir)
    session.filtered_tracks[0]["yt_album"] = "One (Deluxe Version)"
    rows = review_mod.build_review_rows(session.filtered_tracks, session.track_context)
    lines, _ = review_mod.review_lines(session, rows)
    assert "⚠" in lines[1].plain


def test_detail_body_stacks_source_and_match(isolated_data_dir: Path) -> None:
    session = _list_session(isolated_data_dir)
    body = review_mod.detail_body(session.filtered_tracks[0])
    plain = body.plain
    assert "Tidal source" in plain
    assert "YTM match" in plain
    assert "fuzzy" in plain
    assert "needs_review" in plain
    assert "music.youtube.com/watch?v=AAAAAAAAAAA" in plain


def test_detail_body_shows_exact_match_label_for_isrc(isolated_data_dir: Path) -> None:
    session = _list_session(isolated_data_dir)
    track = session.filtered_tracks[0]
    track["match_method"] = "isrc"
    track["confidence"] = {"overall": 1.0}
    assert "exact match" in review_mod.detail_body(track).plain


def test_view_toggle_keys_switch_modes(isolated_data_dir: Path) -> None:
    session = _list_session(isolated_data_dir)
    router = review_mod.KeyRouter(console=Console(), session=session)
    assert router.dispatch("v") is True
    assert session.mode == "detail"
    assert router.dispatch("v") is True
    assert session.mode == "list"
    assert router.dispatch("\r") is True
    assert session.mode == "detail"
    # Esc backs out of detail, quits from the list
    assert router.dispatch("\x1b") is True
    assert session.mode == "list"
    assert router.dispatch("\x1b") is False


def test_decisions_apply_in_detail_mode_without_leaving(isolated_data_dir: Path) -> None:
    plan_path, loaded = _write_plan_with_tracks(
        isolated_data_dir,
        [_review_track(tidal_id=1, status="needs_review", yt_video_id="AAAAAAAAAAA")],
    )
    filtered = list(plan_io.iter_tracks_filtered(loaded))
    session = _make_session(loaded, filtered, plan_path, backup_done=True)
    session.mode = "detail"
    router = review_mod.KeyRouter(console=Console(), session=session)
    assert router.dispatch("a") is True
    assert filtered[0]["status"] == "pending"
    assert session.mode == "detail"
    assert session.flash != ""


def test_wheel_moves_cursor_click_is_swallowed(isolated_data_dir: Path) -> None:
    session = _list_session(isolated_data_dir)
    router = review_mod.KeyRouter(console=Console(), session=session)
    assert router.dispatch("\x1b[<65;1;1M") is True  # wheel down
    assert session.cursor == 1
    assert router.dispatch("\x1b[<64;1;1M") is True  # wheel up
    assert session.cursor == 0
    assert router.dispatch("\x1b[<0;1;1M") is True  # click: no move, no noise
    assert session.cursor == 0
    assert session.flash == ""


def test_list_frame_renders_head_and_footer(isolated_data_dir: Path) -> None:
    session = _list_session(isolated_data_dir)
    console = Console(record=True, width=100)
    console.print(review_mod.review_frame(session, 12))
    out = console.export_text()
    # The footer must advertise the v toggle (README-disclosed) in both modes.
    assert "v" in out
    session.mode = "detail"
    console = Console(record=True, width=100)
    console.print(review_mod.review_frame(session, 12))
    assert "v" in console.export_text()


def test_run_review_missing_plan_raises_not_exits(tmp_path: Path) -> None:
    with pytest.raises(PlanNotFoundError):
        review_mod.run_review(plan_path=tmp_path / "missing.toml")


def test_review_run_no_tracks_match_returns(capsys: Any, isolated_data_dir: Path) -> None:
    plan_path, _ = _write_plan_with_tracks(
        isolated_data_dir,
        [
            {
                "tidal_id": 1,
                "title": "Song",
                "status": "pending",
                "yt_video_id": "AAAAAAAAAAA",
            }
        ],
    )
    # filter for status that does not exist
    review_mod.run_review(status_filter=TrackStatus.NEEDS_REVIEW, plan_path=plan_path)
    out = capsys.readouterr().out
    assert "No tracks" in out
