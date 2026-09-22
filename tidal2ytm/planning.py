"""
planning.py — Interactive planning TUI: search liked tracks, collect a
cross-search selection, and match it to YTM on demand.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    import readchar
    from readchar import key as readchar_key

    _has_readchar = True
except ImportError:  # pragma: no cover
    readchar = None  # type: ignore
    readchar_key = None  # type: ignore
    _has_readchar = False

HAS_READCHAR = _has_readchar

from .confidence import color_for  # noqa: E402
from .format import fmt_duration  # noqa: E402
from .keys import (  # noqa: E402
    RESIZE_KEY,
    WHEEL_DOWN,
    WHEEL_UP,
    classify_windows_event,
    clear_screen,
    drain_escape,
    drain_tail,
    esc_has_tail,
    kernel32,
    map_windows_key,
    parse_sgr_mouse,
    posix_raw,
    read_ansi_key,
    read_line,
    read_windows_console_key,
    windows_mouse,
)
from .matcher import match_track  # noqa: E402
from .models import MatchMethod, SourceTrack  # noqa: E402
from .paths import PLAN_FILE  # noqa: E402
from .picker_rows import (  # noqa: E402
    ListRow,
    PickerView,
    build_rows,
    scrollbar_thumb,
    toggle_row,
    toggle_scope,
    toggle_select,
    visible_window,
)
from .plan_io import (  # noqa: E402
    backup_plan,
    find_existing_match,
    load_plan,
    save_plan,
    update_plan_meta,
    update_track_in_plan,
)
from .planning_merge import (  # noqa: E402
    classify_track,
    insert_track,
    iter_selection_ordered,
    match_result_to_track_dict,
    unmatched_track_dict,
)
from .planning_search import (  # noqa: E402
    parse_tidal_link,
    resolve_album_link,
    search_library,
)

# Re-exports: the Windows key helpers moved to keys.py (Task 11); planning
# keeps them importable at the old path for callers and tests.
__all__ = ["classify_windows_event", "drain_escape", "kernel32", "map_windows_key"]


@dataclass
class PlanningSession:
    plan_path: Path
    liked: list[SourceTrack] = field(default_factory=list[SourceTrack])
    selection: dict[int, SourceTrack] = field(default_factory=dict[int, SourceTrack])
    override: bool = False
    backup_done: bool = False
    library_loaded: bool = False

    @property
    def by_id(self) -> dict[int, SourceTrack]:
        return {t.tidal_id: t for t in self.liked}


@dataclass
class PlanCounts:
    total: int = 0
    pending: int = 0
    needs_review: int = 0
    transferred: int = 0
    skip: int = 0
    failed: int = 0
    unreadable: bool = False


def read_plan_counts(plan_path: Path) -> PlanCounts:
    """Local-only plan totals; zeros when the file is absent or unreadable."""
    if not plan_path.exists():
        return PlanCounts()
    try:
        meta: dict[str, Any] = load_plan(plan_path).get("meta", {})
    except (OSError, ValueError):
        return PlanCounts(unreadable=True)
    return PlanCounts(
        total=int(meta.get("total_tracks", 0)),
        pending=int(meta.get("pending", 0)),
        needs_review=int(meta.get("needs_review", 0)),
        transferred=int(meta.get("transferred", 0)),
        skip=int(meta.get("skip", 0)),
        failed=int(meta.get("failed", 0)),
    )


@dataclass
class AuthPresence:
    ytm_ok: bool = False
    client_secret: bool = False
    tidal_ok: bool = False


def read_auth_presence(data_dir: Path | None = None) -> AuthPresence:
    """Offline token-validity check; performs no network and never raises."""
    from . import auth as auth_mod
    from . import paths as paths_mod

    root = data_dir if data_dir is not None else paths_mod.DATA_DIR
    return AuthPresence(
        ytm_ok=auth_mod.ytm_cache_valid(root / "ytm_auth.json"),
        client_secret=bool(list(root.glob("client_secret_*.json"))),
        tidal_ok=auth_mod.tidal_cache_valid(root / "tidal_token.json"),
    )


def default_grouping(n: int) -> str:
    """Size-based default: flat under 20 hits, by artist to 50, artist+album above."""
    if n < 20:
        return "none"
    if n <= 50:
        return "artist"
    return "both"


def find_compilations(liked: list[SourceTrack]) -> set[int]:
    """Album ids with more than one distinct track artist: compilations."""
    artists: dict[int, set[str]] = {}
    for t in liked:
        artists.setdefault(t.album_id, set()).add(t.artist)
    return {album_id for album_id, names in artists.items() if len(names) > 1}


def _match_one(
    src: SourceTrack,
    plan: dict[str, Any],
    yt: Any,
    console: Console,
    counts: dict[str, int],
    indent: str = "  ",
) -> dict[str, Any] | None:
    """Match one selected track into the plan; None when matching failed.

    A failed match is recorded here: an unmatched needs_review entry when no
    match is stored yet, otherwise the stored match is kept and counted.
    """
    existing = find_existing_match(plan, src.tidal_id)
    try:
        result = match_track(src, yt)
    except Exception as exc:
        if existing is None:
            insert_track(plan, unmatched_track_dict(src, f"Match error: {exc}"), src.album_year)
            counts["new"] += 1
            note = Text(f"{indent}↳ match failed: {exc} [recorded as needs_review]", style="red")
            console.print(note)
        else:
            counts["kept"] += 1
            console.print(Text(f"{indent}↳ match failed: {exc} [kept stored match]", style="red"))
        return None
    return match_result_to_track_dict(result)


def _resolve_conflict(
    existing: dict[str, Any] | None,
    new_dict: dict[str, Any],
    ask: Callable[[str], str],
) -> bool:
    """Ask whether to overwrite a stored match; False on abort or decline."""
    old = existing or {}
    old_conf = old.get("confidence", {}).get("overall", 0.0)
    new_conf = new_dict.get("confidence", {}).get("overall", 0.0)
    prompt = (
        f"Differing match for '{new_dict.get('title')}':\n"
        f"  stored: {old.get('match_method', 'none')} "
        f"{old.get('yt_video_id', '')} @ {old_conf:.2f}\n"
        f"  new: {new_dict.get('match_method')} "
        f"{new_dict.get('yt_video_id')} @ {new_conf:.2f}\n"
        "Overwrite? [y/N] "
    )
    try:
        return ask(prompt).strip().lower() in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        return False


def _apply_match_action(
    plan: dict[str, Any],
    src: SourceTrack,
    existing: dict[str, Any] | None,
    new_dict: dict[str, Any],
    counts: dict[str, int],
    override: bool,
    ask: Callable[[str], str],
    console: Console,
    indent: str = "  ",
) -> None:
    """Apply one successful match to the plan per its merge action."""
    line = _result_line(new_dict, indent)
    action = classify_track(existing, new_dict["yt_video_id"])
    if action == "skip-transferred":
        counts["skipped"] += 1
        line.append_text(_status_tag("skipped, already transferred"))
        console.print(line)
    elif action == "add-new":
        insert_track(plan, new_dict, src.album_year)
        counts["new"] += 1
        line.append_text(_status_tag("new"))
        console.print(line)
    elif action == "keep-same":
        counts["kept"] += 1
        line.append_text(_status_tag("kept, same as stored"))
        console.print(line)
    elif override:
        assert existing is not None
        update_track_in_plan(plan, src.tidal_id, new_dict)
        counts["upgraded"] += 1
        line.append_text(_status_tag("upgraded, override on"))
        console.print(line)
    elif _resolve_conflict(existing, new_dict, ask):
        update_track_in_plan(plan, src.tidal_id, new_dict)
        counts["upgraded"] += 1
        upgraded = Text(f"{indent}↳ upgraded to ")
        upgraded.append(str(new_dict.get("yt_video_id")), style="italic magenta")
        console.print(upgraded)
    else:
        counts["kept"] += 1
        console.print(Text(f"{indent}↳ kept stored match"))


def run_match_action(
    session: PlanningSession,
    yt: Any = None,
    input_fn: Callable[[str], str] | None = None,
    yt_factory: Callable[[], Any] | None = None,
) -> dict[str, int]:
    """Match the session selection into the plan file; returns new/upgraded/kept/skipped counts."""
    console = Console()
    ask: Callable[[str], str] = read_line if input_fn is None else input_fn
    counts = {"new": 0, "upgraded": 0, "kept": 0, "skipped": 0}
    if not session.selection:
        console.print("Nothing selected.")
        return dict(counts)
    n = len(session.selection)
    plural = "s" if n != 1 else ""
    try:
        answer = ask(f"Match {n} selected track{plural}? [Y/n] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return counts
    if answer not in ("", "y", "yes"):
        return counts
    if yt is None:
        assert yt_factory is not None
        from .cli import wait_status

        with wait_status("Authenticating with YouTube Music"):
            yt = yt_factory()
    if session.plan_path.exists():
        plan: dict[str, Any] = load_plan(session.plan_path)
    else:
        plan = {"meta": {}, "artists": []}
    ordered = iter_selection_ordered(session.selection)
    for i, src in enumerate(ordered, 1):
        indent = " " * (len(f"[{i}/{n}]") + 1)
        console.print(_match_tag(i, n, src))
        existing = find_existing_match(plan, src.tidal_id)
        new_dict = _match_one(src, plan, yt, console, counts, indent)
        if new_dict is None:
            continue
        _apply_match_action(
            plan, src, existing, new_dict, counts, session.override, ask, console, indent
        )
    if not session.backup_done and session.plan_path.exists():
        bpath = backup_plan(session.plan_path)
        console.print(f"Backup -> {bpath.name}")
        session.backup_done = True
    update_plan_meta(plan)
    save_plan(plan, session.plan_path)
    console.print(
        f"Matched: {counts['new']} new, {counts['upgraded']} upgraded, "
        f"{counts['kept']} kept, {counts['skipped']} skipped (transferred)"
    )
    return counts


def _ensure_library(
    console: Console,
    session: PlanningSession,
    input_fn: Callable[[str], str] | None = None,
) -> bool:
    """True when the Tidal library is loaded; otherwise guidance plus pause."""
    if session.library_loaded:
        return True
    ask: Callable[[str], str] = read_line if input_fn is None else input_fn
    console.print("Tidal library not loaded. Authenticate first (a).")
    with contextlib.suppress(KeyboardInterrupt, EOFError):
        ask("Press Enter to continue…")
    return False


def _select_all(
    session: PlanningSession,
    console: Console,
    input_fn: Callable[[str], str] | None = None,
) -> None:
    if not _ensure_library(console, session, input_fn):
        return
    session.selection.clear()
    session.selection.update({t.tidal_id: t for t in session.liked})
    console.print(f"Selected everything: {len(session.selection)} track(s).")


def _match_tag(i: int, n: int, src: SourceTrack) -> Text:
    """First progress line: `[i/n] Matching Title by Artist (Album, Track NN, m:ss, ISRC xxx)…`.

    Names (title/artist/album/track number) render cyan, the counter and the
    ISRC value green, the duration cyan; everything else is default white.
    """
    tag = Text()
    tag.append("[")
    tag.append(f"{i}/{n}", style="green")
    tag.append("] Matching ")
    tag.append(src.title, style="cyan")
    tag.append(" by ")
    tag.append(src.artist, style="cyan")
    segments: list[Text] = []
    if src.album:
        album_seg = Text()
        album_seg.append(src.album, style="cyan")
        segments.append(album_seg)
    if src.track_num > 0:
        num_seg = Text("Track ")
        num_seg.append(f"{src.track_num:02d}", style="cyan")
        segments.append(num_seg)
    dur_seg = Text()
    dur_seg.append(fmt_duration(src.duration_sec), style="cyan")
    segments.append(dur_seg)
    if src.isrc:
        isrc_seg = Text("ISRC ")
        isrc_seg.append(src.isrc, style="green")
        segments.append(isrc_seg)
    tag.append(" (")
    for j, seg in enumerate(segments):
        if j:
            tag.append(", ")
        tag.append_text(seg)
    tag.append(")…")
    return tag


def _breakdown_token(conf: dict[str, Any]) -> Text:
    """Parenthesised confidence detail after `conf. X`.

    Structured `(title a, artist b, album c, Δdur Ns)` when the match carried
    similarities; otherwise the raw summary string, or nothing when neither.
    Similarity numbers follow the confidence threshold colours; the duration
    delta is white at zero, red above.
    """
    title_sim = conf.get("title_similarity")
    artist_sim = conf.get("artist_similarity")
    album_sim = conf.get("album_similarity")
    delta = conf.get("duration_delta_sec")
    token = Text()
    if (
        isinstance(title_sim, int | float)
        and isinstance(artist_sim, int | float)
        and isinstance(album_sim, int | float)
        and isinstance(delta, int)
    ):
        token.append(" (title ")
        token.append(f"{title_sim:.2f}", style=color_for(title_sim))
        token.append(", artist ")
        token.append(f"{artist_sim:.2f}", style=color_for(artist_sim))
        token.append(", album ")
        token.append(f"{album_sim:.2f}", style=color_for(album_sim))
        token.append(", Δdur ")
        token.append(f"{delta}s", style="white" if delta == 0 else "red")
        token.append(")")
        return token
    summary = conf.get("summary", "")
    if summary:
        token.append(f" ({summary})")
    return token


def _result_line(track: dict[str, Any], indent: str = "") -> Text:
    """Markup-safe result line: `↳ [method] id — Title by Artist (Album, Track NN) — conf. X (…)`.

    The arrow prefix aligns under the `[i/n]` counter; names render cyan, the
    method bold yellow, the video id italic magenta. Only the arrow carries a
    style on its own span — a base style would leak onto every default-white
    append below.
    """
    conf: dict[str, Any] = track.get("confidence", {}) or {}
    overall = conf.get("overall", 0.0)
    method = track.get("match_method", "none")
    line = Text()
    line.append(f"{indent}↳ ", style="dim")
    line.append("[")
    line.append(str(method), style="bold yellow")
    line.append("] ")
    line.append(str(track.get("yt_video_id") or "(no match)"), style="italic magenta")
    line.append(" — ")
    line.append(str(track.get("yt_title") or "—"), style="cyan")
    line.append(" by ")
    line.append(str(track.get("yt_artist") or "—"), style="cyan")
    line.append(" (")
    line.append(str(track.get("yt_album") or "—"), style="cyan")
    line.append(", Track ")
    num = track.get("yt_album_track_num")
    line.append(f"{num:02d}" if isinstance(num, int) and num > 0 else "N/A", style="cyan")
    line.append(") — conf. ")
    if method == MatchMethod.ISRC.value:
        line.append("exact match", style="blue")
    else:
        line.append(f"{overall:.2f}", style=color_for(overall))
    line.append_text(_breakdown_token(conf))
    return line


def _status_tag(text: str) -> Text:
    """Trailing result tag: green text inside regular-white brackets."""
    tag = Text()
    tag.append(" [")
    tag.append(text, style="green")
    tag.append("]")
    return tag


def hot_hint(pre: str, hot: str, post: str = "", style: str = "bold bright_blue") -> Text:
    """Colour-only hotkey: the action letter coloured inside its word."""
    part = Text()
    part.append(pre)
    part.append(hot, style=style)
    part.append(post)
    return part


def _mark_style(mark: str) -> str:
    """Checkbox glyph style: grey empty, blue partial, green full/committed."""
    if mark == "☐":
        return "bright_black"
    if mark == "▣":
        return "bold blue"
    return "bold bright_green"


def _check_state(members: tuple[SourceTrack, ...], selection: dict[int, SourceTrack]) -> str:
    """Tri-state checkbox glyph: all, some, or none of the members selected."""
    n = sum(1 for t in members if t.tidal_id in selection)
    if n == 0:
        return "☐"
    if n == len(members):
        return "■"
    return "▣"


def _track_details(t: SourceTrack, show_album: bool) -> str:
    """Detail suffix: album unless grouped by it, year when known, duration."""
    parts = [t.album] if show_album else []
    if t.album_year is not None:
        parts.append(str(t.album_year))
    parts.append(fmt_duration(t.duration_sec))
    return "| " + ", ".join(parts)


def row_text(
    row: ListRow,
    selection: dict[int, SourceTrack],
    cursor: bool,
    grouping: str,
    committed: frozenset[int] = frozenset(),
) -> Text:
    """One picker row: cursor marker, tri-state checkbox, label, dim details."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(" " * row.indent)
    if cursor:
        text.append("❯ ", style="bold bright_blue")  # noqa: RUF001
    else:
        text.append("  ")
    if row.kind == "track":
        assert row.track is not None
        t = row.track
        mark = "✓" if t.tidal_id in committed else ("■" if t.tidal_id in selection else "☐")
        text.append(mark, style=_mark_style(mark))
        text.append(" ")
        if grouping == "both":
            text.append(f"{t.track_num:02d}. ")
        if grouping == "none" or row.show_artist:
            text.append(f"{t.artist} - {t.title} ")
        else:
            text.append(f"{t.title} ")
        text.append(_track_details(t, show_album=grouping != "both"))
        return text
    mark = _check_state(row.members, selection)
    text.append(mark, style=_mark_style(mark))
    text.append(" ")
    if row.kind == "artist":
        text.append(row.artist, style="bold")
    elif row.kind == "album":
        text.append(row.album, style="underline")
    else:
        disc = row.members[0].disc_num if row.members else 0
        text.append(f"Disc {disc}", style="italic")
    n_sel = sum(1 for t in row.members if t.tidal_id in selection)
    text.append(f"  ({n_sel}/{len(row.members)} selected)")
    return text


