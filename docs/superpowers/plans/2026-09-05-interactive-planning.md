# Interactive planning TUI implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `tidal2ytm plan` with an interactive planning TUI launched by bare `tidal2ytm`, letting the user search liked tracks, accumulate selections across searches, and match them to YTM on demand.

**Architecture:** New `planning_search.py` (pure search/filter helpers) and `planning_merge.py` (pure match/merge helpers) carry the testable logic; new `planning.py` owns the rich TUI loop and selection session; `cli.py` loses the `plan` subcommand and dispatches bare invocations to the TUI; `plan.py` and its tests are deleted.

**Tech Stack:** Python 3.11, `rich` + `readchar` TUI (same pattern as `review.py`), `tidalapi`, `ytmusicapi`, TOML plan via `plan_io.py`, `uv`, `pytest`, `ruff`, `pyright` strict.

**Spec:** `docs/superpowers/specs/2026-09-05-interactive-planning-design.md`

## Global Constraints

- Every module begins with `from __future__ import annotations`; data containers are dataclasses; every function is type-annotated (`pyright` strict, no new ignores except the established `reportPrivateUsage` pattern for cross-module private reuse).
- Line length 100 (`ruff`), double quotes, Canadian spellings in prose.
- `tidal_id` is track identity; `yt_video_id` is always a bare 11-char ID, never a URL.
- All slug logic stays in `slugs.py`; `[meta]` is recomputed via `plan_io.update_plan_meta()` after any status change.
- `transfer.py`, `review.py`, `status` behaviour is unchanged; the TOML shape is unchanged.
- Private reuse precedent: `review.py` imports `plan_io._extract_video_id`; likewise `planning_search.py` may import `matcher._normalize` and `matcher._similarity`.
- `tests/conftest.py:isolated_data_dir` redirects `DATA_DIR`, `YTM_AUTH_FILE`, `TIDAL_TOKEN_FILE`, `PLAN_FILE`; never mock the `DATA_DIR.mkdir` side effect.

---

## File structure

- Create `tidal2ytm/planning_search.py`: pure search over `list[SourceTrack]` (two-tier ranking, Tidal-link detection and resolution). No I/O, no logins.
- Create `tidal2ytm/planning_merge.py`: pure match/merge helpers (`match_result_to_track_dict` moved verbatim from `plan.py`, selection ordering, merge classification, plan insertion). No TUI.
- Create `tidal2ytm/planning.py`: `PlanningSession` dataclass, pure selection helpers, `run_match_action` (testable via injected `yt` and `input_fn`), `run_planning` TUI loop (manual test only).
- Modify `tidal2ytm/cli.py`: delete `cmd_plan` and the `plan` parser, `required=False`, bare dispatch, repointed no-plan message.
- Modify `tidal2ytm/review.py`, `tidal2ytm/transfer.py`: repoint the one stale `tidal2ytm plan` string each.
- Modify `tidal2ytm/models.py`, `tidal2ytm/tidal_source.py`: composer field, only if the Task 2 spike finds a clean `tidalapi` source.
- Delete `tidal2ytm/plan.py`, `tests/test_plan.py`.
- Create `tests/test_planning_search.py`, `tests/test_planning_merge.py`, `tests/test_planning.py`; modify `tests/test_cli.py`.
- Modify `README.md`, `AGENTS.md`: planning workflow wording.

---

### Task 1: Upgrade all dependencies and re-run gates

**Files:**
- Modify: `uv.lock`, `pyproject.toml` (only if a bound must change)

**Interfaces:**
- Consumes: current bounds (`tidalapi>=0.8.3`, `ytmusicapi>=1.7.0`, `tomli-w>=1.0.0`, `rich>=13.0.0`, `readchar>=4.2.0`; dev `pytest>=8`, `pytest-cov>=6`, `pyright>=1.1`, `ruff>=0.9`, `pre-commit>=4`).
- Produces: upgraded lockfile plus the recorded version list used by later tasks.

- [ ] **Step 1: Upgrade and resync**

Run: `uv lock --upgrade && uv sync`
Expected: lockfile rewritten, environment synced.

- [ ] **Step 2: Record versions**

Run: `uv pip list | Select-String -Pattern "tidalapi|ytmusicapi|tomli|rich|readchar|pytest|ruff|pyright"`
Expected: version table printed; keep it for the commit message.

