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

`text` is the port of `tidal2ytm/text.py`; Tasks 5 and 7 add `track_scoring`
sections here, and Task 6 adds `album_matching`.

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
from pathlib import Path

from tidal2ytm.text import normalize, similarity

CRATE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = CRATE_ROOT / "tests" / "fixtures"

# Invented catalogue data, never real: no artist, album or track here exists.
TITLE = "Aurora Drift"
OTHER_TITLE = "Midnight Aurora"
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


def main() -> None:
    write_fixture("text", normalize_cases() + similarity_cases())


if __name__ == "__main__":
    main()
