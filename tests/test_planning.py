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
    from tidal2ytm.planning import MENU_HINTS, key_hints

    plain = key_hints(MENU_HINTS).plain
    for keys, rest, _style in MENU_HINTS:
        for k in keys:
            assert k in plain
        assert rest in plain
    assert " | " in plain


def test_hot_hint_is_colour_only_letter() -> None:
    from tidal2ytm.planning import hot_hint

    assert hot_hint("select all from ", "A", "rtist").plain == "select all from Artist"
    assert hot_hint("", "*", " to toggle all").plain == "* to toggle all"


def test_hotkey_styles_use_bright_palette() -> None:
    from tidal2ytm.planning import (
        MENU_HINTS,
        RESULTS_HINTS,
        REVIEW_HINTS,
        hot_hint,
    )

    for _key, _label, style in MENU_HINTS + RESULTS_HINTS + REVIEW_HINTS:
        assert style in ("bold bright_blue", "bold bright_yellow"), style
    assert any("bold bright_blue" in str(s.style) for s in hot_hint("x", "A", "y").spans)


def test_menu_body_shows_counts() -> None:
    from tidal2ytm.planning import menu_body

    session = PlanningSession(
        plan_path=Path("x.toml"), liked=[_src(1), _src(2)], selection={1: _src(1)}
    )
    plain = menu_body(session).plain
    assert "select all 2 liked tracks" in plain
    assert "review 1 selected across searches" in plain


def test_menu_body_shows_override_banner() -> None:
    from tidal2ytm.planning import PlanningSession, menu_body

    on = menu_body(PlanningSession(plan_path=Path("x.toml"), liked=[], selection={}, override=True))
    assert "OVERRIDE" in on.plain
    assert any("7f1d1d" in str(s.style) for s in on.spans)
    off = menu_body(PlanningSession(plan_path=Path("x.toml"), liked=[], selection={}))
    assert "OVERRIDE" not in off.plain


def test_track_row_marks_selection() -> None:
    from tidal2ytm.planning import track_row

    assert "■" in track_row(1, True, _src()).plain
    assert "■" not in track_row(1, False, _src()).plain
    assert "| Apple, 1971, 3:20" in track_row(1, False, _src()).plain


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


def test_menu_body_separator_and_hotkeys() -> None:
    from tidal2ytm.planning import menu_body

    plain = menu_body(PlanningSession(plan_path=Path("x.toml"), liked=[], selection={})).plain
    assert "─" in plain
    assert "? | h" in plain and "ctrl+o" in plain and "/ | s" in plain
    assert "ctrl+override" in plain


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


def test_logo_text_is_plain_wordmark() -> None:
    from tidal2ytm.planning import logo_text

    plain = logo_text().plain
    assert "tidal2ytm" in plain
    assert "\\" not in plain


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
    from tidal2ytm import planning as planning_mod

    t = _src(
        7, "Wanderer", "Slate", "Seven (Deluxe Version)", 104, year=1971, track=3, duration=300
    )
    row = planning_mod.ListRow("track", artist=t.artist, album=t.album, track=t, indent=4)
    plain = planning_mod.row_text(row, {}, True, "both").plain
    assert plain == "    ❯ ☐ 03. Wanderer | 1971, 5:00"  # noqa: RUF001


def test_row_text_omits_missing_year() -> None:
    from tidal2ytm import planning as planning_mod

    t = _src(4, "B-Side", "Wren", "Rarities", 103, year=None)
    row = planning_mod.ListRow("track", artist=t.artist, album=t.album, track=t, indent=2)
    plain = planning_mod.row_text(row, {}, False, "artist").plain
    assert "?" not in plain
    assert plain.endswith("B-Side | Rarities, 3:20")


def test_row_text_no_number_outside_album_grouping() -> None:
    from tidal2ytm import planning as planning_mod

    t = _src(
        7, "Wanderer", "Slate", "Seven (Deluxe Version)", 104, year=1971, track=3, duration=300
    )
    row = planning_mod.ListRow("track", artist=t.artist, album=t.album, track=t, indent=2)
    plain = planning_mod.row_text(row, {}, False, "artist").plain
    assert "03." not in plain
    assert plain == "    ☐ Wanderer | Seven (Deluxe Version), 1971, 5:00"


def test_row_text_header_tristate() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_album_hits(), "artist")
    header = rows[0]
    assert "0/2" in planning_mod.row_text(header, {}, False, "artist").plain
    assert "☐" in planning_mod.row_text(header, {}, False, "artist").plain
    assert "▣" in planning_mod.row_text(header, {1: _src(1)}, False, "artist").plain
    sel = {1: _src(1), 2: _src(2)}
    assert "■" in planning_mod.row_text(header, sel, False, "artist").plain