- [ ] **Step 3: Re-run all quality gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q`
Expected: PASS. If a major bump breaks a call site, adapt the call site here (small, mechanical fixes only; anything larger is pinned back with a reason in the commit message, per the spec escape hatch).

- [ ] **Step 4: Commit**

```bash
git add uv.lock pyproject.toml
git commit -m "chore: upgrade all dependencies to latest" -m "tidalapi X, ytmusicapi Y, rich Z, readchar W, tomli-w V, pytest U, ruff T, pyright S"
```

---

### Task 2: Composer-credits spike against the upgraded tidalapi

**Files:**
- Modify: none (read-only probe).

**Interfaces:**
- Consumes: installed `tidalapi` package in `.venv`.
- Produces: a go/no-go decision for Task 3.

- [ ] **Step 1: Probe the Track model for composer data**

Run: `uv run python -c "import tidalapi; print([n for n in dir(tidalapi.Track) if 'ompos' in n.lower() or 'redit' in n.lower() or 'ongwriter' in n.lower()])"`
Expected: a short attribute list (possibly empty).

- [ ] **Step 2: Check the attribute shape in the installed source**

Run: `rg -n -i "composer|credit" .venv/Lib/site-packages/tidalapi --glob "*.py" | Select-Object -First 20`
Expected: hits showing whether the field is a plain string, a list of artist-like objects with `.name`, or absent. If `rg` is unavailable, use `Select-String -Pattern "composer|credit" -Path .venv/Lib/site-packages/tidalapi/*.py`.

- [ ] **Step 3: Record the decision**

Decision rule: a clean field (string or list of objects exposing `.name`, reachable without extra API calls per track) means Task 3 proceeds; anything else means Task 3 is skipped and title-plus-version matching stands as the spec records. Write the one-line outcome as a code comment in Task 3's test file header when Task 3 is skipped (for example `# Composer spike (Task 2): tidalapi X.Y exposes no per-track composer field; artists-mode matches title+version only.`).

---

### Task 3: Carry composer credits into SourceTrack (skip if Task 2 was no-go)

**Files:**
- Modify: `tidal2ytm/models.py`, `tidal2ytm/tidal_source.py`, `tidal2ytm/matcher.py` (`_coerce_source` passthrough)
- Test: `tests/test_tidal_source.py` (append)

**Interfaces:**
- Consumes: Task 2 go decision and the exact attribute shape found.
- Produces: `SourceTrack.composer: str | None` populated by `get_liked_tracks`.

- [ ] **Step 1: Write the failing test**

```python
def test_get_liked_tracks_extracts_composer() -> None:
    from types import SimpleNamespace

    album = SimpleNamespace(name="Goldberg Variations", id=7, year=1981)
    artist = SimpleNamespace(name="Glenn Gould")
    t = SimpleNamespace(
        id=42,
        name="Aria",
        artist=artist,
        artists=[artist],
        album=album,
        duration=200,
        isrc=None,
        track_num=1,
        volume_num=1,
        version=None,
        composer="Johann Sebastian Bach",
    )
    session = SimpleNamespace(
        user=SimpleNamespace(favorites=SimpleNamespace(tracks=lambda limit=0: [t]))
    )

    from tidal2ytm.tidal_source import get_liked_tracks

    tracks = get_liked_tracks(session)  # type: ignore[arg-type]
    assert tracks[0].composer == "Johann Sebastian Bach"
```

(If the real shape is a list of objects, use `composers=[SimpleNamespace(name="Johann Sebastian Bach")]` in the fixture and adjust the assertion; the extraction below handles both.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tidal_source.py::test_get_liked_tracks_extracts_composer -v`
Expected: FAIL with `AttributeError` or assertion on missing `composer`.

- [ ] **Step 3: Add the field and extraction**

In `models.py`, add to `SourceTrack`:

```python
composer: str | None = None
```

In `tidal_source.py`, before `results.append(...)`, add:

```python
composer: str | None = None
raw_composer = getattr(t, "composer", None)
if isinstance(raw_composer, str) and raw_composer:
    composer = raw_composer
else:
    raw_composers = getattr(t, "composers", None)
    names: list[str] = []
    if raw_composers is not None:
        try:
            names = [getattr(a, "name", "") or "" for a in raw_composers if hasattr(a, "name")]
        except Exception:
            names = []
    names = [n for n in names if n]
    composer = ", ".join(names) if names else None
```

Pass `composer=composer` into the `SourceTrack(...)` constructor. In `matcher.py::_coerce_source`, pass `composer=track.get("composer")` into the constructed `SourceTrack`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tidal_source.py tests/test_matcher.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/models.py tidal2ytm/tidal_source.py tidal2ytm/matcher.py tests/test_tidal_source.py
git commit -m "feat: carry composer credits into SourceTrack"
```

---

### Task 4: Pure library search with two-tier ranking and Tidal-link resolution

**Files:**
- Create: `tidal2ytm/planning_search.py`
- Test: `tests/test_planning_search.py`

**Interfaces:**
- Consumes: `SourceTrack` (with optional `composer` from Task 3), `matcher._normalize`, `matcher._similarity`.
- Produces: `SearchMode`, `FUZZY_CUTOFF`, `search_library`, `parse_tidal_link`, `resolve_track_link`, `resolve_album_link` for Task 6.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from tidal2ytm.models import SourceTrack
from tidal2ytm.planning_search import (
    parse_tidal_link,
    resolve_album_link,
    resolve_track_link,
    search_library,
)


def _track(tidal_id: int, title: str, artist: str, album: str, album_id: int = 1) -> SourceTrack:
    return SourceTrack(
        tidal_id=tidal_id,
        title=title,
        artist=artist,
        artists=[artist],
        album=album,
        album_id=album_id,
        album_year=1971,
        duration_sec=200,
        isrc=None,
        track_num=1,
        disc_num=1,
        version=None,
    )


def test_general_search_substring_case_insensitive() -> None:
    tracks = [_track(1, "Aqualung", "Jethro Tull", "Aqualung"), _track(2, "Blue", "Joni", "Blue")]
    assert [t.tidal_id for t in search_library(tracks, "aqualung", "general")] == [1]


def test_songs_mode_ignores_artist_text() -> None:
    tracks = [_track(1, "Blue", "Joni Mitchell", "Blue")]
    assert search_library(tracks, "joni", "songs") == []


def test_albums_mode_matches_album_name() -> None:
    tracks = [_track(1, "Aqualung", "Jethro Tull", "Aqualung")]
    assert [t.tidal_id for t in search_library(tracks, "aqualung", "albums")] == [1]


def test_fuzzy_tier_tolerates_typo() -> None:
    tracks = [_track(1, "Aqualung", "Jethro Tull", "Aqualung")]
    assert [t.tidal_id for t in search_library(tracks, "aqualing", "songs")] == [1]


def test_empty_query_returns_everything() -> None:
    tracks = [_track(1, "A", "B", "C"), _track(2, "D", "E", "F")]
    assert [t.tidal_id for t in search_library(tracks, "", "general")] == [1, 2]


def test_parse_tidal_links() -> None:
    assert parse_tidal_link("https://tidal.com/browse/track/12345") == ("track", 12345)
    assert parse_tidal_link("https://listen.tidal.com/album/67890") == ("album", 67890)
    assert parse_tidal_link("aqualung") is None


def test_resolve_links_against_library() -> None:
    tracks = [
        _track(1, "Aria", "Gould", "Goldberg", album_id=7),
        _track(2, "Var 1", "Gould", "Goldberg", album_id=7),
    ]
    hit = resolve_track_link(tracks, 1)
    assert hit is not None and hit.title == "Aria"
    assert resolve_track_link(tracks, 999) is None
    assert [t.tidal_id for t in resolve_album_link(tracks, 7)] == [1, 2]
    assert resolve_album_link(tracks, 888) == []
```

(If Task 3 added `composer`, also pass `composer="Johann Sebastian Bach"` in one fixture and assert an artists-mode query for `bach` hits it; add that test in Task 3's commit instead if preferred, but keep it in this file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_planning_search.py -q`
Expected: FAIL with collection error (`planning_search` not defined).

- [ ] **Step 3: Write minimal implementation**

```python
"""
planning_search.py — Pure library search for the interactive planning TUI.

No I/O and no logins: every function filters an in-memory liked-tracks list.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from .matcher import _normalize, _similarity  # pyright: ignore[reportPrivateUsage]
from .models import SourceTrack

SearchMode = Literal["general", "artists", "albums", "songs"]

FUZZY_CUTOFF = 0.6

_TIDAL_LINK_RE = re.compile(r"tidal\.com/(?:browse/)?(track|album)/(\d+)", re.IGNORECASE)


def _haystacks(track: SourceTrack, mode: SearchMode) -> list[str]:
    if mode == "general":
        fields: list[Any] = [track.title, track.artist, *track.artists, track.album]
    elif mode == "artists":
        fields = [track.artist, *track.artists, track.title, track.version or ""]
        if track.composer:
            fields.append(track.composer)
    elif mode == "albums":
        fields = [track.album]
    else:
        fields = [track.title, track.version or ""]
    return [f for f in fields if f]
```

Omit the `if track.composer:` lines if Task 3 was skipped. Then:

```python
def search_library(tracks: list[SourceTrack], query: str, mode: SearchMode) -> list[SourceTrack]:
    """Two tiers: normalized substring hits first, then similarity-ranked fuzzy hits."""
    q = _normalize(query)
    if not q:
        return list(tracks)
    tier1 = [t for t in tracks if any(q in _normalize(h) for h in _haystacks(t, mode))]
    tier1_ids = {t.tidal_id for t in tier1}
    scored: list[tuple[float, SourceTrack]] = []
    for t in tracks:
        if t.tidal_id in tier1_ids:
            continue
        best = max((_similarity(h, query) for h in _haystacks(t, mode)), default=0.0)
        if best >= FUZZY_CUTOFF:
            scored.append((best, t))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return tier1 + [t for _, t in scored]


def parse_tidal_link(raw: str) -> tuple[str, int] | None:
    """Return ("track"|"album", numeric id) for Tidal URLs, else None."""
    m = _TIDAL_LINK_RE.search(raw.strip())
    if not m:
        return None
    return (m.group(1).lower(), int(m.group(2)))


def resolve_track_link(tracks: list[SourceTrack], tidal_id: int) -> SourceTrack | None:
    for t in tracks:
        if t.tidal_id == tidal_id:
            return t
    return None


def resolve_album_link(tracks: list[SourceTrack], album_id: int) -> list[SourceTrack]:
    """Canonical album rule: the liked songs of that exact album, never the album entity."""
    return [t for t in tracks if t.album_id == album_id]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_planning_search.py -q && uv run ruff check tidal2ytm/planning_search.py tests/test_planning_search.py && uv run pyright`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/planning_search.py tests/test_planning_search.py
git commit -m "feat: add pure library search for planning TUI"
```

---

### Task 5: Pure match/merge helpers

**Files:**
- Create: `tidal2ytm/planning_merge.py`
- Test: `tests/test_planning_merge.py`

**Interfaces:**
- Consumes: `MatchResult`, `SourceTrack`, `TrackStatus`, `plan_io.find_existing_match`, `slugs` helpers.
- Produces: `match_result_to_track_dict`, `iter_selection_ordered`, `classify_track`, `insert_track`, `unmatched_track_dict` for Task 6.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from typing import Any

from tidal2ytm.models import (
    ConfidenceBreakdown,
    MatchMethod,
    MatchResult,
    SourceTrack,
    TrackStatus,
)
from tidal2ytm.planning_merge import (
    classify_track,
    insert_track,
    iter_selection_ordered,
    match_result_to_track_dict,
)


def _src(tidal_id: int = 1) -> SourceTrack:
    return SourceTrack(
        tidal_id=tidal_id,
        title="Aqualung",
        artist="Jethro Tull",
        artists=["Jethro Tull"],
        album="Aqualung",
        album_id=11,
        album_year=1971,
        duration_sec=200,
        isrc=None,
        track_num=1,
        disc_num=1,
        version=None,
    )


def _result() -> MatchResult:
    return MatchResult(
        source=_src(),
        yt_video_id="AAAAAAAAAAA",
        yt_title="Aqualung",
        yt_artist="Jethro Tull",
        yt_album="Aqualung",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.9),
        status=TrackStatus.PENDING,
    )


def test_track_dict_conversion() -> None:
    d = match_result_to_track_dict(_result())
    assert d["tidal_id"] == 1 and d["yt_video_id"] == "AAAAAAAAAAA"
    assert d["match_method"] == "fuzzy" and d["status"] == "pending"


def test_classify_new_existing_same_transferred() -> None:
    assert classify_track(None, "AAAAAAAAAAA") == "add-new"
    assert classify_track({"status": "pending", "yt_video_id": ""}, "AAAAAAAAAAA") == "ask"
    assert (
        classify_track({"status": "pending", "yt_video_id": "AAAAAAAAAAA"}, "AAAAAAAAAAA")
        == "keep-same"
    )
    assert classify_track({"status": "pending", "yt_video_id": ""}, None) == "keep-same"
    assert (
        classify_track({"status": "transferred", "yt_video_id": "AAAAAAAAAAA"}, "BBBBBBBBBBB")
        == "skip-transferred"
    )


def test_insert_creates_slugs_and_reuses_them() -> None:
    plan: dict[str, Any] = {"meta": {}, "artists": []}
    d = match_result_to_track_dict(_result())
    artist_id, album_id = insert_track(plan, d, 1971)
    assert artist_id == "jethro-tull" and album_id == "jethro-tull/aqualung"
    d2 = dict(d, tidal_id=2)
    artist_id2, album_id2 = insert_track(plan, d2, 1971)
    assert (artist_id2, album_id2) == (artist_id, album_id)
    assert len(plan["artists"][0]["albums"][0]["tracks"]) == 2


def test_selection_ordering() -> None:
    import dataclasses

    b = dataclasses.replace(_src(2), artist="Abba", album="Gold", album_year=1992)
    a = dataclasses.replace(_src(1), artist="Jethro Tull", album="Aqualung", album_year=1971)
    assert [t.tidal_id for t in iter_selection_ordered({1: a, 2: b})] == [2, 1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_planning_merge.py -q`
Expected: FAIL with collection error.

- [ ] **Step 3: Write minimal implementation**

```python
"""
planning_merge.py — Pure match/merge helpers for the interactive planning TUI.

No TUI and no network: callers supply MatchResults and answer prompts.
"""

from __future__ import annotations

from typing import Any, Literal

from .models import MatchResult, SourceTrack, TrackStatus
from .slugs import album_slug, artist_slug, dedup_slugs

MergeAction = Literal["skip-transferred", "add-new", "keep-same", "ask"]


def match_result_to_track_dict(result: MatchResult) -> dict[str, Any]:
    """Convert a MatchResult to the flat dict stored in the TOML plan (moved from plan.py)."""
    src = result.source
    conf = result.confidence
    track: dict[str, Any] = {
        "tidal_id": src.tidal_id,
        "title": src.title,
        "artist": src.artist,
        "tidal_album": src.album,
        "tidal_isrc": src.isrc or "",
        "tidal_duration_sec": src.duration_sec,
        "tidal_track_num": src.track_num,
        "yt_video_id": result.yt_video_id or "",
        "yt_title": result.yt_title or "",
        "yt_artist": result.yt_artist or "",
        "yt_album": result.yt_album or "",
        "yt_album_track_num": result.yt_album_track_num or 0,
        "yt_isrc": result.yt_isrc or "",
        "yt_duration_sec": result.yt_duration_sec or 0,
        "match_method": result.match_method.value,
        "status": result.status.value,
    }
    if result.review_reason:
        track["review_reason"] = result.review_reason
    conf_dict: dict[str, Any] = {"overall": conf.overall}
    if conf.summary:
        conf_dict["summary"] = conf.summary
    if conf.title_similarity is not None:
        conf_dict["title_similarity"] = conf.title_similarity
    if conf.artist_similarity is not None:
        conf_dict["artist_similarity"] = conf.artist_similarity
    if conf.album_similarity is not None:
        conf_dict["album_similarity"] = conf.album_similarity
    if conf.duration_delta_sec is not None:
        conf_dict["duration_delta_sec"] = conf.duration_delta_sec
    track["confidence"] = conf_dict
    return track


def unmatched_track_dict(src: SourceTrack, reason: str) -> dict[str, Any]:
    """Track dict for a pick that could not be matched (match error or no candidates)."""
    return {
        "tidal_id": src.tidal_id,
        "title": src.title,
        "artist": src.artist,
        "tidal_album": src.album,
        "tidal_isrc": src.isrc or "",
        "tidal_duration_sec": src.duration_sec,
        "tidal_track_num": src.track_num,
        "yt_video_id": "",
        "yt_title": "",
        "yt_artist": "",
        "yt_album": "",
        "yt_album_track_num": 0,
        "yt_isrc": "",
        "yt_duration_sec": 0,
        "match_method": "none",
        "status": TrackStatus.NEEDS_REVIEW.value,
        "review_reason": reason,
        "confidence": {"overall": 0.0, "summary": reason},
    }


def iter_selection_ordered(selection: dict[int, SourceTrack]) -> list[SourceTrack]:
    """Legacy plan order: artist A-Z, album year then name, disc then track."""
    return sorted(
        selection.values(),
        key=lambda t: (
            t.artist.casefold(),
            t.album_year or 9999,
            t.album.casefold(),
            t.disc_num,
            t.track_num,
        ),
    )


def classify_track(existing: dict[str, Any] | None, new_video_id: str | None) -> MergeAction:
    """Pure merge policy: transferred is sacred, identical video IDs stay silent."""
    if existing is None:
        return "add-new"
    if existing.get("status") == TrackStatus.TRANSFERRED.value:
        return "skip-transferred"
    if (existing.get("yt_video_id") or "") == (new_video_id or ""):
        return "keep-same"
    return "ask"


def insert_track(
    plan: dict[str, Any], track_dict: dict[str, Any], album_year: int | None
) -> tuple[str, str]:
    """Insert into the artist/album hierarchy, reusing existing match_ids; returns (artist, album)."""
    artist_name: str = track_dict["artist"]
    album_name: str = track_dict["tidal_album"]
    artists: list[dict[str, Any]] = plan.setdefault("artists", [])
    artist_entry = next((a for a in artists if a.get("name") == artist_name), None)
    if artist_entry is None:
        taken = [a.get("match_id", "") for a in artists]
        slug = dedup_slugs(taken + [artist_slug(artist_name)])[-1]
        artist_entry = {"name": artist_name, "match_id": slug, "albums": []}
        artists.append(artist_entry)
        artists.sort(key=lambda a: str(a.get("name", "")).casefold())
    albums: list[dict[str, Any]] = artist_entry.setdefault("albums", [])
    album_entry = next((a for a in albums if a.get("name") == album_name), None)
    if album_entry is None:
        taken_album = [str(a.get("match_id", "")).split("/")[-1] for a in albums]
        aslug = dedup_slugs(taken_album + [album_slug(album_name)])[-1]
        album_entry = {
            "name": album_name,
            "match_id": f"{artist_entry['match_id']}/{aslug}",
            "tracks": [],
        }
        if album_year is not None:
            album_entry["year"] = album_year
        albums.append(album_entry)
        albums.sort(key=lambda a: (a.get("year") or 9999, str(a.get("name", "")).casefold()))
    tracks: list[dict[str, Any]] = album_entry.setdefault("tracks", [])
    tracks.append(track_dict)
    tracks.sort(key=lambda t: int(t.get("tidal_track_num", 0)))
    return (str(artist_entry["match_id"]), str(album_entry["match_id"]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_planning_merge.py -q && uv run ruff check tidal2ytm/planning_merge.py tests/test_planning_merge.py && uv run pyright`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/planning_merge.py tests/test_planning_merge.py
git commit -m "feat: add pure match/merge helpers for planning TUI"
```

---

### Task 6: Planning TUI with selection session and match action

**Files:**
- Create: `tidal2ytm/planning.py`
- Test: `tests/test_planning.py` (pure helpers + `run_match_action` with fake `yt`/`input_fn`; the key loop is manual-tested like `review.py`)

**Interfaces:**
- Consumes: Task 4 (`search_library`, `parse_tidal_link`, `resolve_*`), Task 5 (merge helpers), `matcher.match_track`, `tidal_source.get_liked_tracks`, `plan_io` (load/save/backup/meta, `find_existing_match`, `update_track_in_plan`).
- Produces: `PlanningSession`, `toggle_select`, `run_match_action`, `run_planning` for Task 7.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tidal2ytm.models import SourceTrack
from tidal2ytm.planning import PlanningSession, run_match_action, toggle_select


def _src(tidal_id: int = 1, title: str = "Aqualung") -> SourceTrack:
    return SourceTrack(
        tidal_id=tidal_id,
        title=title,
        artist="Jethro Tull",
        artists=["Jethro Tull"],
        album="Aqualung",
        album_id=11,
        album_year=1971,
        duration_sec=200,
        isrc=None,
        track_num=1,
        disc_num=1,
        version=None,
    )


def test_toggle_select_adds_and_removes() -> None:
    sel: dict[int, SourceTrack] = {}
    assert toggle_select(sel, _src()) is True
    assert toggle_select(sel, _src()) is False
    assert sel == {}


def test_match_action_confirms_before_matching(tmp_path: Path) -> None:
    from tidal2ytm import planning as planning_mod

    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[_src()], selection={1: _src()}
    )
    with patch.object(planning_mod, "match_track") as mock_match:
        counts = run_match_action(session, MagicMock(), input_fn=lambda _p: "n")
        mock_match.assert_not_called()
        assert counts["matched"] == 0


def test_match_action_adds_new_match(tmp_path: Path) -> None:
    from tidal2ytm import planning as planning_mod
    from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, TrackStatus

    session = PlanningSession(
        plan_path=tmp_path / "transfer_plan.toml", liked=[_src()], selection={1: _src()}
    )
    result = MatchResult(
        source=_src(),
        yt_video_id="AAAAAAAAAAA",
        yt_title="Aqualung",
        yt_artist="Jethro Tull",
        yt_album="Aqualung",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.9),
        status=TrackStatus.PENDING,
    )
    with patch.object(planning_mod, "match_track", return_value=result):
        counts = run_match_action(session, MagicMock(), input_fn=lambda _p: "Y")
    assert counts == {"new": 1, "upgraded": 0, "kept": 0, "skipped": 0}
    assert session.plan_path.exists()


def test_match_action_prompts_on_differing_rematch(tmp_path: Path) -> None:
    from tidal2ytm import planning as planning_mod
    from tidal2ytm import plan_io
    from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, TrackStatus

    plan_path = tmp_path / "transfer_plan.toml"
    src = _src()
    old = MatchResult(
        source=src,
        yt_video_id="AAAAAAAAAAA",
        yt_title="Aqualung",
        yt_artist="Jethro Tull",
        yt_album="Aqualung",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.8),
        status=TrackStatus.PENDING,
    )
    from tidal2ytm.planning_merge import insert_track, match_result_to_track_dict

    plan: dict[str, Any] = {"meta": {}, "artists": []}
    insert_track(plan, match_result_to_track_dict(old), 1971)
    plan_io.update_plan_meta(plan)
    plan_io.save_plan(plan, plan_path)

    new = MatchResult(
        source=src,
        yt_video_id="BBBBBBBBBBB",
        yt_title="Aqualung",
        yt_artist="Jethro Tull",
        yt_album="Aqualung",
        yt_album_track_num=1,
        yt_isrc=None,
        yt_duration_sec=200,
        match_method=MatchMethod.FUZZY,
        confidence=ConfidenceBreakdown(overall=0.95),
        status=TrackStatus.PENDING,
    )
    answers = iter(["Y", "n"])
    session = PlanningSession(plan_path=plan_path, liked=[src], selection={1: src})
    with patch.object(planning_mod, "match_track", return_value=new):
        counts = run_match_action(session, MagicMock(), input_fn=lambda _p: next(answers))
    assert counts["kept"] == 1 and counts["upgraded"] == 0
    kept = plan_io.find_existing_match(plan_io.load_plan(plan_path), 1)
    assert kept is not None and kept["yt_video_id"] == "AAAAAAAAAAA"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_planning.py -q`
Expected: FAIL with collection error.

- [ ] **Step 3: Write the session, helpers, and match action**

```python
"""
planning.py — Interactive planning TUI: search liked tracks, collect a
cross-search selection, and match it to YTM on demand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from .matcher import match_track
from .models import SourceTrack
from .paths import PLAN_FILE
from .plan_io import (
    backup_plan,
    find_existing_match,
    load_plan,
    save_plan,
    update_plan_meta,
    update_track_in_plan,
)
from .planning_merge import (
    classify_track,
    insert_track,
    iter_selection_ordered,
    match_result_to_track_dict,
    unmatched_track_dict,
)


@dataclass
class PlanningSession:
    plan_path: Path
    liked: list[SourceTrack] = field(default_factory=list)
    selection: dict[int, SourceTrack] = field(default_factory=dict)
    override: bool = False
    backup_done: bool = False

    @property
    def by_id(self) -> dict[int, SourceTrack]:
        return {t.tidal_id: t for t in self.liked}


def toggle_select(selection: dict[int, SourceTrack], track: SourceTrack) -> bool:
    """Toggle one track; returns True when the track is now selected."""
    if track.tidal_id in selection:
        del selection[track.tidal_id]
        return False
    selection[track.tidal_id] = track
    return True


```python
def run_match_action(
    session: PlanningSession,
    yt: Any,
    input_fn: Callable[[str], str] = input,
) -> dict[str, int]:
    """Match the session selection into the plan file; returns new/upgraded/kept/skipped counts."""
    console = Console()
    counts = {"new": 0, "upgraded": 0, "kept": 0, "skipped": 0}
    if not session.selection:
        console.print("Nothing selected.")
        return {"new": 0, "upgraded": 0, "kept": 0, "skipped": 0}
    n = len(session.selection)
    answer = input_fn(f"Match {n} selected track{'s' if n != 1 else ''}? [Y/n] ").strip().lower()
    if answer not in ("", "y", "yes"):
        return counts
    plan: dict[str, Any] = load_plan(session.plan_path) if session.plan_path.exists() else {"meta": {}, "artists": []}
    for src in iter_selection_ordered(session.selection):
        existing = find_existing_match(plan, src.tidal_id)
        try:
            result = match_track(src, yt)
            new_vid: str | None = result.yt_video_id
            new_dict: dict[str, Any] | None = match_result_to_track_dict(result)
        except Exception as exc:
            if existing is None:
                insert_track(plan, unmatched_track_dict(src, f"Match error: {exc}"), src.album_year)
                counts["new"] += 1
            else:
                counts["kept"] += 1
            continue
        assert new_dict is not None
        action = classify_track(existing, new_vid)
        if action == "skip-transferred":
            counts["skipped"] += 1
        elif action == "add-new":
            insert_track(plan, new_dict, src.album_year)
            counts["new"] += 1
        elif action == "keep-same":
            counts["kept"] += 1
        elif session.override:
            assert existing is not None
            update_track_in_plan(plan, src.tidal_id, new_dict)
            counts["upgraded"] += 1
        else:
            old_vid = str((existing or {}).get("yt_video_id", ""))
            old_method = str((existing or {}).get("match_method", "none"))
            console.print(
                f"Better match for '{src.title}' (was: {old_method} {old_vid}). Overwrite? [y/N] "
            )
            overwrite = input_fn("Overwrite? [y/N] ").strip().lower() in ("y", "yes")
            if overwrite:
                assert existing is not None
                update_track_in_plan(plan, src.tidal_id, new_dict)
                counts["upgraded"] += 1
            else:
                counts["kept"] += 1
    if not session.backup_done and session.plan_path.exists():
        bpath = backup_plan(session.plan_path)
        console.print(f"Backup -> {bpath.name}")
        session.backup_done = True
    update_plan_meta(plan)
    save_plan(plan, session.plan_path)
    console.print(
        f"Matched: {counts['new']} new, {counts['upgraded']} upgraded, "
        f"{counts['kept']} kept, {counts['skipped']} skipped (transferred)"
    )
    return counts


def run_planning(*, tidal_session: Any, plan_path: Path = PLAN_FILE) -> None:
    """TUI entry point: fetch liked tracks once, then loop search/select/review/match."""
    from .tidal_source import get_liked_tracks

    console = Console()
    console.print("Fetching Tidal liked tracks...")
    liked = get_liked_tracks(tidal_session)
    console.print(f"Found {len(liked)} tracks.")
    session = PlanningSession(plan_path=plan_path, liked=liked)
    _tui_loop(console, session)
```

`_tui_loop` follows the `review.py` input pattern (`readchar` raw mode when a TTY, line-buffered fallback otherwise) with this keymap: `e` select everything, `/` search (prompt mode, then query; auto-detect Tidal links via `parse_tidal_link` and resolve instead of ranking), `v` review selection (list grouped with per-row deselect, `c` clear-all), `m` match via `run_match_action` (YTM login lazily inside: `from .cli import _ytm_login; yt = _ytm_login()` on first confirmed run, failures print the existing re-auth guidance and return to the loop), `ctrl+o` (`"\x0f"`) toggles override with a persistent `OVERRIDE: existing matches will be overwritten` banner in the header, `q` quit. Results render Tidal-side fields only. Keep the loop under the same manual-test standard as `review.py`; no unit tests for the loop itself.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_planning.py -q && uv run ruff check tidal2ytm/planning.py tests/test_planning.py && uv run pyright`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/planning.py tests/test_planning.py
git commit -m "feat: add interactive planning TUI with on-demand matching"
```

---

### Task 7: Remove plan command, wire bare invocation, update strings and CLI tests

**Files:**
- Modify: `tidal2ytm/cli.py`, `tidal2ytm/review.py`, `tidal2ytm/transfer.py`, `tests/test_cli.py`
- Delete: `tidal2ytm/plan.py`, `tests/test_plan.py`

**Interfaces:**
- Consumes: Task 6 (`planning.run_planning`).
- Produces: CLI where bare `tidal2ytm` plans, `plan` is rejected, everything else is untouched.

- [ ] **Step 1: Update the failing tests first**

In `tests/test_cli.py`: delete `test_cli_plan_force_flag`; in `test_cli_help_and_subcommand_help` remove the `["tidal2ytm", "plan", "--help"]` entry; append:

```python
def test_cli_bare_invokes_planning(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm"])
    with (
        patch("tidal2ytm.cli._tidal_login", return_value=MagicMock()),
        patch("tidal2ytm.planning.run_planning") as mock_p,
    ):
        cli_mod.main()
        mock_p.assert_called_once()


def test_cli_plan_removed(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["tidal2ytm", "plan"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL (`run_planning` missing, `plan` still parses).

- [ ] **Step 3: Edit the CLI**

In `tidal2ytm/cli.py`: delete `cmd_plan` and the `p_plan` parser block; change `sub = parser.add_subparsers(dest="command", required=True)` to `required=False`; add after `args = parser.parse_args()`:

```python
if args.command is None:
    from .planning import run_planning

    run_planning(tidal_session=_tidal_login(), plan_path=PLAN_FILE)
    return
args.func(args)
```

Add:

```python
def cmd_planning() -> None:
    from .planning import run_planning

    run_planning(tidal_session=_tidal_login(), plan_path=PLAN_FILE)
```

(Use either the inline block or `cmd_planning`; one path only.) In `cmd_status`, replace `Run [bold]tidal2ytm plan[/bold] first.` with `Run [bold]tidal2ytm[/bold] to build one first.` In `review.py:run_review` and `transfer.py:run_transfer`, replace `Run [bold]tidal2ytm plan[/bold] first.` with `Run [bold]tidal2ytm[/bold] to build one first.` Delete `tidal2ytm/plan.py` and `tests/test_plan.py` via `git rm`.

- [ ] **Step 4: Run tests and gates**

Run: `rg -n "run_plan|from .plan|from tidal2ytm.plan|tidal2ytm plan" tidal2ytm tests README.md AGENTS.md` (use `Select-String` if `rg` is unavailable) and clear every hit except this plan file, the spec, and historical changelog text if any. Then run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run pyright`
Expected: PASS, zero stale references.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: replace plan command with interactive planning TUI"
```

---

### Task 8: Docs and final verification

**Files:**
- Modify: `README.md`, `AGENTS.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: consistent user-facing workflow docs.

- [ ] **Step 1: Update the workflow docs**

Read `README.md` and `AGENTS.md` fully, then replace every `tidal2ytm plan` reference: planning is now bare `tidal2ytm` (search, select, review, then the in-TUI match action); `transfer`, `review`, `status`, `auth` are unchanged; the review TUI key map note stays. Keep the OAuth setup and authenticate sections untouched.

- [ ] **Step 2: Final gates plus smoke test**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q && uv run pytest --cov --cov-report=term-missing -q && uv run tidal2ytm --help`
Expected: PASS throughout; `--help` lists `transfer`, `review`, `status`, `auth` and no `plan`.

- [ ] **Step 3: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: planning TUI workflow without plan command"
```

---

## Self-review

**Spec coverage:** Search modes and two-tier ranking plus cutoff (Task 4); Tidal-link paste with track-exact and album-expansion rules (Task 4); cross-search selection with deselect review (Task 6); explicit match with `[Y/n]`, silent-same, per-track `[y/N]`, `ctrl+o` visible override (Tasks 5, 6); transferred always skipped (Tasks 5, 6); `plan` removal with untouched siblings (Task 7); TOML shape, backup, meta (Tasks 5, 6); composer spike with title+version fallback (Tasks 2, 3); dep upgrades recorded (Task 1). No gaps.

**Placeholder scan:** Every step names exact files, commands, and expected outputs; test and implementation bodies are written out; the single conditional (Task 3 on Task 2) carries an explicit decision rule and both branches; the README edit names the exact substitution mapping.

**Type consistency:** `SourceTrack` gains one optional field used by `_haystacks` only when present; `PlanningSession` fields match every constructor call in tests; `run_match_action` returns `dict[str, int]` with exactly `new/upgraded/kept/skipped` keys asserted in tests; `insert_track` returns `(artist_match_id, album_match_id)` as asserted.
