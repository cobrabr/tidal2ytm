from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


def normalize(value: str) -> str:
    ascii_folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM.sub("", ascii_folded.lower()).strip()


def similarity(a: str, b: str) -> float:
    norm_a, norm_b = normalize(a), normalize(b)
    if not norm_a or not norm_b:
        return 0.0
    return SequenceMatcher(None, norm_a, norm_b, autojunk=False).ratio()
