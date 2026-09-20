from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tidal2ytm.models import SourceTrack
from tidal2ytm.planning import PlanningSession, run_match_action, toggle_select


def _src(
    tidal_id: int = 1,
    title: str = "Apple",
    artist: str = "Wren",
    album: str = "Apple",
    album_id: int = 11,
    year: int | None = 1971,
    track: int = 1,
    disc: int = 1,
    duration: int = 200,
) -> SourceTrack:
    return SourceTrack(
        tidal_id=tidal_id,
        title=title,
        artist=artist,
        artists=[artist],
        album=album,
        album_id=album_id,
        album_year=year,
        duration_sec=duration,
        isrc=None,
        track_num=track,
        disc_num=disc,
        version=None,
    )


def test_toggle_select_adds_and_removes() -> None:
    sel: dict[int, SourceTrack] = {}
    assert toggle_select(sel, _src()) is True
    assert toggle_select(sel, _src()) is False
    assert sel == {}


def test_match_action_confirms_before_matching(tmp_path: Path) -> None:
    from tidal2ytm import planning as planning_mod

    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[_src()], selection={1: _src()}
    )
    with patch.object(planning_mod, "match_track") as mock_match:
        counts = run_match_action(session, MagicMock(), input_fn=lambda _p: "n")
        mock_match.assert_not_called()
        assert counts == {"new": 0, "upgraded": 0, "kept": 0, "skipped": 0}


def test_match_action_adds_new_match(tmp_path: Path) -> None:
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, TrackStatus

    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[_src()], selection={1: _src()}
    )
    result = MatchResult(
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
    with patch.object(planning_mod, "match_track", return_value=result):
        counts = run_match_action(session, MagicMock(), input_fn=lambda _p: "Y")
    assert counts == {"new": 1, "upgraded": 0, "kept": 0, "skipped": 0}
    assert session.plan_path.exists()


def test_match_action_prompts_on_differing_rematch(tmp_path: Path) -> None:
    from tidal2ytm import plan_io
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, TrackStatus

    plan_path = tmp_path / "transfer_plan.toml"
    src = _src()
    old = MatchResult(
        source=src,
        yt_video_id="AAAAAAAAAAA",
        yt_title="Apple",
        yt_artist="Wren",
        yt_album="Apple",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.8),
        status=TrackStatus.PENDING,
    )
    from tidal2ytm.planning_merge import insert_track, match_result_to_track_dict

    plan: dict[str, Any] = {"meta": {}, "artists": []}
    insert_track(plan, match_result_to_track_dict(old), 1971)
    plan_io.update_plan_meta(plan)
    plan_io.save_plan(plan, plan_path)

    new = MatchResult(
        source=src,
        yt_video_id="BBBBBBBBBBB",
        yt_title="Apple",
        yt_artist="Wren",
        yt_album="Apple",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.95),
        status=TrackStatus.PENDING,
    )
    answers = iter(["Y", "n"])
    session = PlanningSession(plan_path=plan_path, liked=[src], selection={1: src})
    with patch.object(planning_mod, "match_track", return_value=new):
        counts = run_match_action(session, MagicMock(), input_fn=lambda _p: next(answers))
    assert counts["kept"] == 1 and counts["upgraded"] == 0
    kept = plan_io.find_existing_match(plan_io.load_plan(plan_path), 1)
    assert kept is not None and kept["yt_video_id"] == "AAAAAAAAAAA"


def test_match_action_stores_unmatched_on_error(tmp_path: Path) -> None:
    from tidal2ytm import plan_io
    from tidal2ytm import planning as planning_mod

    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[_src()], selection={1: _src()}
    )

    def _boom(_track: SourceTrack, _yt: Any) -> Any:
        raise RuntimeError("yt exploded")

    with patch.object(planning_mod, "match_track", side_effect=_boom):
        counts = run_match_action(session, MagicMock(), input_fn=lambda _p: "Y")
    assert counts == {"new": 1, "upgraded": 0, "kept": 0, "skipped": 0}
    stored = plan_io.find_existing_match(plan_io.load_plan(session.plan_path), 1)
    assert stored is not None and stored["status"] == "needs_review"


def test_match_action_override_skips_prompt(tmp_path: Path) -> None:
    from tidal2ytm import plan_io
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, TrackStatus
    from tidal2ytm.planning_merge import insert_track, match_result_to_track_dict

    plan_path = tmp_path / "transfer_plan.toml"
    src = _src()
    old = MatchResult(
        source=src,
        yt_video_id="AAAAAAAAAAA",
        yt_title="Apple",
        yt_artist="Wren",
        yt_album="Apple",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.8),
        status=TrackStatus.PENDING,
    )
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    insert_track(plan, match_result_to_track_dict(old), 1971)
    plan_io.update_plan_meta(plan)
    plan_io.save_plan(plan, plan_path)

    new = MatchResult(
        source=src,
        yt_video_id="BBBBBBBBBBB",
        yt_title="Apple",
        yt_artist="Wren",
        yt_album="Apple",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.95),
        status=TrackStatus.PENDING,
    )
    prompts: list[str] = []
    session = PlanningSession(plan_path=plan_path, liked=[src], selection={1: src})
    session.override = True

    def _answer(prompt: str) -> str:
        prompts.append(prompt)
        return "Y"

    with patch.object(planning_mod, "match_track", return_value=new):
        counts = run_match_action(session, MagicMock(), input_fn=_answer)
    assert counts == {"new": 0, "upgraded": 1, "kept": 0, "skipped": 0}
    assert len(prompts) == 1  # confirm only; no per-track prompt in override mode
    stored = plan_io.find_existing_match(plan_io.load_plan(plan_path), 1)
    assert stored is not None and stored["yt_video_id"] == "BBBBBBBBBBB"


def _match_result(video_id: str, confidence: float) -> Any:
    from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, TrackStatus

    return MatchResult(
        source=_src(),
        yt_video_id=video_id,
        yt_title="Apple",
        yt_artist="Wren",
        yt_album="Apple",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=confidence),
        status=TrackStatus.PENDING,
    )


def test_match_action_prompt_shows_both_sides(tmp_path: Path) -> None:
    from tidal2ytm import plan_io
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.planning_merge import insert_track, match_result_to_track_dict

    plan_path = tmp_path / "transfer_plan.toml"
    src = _src()
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    insert_track(plan, match_result_to_track_dict(_match_result("AAAAAAAAAAA", 0.8)), 1971)
    plan_io.update_plan_meta(plan)
    plan_io.save_plan(plan, plan_path)

    prompts: list[str] = []
    answers = iter(["Y", "y"])
    session = PlanningSession(plan_path=plan_path, liked=[src], selection={1: src})

    def _answer(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    with patch.object(planning_mod, "match_track", return_value=_match_result("BBBBBBBBBBB", 0.95)):
        counts = run_match_action(session, MagicMock(), input_fn=_answer)
    assert counts == {"new": 0, "upgraded": 1, "kept": 0, "skipped": 0}
    assert len(prompts) == 2
    assert "AAAAAAAAAAA" in prompts[1] and "BBBBBBBBBBB" in prompts[1]
    assert "0.80" in prompts[1] and "0.95" in prompts[1]


def test_match_action_factory_login_after_confirm(tmp_path: Path) -> None:
    from tidal2ytm import planning as planning_mod

    logins: list[str] = []

    def _factory() -> Any:
        logins.append("login")
        return MagicMock()

    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[_src()], selection={1: _src()}
    )
    counts = run_match_action(session, yt_factory=_factory, input_fn=lambda _p: "n")
    assert logins == []
    assert counts == {"new": 0, "upgraded": 0, "kept": 0, "skipped": 0}
    with patch.object(planning_mod, "match_track", return_value=_match_result("AAAAAAAAAAA", 0.9)):
        counts = run_match_action(session, yt_factory=_factory, input_fn=lambda _p: "Y")
    assert logins == ["login"]
    assert counts == {"new": 1, "upgraded": 0, "kept": 0, "skipped": 0}