def test_row_text_header_styles() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_album_hits(), "both")
    artist_row = next(r for r in rows if r.kind == "artist")
    album_row = next(r for r in rows if r.kind == "album")
    disc_row = next(r for r in rows if r.kind == "disc")
    assert any(
        "bold" in str(s.style) for s in planning_mod.row_text(artist_row, {}, False, "both").spans
    )
    assert any(
        "underline" in str(s.style)
        for s in planning_mod.row_text(album_row, {}, False, "both").spans
    )
    assert any(
        "italic" in str(s.style) for s in planning_mod.row_text(disc_row, {}, False, "both").spans
    )


def test_row_text_dedups_grouped_labels() -> None:
    from tidal2ytm import planning as planning_mod

    rows = planning_mod.build_rows(_album_hits(), "both")
    album_row = next(r for r in rows if r.kind == "album")
    assert (
        planning_mod.row_text(album_row, {}, False, "both").plain
        == "    ☐ Nightshade  (0/2 selected)"
    )
    t = _src(2, "Nightshade", "Slate", "Nightshade", 101, year=1970, track=1)
    flat = planning_mod.ListRow("track", artist=t.artist, album=t.album, track=t)
    assert "Slate - Nightshade" in planning_mod.row_text(flat, {}, False, "none").plain


def test_row_text_details_plain_green() -> None:
    from tidal2ytm import planning as planning_mod

    t = _src(
        7, "Wanderer", "Slate", "Seven (Deluxe Version)", 104, year=1971, track=3, duration=300
    )
    row = planning_mod.ListRow("track", artist=t.artist, album=t.album, track=t, indent=4)
    styles = [str(s.style) for s in planning_mod.row_text(row, {}, False, "both").spans]
    assert "green" in styles


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


def test_picker_screen_fits_narrow_viewport() -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    term_height, width = 30, 60
    narrow = Console(width=width)
    footer_h = planning_mod.footer_lines(narrow, "both", False)
    view_h = planning_mod.viewport_height(term_height, footer_h)
    rows = planning_mod.build_rows(_album_hits(), "both")
    screen = planning_mod.picker_screen("Search: ", "x", rows, 0, {}, "both", 5, view_h, False)
    assert len(narrow.render_lines(screen, narrow.options)) <= term_height - 1


def test_picker_bar_grouping_label_arrows_confirm() -> None:
    from tidal2ytm import planning as planning_mod

    bar = planning_mod.picker_bar("both", False)
    assert "grouping: artist + album" in bar.plain
    assert "↑" in bar.plain and "↓" in bar.plain
    assert "Enter confirm" in bar.plain and "Esc cancel" in bar.plain
    assert "new search" in bar.plain
    assert any("bright_yellow" in str(s.style) for s in bar.spans)


