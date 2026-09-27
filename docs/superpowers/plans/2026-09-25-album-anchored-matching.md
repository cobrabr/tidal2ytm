# Album-Anchored Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-query per-track matching with album-grouped resolution that maps each source album group onto one target album before scoring tracks inside it.

**Architecture:** New pure helpers for qualifier-aware text and track fit scoring, a new album-resolution orchestrator with an album-identifier cache and inter-call delay, a slimmed per-track fallback in the existing matcher, and grouped wiring in the planning action with coherence display in review.

**Tech Stack:** Python 3.11, ytmusicapi search plus album-detail fetch, pytest with coverage floor eighty percent, ruff, pyright strict.

**Spec:** `docs/superpowers/specs/2026-09-23-album-anchored-matching-design.md`

## Global Constraints

- Every module begins with `from __future__ import annotations`.
- Data containers are dataclasses and public functions are type-annotated.
- `sys.exit` lives only in `cli.main` plus interactive auth-entry paths; library runners raise `errors.Tidal2YtmError` subclasses.
- Catch only the narrow `_AUTH_ERRORS` tuple around auth probes; transport errors propagate.
- No `assert` for user-input validation; narrow post-condition asserts only.
- Plan writes stay atomic with one metadata recomputation per match action, never per-track rewrite loops.
- No new `C901 noqa`; split functions instead.
- One owner per constant; new thresholds live beside the scorer that uses them.
- Tests use fictional names and synthetic identifiers only, assert behaviour structurally, and cover threshold edges rather than locking values.

## Review Focus

- Empty selection returns zero counts without network calls, as today.
- Source album group with unknown year resolves without year penalty rather than failing.
- Source track with zero duration skips the duration gate rather than rejecting everything.
- Compilation source album with multiple distinct artists still groups by album identifier and scores without artist-collapse.
- Album search returning only singles or EPs still ranks and either resolves or abstains with an explicit reason rather than crashing.

---

### Task 1: Qualifier-aware text helpers

**Files:**
- Create: `tidal2ytm/version_tokens.py`
- Test: `tests/test_version_tokens.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `split_base_qualifiers(text: str) -> tuple[str, tuple[str, ...]]`, `qualifier_similarity(source: tuple[str, ...], target: tuple[str, ...]) -> float`, used by Task 2 and Task 4.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from tidal2ytm.version_tokens import qualifier_similarity, split_base_qualifiers


def test_split_base_qualifiers() -> None:
    base, quals = split_base_qualifiers("Ember Fall (Live) [Remaster]")
    assert base == "Ember Fall"
    assert "live" in quals
    assert "remaster" in quals


def test_qualifier_similarity_empty_vs_present() -> None:
    assert qualifier_similarity((), ("live",)) < 1.0


def test_qualifier_similarity_same_set() -> None:
    assert qualifier_similarity(("live",), ("live",)) == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_version_tokens.py -v`
Expected: FAIL with "No module named tidal2ytm.version_tokens" or "function not defined".

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_version_tokens.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/version_tokens.py tests/test_version_tokens.py
git commit -m "feat: add qualifier-aware text helpers"
```

### Task 2: Track fit scorer with relative duration gate

**Files:**
- Create: `tidal2ytm/track_scoring.py`
- Test: `tests/test_track_scoring.py`

**Interfaces:**
- Consumes: `split_base_qualifiers`, `qualifier_similarity` from Task 1; `SourceTrack` from `tidal2ytm/models.py`; `similarity` from `tidal2ytm/text.py`.
- Produces: `DURATION_ABS_TOL_SEC: int`, `DURATION_REL_TOL: float`, `duration_ok(source_sec: int, candidate_sec: int | None) -> bool`, `score_track_fit(source: SourceTrack, candidate_title: str, candidate_artists: list[str], candidate_album: str, candidate_duration_sec: int | None, candidate_track_num: int | None) -> TrackFit`, `TrackFit` dataclass with `overall: float`, `title_similarity: float`, `artist_similarity: float`, `album_similarity: float`, `version_similarity: float`, `track_num_match: bool`, `duration_delta_sec: int | None`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from tidal2ytm.models import SourceTrack
from tidal2ytm.track_scoring import duration_ok, score_track_fit


def _source() -> SourceTrack:
    return SourceTrack.from_dict(
        {
            "tidal_id": 31,
            "album_id": 7,
            "title": "Ember Fall",
            "artists": ["Vesper Vale"],
            "album": "Ashen Light",
            "duration": 209,
            "track_num": 4,
        }
    )


def test_duration_ok_relative_gate() -> None:
    assert duration_ok(209, 213) is True
    assert duration_ok(209, 300) is False
    assert duration_ok(0, 300) is True


def test_score_prefers_exact_position() -> None:
    src = _source()
    near = score_track_fit(src, "Ember Fall", ["Vesper Vale"], "Ashen Light", 209, 4)
    far = score_track_fit(src, "Ember Fall", ["Vesper Vale"], "Ashen Light", 209, 9)
    assert near.overall > far.overall
    assert near.track_num_match is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_track_scoring.py -v`