def test_key_hints_render_all_keys() -> None:
    from tidal2ytm.planning import RESULTS_HINTS, key_hints

    plain = key_hints(RESULTS_HINTS).plain
    for keys, rest, _style in RESULTS_HINTS:
        for k in keys:
            assert k in plain
        assert rest in plain
    assert " | " in key_hints([(("a", "b"), "oth", "bold bright_blue")]).plain


def test_hot_hint_is_colour_only_letter() -> None:
    from tidal2ytm.planning import hot_hint

    assert hot_hint("select all from ", "A", "rtist").plain == "select all from Artist"
    assert hot_hint("", "*", " to toggle all").plain == "* to toggle all"


def test_menu_body_shows_counts() -> None:
    from tidal2ytm.planning import menu_body, status_body

    session = PlanningSession(
        plan_path=Path("x.toml"),
        liked=[_src(1), _src(2)],
        selection={1: _src(1)},
        library_loaded=True,
    )
    assert (
        "select everything in your library (2 tracks) or view the 1 selected across searches"
        in menu_body(session).plain
    )
    assert "2 in library" in status_body(session).plain
    assert "1 selected" in status_body(session).plain


def test_menu_selection_line_singular_track() -> None:
    from tidal2ytm.planning import menu_body

    session = PlanningSession(
        plan_path=Path("x.toml"), liked=[_src(1)], selection={}, library_loaded=True
    )
    assert "select everything in your library (1 track) or " in menu_body(session).plain


def test_menu_selection_line_hides_counts_when_library_not_loaded() -> None:
    from tidal2ytm.planning import menu_body

    body = menu_body(PlanningSession(plan_path=Path("x.toml"), liked=[], selection={}))
    assert "library not loaded, cannot select or view tracks — authenticate" in body.plain
    assert "from the 0" not in body.plain


def test_menu_body_shows_override_banner() -> None:
    from tidal2ytm.planning import PlanningSession, menu_body

    on = menu_body(PlanningSession(plan_path=Path("x.toml"), liked=[], selection={}, override=True))
    assert "OVERRIDE" in on.plain
    off = menu_body(PlanningSession(plan_path=Path("x.toml"), liked=[], selection={}))
    assert "OVERRIDE" not in off.plain


def test_track_row_marks_selection() -> None:
    from tidal2ytm.planning import track_row

    selected = track_row(1, True, _src()).plain
    unselected = track_row(1, False, _src()).plain
    assert selected != unselected
    assert "Apple" in unselected and "1971" in unselected and "3:20" in unselected


def test_drain_escape_swallows_click_burst() -> None:
    from tidal2ytm.planning import drain_escape

    stream = ["<", "0", ";", "1", "0", ";", "2", "0", "M"]
    seen: list[str] = []

    def _ready() -> bool:
        return bool(stream)

    def _one() -> str:
        ch = stream.pop(0)
        seen.append(ch)
        return ch

    drain_escape(_ready, _one)
    assert seen == ["<", "0", ";", "1", "0", ";", "2", "0", "M"]
    assert stream == []


def test_drain_escape_bare_esc_reads_nothing() -> None:
    from tidal2ytm.planning import drain_escape

    calls: list[str] = []
    drain_escape(lambda: False, lambda: calls.append("read") or "?")
    assert calls == []


def test_help_text_positive_smoke() -> None:
    from tidal2ytm.planning import HELP_TEXT

    assert "Select everything (whole library)" in HELP_TEXT
    assert "'*' selects all" in HELP_TEXT
    assert "Review all plan matches" in HELP_TEXT
    assert "Quit tidal2ytm" in HELP_TEXT


def test_menu_body_separator_and_hotkeys() -> None:
    from tidal2ytm.planning import menu_body

    plain = menu_body(PlanningSession(plan_path=Path("x.toml"), liked=[], selection={})).plain
    assert "─" in plain
    assert "? | h" in plain and "ctrl+o" in plain and "/ | s" in plain
    assert "override" in plain


def test_read_key_filters_control_codes(monkeypatch: Any) -> None:
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.planning import read_key

    class _FakeReadchar:
        def __init__(self, keys: list[str]) -> None:
            self.keys = keys

        def readkey(self) -> str:
            return self.keys.pop(0)

    monkeypatch.setattr(
        planning_mod, "readchar", _FakeReadchar(["\x00", "", "e", "\x0f", "\r", "\x03"])
    )
    assert read_key() is None
    assert read_key() is None
    assert read_key() == "e"
    assert read_key() == "\x0f"
    assert read_key() == "\r"
    assert read_key() == "\x03"


def _picker_hits() -> list[SourceTrack]:
    return [
        _src(1, "Nightshade", "Slate", "Nightshade", 101),
        _src(2, "Ember", "Slate", "Nightshade", 101),
        _src(3, "Apple", "Wren", "Apple", 102),
    ]


def test_default_grouping_by_result_count() -> None:
    from tidal2ytm import planning as planning_mod

    assert planning_mod.default_grouping(0) == "none"
    assert planning_mod.default_grouping(19) == "none"
    assert planning_mod.default_grouping(20) == "artist"
    assert planning_mod.default_grouping(50) == "artist"
    assert planning_mod.default_grouping(51) == "both"
    assert planning_mod.default_grouping(200) == "both"


def test_build_rows_flat_without_grouping() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_picker_hits(), "none")
    assert [r.kind for r in rows] == ["track"] * 3
    assert [r.track.tidal_id for r in rows if r.track is not None] == [3, 2, 1]


def test_build_rows_groups_by_artist_with_indent() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_picker_hits(), "artist")
    assert [(r.kind, r.indent) for r in rows] == [
        ("artist", 0),
        ("track", 2),
        ("track", 2),
        ("artist", 0),
        ("track", 2),
    ]
    assert rows[0].artist == "Slate"


def test_build_rows_nests_album_under_artist() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_picker_hits(), "both")
    assert [(r.kind, r.indent) for r in rows] == [
        ("artist", 0),
        ("album", 2),
        ("track", 4),
        ("track", 4),
        ("artist", 0),
        ("album", 2),
        ("track", 4),
    ]
    assert rows[1].album == "Nightshade"


def test_toggle_row_flips_track() -> None:
    from tidal2ytm import planning as planning_mod

    sel: dict[int, SourceTrack] = {}
    rows = planning_mod.build_rows(_picker_hits(), "none")
    planning_mod.toggle_row(sel, rows[0])
    assert set(sel) == {3}
    planning_mod.toggle_row(sel, rows[0])
    assert sel == {}


def test_toggle_row_header_selects_then_clears_group() -> None:
    from tidal2ytm import planning as planning_mod

    sel: dict[int, SourceTrack] = {}
    rows = planning_mod.build_rows(_picker_hits(), "artist")
    planning_mod.toggle_row(sel, rows[0])
    assert set(sel) == {1, 2}
    planning_mod.toggle_row(sel, rows[0])
    assert sel == {}


def test_toggle_scope_artist_album_all() -> None:
    from tidal2ytm import planning as planning_mod

    hits = _picker_hits()
    sel: dict[int, SourceTrack] = {}
    planning_mod.toggle_scope(sel, hits, artist="Slate")
    assert set(sel) == {1, 2}
    planning_mod.toggle_scope(sel, hits, album="Nightshade")
    assert sel == {}
    planning_mod.toggle_scope(sel, hits)
    assert set(sel) == {1, 2, 3}
    planning_mod.toggle_scope(sel, hits)
    assert sel == {}


def test_visible_window_follows_cursor() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_picker_hits() * 10, "none")
    start, end = planning_mod.visible_window(rows, cursor=0, height=10)
    assert (start, end) == (0, 10)
    start, end = planning_mod.visible_window(rows, cursor=29, height=10)
    assert start <= 29 < end and end - start == 10
    start, end = planning_mod.visible_window(rows, cursor=5, height=10)
    assert start <= 5 < end


