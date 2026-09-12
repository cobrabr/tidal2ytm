"""
planning.py — Interactive planning TUI: search liked tracks, collect a
cross-search selection, and match it to YTM on demand.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Container
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
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

from .matcher import match_track  # noqa: E402
from .models import SourceTrack  # noqa: E402
from .paths import PLAN_FILE  # noqa: E402
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


def _empty_track_list() -> list[SourceTrack]:
    return []


def _empty_selection() -> dict[int, SourceTrack]:
    return {}


@dataclass
class PlanningSession:
    plan_path: Path
    liked: list[SourceTrack] = field(default_factory=_empty_track_list)
    selection: dict[int, SourceTrack] = field(default_factory=_empty_selection)
    override: bool = False
    backup_done: bool = False

    @property
    def by_id(self) -> dict[int, SourceTrack]:
        return {t.tidal_id: t for t in self.liked}


def toggle_select(selection: dict[int, SourceTrack], track: SourceTrack) -> bool:
    """Toggle one track; returns True when the track is now selected."""
    if track.tidal_id in selection:
        del selection[track.tidal_id]
        return False
    selection[track.tidal_id] = track
    return True


@dataclass
class ListRow:
    """One navigable picker row: an artist/album header or a single track."""

    kind: str  # "artist" | "album" | "disc" | "track"
    artist: str = ""
    album: str = ""
    track: SourceTrack | None = None
    indent: int = 0
    members: tuple[SourceTrack, ...] = ()
    show_artist: bool = False


def default_grouping(n: int) -> str:
    """Size-based default: flat under 20 hits, by artist to 50, artist+album above."""
    if n < 20:
        return "none"
    if n <= 50:
        return "artist"
    return "both"


def _album_sort_key(t: SourceTrack) -> tuple[int, int, str]:
    """Albums by year ascending, unknown years last, then name."""
    return (t.album_year is None, t.album_year or 0, t.album.lower())


VA_LABEL = "Various Artists"


def find_compilations(liked: list[SourceTrack]) -> set[int]:
    """Album ids with more than one distinct track artist: compilations."""
    artists: dict[int, set[str]] = {}
    for t in liked:
        artists.setdefault(t.album_id, set()).add(t.artist)
    return {album_id for album_id, names in artists.items() if len(names) > 1}


def _album_rows(artist: str, bt: list[SourceTrack], rows: list[ListRow]) -> None:
    """Append one album header plus its tracks, adding disc headers when multi-disc."""
    album = bt[0].album
    show_artist = artist == VA_LABEL
    rows.append(ListRow("album", artist=artist, album=album, indent=2, members=tuple(bt)))
    discs: dict[int, list[SourceTrack]] = {}
    for t in bt:
        discs.setdefault(t.disc_num, []).append(t)
    if len(discs) == 1:
        for t in sorted(bt, key=lambda t: (t.disc_num, t.track_num)):
            rows.append(
                ListRow(
                    "track",
                    artist=t.artist,
                    album=t.album,
                    track=t,
                    indent=4,
                    show_artist=show_artist,
                )
            )
        return
    for disc_num in sorted(discs):
        dt = sorted(discs[disc_num], key=lambda t: t.track_num)
        rows.append(ListRow("disc", artist=artist, album=album, indent=4, members=tuple(dt)))
        for t in dt:
            rows.append(
                ListRow(
                    "track",
                    artist=t.artist,
                    album=t.album,
                    track=t,
                    indent=6,
                    show_artist=show_artist,
                )
            )


def build_rows(
    hits: list[SourceTrack], grouping: str, compilations: Container[int] = frozenset()
) -> list[ListRow]:
    """Flatten hits into sorted navigable rows: artists alpha, albums by year,
    tracks by track-list order inside albums, by title otherwise. Disc headers
    appear only inside multi-disc albums when grouping by album. Compilation
    albums group under Various Artists with artist-prefixed tracks."""
    if grouping == "none":
        ordered = sorted(hits, key=lambda t: t.title.lower())
        return [
            ListRow("track", artist=t.artist, album=t.album, track=t, show_artist=True)
            for t in ordered
        ]
    rows: list[ListRow] = []
    artists: dict[str, list[SourceTrack]] = {}
    for t in hits:
        artists.setdefault(VA_LABEL if t.album_id in compilations else t.artist, []).append(t)
    for artist in sorted(artists, key=str.lower):
        atracks = artists[artist]
        rows.append(ListRow("artist", artist=artist, members=tuple(atracks)))
        if grouping == "artist":
            for t in sorted(atracks, key=lambda t: t.title.lower()):
                rows.append(
                    ListRow(
                        "track",
                        artist=t.artist,
                        album=t.album,
                        track=t,
                        indent=2,
                        show_artist=artist == VA_LABEL,
                    )
                )
            continue
        albums: dict[str, list[SourceTrack]] = {}
        for t in atracks:
            albums.setdefault(t.album, []).append(t)
        ordered_albums = sorted(albums.values(), key=lambda bt: _album_sort_key(bt[0]))
        for bt in ordered_albums:
            _album_rows(artist, bt, rows)
    return rows


def _toggle_members(selection: dict[int, SourceTrack], members: list[SourceTrack]) -> None:
    if all(t.tidal_id in selection for t in members):
        for t in members:
            del selection[t.tidal_id]
    else:
        for t in members:
            selection[t.tidal_id] = t


def toggle_row(selection: dict[int, SourceTrack], row: ListRow) -> None:
    """Space on a row: flip a track, or select-all-or-clear a header group."""
    if row.kind == "track":
        assert row.track is not None
        toggle_select(selection, row.track)
    else:
        _toggle_members(selection, list(row.members))


def toggle_scope(
    selection: dict[int, SourceTrack],
    hits: list[SourceTrack],
    artist: str | None = None,
    album: str | None = None,
) -> None:
    """Bulk toggle: all shown, or one artist/album slice (select-all-or-clear)."""
    members = [
        t
        for t in hits
        if (artist is None or t.artist == artist) and (album is None or t.album == album)
    ]
    _toggle_members(selection, members)


def visible_window(rows: list[ListRow], cursor: int, height: int) -> tuple[int, int]:
    """Viewport [start, end) of row indexes keeping the cursor visible."""
    n = len(rows)
    if n <= height:
        return (0, n)
    start = min(max(cursor - height + 1, 0), n - height)
    return (start, start + height)


def run_match_action(  # noqa: C901
    session: PlanningSession,
    yt: Any = None,
    input_fn: Callable[[str], str] | None = None,
    yt_factory: Callable[[], Any] | None = None,
) -> dict[str, int]:
    """Match the session selection into the plan file; returns new/upgraded/kept/skipped counts."""
    console = Console()
    ask: Callable[[str], str] = input if input_fn is None else input_fn
    counts = {"new": 0, "upgraded": 0, "kept": 0, "skipped": 0}
    if not session.selection:
        console.print("Nothing selected.")
        return dict(counts)
    n = len(session.selection)
    plural = "s" if n != 1 else ""
    answer = ask(f"Match {n} selected track{plural}? [Y/n] ").strip().lower()
    if answer not in ("", "y", "yes"):
        return counts
    if yt is None:
        assert yt_factory is not None
        console.print("Authenticating with YouTube Music…")
        yt = yt_factory()
    if session.plan_path.exists():
        plan: dict[str, Any] = load_plan(session.plan_path)
    else:
        plan = {"meta": {}, "artists": []}
    ordered = iter_selection_ordered(session.selection)
    for i, src in enumerate(ordered, 1):
        console.print(f"[{i}/{n}] Matching '{src.title}' by {src.artist} {_src_detail(src)} …")
        existing = find_existing_match(plan, src.tidal_id)
        try:
            result = match_track(src, yt)
            new_vid: str | None = result.yt_video_id
            new_dict: dict[str, Any] = match_result_to_track_dict(result)
        except Exception as exc:
            if existing is None:
                insert_track(plan, unmatched_track_dict(src, f"Match error: {exc}"), src.album_year)
                counts["new"] += 1
                console.print(f"  -> match failed: {exc} [recorded as needs_review]")
            else:
                counts["kept"] += 1
                console.print(f"  -> match failed: {exc} [kept stored match]")
            continue
        conf = result.confidence.overall
        summary = result.confidence.summary or ""
        method = result.match_method.value
        yt_desc = _yt_desc(result)
        action = classify_track(existing, new_vid)
        if action == "skip-transferred":
            counts["skipped"] += 1
            console.print(f"  -> {method} {yt_desc} @ {conf:.2f} [skipped, already transferred]")
        elif action == "add-new":
            insert_track(plan, new_dict, src.album_year)
            counts["new"] += 1
            console.print(f"  -> {method} {yt_desc} @ {conf:.2f} [new]{_summary_suffix(summary)}")
        elif action == "keep-same":
            counts["kept"] += 1
            console.print(f"  -> {method} {yt_desc} @ {conf:.2f} [kept, same as stored]")
        elif session.override:
            assert existing is not None
            update_track_in_plan(plan, src.tidal_id, new_dict)
            counts["upgraded"] += 1
            console.print(f"  -> {method} {yt_desc} @ {conf:.2f} [upgraded, override on]")
        else:
            old = existing or {}
            old_conf = old.get("confidence", {}).get("overall", 0.0)
            new_conf = new_dict.get("confidence", {}).get("overall", 0.0)
            prompt = (
                f"Differing match for '{src.title}':\n"
                f"  stored: {old.get('match_method', 'none')} "
                f"{old.get('yt_video_id', '')} @ {old_conf:.2f}\n"
                f"  new: {new_dict.get('match_method')} "
                f"{new_dict.get('yt_video_id')} @ {new_conf:.2f}\n"
                "Overwrite? [y/N] "
            )
            if ask(prompt).strip().lower() in ("y", "yes"):
                assert existing is not None
                update_track_in_plan(plan, src.tidal_id, new_dict)
                counts["upgraded"] += 1
                console.print(f"  -> upgraded to {new_dict.get('yt_video_id')}")
            else:
                counts["kept"] += 1
                console.print("  -> kept stored match")
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


def _select_all(session: PlanningSession, console: Console) -> None:
    session.selection.clear()
    session.selection.update({t.tidal_id: t for t in session.liked})
    console.print(f"Selected everything: {len(session.selection)} track(s).")


def _fmt_duration(sec: int | None) -> str:
    if not sec:
        return "—"
    m, s = divmod(sec, 60)
    return f"{m}:{s:02d}"


def _src_detail(src: SourceTrack) -> str:
    """One-line Tidal source detail: album, year, duration, ISRC when known."""
    parts = [src.album] if src.album else []
    if src.album_year is not None:
        parts.append(str(src.album_year))
    parts.append(_fmt_duration(src.duration_sec))
    if src.isrc:
        parts.append(f"ISRC {src.isrc}")
    return f"({', '.join(parts)})" if parts else ""


def _yt_desc(result: Any) -> str:
    """One-line YTM hit: video id plus title/artist when known."""
    vid = result.yt_video_id or "(no match)"
    title = result.yt_title or "?"
    artist = result.yt_artist or "?"
    return f"{vid} '{title}' by {artist}"


def _summary_suffix(summary: str) -> str:
    return f" — {summary}" if summary else ""


def hot_hint(pre: str, hot: str, post: str = "", style: str = "bold bright_blue") -> Text:
    """Colour-only hotkey: the action letter coloured inside its word."""
    part = Text()
    part.append(pre, style="dim")
    part.append(hot, style=style)
    part.append(post, style="dim")
    return part


def _mark_style(mark: str) -> str:
    """Checkbox glyph style: grey empty, yellow partial, green full."""
    if mark == "☐":
        return "bright_black"
    if mark == "▣":
        return "bold bright_yellow"
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
    parts.append(_fmt_duration(t.duration_sec))
    return "| " + ", ".join(parts)


def row_text(row: ListRow, selection: dict[int, SourceTrack], cursor: bool, grouping: str) -> Text:
    """One picker row: cursor marker, tri-state checkbox, label, green details."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(" " * row.indent)
    if cursor:
        text.append("❯ ", style="bold cyan")  # noqa: RUF001
    else:
        text.append("  ")
    if row.kind == "track":
        assert row.track is not None
        t = row.track
        mark = "■" if t.tidal_id in selection else "☐"
        text.append(mark, style=_mark_style(mark))
        text.append(" ")
        if grouping == "both":
            text.append(f"{t.track_num:02d}. ")
        if grouping == "none" or row.show_artist:
            text.append(f"{t.artist} - {t.title} ")
        else:
            text.append(f"{t.title} ")
        text.append(_track_details(t, show_album=grouping != "both"), style="green")
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
    text.append(f"  ({n_sel}/{len(row.members)} selected)", style="dim")
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
    """Picker footer: bold-blue keys joined by grey pipes, Enter/Esc yellow."""
    blue = "bold bright_blue"
    bar = Text()
    move = Text()
    for i, k in enumerate(("↑", "↓", "j", "k")):
        if i:
            move.append(" | ", style="dim")
        move.append(k, style=blue)
    move.append(" move", style="dim")
    new_search = Text()
    new_search.append("/", style=blue)
    new_search.append(" | ", style="dim")
    new_search.append("s", style=blue)
    new_search.append(" new search", style="dim")
    parts = [
        move,
        new_search,
        hot_hint("", "space", " to toggle"),
        hot_hint("", "*", " to toggle all"),
        hot_hint("select all from ", "A", "rtist"),
        hot_hint("select all from a", "L", "bum"),
        hot_hint("", "g", f"rouping: {_GROUPING_LABELS[grouping]}"),
    ]
    if clearable:
        parts.append(hot_hint("", "c", "lear all"))
    parts.append(hot_hint("", "q", "uit", style="bold bright_yellow"))
    parts.append(hot_hint("", "Enter", " confirm", style="bold bright_yellow"))
    parts.append(hot_hint("", "Esc", " cancel", style="bold bright_yellow"))
    for i, part in enumerate(parts):
        if i:
            bar.append("   ", style="dim")
        bar.append_text(part)
    return bar