_GROUPING_LABELS = {"none": "none", "artist": "artist", "both": "artist + album"}


def viewport_height(term_height: int, footer_lines: int) -> int:
    """Body rows that fit: terminal minus title/body/footer panels and margin."""
    return max(4, term_height - 8 - footer_lines)


def footer_lines(console: Console, grouping: str, clearable: bool) -> int:
    """Footer bar height at the current width, measured in-panel (borders narrow it)."""
    panel = Panel(picker_bar(grouping, clearable), border_style="dim", expand=True)
    return len(console.render_lines(panel, console.options, pad=False)) - 2


def picker_bar(grouping: str, clearable: bool) -> Text:
    """Picker footer on two lines: blue picker actions, yellow navigation/confirm."""
    blue = "bold bright_blue"
    yellow = "bold bright_yellow"
    move = Text()
    for i, k in enumerate(("↑", "↓", "j", "k", "wheel")):
        if i:
            move.append(" | ", style="dim")
        move.append(k, style=blue)
    move.append(" move")
    new_search = Text()
    new_search.append("/", style=blue)
    new_search.append(" | new ")
    new_search.append("s", style=blue)
    new_search.append("earch")
    top = Text()
    top_parts = [
        move,
        new_search,
        hot_hint("", "space", " to toggle"),
        hot_hint("", "*", " to toggle all"),
        hot_hint("select all from ", "A", "rtist"),
        hot_hint("select all from a", "L", "bum"),
        hot_hint("", "g", f"rouping: {_GROUPING_LABELS[grouping]}"),
    ]
    if clearable:
        top_parts.append(hot_hint("", "c", "lear all"))
    for i, part in enumerate(top_parts):
        if i:
            top.append("   ", style="dim")
        top.append_text(part)
    esc_back = Text()
    esc_back.append("Esc", style=yellow)
    esc_back.append(" | cancel and go ")
    esc_back.append("b", style=yellow)
    esc_back.append("ack")
    bottom = Text()
    bottom_parts = [
        hot_hint("", "Enter", " confirm", style=yellow),
        esc_back,
        hot_hint("", "q", "uit", style=yellow),
    ]
    for i, part in enumerate(bottom_parts):
        if i:
            bottom.append("   ", style="dim")
        bottom.append_text(part)
    bar = Text()
    bar.append_text(top)
    bar.append("\n")
    bar.append_text(bottom)
    return bar


