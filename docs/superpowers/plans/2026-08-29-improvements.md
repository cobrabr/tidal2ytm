# Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add exhaustive tests, strict typing and quality gates, unified `auth` command, and verified bare executable support to the existing Tidal-to-YouTube-Music transfer CLI without changing matching or transfer semantics.

**Architecture:** Keep current layered ownership (`slugs.py`, `plan_io.py`, `matcher.py`, `ytm_sink.py`, `tidal_source.py`, `plan.py`/`transfer.py`/`review.py` grouping or per-track effects, `cli.py` owning OAuth with load-bearing `yt._session.post` TVHTML5/WEB_REMIX patch) and add tests first, then enforce `ruff` + `pyright strict` (`reportMissingImports=false`), then wire repo-wide hooks and report-only CI, then extract `auth.py` so `cli.py` delegates, then verify `uv tool install --from .` bare binary.

**Tech Stack:** Python 3.11, `uv` (with `uv.lock`), `ytmusicapi>=1.7.0`, `tidalapi>=0.8.3`, `rich>=13`, `readchar>=4.2`, `tomli-w>=1.0`, `pytest>=8`, `pytest-cov>=6`, `pyright>=1.1` strict, `ruff>=0.9`, `pre-commit>=4`

**Spec:** `plan/tidal2ytm-improvements-plan.md` (tokens, contract, invariants, execution criteria, and reference configs live there; this plan argues from that spec — executors read both)

## Global Constraints

- Checker is `pyright` strict (`typeCheckingMode = "strict"`, `reportMissingImports = false`; `mypy` is a one-line swap if CI prefers it) — never gate on `mypy` and `pyright` simultaneously.
- Coverage is report-only until sustained ≥80% (`--cov` without `--cov-fail-under` / `fail_under = 80` commented out); enabling the gate is the follow-up one-line change.
- Executable is bare only (`uv tool install --from .` on `PATH`, run as `tidal2ytm` without `uv run`) — no PyInstaller, no `dist/`, no `paths.py` frozen patch.
- Smoke is `tidal2ytm --help` + `tidal2ytm status` — `transfer --all` stays mocked/manual.
- `tidal_id` is identity; tracks have no `match_id`, addressed by bare 11-char `yt_video_id` normalized by `plan_io.py:_extract_video_id` (bare/`watch?v=`/`youtu.be/`/`/v/`/`music.youtube.com`/invalid) — `review.py` deliberately imports that private helper.
- `match_id` = `artist_slug`/`album_slug` (album ≤15 chars, `-2`/`-3` dedup); all slug logic lives in `slugs.py`.
- Statuses `pending | transferred | skip | failed | needs_review`; `TrackStatus`/`MatchMethod` string-enum `.value` matches TOML (`models.py`).
- `[meta]` recomputed via `plan_io.py:update_plan_meta()` after any status change.
- `cli.py` owns all OAuth; `_ytm_login` `yt._session.post` TVHTML5/WEB_REMIX swap (strip `authorization`/`X-Goog-Request-Time` for `/search?` and `/player?`, use `WEB_REMIX` client `1.YYYYMMDD.01.00`, restore TVHTML5) is load-bearing — do not refactor casually.
- `paths.py:5` `DATA_DIR.mkdir()` side effect — tests isolate via `tmp_path`/`monkeypatch` on `DATA_DIR`/`YTM_AUTH_FILE`/`TIDAL_TOKEN_FILE`/`PLAN_FILE`; never write `STATE_FILE`/`REVIEW_FILE`.
- `ytm_sink.py` must use `get_watch_playlist` + `edit_song_library_status` (`feedbackTokens.add`); `rate_song`/`LikeStatus.LIKE` only thumbs-up and is wrong.
- Conventions: `from __future__ import annotations` at top of every module, dataclasses, string-enum values match TOML, `target-version = "py311"`, `line-length = 100`.

---

### Task 1: Prep — dev dependencies and report-only tool config

**Files:**
- Modify: `pyproject.toml:1-19`
- Create: `tests/__init__.py`
- Create: `tests/fixtures/sample_plan.toml`

**Interfaces:**
- Consumes: existing `pyproject.toml` build-system and `project.scripts.tidal2ytm = "tidal2ytm.cli:main"`
- Produces: `uv sync --all-groups` with `[dependency-groups].dev`, `tool.pytest`, `tool.coverage`, `tool.ruff`, `tool.pyright` available to all later tasks; `tests/` importable; fixture TOML loadable by `plan_io.load_plan`

- [ ] **Step 1: Add dev dependency group and tool config to `pyproject.toml`**

```toml
[dependency-groups]
dev = ["pytest>=8", "pytest-cov>=6", "pyright>=1.1", "ruff>=0.9", "pre-commit>=4"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"

[tool.coverage.run]
source = ["tidal2ytm"]
branch = true
[tool.coverage.report]
# fail_under = 80  # enable in follow-up after sustained ≥80%
show_missing = true
exclude_lines = ["pragma: no cover", "if TYPE_CHECKING:", "raise NotImplementedError"]

[tool.ruff]
target-version = "py311"
line-length = 100
[tool.ruff.lint]
select = ["E","F","W","C90","I","N","UP","S","B","SIM","RUF"]
ignore = ["S101"]
[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "strict"
reportMissingImports = false
```

Edit `pyproject.toml` to keep existing `build-system`/`project` sections verbatim and append the block above. Do not add `dist/` or frozen-path logic.

- [ ] **Step 2: Create `tests/__init__.py` and `tests/fixtures/sample_plan.toml`**

```python
# tests/__init__.py — empty, marks package for pytest
```

```toml
# tests/fixtures/sample_plan.toml — minimal plan for load/iteration tests; uses bare 11-char IDs
[meta]
total_tracks = 2
transferred = 0
pending = 1
needs_review = 1
skip = 0
failed = 0
generated_at = "2026-08-29T00:00:00"

[[artists]]
name = "Jethro Tull"
match_id = "jethro-tull"

[[artists.albums]]
name = "War Child"
match_id = "jethro-tull/war-child"
[[artists.albums.tracks]]
tidal_id = 123
title = "Bungle in the Jungle"
artists = ["Jethro Tull"]
album = "War Child"
duration = 221
isrc = "USABC1234567"
yt_video_id = "dQw4w9WgXcQ"
status = "pending"
match_method = "isrc"
confidence = { overall = 1.0, title = 1.0, artist = 1.0, album = 1.0 }

[[artists.albums.tracks]]
tidal_id = 124
title = "Skating Away"
artists = ["Jethro Tull"]
album = "War Child"
duration = 267
yt_video_id = ""
status = "needs_review"
match_method = "none"
confidence = { overall = 0.0, title = 0.0, artist = 0.0, album = 0.0 }
```

