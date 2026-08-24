"""
slugs.py — All slug and match_id generation for tidal2ytm.

No other module should contain slug logic.
"""
from __future__ import annotations
import re
import secrets
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
    Step 5 — Non-Latin fallback: "album-" + 5 random lowercase alnum chars.
    """
    # Step 1
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9 ]", " ", s)
    if not s.strip():
        return _non_latin_fallback()

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
        acronym = "".join(
            w if w.isdigit() else w[0]
            for w in words
        )
        return acronym[:15]
    else:
        return words[0][:15]


def _non_latin_fallback() -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    rand_id = "".join(secrets.choice(alphabet) for _ in range(5))
    return f"album-{rand_id}"


def make_album_match_id(artist_name: str, album_name: str) -> str:
    """Return 'artist-slug/album-slug'."""
    return f"{artist_slug(artist_name)}/{album_slug(album_name)}"


def dedup_slugs(slugs: list[str]) -> list[str]:
    """
    Given an ordered list of slugs at the same level, append -2, -3, …
    to duplicates. The first occurrence is unchanged.
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    for slug in slugs:
        if slug not in seen:
            seen[slug] = 1
            result.append(slug)
        else:
            seen[slug] += 1
            result.append(f"{slug}-{seen[slug]}")
    return result