def test_picker_keys_toggle_and_confirm(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [
        planning_mod.readchar_key.SPACE,
        planning_mod.readchar_key.DOWN,
        planning_mod.readchar_key.SPACE,
        planning_mod.readchar_key.ENTER,
    ]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert set(session.selection) == {2, 3}


def test_picker_esc_confirmed_restores_snapshot(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.readchar_key.SPACE, planning_mod.readchar_key.ESC]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(
        Console(),
        session,
        _picker_hits(),
        title="Search",
        height=24,
        input_fn=lambda _p: "y",
    )
    assert session.selection == {}


def _album_hits() -> list[SourceTrack]:
    return [
        _src(1, "Ember", "Slate", "Nightshade", 101, year=1970, track=2),
        _src(2, "Nightshade", "Slate", "Nightshade", 101, year=1970, track=1),
        _src(3, "Apple", "Wren", "Apple", 102, year=1971, track=1, disc=1),
        _src(4, "B-Side", "Wren", "Rarities", 103, year=None, track=1),
        _src(5, "Deep Cut", "Wren", "Apple", 102, year=1971, track=1, disc=2),
    ]


def test_build_rows_sorts_artists_albums_tracks() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_album_hits(), "both")
    assert rows[0].kind == "artist" and rows[0].artist == "Slate"
    willow = [
        r.track.title
        for r in rows
        if r.kind == "track" and r.artist == "Slate" and r.track is not None
    ]
    assert willow == ["Nightshade", "Ember"]
    wren_albums = [r.album for r in rows if r.kind == "album" and r.artist == "Wren"]
    assert wren_albums == ["Apple", "Rarities"]


def test_build_rows_disc_headers_only_when_multi_disc() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_album_hits(), "both")
    discs = [(r.album, r.members[0].disc_num) for r in rows if r.kind == "disc"]
    assert discs == [("Apple", 1), ("Apple", 2)]


def test_build_rows_flat_sorts_by_title() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_album_hits(), "none")
    titles = [r.track.title for r in rows if r.track is not None]
    assert titles == ["Apple", "B-Side", "Deep Cut", "Ember", "Nightshade"]


def test_build_rows_artist_mode_tracks_alpha() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_album_hits(), "artist")
    willow = [
        r.track.title
        for r in rows
        if r.kind == "track" and r.artist == "Slate" and r.track is not None
    ]
    assert willow == ["Ember", "Nightshade"]


def test_row_text_track_format_in_album_grouping() -> None:
    from tidal2ytm.planning import ListRow, row_text

    t = _src(
        7, "Wanderer", "Slate", "Seven (Deluxe Version)", 104, year=1971, track=3, duration=300
    )
    row = ListRow("track", artist=t.artist, album=t.album, track=t, indent=4)
    plain = row_text(row, {}, True, "both").plain
    assert "03." in plain and "Wanderer" in plain and "1971" in plain and "5:00" in plain
    assert plain != row_text(row, {}, False, "both").plain


def test_mark_style_selected_and_committed_are_green() -> None:
    from rich.console import Console

    from tidal2ytm.planning import ListRow, row_text

    console = Console()
    t = _src(7, "Wanderer", "Slate", "Seven (Deluxe Version)", 104, year=1971, track=3)
    row = ListRow("track", artist=t.artist, album=t.album, track=t)
    # Mark sits at offset 2: no indent and the two-space cursor pad.
    for text in (
        row_text(row, {7: t}, False, "both"),
        row_text(row, {}, False, "both", committed=frozenset({7})),
    ):
        color = text.get_style_at_offset(console, 2).color
        assert color is not None and "green" in color.name


def test_row_text_committed_shows_check() -> None:
    from tidal2ytm.planning import ListRow, row_text

    t = _src(7, "Wanderer", "Slate", "Seven (Deluxe Version)", 104, year=1971, track=3)
    row = ListRow("track", artist=t.artist, album=t.album, track=t, indent=4)
    plain = row_text(row, {7: t}, False, "both", committed=frozenset({7})).plain
    assert "✓" in plain and "■" not in plain


def test_row_text_cursor_visible_on_cursor_row_only() -> None:
    from tidal2ytm.planning import ListRow, row_text

    t = _src(
        7, "Wanderer", "Slate", "Seven (Deluxe Version)", 104, year=1971, track=3, duration=300
    )
    row = ListRow("track", artist=t.artist, album=t.album, track=t, indent=4)
    cursor_row = row_text(row, {}, True, "both").plain
    assert cursor_row != row_text(row, {}, False, "both").plain


def test_row_text_omits_missing_year() -> None:
    from tidal2ytm.planning import ListRow, row_text

    t = _src(4, "B-Side", "Wren", "Rarities", 103, year=None)
    row = ListRow("track", artist=t.artist, album=t.album, track=t, indent=2)
    plain = row_text(row, {}, False, "artist").plain
    assert "?" not in plain
    assert "B-Side" in plain and "Rarities" in plain and "3:20" in plain


def test_row_text_no_number_outside_album_grouping() -> None:
    from tidal2ytm.planning import ListRow, row_text

    t = _src(
        7, "Wanderer", "Slate", "Seven (Deluxe Version)", 104, year=1971, track=3, duration=300
    )
    row = ListRow("track", artist=t.artist, album=t.album, track=t, indent=2)
    plain = row_text(row, {}, False, "artist").plain
    assert "03." not in plain
    assert "Wanderer" in plain and "Seven (Deluxe Version)" in plain and "5:00" in plain


def test_row_text_header_tristate() -> None:
    from tidal2ytm.planning import build_rows, row_text

    rows = build_rows(_album_hits(), "artist")
    header = rows[0]
    empty = row_text(header, {}, False, "artist").plain
    partial = row_text(header, {1: _src(1)}, False, "artist").plain
    full = row_text(header, {1: _src(1), 2: _src(2)}, False, "artist").plain
    # tri-state: none / some / all selected render three distinct rows
    assert len({empty, partial, full}) == 3


def test_row_text_header_rows_show_labels() -> None:
    from tidal2ytm.planning import build_rows, row_text

    rows = build_rows(_album_hits(), "both")
    artist_row = next(r for r in rows if r.kind == "artist")
    album_row = next(r for r in rows if r.kind == "album")
    disc_row = next(r for r in rows if r.kind == "disc")
    assert "Slate" in row_text(artist_row, {}, False, "both").plain
    assert "Nightshade" in row_text(album_row, {}, False, "both").plain
    assert "Disc" in row_text(disc_row, {}, False, "both").plain


def test_row_text_dedups_grouped_labels() -> None:
    from tidal2ytm.planning import ListRow, build_rows, row_text

    rows = build_rows(_album_hits(), "both")
    album_row = next(r for r in rows if r.kind == "album")
    album_plain = row_text(album_row, {}, False, "both").plain
    # grouped album label appears exactly once; the member count is shown
    assert album_plain.count("Nightshade") == 1
    assert "selected" in album_plain
    t = _src(2, "Nightshade", "Slate", "Nightshade", 101, year=1970, track=1)
    flat = ListRow("track", artist=t.artist, album=t.album, track=t)
    assert "Slate - Nightshade" in row_text(flat, {}, False, "none").plain


def test_row_text_never_wraps() -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    t = _src(
        7,
        "A Bit of Finger / Sleeping Village / Warning (2014 Remaster)",
        "Slate",
        "Slate (2014 Remaster)",
        104,
        year=2014,
        track=5,
        duration=553,
    )
    row = planning_mod.ListRow("track", artist=t.artist, album=t.album, track=t, indent=4)
    narrow = Console(width=40)
    lines = narrow.render_lines(planning_mod.row_text(row, {}, True, "both"), narrow.options)
    assert len(lines) == 1


def test_viewport_height_budgets_chrome() -> None:
    from tidal2ytm import planning as planning_mod

    assert planning_mod.viewport_height(30, 2) == 20
    assert planning_mod.viewport_height(54, 5) == 41
    assert planning_mod.viewport_height(10, 2) == 4


def test_picker_frame_fits_narrow_viewport() -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod
    from tidal2ytm.picker_rows import PickerView

    term_height, width = 30, 60
    narrow = Console(width=width)
    footer_h = planning_mod.footer_lines(narrow, "both", False)
    view_h = planning_mod.viewport_height(term_height, footer_h)
    rows = planning_mod.build_rows(_album_hits(), "both")
    view = PickerView(
        title="Search: ",
        title_term="x",
        rows=rows,
        cursor=0,
        selection={},
        grouping="both",
        hits_total=5,
        height=view_h,
        clearable=False,
        notice="",
    )
    screen = planning_mod.picker_frame(view)
    assert len(narrow.render_lines(screen, narrow.options)) <= term_height - 1