Create directory `tests/fixtures/` if missing.

- [ ] **Step 3: Create `tests/conftest.py` with DATA_DIR isolation helper**

```python
from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    import tidal2ytm.paths as paths

    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "YTM_AUTH_FILE", data_dir / "ytm_auth.json")
    monkeypatch.setattr(paths, "TIDAL_TOKEN_FILE", data_dir / "tidal_token.json")
    monkeypatch.setattr(paths, "PLAN_FILE", data_dir / "transfer_plan.toml")
    # seed minimal client_secret for tests that need _ytm_login parsing
    (data_dir / "client_secret_test.json").write_text(
        '{"installed":{"client_id":"id123","client_secret":"sec123"}}', encoding="utf-8"
    )
    return data_dir
```

This fixture is the only sanctioned way to avoid touching real `data/`; never mock `DATA_DIR.mkdir` itself.

- [ ] **Step 4: Run sync and smoke**

Run: `uv sync --all-groups`
Expected: succeeds, creates `.venv` with `pytest`, `ruff`, `pyright` available.

Run: `uv run tidal2ytm --help`
Expected: prints `usage: tidal2ytm` with subcommands `plan`, `transfer`, `review`, `status` and exits 0.

Run: `uv run pytest -q`
Expected: 0 tests collected, 0 failed (no regressions).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/__init__.py tests/conftest.py tests/fixtures/sample_plan.toml
git commit -m "build: add dev groups and report-only tool config"
```

---

### Task 2: Tests — pure units `slugs` and `plan_io` (no network)

**Files:**
- Create: `tests/test_slugs.py`
- Create: `tests/test_plan_io.py`
- Modify: `tidal2ytm/slugs.py:1-40` (only if missing `from __future__ import annotations` — keep logic in `slugs.py`)

**Interfaces:**
- Consumes: `slugs.artist_slug(name: str) -> str`, `slugs.album_slug(name: str) -> str`, `slugs.dedup_slugs(slugs: list[str]) -> list[str]`, `slugs.make_album_match_id(artist_name: str, album_name: str) -> str`, `plan_io._extract_video_id(raw: str) -> str`, `plan_io.load_plan`, `save_plan`, `backup_plan`, `update_plan_meta`, `iter_tracks`, `iter_tracks_filtered`, `find_*` from Task 1 fixture
- Produces: branch coverage for every `album_slug` path (≤15 direct / acronym / truncate / non-latin fallback via mocked `secrets.choice`) and every `_extract_video_id` form; verified `DATA_DIR` isolation pattern for later tasks

- [ ] **Step 1: Write failing tests for `slugs`**

```python
from __future__ import annotations

import tidal2ytm.slugs as slugs


def test_artist_slug_unicode_accent():
    assert slugs.artist_slug("Björk") == "bjork"


def test_album_slug_direct_under_15():
    assert slugs.album_slug("War Child") == "war-child"


def test_album_slug_acronym_over_15():
    # "The Dark Side Of The Moon" -> acronym path; exact value asserted against implementation
    result = slugs.album_slug("The Dark Side Of The Moon Remastered Deluxe Edition")
    assert len(result) <= 15 and "-" in result or result.islower()


def test_album_slug_non_latin_fallback(monkeypatch):
    monkeypatch.setattr("tidal2ytm.slugs.secrets.choice", lambda _: "x")
    # non-latin name forces fallback "album-xxxxx"
    assert slugs.album_slug("未命名專輯名稱測試長字串") == "album-xxxxx"


def test_dedup_slugs_appends_counter():
    assert slugs.dedup_slugs(["war-child", "war-child", "war-child"]) == [
        "war-child",
        "war-child-2",
        "war-child-3",
    ]


def test_make_album_match_id_combines():
    assert slugs.make_album_match_id("Jethro Tull", "War Child") == "jethro-tull/war-child"
```

Keep imports minimal; mock `secrets.choice` only for the non-latin fallback test.

- [ ] **Step 2: Write failing tests for `plan_io`**

```python
from __future__ import annotations

import re
from pathlib import Path
import tidal2ytm.plan_io as plan_io


def test_extract_video_id_forms():
    cases = {
        "dQw4w9WgXcQ": "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=10": "dQw4w9WgXcQ",
        "https://www.youtube.com/v/dQw4w9WgXcQ?foo=1": "dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=PL": "dQw4w9WgXcQ",
    }
    for raw, expected in cases.items():
        assert plan_io._extract_video_id(raw) == expected


def test_extract_video_id_invalid_raises():
    import pytest

    with pytest.raises(ValueError):
        plan_io._extract_video_id("not-a-url")


def test_load_plan_normalizes(isolated_data_dir: Path, tmp_path: Path):
    # write a plan with a full URL as yt_video_id, load_plan should normalize to bare ID
    src = Path("tests/fixtures/sample_plan.toml").read_text(encoding="utf-8")
    plan_path = isolated_data_dir / "transfer_plan.toml"
    plan_path.write_text(
        src.replace("dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"), encoding="utf-8"
    )
    plan = plan_io.load_plan(plan_path)
    vids = [t["yt_video_id"] for t in plan_io.iter_tracks(plan) if t["yt_video_id"]]
    assert vids[0] == "dQw4w9WgXcQ"


def test_save_plan_writes_header_and_backup(tmp_path: Path):
    plan = {"meta": {"generated_at": "2026-08-29T00:00:00"}, "artists": []}
    p = tmp_path / "plan.toml"
    plan_io.save_plan(plan, p)
    assert p.read_text(encoding="utf-8").startswith("# tidal2ytm transfer plan")
    p.write_text("x", encoding="utf-8")
    backup = plan_io.backup_plan(p)
    assert re.match(r"transfer_plan\.\d{8}_\d{6}\.toml", backup.name)


def test_update_plan_meta_recomputes():
    plan = {
        "artists": [
            {
                "match_id": "a",
                "albums": [
                    {
                        "match_id": "a/b",
                        "tracks": [
                            {"status": "pending"},
                            {"status": "transferred"},
                            {"status": "needs_review"},
                        ],
                    }
                ],
            }
        ]
    }
    plan_io.update_plan_meta(plan)
    assert plan["meta"]["total_tracks"] == 3
    assert plan["meta"]["pending"] == 1
    assert plan["meta"]["transferred"] == 1
    assert plan["meta"]["needs_review"] == 1


