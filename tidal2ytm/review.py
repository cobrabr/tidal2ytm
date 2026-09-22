"""
review.py — Interactive rich TUI navigator for the tidal2ytm transfer plan.
"""

from __future__ import annotations

import contextlib
import difflib
import os
import sys
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .confidence import color_for
from .errors import PlanNotFoundError
from .format import fmt_duration
from .keys import (
    CTRL_C,
    RESIZE_KEY,
    WHEEL_DOWN,
    WHEEL_UP,
    clear_screen,
    parse_sgr_mouse,
    posix_raw,
    read_ansi_key,
    read_line,
    read_windows_console_key,
    readchar_key,
    windows_mouse,
)
from .matcher import DURATION_TOLERANCE_SEC
from .models import MatchMethod, TrackStatus
from .paths import PLAN_FILE
from .picker_rows import scrollbar_thumb, visible_window
from .plan_io import (
    backup_plan,
    extract_video_id,
    iter_tracks_filtered,
    load_plan,
    save_plan,
    update_plan_meta,
    update_track_in_plan,
)
from .style import STATUS_STYLE

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

HELP_TEXT = """\
[bold]List[/bold]
   j / ↓                Next track                k / ↑   Previous track
   n / →                Next album                p / ←   Previous album
   N                    Next artist               P       Previous artist
   v / Enter            Open detail view          wheel   Scroll

[bold]Detail[/bold]
   v / Enter / Esc      Back to the list
   j / k / n / p / N / P    Move (detail follows the cursor)

[bold]Track decisions (work in both views)[/bold]
   a   Accept match          → pending
   s   Skip track            → skip
   r   Reject match          → needs_review
   o   Override video ID     → prompts, then pending on success
   t   Mark as transferred   → transferred

[bold]Other[/bold]
   ? / h   Show this help
   Esc / b Back to the main menu (Esc backs out of the detail view first)
   q       Quit tidal2ytm (asks for confirmation)

All decisions are written immediately — there is no unsaved state.
"""


def _confidence_text(value: float | None, match_method: str = "none") -> Text:
    if match_method == "isrc":
        return Text("exact match", style="blue")
    if value is None:
        return Text("—")
    return Text(f"{value:.2f}", style=color_for(value))


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class ReviewSession:
    plan: dict[str, Any]
    plan_path: Path
    backup_done: bool
    cursor: int
    filtered_tracks: list[dict[str, Any]]
    # "list" shows the scrollable match list; "detail" the focused track view.
    mode: str = "list"
    # One-line confirmation/notice rendered in the frame head; cleared on the
    # next keypress (Live redraws would clobber direct console.print output).
    flash: str = ""
    # Body rows of the current viewport; refreshed every frame, drives PgUp/PgDn.
    view_h: int = 0
    # True when the user confirmed quitting the whole app from the review TUI.
    quit_app: bool = False

    # map tidal_id -> (artist_match_id, album_match_id, album_name,
    # track_index_in_album, album_total)
    track_context: dict[int, dict[str, Any]] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]