def test_picker_bar_grouping_label_arrows_confirm() -> None:
    from tidal2ytm.planning import picker_bar

    bar = picker_bar("both", False).plain
    assert "grouping: artist + album" in bar
    assert "Enter confirm" in bar and "Esc cancel" in bar
    assert "new search" in bar


def test_picker_bar_advertises_wheel_scroll() -> None:
    from tidal2ytm.planning import picker_bar

    assert "wheel" in picker_bar("both", False).plain


def test_picker_unknown_sequence_ignored(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = ["\x1b[Z", planning_mod.readchar_key.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    drained: list[str] = []
    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    monkeypatch.setattr(planning_mod, "_drain_tail", lambda: drained.append("drain"))
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert drained == ["drain"]
    assert session.selection == {}


def test_picker_wheel_down_moves_cursor_three(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = ["\x1b[<65;1;1M", " ", planning_mod.readchar_key.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert set(session.selection) == {1}


def test_picker_wheel_sentinel_moves_cursor(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.WHEEL_DOWN, " ", planning_mod.readchar_key.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert set(session.selection) == {1}


def test_resolve_search_delegates_to_search_library(monkeypatch: Any, tmp_path: Path) -> None:
    from tidal2ytm import planning as planning_mod

    seen: dict[str, str] = {}

    def _fake_search(liked: list[SourceTrack], query: str) -> tuple[list[SourceTrack], bool]:
        seen["query"] = query
        return [], True

    monkeypatch.setattr(planning_mod, "search_library", _fake_search)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[_src()])
    assert planning_mod.resolve_search(session, "willow") == ([], True)
    assert seen == {"query": "willow"}


def test_picker_head_shows_notice() -> None:
    from tidal2ytm.planning import picker_head

    notice = 'No matches for "blashen" — closest:'
    plain = picker_head("Search: ", "blashen", 3, 0, notice).plain
    assert "blashen" in plain and "closest" in plain
    assert "closest" not in picker_head("Search: ", "blashen", 3, 0, "").plain


def _comp_hits() -> list[SourceTrack]:
    return [
        _src(1, "Beacon", "Hawthorn", "Tanglewood", 101),
        _src(2, "Harbor", "Alder", "Tanglewood", 101),
        _src(3, "Nightshade", "Slate", "Nightshade", 102),
    ]


def test_find_compilations_marks_multi_artist_albums() -> None:
    from tidal2ytm import planning as planning_mod

    assert planning_mod.find_compilations(_comp_hits()) == {101}


def test_build_rows_groups_compilations_under_various_artists() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_comp_hits(), "both", {101})
    artists = [r.artist for r in rows if r.kind == "artist"]
    assert artists == ["Slate", "Various Artists"]
    va = next(r for r in rows if r.kind == "artist" and r.artist == "Various Artists")
    assert (
        planning_mod.row_text(va, {}, False, "both").plain == "  ☐ Various Artists  (0/2 selected)"
    )


def test_various_artists_tracks_show_artist() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_comp_hits(), "both", {101})
    track = next(r for r in rows if r.kind == "track" and r.artist == "Hawthorn")
    assert "Hawthorn - Beacon" in planning_mod.row_text(track, {}, False, "both").plain


def test_map_windows_key() -> None:
    from tidal2ytm import planning as planning_mod

    rk = planning_mod.readchar_key
    assert planning_mod.map_windows_key(38) == rk.UP
    assert planning_mod.map_windows_key(40) == rk.DOWN
    assert planning_mod.map_windows_key(33) == rk.PAGE_UP
    assert planning_mod.map_windows_key(34) == rk.PAGE_DOWN
    assert planning_mod.map_windows_key(112) is None


def test_classify_windows_event() -> None:
    from tidal2ytm import planning as planning_mod

    assert planning_mod.classify_windows_event(4, False, 0, "") == planning_mod.RESIZE_KEY
    assert planning_mod.classify_windows_event(2, False, 0, "") is None
    assert planning_mod.classify_windows_event(8, False, 0, "") is None
    assert planning_mod.classify_windows_event(1, False, 38, "") is None
    assert planning_mod.classify_windows_event(1, True, 38, "\x00") == planning_mod.readchar_key.UP
    assert planning_mod.classify_windows_event(1, True, 65, "a") == "a"


def test_parse_sgr_mouse_wheel() -> None:
    from tidal2ytm import keys as keys_mod

    assert keys_mod.parse_sgr_mouse("\x1b[<64;10;5M") == keys_mod.WHEEL_UP
    assert keys_mod.parse_sgr_mouse("\x1b[<65;10;5M") == keys_mod.WHEEL_DOWN
    assert keys_mod.parse_sgr_mouse("\x1b[<0;10;5M") is None
    assert keys_mod.parse_sgr_mouse("\x1b[Z") is None


def test_classify_windows_event_wheel() -> None:
    from tidal2ytm import keys as keys_mod

    up = keys_mod.classify_windows_event(
        2, False, 0, "", button_state=0x00780000, event_flags=0x0004
    )
    assert up == keys_mod.WHEEL_UP
    down = keys_mod.classify_windows_event(
        2, False, 0, "", button_state=0xFF880000, event_flags=0x0004
    )
    assert down == keys_mod.WHEEL_DOWN
    assert keys_mod.classify_windows_event(2, False, 0, "", button_state=0, event_flags=0) is None


def test_scrollbar_thumb_hidden_without_overflow() -> None:
    from tidal2ytm.picker_rows import scrollbar_thumb

    assert scrollbar_thumb(5, 10, 0) is None
    assert scrollbar_thumb(10, 10, 0) is None


def test_scrollbar_thumb_tracks_window() -> None:
    from tidal2ytm.picker_rows import scrollbar_thumb

    assert scrollbar_thumb(100, 10, 0) == 0
    assert scrollbar_thumb(100, 10, 90) == 9
    thumb = scrollbar_thumb(100, 10, 45)
    assert thumb is not None and 0 < thumb < 9


def test_picker_frame_shows_scrollbar_on_overflow() -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod
    from tidal2ytm.picker_rows import PickerView

    hits = [_src(i, f"Song{i:02d}") for i in range(1, 31)]
    rows = planning_mod.build_rows(hits, "none")
    view = PickerView(
        title="Search: ",
        title_term="x",
        rows=rows,
        cursor=0,
        selection={},
        grouping="none",
        hits_total=30,
        height=10,
        clearable=False,
        notice="",
    )
    console = Console(width=60, record=True)
    console.print(planning_mod.picker_frame(view))
    assert "█" in console.export_text()


def test_picker_frame_hides_scrollbar_without_overflow() -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod
    from tidal2ytm.picker_rows import PickerView

    rows = planning_mod.build_rows(_picker_hits(), "none")
    view = PickerView(
        title="Search: ",
        title_term="x",
        rows=rows,
        cursor=0,
        selection={},
        grouping="none",
        hits_total=3,
        height=10,
        clearable=False,
        notice="",
    )
    console = Console(width=60, record=True)
    console.print(planning_mod.picker_frame(view))
    assert "█" not in console.export_text()


def test_picker_frame_scrollbar_survives_long_titles() -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod
    from tidal2ytm.picker_rows import PickerView

    long_title = "A Very Long Track Title That Definitely Overflows The Panel Width For Sure"
    hits = [_src(i, f"{long_title} - {i:02d}") for i in range(1, 31)]
    rows = planning_mod.build_rows(hits, "none")
    view = PickerView(
        title="Search: ",
        title_term="long",
        rows=rows,
        cursor=0,
        selection={},
        grouping="none",
        hits_total=30,
        height=10,
        clearable=False,
        notice="",
    )
    console = Console(width=60, record=True)
    console.print(planning_mod.picker_frame(view))
    assert "█" in console.export_text()


def test_picker_frame_renders_committed_checks() -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    hits = _picker_hits()
    rows = planning_mod.build_rows(hits, "none")
    view = planning_mod.PickerView(
        title="Search: ",
        title_term="nightshade",
        rows=rows,
        cursor=0,
        selection={1: hits[0]},
        grouping="none",
        hits_total=3,
        height=10,
        clearable=False,
        notice="",
        committed=frozenset({1}),
    )
    console = Console(width=60, record=True)
    console.print(planning_mod.picker_frame(view))
    assert "✓" in console.export_text()


def test_picker_bar_advertises_quit() -> None:
    from tidal2ytm import planning as planning_mod

    assert "quit" in planning_mod.picker_bar("both", False).plain


def test_do_search_loops_on_new_search(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    monkeypatch.setattr(sys, "stdin", _TtyStub())
    monkeypatch.setattr("builtins.input", _InputStub(["nightshade", "nightshade"]))
    calls: list[str] = []

    def _fake_picker(
        console: Console, session: PlanningSession, hits: list[SourceTrack], **kwargs: Any
    ) -> str | None:
        calls.append(kwargs.get("title_term", ""))
        return "search" if len(calls) == 1 else None

    monkeypatch.setattr(planning_mod, "run_picker", _fake_picker)
    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml",
        liked=[_src(1, "Nightshade", "Slate", "Nightshade", 101)],
        library_loaded=True,
    )
    planning_mod.COMMANDS["s"](Console(), session)
    assert calls == ["nightshade", "nightshade"]


def test_do_search_confirmed_selection_flashes_and_searches_again(
    monkeypatch: Any, tmp_path: Path
) -> None:
    import io

    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    monkeypatch.setattr(sys, "stdin", _TtyStub())
    monkeypatch.setattr("builtins.input", _InputStub(["nightshade", ""]))
    monkeypatch.setattr(planning_mod, "_COMMIT_FLASH_SEC", 0)
    calls: list[str] = []

    def _fake_picker(
        console: Console, session: PlanningSession, hits: list[SourceTrack], **kwargs: Any
    ) -> str | None:
        calls.append(kwargs.get("title_term", ""))
        session.selection[hits[0].tidal_id] = hits[0]
        return None

    monkeypatch.setattr(planning_mod, "run_picker", _fake_picker)
    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml",
        liked=[_src(1, "Nightshade", "Slate", "Nightshade", 101)],
        library_loaded=True,
    )
    buf = io.StringIO()
    planning_mod.COMMANDS["s"](Console(file=buf, width=60, force_terminal=True), session)
    assert calls == ["nightshade"]
    assert "✓" in buf.getvalue()
    assert set(session.selection) == {1}


def test_do_search_second_round_keeps_staging(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    monkeypatch.setattr(sys, "stdin", _TtyStub())
    monkeypatch.setattr("builtins.input", _InputStub(["nightshade", "nightshade", ""]))
    monkeypatch.setattr(planning_mod, "_COMMIT_FLASH_SEC", 0)
    calls: list[str] = []

    def _fake_picker(
        console: Console, session: PlanningSession, hits: list[SourceTrack], **kwargs: Any
    ) -> str | None:
        calls.append(kwargs.get("title_term", ""))
        if len(calls) == 1:
            session.selection[hits[0].tidal_id] = hits[0]
        return None

    monkeypatch.setattr(planning_mod, "run_picker", _fake_picker)
    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml",
        liked=[_src(1, "Nightshade", "Slate", "Nightshade", 101)],
        library_loaded=True,
    )
    planning_mod.COMMANDS["s"](Console(), session)
    assert calls == ["nightshade", "nightshade"]
    assert set(session.selection) == {1}


def test_do_search_no_hits_prompts_again(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    monkeypatch.setattr(sys, "stdin", _TtyStub())
    monkeypatch.setattr("builtins.input", _InputStub(["zzz-no-match", "nightshade", ""]))
    calls: list[str] = []

    def _fake_picker(
        console: Console, session: PlanningSession, hits: list[SourceTrack], **kwargs: Any
    ) -> str | None:
        calls.append(kwargs.get("title_term", ""))
        return None

    monkeypatch.setattr(planning_mod, "run_picker", _fake_picker)
    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml",
        liked=[_src(1, "Nightshade", "Slate", "Nightshade", 101)],
        library_loaded=True,
    )
    planning_mod.COMMANDS["s"](Console(), session)
    assert calls == ["nightshade"]


def test_search_fallback_star_selects_all(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    class _NoTtyStub:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sys, "stdin", _NoTtyStub())
    monkeypatch.setattr("builtins.input", _InputStub(["nightshade", "*", ""]))
    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml",
        liked=[
            _src(101, "Nightshade", "Slate", "Nightshade", 201),
            _src(102, "Nightshade", "Slate", "Nightshade", 202),
        ],
        library_loaded=True,
    )
    planning_mod.COMMANDS["s"](Console(), session)
    assert set(session.selection) == {101, 102}