def picker_head(title: str, title_term: str, hits_total: int, n_selected: int, notice: str) -> Text:
    """Title bar: blue label, italic blue term, dim counts, yellow notice line."""
    head = Text(no_wrap=True, overflow="ellipsis")
    head.append(title, style="bold bright_blue")
    if title_term:
        head.append(title_term, style="bold italic bright_blue")
    head.append(f"  {hits_total} hit(s)  {n_selected} selected")
    if notice:
        head.append(f"\n{notice}", style="yellow")
    return head


def picker_frame(view: PickerView) -> Group:
    """One constant-height frame: title, padded viewport, footer. For Live."""
    head = picker_head(
        view.title, view.title_term, view.hits_total, len(view.selection), view.notice
    )
    start, end = visible_window(view.rows, view.cursor, view.height)
    lines = [
        row_text(view.rows[i], view.selection, i == view.cursor, view.grouping, view.committed)
        for i in range(start, end)
    ]
    while len(lines) < view.height:
        lines.append(Text(""))
    thumb = scrollbar_thumb(len(view.rows), view.height, start)
    if thumb is not None:
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(width=1)
        for i, line in enumerate(lines):
            grid.add_row(line, "█" if i == thumb else "│")
        body = grid
    else:
        body = Text(no_wrap=True, overflow="ellipsis")
        for i, line in enumerate(lines):
            if i:
                body.append("\n")
            body.append_text(line)
    return Group(
        Panel(head, border_style="dim", expand=True),
        Panel(body, expand=True),
        Panel(picker_bar(view.grouping, view.clearable), border_style="dim", expand=True),
    )


