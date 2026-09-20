from __future__ import annotations

# Single owner for the wait-spinner choice (cli.wait_status and the planning
# TUI both use it); tweak here to try other rich spinner names. Rich advances
# frames at `SPINNER_SPEED / arc.interval` (arc interval = 100 ms), so speed
# drives the cadence and SPINNER_REFRESH_PER_SECOND the redraw rate.
SPINNER_NAME = "arc"
SPINNER_STYLE = "dim white"
SPINNER_SPEED = 1.0
SPINNER_REFRESH_PER_SECOND = 30.0

STATUS_STYLE: dict[str, str] = {
    "needs_review": "on cyan",
    "transferred": "on magenta",
    "skip": "dim",
    "failed": "on red",
}