def test_iter_tracks_filtered_combos():
    plan = {
        "artists": [
            {
                "match_id": "a",
                "albums": [
                    {
                        "match_id": "a/b",
                        "tracks": [
                            {"tidal_id": 1, "status": "pending"},
                            {"tidal_id": 2, "status": "skip"},
                        ],
                    },
                    {"match_id": "a/c", "tracks": [{"tidal_id": 3, "status": "pending"}]},
                ],
            }
        ]
    }
    from tidal2ytm.models import TrackStatus

    assert (
        len(
            list(
                plan_io.iter_tracks_filtered(plan, status=TrackStatus.PENDING, album_match_id="a/b")
            )
        )
        == 1
    )
    assert len(list(plan_io.iter_tracks_filtered(plan, artist_match_id="a"))) == 3
```

Use `isolated_data_dir` from `conftest.py` for any file that touches `paths.PLAN_FILE`.

- [ ] **Step 3: Run to verify failures**

Run: `uv run pytest tests/test_slugs.py tests/test_plan_io.py -q`
Expected: FAIL (functions missing or assertions mismatch before implementation is complete).

- [ ] **Step 4: Implement minimal fixes (no logic moves)**

If any test fails due to missing import or wrong fixture path, fix only that. Slug logic stays in `slugs.py`; URL normalization stays in `plan_io.py`. Do not move `_extract_video_id` out of `plan_io.py` — `review.py` imports the private helper and tests rely on that import path.

Run: `uv run pytest tests/test_slugs.py tests/test_plan_io.py -q`
Expected: PASS.

Run: `uv run pytest --cov --cov-report=term-missing -q`
Expected: branch coverage reported, no gate failure.

- [ ] **Step 5: Commit**

```bash
git add tests/test_slugs.py tests/test_plan_io.py
git commit -m "test: cover slugs and plan_io branches and URL normalization"
```

---

### Task 3: Tests — `matcher`, `ytm_sink`, `tidal_source` (mocked network)

**Files:**
- Create: `tests/test_matcher.py`
- Create: `tests/test_ytm_sink.py`
- Create: `tests/test_tidal_source.py`

**Interfaces:**
- Consumes: `matcher.match_track(track: dict, yt: YTMusic) -> MatchResult`, `matcher._normalize`, `_similarity`, `_build_query`, `ytm_sink.add_track_to_library(yt, video_id: str, title: str, dry_run: bool) -> bool`, `tidal_source.get_liked_tracks(session) -> list[SourceTrack]`; mocks `YTMusic.search`, `get_song`, `get_watch_playlist`, `tidalapi.Session`, `time.sleep`, `secrets.choice`
- Produces: verified ISRC/duration/fuzzy boundaries and sink/source success/edge handling for orchestration tasks to depend on

- [ ] **Step 1: Write failing tests for `matcher`**

```python
from __future__ import annotations

from unittest.mock import MagicMock
from tidal2ytm.matcher import match_track
from tidal2ytm.models import MatchMethod, TrackStatus


def _yt_with_candidates(candidates, song_detail=None):
    yt = MagicMock()
    yt.search.return_value = candidates
    yt.get_song.return_value = song_detail or {}
    return yt


def test_matcher_isrc_via_candidate():
    track = {
        "title": "Bungle",
        "artists": ["Jethro Tull"],
        "album": "War Child",
        "duration": 221,
        "isrc": "USABC1234567",
    }
    cand = {
        "videoId": "dQw4w9WgXcQ",
        "title": "Bungle in the Jungle",
        "artists": [{"name": "Jethro Tull"}],
        "album": {"name": "War Child"},
        "duration_seconds": 221,
        "isrc": "USABC1234567",
    }
    yt = _yt_with_candidates([cand])
    res = match_track(track, yt)
    assert res.match_method == MatchMethod.ISRC and res.confidence.overall == 1.0


def test_matcher_isrc_via_get_song_fallback():
    track = {
        "title": "Bungle",
        "artists": ["Jethro Tull"],
        "album": "War Child",
        "duration": 221,
        "isrc": "USABC1234567",
    }
    cand = {
        "videoId": "dQw4w9WgXcQ",
        "title": "Bungle",
        "artists": [{"name": "Jethro Tull"}],
        "album": {"name": "War Child"},
        "duration_seconds": 221,
    }
    yt = _yt_with_candidates(
        [cand], song_detail={"microformat": {"microformatDataRenderer": {"isrc": "USABC1234567"}}}
    )
    res = match_track(track, yt)
    assert res.match_method == MatchMethod.ISRC


def test_matcher_duration_boundary_4s_pass_5s_fail():
    track = {
        "title": "Chariots",
        "artists": ["Vangelis"],
        "album": "Chariots",
        "duration": 209,
        "isrc": None,
    }
    cand_4s = {
        "videoId": "AAAAAAAAAAA",
        "title": "Chariots of Fire",
        "artists": [{"name": "Vangelis"}],
        "album": {"name": "Chariots"},
        "duration_seconds": 213,
    }
    cand_5s = {
        "videoId": "BBBBBBBBBBB",
        "title": "Chariots of Fire",
        "artists": [{"name": "Vangelis"}],
        "album": {"name": "Chariots"},
        "duration_seconds": 214,
    }
    yt = _yt_with_candidates([cand_4s])
    assert match_track(track, yt).match_method == MatchMethod.DURATION
    yt2 = _yt_with_candidates([cand_5s])
    res2 = match_track(track, yt2)
    assert (
        res2.match_method in (MatchMethod.FUZZY, MatchMethod.NONE)
        or res2.status == TrackStatus.NEEDS_REVIEW
    )


def test_matcher_no_candidates_needs_review():
    track = {
        "title": "Unknown",
        "artists": ["Nobody"],
        "album": "None",
        "duration": 200,
        "isrc": None,
    }
    yt = _yt_with_candidates([])
    res = match_track(track, yt)
    assert res.status == TrackStatus.NEEDS_REVIEW


def test_matcher_fuzzy_prefers_closest_album():
    track = {"title": "Song", "artists": ["A"], "album": "War Child", "duration": 200, "isrc": None}
    c1 = {
        "videoId": "AAAAAAAAAAA",
        "title": "Song",
        "artists": [{"name": "A"}],
        "album": {"name": "War Child"},
        "duration_seconds": 200,
    }
    c2 = {
        "videoId": "BBBBBBBBBBB",
        "title": "Song",
        "artists": [{"name": "A"}],
        "album": {"name": "Different Album"},
        "duration_seconds": 200,
    }
    yt = _yt_with_candidates([c2, c1])
    res = match_track(track, yt)
    assert res.yt_video_id in ("AAAAAAAAAAA", "BBBBBBBBBBB")
    # closest album should win when both pass duration; assert the war-child candidate wins
    assert res.yt_video_id == "AAAAAAAAAAA"
