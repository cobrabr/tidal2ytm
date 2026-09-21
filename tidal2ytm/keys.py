"""
keys.py — shared terminal key and line reading for the interactive TUIs.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Generator
from typing import Any, cast

try:
    import readchar
    from readchar import key as readchar_key

    _has_readchar = True
except ImportError:  # pragma: no cover
    readchar = None  # type: ignore[assignment]
    readchar_key = None  # type: ignore[assignment]
    _has_readchar = False

HAS_READCHAR = _has_readchar

__all__ = [
    "CTRL_C",
    "HAS_READCHAR",
    "RESIZE_KEY",
    "WHEEL_DOWN",
    "WHEEL_UP",
    "classify_windows_event",
    "clear_screen",
    "drain_escape",
    "drain_tail",
    "esc_has_tail",
    "kernel32",
    "map_windows_key",
    "parse_sgr_mouse",
    "posix_raw",
    "read_ansi_key",
    "read_key",
    "read_line",
    "read_windows_console_key",
    "readchar_key",
    "windows_mouse",
]

# Ctrl+C as reported by readchar in raw mode
CTRL_C = "\x03"

RESIZE_KEY = "\x00R"

# Mouse-wheel sentinels: "\x00" prefix keeps them disjoint from printable keys,
# matching the RESIZE_KEY convention.
WHEEL_UP = "\x00WU"
WHEEL_DOWN = "\x00WD"

# Windows msvcrt prefixes an extended key (arrows, function keys) with one of
# these bytes; arrow sequences are translated to the readchar escape forms so
# the navigation tables stay mode-independent.
_WIN_EXTENDED_PREFIXES = ("\x00", "\xe0")
_WIN_ARROW_TRANSLATION = {
    "\x00H": "\x1b[A",
    "\x00P": "\x1b[B",
    "\x00M": "\x1b[C",
    "\x00K": "\x1b[D",
    "\xe0H": "\x1b[A",
    "\xe0P": "\x1b[B",
    "\xe0M": "\x1b[C",
    "\xe0K": "\x1b[D",
}


def _read_windows_key() -> str:
    """Read one keypress via msvcrt (Windows fallback when readchar is absent)."""
    import msvcrt

    getwch = cast(Callable[[], str], msvcrt.getwch)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnnecessaryCast]
    ch: str = getwch()
    if ch in _WIN_EXTENDED_PREFIXES:
        ch += getwch()
    return _WIN_ARROW_TRANSLATION.get(ch, ch)


def read_key(prompt: str = "") -> str:
    """Read a single keypress, or a stripped line where raw mode is unavailable.

    Uses readchar when installed and stdin is a TTY; falls back to msvcrt on
    Windows; otherwise reads a stripped ``input()`` line. The ``prompt`` is
    only echoed on that line-buffered path.
    """
    if HAS_READCHAR and readchar is not None and sys.stdin.isatty():
        return readchar.readkey()
    if os.name == "nt" and sys.stdin.isatty():
        return _read_windows_key()
    return input(prompt).strip()


@contextlib.contextmanager
def _cooked_console() -> Generator[None, None, None]:
    """Temporarily restore line mode so input() can see a terminated line.

    The review/planning loops run under windows_mouse()/posix_raw(), which
    leave the console without LINE/ECHO/PROCESSED (or termios ICANON/ECHO).
    A bare input() inside those scopes receives Enter as '\\r' with no '\\n'
    and blocks forever. Re-enabling cooked mode for the duration of the
    prompt (restoring raw afterwards) makes every prompt responsive.
    No-op when the console mode cannot be read or set.
    """
    if os.name == "nt":
        if not sys.stdin.isatty():
            yield
            return
        import ctypes
        from ctypes import wintypes

        kernel = kernel32()
        stdin = kernel.GetStdHandle(wintypes.DWORD(-10))
        mode = wintypes.DWORD(0)
        if not kernel.GetConsoleMode(stdin, ctypes.byref(mode)):
            yield
            return
        old_mode = mode.value
        cooked = old_mode | 0x0001 | 0x0002 | 0x0004  # PROCESSED | LINE | ECHO
        cooked &= ~0x0010  # MOUSE_INPUT off: wheel records must not feed input()
        kernel.SetConsoleMode(stdin, cooked)
        try:
            yield
        finally:
            kernel.SetConsoleMode(stdin, old_mode)
        return
    if not sys.stdin.isatty():
        yield
        return
    try:
        import termios
    except ImportError:
        yield
        return
    try:
        fileno = sys.stdin.fileno()
    except OSError:
        yield
        return
    old = termios.tcgetattr(fileno)
    new = termios.tcgetattr(fileno)
    new[3] |= termios.ICANON | termios.ECHO | termios.ISIG
    termios.tcsetattr(fileno, termios.TCSANOW, new)
    try:
        yield
    finally:
        termios.tcsetattr(fileno, termios.TCSANOW, old)


def read_line(prompt: str = "") -> str:
    """Read one line of input with an echoed prompt.

    Cooked mode is forced for the call so prompts stay responsive even when
    the caller sits inside a mouse-mode TUI scope.
    """
    with _cooked_console():
        return input(prompt)


_ANSI_READY_APPEND = ("~", "~")


@contextlib.contextmanager
def windows_mouse() -> Generator[None, None, None]:
    """Enable console mouse (wheel) events on Windows; restore mode on exit.

    Without ENABLE_MOUSE_INPUT no MOUSE_WHEELED records are queued, so the
    wheel-reader never sees them. QUICK_EDIT is disabled so a stray click does
    not park the console. No-op when the console API is unavailable.
    """
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel = kernel32()
    stdin = kernel.GetStdHandle(wintypes.DWORD(-10))
    mode = wintypes.DWORD(0)
    if not kernel.GetConsoleMode(stdin, ctypes.byref(mode)):
        yield
        return
    old_mode = mode.value
    try:
        # Clear line/echo/processed/quick-edit; keep insert + anything else.
        keep = old_mode & ~(
            0x0001 | 0x0002 | 0x0004 | 0x0040  # PROCESSED | LINE | ECHO | QUICK_EDIT
        )
        new_mode = keep | 0x0010 | 0x0080  # MOUSE_INPUT | EXTENDED_FLAGS
        if kernel.SetConsoleMode(stdin, new_mode):
            yield
        else:
            yield
    finally:
        kernel.SetConsoleMode(stdin, old_mode)


@contextlib.contextmanager
def posix_raw() -> Generator[None, None, None]:
    """Raw termios on a posix TTY; a no-op elsewhere.

    VMIN=1 with a VTIME timeout means reads return a byte promptly and time
    out with b"" when no burst follows, so `read_ansi_key` can reconstruct
    ANSI/SGR sequences without readchar's per-read TCSAFLUSH discarding them.
    """
    if os.name == "nt" or not sys.stdin.isatty():
        yield
        return
    try:
        import termios
    except ImportError:
        yield
        return
    fileno = sys.stdin.fileno()
    old = termios.tcgetattr(fileno)
    new = termios.tcgetattr(fileno)
    new[0] &= ~(termios.IXON | termios.IXOFF)
    new[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
    new[6][termios.VMIN] = 1  # type: ignore[index]
    new[6][termios.VTIME] = 3  # type: ignore[index]  # ~0.3 s read timeout
    termios.tcsetattr(fileno, termios.TCSAFLUSH, new)
    try:
        yield
    finally:
        termios.tcsetattr(fileno, termios.TCSAFLUSH, old)


def _read_escape_sequence(fileno: int) -> str:
    """Drain an ESC gateway sequence (arrows, SGR, SS3) already in the buffer."""
    parts = [b"\x1b"]
    while len(parts) < 16:
        try:
            chunk = os.read(fileno, 1)
        except OSError:
            break
        if not chunk:  # VTIME elapsed, burst done
            break
        parts.append(chunk)
        if chunk in (b"M", b"m", b"A", b"B", b"C", b"D", b"~", b"Z"):
            break
    try:
        return b"".join(parts).decode("utf-8")
    except UnicodeDecodeError:
        return b"".join(parts).decode("utf-8", "replace")


def read_ansi_key() -> str:
    """One keypress on posix TTYs; ESC gateway sequences are read whole.

    Reads block on the first byte (VMIN); the burst tail is drained until the
    VTIME timeout kept active by `posix_raw`, so lone Esc presses finish
    quickly and SGR wheel/click reports arrive intact.
    """
    if os.name == "nt" or not sys.stdin.isatty():
        return sys.stdin.read(1)
    fileno = sys.stdin.fileno()
    try:
        first = os.read(fileno, 1)
    except OSError:
        return sys.stdin.read(1)
    if not first:
        return ""
    if first != b"\x1b":
        try:
            return first.decode("utf-8")
        except UnicodeDecodeError:
            return first.decode("utf-8", "replace")
    return _read_escape_sequence(fileno)


def parse_sgr_mouse(seq: str) -> str | None:
    """SGR mouse report (``\x1b[<Cb;x;yM/m``): wheel direction, else None."""
    if not seq.startswith("\x1b[<") or not seq.endswith(("M", "m")):
        return None
    try:
        button = int(seq[3:-1].split(";")[0])
    except ValueError:
        return None
    if not button & 64:
        return None
    return WHEEL_DOWN if button & 1 else WHEEL_UP


def classify_windows_event(
    event_type: int,
    key_down: bool,
    vk: int,
    ch: str,
    *,
    button_state: int = 0,
    event_flags: int = 0,
) -> str | None:
    """One console input record: resize marker, key string, wheel, or None to discard."""
    if event_type == 4:  # WINDOW_BUFFER_SIZE_EVENT
        return RESIZE_KEY
    if event_type == 2 and event_flags & 0x0004:  # MOUSE_WHEELED
        delta = button_state >> 16
        if delta >= 0x8000:
            delta -= 0x10000
        return WHEEL_UP if delta > 0 else WHEEL_DOWN
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

    kernel: Any = cast(Any, ctypes.windll.kernel32)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnnecessaryCast]
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
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetConsoleMode.restype = wintypes.BOOL
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.SetConsoleMode.restype = wintypes.BOOL
    return kernel


def read_windows_console_key() -> str:
    """Block for one keypress, discarding non-wheel mouse/window/resize events.

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
            button_state=int.from_bytes(raw[8:12], "little"),
            event_flags=int.from_bytes(raw[16:20], "little"),
        )
        if key is None:
            continue
        return key


