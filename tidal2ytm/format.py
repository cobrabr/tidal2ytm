from __future__ import annotations


def fmt_duration(sec: int | None) -> str:
    if not sec:
        return "—"
    minutes, seconds = divmod(sec, 60)
    return f"{minutes}:{seconds:02d}"
