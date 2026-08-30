"""
review.py — Interactive rich TUI navigator for the tidal2ytm transfer plan.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
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

from .models import TrackStatus  # noqa: E402
from .paths import PLAN_FILE  # noqa: E402
from .plan_io import (  # noqa: E402
    backup_plan,
    iter_tracks_filtered,
    load_plan,
    save_plan,
    update_plan_meta,
    update_track_in_plan,
)

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

DURATION_TOLERANCE_SEC = 4


def _confidence_color(value: float) -> str:
    if value == 1.0:
        return "blue"
    elif value > 0.85:
        return "green"
    elif value >= 0.70:
        return "yellow"
    else:
        return "red"


def _confidence_text(value: float | None) -> Text:
    if value is None:
        return Text("—")
    t = Text(f"{value:.2f}", style=_confidence_color(value))
    if value == 1.0:
        t.append(" ✓")
    return t


def _status_style(status: str) -> str:
    return {
        TrackStatus.NEEDS_REVIEW.value: "on yellow",
        TrackStatus.TRANSFERRED.value: "on green",
        TrackStatus.SKIP.value: "dim",
        TrackStatus.FAILED.value: "on red",
        TrackStatus.PENDING.value: "",
    }.get(status, "")


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
    ctx: dict[int, dict[str, Any]] = {}
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
    from collections import defaultdict

    album_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in filtered:
        _, alb_id, _ = tidal_id_to_album.get(t["tidal_id"], ("", "", ""))
        album_buckets[alb_id].append(t)

    for _i, t in enumerate(filtered):
        ar_id, alb_id, alb_name = tidal_id_to_album.get(t["tidal_id"], ("", "", ""))
        bucket = album_buckets[alb_id]
        pos = bucket.index(t) + 1
        total = len(bucket)
        ctx[t["tidal_id"]] = {
            "artist_match_id": ar_id,
            "album_match_id": alb_id,
            "album_name": alb_name,
            "pos_in_album": pos,
            "total_in_album": total,
        }
    return ctx


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

HELP_TEXT = """\
[bold]Navigation[/bold]
  k / ] / ↓ / Enter         Next track
  j / [ / ↑                 Previous track
  n / → / Tab               Next album
  p / ← / Shift+Tab         Previous album
  N                         Next artist
  P                         Previous artist
  g <id>                    Jump by album match_id, artist match_id, or video ID

[bold]Track decisions[/bold]
  a   Accept match          → pending
  s   Skip track            → skip
  r   Reject match          → needs_review
  o   Override video ID     → prompts, then pending on success
  t   Mark as transferred   → transferred

[bold]Other[/bold]
  ? / h   Show this help
  q       Quit

All decisions are written immediately — there is no unsaved state.
"""


def _render_track(
    console: Console,
    track: dict[str, Any],
    ctx: dict[int, dict[str, Any]],
    session: ReviewSession,
) -> None:
    tidal_id = track.get("tidal_id", 0)
    info = ctx.get(tidal_id, {})
    alb_match_id = info.get("album_match_id", "")
    pos = info.get("pos_in_album", "?")
    total = info.get("total_in_album", "?")

    title_text = f"[bold]{alb_match_id}[/bold]  Track {pos} of {total}"

    # Left column: Source
    src_lines = [
        ("Artist", track.get("artist", "—")),
        ("Title", track.get("title", "—")),
        ("Album", track.get("tidal_album", "—")),
        ("Duration", _fmt_duration(track.get("tidal_duration_sec"))),
        ("ISRC", track.get("tidal_isrc") or "—"),
        ("Track #", str(track.get("tidal_track_num", "—"))),
    ]

    # Right column: YTM
    yt_album = track.get("yt_album", "") or ""
    tidal_album = track.get("tidal_album", "") or ""
    yt_dur = track.get("yt_duration_sec") or 0
    tidal_dur = track.get("tidal_duration_sec") or 0
    dur_delta = abs(yt_dur - tidal_dur) if yt_dur and tidal_dur else 999

    album_mismatch = yt_album and tidal_album and yt_album.casefold() != tidal_album.casefold()
    dur_mismatch = dur_delta > DURATION_TOLERANCE_SEC

    video_id = track.get("yt_video_id", "") or ""
    yt_url = f"https://music.youtube.com/watch?v={video_id}" if video_id else "—"

    yt_album_display = yt_album or "—"
    yt_dur_display = _fmt_duration(yt_dur)
    yt_lines: list[tuple[str, str, bool]] = [
        ("Artist", track.get("yt_artist", "—") or "—", False),
        ("Title", track.get("yt_title", "—") or "—", False),
        ("Album", yt_album_display, album_mismatch),  # pyright: ignore[reportAssignmentType]
        ("Duration", yt_dur_display, dur_mismatch),  # pyright: ignore[reportAssignmentType]
        ("ISRC", track.get("yt_isrc") or "—", False),
        ("Track #", str(track.get("yt_album_track_num") or "—"), False),
        ("Video ID", video_id or "—", False),
        ("URL", yt_url, False),
    ]

    # Confidence
    conf = track.get("confidence", {})
    overall = conf.get("overall", 0.0)
    summary = conf.get("summary", "")
    status = track.get("status", "")
    review_reason = track.get("review_reason", "")

    match_method = track.get("match_method", "none")
    status_style = _status_style(status)

    # Build body
    body = Text()
    body.append("  Source (Tidal)".ljust(42), style="bold")
    body.append("YTM Match\n", style="bold")
    body.append("  " + "─" * 38 + "  " + "─" * 38 + "\n", style="dim")

    max_rows = max(len(src_lines), len(yt_lines))
    for i in range(max_rows):
        if i < len(src_lines):
            label, val = src_lines[i]
            body.append(f"  {label + ':':10} {val}")
            body.append(" " * max(0, 28 - len(val)))
        else:
            body.append(" " * 40)
        if i < len(yt_lines):
            label, val, warn = yt_lines[i]
            body.append(f"{label + ':':10} {val}")
            if warn:
                body.append(" ⚠", style="yellow")
        body.append("\n")

    body.append("\n")
    body.append(f"  Match method:  {match_method}\n")

    body.append("  Confidence:    ")
    body.append_text(_confidence_text(overall))
    if summary:
        body.append(f"  ←  {summary}")
    body.append("\n")

    body.append("  Status:        ")
    body.append(status, style=status_style)
    if review_reason:
        body.append(f'  ←  "{review_reason}"', style="dim")
    body.append("\n")

    body.append("\n")
    body.append(
        "  [a] Accept  [s] Skip  [r] Reject  [o] Override  [t] Mark transferred\n"
        "  [k/j] Next/Prev track  [n/p] Next/Prev album  [N/P] Next/Prev artist\n"
        "  [?/h] Help  [q] Quit",
        style="dim",
    )

    console.print(Panel(body, title=title_text, expand=True))


def _fmt_duration(sec: int | None) -> str:
    if not sec:
        return "—"
    m, s = divmod(sec, 60)
    return f"{m}:{s:02d}"


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


def _current_album_id(session: ReviewSession) -> str:
    if not session.filtered_tracks:
        return ""
    track = session.filtered_tracks[session.cursor]
    return session.track_context.get(track["tidal_id"], {}).get("album_match_id", "")


def _current_artist_id(session: ReviewSession) -> str:
    if not session.filtered_tracks:
        return ""
    track = session.filtered_tracks[session.cursor]
    return session.track_context.get(track["tidal_id"], {}).get("artist_match_id", "")


def _next_album_cursor(session: ReviewSession) -> int:
    cur_album = _current_album_id(session)
    for i in range(session.cursor + 1, len(session.filtered_tracks)):
        if (
            session.track_context.get(session.filtered_tracks[i]["tidal_id"], {}).get(
                "album_match_id"
            )
            != cur_album
        ):
            return i
    return session.cursor  # already at last album


def _prev_album_cursor(session: ReviewSession) -> int:
    cur_album = _current_album_id(session)
    # Find first track of current album
    first_of_cur = session.cursor
    for i in range(session.cursor - 1, -1, -1):
        if (
            session.track_context.get(session.filtered_tracks[i]["tidal_id"], {}).get(
                "album_match_id"
            )
            == cur_album
        ):
            first_of_cur = i
        else:
            break
    if first_of_cur == 0:
        return 0
    # Go to first track of previous album
    prev_album = session.track_context.get(
        session.filtered_tracks[first_of_cur - 1]["tidal_id"], {}
    ).get("album_match_id")
    for i in range(first_of_cur - 1, -1, -1):
        if (
            session.track_context.get(session.filtered_tracks[i]["tidal_id"], {}).get(
                "album_match_id"
            )
            != prev_album
        ):
            return i + 1
    return 0


def _next_artist_cursor(session: ReviewSession) -> int:
    cur_artist = _current_artist_id(session)
    for i in range(session.cursor + 1, len(session.filtered_tracks)):
        if (
            session.track_context.get(session.filtered_tracks[i]["tidal_id"], {}).get(
                "artist_match_id"
            )
            != cur_artist
        ):
            return i
    return session.cursor


def _prev_artist_cursor(session: ReviewSession) -> int:
    cur_artist = _current_artist_id(session)
    first_of_cur = session.cursor
    for i in range(session.cursor - 1, -1, -1):
        if (
            session.track_context.get(session.filtered_tracks[i]["tidal_id"], {}).get(
                "artist_match_id"
            )
            == cur_artist
        ):
            first_of_cur = i
        else:
            break
    if first_of_cur == 0:
        return 0
    prev_artist = session.track_context.get(
        session.filtered_tracks[first_of_cur - 1]["tidal_id"], {}
    ).get("artist_match_id")
    for i in range(first_of_cur - 1, -1, -1):
        if (
            session.track_context.get(session.filtered_tracks[i]["tidal_id"], {}).get(
                "artist_match_id"
            )
            != prev_artist
        ):
            return i + 1
    return 0


# ---------------------------------------------------------------------------
# Override helper
# ---------------------------------------------------------------------------


def _do_override(console: Console, session: ReviewSession, track: dict[str, Any]) -> None:
    while True:
        raw = input("Enter YouTube video ID or URL: ").strip()
        try:
            from .plan_io import _extract_video_id as _ev  # pyright: ignore[reportPrivateUsage]

            vid = _ev(raw)
        except ValueError:
            console.print("[red]✗ Could not parse a YouTube video ID from that input.[/red]")
            continue
        _apply_decision(session, track, TrackStatus.PENDING.value, {"yt_video_id": vid})
        console.print(f"[green]✓[/green] Override set: {vid}")
        break


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_review(  # noqa: C901
    *,
    status_filter: TrackStatus | None = None,
    artist_match_id: str | None = None,
    album_match_id: str | None = None,
    plan_path: Path = PLAN_FILE,
) -> None:
    console = Console()

    if not plan_path.exists():
        console.print(
            "[red]Error:[/red] No transfer plan found. Run [bold]tidal2ytm plan[/bold] first."
        )
        sys.exit(1)

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
        return

    track_context: dict[int, dict[str, Any]] = _build_track_context(plan, filtered)

    session = ReviewSession(
        plan=plan,
        plan_path=plan_path,
        backup_done=False,
        cursor=0,
        filtered_tracks=filtered,
        track_context=track_context,
    )

    console.print(
        f"[bold]Reviewing {len(filtered)} tracks.[/bold]  Press [bold]?[/bold] for help.\n"
    )

    # Helpers for jump target search
    def _jump_to(target: str) -> None:
        found = False
        for i, t in enumerate(session.filtered_tracks):
            ctx2 = session.track_context.get(t["tidal_id"], {})
            if (
                t.get("yt_video_id") == target
                or ctx2.get("album_match_id") == target
                or ctx2.get("artist_match_id") == target
            ):
                session.cursor = i
                found = True
                break
        if not found:
            console.print(f"[yellow]Not found:[/yellow] {target}")

    # Detect raw key mode (first-class controls)
    use_readchar = HAS_READCHAR and sys.stdin.isatty()

    while True:
        track = session.filtered_tracks[session.cursor]

        console.clear()
        _render_track(console, track, session.track_context, session)

        try:
            if use_readchar:
                key = readchar.readkey()  # type: ignore[attr-defined]
                # Ctrl+C
                if key == readchar_key.CTRL_C:  # type: ignore[attr-defined]
                    break
            else:
                key = input().strip()
        except (KeyboardInterrupt, EOFError):
            break

        # In readchar mode, Enter is \r (and \n on POSIX) — normalize to ""
        # and handle Shift+Tab explicitly
        if use_readchar:
            # Navigation — first-class single-press controls
            if key in (
                "k",
                "]",
                readchar_key.DOWN,  # pyright: ignore[reportOptionalMemberAccess]
                readchar_key.ENTER,  # pyright: ignore[reportOptionalMemberAccess]
                readchar_key.CR,  # pyright: ignore[reportOptionalMemberAccess]
                readchar_key.LF,  # pyright: ignore[reportOptionalMemberAccess]
                "\r",
                "\n",
            ):  # type: ignore[attr-defined]
                if session.cursor < len(session.filtered_tracks) - 1:
                    session.cursor += 1
                else:
                    console.print("(End of list)", style="dim")
                    # Brief pause so message is visible before next clear
                    import time as _time

                    _time.sleep(0.6)
                continue
            elif key in ("j", "[", readchar_key.UP):  # type: ignore[attr-defined]
                if session.cursor > 0:
                    session.cursor -= 1
                else:
                    console.print("(Beginning of list)", style="dim")
                    import time as _time

                    _time.sleep(0.6)
                continue
            elif key in ("n", readchar_key.RIGHT, readchar_key.TAB, "\t"):  # type: ignore[attr-defined]
                session.cursor = _next_album_cursor(session)
                continue
            elif key in ("p", readchar_key.LEFT) or key == "\x1b[Z":  # type: ignore[attr-defined]  # Shift+Tab is ESC[Z on POSIX
                session.cursor = _prev_album_cursor(session)
                continue
            elif key == "N":
                session.cursor = _next_artist_cursor(session)
                continue
            elif key == "P":
                session.cursor = _prev_artist_cursor(session)
                continue
            elif key == "g":
                # Prompt for jump target: allows `g <id>` without needing
                # to type the space in raw mode
                try:
                    console.print(
                        "[dim]Jump to (album match_id / artist match_id / "
                        "video ID, empty to cancel):[/dim] ",
                        end="",
                    )
                    target = input().strip()
                except (KeyboardInterrupt, EOFError):
                    continue
                if not target:
                    continue
                _jump_to(target)
                continue
            # Decisions and Help/Quit fall through to shared handlers below
            # using `key` (readchar mode already handled navigation;
            # non-navigation keys continue)
        else:
            # Fallback line-buffered mode — supports `g <id>` typed on one line
            if key in ("k", "]", ""):
                if session.cursor < len(session.filtered_tracks) - 1:
                    session.cursor += 1
                else:
                    console.print("(End of list)", style="dim")
                continue
            elif key in ("j", "["):
                if session.cursor > 0:
                    session.cursor -= 1
                else:
                    console.print("(Beginning of list)", style="dim")
                continue
            elif key in ("n", "\t"):
                session.cursor = _next_album_cursor(session)
                continue
            elif key in ("p",):
                session.cursor = _prev_album_cursor(session)
                continue
            elif key == "N":
                session.cursor = _next_artist_cursor(session)
                continue
            elif key == "P":
                session.cursor = _prev_artist_cursor(session)
                continue
            elif key.startswith("g "):
                target = key[2:].strip()
                _jump_to(target)
                continue
            elif key == "g":
                # Bare `g` in fallback mode — prompt as in readchar mode
                try:
                    console.print(
                        "[dim]Jump to (album match_id / artist match_id / video ID):[/dim] ", end=""
                    )
                    target = input().strip()
                except (KeyboardInterrupt, EOFError):
                    continue
                if not target:
                    continue
                _jump_to(target)
                continue

        # Decisions (shared for both input modes)
        if key == "a":
            _apply_decision(session, track, TrackStatus.PENDING.value)
            console.print("[green]✓ Accepted (pending)[/green]")
            if session.cursor < len(session.filtered_tracks) - 1:
                session.cursor += 1
        elif key == "s":
            _apply_decision(session, track, TrackStatus.SKIP.value)
            console.print("[dim]— Skipped[/dim]")
            if session.cursor < len(session.filtered_tracks) - 1:
                session.cursor += 1
        elif key == "r":
            was_isrc = (
                track.get("match_method") == "isrc"
                and track.get("confidence", {}).get("overall", 0.0) == 1.0
            )
            _apply_decision(session, track, TrackStatus.NEEDS_REVIEW.value)
            console.print("[yellow]✗ Rejected (needs_review)[/yellow]")
            if was_isrc:
                console.print(
                    "  [dim]Note: this was an ISRC match (confidence 1.0). "
                    "Set status back to 'pending' if this was accidental.[/dim]"
                )
            if session.cursor < len(session.filtered_tracks) - 1:
                session.cursor += 1
        elif key == "o":
            _do_override(console, session, track)
            if session.cursor < len(session.filtered_tracks) - 1:
                session.cursor += 1
        elif key == "t":
            _apply_decision(session, track, TrackStatus.TRANSFERRED.value)
            console.print("[green]✓ Marked as transferred[/green]")
            if session.cursor < len(session.filtered_tracks) - 1:
                session.cursor += 1

        # Help / quit
        elif key in ("?", "h"):
            console.print(Panel(Text.from_markup(HELP_TEXT), title="Help"))
            input("Press Enter to continue…")
        elif key == "q":
            break
        else:
            console.print(f"[dim]Unknown key: {key!r}  (press ? for help)[/dim]")

    console.print("\nReview session ended.")
