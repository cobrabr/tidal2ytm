"""Generate the golden corpus the Rust parity tests replay.

Run from the repository root:

    uv run python crates/tidal2ytm-core/tools/generate_fixtures.py

Every expectation in `crates/tidal2ytm-core/tests/fixtures/*.json` is the value
the Python implementation returned when the file was written. Nothing is
reimplemented here and nothing is recomputed at test time, so a rule that
changes in either language shows up as a diff in the fixture instead of as a
test that has quietly agreed with its own implementation. One generator section
per ported module, one fixture file per ported module, and one
`tests/parity_<module>.rs` per ported module, added together as each port lands.

`text` is the port of `tidal2ytm/text.py` and `version_tokens` the port of
`tidal2ytm/version_tokens.py`; this task also adds `track_scoring` and `search`,
and Task 6 adds `album_matching`.

Confirmed pure half of `tidal2ytm/album_matching.py`, recorded here because
Task 6's `album_match.rs` cross-references it. The criterion is that a function
belongs to the pure half if it takes no YTM client and performs no I/O:

    group_by_album_id
    _candidate_artist_names
    _version_term
    _album_meta_score
    _sibling_year
    _sibling_entry
    _dedupe_details
    _best_track_fit
    _rank_candidates
    _map_winner_tracks

The seam worth naming is `_dedupe_details`: it reads only `detail` dictionaries
that `_fetch_album_details` and `_harvest_sibling_candidates` have already
fetched, so it moves nothing across the line. The impure half stays Python:
`_review_results`, `_try_fetch_album`, `_harvest_sibling_candidates`,
`_fetch_album_details`, and the search half of `resolve_album_group`.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tidal2ytm.album_matching import (
    ALBUM_CERTAIN_THRESHOLD,
    ALBUM_MARGIN_EPS,
    _album_meta_score,  # pyright: ignore[reportPrivateUsage]
    _best_track_fit,  # pyright: ignore[reportPrivateUsage]
    _candidate_artist_names,  # pyright: ignore[reportPrivateUsage]
    _dedupe_details,  # pyright: ignore[reportPrivateUsage]
    _map_winner_tracks,  # pyright: ignore[reportPrivateUsage]
    _rank_candidates,  # pyright: ignore[reportPrivateUsage]
    _sibling_entry,  # pyright: ignore[reportPrivateUsage]
    _sibling_year,  # pyright: ignore[reportPrivateUsage]
    _version_term,  # pyright: ignore[reportPrivateUsage]
    group_by_album_id,
)
from tidal2ytm.models import MatchResult, SourceTrack
from tidal2ytm.planning_search import (
    # `_named_haystacks` is private to the module, and reaching for it is the
    # point: the fixture has to freeze what that exact function returns, not a
    # second copy of the same rule written here.
    _named_haystacks,  # pyright: ignore[reportPrivateUsage]
    parse_tidal_link,
    resolve_album_link,
    search_library,
)
from tidal2ytm.text import normalize, similarity
from tidal2ytm.track_scoring import (
    DURATION_ABS_TOL_SEC,
    TrackFit,
    duration_ok,
    score_track_fit,
)
from tidal2ytm.version_tokens import qualifier_similarity, split_base_qualifiers

CRATE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = CRATE_ROOT / "tests" / "fixtures"

# Invented catalogue data, never real: no artist, album or track here exists.
TITLE = "Aurora Drift"
OTHER_TITLE = "Midnight Aurora"
ARTIST = "Halcyon Bay"
ALBUM = "Northern Signal"
# Long enough to cross difflib's 200-element threshold, where `autojunk` would
# start discarding the space character if the port forgot `autojunk=False`.
LONG_TITLE = (
    "the hollow lantern festival at the edge of the sleeping sea, "
    "where every year the tide forgets the shore it came from "
) * 2


def write_fixture(module: str, cases: list[dict[str, object]]) -> None:
    """Write `cases` to `tests/fixtures/<module>.json` as the review artifact."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"{module}.json"
    # `newline="\n"` keeps the committed blob byte-identical whatever platform
    # ran the generator, so a fixture diff is only ever a real change.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(cases, indent=2) + "\n")
    print(f"wrote {path.relative_to(CRATE_ROOT.parents[1])} ({len(cases)} cases)")


def normalize_cases() -> list[dict[str, object]]:
    """One explicit row per normalization behaviour worth pinning.

    The order of NFKD and the ASCII filter is the landmine in this function:
    decomposing first turns `é` into `e` plus a combining accent, so the accent
    is dropped and the letter survives. Filtering first drops the whole
    character and the accent-decomposing cases are what catch that mistake.
    """
    rows: dict[str, str] = {
        "empty string": "",
        "single character": "a",
        "whitespace only": " \t\n ",
        "punctuation only": "!@#$%^&*()-_=+[]{};':\",./<>?",
        # NFKD leaves `ẞ` as `U+1E9E` — it has no decomposition — so the ASCII-ignore
        # encode drops it whole and nothing survives to reach `.lower()`.
        "uppercase letter with no ascii form": "\u1e9e",
        "plain ascii text": "Aurora Drift (Live at Reykjavik)",
        "accented latin, decomposes": "Caf\u00e9 M\u00fcnster \u2014 R\u00e9my 2024",
        "accented latin, no decomposition": "S\u00f8lvi Br\u00e5ker",
        "non-latin script, no ascii decomposition": "\ud55c\uad6d \u30ab\u30e9\u30aa\u30b1",
        "decomposing and non-decomposing mixed": "Caf\u00e9 S\u00f8lvi \ud55c\uad6d",
        "autojunk length, padded by spaces": " " * 250 + TITLE + " " * 250,
        "autojunk length, single token": ("aurora" * 42)[:250],
        "tabs and newlines": "Line\tOne\nLine Two\r\nThree",
        # Python's `\s` accepts five more ASCII code points than Rust's
        # `is_ascii_whitespace`, and `str.strip()` accepts four that Unicode's
        # `White_Space` rejects. Both reach this function because the
        # ASCII-ignore encode runs first, so the filter only ever sees ASCII.
        "python whitespace the rust predicates reject": "a\u000bb\u001cc\u001dd\u001ee\u001ff",
        "same, at the edges where strip applies": "\u000bab\u001c",
        "same, trailing edge": "ab\u000b\u001c",
        "combining marks only": "\u0301\u0302\u0303\u0308",
    }
    return [{"input": value, "expected": normalize(value)} for value in rows.values()]