def picker_head(title: str, title_term: str, hits_total: int, n_selected: int, notice: str) -> Text:
    """Title bar: white label, magenta term, dim counts, yellow notice line."""
    head = Text(no_wrap=True, overflow="ellipsis")
    head.append(title, style="bold white")
    if title_term:
        head.append(title_term, style="bold italic magenta")
    head.append(f"  {hits_total} hit(s)  {n_selected} selected", style="dim")
    if notice:
        head.append(f"\n{notice}", style="yellow")
    return head


def picker_screen(
    title: str,
    title_term: str,
    rows: list[ListRow],
    cursor: int,
    selection: dict[int, SourceTrack],
    grouping: str,
    hits_total: int,
    height: int,
    clearable: bool,
    notice: str = "",
) -> Group:
    """One constant-height frame: title, padded viewport, footer. For Live."""
    head = picker_head(title, title_term, hits_total, len(selection), notice)
    start, end = visible_window(rows, cursor, height)
    lines = [row_text(rows[i], selection, i == cursor, grouping) for i in range(start, end)]
    while len(lines) < height:
        lines.append(Text(""))
    body = Text(no_wrap=True, overflow="ellipsis")
    for i, line in enumerate(lines):
        if i:
            body.append("\n")
        body.append_text(line)
    return Group(
        Panel(head, border_style="dim", expand=True),
        Panel(body, expand=True),
        Panel(picker_bar(grouping, clearable), border_style="dim", expand=True),
    )