def esc_has_tail() -> bool:
    """True when input bytes follow an ESC: a click burst, not a lone Esc press."""
    try:
        import msvcrt

        kbhit = cast(Callable[[], int], msvcrt.kbhit)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnnecessaryCast]
        return bool(kbhit())
    except ImportError:
        import select

        try:
            return bool(select.select([sys.stdin], [], [], 0)[0])
        except (OSError, ValueError):
            # Non-pollable stdin (pytest capture, closed pipe): assume a lone Esc press.
            return False


def drain_escape(read_ready: Callable[[], int], read_one: Callable[[], str]) -> None:
    """Swallow the tail of an ANSI escape burst (e.g. mouse click reports)."""
    while read_ready():
        if "@" <= read_one() <= "~":
            return


def drain_tail() -> None:
    """Swallow a pending escape burst (e.g. a mouse click report)."""
    try:
        import msvcrt

        kbhit = cast(Callable[[], int], msvcrt.kbhit)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnnecessaryCast]
        getwch = cast(Callable[[], str], msvcrt.getwch)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnnecessaryCast]
        drain_escape(kbhit, getwch)
    except ImportError:
        import select

        try:
            drain_escape(
                lambda: bool(select.select([sys.stdin], [], [], 0)[0]),
                lambda: sys.stdin.read(1),
            )
        except (OSError, ValueError):
            # Non-pollable stdin (pytest capture, closed pipe): nothing to drain.
            return


def clear_screen() -> None:
    """Hard clear bypassing Rich: console.clear() misroutes inside Live."""
    out = sys.__stdout__
    assert out is not None
    out.write("\x1b[2J\x1b[H]")
    out.flush()