def similarity_cases() -> list[dict[str, object]]:
    """One explicit row per shape the matching-block search has to survive.

    Every row is a pair rather than a sample: the difflib port is only as
    trustworthy as the corpus behind it, and an enumerated matrix is auditable
    by reading this function.
    """
    rows: dict[str, tuple[str, str]] = {
        "both empty": ("", ""),
        "first empty": ("", TITLE),
        "second empty": (TITLE, ""),
        "identical": (TITLE, TITLE),
        "one character changed in the middle": (TITLE, "Aurora Driftz"),
        "one character appended": (TITLE, TITLE + "s"),
        "adjacent characters transposed": ("Amber Tide", "Ambre Tide"),
        "run of 1 in the first operand": ("a" + TITLE, TITLE),
        "run of 1 in both operands": ("a" + TITLE, "a" + TITLE),
        "run of 5 in the first operand": ("aaaaa" + TITLE, TITLE),
        "run of 5 in both operands": ("aaaaa" + TITLE, "aaaaa" + TITLE),
        "run of 50 in the first operand": ("z" * 50 + TITLE, TITLE),
        "run of 50 in both operands": ("z" * 50 + TITLE, "z" * 50 + TITLE),
        "prefix of the other": (TITLE, TITLE + " Revisited"),
        "suffix of the other": (TITLE, "The " + TITLE),
        "substring at the start": (TITLE, "Aurora " + TITLE),
        "substring in the middle": (TITLE, "The Aurora " + TITLE),
        "substring at the end": (TITLE, TITLE + " Reprise"),
        # A letter, not punctuation: the ASCII filter would strip a symbol and
        # leave the two operands identical, which tests nothing.
        "over 200 characters, one character differs": (
            LONG_TITLE,
            LONG_TITLE[:100] + "q" + LONG_TITLE[101:],
        ),
        # Both operands already identical would score 1.0 whether or not the port
        # honoured `autojunk`, so the differing pair above is what proves it.
        "over 200 characters, one space differs": (
            LONG_TITLE,
            LONG_TITLE[:100] + " " + LONG_TITLE[100:],
        ),
        # The one case where the port's `autojunk=False` actually changes the
        # answer: 0.989384 with it honoured, 0.985138 with difflib's default.
        "over 200 characters, fragmented match": (LONG_TITLE, "tt hh ee " + LONG_TITLE[4:]),
        "long, and disjoint": (LONG_TITLE, (OTHER_TITLE + " ") * 14),
        "differs by one space": ("midnight aurora drive", "midnight auroradrive"),
        # If the port's whitespace class were `char::is_ascii_whitespace`, these two
        # operands would each lose their `0x0B` and the `0x1C` respectively, and
        # the pairs would score 0.5 and 0.0 instead.
        "differs only by vertical tab": ("a\u000bb", "a\u000bc"),
        "differs only by file separator": ("a\u001cb", "a\u001cc"),
        "second operand empty after normalization": (TITLE, "\ud55c\uad6d"),
        "case and accent differ only": ("Caf\u00e9 Drift", "cafe drift"),
    }
    return [
        {"input": [first, second], "expected": similarity(first, second)}
        for first, second in rows.values()
    ]


def split_base_qualifier_cases() -> list[dict[str, object]]:
    """One explicit row per thing the `(`/`[` pattern does or refuses to do.

    The pattern's two character classes are independent, so `(live]` is a match
    and a newline inside a qualifier ends the attempt at that position rather
    than being swallowed. A qualifier that is empty once stripped is dropped
    instead of becoming one that counts toward the overlap and matches nothing.
    """
    rows: dict[str, str] = {
        "no qualifier": TITLE,
        "one parenthesised qualifier": f"{TITLE} (Live)",
        "one bracketed qualifier": f"{TITLE} [Remastered 2011]",
        "both kinds, side by side": f"{TITLE} (Live) [Remastered 2011]",
        "adjacent qualifiers": f"{TITLE} (Live)(Radio Edit)",
        "no base left behind": "(Live)",
        "padded base and qualifier": f"  {TITLE} (  Live  )  ",
        "empty qualifier is dropped": f"{TITLE} () [Solo]",
        "inner opener does not start a new match": f"{TITLE} (Drift Live) Reprise",
        "opener and closer kinds need not agree": f"{TITLE} (Live]",
        "qualifier spanning a newline does not match": f"{TITLE} (Live\nReprise)",
        "case is folded away": f"{TITLE} (LiVe)",
        "accented qualifier": "Caf\u00e9 Drift (\u00c9dition)",
    }
    return [
        {"input": {"op": "split", "text": text}, "expected": list(split_base_qualifiers(text))}
        for text in rows.values()
    ]


def qualifier_similarity_cases() -> list[dict[str, object]]:
    """One explicit row per overlap shape the score has to distinguish.

    Every list here is already casefolded and stripped, because that is what
    `split_base_qualifiers` hands over: this function does no normalizing of its
    own, and the two rows whose qualifiers differ by case or by padding are what
    pin that division of labour rather than leaving it to be assumed.

    The three fractional rows are where reassociating `0.4 + 0.6 * overlap`
    would show: at an overlap of `1 / 7` the regrouped expression differs from
    CPython's in the last bit, and the corpus compares exactly.
    """
    rows: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "both empty": ((), ()),
        "source empty only": ((), ("live",)),
        "target empty only": (("live",), ()),
        "identical single qualifier": (("live",), ("live",)),
        "identical, declared in a different order": (
            ("live", "remastered 2011"),
            ("remastered 2011", "live"),
        ),
        "one side is a subset": (("live",), ("live", "remastered 2011")),
        "disjoint": (("live",), ("acoustic",)),
        "partial overlap": (("live", "remastered 2011"), ("live", "radio edit")),
        "differs only by case, so it is still a distinct qualifier": (
            ("live",),
            ("LIVE",),
        ),
        "differs only by padding, so it is still a distinct qualifier": (
            ("live",),
            (" live ",),
        ),
        "a repeat on one side counts once": (("live", "live"), ("live", "radio edit")),
        "overlap one seventh": (("live",), ("live", "a", "b", "c", "d", "e", "f")),
        "overlap two fifths": (("live", "remix"), ("live", "a", "b", "c", "d")),
        "overlap one third": (("live",), ("live", "a", "b")),
    }
    return [
        {
            "input": {"op": "qualify", "source": list(source), "target": list(target)},
            "expected": qualifier_similarity(source, target),
        }
        for source, target in rows.values()
    ]