def _build_track_context(
    plan: dict[str, Any], filtered: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """
    For each filtered track, record which album it belongs to and its
    position within the *filtered* album subset.
    """
    # Index: tidal_id → (artist_match_id, album_match_id, album_name)
    tidal_id_to_album: dict[int, tuple[str, str, str]] = {}
    for artist in plan.get("artists", []):
        for album in artist.get("albums", []):
            for track in album.get("tracks", []):
                tidal_id_to_album[track["tidal_id"]] = (
                    artist["match_id"],
                    album["match_id"],
                    album["name"],
                )

    # Group filtered tracks by album_match_id
    album_buckets: dict[str, list[dict[str, Any]]] = {}
    for t in filtered:
        _, alb_id, _ = tidal_id_to_album.get(t["tidal_id"], ("", "", ""))
        album_buckets.setdefault(alb_id, []).append(t)

    # Positions within each bucket in a single enumerate pass (no index scan)
    positions: dict[int, int] = {}
    totals: dict[str, int] = {}
    for alb_id, bucket in album_buckets.items():
        totals[alb_id] = len(bucket)
        for pos, t in enumerate(bucket, 1):
            positions[t["tidal_id"]] = pos

    ctx: dict[int, dict[str, Any]] = {}
    for t in filtered:
        ar_id, alb_id, alb_name = tidal_id_to_album.get(t["tidal_id"], ("", "", ""))
        ctx[t["tidal_id"]] = {
            "artist_match_id": ar_id,
            "album_match_id": alb_id,
            "album_name": alb_name,
            "pos_in_album": positions[t["tidal_id"]],
            "total_in_album": totals[alb_id],
        }
    return ctx


def review_title(track: dict[str, Any], ctx: dict[int, dict[str, Any]]) -> Text:
    """Panel title with a cyan Review marker plus the album match id."""
    tidal_id = track.get("tidal_id", 0)
    info = ctx.get(tidal_id, {})
    title = Text()
    title.append("Review", style="bold cyan")
    title.append(f"  {info.get('album_match_id', '')}", style="bold")
    title.append(
        f"  Track {info.get('pos_in_album', '?')} of {info.get('total_in_album', '?')}",
        style="dim",
    )
    return title


# ---------------------------------------------------------------------------
# List model: album headers + stacked git-diff-style track rows
# ---------------------------------------------------------------------------


@dataclass
class ReviewRow:
    """One list row: an album header or a single track.

    `index` is the position in `ReviewSession.filtered_tracks` (-1 for album
    headers, which are display-only — the cursor always sits on a track).
    """

    kind: str  # "album" | "track"
    index: int
    album_match_id: str = ""
    album_name: str = ""
    album_artist: str = ""
    album_size: int = 0


def build_review_rows(
    filtered: list[dict[str, Any]], ctx: dict[int, dict[str, Any]]
) -> list[ReviewRow]:
    """Group tracks under album headers in first-appearance (plan) order."""
    rows: list[ReviewRow] = []
    header_by_album: dict[str, ReviewRow] = {}
    for i, track in enumerate(filtered):
        info = ctx.get(track.get("tidal_id", 0), {})
        album_id = str(info.get("album_match_id", ""))
        header = header_by_album.get(album_id)
        if header is None:
            header = ReviewRow(
                "album",
                -1,
                album_match_id=album_id,
                album_name=str(info.get("album_name", "")),
                album_artist=str(track.get("artist", "") or ""),
            )
            header_by_album[album_id] = header
            rows.append(header)
        header.album_size += 1
        rows.append(ReviewRow("track", i))
    return rows


def _album_mismatch(track: dict[str, Any]) -> bool:
    yt_album = track.get("yt_album", "") or ""
    tidal_album = track.get("tidal_album", "") or ""
    return bool(yt_album and tidal_album and yt_album.casefold() != tidal_album.casefold())


def _dur_mismatch(track: dict[str, Any]) -> bool:
    yt_dur = track.get("yt_duration_sec") or 0
    tidal_dur = track.get("tidal_duration_sec") or 0
    return bool(yt_dur and tidal_dur and abs(yt_dur - tidal_dur) > DURATION_TOLERANCE_SEC)


def _title_mismatch(track: dict[str, Any]) -> bool:
    yt_title = track.get("yt_title", "") or ""
    title = track.get("title", "") or ""
    return bool(yt_title and title and yt_title.casefold() != title.casefold())


def _num_mismatch(track: dict[str, Any]) -> bool:
    tidal_num = track.get("tidal_track_num")
    yt_num = track.get("yt_album_track_num")
    return bool(tidal_num and yt_num and tidal_num != yt_num)


def _status_label(status: str) -> str:
    """Human-facing status words for the head line."""
    if status == TrackStatus.PENDING.value:
        return "pending transfer"
    if status == TrackStatus.NEEDS_REVIEW.value:
        return "needs review"
    return status


def _track_head_line(track: dict[str, Any], cursor: bool) -> Text:
    """First line of a track row: cursor marker, song name, confidence, status."""
    line = Text(no_wrap=True, overflow="ellipsis")
    if cursor:
        line.append("❯ ", style="bold cyan")  # noqa: RUF001
    else:
        line.append("  ")
    line.append(str(track.get("artist", "—") or "—"), style="cyan")
    line.append(" - ")
    line.append(str(track.get("title", "—") or "—"), style="cyan")
    line.append(" | ", style="dim white")
    conf: dict[str, Any] = track.get("confidence", {}) or {}
    match_method = track.get("match_method", "none")
    if match_method != MatchMethod.ISRC.value:
        line.append("conf. ")
    line.append_text(_confidence_text(conf.get("overall"), match_method))
    line.append(" | ", style="dim white")
    status = track.get("status", "")
    line.append(_status_label(status), style=STATUS_STYLE.get(status, ""))
    if _album_mismatch(track) or _dur_mismatch(track):
        line.append(" ⚠", style="yellow")
    return line


def _num_field(num: Any) -> str:
    """Zero-padded two-digit track number, or N/A when absent/unknown."""
    return f"{num:02d}" if isinstance(num, int) and num > 0 else "N/A"


# Diff-line layout knobs — tweak these to restyle the stacked rows:
_DIFF_INDENT = "    "  # plain leading spaces; both bands start after this
_ID_GAP = 3  # spaces between the label column and the video-id column
_ID_WIDTH = 11  # video-id column width; the Tidal line pads to this
_FIELD_SEP = " · "  # separator between the property columns
# Tidal band: neutral dark grey. YTM band: translucent dark red — true
# translucency is not renderable, so this is dark red pre-blended over a
# dark terminal background. Changed words get a solid highlight instead of
# just bold: black on white (Tidal) / white on red (YTM).
_DIFF_BG = "on #2b2b2b"
_DIFF_BG_HOT = "on #3d3d3d"
_YDIFF_BG = "on #2c0c11"
_YDIFF_BG_HOT = "on #601a25"
_T_BASE = f"white {_DIFF_BG}"
_T_HOT = f"bold black {_DIFF_BG_HOT}"
_Y_BASE = f"red {_YDIFF_BG}"
_Y_HOT = f"bold white {_YDIFF_BG_HOT}"
_Y_ID = f"italic red {_YDIFF_BG}"
_Y_DIM = f"dim {_YDIFF_BG}"


def _cell_len(text: str) -> int:
    """Visible cell width (wide glyphs count double)."""
    return Text(text).cell_len


def _diff_columns(track: dict[str, Any]) -> list[tuple[str, str, bool, bool]]:
    """One (tidal text, ytm text, casefold compare, tidal-hot) per property column."""
    return [
        (
            str(track.get("title", "—") or "—"),
            str(track.get("yt_title", "—") or "—"),
            True,
            _title_mismatch(track),
        ),
        (
            str(track.get("tidal_album", "—") or "—"),
            str(track.get("yt_album", "") or "—"),
            True,
            _album_mismatch(track),
        ),
        (
            f"Track {_num_field(track.get('tidal_track_num'))}",
            f"Track {_num_field(track.get('yt_album_track_num'))}",
            False,
            _num_mismatch(track),
        ),
        (
            fmt_duration(track.get("tidal_duration_sec")),
            fmt_duration(track.get("yt_duration_sec") or 0),
            False,
            _dur_mismatch(track),
        ),
    ]


def _column_widths(columns: list[tuple[str, str, bool, bool]]) -> list[int]:
    """Per-column width: the wider of the two lines, so properties align."""
    return [max(_cell_len(tidal), _cell_len(ytm)) for tidal, ytm, _, _ in columns]


def _append_ytm_field(line: Text, tidal_text: str, ytm_text: str, *, fold: bool = False) -> None:
    """Append a YTM field, bolding the characters that differ from Tidal.

    Literal text comparison, not the semantic mismatch helpers: a 1-second
    duration delta is within tolerance yet still worth highlighting.
    """
    same = tidal_text.casefold() == ytm_text.casefold() if fold else tidal_text == ytm_text
    if same:
        line.append(ytm_text, style=_Y_BASE)
        return
    matcher = difflib.SequenceMatcher(None, tidal_text, ytm_text, autojunk=False)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if j1 == j2:
            continue
        line.append(ytm_text[j1:j2], style=_Y_HOT if tag != "equal" else _Y_BASE)


def _tidal_diff_line(track: dict[str, Any]) -> Text:
    """Second row line: the Tidal source in white, columns aligned with the YTM line."""
    line = Text(no_wrap=True, overflow="ellipsis")
    video_id = track.get("yt_video_id", "") or ""
    line.append(_DIFF_INDENT)
    line.append("Tidal", style=f"bold white {_DIFF_BG}")
    id_width = len(video_id) if video_id else _ID_WIDTH
    line.append(" " * (_ID_GAP + id_width + len(_FIELD_SEP)), style=_DIFF_BG)
    columns = _diff_columns(track)
    widths = _column_widths(columns)
    for i, (tidal_text, _, _, hot) in enumerate(columns):
        if i:
            line.append(_FIELD_SEP, style=_T_BASE)
        line.append(tidal_text, style=_T_HOT if hot else _T_BASE)
        pad = widths[i] - _cell_len(tidal_text)
        if pad:
            line.append(" " * pad, style=_T_BASE)
    return line


def _ytm_diff_line(track: dict[str, Any]) -> Text:
    """Third row line: the YTM match in red, id first so the columns align with Tidal."""
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append(_DIFF_INDENT)
    line.append("  YTM", style=f"bold red {_YDIFF_BG}")
    line.append(" " * _ID_GAP, style=_YDIFF_BG)
    video_id = track.get("yt_video_id", "") or ""
    if not video_id:
        line.append("— no match —", style=_Y_DIM)
        return line
    line.append(video_id, style=_Y_ID)
    line.append(_FIELD_SEP, style=_Y_BASE)
    columns = _diff_columns(track)
    widths = _column_widths(columns)
    for i, (tidal_text, ytm_text, fold, _) in enumerate(columns):
        if i:
            line.append(_FIELD_SEP, style=_Y_BASE)
        _append_ytm_field(line, tidal_text, ytm_text, fold=fold)
        pad = widths[i] - _cell_len(ytm_text)
        if pad:
            line.append(" " * pad, style=_Y_BASE)
    return line


def _album_head_line(row: ReviewRow) -> Text:
    """One album header line: artist — album, matching the search grouping."""
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append("  ")
    line.append(row.album_artist or "—", style="bold")
    line.append(" — ")
    line.append(row.album_name or row.album_match_id or "—", style="underline")
    line.append(f"  ({row.album_size} track{'s' if row.album_size != 1 else ''})", style="dim")
    return line


def review_lines(
    session: ReviewSession, rows: list[ReviewRow]
) -> tuple[list[Text], dict[int, int]]:
    """Flatten rows to console lines plus a track-index → first-line lookup."""
    lines: list[Text] = []
    first_line: dict[int, int] = {}
    for row in rows:
        if row.kind == "album":
            lines.append(_album_head_line(row))
            continue
        track = session.filtered_tracks[row.index]
        first_line[row.index] = len(lines)
        lines.append(_track_head_line(track, row.index == session.cursor))
        lines.append(_tidal_diff_line(track))
        lines.append(_ytm_diff_line(track))
    return lines, first_line


# ---------------------------------------------------------------------------
# Detail view: stacked Tidal-source / YTM-match panel for the focused track
# ---------------------------------------------------------------------------


def detail_body(track: dict[str, Any]) -> Text:
    """Stacked detail sections; mismatch fields render yellow with a warning."""
    body = Text()
    body.append("Tidal source\n", style="bold")
    for label, val in [
        ("Artist", track.get("artist", "—")),
        ("Title", track.get("title", "—")),
        ("Album", track.get("tidal_album", "—")),
        ("Duration", fmt_duration(track.get("tidal_duration_sec"))),
        ("ISRC", track.get("tidal_isrc") or "—"),
        ("Track #", str(track.get("tidal_track_num", "—"))),
    ]:
        body.append(f"  {label + ':':10} {val}\n")

    body.append("\nYTM match\n", style="bold")
    video_id = track.get("yt_video_id", "") or ""
    if not video_id:
        body.append("  — no match —\n", style="dim")
    else:
        yt_rows: list[tuple[str, Any, bool]] = [
            ("Artist", track.get("yt_artist", "—") or "—", False),
            ("Title", track.get("yt_title", "—") or "—", False),
            ("Album", track.get("yt_album", "") or "—", _album_mismatch(track)),
            ("Duration", fmt_duration(track.get("yt_duration_sec") or 0), _dur_mismatch(track)),
            ("ISRC", track.get("yt_isrc") or "—", False),
            ("Track #", str(track.get("yt_album_track_num") or "—"), False),
            ("Video ID", video_id, False),
            ("URL", f"https://music.youtube.com/watch?v={video_id}", False),
        ]
        for label, val, warn in yt_rows:
            body.append(f"  {label + ':':10} ")
            body.append(str(val), style="yellow" if warn else None)
            if warn:
                body.append(" ⚠", style="yellow")
            body.append("\n")

    conf: dict[str, Any] = track.get("confidence", {}) or {}
    match_method = track.get("match_method", "none")
    status = track.get("status", "")
    review_reason = track.get("review_reason", "")
    body.append("\n")
    body.append(f"  Match method:  {match_method}\n")
    body.append("  Confidence:    ")
    body.append_text(_confidence_text(conf.get("overall"), match_method))
    summary = conf.get("summary", "")
    if summary:
        body.append(f"  ←  {summary}")
    body.append("\n")
    body.append("  Status:        ")
    body.append(status, style=STATUS_STYLE.get(status, ""))
    if review_reason:
        body.append(f'  ←  "{review_reason}"', style="dim")
    body.append("\n")
    return body


# ---------------------------------------------------------------------------
# Frames: head, viewport with scrollbar, footer bar (for Live)
# ---------------------------------------------------------------------------


def _hot(pre: str, hot: str, post: str = "", style: str = "bold cyan") -> Text:
    """Colour-only hotkey: the action letters coloured inside their word."""
    part = Text()
    part.append(pre)
    part.append(hot, style=style)
    part.append(post)
    return part


def _hint(key: str, label: str, style: str = "bold cyan") -> Text:
    """A hotkey hint with the keys coloured inside the word, no brackets."""
    if " | " in key:
        head, _, rest = key.partition(" | ")
        part = Text()
        part.append_text(_hot("", head, "", style))
        part.append(" | ", style="dim")
        part.append_text(_hot("", rest, "", style))
        part.append(label)
        return part
    return _hot("", key, label, style)


def review_bar(mode: str) -> Text:
    """Footer on two lines: cyan review/album/artist actions, yellow general help."""
    cyan = "bold cyan"
    yellow = "bold yellow"
    decide = _hint("a", "ccept")
    decide.append(", ", style="dim")
    decide.append_text(_hint("r", "eject"))
    decide.append(", ", style="dim")
    decide.append_text(_hint("s", "kip"))
    decide.append(" or ", style="dim")
    decide.append_text(_hint("o", "verride"))
    decide.append(" match")
    top = Text()
    move = Text()
    for i, k in enumerate(("↑", "↓", "j", "k", "wheel")):
        if i:
            move.append(" | ", style="dim")
        move.append(k, style=cyan)
    move.append(" move")
    album = _hint("n", "ext / ")
    album.append_text(_hint("p", "revious album"))
    artist = _hint("N", "ext / ")
    artist.append_text(_hint("P", "revious artist"))
    top_parts = [
        move,
        album,
        artist,
        _hint("Enter | v", "iew details" if mode == "list" else "iew list"),
        decide,
        _hot("mark as ", "t", "ransferred", style=cyan),
    ]
    for i, part in enumerate(top_parts):
        if i:
            top.append("   ", style="dim")
        top.append_text(part)
    bottom = Text()
    bottom_parts = [
        _hint("? | h", "elp", style=yellow),
    ]
    if mode == "list":
        bottom_parts.append(_hint("Esc | b", "ack to main menu", style=yellow))
    bottom_parts.append(_hint("q", "uit app", style=yellow))
    for i, part in enumerate(bottom_parts):
        if i:
            bottom.append("   ", style="dim")
        bottom.append_text(part)
    bar = Text()
    bar.append_text(top)
    bar.append("\n")
    bar.append_text(bottom)
    return bar


def review_head(session: ReviewSession) -> Text:
    """Title bar: cyan label, dim position counts, yellow flash line."""
    head = Text(no_wrap=True, overflow="ellipsis")
    head.append("Review", style="bold cyan")
    n = len(session.filtered_tracks)
    head.append(f"  {n} track{'s' if n != 1 else ''}  {session.cursor + 1}/{n}")
    if session.mode == "detail":
        head.append("  detail", style="dim")
    if session.flash:
        head.append(f"\n{session.flash}", style="yellow")
    return head


def list_frame(session: ReviewSession, height: int) -> Group:
    """One constant-height list frame: head, padded viewport, footer. For Live."""
    rows = build_review_rows(session.filtered_tracks, session.track_context)
    lines, first_line = review_lines(session, rows)
    start, end = visible_window(lines, first_line.get(session.cursor, 0), height)
    window = lines[start:end]
    while len(window) < height:
        window.append(Text(""))
    thumb = scrollbar_thumb(len(lines), height, start)
    if thumb is not None:
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(width=1)
        for i, line in enumerate(window):
            grid.add_row(line, "█" if i == thumb else "│")
        body = grid
    else:
        body = Text(no_wrap=True, overflow="ellipsis")
        for i, line in enumerate(window):
            if i:
                body.append("\n")
            body.append_text(line)
    return Group(
        Panel(review_head(session), border_style="dim", expand=True),
        Panel(body, expand=True),
        Panel(review_bar(session.mode), border_style="dim", expand=True),
    )


def detail_frame(session: ReviewSession) -> Group:
    """Detail frame: head, the focused track panel, footer."""
    track = session.filtered_tracks[session.cursor]
    return Group(
        Panel(review_head(session), border_style="dim", expand=True),
        Panel(
            detail_body(track),
            title=review_title(track, session.track_context),
            expand=True,
        ),
        Panel(review_bar(session.mode), border_style="dim", expand=True),
    )


def review_frame(session: ReviewSession, height: int) -> Group:
    """Dispatch to the list or detail frame for the current mode."""
    if session.mode == "detail":
        return detail_frame(session)
    return list_frame(session, height)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _save(session: ReviewSession) -> None:
    if not session.backup_done:
        bpath = backup_plan(session.plan_path)
        print(f"Backup → {bpath.name}")
        session.backup_done = True
    update_plan_meta(session.plan)
    save_plan(session.plan, session.plan_path)


def _apply_decision(
    session: ReviewSession,
    track: dict[str, Any],
    new_status: str,
    extra: dict[str, Any] | None = None,
) -> None:
    updates: dict[str, Any] = {"status": new_status}
    if extra:
        updates.update(extra)
    update_track_in_plan(session.plan, track["tidal_id"], updates)
    track.update(updates)
    _save(session)


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------


def _album_key(ctx_entry: dict[str, Any]) -> Any:
    return ctx_entry.get("album_match_id")


def _artist_key(ctx_entry: dict[str, Any]) -> Any:
    return ctx_entry.get("artist_match_id")


def _step(
    session: ReviewSession,
    key_fn: Callable[[dict[str, Any]], Any],
    delta: int,
) -> int:
    """Return the cursor of the adjacent album/artist group in direction `delta`.

    `key_fn` selects the grouping field (``album_match_id`` /
    ``artist_match_id``) from a track-context entry. At either end of the
    list the cursor stays where it is.
    """
    if not session.filtered_tracks:
        return session.cursor
    entry = session.track_context.get(session.filtered_tracks[session.cursor]["tidal_id"], {})
    current = key_fn(entry)
    if delta > 0:
        for i in range(session.cursor + 1, len(session.filtered_tracks)):
            entry = session.track_context.get(session.filtered_tracks[i]["tidal_id"], {})
            if key_fn(entry) != current:
                return i
        return session.cursor  # already at last group

    # delta < 0: first track of the current group, then first of the previous
    first_of_cur = session.cursor
    for i in range(session.cursor - 1, -1, -1):
        entry = session.track_context.get(session.filtered_tracks[i]["tidal_id"], {})
        if key_fn(entry) == current:
            first_of_cur = i
        else:
            break
    if first_of_cur == 0:
        return 0
    prev = key_fn(
        session.track_context.get(session.filtered_tracks[first_of_cur - 1]["tidal_id"], {})
    )
    for i in range(first_of_cur - 1, -1, -1):
        entry = session.track_context.get(session.filtered_tracks[i]["tidal_id"], {})
        if key_fn(entry) != prev:
            return i + 1
    return 0


# ---------------------------------------------------------------------------
# Key routing
# ---------------------------------------------------------------------------

# ANSI page-up/page-down sequences (readchar key constants equal these, but the
# literals keep review working when readchar is absent).
_PGUP = "\x1b[5~"
_PGDN = "\x1b[6~"

# Cursor lines moved per mouse-wheel notch (one whole track row).
_WHEEL_STEP = 1

# Navigation table: key token → action. View switching (v/Enter/Esc), wheel,
# and decisions are handled in KeyRouter.dispatch so both views share them.
NAV_ACTIONS: dict[str, str] = {
    "k": "next",
    "]": "next",
    "j": "prev",
    "[": "prev",
    "n": "next_album",
    "p": "prev_album",
    "\t": "next_album",
    "\x1b[Z": "prev_album",  # Shift+Tab escape sequence (POSIX terminals)
    "N": "next_artist",
    "P": "prev_artist",
    _PGUP: "page_up",
    _PGDN: "page_down",
}
if readchar_key is not None:
    NAV_ACTIONS[readchar_key.DOWN] = "next"
    NAV_ACTIONS[readchar_key.UP] = "prev"
    NAV_ACTIONS[readchar_key.RIGHT] = "next_album"
    NAV_ACTIONS[readchar_key.LEFT] = "prev_album"
    NAV_ACTIONS[readchar_key.TAB] = "next_album"

# Decision table: key → (persisted status, confirmation flash). Plain text —
# flashes render as Text, never through markup, so user input cannot break them.
DECISIONS: dict[str, tuple[str, str]] = {
    "a": (TrackStatus.PENDING.value, "✓ Accepted (pending)"),
    "s": (TrackStatus.SKIP.value, "— Skipped"),
    "r": (TrackStatus.NEEDS_REVIEW.value, "✗ Rejected (needs_review)"),
    "t": (TrackStatus.TRANSFERRED.value, "✓ Marked as transferred"),
}


@dataclass
class KeyRouter:
    """Single-dispatch router mapping keypresses to navigation and decisions.

    Both views share one navigation table and one decision table; the mode
    only changes what v/Enter/Esc do. `live` is paused around line prompts
    (override/jump/help) and left unset (None) in tests.
    """

    session: ReviewSession
    console: Console
    live: Live | None = None

    @contextlib.contextmanager
    def _paused_live(self) -> Generator[None, None, None]:
        if self.live is None:
            yield
            return
        self.live.stop()
        try:
            yield
        finally:
            self.live.start(refresh=True)

    def dispatch(self, key: str) -> bool:
        """Handle one keypress; return False when the session should end."""
        self.session.flash = ""
        view_result = self._dispatch_view(key)
        if view_result is not None:
            return view_result
        action = NAV_ACTIONS.get(key)
        if action is not None:
            self._navigate(action)
            return True
        return self._dispatch_action(key)

    def _confirm_quit(self) -> bool:
        """Ask before quitting the whole app; False keeps the review open."""
        with self._paused_live():
            try:
                answer = read_line("Quit tidal2ytm? [y/N] ").strip().lower()
            except (KeyboardInterrupt, EOFError, OSError):
                return False
        if answer in ("y", "yes"):
            self.session.quit_app = True
            return False
        self.session.flash = "Still in review — q again to quit the app."
        return True

    def _dispatch_view(self, key: str) -> bool | None:
        """View-level keys (quit/resize/wheel/mode toggle); None when unhandled."""
        if key in ("q", CTRL_C):
            return self._confirm_quit()
        if key == "":
            return True
        if key == RESIZE_KEY:
            clear_screen()
            return True
        if key.startswith("\x1b["):
            # SGR mouse reports move the cursor; clicks and stray sequences
            # are swallowed silently so they never flash unknown-key noise.
            wheel = parse_sgr_mouse(key)
            if wheel == WHEEL_UP:
                self._move_tracks(-_WHEEL_STEP)
            elif wheel == WHEEL_DOWN:
                self._move_tracks(_WHEEL_STEP)
            return True
        if key in ("v", "\r", "\n"):
            self.session.mode = "detail" if self.session.mode == "list" else "list"
            return True
        if key in ("\x1b", "b"):
            # Detail → list; from the list, back out to the main menu.
            if self.session.mode == "detail":
                self.session.mode = "list"
                return True
            return False
        return None

    def _dispatch_action(self, key: str) -> bool:
        """Jump/override/decision/help keys shared by both views."""
        if key == "g":
            self._prompt_jump()
            return True
        if key.startswith("g "):
            self._jump_to(key[2:].strip())
            return True
        if key == "o":
            track = self.session.filtered_tracks[self.session.cursor]
            with self._paused_live():
                _do_override(self.console, self.session, track)
            self._advance()
            return True
        if self.dispatch_decision(key) is not None:
            return True
        if key in ("?", "h"):
            with self._paused_live():
                _show_help(self.console)
            return True
        self.session.flash = f"Unknown key: {key!r}  (press ? for help)"
        return True

    def dispatch_decision(self, key: str) -> str | None:
        """Apply the track decision bound to `key`; return the status or None."""
        entry = DECISIONS.get(key)
        if entry is None:
            return None
        status, message = entry
        track = self.session.filtered_tracks[self.session.cursor]
        was_isrc = (
            key == "r"
            and track.get("match_method") == "isrc"
            and track.get("confidence", {}).get("overall", 0.0) == 1.0
        )
        _apply_decision(self.session, track, status)
        self.session.flash = message
        if was_isrc:
            self.session.flash += " — this was an ISRC match; set back to 'pending' if accidental."
        self._advance()
        return status

    def _page_step(self) -> int:
        """Tracks per PageUp/PageDown: a third of the viewport, at least one."""
        return max(1, (self.session.view_h or 9) // 3)

    def _move_tracks(self, delta: int) -> None:
        """Move the cursor by `delta` tracks, flashing at either end."""
        session = self.session
        if not session.filtered_tracks:
            return
        if delta < 0 and session.cursor == 0:
            session.flash = "(Beginning of list)"
        elif delta > 0 and session.cursor == len(session.filtered_tracks) - 1:
            session.flash = "(End of list)"
        else:
            session.cursor = max(0, min(len(session.filtered_tracks) - 1, session.cursor + delta))

    def _advance(self) -> None:
        if self.session.cursor < len(self.session.filtered_tracks) - 1:
            self.session.cursor += 1

    def _navigate(self, action: str) -> None:
        session = self.session
        if action == "next":
            self._move_tracks(1)
        elif action == "prev":
            self._move_tracks(-1)
        elif action == "page_up":
            self._move_tracks(-self._page_step())
        elif action == "page_down":
            self._move_tracks(self._page_step())
        elif action == "next_album":
            session.cursor = _step(session, _album_key, 1)
        elif action == "prev_album":
            session.cursor = _step(session, _album_key, -1)
        elif action == "next_artist":
            session.cursor = _step(session, _artist_key, 1)
        elif action == "prev_artist":
            session.cursor = _step(session, _artist_key, -1)

    def _jump_to(self, target: str) -> None:
        for i, t in enumerate(self.session.filtered_tracks):
            ctx2 = self.session.track_context.get(t["tidal_id"], {})
            if (
                t.get("yt_video_id") == target
                or ctx2.get("album_match_id") == target
                or ctx2.get("artist_match_id") == target
            ):
                self.session.cursor = i
                return
        self.session.flash = f"Not found: {target}"

    def _prompt_jump(self) -> None:
        # Prompt for jump target: allows `g <id>` without needing to type
        # the space in raw mode
        try:
            self.console.print(
                "[dim]Jump to (album match_id / artist match_id / "
                "video ID, empty to cancel):[/dim] ",
                end="",
            )
            target = read_line().strip()
        except (KeyboardInterrupt, EOFError):
            return
        if target:
            self._jump_to(target)


# ---------------------------------------------------------------------------
# Override helper
# ---------------------------------------------------------------------------


def _do_override(console: Console, session: ReviewSession, track: dict[str, Any]) -> None:
    while True:
        try:
            raw = read_line("Enter YouTube video ID or URL: ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        try:
            vid = extract_video_id(raw)
        except ValueError:
            console.print("[red]✗ Could not parse a YouTube video ID from that input.[/red]")
            continue
        _apply_decision(
            session,
            track,
            TrackStatus.PENDING.value,
            {
                "yt_video_id": vid,
                "match_method": "none",
                "confidence": {"overall": 0.0},
                "review_reason": "",
            },
        )
        console.print(f"[green]✓[/green] Override set: {vid}")
        break


def _show_help(console: Console) -> None:
    console.print(Panel(Text.from_markup(HELP_TEXT), title="Help"))
    with contextlib.suppress(KeyboardInterrupt, EOFError):
        read_line("Press Enter to continue…")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_review(
    *,
    status_filter: TrackStatus | None = None,
    artist_match_id: str | None = None,
    album_match_id: str | None = None,
    plan_path: Path = PLAN_FILE,
) -> bool:
    """Run the review TUI; True when the user quit the whole app."""
    console = Console()

    if not plan_path.exists():
        console.print(
            "[red]Error:[/red] No transfer plan found. "
            "Run [bold]tidal2ytm[/bold] to build one first."
        )
        raise PlanNotFoundError("Error: No transfer plan found. Run tidal2ytm to build one first.")

    plan: dict[str, Any] = load_plan(plan_path)
    filtered: list[dict[str, Any]] = list(
        iter_tracks_filtered(
            plan,
            status=status_filter,
            artist_match_id=artist_match_id,
            album_match_id=album_match_id,
        )
    )
    if not filtered:
        console.print("No tracks match the current filters.")
        return False

    session = ReviewSession(
        plan=plan,
        plan_path=plan_path,
        backup_done=False,
        cursor=0,
        filtered_tracks=filtered,
        track_context=_build_track_context(plan, filtered),
    )
    console.print(
        f"[bold]Reviewing {len(filtered)} tracks.[/bold]  Press [bold]?[/bold] for help.\n"
    )
    _run_loop(console, session)
    return session.quit_app


# ---------------------------------------------------------------------------
# Input + Live loop (mirrors the planning picker framework)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _review_mouse() -> Generator[None, None, None]:
    """Mouse-wheel input scope: SGR reports for posix TTYs, raw reads on both.

    Windows gets console mouse flags via `windows_mouse`; posix additionally
    needs SGR 1000/1006 reports plus raw reads so the loop sees wheel events.
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


def _review_readkey() -> str:
    """One review keypress: raw reads on a TTY, a stripped input line otherwise."""
    if sys.stdin.isatty():
        if os.name == "nt":
            return read_windows_console_key()
        return read_ansi_key()
    return read_line().strip()


def _bar_height(console: Console, mode: str) -> int:
    """Footer bar height at the current width, measured in-panel (borders narrow it)."""
    panel = Panel(review_bar(mode), border_style="dim", expand=True)
    return len(console.render_lines(panel, console.options, pad=False)) - 2


def _viewport_height(term_height: int, footer_lines: int) -> int:
    """Body rows that fit: terminal minus head/body/footer panels and margin."""
    return max(4, term_height - 8 - footer_lines)


def _run_loop(console: Console, session: ReviewSession) -> None:
    router = KeyRouter(console=console, session=session)
    with _review_mouse():
        live = Live(
            review_frame(session, _viewport_height(console.size.height or 24, 1)),
            console=console,
            auto_refresh=False,
            transient=False,
        )
        router.live = live
        live.start()
        try:
            while True:
                session.view_h = _viewport_height(
                    console.size.height or 24,
                    _bar_height(console, session.mode),
                )
                live.update(review_frame(session, session.view_h), refresh=True)
                try:
                    key = _review_readkey()
                except (KeyboardInterrupt, EOFError):
                    break
                if not router.dispatch(key):
                    break
        finally:
            live.stop()

    console.print("\nReview session ended.")
