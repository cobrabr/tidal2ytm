from __future__ import annotations

GREEN_ABOVE = 0.85
YELLOW_AT_LEAST = 0.70


def color_for(value: float) -> str:
    # Numeric confidence (including a perfect fuzzy 1.0) renders green.
    if value > GREEN_ABOVE:
        return "green"
    if value >= YELLOW_AT_LEAST:
        return "yellow"
    return "red"