def source_track(**overrides: object) -> SourceTrack:
    """A `SourceTrack` for the scoring corpus, with every field explicit.

    The Rust side has no `from_dict`, so a fixture row that omitted a field
    would be asserting that the port invents a default the Python never had.
    Every row therefore carries all eleven.
    """
    fields: dict[str, object] = {
        "tidal_id": 100_001,
        "title": TITLE,
        "artist": ARTIST,
        "artists": [ARTIST],
        "album": ALBUM,
        "album_id": 200_001,
        "album_year": 2019,
        "duration_sec": 200,
        "track_num": 3,
        "disc_num": 1,
        "version": None,
    }
    fields.update(overrides)
    return SourceTrack(**fields)  # pyright: ignore[reportArgumentType]


def candidate(
    title: str,
    artists: list[str],
    album: str | None,
    duration_sec: int | None,
    album_track_num: int | None,
) -> dict[str, object]:
    """A `Candidate` as the Rust envelope holds it, for the scoring corpus."""
    return {
        "video_id": "9aB3xQ1lZ7c",
        "title": title,
        "artists": artists,
        "album": album,
        "album_track_num": album_track_num,
        "duration_sec": duration_sec,
    }


def duration_ok_cases() -> list[dict[str, object]]:
    """One explicit row per side of both tolerance arms.

    A short track is decided by `DURATION_ABS_TOL_SEC` alone; a long one by
    `int(duration * DURATION_REL_TOL)`, which is wider, so the delta that fails
    on the short track passes on the long one. The same three deltas therefore
    appear twice with opposite answers, and that is the point of the second set.
    The truncation row is what pins `int()` against a rounding one.
    """
    short, long = 120, 333
    rows: dict[str, tuple[int, int | None]] = {
        "short track, one second inside the absolute tolerance": (short, short + 3),
        "short track, exactly at the absolute tolerance": (short, short + DURATION_ABS_TOL_SEC),
        "short track, one second past the absolute tolerance": (
            short,
            short + DURATION_ABS_TOL_SEC + 1,
        ),
        "short track, delta measured downwards": (short, short - 3),
        "long track, inside the absolute tolerance": (long, long + 3),
        "long track, exactly at the absolute tolerance": (long, long + DURATION_ABS_TOL_SEC),
        "long track, past the absolute tolerance but inside the relative one": (
            long,
            long + DURATION_ABS_TOL_SEC + 1,
        ),
        "long track, exactly at the relative tolerance": (long, long + 6),
        "long track, one second past the relative tolerance": (long, long + 7),
        "relative tolerance truncates rather than rounds": (349, 356),
        "a source duration of zero accepts any candidate": (0, 9_999),
        "a source duration of zero accepts an absent candidate duration": (0, None),
        "a candidate with no duration is refused": (short, None),
    }
    return [
        {
            "input": {
                "op": "duration_ok",
                "source_sec": source_sec,
                "candidate_sec": candidate_sec,
            },
            "expected": duration_ok(source_sec, candidate_sec),
        }
        for source_sec, candidate_sec in rows.values()
    ]


def score_track_fit_cases() -> list[dict[str, object]]:
    """One explicit row per term of the weighted sum, and per branch around it.

    The four similarities are pinned separately as well as through `overall`,
    because a wrong term and a wrong weight produce the same wrong total and
    only one of them is a bug in this function. The track-number rows bracket
    the bonus at zero and at one, and the identical pair is where the `+ 0.08`
    bonus meets the `1.0` cap.
    """
    rows: dict[str, tuple[SourceTrack, dict[str, object]]] = {
        "an exact copy of the source": (
            source_track(),
            candidate(TITLE, [ARTIST], ALBUM, 200, None),
        ),
        "identical track numbers take the bonus": (
            source_track(),
            candidate(TITLE, [ARTIST], "Southern Static", 200, 3),
        ),
        "track numbers one apart take no bonus": (
            source_track(),
            candidate(TITLE, [ARTIST], "Southern Static", 200, 2),
        ),
        "a source track number of zero never matches": (
            source_track(track_num=0),
            candidate(TITLE, [ARTIST], "Southern Static", 200, 0),
        ),
        "an absent candidate track number never matches": (
            source_track(),
            candidate(TITLE, [ARTIST], "Southern Static", 200, None),
        ),
        # The same pair with the bonus withheld sits at 0.971, so the two
        # together show the bonus being applied and then clipped.
        "the bonus is capped at one": (
            source_track(),
            candidate(TITLE, [ARTIST], f"{ALBUM} Echo", 200, 3),
        ),
        "just below the cap, so the bonus is not clipped": (
            source_track(),
            candidate(TITLE, [ARTIST], f"{ALBUM} Echo", 200, 2),
        ),
        "a candidate with no album scores zero for it": (
            source_track(),
            candidate(TITLE, [ARTIST], None, 200, None),
        ),
        "an empty candidate album is absent too": (
            source_track(),
            candidate(TITLE, [ARTIST], "", 200, None),
        ),
        "the candidate artist need not be the first one": (
            source_track(artists=[ARTIST, "Iris Vale"]),
            candidate(TITLE, ["Iris Vale"], ALBUM, 200, None),
        ),
        "the source artist need not be the first one either": (
            source_track(artists=["Iris Vale", ARTIST]),
            candidate(TITLE, [ARTIST], ALBUM, 200, None),
        ),
        "several candidate artists, one of them matching": (
            source_track(artists=[ARTIST, "Iris Vale"]),
            candidate(TITLE, ["Odell Marsh", "Iris Vale"], ALBUM, 200, None),
        ),
        "no candidate artists scores zero": (
            source_track(),
            candidate(TITLE, [], ALBUM, 200, None),
        ),
        "an empty source artist list falls back to the artist field": (
            source_track(artists=[], artist=ARTIST),
            candidate(TITLE, [ARTIST], ALBUM, 200, None),
        ),
        "an empty source artist list and an empty artist field score zero": (
            source_track(artists=[], artist=""),
            candidate(TITLE, [ARTIST], ALBUM, 200, None),
        ),
        "a qualifier on the source title only": (
            source_track(title=f"{TITLE} (Live)"),
            candidate(TITLE, [ARTIST], ALBUM, 200, None),
        ),
        "the same qualifier in both, spelled differently": (
            source_track(title=f"{TITLE} (Live)"),
            candidate(f"{TITLE} [LIVE]", [ARTIST], ALBUM, 200, None),
        ),
        "qualifiers on both sides that disagree": (
            source_track(title=f"{TITLE} (Live)"),
            candidate(f"{TITLE} [Radio Edit]", [ARTIST], ALBUM, 200, None),
        ),
        "a source version joins the qualifiers": (
            source_track(version="Remastered"),
            candidate(f"{TITLE} [Remastered]", [ARTIST], ALBUM, 200, None),
        ),
        "a source version the candidate does not name": (
            source_track(version="Live"),
            candidate(TITLE, [ARTIST], ALBUM, 200, None),
        ),
        "a source version beside a title qualifier": (
            source_track(title=f"{TITLE} (Acoustic)", version="Live"),
            candidate(TITLE, [ARTIST], ALBUM, 200, None),
        ),
        "an absent candidate duration leaves no delta": (
            source_track(),
            candidate(TITLE, [ARTIST], ALBUM, None, None),
        ),
        "a candidate delta measured upwards": (
            source_track(),
            candidate(TITLE, [ARTIST], ALBUM, 260, None),
        ),
        "a candidate delta measured downwards": (
            source_track(),
            candidate(TITLE, [ARTIST], ALBUM, 140, None),
        ),
        "nothing in common at all": (
            source_track(title="Aurora Drift", album=ALBUM),
            candidate("Kelpline Static", ["Odell Marsh"], "Tidal Algebra", 200, 9),
        ),
    }
    cases: list[dict[str, object]] = []
    for source, cand in rows.values():
        fit = score_track_fit(
            source,
            str(cand["title"]),
            cand["artists"],  # pyright: ignore[reportArgumentType]
            cand["album"] or "",  # pyright: ignore[reportArgumentType]
            cand["duration_sec"],  # pyright: ignore[reportArgumentType]
            cand["album_track_num"],  # pyright: ignore[reportArgumentType]
        )
        cases.append(
            {
                "input": {
                    "op": "score",
                    "source": asdict(source),
                    "candidate": cand,
                },
                "expected": asdict(fit),
            }
        )
    return cases


