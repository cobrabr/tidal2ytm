from __future__ import annotations

from .models import MatchMethod

CERTAIN_CONFIDENCE = 1.0
GREEN_ABOVE = 0.85
YELLOW_AT_LEAST = 0.70


def color_for(value: float) -> str:
    if value >= 0.999:
        return "blue"
    if value > GREEN_ABOVE:
        return "green"
    if value >= YELLOW_AT_LEAST:
        return "yellow"
    return "red"


def is_certain(method: MatchMethod, confidence: float) -> bool:
    return method == MatchMethod.ISRC and confidence >= 0.999
