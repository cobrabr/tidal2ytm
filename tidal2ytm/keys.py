"""
keys.py — shared terminal key and line reading for the interactive TUIs.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
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
    "classify_windows_event",
    "clear_screen",
    "drain_escape",
    "drain_tail",
    "esc_has_tail",
    "kernel32",
    "map_windows_key",
    "read_key",
    "read_line",
    "read_windows_console_key",
    "readchar_key",
]

# Ctrl+C as reported by readchar in raw mode
CTRL_C = "\x03"

RESIZE_KEY = "\x00R"

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


def read_line(prompt: str = "") -> str:
    """Read one line of input with an echoed prompt."""
    return input(prompt)


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
    return kernel


def read_windows_console_key() -> str:
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