def library_track(
    tidal_id: int, title: str, artist: str, album: str, **overrides: object
) -> SourceTrack:
    """A `SourceTrack` for the search corpus. All eleven fields are explicit."""
    fields: dict[str, object] = {
        "tidal_id": tidal_id,
        "title": title,
        "artist": artist,
        "artists": [artist] if artist else [],
        "album": album,
        "album_id": 200_000 + tidal_id,
    }
    fields.update(overrides)
    return source_track(**fields)


# One library the search rows share. The second `Aurora Drift` sits under a
# different album so the album-link rule has two albums to tell apart, and the
# punctuation-only title normalizes to nothing at all.
LIBRARY: list[SourceTrack] = [
    library_track(100_001, TITLE, ARTIST, ALBUM, album_id=200_001, track_num=1),
    library_track(100_002, OTHER_TITLE, "Odell Marsh", "Tidal Algebra", album_id=200_002),
    library_track(
        100_003,
        "Kelpline Static",
        "Iris Vale",
        "Kelpline Sessions",
        album_id=200_003,
        artists=["Iris Vale", "Odell Marsh"],
    ),
    library_track(100_004, TITLE, ARTIST, "Northern Signal Redux", album_id=200_004),
    library_track(100_005, "!!! ???", ARTIST, "Silence Deluxe", album_id=200_005),
    library_track(100_006, "Hollow Bell", "", "Quiet Rooms", album_id=200_006, artists=[]),
]


def haystack_cases() -> list[dict[str, object]]:
    """The searchable fields, with the names the review screen prints beside them.

    `artist0`, `artist1` and so on are the contract: a score shown to a person
    has to say which credit it came from, so the index is part of the name rather
    than a separate piece of data.
    """
    rows: dict[str, SourceTrack] = {
        "a single artist": library_track(1, TITLE, ARTIST, ALBUM),
        "several artists": library_track(
            1, TITLE, ARTIST, ALBUM, artists=[ARTIST, "Iris Vale", "Odell Marsh"]
        ),
        "an empty artist field beside a populated artist list": library_track(
            1, TITLE, "", ALBUM, artists=["Iris Vale"]
        ),
        "an empty album": library_track(1, TITLE, ARTIST, ""),
        "an empty artist field and an empty artist list": library_track(
            1, TITLE, "", "", artists=[]
        ),
        "every field empty": library_track(1, "", "", "", artists=[]),
    }
    return [
        {
            "input": {"op": "haystacks", "track": asdict(track)},
            "expected": [[name, field] for name, field in _named_haystacks(track)],
        }
        for track in rows.values()
    ]


def search_library_cases() -> list[dict[str, object]]:
    """One explicit row per branch of the search, over named libraries.

    Two libraries are used because the two awkward shapes need one to themselves:
    a track whose every field is empty has no haystack at all, and a track whose
    title is punctuation has a haystack that normalizes to nothing. Either would
    panic on the Rust side if the maximum over a track's haystacks were taken
    without a default, and either could be scored as a match if containment were
    tested against the raw string instead of the normalized one.

    `search_library` returns `SourceTrack` values and the Rust port returns
    indices into the slice it was handed. Both hold the same order, so the corpus
    records the indices — which is also the only shape a `{input, expected}` row
    can carry, since a returned `SourceTrack` would have to be duplicated into
    every row that mentioned one. Identity, not equality, does the mapping: two
    rows of a library can be equal without being the same track.
    """
    empty_fields = [library_track(100_009, "", "", "", artists=[])]
    ties = [
        library_track(100_010, "Kelpline Static", "Iris Vale", "Alpha Sessions"),
        library_track(100_011, "Kelpline Static", "Iris Vale", "Beta Sessions"),
        library_track(100_012, "Kelpline Static", "Iris Vale", "Gamma Sessions"),
    ]
    # The punctuation title's album and artist are real haystacks, so this track
    # scores something; only its title normalizes to nothing. The query is not a
    # substring of either, which puts the track on the fuzzy branch where a
    # mistakenly-zero-length-tolerant score would put it in the results.
    unsearchable = [
        library_track(100_013, "!!! ???", ARTIST, "Silence Deluxe"),
        library_track(100_014, "Kelpline Static", "Iris Vale", "Kelpline Sessions"),
    ]
    rows: dict[str, tuple[list[SourceTrack], str]] = {
        "an empty query returns the whole library as a direct result": (LIBRARY, ""),
        "a whitespace-only query normalizes to empty": (LIBRARY, "   \t "),
        "a query matching exactly": (LIBRARY, "aurora drift"),
        "a query matching by substring, including mid-title": (LIBRARY, "aurora"),
        "a query matching only a second artist's credit": (LIBRARY, "odell marsh"),
        "a query matching nothing": (LIBRARY, "zzzqqq wibble"),
        "a query matching one of two identical titles": (LIBRARY, "kelpline static"),
        "a track with no artist field is still reachable by title": (LIBRARY, "hollow bell"),
        "an empty library returns nothing": ([], "aurora"),
        "a track whose every field is empty does not crash the search": (
            empty_fields,
            "aurora",
        ),
        "a track whose every field is empty is not a match": (empty_fields, ""),
        "a title that normalizes to empty is absent from the fuzzy result": (
            unsearchable,
            "kelplne statc",
        ),
        # The typo is not a substring of any haystack, so nothing is contained
        # and all three fall to the fuzzy branch, where they score identically.
        "a tie is ordered by declaration, not by score": (ties, "kelplne statc"),
    }
    cases: list[dict[str, object]] = []
    for tracks, query in rows.values():
        found, direct = search_library(tracks, query)
        position = {id(track): index for index, track in enumerate(tracks)}
        cases.append(
            {
                "input": {
                    "op": "search",
                    "tracks": [asdict(track) for track in tracks],
                    "query": query,
                },
                "expected": {
                    "indices": [position[id(track)] for track in found],
                    "direct": direct,
                },
            }
        )
    return cases