def _esc_has_tail() -> bool:
    """True when input bytes follow an ESC: a click burst, not a lone Esc press."""
    try:
        import msvcrt

        # getattr: msvcrt members resolve only on Windows; keeps pyright strict clean elsewhere.
        kbhit = getattr(msvcrt, "kbhit")  # noqa: B009
        return bool(kbhit())
    except ImportError:
        import select

        try:
            return bool(select.select([sys.stdin], [], [], 0)[0])
        except (OSError, ValueError):
            # Non-pollable stdin (pytest capture, closed pipe): assume a lone Esc press.
            return False


def _drain_tail() -> None:
    """Swallow a pending escape burst (e.g. a mouse click report)."""
    try:
        import msvcrt

        # getattr: msvcrt members resolve only on Windows; keeps pyright strict clean elsewhere.
        drain_escape(getattr(msvcrt, "kbhit"), getattr(msvcrt, "getwch"))  # noqa: B009
    except ImportError:
        import select

        drain_escape(
            lambda: bool(select.select([sys.stdin], [], [], 0)[0]),
            lambda: sys.stdin.read(1),
        )


def run_picker(  # noqa: C901
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
    snapshot = dict(session.selection)
    compilations = find_compilations(session.liked)
    grouping = default_grouping(len(hits))
    order = {"none": "artist", "artist": "both", "both": "none"}
    cursor = 0
    ask: Callable[[str], str] = input if input_fn is None else input_fn
    if height is None:
        view_h = viewport_height(
            console.size.height or 24, footer_lines(console, grouping, clearable)
        )
    else:
        view_h = height
    rk = readchar_key
    live = Live(
        picker_screen(
            title, title_term, [], 0, session.selection, grouping, 0, view_h, clearable, notice
        ),
        console=console,
        auto_refresh=False,
        transient=False,
    )

    def _confirm_cancel() -> bool:
        """Discard-changes prompt with Live stopped; True means discarded."""
        live.stop()
        answer = ask("Discard these selection changes? [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            session.selection.clear()
            session.selection.update(snapshot)
            return True
        live.start(refresh=True)
        return False

    console.clear()
    live.start()
    try:
        while True:
            if height is None:
                view_h = viewport_height(
                    console.size.height or 24, footer_lines(console, grouping, clearable)
                )
            rows = build_rows(hits, grouping, compilations)
            cursor = max(0, min(cursor, len(rows) - 1)) if rows else 0
            live.update(
                picker_screen(
                    title,
                    title_term,
                    rows,
                    cursor,
                    session.selection,
                    grouping,
                    len(hits),
                    view_h,
                    clearable,
                    notice,
                ),
                refresh=True,
            )
            key = _picker_readkey()
            if key == RESIZE_KEY:
                _clear_screen()
                continue
            if key in ("/", "s") and (session.selection == snapshot or _confirm_cancel()):
                return "search"
            if key == "q":
                if session.selection == snapshot or _confirm_cancel():
                    raise KeyboardInterrupt
            elif key in (rk.UP, "k"):  # type: ignore[union-attr]
                cursor -= 1
            elif key in (rk.DOWN, "j"):  # type: ignore[union-attr]
                cursor += 1
            elif key == rk.PAGE_UP:  # type: ignore[union-attr]
                cursor -= view_h
            elif key == rk.PAGE_DOWN:  # type: ignore[union-attr]
                cursor += view_h
            elif key == rk.SPACE:  # type: ignore[union-attr]
                if rows:
                    toggle_row(session.selection, rows[cursor])
            elif key == "*":
                toggle_scope(session.selection, hits)
            elif key == "A":
                if rows:
                    row = rows[cursor]
                    if row.kind == "track":
                        toggle_scope(session.selection, hits, artist=row.artist)
                    else:
                        toggle_row(session.selection, row)
            elif key == "L":
                if rows:
                    row = rows[cursor]
                    if row.kind == "track":
                        toggle_scope(session.selection, hits, album=row.album)
                    else:
                        toggle_row(session.selection, row)
            elif key == "g":
                grouping = order[grouping]
            elif clearable and key == "c":
                session.selection.clear()
            elif key in (rk.ENTER, "\r", "\n"):  # type: ignore[union-attr]
                return
            elif key == rk.ESC:  # type: ignore[union-attr]
                if _esc_has_tail():
                    _drain_tail()
                    continue
                if session.selection == snapshot:
                    return
                if _confirm_cancel():
                    return
            elif key == "\x03":
                if session.selection == snapshot:
                    return
                if _confirm_cancel():
                    return
            elif key.startswith("\x1b"):
                _drain_tail()
                continue
    except (KeyboardInterrupt, EOFError):
        session.selection.clear()
        session.selection.update(snapshot)
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
    return search_library(session.liked, query, "general")


def _prompt_query() -> str | None:
    """One search prompt; None on abort, "" on empty input."""
    try:
        return input("Search (or paste a Tidal link): ").strip()
    except (KeyboardInterrupt, EOFError):
        return None


def _do_search(console: Console, session: PlanningSession) -> None:  # noqa: C901
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
            return
        notice = "" if direct else f'No matches for "{query}" — closest:'
        if not (HAS_READCHAR and sys.stdin.isatty()):
            break
        if (
            run_picker(console, session, hits, title="Search: ", title_term=query, notice=notice)
            != "search"
        ):
            return
    if notice:
        console.print(Text(notice, style="yellow"))
    listing = Text()
    listing.append(f"{len(hits)} hit(s), Tidal side:", style="underline")
    order: list[SourceTrack] = []
    for t in hits:
        order.append(t)
        listing.append("\n")
        listing.append_text(track_row(len(order), t.tidal_id in session.selection, t))
    console.print(Panel(listing, title=Text(f"Search: {query}"), expand=True))
    console.print(Panel(key_hints(RESULTS_HINTS), border_style="dim", expand=True))
    try:
        raw = input("Toggle numbers, 'a' for all, Enter to go back: ")
    except (KeyboardInterrupt, EOFError):
        return
    raw = raw.strip()
    if not raw:
        return
    if raw.lower() == "a":
        for t in hits:
            session.selection[t.tidal_id] = t
        console.print(f"Selected {len(hits)} track(s).")
        return
    toggled = 0
    for tok in raw.split():
        try:
            idx = int(tok)
        except ValueError:
            continue
        if 1 <= idx <= len(order):
            toggle_select(session.selection, order[idx - 1])
            toggled += 1
    console.print(f"Toggled {toggled}; {len(session.selection)} selected in total.")


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
        raw = input("Remove numbers (space-separated), 'c' to clear all, Enter to go back: ")
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
    for tok in raw.split():
        try:
            idx = int(tok)
        except ValueError:
            continue
        if 1 <= idx <= len(ordered) and ordered[idx - 1].tidal_id in session.selection:
            del session.selection[ordered[idx - 1].tidal_id]
            removed += 1
    console.print(f"Removed {removed}; {len(session.selection)} selected in total.")


def _do_match(console: Console, session: PlanningSession) -> None:
    if not session.selection:
        console.print("Nothing selected.")
        return

    def _login() -> Any:
        from .cli import _ytm_login  # pyright: ignore[reportPrivateUsage]

        return _ytm_login()

    try:
        run_match_action(session, yt_factory=_login)
    except SystemExit:
        # _ytm_login already printed guidance (missing secrets / expired token).
        pass
    except Exception as exc:
        console.print(f"[red]Match failed: {exc}[/red]")
    input("Press Enter to continue...")


HELP_TEXT = """\
[bold underline]Planning[/bold underline]
  e   Select everything (all liked tracks)
  / | s   Search: type query or paste a Tidal link
  v   Review selection across searches (deselect, clear)
  m   Match selection to YTM (asks Y/n first)
  ctrl+o (TTY) / O (fallback)   Toggle override (existing matches overwritten)
  ? | h   Show this help
  q   Quit

In search results: ↑/↓ or j/k moves, space toggles, '*' all, 'A' artist,
'L' album, 'g' grouping, '/'|'s' new search, 'q' quits, Enter confirms,
Esc cancels (asks) to go back.
In review: same, plus 'c' clears the selection.
Without a TTY: numbers toggle, 'a' selects all, Enter goes back.
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
        bar.append(rest, style="dim")
    return bar


MENU_HINTS: list[tuple[tuple[str, ...], str, str]] = [
    (("e",), "verything", "bold bright_blue"),
    (("/", "s"), "earch", "bold bright_blue"),
    (("v",), "iew", "bold bright_blue"),
    (("m",), "atch", "bold bright_blue"),
    (("ctrl+o",), "verride", "bold bright_blue"),
    (("?", "h"), "elp", "bold bright_yellow"),
    (("q",), "uit", "bold bright_yellow"),
]

RESULTS_HINTS: list[tuple[tuple[str, ...], str, str]] = [
    (("1-9",), " toggle", "bold bright_blue"),
    (("a",), "ll", "bold bright_blue"),
    (("Enter",), " back", "bold bright_blue"),
]

REVIEW_HINTS: list[tuple[tuple[str, ...], str, str]] = [
    (("1-9",), " remove", "bold bright_blue"),
    (("c",), "lear", "bold bright_blue"),
    (("Enter",), " back", "bold bright_blue"),
]


def menu_body(session: PlanningSession) -> Text:
    body = Text()
    if session.override:
        # Dark red: the named palette has none; hex downgrades gracefully.
        body.append("OVERRIDE: existing matches overwritten", style="#fecaca on #7f1d1d")
    rows = [
        (("e",), "verything", f"select all {len(session.liked)} liked tracks", "bold bright_blue"),
        (("/", "s"), "earch", "query or Tidal link", "bold bright_blue"),
        (
            ("v",),
            "iew",
            f"review {len(session.selection)} selected across searches",
            "bold bright_blue",
        ),
        (("m",), "atch", "match selection to YTM (asks Y/n first)", "bold bright_blue"),
        (("ctrl+o",), "verride", "overwrite existing matches when on", "bold bright_blue"),
        ((), "", "", ""),
        (("?", "h"), "elp", "show help", "bold bright_yellow"),
        (("q",), "uit", "quit", "bold bright_yellow"),
    ]
    for i, (keys, rest, desc, style) in enumerate(rows):
        if i or session.override:
            body.append("\n")
        if not keys:
            body.append("─" * 40, style="dim")
            continue
        for j, k in enumerate(keys):
            if j:
                body.append(" | ", style="dim")
            body.append(k, style=style)
        body.append(rest + "  ")
        body.append(desc, style="dim")
    return body


def track_row(n: int, selected: bool, t: SourceTrack) -> Text:
    row = Text()
    row.append(f"  {n:>3}. ")
    mark = "■" if selected else "☐"
    row.append(mark, style=_mark_style(mark))
    row.append(f" {t.artist} - {t.title} ")
    row.append(_track_details(t, show_album=True), style="green")
    return row


def logo_text() -> Text:
    return Text.assemble(
        ("tidal2ytm", "bold bright_blue"),
        ("   Tidal liked tracks to YouTube Music", "dim"),
    )


RESIZE_KEY = "\x00R"


def classify_windows_event(event_type: int, key_down: bool, vk: int, ch: str) -> str | None:
    """One console input record: resize marker, key string, or None to discard."""
    if event_type == 4:  # WINDOW_BUFFER_SIZE_EVENT
        return RESIZE_KEY
    if event_type != 1 or not key_down:  # key-down events only
        return None
    if ch in ("\x00", ""):
        return map_windows_key(vk)
    return ch


def map_windows_key(vk: int) -> str | None:
    """Map a Windows virtual-key code to a readchar-style key, else None."""
    return {
        38: readchar_key.UP,  # type: ignore[union-attr]
        40: readchar_key.DOWN,  # type: ignore[union-attr]
        33: readchar_key.PAGE_UP,  # type: ignore[union-attr]
        34: readchar_key.PAGE_DOWN,  # type: ignore[union-attr]
    }.get(vk)


def kernel32() -> Any:
    """kernel32 with 64-bit-safe prototypes (unprototyped calls truncate handles)."""
    import ctypes
    from ctypes import wintypes

    # getattr: ctypes.windll exists only on Windows; keeps pyright strict clean elsewhere.
    kernel = getattr(ctypes, "windll").kernel32  # noqa: B009
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReadConsoleInputW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.ReadConsoleInputW.restype = wintypes.BOOL
    return kernel


def _read_windows_key() -> str:
    """Block for one keypress, discarding mouse/window/resize events.

    msvcrt/readchar observe every console input record, so a click can surface
    as junk or stall the read; filtering to key-down events fixes both.
    """
    import ctypes
    from ctypes import wintypes

    kernel = kernel32()
    stdin = kernel.GetStdHandle(wintypes.DWORD(-10))
    buf = ctypes.create_string_buffer(32)
    count = wintypes.DWORD(0)
    while True:
        kernel.WaitForSingleObject(stdin, 0xFFFFFFFF)
        if not kernel.ReadConsoleInputW(stdin, buf, 1, ctypes.byref(count)):
            continue
        if not count.value:
            continue
        raw = bytes(buf.raw)
        key = classify_windows_event(
            int.from_bytes(raw[0:2], "little"),
            int.from_bytes(raw[4:8], "little") != 0,
            int.from_bytes(raw[10:12], "little"),
            raw[14:16].decode("utf-16-le"),
        )
        if key is None:
            continue
        return key


def _picker_readkey() -> str:
    """One picker keypress: console-event-filtered on Windows, readchar elsewhere."""
    if os.name == "nt":
        return _read_windows_key()
    return readchar.readkey()  # type: ignore[attr-defined]


def _clear_screen() -> None:
    """Hard clear bypassing Rich: console.clear() misroutes inside Live."""
    out = sys.__stdout__
    assert out is not None
    out.write("\x1b[2J\x1b[H]")
    out.flush()


def drain_escape(read_ready: Callable[[], bool], read_one: Callable[[], str]) -> None:
    """Swallow the tail of an ANSI escape burst (e.g. mouse click reports)."""
    while read_ready():
        if "@" <= read_one() <= "~":
            return


def read_key() -> str | None:
    """One logical keypress; mouse bursts and stray control codes collapse to None."""
    key: str = readchar.readkey()  # type: ignore[attr-defined]
    if key.startswith("\x1b"):
        try:
            import msvcrt

            # getattr: msvcrt members resolve only on Windows; keeps pyright strict clean elsewhere.
            drain_escape(getattr(msvcrt, "kbhit"), getattr(msvcrt, "getwch"))  # noqa: B009
        except ImportError:
            import select

            drain_escape(
                lambda: bool(select.select([sys.stdin], [], [], 0)[0]),
                lambda: sys.stdin.read(1),
            )
        return None
    if len(key) == 1 and (key.isprintable() or key in "\r\n\t\x03\x0f"):
        return key
    return None


def _render_menu(console: Console, session: PlanningSession) -> None:
    console.clear()
    logo = logo_text()
    console.print(Panel(logo, border_style="dim", expand=True))
    title = Text()
    title.append("Planning", style="bold underline")
    title.append(f"  {len(session.liked)} liked  {len(session.selection)} selected", style="dim")
    console.print(Panel(menu_body(session), title=title, expand=True))
    console.print(Panel(key_hints(MENU_HINTS), border_style="dim", expand=True))


def _tui_loop(console: Console, session: PlanningSession) -> None:  # noqa: C901
    use_readchar = HAS_READCHAR and sys.stdin.isatty()
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

        if use_readchar:
            if key == "e":
                _select_all(session, console)
            elif key in ("/", "s"):
                _do_search(console, session)
            elif key == "v":
                _do_review(console, session)
            elif key == "m":
                _do_match(console, session)
            elif key == "\x0f":  # ctrl+o
                session.override = not session.override
                state = "ON" if session.override else "off"
                console.print(f"Override {state}.")
            elif key in ("o", "O"):
                console.print("[dim]Override needs ctrl+o (plain 'o' does nothing).[/dim]")
            elif key in ("?", "h"):
                console.print(HELP_TEXT)
                input("Press Enter to continue...")
            elif key == "q":
                break
            else:
                console.print("[dim]Unknown key  (press ? for help)[/dim]")
        else:
            if key == "e":
                _select_all(session, console)
            elif key in ("/", "s"):
                _do_search(console, session)
            elif key == "v":
                _do_review(console, session)
            elif key == "m":
                _do_match(console, session)
            elif key == "O":
                session.override = not session.override
                state = "ON" if session.override else "off"
                console.print(f"Override {state}.")
            elif key == "o":
                console.print("[dim]Override needs capital O here (ctrl+o on a TTY).[/dim]")
            elif key in ("?", "h"):
                console.print(HELP_TEXT)
            elif key == "q":
                break
            else:
                console.print("[dim]Unknown command  (press ? for help)[/dim]")

        _render_menu(console, session)

    console.print("\nPlanning session ended.")


def run_planning(*, tidal_session: Any, plan_path: Path = PLAN_FILE) -> None:
    """TUI entry point: fetch liked tracks once, then loop search/select/review/match."""
    from .tidal_source import get_liked_tracks

    console = Console()
    console.print("Fetching Tidal liked tracks...")
    liked = get_liked_tracks(tidal_session)
    console.print(f"Found {len(liked)} tracks.")
    session = PlanningSession(plan_path=plan_path, liked=liked)
    _tui_loop(console, session)
