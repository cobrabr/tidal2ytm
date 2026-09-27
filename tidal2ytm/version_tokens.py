from __future__ import annotations

import re

_PAREN_RE = re.compile(r"[\(\[](.*?)[\)\]]")


def split_base_qualifiers(text: str) -> tuple[str, tuple[str, ...]]:
    quals: list[str] = [m.group(1).strip().casefold() for m in _PAREN_RE.finditer(text or "")]
    base = _PAREN_RE.sub("", text or "").strip()
    return (base, tuple(q for q in quals if q))


def qualifier_similarity(source: tuple[str, ...], target: tuple[str, ...]) -> float:
    if not source and not target:
        return 1.0
    if not source or not target:
        return 0.4
    s = set(source)
    t = set(target)
    if s == t:
        return 1.0
    overlap = len(s & t) / len(s | t)
    return 0.4 + 0.6 * overlap