def parse_tidal_link_cases() -> list[dict[str, object]]:
    """One explicit row per shape of the link pattern, and per way of failing it."""
    rows: dict[str, str] = {
        "a bare track url": "https://tidal.com/track/123456",
        "a browse album url": "https://tidal.com/browse/album/654321",
        "a listen host with an album id": "  https://listen.tidal.com/album/777  ",
        "uppercase scheme and path": "https://TIDAL.COM/Track/123456",
        "mixed-case browse path": "HTTPS://TIDAL.COM/Browse/Album/42",
        "a trailing slash after the id": "https://tidal.com/browse/track/42/",
        "an id embedded in a sentence": "see https://tidal.com/album/555 now",
        "an unrecognised first link, then a valid one": (
            "tidal.com/nothing and tidal.com/album/556"
        ),
        "no id after the kind": "https://tidal.com/track/",
        "a kind the pattern does not name": "https://tidal.com/artist/999",
        "a different host entirely": "https://example.com/track/123",
        "not a link at all": "northern signal",
    }
    return [
        {"input": {"op": "parse_link", "raw": raw}, "expected": parse_tidal_link(raw)}
        for raw in rows.values()
    ]


def resolve_album_link_cases() -> list[dict[str, object]]:
    """One explicit row per way the album rule can select, and one that selects none.

    Two tracks share album `200_002` so the row can show that the rule returns
    the whole album's liked songs in declaration order rather than stopping at
    the first, and the two albums here carry adjacent ids so a comparison by
    prefix rather than by equality would also pass.
    """
    albums: list[SourceTrack] = [
        library_track(100_020, TITLE, ARTIST, ALBUM, album_id=200_002),
        library_track(100_021, OTHER_TITLE, "Odell Marsh", "Tidal Algebra", album_id=200_003),
        library_track(100_022, "Kelpline Static", "Iris Vale", "Tidal Algebra", album_id=200_003),
        library_track(100_023, "Hollow Bell", "", "Quiet Rooms", album_id=200_009),
    ]
    rows: dict[str, tuple[list[SourceTrack], int]] = {
        "the only track of an album": (albums, 200_002),
        "every track of an album, in declaration order": (albums, 200_003),
        "an album with no liked songs": (albums, 999_999),
        "an empty library": ([], 200_002),
    }
    return [
        {
            "input": {
                "op": "resolve_album",
                "tracks": [asdict(track) for track in tracks],
                "album_id": album_id,
            },
            "expected": [tracks.index(track) for track in resolve_album_link(tracks, album_id)],
        }
        for tracks, album_id in rows.values()
    ]


# --- album_match (Task 6) ---------------------------------------------------
#
# The album corpus mixes two shapes: the Python pure half reads YTM-shaped
# dicts (`videoId`, `duration_seconds`, `trackNumber`, artists as objects with a
# `name`) while the Rust envelope reads typed `AlbumSummary`/`AlbumDetail`/
# `Candidate`. Every builder below emits both, so one description drives the
# Python call that produces the expectation and the JSON the Rust side reads.

ALBUM_ID = "MPREb_00000042"
SIBLING_ID = "MPREb_00000043"


