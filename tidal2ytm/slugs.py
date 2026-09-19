"""
slugs.py — All slug and match_id generation for tidal2ytm.

No other module should contain slug logic.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata


def artist_slug(name: str) -> str:
    """
    Normalize an artist name into a URL-safe slug.

    1. NFKD-normalize, encode ASCII ignoring errors.
    2. Lowercase.
    3. Replace any run of non-alphanumeric characters with a single '-'.
    4. Strip leading/trailing '-'.
    No length cap — artist slugs are always the full normalized name.
    """
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def album_slug(name: str) -> str:
    """
    Normalize an album name into a slug of at most 15 characters.

    Step 1 — Strip non-Latin: NFKD-normalize; remove chars that are not
              ASCII alphanumeric or a space. If empty → Step 5.
    Step 2 — Normalize: lowercase; replace runs of non-alphanumeric with
              a single space; strip leading/trailing whitespace.
    Step 3 — If collapsing spaces to hyphens yields ≤ 15 chars, use it.
    Step 4 — Reduce:
              • Has spaces → acronym (first char of each word; digit-only
                words contribute the full word). If still > 15, truncate.
              • Single token → truncate to 15.
    Step 5 — Non-Latin fallback: "album-" + 6-char SHA-1 hex digest.
    """
    # Step 1
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9 ]", " ", s)
    if not s.strip():
        return _non_latin_fallback(name)

    # Step 2
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()

    # Step 3
    candidate = s.replace(" ", "-")
    if len(candidate) <= 15:
        return candidate

    # Step 4
    words = s.split()
    if len(words) > 1:
        acronym = "".join(w if w.isdigit() else w[0] for w in words)
        return acronym[:15]
    else:
        return words[0][:15]


def _non_latin_fallback(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
    return f"album-{digest}"


def dedup_slugs(slugs: list[str]) -> list[str]:
    """
    Given an ordered list of slugs at the same level, append -2, -3, …
    to duplicates. The first occurrence is unchanged.

    Candidates skip any suffix already taken by an earlier entry so the
    result contains no duplicates.
    """
    seen: dict[str, int] = {}
    used: set[str] = set()
    result: list[str] = []
    for slug in slugs:
        if slug not in seen and slug not in used:
            seen[slug] = 1
            used.add(slug)
            result.append(slug)
        else:
            n = seen.get(slug, 1) + 1
            while f"{slug}-{n}" in used or f"{slug}-{n}" in seen:
                n += 1
            seen[slug] = n
            candidate = f"{slug}-{n}"
            seen[candidate] = 1
            used.add(candidate)
            result.append(candidate)
    return result