def test_do_review_delegates_new_search(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    monkeypatch.setattr(sys, "stdin", _TtyStub())

    def _fake_picker(*args: Any, **kwargs: Any) -> str:
        return "search"

    monkeypatch.setattr(planning_mod, "run_picker", _fake_picker)
    searched: list[str] = []

    def _fake_search(console: Console, session: PlanningSession) -> None:
        searched.append("search")

    monkeypatch.setattr(planning_mod, "_do_search", _fake_search)
    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[], selection={1: _src(1)}
    )
    planning_mod.COMMANDS["v"](Console(), session)
    assert searched == ["search"]


class _TtyStub:
    def isatty(self) -> bool:
        return True


class _WritesStub:
    def __init__(self, writes: list[str]) -> None:
        self.writes = writes

    def write(self, text: str) -> int:
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def test_sgr_mouse_writes_enable_disable(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    writes: list[str] = []
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "stdin", _TtyStub())
    monkeypatch.setattr(sys, "stdout", _WritesStub(writes))
    monkeypatch.setattr(planning_mod, "_picker_readkey", lambda: planning_mod.readchar_key.ENTER)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert "\x1b[?1000h\x1b[?1006h" in writes
    assert "\x1b[?1000l" in writes


def test_sgr_mouse_noop_without_tty(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    class _NoTtyStub:
        def isatty(self) -> bool:
            return False

    writes: list[str] = []
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "stdin", _NoTtyStub())
    monkeypatch.setattr(sys, "stdout", _WritesStub(writes))
    monkeypatch.setattr(planning_mod, "_picker_readkey", lambda: planning_mod.readchar_key.ENTER)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert "\x1b[?1000h\x1b[?1006h" not in writes
    assert "\x1b[?1000l" not in writes


class _InputStub:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers

    def __call__(self, prompt: str = "") -> str:
        return self.answers.pop(0)


@pytest.mark.skipif(os.name != "nt", reason="Windows console API")
def test_windows_reader_prototypes_set() -> None:
    from tidal2ytm import planning as planning_mod

    kernel32 = planning_mod.kernel32()
    assert kernel32.GetStdHandle.argtypes is not None
    assert kernel32.GetStdHandle.restype is not None
    assert kernel32.WaitForSingleObject.argtypes is not None
    assert kernel32.ReadConsoleInputW.argtypes is not None


def test_match_action_prints_informative_progress(tmp_path: Path, capsys: Any) -> None:
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, TrackStatus

    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[_src()], selection={1: _src()}
    )
    result = MatchResult(
        source=_src(),
        yt_video_id="AAAAAAAAAAA",
        yt_title="Apple",
        yt_artist="Wren",
        yt_album="Apple",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.9, summary="title=0.95, artist=1.00"),
        status=TrackStatus.PENDING,
    )
    with patch.object(planning_mod, "match_track", return_value=result):
        run_match_action(session, MagicMock(), input_fn=lambda _p: "Y")
    out = capsys.readouterr().out
    assert "[1/1]" in out
    assert "Apple" in out and "Wren" in out
    assert "AAAAAAAAAAA" in out
    assert "fuzzy" in out.lower()
    assert "0.90" in out


def test_read_plan_counts_missing_file_returns_zeros(tmp_path: Path) -> None:
    from tidal2ytm.planning import PlanCounts, read_plan_counts

    assert read_plan_counts(tmp_path / "nope.toml") == PlanCounts(
        total=0, pending=0, needs_review=0, transferred=0, skip=0, failed=0
    )