def album_summary(
    title: str,
    artist: str = ARTIST,
    year: int | None = None,
    album_id: str = ALBUM_ID,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One album summary in both shapes.

    The Python dict carries an `artists` list of objects, which is what
    `_candidate_artist_names` reads. The Rust `AlbumSummary` has one `artist`
    string: the envelope collapses the list — deviation 3 — so every summary
    here is single-artist, and no corpus row exercises a multi-artist album,
    where that collapse would change the dedupe key.
    """
    python = {
        "browseId": album_id,
        "title": title,
        "artists": [{"name": artist}] if artist else [],
        "year": year,
    }
    rust = {"album_id": album_id, "title": title, "artist": artist, "year": year}
    return python, rust


def album_track(
    title: str,
    artist: str,
    duration_sec: int | None,
    track_num: int | None,
    *,
    video_id: str = "9aB3xQ1lZ7c",
    rust_album: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One tracklist row in both shapes.

    `rust_album` is set only on the row that proves the album title passed to
    `best_track_fit` overrides whatever the row itself carries: the Python
    tracklist never has an album field, so a port that read the row's album
    instead would score against a value Python never saw.
    """
    python = {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": artist}] if artist else [],
        "duration_seconds": duration_sec,
        "trackNumber": track_num,
    }
    rust = {
        "video_id": video_id,
        "title": title,
        "artists": [artist] if artist else [],
        "album": rust_album,
        "album_track_num": track_num,
        "duration_sec": duration_sec,
    }
    return python, rust


def album_detail(
    summary: tuple[dict[str, Any], dict[str, Any]],
    tracks: list[tuple[dict[str, Any], dict[str, Any]]],
    other_versions: list[tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One album detail in both shapes: the Python dict and the `AlbumDetail`."""
    python_summary, rust_summary = summary
    others = other_versions or []
    python: dict[str, Any] = {
        "title": python_summary["title"],
        "tracks": [python_track for python_track, _ in tracks],
        "other_versions": [python_sib for python_sib, _ in others],
    }
    rust: dict[str, Any] = {
        "summary": rust_summary,
        "tracks": [rust_track for _, rust_track in tracks],
        "other_versions": [rust_sib for _, rust_sib in others],
    }
    return python, rust


def track_fit_expected(fit: TrackFit, index: int) -> dict[str, Any]:
    """The `TrackFit` a `best_track_fit` case expects, plus the winning row."""
    return {
        "overall": fit.overall,
        "title_similarity": fit.title_similarity,
        "artist_similarity": fit.artist_similarity,
        "album_similarity": fit.album_similarity,
        "version_similarity": fit.version_similarity,
        "track_num_match": fit.track_num_match,
        "duration_delta_sec": fit.duration_delta_sec,
        "index": index,
    }


def map_result_expected(match: MatchResult) -> dict[str, Any]:
    """The fields a `map_winner_tracks` case asserts.

    The `summary` string is deliberately absent: it is user-facing copy, and the
    parity rule keeps tests off copy. Whether a review reason exists is a fact
    about the result, so that presence is recorded instead of its text.
    """
    confidence = match.confidence
    return {
        "yt_video_id": match.yt_video_id,
        "yt_title": match.yt_title,
        "yt_artist": match.yt_artist,
        "yt_album": match.yt_album,
        "yt_album_track_num": match.yt_album_track_num,
        "yt_duration_sec": match.yt_duration_sec,
        "match_method": match.match_method.value,
        "status": match.status.value,
        "review_reason_present": match.review_reason is not None,
        "overall": confidence.overall,
        "title_similarity": confidence.title_similarity,
        "artist_similarity": confidence.artist_similarity,
        "album_similarity": confidence.album_similarity,
        "duration_delta_sec": confidence.duration_delta_sec,
        "version_similarity": confidence.version_similarity,
        "track_num_match": confidence.track_num_match,
        "album_coherence": confidence.album_coherence,
    }


def album_group_cases() -> list[dict[str, object]]:
    """`group_by_album_id` over a small library, and over an empty one."""
    tracks: list[SourceTrack] = [
        library_track(100_001, TITLE, ARTIST, ALBUM, album_id=200_001),
        library_track(100_002, OTHER_TITLE, "Odell Marsh", "Tidal Algebra", album_id=200_002),
        library_track(100_003, "Paper Tide", ARTIST, ALBUM, album_id=200_001),
        library_track(100_004, "Rose Window", "Iris Vale", "Quiet Rooms", album_id=200_003),
    ]
    cases: list[dict[str, object]] = []
    for group_tracks in (tracks, []):
        position = {id(track): index for index, track in enumerate(group_tracks)}
        expected = [
            [album_id, [position[id(track)] for track in group]]
            for album_id, group in sorted(group_by_album_id(group_tracks).items())
        ]
        cases.append(
            {
                "input": {"op": "group", "tracks": [asdict(track) for track in group_tracks]},
                "expected": expected,
            }
        )
    return cases


def album_version_term_cases() -> list[dict[str, object]]:
    """One row per arm of `_version_term`, including the word-set subset rule."""
    rows: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "both sides unmarked": ((), ()),
        "identical single qualifier": (("live",), ("live",)),
        "a qualifier on the source only": (("live",), ()),
        "a qualifier on the candidate only": ((), ("live",)),
        "the candidate set is a subset": (("live", "remastered"), ("live",)),
        "the source set is a subset": (("live",), ("live", "remastered")),
        "disjoint qualifiers": (("remix",), ("live",)),
        "a phrase matches its head word": (("deluxe version",), ("deluxe",)),
        "word sets partially overlap": (("live",), ("live acoustic",)),
    }
    return [
        {
            "input": {"op": "version", "source": list(source), "cand": list(cand)},
            "expected": _version_term(source, cand),
        }
        for source, cand in rows.values()
    ]


def album_meta_cases() -> list[dict[str, object]]:
    """One row per qualifier shape and per year asymmetry `_album_meta_score` has.

    The two year rows pointing opposite ways are the point: a known candidate
    year with an unknown source year keeps the neutral `0.5`, and so does the
    reverse, because the delta needs both sides.
    """
    rows: dict[str, tuple[SourceTrack, str, str, int | None]] = {
        "both titles carry no qualifier": (source_track(), ALBUM, ARTIST, 2019),
        "identical qualifiers": (
            source_track(album=f"{ALBUM} (Live)"),
            f"{ALBUM} (Live)",
            ARTIST,
            2019,
        ),
        "a qualifier on the source only": (
            source_track(album=f"{ALBUM} (Live)"),
            ALBUM,
            ARTIST,
            2019,
        ),
        "a qualifier on the candidate only": (
            source_track(),
            f"{ALBUM} (Live)",
            ARTIST,
            2019,
        ),
        "a qualifier the candidate adds": (
            source_track(album=f"{ALBUM} (Live)"),
            f"{ALBUM} (Live) (Deluxe)",
            ARTIST,
            2019,
        ),
        "disjoint qualifiers": (
            source_track(album=f"{ALBUM} (Live)"),
            f"{ALBUM} (Remix)",
            ARTIST,
            2019,
        ),
        "a source with no album year against a dated candidate": (
            source_track(album_year=None),
            ALBUM,
            ARTIST,
            2019,
        ),
        "a dated source against a candidate with no year": (
            source_track(album_year=2019),
            ALBUM,
            ARTIST,
            None,
        ),
        "a year delta of zero": (source_track(album_year=2019), ALBUM, ARTIST, 2019),
        "a year delta of one": (source_track(album_year=2019), ALBUM, ARTIST, 2020),
        "a year delta of ten": (source_track(album_year=2019), ALBUM, ARTIST, 2029),
        "an ampersand against the word and": (
            source_track(album="Salt & Ember"),
            "Salt and Ember",
            ARTIST,
            2019,
        ),
    }
    cases: list[dict[str, object]] = []
    for source, title, artist, year in rows.values():
        python, rust = album_summary(title, artist, year)
        cases.append(
            {
                "input": {"op": "meta", "source": asdict(source), "candidate": rust},
                "expected": _album_meta_score(source, python),
            }
        )
    return cases


def album_sibling_year_cases() -> list[dict[str, object]]:
    """`_sibling_year`: the sibling's parseable year, else the parent's."""
    rows: dict[str, tuple[int | None, int | None]] = {
        "the sibling carries the year": (2020, 2019),
        "the sibling has none so the parent's is used": (None, 2019),
        "the sibling's year wins over the parent's": (2018, 2019),
        "neither side carries one": (None, None),
    }
    return [
        {
            "input": {"op": "sibling_year", "sib": sib, "parent": parent},
            "expected": _sibling_year({"year": sib}, {"year": parent}),
        }
        for sib, parent in rows.values()
    ]


def album_sibling_entry_cases() -> list[dict[str, object]]:
    """`_sibling_entry`: each field from the sibling, else the parent."""
    cases: list[dict[str, object]] = []

    def add(
        parent: tuple[dict[str, Any], dict[str, Any]],
        sib: tuple[dict[str, Any], dict[str, Any]],
    ) -> None:
        python_parent, rust_parent = parent
        python_sib, rust_sib = sib
        python_entry = _sibling_entry(python_parent, python_sib)
        names = _candidate_artist_names(python_entry)
        cases.append(
            {
                "input": {"op": "sibling_entry", "parent": rust_parent, "sib": rust_sib},
                "expected": {
                    "album_id": python_entry["browseId"],
                    "title": python_entry["title"],
                    "artist": names[0] if names else "",
                    "year": python_entry["year"],
                },
            }
        )

    add(
        album_summary(ALBUM, ARTIST, 2019, ALBUM_ID),
        album_summary(f"{ALBUM} (Deluxe)", "Iris Vale", 2020, SIBLING_ID),
    )
    add(
        album_summary(ALBUM, ARTIST, 2019, ALBUM_ID),
        album_summary("", "Iris Vale", 2020, SIBLING_ID),
    )
    add(
        album_summary(ALBUM, ARTIST, 2019, ALBUM_ID),
        album_summary(f"{ALBUM} (Deluxe)", "", None, SIBLING_ID),
    )
    add(
        album_summary(ALBUM, ARTIST, 2019, ALBUM_ID),
        album_summary(f"{ALBUM} (Deluxe)", "Iris Vale", None, SIBLING_ID),
    )
    # A parent represented only by the older singular `artist` string still
    # supplies a name when the sibling names no artist at all.
    add(
        (
            {"browseId": ALBUM_ID, "title": ALBUM, "artist": ARTIST, "year": 2019},
            {"album_id": ALBUM_ID, "title": ALBUM, "artist": ARTIST, "year": 2019},
        ),
        album_summary(f"{ALBUM} (Deluxe)", "", None, SIBLING_ID),
    )
    return cases


def album_dedupe_cases() -> list[dict[str, object]]:
    """`_dedupe_details`, one row per way a key can or cannot collide."""

    def make(
        specs: list[tuple[str, str]],
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[list[dict[str, Any]]]]:
        python: list[tuple[dict[str, Any], dict[str, Any]]] = []
        rust: list[list[dict[str, Any]]] = []
        for title, artist in specs:
            python_summary, rust_summary = album_summary(title, artist)
            python_detail, rust_detail = album_detail((python_summary, rust_summary), [])
            python.append((python_summary, python_detail))
            rust.append([rust_summary, rust_detail])
        return python, rust

    def case(
        python: list[tuple[dict[str, Any], dict[str, Any]]],
        rust: list[list[dict[str, Any]]],
    ) -> dict[str, object]:
        kept_ids = {id(candidate) for candidate, _ in _dedupe_details(python)}
        expected = [
            index for index, (candidate, _) in enumerate(python) if id(candidate) in kept_ids
        ]
        return {"input": {"op": "dedupe", "details": rust}, "expected": expected}

    cases: list[dict[str, object]] = [
        case(*make([(ALBUM, ARTIST), (ALBUM, ARTIST)])),
        case(*make([(ALBUM, ARTIST), (ALBUM.lower(), ARTIST)])),
        case(*make([("Salt & Ember", ARTIST), ("Salt and Ember", ARTIST)])),
        case(*make([("Northern  Signal", ARTIST), ("Northern Signal", ARTIST)])),
        case(*make([(ALBUM, ARTIST), (ALBUM, "Iris Vale")])),
        case(*make([(ALBUM, ARTIST), ("Southern Static", ARTIST)])),
    ]

    # A detail whose `other_versions` shelf names the same album again: the same
    # title and artist as the next pool entry, which is how a real harvest
    # produces the twin the dedupe exists to collapse.
    python, rust = make([(ALBUM, ARTIST), (ALBUM, ARTIST)])
    python_other, rust_other = album_summary(ALBUM, ARTIST)
    python[0][1]["other_versions"] = [python_other]
    rust[0][1]["other_versions"] = [rust_other]
    cases.append(case(python, rust))
    return cases


def album_fit_cases() -> list[dict[str, object]]:
    """`_best_track_fit`: the fitting row, the skipped row, the tie, and empty."""
    source = source_track(
        tidal_id=100_001, title="Paper Tide", album=ALBUM, album_year=2019, duration_sec=200
    )

    def run(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, object]:
        python_tracks = [python for python, _ in pairs]
        rust_tracks = [rust for _, rust in pairs]
        fit, found = _best_track_fit(source, python_tracks, ALBUM)
        expected: dict[str, Any] | None
        if fit is None or found is None:
            expected = None
        else:
            index = next(i for i, track in enumerate(python_tracks) if track is found)
            expected = track_fit_expected(fit, index)
        return {
            "input": {
                "op": "fit",
                "source": asdict(source),
                "tracks": rust_tracks,
                "album_title": ALBUM,
            },
            "expected": expected,
        }

    return [
        run([album_track("Paper Tide", ARTIST, 200, 1)]),
        # A duration 50 seconds out fails `duration_ok` and the row is skipped.
        run([album_track("Paper Tide", ARTIST, 250, 1)]),
        # Two rows scoring identically: the first strict maximum keeps the earlier.
        run(
            [
                album_track("Paper Tide", ARTIST, 200, 1, video_id="9aB3xQ1lZ7c"),
                album_track("Paper Tide", ARTIST, 200, 1, video_id="Zq4Wm8n2RtY"),
            ]
        ),
        run([]),
        # The row carries a different album, which `best_track_fit` must ignore.
        run([album_track("Paper Tide", ARTIST, 200, 1, rust_album="A Different Album")]),
    ]


def album_rank_cases() -> list[dict[str, object]]:
    """`_rank_candidates`: better fit first, and a tie keeping declaration order."""
    first = source_track(
        tidal_id=100_001, title="Paper Tide", album=ALBUM, album_year=2019, duration_sec=200
    )
    sources = [
        first,
        source_track(
            tidal_id=100_002, title="Hollow Bell", album=ALBUM, album_year=2019, duration_sec=210
        ),
    ]

    def detail(
        title: str,
        artist: str,
        year: int | None,
        tracks: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[tuple[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]:
        summary = album_summary(title, artist, year)
        return summary, album_detail(summary, tracks)

    def run(
        details: list[
            tuple[tuple[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]
        ],
    ) -> dict[str, object]:
        python_details = [(summary[0], detail[0]) for summary, detail in details]
        rust_details = [[summary[1], detail[1]] for summary, detail in details]
        scored = _rank_candidates(first, sources, python_details)
        position = {id(python_cand): index for index, (python_cand, _) in enumerate(python_details)}
        expected = [[score, position[id(candidate)]] for score, candidate, _ in scored]
        return {
            "input": {
                "op": "rank",
                "first": asdict(first),
                "sources": [asdict(source) for source in sources],
                "details": rust_details,
            },
            "expected": expected,
        }

    good = detail(
        ALBUM,
        ARTIST,
        2019,
        [
            album_track("Paper Tide", ARTIST, 200, 1),
            album_track("Hollow Bell", ARTIST, 210, 2),
        ],
    )
    good_twin = detail(
        ALBUM,
        ARTIST,
        2019,
        [
            album_track("Paper Tide", ARTIST, 200, 1),
            album_track("Hollow Bell", ARTIST, 210, 2),
        ],
    )
    poor = detail(
        "Southern Static",
        "Odell Marsh",
        2015,
        [
            album_track("Kelpline Static", "Odell Marsh", 200, 1),
            album_track("Rose Window", "Odell Marsh", 210, 2),
        ],
    )
    empty = detail("Empty Rooms", "Iris Vale", 2011, [])
    return [
        run([good, poor]),
        run([poor, good]),
        # Two identical candidates score identically, so a stable sort keeps the
        # order they were declared in.
        run([good, good_twin]),
        run([empty, poor]),
    ]


def album_abstain_cases() -> list[dict[str, object]]:
    """The `resolve_album_group` abstention rule, split out as `album_abstains`.

    Python has no function by this name: the condition is inline in
    `resolve_album_group`, which needs a client. These rows transcribe that
    condition exactly, including the empty case the Rust split adds, so the split
    has a frozen reference to replay against.
    """
    rows: dict[str, list[tuple[float, int]]] = {
        "no candidates abstain": [],
        "a lone candidate never abstains": [(0.5, 0)],
        "a score exactly at the certain threshold ignores a close second": [
            (ALBUM_CERTAIN_THRESHOLD, 0),
            (ALBUM_CERTAIN_THRESHOLD - ALBUM_MARGIN_EPS, 1),
        ],
        "just below the threshold with a close second abstains": [(0.94, 0), (0.91, 1)],
        "a gap exactly one margin below the top does not abstain": [
            (ALBUM_MARGIN_EPS, 0),
            (0.0, 1),
        ],
        "a gap inside the margin abstains": [(0.5, 0), (0.47, 1)],
        "a gap wider than the margin does not abstain": [(0.5, 0), (0.2, 1)],
    }
    cases: list[dict[str, object]] = []
    for scored in rows.values():
        top = scored[0][0] if scored else 0.0
        abstains = not scored or (
            len(scored) > 1
            and top < ALBUM_CERTAIN_THRESHOLD
            and (top - scored[1][0]) < ALBUM_MARGIN_EPS
        )
        cases.append(
            {
                "input": {
                    "op": "abstain",
                    "scored": [[score, index] for score, index in scored],
                },
                "expected": abstains,
            }
        )
    return cases


def album_map_cases() -> list[dict[str, object]]:
    """`_map_winner_tracks`: a fitting source, an absent fit, and an empty list."""
    paper = source_track(
        tidal_id=100_001, title="Paper Tide", album=ALBUM, album_year=2019, duration_sec=200
    )
    hollow = source_track(
        tidal_id=100_002, title="Hollow Bell", album=ALBUM, album_year=2019, duration_sec=210
    )
    absent = source_track(
        tidal_id=100_003, title="Nothing Here", album=ALBUM, album_year=2019, duration_sec=500
    )

    def detail(
        title: str,
        artist: str,
        year: int | None,
        tracks: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return album_detail(album_summary(title, artist, year), tracks)

    def run(
        sources: list[SourceTrack],
        winner: tuple[dict[str, Any], dict[str, Any]],
    ) -> dict[str, object]:
        python_winner, rust_winner = winner
        matches = _map_winner_tracks(sources, python_winner)
        return {
            "input": {
                "op": "map",
                "sources": [asdict(source) for source in sources],
                "winner": rust_winner,
            },
            "expected": [map_result_expected(match) for match in matches],
        }

    winner = detail(
        ALBUM,
        ARTIST,
        2019,
        [
            album_track("Paper Tide", ARTIST, 200, 1, video_id="9aB3xQ1lZ7c"),
            album_track("Hollow Bell", ARTIST, 210, 2, video_id="Zq4Wm8n2RtY"),
        ],
    )
    empty_winner = detail(ALBUM, ARTIST, 2019, [])
    return [
        run([paper], winner),
        run([hollow], winner),
        run([paper, hollow, absent], winner),
        run([paper], empty_winner),
    ]


def main() -> None:
    write_fixture("text", normalize_cases() + similarity_cases())
    write_fixture(
        "version_tokens",
        split_base_qualifier_cases() + qualifier_similarity_cases(),
    )
    write_fixture("track_scoring", duration_ok_cases() + score_track_fit_cases())
    write_fixture(
        "search",
        haystack_cases()
        + search_library_cases()
        + parse_tidal_link_cases()
        + resolve_album_link_cases(),
    )
    write_fixture(
        "album_match",
        album_group_cases()
        + album_version_term_cases()
        + album_meta_cases()
        + album_sibling_year_cases()
        + album_sibling_entry_cases()
        + album_dedupe_cases()
        + album_fit_cases()
        + album_rank_cases()
        + album_abstain_cases()
        + album_map_cases(),
    )


if __name__ == "__main__":
    main()