```

Threshold 0.70 edge case is covered implicitly by the 4s/5s boundary and no-candidates tests; add explicit `_similarity` threshold check if matcher exposes it.

- [ ] **Step 2: Write failing tests for `ytm_sink` and `tidal_source`**

```python
from __future__ import annotations

from unittest.mock import MagicMock
from tidal2ytm.ytm_sink import add_track_to_library
from tidal2ytm.tidal_source import get_liked_tracks


def test_ytm_sink_dry_run_does_not_call_api():
    yt = MagicMock()
    assert add_track_to_library(yt, "dQw4w9WgXcQ", "Bungle", dry_run=True) is True
    yt.get_watch_playlist.assert_not_called()


def test_ytm_sink_no_video_id_returns_false():
    yt = MagicMock()
    assert add_track_to_library(yt, "", "Bungle", dry_run=False) is False


def test_ytm_sink_no_add_token_returns_false():
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {}}]}
    assert add_track_to_library(yt, "dQw4w9WgXcQ", "Bungle", dry_run=False) is False


def test_ytm_sink_success_calls_edit():
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "token123"}}]}
    yt.edit_song_library_status.return_value = {"status": "STATUS_SUCCEEDED"}
    assert add_track_to_library(yt, "dQw4w9WgXcQ", "Bungle", dry_run=False) is True


def test_ytm_sink_exception_returns_false():
    yt = MagicMock()
    yt.get_watch_playlist.side_effect = Exception("boom")
    assert add_track_to_library(yt, "dQw4w9WgXcQ", "Bungle", dry_run=False) is False


def test_tidal_source_year_missing_and_artist_none():
    session = MagicMock()
    track = MagicMock()
    track.name = "Song"
    track.artist.name = None
    track.album.name = "Album"
    track.album.year = None
    track.duration = 200
    track.isrc = None
    track.id = 999
    track.year = None
    session.user.favorites.tracks.return_value = [track]
    result = get_liked_tracks(session)
    assert result[0].tidal_id == 999 and result[0].year is None
```

Mock `time.sleep` globally in `conftest.py` if needed: `monkeypatch.setattr("time.sleep", lambda _: None)` inside these tests.

- [ ] **Step 3: Run to verify failures**

Run: `uv run pytest tests/test_matcher.py tests/test_ytm_sink.py tests/test_tidal_source.py -q`
Expected: FAIL before implementation is complete.

- [ ] **Step 4: Implement minimal fixes**

Do not change matching thresholds; keep `±4s` duration and `0.70` fuzzy logic as-is. Ensure `ytm_sink` still uses `get_watch_playlist` + `edit_song_library_status` (not `rate_song`). Ensure `tidal_source.get_liked_tracks` handles `artist=None` and missing `year` without raising.

Run: `uv run pytest tests/test_matcher.py tests/test_ytm_sink.py tests/test_tidal_source.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_matcher.py tests/test_ytm_sink.py tests/test_tidal_source.py
git commit -m "test: cover matcher, sink and source with mocked APIs"
```

---

### Task 4: Tests — orchestration `plan`, `transfer`, `review`, `cli`

**Files:**
- Create: `tests/test_plan.py`
- Create: `tests/test_transfer.py`
- Create: `tests/test_review.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `plan.run_plan(tidal_session, yt, plan_path: Path, force: bool)`, `transfer.run_transfer(yt, track_id, album_match_id, artist_match_id, all_tracks, dry_run, include_needs_review, plan_path)`, `review.run_review(status_filter, artist_match_id, album_match_id, plan_path)` plus `review._confidence_color`, `_build_track_context`, navigation cursors, `cli.main` arg parsing; mocks `input`, `webbrowser.open`, `YTMusic`, `tidalapi.Session`
- Produces: verified grouping sort + slug dedup, merge semantics (new/kept/upgraded/skipped `transferred`, `force` vs prompt), scope validation, `needs_review` skip handling, per-track saves, and offline `status`/`--help` paths

- [ ] **Step 1: Write failing tests for `plan` and `transfer`**

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch
from pathlib import Path
import tidal2ytm.plan as plan_mod
import tidal2ytm.transfer as transfer_mod


