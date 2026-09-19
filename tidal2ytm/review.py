"""
review.py — Interactive rich TUI navigator for the tidal2ytm transfer plan.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .confidence import color_for
from .errors import PlanNotFoundError
from .format import fmt_duration
from .keys import CTRL_C, read_key, read_line, readchar_key
from .matcher import DURATION_TOLERANCE_SEC
from .models import TrackStatus
from .paths import PLAN_FILE
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

NAV_PAUSE_SEC = 0.6

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


def _confidence_text(value: float | None) -> Text:
    if value is None:
        return Text("—")
    t = Text(f"{value:.2f}", style=color_for(value))
    if value == 1.0:
        t.append(" ✓")
    return t


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


def _render_track(
    console: Console,
    track: dict[str, Any],
    ctx: dict[int, dict[str, Any]],
    session: ReviewSession,
) -> None:
    title_text = review_title(track, ctx)

    # Left column: Source
    src_lines = [
        ("Artist", track.get("artist", "—")),
        ("Title", track.get("title", "—")),
        ("Album", track.get("tidal_album", "—")),
        ("Duration", fmt_duration(track.get("tidal_duration_sec"))),
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
    dur_mismatch = bool(yt_dur and tidal_dur and dur_delta > DURATION_TOLERANCE_SEC)

    video_id = track.get("yt_video_id", "") or ""
    yt_url = f"https://music.youtube.com/watch?v={video_id}" if video_id else "—"

    yt_album_display = yt_album or "—"
    yt_dur_display = fmt_duration(yt_dur)
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
    status_style = STATUS_STYLE.get(status, "")

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

# Navigation table: key token → action. readchar key constants are added when
# the package is available so both raw and line-buffered modes share one table.
NAV_ACTIONS: dict[str, str] = {
    "": "next",
    "k": "next",
    "]": "next",
    "\r": "next",
    "\n": "next",
    "j": "prev",
    "[": "prev",
    "n": "next_album",
    "p": "prev_album",
    "\t": "next_album",
    "\x1b[Z": "prev_album",  # Shift+Tab escape sequence (POSIX terminals)
    "N": "next_artist",
    "P": "prev_artist",
}
if readchar_key is not None:
    NAV_ACTIONS[readchar_key.DOWN] = "next"
    NAV_ACTIONS[readchar_key.UP] = "prev"
    NAV_ACTIONS[readchar_key.RIGHT] = "next_album"
    NAV_ACTIONS[readchar_key.LEFT] = "prev_album"
    NAV_ACTIONS[readchar_key.ENTER] = "next"
    NAV_ACTIONS[readchar_key.CR] = "next"
    NAV_ACTIONS[readchar_key.LF] = "next"
    NAV_ACTIONS[readchar_key.TAB] = "next_album"

# Decision table: key → (persisted status, confirmation message)
DECISIONS: dict[str, tuple[str, str]] = {
    "a": (TrackStatus.PENDING.value, "[green]✓ Accepted (pending)[/green]"),
    "s": (TrackStatus.SKIP.value, "[dim]— Skipped[/dim]"),
    "r": (TrackStatus.NEEDS_REVIEW.value, "[yellow]✗ Rejected (needs_review)[/yellow]"),
    "t": (TrackStatus.TRANSFERRED.value, "[green]✓ Marked as transferred[/green]"),
}


def _edge_message(console: Console, message: str) -> None:
    """Show an end-of-list notice, pausing briefly so it stays visible."""
    console.print(message, style="dim")
    time.sleep(NAV_PAUSE_SEC)


@dataclass
class KeyRouter:
    """Single-dispatch router mapping keypresses to navigation and decisions."""

    session: ReviewSession
    console: Console

    def dispatch(self, key: str) -> bool:
        """Handle one keypress; return False when the session should end."""
        action = NAV_ACTIONS.get(key)
        if action is not None:
            self._navigate(action)
            return True
        if key == "g":
            self._prompt_jump()
            return True
        if key.startswith("g "):
            self._jump_to(key[2:].strip())
            return True
        if key == "o":
            track = self.session.filtered_tracks[self.session.cursor]
            _do_override(self.console, self.session, track)
            self._advance()
            return True
        if self.dispatch_decision(key) is not None:
            return True
        if key in ("?", "h"):
            _show_help(self.console)
            return True
        if key == "q":
            return False
        self.console.print(f"[dim]Unknown key: {key!r}  (press ? for help)[/dim]")
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
        self.console.print(message)
        if was_isrc:
            self.console.print(
                "  [dim]Note: this was an ISRC match (confidence 1.0). "
                "Set status back to 'pending' if this was accidental.[/dim]"
            )
        self._advance()
        return status

    def _advance(self) -> None:
        if self.session.cursor < len(self.session.filtered_tracks) - 1:
            self.session.cursor += 1

    def _navigate(self, action: str) -> None:
        session = self.session
        if action == "next":
            if session.cursor < len(session.filtered_tracks) - 1:
                session.cursor += 1
            else:
                _edge_message(self.console, "(End of list)")
        elif action == "prev":
            if session.cursor > 0:
                session.cursor -= 1
            else:
                _edge_message(self.console, "(Beginning of list)")
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
        self.console.print(f"[yellow]Not found:[/yellow] {target}")

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
) -> None:
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
        return

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


def _run_loop(console: Console, session: ReviewSession) -> None:
    router = KeyRouter(console=console, session=session)
    while True:
        track = session.filtered_tracks[session.cursor]

        console.clear()
        _render_track(console, track, session.track_context, session)

        try:
            key = read_key()
        except (KeyboardInterrupt, EOFError):
            break
        if key == CTRL_C:
            break
        if not router.dispatch(key):
            break

    console.print("\nReview session ended.")