def test_picker_click_burst_drained(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.readchar_key.ESC, planning_mod.readchar_key.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    drained: list[str] = []
    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    monkeypatch.setattr(planning_mod, "_esc_has_tail", lambda: True)
    monkeypatch.setattr(planning_mod, "_drain_tail", lambda: drained.append("drain"))
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert drained == ["drain"]
    assert session.selection == {}


def test_picker_esc_without_tail_cancels(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.readchar_key.SPACE, planning_mod.readchar_key.ESC]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    asked: list[str] = []
    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    monkeypatch.setattr(planning_mod, "_esc_has_tail", lambda: False)

    def _ask(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(
        Console(), session, _picker_hits(), title="Search", height=24, input_fn=_ask
    )
    assert session.selection == {}
    assert len(asked) == 1


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


def test_resolve_search_uses_general_mode(monkeypatch: Any, tmp_path: Path) -> None:
    from tidal2ytm import planning as planning_mod

    seen: dict[str, str] = {}

    def _fake_search(
        liked: list[SourceTrack], query: str, mode: str
    ) -> tuple[list[SourceTrack], bool]:
        seen["mode"] = mode
        return [], True

    monkeypatch.setattr(planning_mod, "search_library", _fake_search)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[_src()])
    assert planning_mod.resolve_search(session, "willow") == ([], True)
    assert seen == {"mode": "general"}


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


def _dup_album_hits() -> list[SourceTrack]:
    hits = [_src(1, "One", "Band A", "Gold", 101, track=1)]
    hits.append(_src(2, "Two", "Band B", "Gold", 102, track=1))
    hits.extend(_src(i, f"Filler {i}", "ZZ Top", "Filler", 200, track=i) for i in range(3, 52))
    return hits


def test_bulk_key_on_header_toggles_only_that_group(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.readchar_key.DOWN, "A", planning_mod.readchar_key.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _dup_album_hits(), title="Search", height=24)
    assert set(session.selection) == {1}


def test_bulk_key_on_second_header_toggles_only_that_group(
    monkeypatch: Any, tmp_path: Path
) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    rk = planning_mod.readchar_key
    keys = [rk.DOWN, rk.DOWN, rk.DOWN, rk.DOWN, "L", rk.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _dup_album_hits(), title="Search", height=24)
    assert set(session.selection) == {2}


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


def test_picker_resize_repaints(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.RESIZE_KEY, planning_mod.readchar_key.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert session.selection == {}


def test_picker_q_quits_unchanged(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = ["q"]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    with pytest.raises(KeyboardInterrupt):
        planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert session.selection == {}


def test_picker_q_confirmed_restores_and_quits(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.readchar_key.SPACE, "q"]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    with pytest.raises(KeyboardInterrupt):
        planning_mod.run_picker(
            Console(), session, _picker_hits(), title="Search", height=24, input_fn=lambda _p: "y"
        )
    assert session.selection == {}


def test_picker_q_declined_stays(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [
        planning_mod.readchar_key.SPACE,
        "q",
        planning_mod.readchar_key.ENTER,
    ]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(
        Console(), session, _picker_hits(), title="Search", height=24, input_fn=lambda _p: "n"
    )
    assert set(session.selection) == {3}


def test_picker_bar_advertises_quit() -> None:
    from tidal2ytm import planning as planning_mod

    assert "quit" in planning_mod.picker_bar("both", False).plain


@pytest.mark.parametrize("slash_key", ["/", "s"])
def test_picker_slash_requests_new_search(monkeypatch: Any, tmp_path: Path, slash_key: str) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [slash_key]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    action = planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert action == "search"


def test_picker_slash_confirms_changes_first(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.readchar_key.SPACE, "/"]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    action = planning_mod.run_picker(
        Console(), session, _picker_hits(), title="Search", height=24, input_fn=lambda _p: "y"
    )
    assert action == "search"
    assert session.selection == {}


def test_picker_slash_declined_stays(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [
        planning_mod.readchar_key.SPACE,
        "/",
        planning_mod.readchar_key.ENTER,
    ]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    action = planning_mod.run_picker(
        Console(), session, _picker_hits(), title="Search", height=24, input_fn=lambda _p: "n"
    )
    assert action is None
    assert set(session.selection) == {3}


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
    )
    planning_mod._do_search(Console(), session)  # pyright: ignore[reportPrivateUsage]
    assert calls == ["nightshade", "nightshade"]


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
    planning_mod._do_review(Console(), session)  # pyright: ignore[reportPrivateUsage]
    assert searched == ["search"]


class _TtyStub:
    def isatty(self) -> bool:
        return True


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


def test_checkbox_glyph_colours() -> None:
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.planning import ListRow, row_text

    t = _src(7, "Wanderer", "Slate", "Seven (Deluxe Version)", 104, year=1971, track=3)
    row = ListRow("track", artist=t.artist, album=t.album, track=t, indent=4)
    off = row_text(row, {}, False, "both")
    got_off = [(off.plain[s.start : s.end], str(s.style)) for s in off.spans]
    assert ("☐", "bright_black") in got_off
    on = row_text(row, {7: t}, False, "both")
    got_on = [(on.plain[s.start : s.end], str(s.style)) for s in on.spans]
    assert ("■", "bold bright_green") in got_on
    header = planning_mod.build_rows(_album_hits(), "artist")[0]
    part = row_text(header, {1: _src(1)}, False, "artist")
    got_part = [(part.plain[s.start : s.end], str(s.style)) for s in part.spans]
    assert ("▣", "bold bright_yellow") in got_part


def test_picker_ctrl_c_restores_snapshot(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    class _FakeReadchar:
        def __init__(self) -> None:
            self.n = 0

        def readkey(self) -> str:
            self.n += 1
            if self.n == 1:
                return planning_mod.readchar_key.SPACE
            raise KeyboardInterrupt

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    with pytest.raises(KeyboardInterrupt):
        planning_mod.run_picker(Console(), session, _picker_hits(), title="Search", height=24)
    assert session.selection == {}


def test_picker_paints_each_frame(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.readchar_key.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    console = Console(record=True, force_terminal=True, width=100, height=30)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(
        console, session, _picker_hits(), title="Search: ", title_term="willow", height=10
    )
    assert console.export_text().count("Ember") >= 2


def test_picker_default_height_budgets_frame(monkeypatch: Any, tmp_path: Path) -> None:
    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.readchar_key.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    console = Console(record=True, force_terminal=True, width=100, height=30)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(console, session, _picker_hits(), title="Search: ", title_term="x")
    assert console.export_text().count("Ember") >= 2


def test_picker_repaints_in_place(monkeypatch: Any, tmp_path: Path) -> None:
    import io
    import re

    from rich.console import Console

    from tidal2ytm import planning as planning_mod

    keys = [planning_mod.readchar_key.DOWN, planning_mod.readchar_key.ENTER]

    class _FakeReadchar:
        def readkey(self) -> str:
            return keys.pop(0)

    monkeypatch.setattr(planning_mod, "_picker_readkey", _FakeReadchar().readkey)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=100, height=30)
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    planning_mod.run_picker(console, session, _picker_hits(), title="Search", height=10)
    assert re.search(r"\x1b\[\d+A", buf.getvalue())