# Cursor lines moved per mouse-wheel notch.
_WHEEL_STEP = 3


class _PickerDriver:
    """Single-dispatch key router for the selection picker (see run_picker)."""

    def __init__(
        self,
        console: Console,
        session: PlanningSession,
        hits: list[SourceTrack],
        *,
        title: str,
        title_term: str,
        notice: str,
        clearable: bool,
        live: Live,
        ask: Callable[[str], str],
    ) -> None:
        self.console = console
        self.session = session
        self.hits = hits
        self.title = title
        self.title_term = title_term
        self.notice = notice
        self.clearable = clearable
        self.live = live
        self.ask = ask
        self.snapshot = dict(session.selection)
        self.compilations = find_compilations(session.liked)
        self.grouping = default_grouping(len(hits))
        self.cursor = 0
        self.view_h = viewport_height(
            console.size.height or 24, footer_lines(console, self.grouping, clearable)
        )
        self._grouping_order = {"none": "artist", "artist": "both", "both": "none"}

    def rows(self) -> list[ListRow]:
        return build_rows(self.hits, self.grouping, self.compilations)

    def view(self, rows: list[ListRow]) -> PickerView:
        return PickerView(
            title=self.title,
            title_term=self.title_term,
            rows=rows,
            cursor=self.cursor,
            selection=self.session.selection,
            grouping=self.grouping,
            hits_total=len(self.hits),
            height=self.view_h,
            clearable=self.clearable,
            notice=self.notice,
        )

    def refresh_height(self, height: int | None) -> None:
        """Re-measure the viewport when the frame height is not fixed."""
        if height is not None:
            return
        self.view_h = viewport_height(
            self.console.size.height or 24,
            footer_lines(self.console, self.grouping, self.clearable),
        )

    def clamp_cursor(self, rows: list[ListRow]) -> None:
        self.cursor = max(0, min(self.cursor, len(rows) - 1)) if rows else 0

    def confirm_cancel(self) -> bool:
        """Discard-changes prompt with Live stopped; True means discarded."""
        self.live.stop()
        try:
            answer = self.ask("Discard these selection changes? [y/N] ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            self.live.start(refresh=True)
            return False
        if answer in ("y", "yes"):
            self.session.selection.clear()
            self.session.selection.update(self.snapshot)
            return True
        self.live.start(refresh=True)
        return False

    def confirm_quit(self) -> bool:
        """Quit-confirmation prompt with Live stopped; True means confirmed."""
        self.live.stop()
        try:
            answer = self.ask("Quit tidal2ytm? [y/N] ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            self.live.start(refresh=True)
            return False
        if answer in ("y", "yes"):
            return True
        self.live.start(refresh=True)
        return False

    def _quit(self) -> str:
        """q: confirm the quit when the selection is clean, else offer a discard first."""
        if self.session.selection == self.snapshot:
            return "quit" if self.confirm_quit() else ""
        if self.confirm_cancel():
            return "quit"
        return ""

    def _cancel(self, key: str) -> str:
        """Esc / b / Ctrl+C: drain a click burst, else cancel back to the snapshot."""
        if key == readchar_key.ESC and _esc_has_tail():  # type: ignore[union-attr]
            _drain_tail()
            return ""
        if self.session.selection == self.snapshot or self.confirm_cancel():
            return "confirm"
        return ""

    def _move(self, key: str) -> None:
        """Cursor movement keys."""
        if key in (readchar_key.UP, "k"):  # type: ignore[union-attr]
            self.cursor -= 1
        elif key in (readchar_key.DOWN, "j"):  # type: ignore[union-attr]
            self.cursor += 1
        elif key == WHEEL_UP:
            self.cursor -= _WHEEL_STEP
        elif key == WHEEL_DOWN:
            self.cursor += _WHEEL_STEP
        elif key == readchar_key.PAGE_UP:  # type: ignore[union-attr]
            self.cursor -= self.view_h
        elif key == readchar_key.PAGE_DOWN:  # type: ignore[union-attr]
            self.cursor += self.view_h

    def _toggle(self, key: str, rows: list[ListRow]) -> None:
        """Selection and grouping keys; disjoint from the movement key set."""
        if key == readchar_key.SPACE:  # type: ignore[union-attr]
            if rows:
                toggle_row(self.session.selection, rows[self.cursor])
        elif key == "*":
            toggle_scope(self.session.selection, self.hits)
        elif key in ("A", "L") and rows:
            row = rows[self.cursor]
            if row.kind == "track":
                if key == "A":
                    toggle_scope(self.session.selection, self.hits, artist=row.artist)
                else:
                    toggle_scope(self.session.selection, self.hits, album=row.album)
            else:
                toggle_row(self.session.selection, row)
        elif key == "g":
            self.grouping = self._grouping_order[self.grouping]
        elif self.clearable and key == "c":
            self.session.selection.clear()

    def _wheel(self, key: str) -> bool:
        """SGR mouse sequences move the cursor; True when the key was handled."""
        if not key.startswith("\x1b["):
            return False
        wheel = parse_sgr_mouse(key)
        if wheel is None:
            return False
        self._move(wheel)
        return True

    def dispatch(self, key: str, rows: list[ListRow]) -> str:
        """Apply one keypress; returns "search", "confirm", "quit", or "" to continue."""
        if key == RESIZE_KEY:
            _clear_screen()
            return ""
        if key in ("/", "s"):
            if self.session.selection == self.snapshot or self.confirm_cancel():
                return "search"
            return ""
        if key == "q":
            return self._quit()
        if key in (readchar_key.ENTER, "\r", "\n"):  # type: ignore[union-attr]
            return "confirm"
        if key == readchar_key.ESC or key == "\x03" or key == "b":  # type: ignore[union-attr]
            return self._cancel(key)
        if self._wheel(key):
            return ""
        if key.startswith("\x1b") and key not in (
            readchar_key.UP,  # type: ignore[union-attr]
            readchar_key.DOWN,  # type: ignore[union-attr]
            readchar_key.PAGE_UP,  # type: ignore[union-attr]
            readchar_key.PAGE_DOWN,  # type: ignore[union-attr]
        ):
            _drain_tail()
            return ""
        self._move(key)
        self._toggle(key, rows)
        return ""


def run_picker(
    console: Console,
    session: PlanningSession,
    hits: list[SourceTrack],
    *,
    title: str,
    title_term: str = "",
    notice: str = "",
    height: int | None = None,
    clearable: bool = False,
    input_fn: Callable[[str], str] | None = None,
) -> str | None:
    """Keypress-driven selection list. Enter confirms, Esc cancels to snapshot.

    Returns "search" when the user asks for a new search, else None.
    """
    ask: Callable[[str], str] = read_line if input_fn is None else input_fn
    grouping = default_grouping(len(hits))
    if height is None:
        view_h = viewport_height(
            console.size.height or 24, footer_lines(console, grouping, clearable)
        )
    else:
        view_h = height
    live = Live(
        picker_frame(
            PickerView(
                title=title,
                title_term=title_term,
                rows=[],
                cursor=0,
                selection=session.selection,
                grouping=grouping,
                hits_total=0,
                height=view_h,
                clearable=clearable,
                notice=notice,
            )
        ),
        console=console,
        auto_refresh=False,
        transient=False,
    )
    driver = _PickerDriver(
        console,
        session,
        hits,
        title=title,
        title_term=title_term,
        notice=notice,
        clearable=clearable,
        live=live,
        ask=ask,
    )
    driver.view_h = view_h

    console.clear()
    live.start()
    rows: list[ListRow] = []
    built_grouping: str | None = None
    with _sgr_mouse():
        try:
            while True:
                driver.refresh_height(height)
                if driver.grouping != built_grouping:
                    rows = driver.rows()
                    built_grouping = driver.grouping
                driver.clamp_cursor(rows)
                live.update(picker_frame(driver.view(rows)), refresh=True)
                action = driver.dispatch(_picker_readkey(), rows)
                if action == "search":
                    return "search"
                if action == "quit":
                    raise KeyboardInterrupt
                if action == "confirm":
                    return None
        except (KeyboardInterrupt, EOFError):
            session.selection.clear()
            session.selection.update(driver.snapshot)
            raise
        finally:
            live.stop()


def resolve_search(session: PlanningSession, query: str) -> tuple[list[SourceTrack], bool]:
    """Resolve a query: Tidal link first, else general search with direct flag."""
    link = parse_tidal_link(query)
    if link is not None:
        kind, link_id = link
        if kind == "track":
            hit = session.by_id.get(link_id)
            return ([hit] if hit is not None else [], True)
        return (resolve_album_link(session.liked, link_id), True)
    return search_library(session.liked, query)


def _prompt_query() -> str | None:
    """One search prompt; None on abort, "" on empty input."""
    try:
        return read_line("Search or paste a Tidal link (Enter to go back): ").strip()
    except (KeyboardInterrupt, EOFError):
        return None


def number_toggle_list(raw: str, count: int) -> list[int]:
    """1-based indexes parsed from a whitespace-separated reply; junk ignored."""
    picked: list[int] = []
    for tok in raw.split():
        try:
            idx = int(tok)
        except ValueError:
            continue
        if 1 <= idx <= count:
            picked.append(idx)
    return picked


def _apply_search_toggle(
    console: Console, session: PlanningSession, hits: list[SourceTrack], raw: str
) -> None:
    """Fallback toggle action: '*' selects all, numbers toggle, empty does nothing."""
    if not raw:
        return
    if raw == "*":
        for t in hits:
            session.selection[t.tidal_id] = t
        console.print(f"Selected {len(hits)} track(s).")
        return
    toggled = 0
    for idx in number_toggle_list(raw, len(hits)):
        toggle_select(session.selection, hits[idx - 1])
        toggled += 1
    console.print(f"Toggled {toggled}; {len(session.selection)} selected in total.")


def _present_search_fallback(
    console: Console,
    session: PlanningSession,
    hits: list[SourceTrack],
    query: str,
    notice: str,
) -> None:
    """Numbered listing with the toggle prompt for non-TTY sessions."""
    if notice:
        console.print(Text(notice, style="yellow"))
    listing = Text()
    listing.append(f"{len(hits)} hit(s), Tidal side:", style="underline")
    for i, t in enumerate(hits, 1):
        listing.append("\n")
        listing.append_text(track_row(i, t.tidal_id in session.selection, t))
    console.print(Panel(listing, title=Text(f"Search: {query}"), expand=True))
    console.print(Panel(key_hints(RESULTS_HINTS), border_style="dim", expand=True))
    try:
        raw = read_line("Toggle numbers, '*' for all, Enter to go back: ")
    except (KeyboardInterrupt, EOFError):
        return
    _apply_search_toggle(console, session, hits, raw.strip())


# Seconds the committed-✓ frame stays on screen after a confirmed search round.
_COMMIT_FLASH_SEC = 1.0


def _flash_committed(console: Console, session: PlanningSession, hits: list[SourceTrack]) -> None:
    """Static ✓ frame over the committed staging, then a clean screen so the next
    prompt never scrolls below a stale picker frame."""
    grouping = default_grouping(len(hits))
    view = PickerView(
        title="Search: ",
        title_term="",
        rows=build_rows(hits, grouping, find_compilations(session.liked)),
        cursor=0,
        selection=session.selection,
        grouping=grouping,
        hits_total=len(hits),
        height=viewport_height(console.size.height or 24, footer_lines(console, grouping, False)),
        clearable=False,
        notice="Kept — search again",
        committed=frozenset(session.selection),
    )
    console.clear()
    console.print(picker_frame(view))
    time.sleep(_COMMIT_FLASH_SEC)
    console.clear()


def _picker_round(
    console: Console,
    session: PlanningSession,
    hits: list[SourceTrack],
    query: str,
    notice: str,
) -> bool:
    """One picker round over these hits; True prompts the next query, False exits to the menu."""
    if (
        run_picker(console, session, hits, title="Search: ", title_term=query, notice=notice)
        == "search"
    ):
        return True
    if not session.selection:
        return False
    _flash_committed(console, session, hits)
    return True


def _do_search(
    console: Console,
    session: PlanningSession,
    input_fn: Callable[[str], str] | None = None,
) -> None:
    if not _ensure_library(console, session, input_fn):
        return
    while True:
        query = _prompt_query()
        if query is None:
            return
        if not query:
            console.print("Empty query.")
            return
        hits, direct = resolve_search(session, query)
        if not hits:
            console.print("[yellow]No hits.[/yellow]")
            continue
        notice = "" if direct else f'No matches for "{query}" — closest:'
        if not (HAS_READCHAR and sys.stdin.isatty()):
            _present_search_fallback(console, session, hits, query, notice)
            return
        if not _picker_round(console, session, hits, query, notice):
            return


def _review_picker_action(console: Console, session: PlanningSession) -> None:
    """Review picker; a new-search request opens search instead."""
    if (
        run_picker(
            console,
            session,
            iter_selection_ordered(session.selection),
            title="Selection",
            clearable=True,
        )
        == "search"
    ):
        _do_search(console, session)


def _do_review(console: Console, session: PlanningSession) -> None:
    if not session.selection:
        console.print("Nothing selected yet.")
        return
    if HAS_READCHAR and sys.stdin.isatty():
        _review_picker_action(console, session)
        return
    ordered = iter_selection_ordered(session.selection)
    listing = Text()
    listing.append(f"{len(ordered)} selected across searches:", style="underline")
    for i, t in enumerate(ordered, 1):
        listing.append("\n")
        listing.append_text(track_row(i, True, t))
    console.print(Panel(listing, title="Selection", expand=True))
    console.print(Panel(key_hints(REVIEW_HINTS), border_style="dim", expand=True))
    try:
        raw = read_line("Remove numbers (space-separated), 'c' to clear all, Enter to go back: ")
    except (KeyboardInterrupt, EOFError):
        return
    raw = raw.strip()
    if not raw:
        return
    if raw.lower() == "c":
        session.selection.clear()
        console.print("Selection cleared.")
        return
    removed = 0
    for idx in number_toggle_list(raw, len(ordered)):
        if ordered[idx - 1].tidal_id in session.selection:
            del session.selection[ordered[idx - 1].tidal_id]
            removed += 1
    console.print(f"Removed {removed}; {len(session.selection)} selected in total.")


def _do_match(console: Console, session: PlanningSession) -> None:
    if not session.selection:
        console.print("Nothing selected.")
        return

    def _login() -> Any:
        from .ytm_client import YTMClient

        return YTMClient().login()

    try:
        run_match_action(session, yt_factory=_login)
    except SystemExit:
        # YTMClient.login already printed guidance (missing secrets / expired token).
        pass
    except Exception as exc:
        console.print(f"[red]Match failed: {exc}[/red]")
    with contextlib.suppress(KeyboardInterrupt, EOFError):
        read_line("Press Enter to continue…")


def _do_gateway_review(
    console: Console,
    session: PlanningSession,
    input_fn: Callable[[str], str] | None = None,
) -> bool:
    """Open the full review TUI in-process; True when the user quit the app.

    Guidance plus pause when no plan exists.
    """
    ask: Callable[[str], str] = read_line if input_fn is None else input_fn
    try:
        if not session.plan_path.exists():
            console.print("No transfer plan found. Run a match first (m) to build one.")
            ask("Press Enter to continue…")
            return False
        from .review import run_review

        counts = read_plan_counts(session.plan_path)
        if counts.unreadable:
            console.print(
                "The transfer plan is unreadable. Fix or delete it, then match (m) to rebuild."
            )
            ask("Press Enter to continue…")
            return False
        if counts.total == 0:
            console.print("The transfer plan is empty. Run a match first (m) to add tracks.")
            ask("Press Enter to continue…")
            return False
        return run_review(plan_path=session.plan_path)
    except (KeyboardInterrupt, EOFError):
        return False
    except SystemExit:
        ask("Press Enter to continue…")
        return False
    except Exception as exc:
        console.print(f"[red]Review failed: {exc}[/red]")
        ask("Press Enter to continue…")
        return False


def _do_gateway_transfer(
    console: Console,
    session: PlanningSession,
    *,
    dry_run: bool,
    input_fn: Callable[[str], str] | None = None,
) -> None:
    """Transfer all pending tracks in-process; pending-only, never needs_review."""
    ask: Callable[[str], str] = read_line if input_fn is None else input_fn
    try:
        if not session.plan_path.exists():
            console.print("No transfer plan found. Run a match first (m) to build one.")
            ask("Press Enter to continue…")
            return
        label = "Dry-run transfer" if dry_run else "Transfer"
        answer = ask(f"{label} all pending tracks? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            return
        from .cli import wait_status
        from .transfer import run_transfer
        from .ytm_client import YTMClient

        with wait_status("Authenticating with YouTube Music"):
            yt = YTMClient().login()
        run_transfer(
            yt,
            all_tracks=True,
            dry_run=dry_run,
            include_needs_review=False,
            plan_path=session.plan_path,
        )
    except (KeyboardInterrupt, EOFError):
        return
    except SystemExit:
        pass
    except Exception as exc:
        console.print(f"[red]Transfer failed: {exc}[/red]")
    ask("Press Enter to continue…")


def _attempt_auth(console: Console, label: str, run: Callable[[], object]) -> bool:
    """Run one provider auth; its failure is reported, never skips the other provider."""
    from .cli import wait_status

    try:
        with wait_status(f"Authenticating with {label}"):
            run()
    except Exception as exc:
        console.print(f"[red]{label} auth failed: {exc}[/red]")
        return False
    else:
        console.print(f"{label} auth ok.")
        return True


def _do_gateway_auth(
    console: Console,
    session: PlanningSession,
    input_fn: Callable[[str], str] | None = None,
) -> None:
    """Run the auth flows in-process; cached valid tokens are skipped via force=False."""
    ask: Callable[[str], str] = read_line if input_fn is None else input_fn
    try:
        scope = ask("Authenticate [both/tidal/ytm] (default both): ").strip().lower() or "both"
        if scope not in ("both", "tidal", "ytm"):
            console.print("Unknown scope (press ? for help)")
            ask("Press Enter to continue…")
            return
        from . import auth as auth_mod

        tidal_ok = False
        if scope in ("both", "ytm"):
            _attempt_auth(console, "YTM", partial(auth_mod.run_ytm_auth, force=False))
        if scope in ("both", "tidal"):
            tidal_ok = _attempt_auth(
                console, "Tidal", partial(auth_mod.run_tidal_auth, force=False)
            )
        if tidal_ok and not session.library_loaded:
            from .cli import tidal_login, wait_status
            from .tidal_source import get_liked_tracks

            with wait_status("Fetching Tidal tracks"):
                session.liked = get_liked_tracks(tidal_login())
            session.library_loaded = True
            console.print(f"Found {len(session.liked)} tracks.")
    except (KeyboardInterrupt, EOFError):
        return
    except SystemExit:
        pass
    except Exception as exc:
        console.print(f"[red]Auth failed: {exc}[/red]")
    ask("Press Enter to continue…")


HELP_TEXT = """\
[bold underline]Planning[/bold underline]
  [bold bright_blue]e[/]   Select everything (whole library)
  [bold bright_blue]/[/] | [bold bright_blue]s[/]   Search: type query or paste a Tidal link
  [bold bright_blue]v[/]   Review selection across searches (deselect, clear)
  [bold bright_green]m[/]   Match selection to YTM
  [bold cyan]r[/]   Review all plan matches (full review TUI)
  [bold bright_magenta]t[/]   Transfer pending plan tracks
  [bold bright_magenta]d[/]   Dry-run transfer of pending plan tracks
  [bold bright_red]a[/]   Authenticate with YTM and Tidal
  [bold bright_green]ctrl+o[/] (TTY) / [bold bright_green]O[/] (fallback)   Toggle override mode
  [bold bright_yellow]?[/] | [bold bright_yellow]h[/]   Show this help
  [bold bright_yellow]q[/]   Quit tidal2ytm

In search results: ↑/↓ or j/k moves, space toggles, '*' all, 'A' artist,
'L' album, 'g' grouping, '/'|'s' new search, 'q' quits, Enter confirms,
Esc|'b' cancels (asks) to go back.
In review: same, plus 'c' clears the selection.
Without a TTY: numbers toggle, '*' selects all, Enter goes back.
"""


def key_hints(hints: list[tuple[tuple[str, ...], str, str]]) -> Text:
    """Footer hint bar. Bold keys joined by dim pipes, merged into the word."""
    bar = Text()
    for i, (keys, rest, style) in enumerate(hints):
        if i:
            bar.append("   ", style="dim")
        for j, k in enumerate(keys):
            if j:
                bar.append(" | ", style="dim")
            bar.append(k, style=style)
        bar.append(rest)
    return bar


# Main-menu colour groups: selection blue (matches the search footer), match
# green, review cyan, transfer magenta, auth red, meta keys yellow.
_STYLE_SELECT = "bold bright_blue"
_STYLE_MATCH = "bold bright_green"
_STYLE_REVIEW = "bold cyan"
_STYLE_TRANSFER = "bold bright_magenta"
_STYLE_AUTH = "bold bright_red"
_STYLE_HELP = "bold bright_yellow"

RESULTS_HINTS: list[tuple[tuple[str, ...], str, str]] = [
    (("1-9",), " toggle", "bold bright_blue"),
    (("*",), " all", "bold bright_blue"),
    (("Enter",), " back", "bold bright_blue"),
]

REVIEW_HINTS: list[tuple[tuple[str, ...], str, str]] = [
    (("1-9",), " remove", "bold bright_blue"),
    (("c",), "lear", "bold bright_blue"),
    (("Enter",), " back", "bold bright_blue"),
]


def menu_body(session: PlanningSession, has_plan: bool = True) -> Text:
    """Grouped instruction lines; same-colour options share a line, groups split by blanks."""
    blue = _STYLE_SELECT
    green = _STYLE_MATCH
    select = Text.assemble(
        ("/", blue),
        (" | ", "dim"),
        ("s", blue),
        ("earch for tracks (query or Tidal link)", ""),
    )
    if session.library_loaded:
        track_word = "track" if len(session.liked) == 1 else "tracks"
        selection = Text.assemble(
            ("select ", ""),
            ("e", blue),
            (f"verything in your library ({len(session.liked)} {track_word}) or ", ""),
            ("v", blue),
            (f"iew the {len(session.selection)} selected across searches", ""),
        )
    else:
        selection = Text.assemble(
            ("library not loaded, cannot select or view tracks — ", ""),
            ("a", _STYLE_AUTH),
            ("uthenticate", ""),
        )
    match = Text.assemble(
        ("m", green),
        ("atch your selection to YTM, ", ""),
        ("ctrl+o", green),
        ("verride existing matches", ""),
    )
    review = Text.assemble(("r", _STYLE_REVIEW), ("eview every match in the plan", ""))
    transfer = Text.assemble(
        ("t", _STYLE_TRANSFER),
        ("ransfer pending tracks to YTM or ", ""),
        ("d", _STYLE_TRANSFER),
        ("ry-run the transfer", ""),
    )
    auth = Text.assemble(("a", _STYLE_AUTH), ("uthenticate with Tidal and YTM", ""))
    if not has_plan:
        review.append("  (needs plan)")
        transfer.append("  (needs plan)")
    meta = Text.assemble(
        ("?", _STYLE_HELP),
        (" | ", "dim"),
        ("h", _STYLE_HELP),
        ("elp", ""),
    )
    quit_line = Text.assemble(("q", _STYLE_HELP), ("uit tidal2ytm", ""))
    blocks: list[list[Text]] = [
        [select, selection],
        [match],
        [review],
        [transfer],
        [auth],
        [Text("─" * 40, style="dim"), meta, quit_line],
    ]
    body = Text()
    if session.override:
        # Dark red: the named palette has none; hex downgrades gracefully.
        body.append("OVERRIDE: existing matches overwritten", style="#fecaca on #7f1d1d")
        body.append("\n")
    for bi, block in enumerate(blocks):
        if bi:
            body.append("\n")
        for line in block:
            body.append_text(line)
            body.append("\n")
    body.plain = body.plain.removesuffix("\n")
    return body


def status_body(
    session: PlanningSession,
    counts: PlanCounts | None = None,
    auth: AuthPresence | None = None,
    has_plan: bool = True,
) -> Text:
    """Status panel: library, plan totals, and auth presence with ✓/✕ glyphs."""
    body = Text()
    if session.library_loaded:
        body.append(str(len(session.liked)), style=_STYLE_SELECT)
        body.append(" in library   ")
        body.append(str(len(session.selection)), style=_STYLE_SELECT)
        body.append(" selected")
    else:
        body.append("library not loaded — ")
        body.append("a", style=_STYLE_AUTH)
        body.append("uthenticate")
    body.append("\n")
    if not has_plan or counts is None:
        body.append("plan", style=_STYLE_REVIEW)
        body.append("  no plan yet — match (m) first")
    elif counts.unreadable:
        body.append("plan", style=_STYLE_REVIEW)
        body.append("  ")
        body.append("unreadable — showing zeros", style="bold red")
    else:
        body.append("plan", style=_STYLE_REVIEW)
        body.append("  total ")
        body.append(str(counts.total), style="bold")
        body.append("  pending ")
        body.append(str(counts.pending), style=_STYLE_TRANSFER)
        body.append("  needs_review ")
        body.append(str(counts.needs_review), style="bold bright_cyan")
        body.append("  transferred ")
        body.append(str(counts.transferred), style=_STYLE_TRANSFER)
        body.append(f"  skip {counts.skip}  failed ")
        body.append(str(counts.failed), style="bold red")
    body.append("\n")
    if auth is not None:
        body.append("auth", style=_STYLE_AUTH)
        body.append("  ")
        for label, ok in (
            ("YTM", auth.ytm_ok),
            ("client_secret", auth.client_secret),
            ("Tidal", auth.tidal_ok),
        ):
            glyph, glyph_style = _status_glyph(ok)
            body.append(f"{label} ")
            body.append(glyph, style=glyph_style)
            body.append("  ")
    return body


def _status_glyph(ok: bool) -> tuple[str, str]:
    """Green ✓ when the cached token is valid, red ✕ when missing or expired."""
    return ("✓", "bold green") if ok else ("✕", "bold red")


def track_row(n: int, selected: bool, t: SourceTrack) -> Text:
    row = Text()
    row.append(f"  {n:>3}. ")
    mark = "■" if selected else "☐"
    row.append(mark, style=_mark_style(mark))
    row.append(f" {t.artist} - {t.title} ")
    row.append(_track_details(t, show_album=True))
    return row


def _esc_has_tail() -> bool:
    """Escape-burst probe kept patchable at this module path; see keys.esc_has_tail."""
    return esc_has_tail()


def _drain_tail() -> None:
    """Escape-burst drain kept patchable at this module path; see keys.drain_tail."""
    drain_tail()


def _clear_screen() -> None:
    """Hard clear kept at this module path; see keys.clear_screen."""
    clear_screen()


@contextlib.contextmanager
def _sgr_mouse() -> Generator[None, None, None]:
    """Mouse-wheel input scope: SGR codes for posix TTYs, raw mode on both.

    Windows gets console mouse flags via `windows_mouse`; posix additionally
    needs SGR 1000/1006 reports plus raw reads so the picker sees wheel events.
    """
    if os.name == "nt":
        with windows_mouse():
            yield
        return
    if not sys.stdin.isatty():
        yield
        return
    sys.stdout.write("\x1b[?1000h\x1b[?1006h")
    sys.stdout.flush()
    try:
        with posix_raw():
            yield
    finally:
        sys.stdout.write("\x1b[?1000l")
        sys.stdout.flush()


def _picker_readkey() -> str:
    """One picker keypress: console-event-filtered on Windows, raw posix reads elsewhere."""
    if os.name == "nt":
        return read_windows_console_key()
    return read_ansi_key()


def read_key() -> str | None:
    """One logical keypress; mouse bursts and stray control codes collapse to None."""
    key: str = readchar.readkey()  # type: ignore[attr-defined]
    if key.startswith("\x1b"):
        _drain_tail()
        return None
    if len(key) == 1 and (key.isprintable() or key in "\r\n\t\x03\x0f"):
        return key
    return None


def _ramp(t: float) -> str:
    """Hex colour along the logo ramp: grey -> white -> red for t in [0, 1]."""
    grey = (0x80, 0x80, 0x80)
    white = (0xFF, 0xFF, 0xFF)
    red = (0xFF, 0x00, 0x00)
    low, high, span = (grey, white, t * 2) if t < 0.5 else (white, red, (t - 0.5) * 2)
    mixed = tuple(round(a + (b - a) * span) for a, b in zip(low, high, strict=True))
    return f"#{mixed[0]:02x}{mixed[1]:02x}{mixed[2]:02x}"


def logo_text() -> Text:
    word = "tidal2ytm"
    logo = Text()
    for i, ch in enumerate(word):
        logo.append(ch, style=f"bold {_ramp(i / (len(word) - 1))}")
    logo.append("   Transfer Tidal tracks to YouTube Music", style="dim")
    return logo


def _render_menu(console: Console, session: PlanningSession) -> None:
    console.clear()
    console.print(Panel(logo_text(), border_style="dim", expand=True))
    has_plan = session.plan_path.exists()
    counts = read_plan_counts(session.plan_path) if has_plan else None
    auth = read_auth_presence()
    menu = Panel(
        menu_body(session, has_plan),
        title=Text("Main menu", style="bold"),
        expand=True,
    )
    status_text = status_body(session, counts, auth, has_plan)
    # Fixed content width: the menu takes everything else.
    width = max((len(line) for line in status_text.plain.splitlines()), default=0) + 4
    status = Panel(status_text, title=Text("Status", style="bold"), width=width, expand=False)
    # Columns stacks the panels vertically when the terminal is too narrow.
    console.print(Columns([menu, status], equal=False, expand=True))


# Menu dispatch table: key → handler. The override and unknown-key paths are
# mode-dependent (ctrl+o vs capital O) and live in _dispatch_menu.
COMMANDS: dict[str, Callable[[Console, PlanningSession], Any]] = {
    "e": lambda console, session: _select_all(session, console),
    "/": _do_search,
    "s": _do_search,
    "v": _do_review,
    "m": lambda console, session: _do_match(console, session),
    "r": _do_gateway_review,
    "t": lambda console, session: _do_gateway_transfer(console, session, dry_run=False),
    "d": lambda console, session: _do_gateway_transfer(console, session, dry_run=True),
    "a": _do_gateway_auth,
}

# Per-mode messages for keys that look like the override key but are not.
_OVERRIDE_HINTS: dict[bool, str] = {
    True: "Override needs ctrl+o (plain 'o' does nothing).",
    False: "Override needs capital O here (ctrl+o on a TTY).",
}

_UNKNOWN_MESSAGES: dict[bool, str] = {
    True: "Unknown key  (press ? for help)",
    False: "Unknown command  (press ? for help)",
}


def _dispatch_menu(
    console: Console,
    session: PlanningSession,
    key: str,
    override_key: str,
    use_readchar: bool,
) -> bool:
    """Run one menu command; True when the session should end."""
    command = COMMANDS.get(key)
    if command is not None:
        # A gateway may request an app-level quit (review's confirmed q).
        return bool(command(console, session))
    if key == override_key:
        session.override = not session.override
        state = "ON" if session.override else "off"
        console.print(f"Override {state}.")
    elif key in ("o", "O"):
        console.print(_OVERRIDE_HINTS[use_readchar])
    elif key in ("?", "h"):
        console.print(HELP_TEXT)
        if use_readchar:
            with contextlib.suppress(KeyboardInterrupt, EOFError):
                read_line("Press Enter to continue…")
    elif key == "q":
        return True
    else:
        console.print(_UNKNOWN_MESSAGES[use_readchar])
    return False


def _tui_loop(console: Console, session: PlanningSession) -> None:
    use_readchar = HAS_READCHAR and sys.stdin.isatty()
    override_key = "\x0f" if use_readchar else "O"
    _render_menu(console, session)
    while True:
        try:
            if use_readchar:
                key = read_key()
                if key is None:
                    continue
                if key == readchar_key.CTRL_C:  # type: ignore[attr-defined]
                    break
            else:
                key = input().strip()
        except (KeyboardInterrupt, EOFError):
            break

        if _dispatch_menu(console, session, key, override_key, use_readchar):
            break

        _render_menu(console, session)


def _try_startup_library_load(
    console: Console,
    session: PlanningSession,
    input_fn: Callable[[str], str] | None = None,
) -> None:
    """Load the Tidal library at startup when the cached token is safely fresh.

    Never logs in: a stale or missing token leaves the library unloaded for
    the auth action (a), and a failed fetch prints guidance plus a pause (the
    first menu render would otherwise clear it unseen).
    """
    from .cli import tidal_login, wait_status
    from .tidal_source import get_liked_tracks

    ask: Callable[[str], str] = read_line if input_fn is None else input_fn
    try:
        tidal_session = tidal_login(login=False)
        if tidal_session is None:
            return
        with wait_status("Fetching Tidal tracks"):
            session.liked = get_liked_tracks(tidal_session)
    except Exception:
        console.print(f"Could not load Tidal library — [{_STYLE_AUTH}]a[/]uthenticate to retry.")
        ask("Press Enter to continue…")
        return
    session.library_loaded = True


def run_planning(*, plan_path: Path = PLAN_FILE) -> None:
    """TUI entry point: menu first; the library loads at startup when the cached
    Tidal token is fresh, otherwise on demand via auth (a)."""
    console = Console()
    session = PlanningSession(plan_path=plan_path)
    with console.screen():
        _try_startup_library_load(console, session)
        _tui_loop(console, session)
    console.print("\nPlanning session ended.")