def test_read_plan_counts_corrupt_file_returns_unreadable(tmp_path: Path) -> None:
    from tidal2ytm.planning import PlanCounts, read_plan_counts

    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("not [valid toml", encoding="utf-8")
    assert read_plan_counts(plan_path) == PlanCounts(unreadable=True)


def test_read_plan_counts_reads_meta(tmp_path: Path) -> None:
    from tidal2ytm import plan_io
    from tidal2ytm.planning import read_plan_counts

    plan_path = tmp_path / "transfer_plan.toml"
    plan: dict[str, Any] = {
        "meta": {},
        "artists": [
            {
                "name": "Wren",
                "match_id": "wren",
                "albums": [
                    {
                        "name": "Apple",
                        "match_id": "wren/apple",
                        "tracks": [
                            {"tidal_id": 1, "title": "A", "status": "pending"},
                            {"tidal_id": 2, "title": "B", "status": "needs_review"},
                            {"tidal_id": 3, "title": "C", "status": "transferred"},
                        ],
                    }
                ],
            }
        ],
    }
    plan_io.update_plan_meta(plan)
    plan_io.save_plan(plan, plan_path)
    counts = read_plan_counts(plan_path)
    assert (counts.total, counts.pending, counts.needs_review, counts.transferred) == (3, 1, 1, 1)
    assert (counts.skip, counts.failed) == (0, 0)


def test_read_auth_presence_reports_validity(tmp_path: Path) -> None:
    import datetime
    import json
    import time

    from tidal2ytm.planning import AuthPresence, read_auth_presence

    assert read_auth_presence(tmp_path) == AuthPresence(
        ytm_ok=False, client_secret=False, tidal_ok=False
    )
    future = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(days=4)
    ).isoformat()
    (tmp_path / "ytm_auth.json").write_text(
        json.dumps({"access_token": "tok", "expires_at": int(time.time()) + 3600}),
        encoding="utf-8",
    )
    (tmp_path / "client_secret_test.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tidal_token.json").write_text(
        json.dumps({"expiry_time": future}), encoding="utf-8"
    )
    assert read_auth_presence(tmp_path) == AuthPresence(
        ytm_ok=True, client_secret=True, tidal_ok=True
    )
    past = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=1)
    ).isoformat()
    (tmp_path / "tidal_token.json").write_text(json.dumps({"expiry_time": past}), encoding="utf-8")
    (tmp_path / "ytm_auth.json").write_text("not valid json", encoding="utf-8")
    assert read_auth_presence(tmp_path) == AuthPresence(
        ytm_ok=False, client_secret=True, tidal_ok=False
    )


def test_menu_body_shows_plan_counts_and_auth() -> None:
    from tidal2ytm.planning import AuthPresence, PlanCounts, status_body

    session = PlanningSession(plan_path=Path("x.toml"), liked=[_src(1)], selection={})
    plain = status_body(
        session,
        PlanCounts(total=3, pending=1, needs_review=1, transferred=1, skip=0, failed=0),
        AuthPresence(ytm_ok=True, client_secret=True, tidal_ok=False),
        True,
    ).plain
    assert "pending 1" in plain and "needs_review 1" in plain and "transferred 1" in plain
    assert "YTM" in plain and "Tidal" in plain
    assert "unreadable" not in plain


def test_status_body_shows_unreadable_marker() -> None:
    from tidal2ytm.planning import PlanCounts, status_body

    session = PlanningSession(plan_path=Path("x.toml"), liked=[_src(1)], selection={})
    plain = status_body(session, PlanCounts(unreadable=True), None, True).plain
    assert "unreadable — showing zeros" in plain


def test_gateway_review_opens_full_tui(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    seen: dict[str, Any] = {}

    def _fake_run_review(**kwargs: Path) -> None:
        seen.update(kwargs)

    monkeypatch.setattr("tidal2ytm.review.run_review", _fake_run_review)
    from tidal2ytm import plan_io
    from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, TrackStatus
    from tidal2ytm.planning_merge import insert_track, match_result_to_track_dict

    plan_path = tmp_path / "transfer_plan.toml"
    old = MatchResult(
        source=_src(),
        yt_video_id="AAAAAAAAAAA",
        yt_title="Apple",
        yt_artist="Wren",
        yt_album="Apple",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.8),
        status=TrackStatus.PENDING,
    )
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    insert_track(plan, match_result_to_track_dict(old), 1971)
    plan_io.update_plan_meta(plan)
    plan_io.save_plan(plan, plan_path)
    planning_mod.COMMANDS["r"](
        Console(), PlanningSession(plan_path=plan_path, liked=[], selection={})
    )
    assert seen == {"plan_path": plan_path}


def test_gateway_review_missing_plan_pauses(capsys: Any, tmp_path: Path, monkeypatch: Any) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    monkeypatch.setattr("builtins.input", _paused_enter)
    planning_mod.COMMANDS["r"](
        Console(),
        PlanningSession(plan_path=tmp_path / "missing.toml", liked=[], selection={}),
    )
    assert "No transfer plan" in capsys.readouterr().out