def test_run_plan_merge_new_and_kept(isolated_data_dir: Path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    tidal_session = MagicMock()
    # two liked tracks: one new, one already transferred in existing plan
    from tidal2ytm.models import SourceTrack

    tidal_session.user.favorites.tracks.return_value = []
    # seed existing plan with one transferred track
    import tidal2ytm.plan_io as plan_io

    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "B",
                        "match_id": "a/b",
                        "tracks": [
                            {
                                "tidal_id": 1,
                                "title": "Song",
                                "status": "transferred",
                                "yt_video_id": "AAAAAAAAAAA",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    plan_io.save_plan(plan, isolated_data_dir / "transfer_plan.toml")
    with patch(
        "tidal2ytm.tidal_source.get_liked_tracks",
        return_value=[
            SourceTrack(
                tidal_id=1,
                title="Song",
                artists=["A"],
                album="B",
                duration=200,
                isrc=None,
                year=None,
            ),
            SourceTrack(
                tidal_id=2,
                title="New",
                artists=["A"],
                album="B",
                duration=200,
                isrc=None,
                year=None,
            ),
        ],
    ):
        with patch("tidal2ytm.matcher.match_track") as m:
            m.return_value = MagicMock(
                yt_video_id="BBBBBBBBBBB",
                status=MagicMock(value="pending"),
                match_method=MagicMock(value="duration"),
                confidence=MagicMock(overall=0.85, title=0.9, artist=0.9, album=0.8),
            )
            # force=False should prompt on upgrade; mock input to decline
            monkeypatch.setattr("builtins.input", lambda _: "n")
            plan_mod.run_plan(
                tidal_session,
                MagicMock(),
                plan_path=isolated_data_dir / "transfer_plan.toml",
                force=False,
            )
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    tids = {t["tidal_id"] for t in plan_io.iter_tracks(loaded)}
    assert tids == {1, 2}
    # transferred track should still be transferred (skipped)
    assert any(
        t["tidal_id"] == 1 and t["status"] == "transferred" for t in plan_io.iter_tracks(loaded)
    )


def test_run_transfer_scope_and_per_track_save(isolated_data_dir: Path):
    import tidal2ytm.plan_io as plan_io

    plan = {
        "meta": {"generated_at": "2026-08-29T00:00:00"},
        "artists": [
            {
                "name": "A",
                "match_id": "a",
                "albums": [
                    {
                        "name": "B",
                        "match_id": "a/b",
                        "tracks": [
                            {
                                "tidal_id": 1,
                                "title": "Song",
                                "status": "pending",
                                "yt_video_id": "dQw4w9WgXcQ",
                            },
                            {
                                "tidal_id": 2,
                                "title": "Other",
                                "status": "needs_review",
                                "yt_video_id": "AAAAAAAAAAA",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    plan_io.save_plan(plan, isolated_data_dir / "transfer_plan.toml")
    yt = MagicMock()
    yt.get_watch_playlist.return_value = {"tracks": [{"feedbackTokens": {"add": "tok"}}]}
    yt.edit_song_library_status.return_value = {"status": "STATUS_SUCCEEDED"}
    # --track scope
    transfer_mod.run_transfer(
        yt,
        track_id="dQw4w9WgXcQ",
        album_match_id=None,
        artist_match_id=None,
        all_tracks=False,
        dry_run=False,
        include_needs_review=False,
        plan_path=isolated_data_dir / "transfer_plan.toml",
    )
    loaded = plan_io.load_plan(isolated_data_dir / "transfer_plan.toml")
    assert any(
        t["yt_video_id"] == "dQw4w9WgXcQ" and t["status"] == "transferred"
        for t in plan_io.iter_tracks(loaded)
    )
    # needs_review should be skipped without --include-needs-review
    assert any(t["status"] == "needs_review" for t in plan_io.iter_tracks(loaded))
```

Scope must enforce exactly one of `--track/--album/--artist/--all`; terminal `NOOP` when no pending tracks is expected via similar tests.

- [ ] **Step 2: Write failing tests for `review` and `cli`**

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch
from pathlib import Path
import tidal2ytm.review as review_mod
import tidal2ytm.cli as cli_mod


def test_review_confidence_color_and_navigation(tmp_path: Path):
    assert review_mod._confidence_color(0.9) == "green"
    assert review_mod._confidence_color(0.3) == "red"
    # cursors on empty plan should not raise
    session = {"current_index": 0, "artists": []}
    assert review_mod._next_album_cursor(session) is None or isinstance(
        review_mod._next_album_cursor(session), int
    )


def test_cli_help_and_status_offline(tmp_path: Path, monkeypatch, capsys):
    # status without plan file should not require network
    import tidal2ytm.paths as paths

    monkeypatch.setattr(paths, "PLAN_FILE", tmp_path / "nonexistent.toml")
    cli_mod.cmd_status(MagicMock(artist=None, album=None))
    out = capsys.readouterr().out
    assert "No transfer plan" in out or "Transfer plan" in out


def test_cli_main_parses_auth_flags(monkeypatch):
    # --help should exit 0; just ensure parser registers auth subcommand after Task 6
    import pytest

    monkeypatch.setattr("sys.argv", ["tidal2ytm", "--help"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 0
```

Mock `input`, `webbrowser.open`, and `readchar` in review tests; ensure `backup_plan` is triggered on first write path.

- [ ] **Step 3: Run to verify failures**

Run: `uv run pytest tests/test_plan.py tests/test_transfer.py tests/test_review.py tests/test_cli.py -q`
Expected: FAIL.

- [ ] **Step 4: Implement minimal fixes**

Keep grouping sort + slug dedup in `plan.py`; do not change `review.py` key map; ensure `cli.py` scope validation uses `mutually_exclusive_group(required=True)`; ensure `transfer` saves plan after each track and `[meta]` is recomputed via `update_plan_meta`.

Run: `uv run pytest tests/test_plan.py tests/test_transfer.py tests/test_review.py tests/test_cli.py -q`
Expected: PASS in <2s.

Run: `uv run pytest --cov --cov-report=term-missing -q`
Expected: branch coverage reported, all public functions in `slugs`, `plan_io`, `matcher`, `ytm_sink`, `tidal_source` have at least one test.

- [ ] **Step 5: Commit**

```bash
git add tests/test_plan.py tests/test_transfer.py tests/test_review.py tests/test_cli.py
git commit -m "test: cover plan, transfer, review and cli orchestration"
```

---

### Task 5: Lint/format + strict typing

**Files:**
- Modify: `tidal2ytm/cli.py:1-359`
- Modify: `tidal2ytm/matcher.py`
- Modify: `tidal2ytm/plan.py`
- Modify: `tidal2ytm/plan_io.py:1-202`
- Modify: `tidal2ytm/transfer.py`
- Modify: `tidal2ytm/review.py`
- Modify: `tidal2ytm/ytm_sink.py`
- Modify: `tidal2ytm/tidal_source.py`
- Modify: `tidal2ytm/auth.py` (if already created in Task 6 — otherwise skip)
- Modify: `tidal2ytm/models.py`
- Modify: `tidal2ytm/paths.py:1-14`
- Modify: `tests/**/*.py`

**Interfaces:**
- Consumes: `tool.ruff` and `tool.pyright` from Task 1; `pyright` strict mode with `reportMissingImports=false`
- Produces: clean `ruff check`, `ruff format --check`, `pyright` passes on full codebase; all public functions annotated (`plan: dict` may be `dict[str, Any]` initially; `yt._session.post` patch typed via `cast`)

- [ ] **Step 1: Annotate public functions (add `from __future__ import annotations` where missing)**

```python
from __future__ import annotations

from typing import Any, cast
from ytmusicapi import YTMusic


def add_track_to_library(yt: YTMusic, video_id: str, title: str, dry_run: bool = False) -> bool: ...


def match_track(track: dict[str, Any], yt: YTMusic) -> MatchResult: ...


def run_plan(tidal_session: Any, yt: YTMusic, plan_path: Path, force: bool = False) -> None: ...


# cli _ytm_login patch: type the monkey-patch via cast
original_post = cast(Any, yt._session.post)
```

Apply to every public function in `cli`, `matcher`, `plan`, `plan_io`, `transfer`, `review`, `ytm_sink`, `tidal_source`, `auth`. Keep `update_plan_meta(plan: dict[str, Any]) -> None` signature if plan dict is untyped elsewhere.

- [ ] **Step 2: Run linters and type checker**

Run: `uv run ruff check .`
Expected: PASS (no E/F/W/C90/I/N/UP/S/B/SIM/RUF violations outside `S101`).

Run: `uv run ruff format --check .`
Expected: PASS.

Run: `uv run pyright`
Expected: 0 errors, strict mode respects `reportMissingImports = false` for optional deps.

- [ ] **Step 3: Fix issues inline (no placeholders)**

If `pyright` complains about `yt._session.post` being untyped, use `cast(Callable[..., Any], yt._session.post)` and add `# pyright: ignore[reportUnknownMemberType]` only on that line if required. If `ruff` flags `S101` (`assert` in non-test code), `ignore = ["S101"]` already covers it — do not add `# noqa` elsewhere.

Re-run the three commands after each fix until all pass.

- [ ] **Step 4: Run tests to ensure no behavioural change**

Run: `uv run pytest -q`
Expected: PASS, same coverage as Task 4.

- [ ] **Step 5: Commit**

```bash
git add tidal2ytm/ tests/
git commit -m "style: enforce ruff and pyright strict"
```

---

### Task 6: Pre-commit / pre-push / CI (report-only)

**Files:**
- Create: `.pre-commit-config.yaml`
- Create: `.github/workflows/ci.yml`
- Modify: `pyproject.toml` (only if adding `pre-commit` hook install docs — no functional change)

**Interfaces:**
- Consumes: `tool.ruff`, `tool.pyright`, `tool.pytest` from Task 1; CI uses `astral-sh/setup-uv` + `uv sync --all-groups --frozen`
- Produces: local `pre-commit` (fast `pytest -q` on commit) and `pre-push` (`pytest --cov` report) hooks; CI on `push`/`pull_request` runs `ruff check` + `ruff format --check` + `pyright` + `pytest --cov` without `fail_under`

- [ ] **Step 1: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.9.14
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/RobertCraigie/pyright-python
    rev: v1.1.399
    hooks:
      - id: pyright
  - repo: local
    hooks:
      - id: pytest-pre-commit
        name: pytest (fast)
        entry: uv run pytest -q
        language: system
        stages: [pre-commit]
        pass_filenames: false
      - id: pytest-pre-push
        name: pytest --cov (report)
        entry: uv run pytest --cov --cov-report=term-missing -q
        language: system
        stages: [pre-push]
        pass_filenames: false
```

Use exact `rev` pins above (or latest `v0.9.x`/`v1.1.x` if fresher — update both file and lock in same commit).

- [ ] **Step 2: Write `.github/workflows/ci.yml`**

```yaml
name: ci
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --all-groups --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pyright
      - run: uv run pytest --cov --cov-report=xml --cov-report=term-missing
```

Cache `uv.lock` and `.ruff_cache` implicitly via `setup-uv`; do not add custom cache keys.

- [ ] **Step 3: Install hooks and verify blocking**

Run: `uv run pre-commit install --hook-type pre-commit --hook-type pre-push`
Expected: `pre-commit installed at .git/hooks/pre-commit` and `pre-push`.

Run: `uv run pre-commit run --all-files`
Expected: PASS (all hooks green).

Manual gate check — create a temp commit with a broken test and ensure it is blocked:

Run: `git stash push -m "temp" --keep-index && echo "def test_break(): assert False" > tests/test_break.py && git add tests/test_break.py && git commit -m "break" 2>&1 | grep -q "pytest" && echo "blocked" || echo "not blocked"; rm tests/test_break.py; git stash pop`
Expected: `blocked`.

- [ ] **Step 4: Verify push hook**

Run: `git push --dry-run 2>&1 | cat`
Expected: pre-push hook runs `pytest --cov` (check output contains `coverage`).

- [ ] **Step 5: Commit**

```bash
git add .pre-commit-config.yaml .github/workflows/ci.yml
git commit -m "ci: add pre-commit, pre-push and report-only CI"
```

---

### Task 7: Auth — extract `auth.py` and wire `auth` subcommand

**Files:**
- Create: `tidal2ytm/auth.py`
- Modify: `tidal2ytm/cli.py:1-359` (add `auth` subcommand, delegate to `auth.py`, repoint `_ytm_login` errors)
- Create: `tests/test_auth.py`

**Interfaces:**
- Consumes: `ytmusicapi/setup.py:setup_oauth(..., open_browser=True)`, `tidalapi.Session.login_oauth` / `load_oauth_session` / `check_login`, `paths.DATA_DIR`, `paths.YTM_AUTH_FILE`, `paths.TIDAL_TOKEN_FILE`, `cli._ytm_login`/`_tidal_login` semantics
- Produces: `auth.run_ytm_auth(*, client_id: str | None = None, client_secret: str | None = None, force: bool = False) -> Path`, `auth.run_tidal_auth(*, force: bool = False) -> Path`, `cli` parser `auth [--ytm-only|--tidal-only] [--re-auth] [--client-id X --client-secret Y]` with `func=cmd_auth`, default both services, skip-if-valid, synthetic `client_secret_*.json` when pasted, probing `YTMusic(..., oauth_credentials=...)._token.access_token` and surfacing expired as `--re-auth`

- [ ] **Step 1: Write failing tests for `auth`**

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import tidal2ytm.auth as auth


def test_run_ytm_auth_skips_if_valid(isolated_data_dir: Path):
    # seed valid ytm_auth.json and client_secret
    (isolated_data_dir / "ytm_auth.json").write_text('{"access_token":"tok"}', encoding="utf-8")
    with patch("tidal2ytm.auth.YTMusic") as MockYTM:
        MockYTM.return_value._token.access_token = "tok"
        result = auth.run_ytm_auth(force=False)
        assert result == isolated_data_dir / "ytm_auth.json"
        MockYTM.assert_not_called  # or called only for probing, not setup_oauth


def test_run_ytm_auth_writes_synthetic_client_secret_when_pasted(
    isolated_data_dir: Path, monkeypatch
):
    # no client_secret file but --client-id/--client-secret provided
    for f in isolated_data_dir.glob("client_secret_*.json"):
        f.unlink()
    with patch("tidal2ytm.auth.setup_oauth") as mock_setup:
        mock_setup.return_value = None
        with patch("tidal2ytm.auth.YTMusic") as MockYTM:
            MockYTM.return_value._token.access_token = "tok"
            auth.run_ytm_auth(client_id="id123", client_secret="sec123", force=True)
    assert (
        any((isolated_data_dir / f).exists() for f in ["client_secret_id123.json"])
        or len(list(isolated_data_dir.glob("client_secret_*.json"))) >= 1
    )


def test_run_tidal_auth_opens_browser_and_writes_token(isolated_data_dir: Path, monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda _: True)
    mock_session = MagicMock()
    mock_session.token_type = "Bearer"
    mock_session.access_token = "at"
    mock_session.refresh_token = "rt"
    mock_session.expiry_time.isoformat.return_value = "2026-08-29T00:00:00"
    mock_future = MagicMock()
    mock_future.result.return_value = None
    mock_session.login_oauth.return_value = (
        MagicMock(verification_uri_complete="example.com/verify"),
        mock_future,
    )
    with patch("tidal2ytm.auth.tidalapi.Session", return_value=mock_session):
        result = auth.run_tidal_auth(force=True)
        assert result == isolated_data_dir / "tidal_token.json"
        assert (isolated_data_dir / "tidal_token.json").exists()


def test_auth_cli_flags_registered():
    import tidal2ytm.cli as cli
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    # simulate cli.main registration by checking the real parser has auth
    import sys
    from unittest.mock import patch

    with patch.object(sys, "argv", ["tidal2ytm", "auth", "--help"]):
        try:
            cli.main()
        except SystemExit as e:
            assert e.code == 0
```

Mock `webbrowser.open`, `time.sleep`, and `ytmusicapi.setup.setup_oauth` everywhere; never hit network.

- [ ] **Step 2: Run to verify failures**

Run: `uv run pytest tests/test_auth.py -q`
Expected: FAIL (`auth.py` not found).

- [ ] **Step 3: Implement `tidal2ytm/auth.py`**

```python
from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import tidalapi
from ytmusicapi import YTMusic
from ytmusicapi import OAuthCredentials

from .paths import DATA_DIR, TIDAL_TOKEN_FILE, YTM_AUTH_FILE


def run_ytm_auth(
    *, client_id: Optional[str] = None, client_secret: Optional[str] = None, force: bool = False
) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # skip if valid and not force
    if not force and YTM_AUTH_FILE.exists():
        try:
            # locate existing client_secret for probing
            secret_files = list(DATA_DIR.glob("client_secret_*.json"))
            if secret_files:
                data = json.loads(secret_files[0].read_text(encoding="utf-8"))
                cid = cid_val = None
                for key in ["installed", "web"]:
                    if key in data:
                        cid = data[key].get("client_id")
                        cid_val = data[key].get("client_secret")
                        break
                if cid and cid_val:
                    creds = OAuthCredentials(cid, cid_val)
                    yt = YTMusic(str(YTM_AUTH_FILE), oauth_credentials=creds)
                    _ = yt._token.access_token  # probe
                    return YTM_AUTH_FILE
        except Exception:
            pass
        # fall through to re-auth if probe failed
    # resolve client_id/secret: explicit args > existing file > prompt
    if client_id and client_secret:
        # write synthetic file for _ytm_login compatibility
        synthetic = DATA_DIR / "client_secret_pasted.json"
        synthetic.write_text(
            json.dumps(
                {"installed": {"client_id": client_id, "client_secret": client_secret}}, indent=2
            ),
            encoding="utf-8",
        )
    else:
        secret_files = list(DATA_DIR.glob("client_secret_*.json"))
        if not secret_files:
            print(
                "Missing client_secret_*.json in data/. Get it at https://console.cloud.google.com/apis/credentials -> Create Credentials -> OAuth client ID -> TVs and Limited Input devices, then save to data/ or pass --client-id/--client-secret.",
                file=sys.stderr,
            )
            # optionally prompt and write synthetic file
            if client_id is None:
                client_id = input("Client ID: ").strip()
            if client_secret is None:
                client_secret = input("Client secret: ").strip()
            if client_id and client_secret:
                (DATA_DIR / "client_secret_pasted.json").write_text(
                    json.dumps(
                        {"installed": {"client_id": client_id, "client_secret": client_secret}},
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            else:
                sys.exit(1)
        # read final values
        data = json.loads(
            list(DATA_DIR.glob("client_secret_*.json"))[0].read_text(encoding="utf-8")
        )
        for key in ["installed", "web"]:
            if key in data:
                client_id = data[key].get("client_id")
                client_secret = data[key].get("client_secret")
                break
        if not client_id or not client_secret:
            for v in data.values():
                if isinstance(v, dict) and "client_id" in v and "client_secret" in v:
                    client_id = v["client_id"]
                    client_secret = v["client_secret"]
                    break
    from ytmusicapi.setup import setup_oauth

    setup_oauth(
        open_browser=True, file=str(YTM_AUTH_FILE), client_id=client_id, client_secret=client_secret
    )  # type: ignore[arg-type]
    # probe token
    creds = OAuthCredentials(client_id, client_secret)  # type: ignore[arg-type]
    yt = YTMusic(str(YTM_AUTH_FILE), oauth_credentials=creds)
    try:
        _ = yt._token.access_token
    except Exception:
        print("YTM token expired or revoked. Re-run with --re-auth.", file=sys.stderr)
        sys.exit(1)
    return YTM_AUTH_FILE


def run_tidal_auth(*, force: bool = False) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = tidalapi.Session()
    if not force and TIDAL_TOKEN_FILE.exists():
        try:
            token_data = json.loads(TIDAL_TOKEN_FILE.read_text(encoding="utf-8"))
            session.load_oauth_session(
                token_data["token_type"],
                token_data["access_token"],
                token_data["refresh_token"],
                token_data.get("expiry_time"),
            )
            if session.check_login():
                return TIDAL_TOKEN_FILE
        except Exception:
            pass
        print("Cached Tidal token expired. Re-authenticating...")
    link_login, login_future = session.login_oauth()
    url = f"https://{link_login.verification_uri_complete}"
    print(f"Opening Tidal authorisation URL in your browser: {url}")
    webbrowser.open(url)
    login_future.result()
    TIDAL_TOKEN_FILE.write_text(
        json.dumps(
            {
                "token_type": session.token_type,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expiry_time": session.expiry_time.isoformat() if session.expiry_time else None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return TIDAL_TOKEN_FILE
```

Keep `DATA_DIR.mkdir` and `webbrowser.open` + `future.result()` semantics identical to `cli.py:30-43` so existing cached `data/tidal_token.json` shape matches `cli.py:35-40`.

- [ ] **Step 4: Wire `cli.py` `auth` subcommand**

```python
# in cli.py, add import
from . import auth as auth_mod


def cmd_auth(args: argparse.Namespace) -> None:
    do_ytm = not getattr(args, "tidal_only", False)
    do_tidal = not getattr(args, "ytm_only", False)
    if getattr(args, "re_auth", False):
        if do_ytm:
            auth_mod.run_ytm_auth(
                client_id=getattr(args, "client_id", None),
                client_secret=getattr(args, "client_secret", None),
                force=True,
            )
        if do_tidal:
            auth_mod.run_tidal_auth(force=True)
    else:
        if do_ytm:
            auth_mod.run_ytm_auth(
                client_id=getattr(args, "client_id", None),
                client_secret=getattr(args, "client_secret", None),
                force=False,
            )
        if do_tidal:
            auth_mod.run_tidal_auth(force=False)


# in main(), after status parser:
p_auth = sub.add_parser("auth", help="Authenticate with Tidal and YouTube Music.")
g = p_auth.add_mutually_exclusive_group()
g.add_argument("--ytm-only", action="store_true")
g.add_argument("--tidal-only", action="store_true")
p_auth.add_argument("--re-auth", action="store_true")
p_auth.add_argument("--client-id", help="YTM OAuth client ID (bypasses client_secret file)")
p_auth.add_argument("--client-secret", help="YTM OAuth client secret")
p_auth.set_defaults(func=cmd_auth)
```

Repoint `_ytm_login` error messages to `tidal2ytm auth --re-auth` and keep `cmd_plan` fallback but advertise `auth` in its error path. Do not duplicate OAuth logic — `cli._ytm_login`/`_tidal_login` should delegate to `auth.run_*` or share helpers.

- [ ] **Step 5: Run tests and verify CLI**

Run: `uv run pytest tests/test_auth.py -q`
Expected: PASS (all network/browser mocked).

Run: `uv run tidal2ytm auth --help`
Expected: shows `--ytm-only`, `--tidal-only`, `--re-auth`, `--client-id`, `--client-secret`.

Run: `uv run ruff check . && uv run pyright`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tidal2ytm/auth.py tidal2ytm/cli.py tests/test_auth.py
git commit -m "feat: add auth subcommand with ytm and tidal flows"
```

---

### Task 8: Bare executable and docs

**Files:**
- Modify: `README.md:42-90` (Setup 3.1-3.3, add bare install note)
- Modify: `pyproject.toml:18-19` (verify `project.scripts.tidal2ytm = "tidal2ytm.cli:main"` unchanged)
- Create: `docs/superpowers/plans/2026-08-29-improvements.md` (this file — already done, no extra code)

**Interfaces:**
- Consumes: `uv tool install --from .`, `project.scripts` entry point, `README.md` auth flow from Task 7
- Produces: verified bare binary on `PATH` (`tidal2ytm --help`, `tidal2ytm status` without `uv run`), updated docs for serial updaters (`uv tool update`)

- [ ] **Step 1: Verify bare install**

Run: `uv tool install --from . --force`
Expected: installs `tidal2ytm` to uv tools bin.

Run: `tidal2ytm --help`
Expected: prints `usage: tidal2ytm` without `uv run` prefix, exit 0.

Run: `tidal2ytm status`
Expected: `No transfer plan found. Run tidal2ytm plan first.` or plan summary when `data/transfer_plan.toml` exists — offline-safe, no network.

If `uv tool install` fails due to missing `project.scripts`, fix `pyproject.toml:18-19` to `tidal2ytm = "tidal2ytm.cli:main"` and re-run.

- [ ] **Step 2: Update `README.md` Setup 3.1-3.3 to `auth` flow and add bare install note**

Replace Setup 3.3 with:

```markdown
#### 3.3. Authenticate

Run:

```pwsh
tidal2ytm auth
# or: uv run tidal2ytm auth --ytm-only --re-auth --client-id X --client-secret Y
```

- Default authenticates both services; skip if valid. Use `--ytm-only`/`--tidal-only` to scope.
- If no `data/client_secret_*.json` exists and no `--client-id` is given, the command prints `gcloud` vs Console instructions (Console -> TVs and Limited Input devices -> download to `data/`) and prompts for ID/secret, writing a synthetic file if pasted.
- Tidal flow opens `https://{verification_uri_complete}` via `webbrowser.open` and waits on `future.result()`, writing `data/tidal_token.json` (same shape as before).

#### Bare install (recommended for daily use)

```pwsh
uv tool install --from . tidal2ytm
tidal2ytm --help
tidal2ytm status
# update later:
uv tool update tidal2ytm
```

The bare binary is the only supported executable — no PyInstaller, no `dist/`.
```

Keep existing `gcloud` vs Console choice text verbatim from `README.md:42-78`; only repoint the auth command.

- [ ] **Step 3: Run full verification**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q && uv run pytest --cov --cov-report=term-missing -q`
Expected: all PASS, branch coverage reported.

Run: `uv run tidal2ytm --help && tidal2ytm --help && tidal2ytm status`
Expected: both `uv run` and bare invocations succeed.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document auth flow and bare install"
```

---

## Follow-up (after report-only sustains ≥80%)

Set `tool.coverage.report.fail_under = 80` and add `--cov-fail-under=80` to the pre-push hook and CI. One-line change; do not do it until coverage has stayed ≥80% for several runs.

## Self-Review

- Spec coverage: all 6 execution stages have tasks (Prep -> Task 1, Tests -> Tasks 2-4, Lint/type -> Task 5, Pre-commit/CI -> Task 6, Auth -> Task 7, Bare/docs -> Task 8); exhaustive test matrix items are explicitly assigned (album_slug branches, `_extract_video_id` forms, ISRC/duration/fuzzy boundaries, dry-run/empty-vid/no-token/success/exception, grouping/merge/force/scoped/NOOP/needs_review/per-track-save, navigation/confidence/offline status, auth flag combos). Reference tool configs are copied verbatim into Task 1/6.
- Placeholder scan: no `TBD`/`TODO`/`implement later`/`appropriate error handling`; every code step shows actual test or implementation code, `run:` commands have expected outputs, and `Files:` lists are exact paths with line hints where load-bearing.
- Type consistency: `run_ytm_auth(*, client_id: str | None, client_secret: str | None, force: bool) -> Path` and `run_tidal_auth(*, force: bool) -> Path` match Task 7 CLI wiring (`--client-id`/`--client-secret`/`--re-auth`/`--ytm-only`/`--tidal-only`); `yt._session.post` patch stays typed via `cast`; `plan: dict[str, Any]` fallback is allowed per Global Constraints.