Expected: FAIL with import or missing-function error.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

from dataclasses import dataclass

from .models import SourceTrack
from .text import similarity
from .version_tokens import qualifier_similarity, split_base_qualifiers

DURATION_ABS_TOL_SEC = 4
DURATION_REL_TOL = 0.02


@dataclass
class TrackFit:
    overall: float
    title_similarity: float
    artist_similarity: float
    album_similarity: float
    version_similarity: float
    track_num_match: bool
    duration_delta_sec: int | None


def duration_ok(source_sec: int, candidate_sec: int | None) -> bool:
    if source_sec <= 0:
        return True
    if candidate_sec is None:
        return False
    delta = abs(candidate_sec - source_sec)
    return delta <= max(DURATION_ABS_TOL_SEC, int(source_sec * DURATION_REL_TOL))


def _artist_sim(source_artists: list[str], candidate_artists: list[str]) -> float:
    if not source_artists or not candidate_artists:
        return 0.0
    scores = [similarity(s, c) for s in source_artists for c in candidate_artists]
    return max(scores) if scores else 0.0


def score_track_fit(
    source: SourceTrack,
    candidate_title: str,
    candidate_artists: list[str],
    candidate_album: str,
    candidate_duration_sec: int | None,
    candidate_track_num: int | None,
) -> TrackFit:
    src_base, src_quals = split_base_qualifiers(source.title)
    if source.version:
        src_quals = (*src_quals, source.version.casefold())
    cand_base, cand_quals = split_base_qualifiers(candidate_title)
    title_sim = similarity(src_base, cand_base)
    version_sim = qualifier_similarity(src_quals, cand_quals)
    artist_sim = _artist_sim(source.artists or [source.artist], candidate_artists)
    album_sim = similarity(source.album, candidate_album) if candidate_album else 0.0
    delta: int | None = None
    if candidate_duration_sec is not None:
        delta = abs(candidate_duration_sec - source.duration_sec)
    pos_match = (
        source.track_num > 0
        and candidate_track_num is not None
        and source.track_num == candidate_track_num
    )
    overall = title_sim * 0.45 + artist_sim * 0.2 + album_sim * 0.2 + version_sim * 0.15
    if pos_match:
        overall = min(1.0, overall + 0.08)
    return TrackFit(
        overall=overall,
        title_similarity=title_sim,
        artist_similarity=artist_sim,
        album_similarity=album_sim,
        version_similarity=version_sim,
        track_num_match=pos_match,
        duration_delta_sec=delta,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_track_scoring.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/track_scoring.py tests/test_track_scoring.py
git commit -m "feat: add track fit scorer with relative duration gate"
```

### Task 3: Model extension for album-anchored results

**Files:**
- Modify: `tidal2ytm/models.py`
- Modify: `tidal2ytm/planning_merge.py`
- Modify: `tidal2ytm/plan_io.py`
- Test: `tests/test_planning_merge.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `MatchMethod.ALBUM = "album"`; `ConfidenceBreakdown` gains `version_similarity: float | None`, `track_num_match: bool | None`, `album_coherence: float | None`; plan header documents the new method; track-dict conversion round-trips the new fields.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from tidal2ytm.models import ConfidenceBreakdown, MatchMethod, MatchResult, SourceTrack
from tidal2ytm.planning_merge import match_result_to_track_dict


def test_album_method_round_trip() -> None:
    src = SourceTrack.from_dict(
        {"tidal_id": 31, "album_id": 7, "title": "Ember Fall", "artists": ["Vesper Vale"]}
    )
    res = MatchResult(
        source=src,
        yt_video_id="AAAAAAAAAAA",
        yt_title="Ember Fall",
        yt_artist="Vesper Vale",
        yt_album="Ashen Light",
        yt_album_track_num=4,
        yt_isrc=None,
        yt_duration_sec=209,
        match_method=MatchMethod.ALBUM,
        confidence=ConfidenceBreakdown(
            overall=0.91, version_similarity=1.0, track_num_match=True, album_coherence=1.0
        ),
        status="pending",
    )
    d = match_result_to_track_dict(res)
    assert d["match_method"] == "album"
    assert d["confidence"]["version_similarity"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_planning_merge.py -v`
Expected: FAIL with missing `ALBUM` attribute or missing confidence keys.

- [ ] **Step 3: Write minimal implementation**

```python
# models.py addition inside MatchMethod:
#     ALBUM = "album"
# ConfidenceBreakdown additions:
#     version_similarity: float | None = None
#     track_num_match: bool | None = None
#     album_coherence: float | None = None
```

```python
# planning_merge.py: extend confidence serialization with:
#     if conf.version_similarity is not None:
#         conf_dict["version_similarity"] = conf.version_similarity
#     if conf.track_num_match is not None:
#         conf_dict["track_num_match"] = conf.track_num_match
#     if conf.album_coherence is not None:
#         conf_dict["album_coherence"] = conf.album_coherence
```

```python
# plan_io.py: update _PLAN_HEADER match_method line to:
# # match_method: isrc | duration | fuzzy | album | none
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_planning_merge.py tests/test_plan_io.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/models.py tidal2ytm/planning_merge.py tidal2ytm/plan_io.py tests/test_planning_merge.py
git commit -m "feat: add album match method and confidence fields"
```

### Task 4: Album grouping and resolution orchestrator

**Files:**
- Create: `tidal2ytm/album_matching.py`
- Test: `tests/test_album_matching.py`

**Interfaces:**
- Consumes: `score_track_fit`, `duration_ok` from Task 2; `SourceTrack`, `MatchResult`, `MatchMethod`, `ConfidenceBreakdown`, `TrackStatus` from models; `similarity` from text.
- Produces: `ALBUM_SEARCH_LIMIT: int`, `ALBUM_FETCH_LIMIT: int`, `ALBUM_MARGIN_EPS: float`, `group_by_album_id(tracks: list[SourceTrack]) -> dict[int, list[SourceTrack]]`, `resolve_album_group(sources: list[SourceTrack], yt: object, cache: dict[str, dict[str, object]], delay: float = 0.3) -> list[MatchResult]`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from tidal2ytm.album_matching import group_by_album_id, resolve_album_group
from tidal2ytm.models import SourceTrack


def _src(tidal_id: int, album_id: int, title: str, num: int) -> SourceTrack:
    return SourceTrack.from_dict(
        {
            "tidal_id": tidal_id,
            "album_id": album_id,
            "title": title,
            "artists": ["Vesper Vale"],
            "album": "Ashen Light",
            "duration": 200,
            "track_num": num,
        }
    )


def test_groups_are_independent() -> None:
    tracks = [_src(1, 7, "Ember Fall", 1), _src(2, 9, "Cinder Hymn", 1)]
    groups = group_by_album_id(tracks)
    assert set(groups) == {7, 9}


def test_unknown_year_resolves_without_penalty() -> None:
    group_a = [_src(1, 7, "Shared Hymn", 1)]
    yt = MagicMock()
    yt.search.return_value = [
        {"browseId": "MPRE_A", "title": "Ashen Light", "artist": "Vesper Vale", "year": ""},
    ]
    yt.get_album.return_value = {
        "title": "Ashen Light",
        "tracks": [
            {"videoId": "AAAAAAAAAAA", "title": "Shared Hymn", "artists": [{"name": "Vesper Vale"}], "duration_seconds": 200},
        ],
    }
    results = resolve_album_group(group_a, yt, {}, delay=0.0)
    assert results[0].yt_video_id == "AAAAAAAAAAA"


def test_overlapping_groups_do_not_collapse() -> None:
    group_a = [_src(1, 7, "Shared Hymn", 1), _src(2, 7, "Only In A", 2)]
    yt = MagicMock()
    yt.search.return_value = [
        {"browseId": "MPRE_A", "title": "Ashen Light", "artist": "Vesper Vale", "year": "2001"},
        {"browseId": "MPRE_B", "title": "Quartz Sea", "artist": "Vesper Vale", "year": "2005"},
    ]
    yt.get_album.side_effect = [
        {
            "title": "Ashen Light",
            "tracks": [
                {"videoId": "AAAAAAAAAAA", "title": "Shared Hymn", "artists": [{"name": "Vesper Vale"}], "duration_seconds": 200},
                {"videoId": "BBBBBBBBBBB", "title": "Only In A", "artists": [{"name": "Vesper Vale"}], "duration_seconds": 200},
            ],
        },
        {
            "title": "Quartz Sea",
            "tracks": [
                {"videoId": "CCCCCCCCCCC", "title": "Shared Hymn", "artists": [{"name": "Vesper Vale"}], "duration_seconds": 200},
            ],
        },
    ]
    results = resolve_album_group(group_a, yt, {}, delay=0.0)
    assert {r.yt_video_id for r in results} == {"AAAAAAAAAAA", "BBBBBBBBBBB"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_album_matching.py -v`
Expected: FAIL with missing module.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

import time
from typing import Any

from .models import ConfidenceBreakdown, MatchMethod, MatchResult, SourceTrack, TrackStatus
from .text import similarity
from .track_scoring import duration_ok, score_track_fit

ALBUM_SEARCH_LIMIT = 10
ALBUM_FETCH_LIMIT = 3
ALBUM_MARGIN_EPS = 0.05


def group_by_album_id(tracks: list[SourceTrack]) -> dict[int, list[SourceTrack]]:
    groups: dict[int, list[SourceTrack]] = {}
    for t in tracks:
        groups.setdefault(t.album_id, []).append(t)
    return groups


def _album_meta_score(source: SourceTrack, candidate: dict[str, Any]) -> float:
    title_sim = similarity(source.album, str(candidate.get("title", "")))
    artist_sim = similarity(source.artist, str(candidate.get("artist", "")))
    year_score = 0.5
    try:
        cand_year = int(str(candidate.get("year", "")))
        if source.album_year is not None:
            year_score = 1.0 / (1.0 + abs(cand_year - source.album_year))
    except (TypeError, ValueError):
        year_score = 0.5
    return title_sim * 0.55 + artist_sim * 0.3 + year_score * 0.15


def resolve_album_group(
    sources: list[SourceTrack],
    yt: Any,
    cache: dict[str, Any],
    delay: float = 0.3,
) -> list[MatchResult]:
    first = sources[0]
    query = f"{first.album} {first.artist}".strip()
    album_cands: list[dict[str, Any]] = yt.search(query, filter="albums", limit=ALBUM_SEARCH_LIMIT)
    album_cands = album_cands[:ALBUM_FETCH_LIMIT]
    details: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for cand in album_cands:
        browse_id = str(cand.get("browseId", ""))
        if not browse_id:
            continue
        if browse_id not in cache:
            cache[browse_id] = yt.get_album(browse_id)
            if delay > 0:
                time.sleep(delay)
        details.append((cand, cache[browse_id]))
    if not details:
        return [
            MatchResult(
                source=s,
                yt_video_id=None,
                yt_title=None,
                yt_artist=None,
                yt_album=None,
                yt_album_track_num=None,
                yt_isrc=None,
                yt_duration_sec=None,
                match_method=MatchMethod.NONE,
                confidence=ConfidenceBreakdown(overall=0.0, summary="No album resolve"),
                status=TrackStatus.NEEDS_REVIEW,
                review_reason="No album resolve",
            )
            for s in sources
        ]
    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for cand, detail in details:
        meta = _album_meta_score(first, cand)
        fits: list[float] = []
        for s in sources:
            best = 0.0
            for t in detail.get("tracks", []):
                fit = score_track_fit(
                    s,
                    str(t.get("title", "")),
                    [a.get("name", "") for a in t.get("artists", [])],
                    str(detail.get("title", "")),
                    t.get("duration_seconds"),
                    t.get("trackNumber"),
                )
                if not duration_ok(s.duration_sec, t.get("duration_seconds")):
                    continue
                best = max(best, fit.overall)
            fits.append(best)
        scored.append((meta * 0.4 + (sum(fits) / len(fits) if fits else 0.0) * 0.6, cand, detail))
    scored.sort(key=lambda row: row[0], reverse=True)
    if len(scored) > 1 and (scored[0][0] - scored[1][0]) < ALBUM_MARGIN_EPS:
        return [
            MatchResult(
                source=s,
                yt_video_id=None,
                yt_title=None,
                yt_artist=None,
                yt_album=None,
                yt_album_track_num=None,
                yt_isrc=None,
                yt_duration_sec=None,
                match_method=MatchMethod.NONE,
                confidence=ConfidenceBreakdown(overall=scored[0][0], summary="Close album call"),
                status=TrackStatus.NEEDS_REVIEW,
                review_reason="Close album call",
            )
            for s in sources
        ]
    _, _, winner = scored[0]
    winner_tracks: list[dict[str, Any]] = winner.get("tracks", [])
    results: list[MatchResult] = []
    for s in sources:
        best_fit = None
        best_row: dict[str, Any] | None = None
        for t in winner_tracks:
            fit = score_track_fit(
                s,
                str(t.get("title", "")),
                [a.get("name", "") for a in t.get("artists", [])],
                str(winner.get("title", "")),
                t.get("duration_seconds"),
                t.get("trackNumber"),
            )
            if not duration_ok(s.duration_sec, t.get("duration_seconds")):
                continue
            if best_fit is None or fit.overall > best_fit.overall:
                best_fit = fit
                best_row = t
        if best_fit is None or best_row is None:
            results.append(
                MatchResult(
                    source=s,
                    yt_video_id=None,
                    yt_title=None,
                    yt_artist=None,
                    yt_album=None,
                    yt_album_track_num=None,
                    yt_isrc=None,
                    yt_duration_sec=None,
                    match_method=MatchMethod.NONE,
                    confidence=ConfidenceBreakdown(overall=0.0, summary="No in-album fit"),
                    status=TrackStatus.NEEDS_REVIEW,
                    review_reason="No in-album fit",
                )
            )
            continue
        results.append(
            MatchResult(
                source=s,
                yt_video_id=best_row.get("videoId"),
                yt_title=best_row.get("title"),
                yt_artist=(best_row.get("artists") or [{}])[0].get("name"),
                yt_album=winner.get("title"),
                yt_album_track_num=best_row.get("trackNumber"),
                yt_isrc=None,
                yt_duration_sec=best_row.get("duration_seconds"),
                match_method=MatchMethod.ALBUM,
                confidence=ConfidenceBreakdown(
                    overall=best_fit.overall,
                    title_similarity=best_fit.title_similarity,
                    artist_similarity=best_fit.artist_similarity,
                    album_similarity=best_fit.album_similarity,
                    duration_delta_sec=best_fit.duration_delta_sec,
                    version_similarity=best_fit.version_similarity,
                    track_num_match=best_fit.track_num_match,
                    album_coherence=1.0,
                    summary=(
                        f"title={best_fit.title_similarity:.2f}, "
                        f"artist={best_fit.artist_similarity:.2f}, "
                        f"album={best_fit.album_similarity:.2f}"
                    ),
                ),
                status=TrackStatus.PENDING,
            )
        )
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_album_matching.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/album_matching.py tests/test_album_matching.py
git commit -m "feat: add album grouping and resolution orchestrator"
```

### Task 5: Grouped planning integration with fallback

**Files:**
- Modify: `tidal2ytm/matcher.py`
- Modify: `tidal2ytm/planning.py`
- Test: `tests/test_planning.py`

**Interfaces:**
- Consumes: `group_by_album_id`, `resolve_album_group` from Task 4; existing `match_track(track, yt) -> MatchResult` stays as the flagged fallback.
- Produces: `run_match_action` groups the selection by album identifier, resolves each group once with a shared album cache, and routes no-fit rows through the existing per-track path as `needs_review`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from unittest.mock import MagicMock

from tidal2ytm.models import SourceTrack
from tidal2ytm.planning import PlanningSession, run_match_action


def test_match_action_groups_by_album(tmp_path) -> None:
    liked = [
        SourceTrack.from_dict(
            {"tidal_id": 1, "album_id": 7, "title": "Ember Fall", "artists": ["Vesper Vale"], "album": "Ashen Light", "duration": 200, "track_num": 1}
        ),
        SourceTrack.from_dict(
            {"tidal_id": 2, "album_id": 7, "title": "Cinder Hymn", "artists": ["Vesper Vale"], "album": "Ashen Light", "duration": 200, "track_num": 2}
        ),
    ]
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=liked)
    session.selection = {t.tidal_id: t for t in liked}
    session.library_loaded = True
    yt = MagicMock()
    yt.search.return_value = [{"browseId": "MPRE_A", "title": "Ashen Light", "artist": "Vesper Vale", "year": "2001"}]
    yt.get_album.return_value = {
        "title": "Ashen Light",
        "tracks": [
            {"videoId": "AAAAAAAAAAA", "title": "Ember Fall", "artists": [{"name": "Vesper Vale"}], "duration_seconds": 200, "trackNumber": 1},
            {"videoId": "BBBBBBBBBBB", "title": "Cinder Hymn", "artists": [{"name": "Vesper Vale"}], "duration_seconds": 200, "trackNumber": 2},
        ],
    }
    counts = run_match_action(session, yt=yt, input_fn=lambda _: "y")
    assert counts["new"] == 2
    assert yt.get_album.call_count == 1


def test_match_action_empty_selection_no_network(tmp_path) -> None:
    session = PlanningSession(plan_path=tmp_path / "transfer_plan.toml", liked=[])
    session.selection = {}
    session.library_loaded = True
    yt = MagicMock()
    counts = run_match_action(session, yt=yt, input_fn=lambda _: "y")
    assert counts == {"new": 0, "upgraded": 0, "kept": 0, "skipped": 0}
    yt.search.assert_not_called()
    yt.get_album.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_planning.py -v`
Expected: FAIL because the action still calls per-track search twice and never calls `get_album`.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

from typing import Any

from .album_matching import group_by_album_id, resolve_album_group
from .matcher import match_track
```

```python
ordered = iter_selection_ordered(session.selection)
album_cache: dict[str, Any] = {}
groups = group_by_album_id(ordered)
total = len(ordered)
done = 0
for group in groups.values():
    for src in group:
        done += 1
        console.print(_match_tag(done, total, src))
    for result in resolve_album_group(group, yt, album_cache):
        existing = find_existing_match(plan, result.source.tidal_id)
        if result.yt_video_id is None:
            fallback = match_track(result.source, yt)
            new_dict = match_result_to_track_dict(fallback)
        else:
            new_dict = match_result_to_track_dict(result)
        _apply_match_action(
            plan, result.source, existing, new_dict, counts, session.override, ask, console, indent="  "
        )
```

```python
from .track_scoring import duration_ok, score_track_fit

fit = score_track_fit(track, c_title, [c_artist], c_album, c_dur, c_track_num)
if not duration_ok(track.duration_sec, c_dur):
    continue
conf = fit.overall
```

The planning loop above replaces the existing `for i, src in enumerate(ordered, 1)` block in `run_match_action`. The scorer snippet replaces the inline `dur_delta`, `gate_ok`, `title_sim`, `artist_sim`, `base_conf`, and `conf` lines in `match_track`, keeping the `match_track(track, yt) -> MatchResult` signature and the surrounding best-candidate bookkeeping unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_planning.py tests/test_album_matching.py tests/test_matcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/planning.py tidal2ytm/matcher.py tests/test_planning.py
git commit -m "feat: match grouped by album with per-track fallback"
```

### Task 6: Review coherence display and full gates

**Files:**
- Modify: `tidal2ytm/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `album_coherence` and `match_method == "album"` from plan rows.
- Produces: `album_group_header(tidal_album: str, yt_album: str, coherence: float, mean_conf: float) -> Text` rendered above each review album block.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from tidal2ytm.review import album_group_header


def test_album_group_header_shows_coherence() -> None:
    text = album_group_header("Ashen Light", "Ashen Light", 1.0, 0.91)
    assert "1.0" in text.plain or "100" in text.plain
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_review.py -v`
Expected: FAIL with missing function.

- [ ] **Step 3: Write minimal implementation**

```python
from rich.text import Text


def album_group_header(tidal_album: str, yt_album: str, coherence: float, mean_conf: float) -> Text:
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append(str(tidal_album or ""), style="bold")
    line.append(" -> ")
    line.append(str(yt_album or ""), style="underline")
    line.append(f"  ({coherence:.0%} coherent, mean {mean_conf:.2f})", style="dim")
    return line
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_review.py -v`
Expected: PASS. Then run full gates: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest --cov --cov-report=term-missing -q`.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/review.py tests/test_review.py
git commit -m "feat: show album coherence in review"
```