def test_gateway_review_empty_plan_pauses(tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    def _must_not_run(**kwargs: Any) -> None:
        raise AssertionError("must not run")

    monkeypatch.setattr("tidal2ytm.review.run_review", _must_not_run)
    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("[meta]\n", encoding="utf-8")
    prompts: list[str] = []

    def _answer(prompt: str = "") -> str:
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", _answer)
    planning_mod.COMMANDS["r"](
        Console(),
        PlanningSession(plan_path=plan_path, liked=[], selection={}),
    )
    assert "plan is empty" in capsys.readouterr().out
    assert any("Press Enter" in p for p in prompts)


def test_gateway_review_unreadable_plan_pauses(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    def _must_not_run(**kwargs: Any) -> None:
        raise AssertionError("must not run")

    monkeypatch.setattr("tidal2ytm.review.run_review", _must_not_run)
    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("not [valid toml", encoding="utf-8")
    prompts: list[str] = []

    def _answer(prompt: str = "") -> str:
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", _answer)
    planning_mod.COMMANDS["r"](
        Console(),
        PlanningSession(plan_path=plan_path, liked=[], selection={}),
    )
    assert "unreadable" in capsys.readouterr().out
    assert any("Press Enter" in p for p in prompts)


def test_gateway_transfer_runs_all_pending(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    calls: dict[str, Any] = {}

    class _FakeClient:
        def login(self) -> str:
            return "YT"

    monkeypatch.setattr("tidal2ytm.ytm_client.YTMClient", _FakeClient)

    def _fake_transfer(yt: Any, **kwargs: Any) -> None:
        calls["yt"] = yt
        calls.update(kwargs)

    monkeypatch.setattr("tidal2ytm.transfer.run_transfer", _fake_transfer)
    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("[meta]\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", _paused_enter)
    planning_mod.COMMANDS["t"](
        Console(),
        PlanningSession(plan_path=plan_path, liked=[], selection={}),
    )
    assert calls["yt"] == "YT"
    assert calls["all_tracks"] is True and calls["dry_run"] is False
    assert calls["include_needs_review"] is False and calls["plan_path"] == plan_path


def test_gateway_transfer_decline_does_nothing(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    class _NoClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("no login")

    def _no_transfer(_yt: Any, **kwargs: Any) -> None:
        raise AssertionError("no transfer")

    monkeypatch.setattr("tidal2ytm.ytm_client.YTMClient", _NoClient)
    monkeypatch.setattr("tidal2ytm.transfer.run_transfer", _no_transfer)
    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("[meta]\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _p="": "n")
    planning_mod.COMMANDS["d"](
        Console(),
        PlanningSession(plan_path=plan_path, liked=[], selection={}),
    )


@pytest.mark.parametrize(
    ("scope", "expected", "library_loaded", "notice"),
    [
        ("", ["ytm", "tidal"], True, None),
        ("tidal", ["tidal"], True, None),
        ("ytm", ["ytm"], False, None),
        ("bogus", [], False, "Unknown scope"),
    ],
)
def test_gateway_auth_scope_routing(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
    scope: str,
    expected: list[str],
    library_loaded: bool,
    notice: str | None,
) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    done: list[str] = []

    def _fake_ytm_auth(
        *, client_id: str | None = None, client_secret: str | None = None, force: bool = False
    ) -> Path:
        done.append("ytm")
        return tmp_path / "ytm_auth.json"

    def _fake_tidal_auth(*, force: bool = False) -> Path:
        done.append("tidal")
        return tmp_path / "tidal_token.json"

    def _fake_login() -> object:
        return object()

    def _fake_liked(_session: Any) -> list[Any]:
        return [_src(1)]

    monkeypatch.setattr("tidal2ytm.auth.run_ytm_auth", _fake_ytm_auth)
    monkeypatch.setattr("tidal2ytm.auth.run_tidal_auth", _fake_tidal_auth)
    monkeypatch.setattr("tidal2ytm.cli.tidal_login", _fake_login)
    monkeypatch.setattr("tidal2ytm.tidal_source.get_liked_tracks", _fake_liked)
    answers = iter([scope, ""])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    session = PlanningSession(plan_path=tmp_path / "x.toml", liked=[], selection={})
    planning_mod.COMMANDS["a"](Console(), session)
    assert done == expected
    assert session.library_loaded is library_loaded
    out = capsys.readouterr().out
    if notice:
        assert notice in out
    elif library_loaded:
        assert "Found 1 tracks." in out


def test_gateway_auth_ytm_failure_still_runs_tidal(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    def _boom_ytm(
        *, client_id: str | None = None, client_secret: str | None = None, force: bool = False
    ) -> Path:
        raise RuntimeError("ytm down")

    done: list[str] = []

    def _fake_tidal_auth(*, force: bool = False) -> Path:
        done.append("tidal")
        return tmp_path / "tidal_token.json"

    def _fake_login() -> object:
        return object()

    def _fake_liked(_session: Any) -> list[Any]:
        return [_src(1)]

    monkeypatch.setattr("tidal2ytm.auth.run_ytm_auth", _boom_ytm)
    monkeypatch.setattr("tidal2ytm.auth.run_tidal_auth", _fake_tidal_auth)
    monkeypatch.setattr("tidal2ytm.cli.tidal_login", _fake_login)
    monkeypatch.setattr("tidal2ytm.tidal_source.get_liked_tracks", _fake_liked)
    answers = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    session = PlanningSession(plan_path=tmp_path / "x.toml", liked=[], selection={})
    planning_mod.COMMANDS["a"](Console(), session)
    assert done == ["tidal"]
    assert session.library_loaded is True
    out = capsys.readouterr().out
    assert "YTM auth failed" in out
    assert "Tidal auth ok." in out
    assert "Found 1 tracks." in out


def test_gateway_auth_shows_wait_spinner(monkeypatch: Any, tmp_path: Path) -> None:
    import contextlib
    from collections.abc import Generator

    from rich.console import Console

    import tidal2ytm.cli as cli_mod
    from tidal2ytm import planning as planning_mod

    seen: list[str] = []

    @contextlib.contextmanager
    def _fake_wait_status(thing: str) -> Generator[None, None, None]:
        seen.append(thing)
        yield

    monkeypatch.setattr(cli_mod, "wait_status", _fake_wait_status)

    def _fake_ytm_auth(*, force: bool = False) -> None:
        return None

    monkeypatch.setattr("tidal2ytm.auth.run_ytm_auth", _fake_ytm_auth)
    monkeypatch.setattr("builtins.input", _InputStub(["ytm", ""]))
    session = PlanningSession(
        plan_path=tmp_path / "x.toml", liked=[], selection={}, library_loaded=True
    )
    planning_mod.COMMANDS["a"](Console(), session)
    assert seen == ["Authenticating with YTM"]


@pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
def test_gateway_transfer_abort_is_not_a_failure(
    tmp_path: Path, capsys: Any, monkeypatch: Any, exc: type[BaseException]
) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    def _abort(_prompt: str = "") -> str:
        raise exc

    plan_path = tmp_path / "transfer_plan.toml"
    plan_path.write_text("[meta]\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", _abort)
    planning_mod.COMMANDS["d"](
        Console(),
        PlanningSession(plan_path=plan_path, liked=[], selection={}),
    )
    assert "Transfer failed" not in capsys.readouterr().out


def _gateway_session(selected: int = 0) -> PlanningSession:
    liked = [_src(1), _src(2)]
    sel = {t.tidal_id: t for t in liked[:selected]}
    return PlanningSession(
        plan_path=Path("x.toml"), liked=liked, selection=sel, library_loaded=True
    )


def test_menu_body_lists_gateway_modes() -> None:
    from tidal2ytm.planning import menu_body

    plain = menu_body(_gateway_session()).plain
    assert "review every match in the plan" in plain
    assert "transfer pending tracks" in plain
    assert "dry-run the transfer" in plain
    assert "authenticate with Tidal and YTM" in plain
    assert "quit tidal2ytm" in plain


def test_menu_marks_plan_dependent_rows_without_plan() -> None:
    from tidal2ytm.planning import menu_body

    assert "(needs plan)" in menu_body(_gateway_session(), has_plan=False).plain
    assert "(needs plan)" not in menu_body(_gateway_session(), has_plan=True).plain


def test_status_body_shows_library_plan_and_auth() -> None:
    from tidal2ytm.planning import AuthPresence, PlanCounts, status_body

    plain = status_body(
        _gateway_session(selected=1),
        PlanCounts(total=3, pending=1, needs_review=1, transferred=1, skip=0, failed=0),
        AuthPresence(ytm_ok=True, client_secret=True, tidal_ok=False),
        has_plan=True,
    ).plain
    assert "2 in library" in plain and "1 selected" in plain
    assert "pending 1" in plain and "transferred 1" in plain
    assert "✓" in plain and "✕" in plain


def test_status_body_hides_counts_when_library_not_loaded() -> None:
    from tidal2ytm.planning import status_body

    body = status_body(
        PlanningSession(plan_path=Path("x.toml"), liked=[], selection={}), None, None, True
    )
    assert "library not loaded — authenticate" in body.plain
    assert "0 in library" not in body.plain
    spans = {body.plain[s.start : s.end]: str(s.style) for s in body.spans}
    assert spans.get("a") == "bold bright_red"
    assert "dim" not in " ".join(str(s.style) for s in body.spans)


def test_status_body_without_plan() -> None:
    from tidal2ytm.planning import status_body

    assert "no plan yet" in status_body(_gateway_session(), has_plan=False).plain


def test_menu_groups_share_lines_with_blank_separators() -> None:
    from tidal2ytm.planning import menu_body

    lines = menu_body(_gateway_session(), has_plan=True).plain.splitlines()
    assert any("transfer" in line and "dry-run" in line for line in lines)
    assert any("match" in line and "ctrl+o" in line for line in lines)
    sep = next(i for i, line in enumerate(lines) if "─" in line)
    assert lines[sep - 1] == ""


def test_render_menu_includes_logo_tagline(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    """The logo wordmark must flow through the rendered main menu.

    Guards the wiring regression where _render_menu printed a plain
    'tidal2ytm' string instead of Panel(logo_text(), ...): only logo_text
    carries the 'Transfer Tidal tracks' tagline, so a reverted wiring drops it.
    """

    def _no_fresh_token(*, login: bool = True) -> None:
        assert login is False
        return None

    def _eof(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("tidal2ytm.cli.tidal_login", _no_fresh_token)
    monkeypatch.setattr("builtins.input", _eof)
    from tidal2ytm import planning as planning_mod

    planning_mod.run_planning(plan_path=tmp_path / "x.toml")
    out = capsys.readouterr().out
    assert "tidal2ytm" in out
    assert "Transfer Tidal tracks to YouTube Music" in out
    assert "liked" not in out


def test_render_menu_titles_main_menu(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    from tidal2ytm import planning as planning_mod

    def _no_fresh_token(*, login: bool = True) -> None:
        assert login is False
        return None

    def _eof(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("tidal2ytm.cli.tidal_login", _no_fresh_token)
    monkeypatch.setattr("builtins.input", _eof)
    planning_mod.run_planning(plan_path=tmp_path / "x.toml")
    out = capsys.readouterr().out
    assert "Main menu" in out
    assert "Status" in out


def test_run_planning_uses_alt_screen_and_ends_after(tmp_path: Path, monkeypatch: Any) -> None:
    from tidal2ytm import planning as planning_mod

    events: list[str] = []
    screens: list[str] = []

    class _FakeConsole:
        def clear(self) -> None:
            events.append("clear")

        def print(self, *args: Any, **kwargs: Any) -> None:
            events.append("print:" + str(args[0]))

        def screen(self) -> Any:
            class _Ctx:
                def __enter__(self) -> None:
                    screens.append("enter")

                def __exit__(self, *exc: Any) -> bool:
                    screens.append("exit")
                    return False

            return _Ctx()

    seen: list[str] = []

    def _fake_startup(console: Any, session: Any) -> None:
        seen.append("startup")

    def _fake_tui(console: Any, session: Any) -> None:
        seen.append("tui")

    monkeypatch.setattr(planning_mod, "Console", _FakeConsole)
    monkeypatch.setattr(planning_mod, "_try_startup_library_load", _fake_startup)
    monkeypatch.setattr(planning_mod, "_tui_loop", _fake_tui)
    planning_mod.run_planning(plan_path=tmp_path / "x.toml")
    assert seen == ["startup", "tui"]
    assert screens == ["enter", "exit"]
    assert events[-1] == "print:\nPlanning session ended."


def test_review_title_marks_review_mode() -> None:
    from tidal2ytm.review import review_title

    ctx: dict[int, dict[str, Any]] = {
        1: {"album_match_id": "wren/apple", "pos_in_album": 1, "total_in_album": 2}
    }
    title = review_title({"tidal_id": 1}, ctx)
    assert title.plain.startswith("Review")
    assert "wren/apple" in title.plain


def _paused_enter(_prompt: str = "") -> str:
    return ""


def test_do_search_requires_library(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    def _no_query() -> None:
        raise AssertionError("search must not prompt without the library")

    monkeypatch.setattr(planning_mod, "_prompt_query", _no_query)
    monkeypatch.setattr("builtins.input", _paused_enter)
    session = PlanningSession(plan_path=tmp_path / "x.toml", liked=[], selection={})
    planning_mod.COMMANDS["s"](Console(), session)
    assert "Authenticate first (a)" in capsys.readouterr().out


def test_select_all_requires_library(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    monkeypatch.setattr("builtins.input", _paused_enter)
    session = PlanningSession(plan_path=tmp_path / "x.toml", liked=[], selection={})
    planning_mod.COMMANDS["e"](Console(), session)
    assert session.selection == {}
    assert "Authenticate first (a)" in capsys.readouterr().out


def test_startup_load_fetches_library_when_token_fresh(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    from tidal2ytm import planning as planning_mod

    def _fake_login(*, login: bool = True) -> object:
        assert login is False
        return object()

    def _fake_liked(_session: Any) -> list[Any]:
        return [_src(1), _src(2)]

    monkeypatch.setattr("tidal2ytm.cli.tidal_login", _fake_login)
    monkeypatch.setattr("tidal2ytm.tidal_source.get_liked_tracks", _fake_liked)
    captured: dict[str, PlanningSession] = {}

    def _fake_loop(_console: Any, session: PlanningSession) -> None:
        captured["session"] = session

    monkeypatch.setattr(planning_mod, "_tui_loop", _fake_loop)
    planning_mod.run_planning(plan_path=tmp_path / "x.toml")
    session = captured["session"]
    assert session.library_loaded is True
    assert len(session.liked) == 2
    assert "Fetching Tidal tracks" in capsys.readouterr().out


def test_startup_load_stays_silent_without_fresh_token(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    from tidal2ytm import planning as planning_mod

    def _fake_login(*, login: bool = True) -> None:
        assert login is False
        return None

    def _no_fetch(_session: Any) -> list[Any]:
        raise AssertionError("must not fetch without a session")

    monkeypatch.setattr("tidal2ytm.cli.tidal_login", _fake_login)
    monkeypatch.setattr("tidal2ytm.tidal_source.get_liked_tracks", _no_fetch)
    captured: dict[str, PlanningSession] = {}

    def _fake_loop(_console: Any, session: PlanningSession) -> None:
        captured["session"] = session

    monkeypatch.setattr(planning_mod, "_tui_loop", _fake_loop)
    planning_mod.run_planning(plan_path=tmp_path / "x.toml")
    session = captured["session"]
    assert session.library_loaded is False
    assert session.liked == []
    out = capsys.readouterr().out
    assert "Could not load Tidal library" not in out
    assert "Fetching Tidal tracks" not in out


def test_startup_load_reports_fetch_failure(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    from tidal2ytm import planning as planning_mod

    def _fake_login(*, login: bool = True) -> object:
        assert login is False
        return object()

    def _boom(_session: Any) -> list[Any]:
        raise OSError("offline")

    monkeypatch.setattr("tidal2ytm.cli.tidal_login", _fake_login)
    monkeypatch.setattr("tidal2ytm.tidal_source.get_liked_tracks", _boom)
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
    captured: dict[str, PlanningSession] = {}

    def _fake_loop(_console: Any, session: PlanningSession) -> None:
        captured["session"] = session

    monkeypatch.setattr(planning_mod, "_tui_loop", _fake_loop)
    planning_mod.run_planning(plan_path=tmp_path / "x.toml")
    session = captured["session"]
    assert session.library_loaded is False
    out = capsys.readouterr().out
    assert "Could not load Tidal library" in out and "uthenticate to retry" in out


def test_run_planning_starts_with_empty_library(tmp_path: Path, monkeypatch: Any) -> None:
    from tidal2ytm import planning as planning_mod

    seen: dict[str, Any] = {}

    def _fake_loop(_console: Any, _session: PlanningSession) -> None:
        seen["session"] = _session

    def _no_fresh_token(*, login: bool = True) -> None:
        assert login is False
        return None

    monkeypatch.setattr(planning_mod, "_tui_loop", _fake_loop)
    monkeypatch.setattr("tidal2ytm.cli.tidal_login", _no_fresh_token)
    planning_mod.run_planning(plan_path=tmp_path / "x.toml")
    session = seen["session"]
    assert session.liked == []
    assert session.library_loaded is False


@pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
def test_match_action_abort_at_confirm_returns_counts(
    tmp_path: Path, exc: type[BaseException]
) -> None:
    def _abort(_prompt: str = "") -> str:
        raise exc

    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[_src()], selection={1: _src()}
    )
    counts = run_match_action(session, MagicMock(), input_fn=_abort)
    assert counts == {"new": 0, "upgraded": 0, "kept": 0, "skipped": 0}
    assert not session.plan_path.exists()


@pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
def test_match_action_abort_at_overwrite_keeps_stored(
    tmp_path: Path, exc: type[BaseException]
) -> None:
    from tidal2ytm import plan_io
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, TrackStatus
    from tidal2ytm.planning_merge import insert_track, match_result_to_track_dict

    plan_path = tmp_path / "transfer_plan.toml"
    src = _src()
    old = MatchResult(
        source=src,
        yt_video_id="AAAAAAAAAAA",
        yt_title="Apple",
        yt_artist="Wren",
        yt_album="Apple",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.8),
        status=TrackStatus.PENDING,
    )
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    insert_track(plan, match_result_to_track_dict(old), 1971)
    plan_io.update_plan_meta(plan)
    plan_io.save_plan(plan, plan_path)

    new = MatchResult(
        source=src,
        yt_video_id="BBBBBBBBBBB",
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
    answers = iter(["Y"])

    def _ask(prompt: str = "") -> str:
        if "Overwrite" in prompt:
            raise exc
        return next(answers)

    session = PlanningSession(plan_path=plan_path, liked=[src], selection={1: src})
    with patch.object(planning_mod, "match_track", return_value=new):
        counts = run_match_action(session, MagicMock(), input_fn=_ask)
    assert counts["kept"] == 1
    assert (
        plan_io.load_plan(plan_path)["artists"][0]["albums"][0]["tracks"][0]["yt_video_id"]
        == "AAAAAAAAAAA"
    )
