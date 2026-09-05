# AGENTS.md

Python CLI (`tidal2ytm`) that transfers Tidal liked tracks to YouTube Music — ISRC > duration (±4 s) > fuzzy, managed with `uv` from the repo root.

## Workflow

0. `tidal2ytm auth [--ytm-only|--tidal-only] [--re-auth] [--client-id X --client-secret Y]` — create or refresh OAuth tokens. Default authenticates both YTM and Tidal; a provider whose cached token still validates is skipped. YTM flow wraps `ytmusicapi.setup.setup_oauth`; Tidal flow wraps `tidalapi.Session.login_oauth`.
1. `tidal2ytm plan [--force]` — fetch `tidal_source.py:get_liked_tracks` → `matcher.py:match_track` → merge into `data/transfer_plan.toml`. Done when the plan exists, `transferred` tracks are skipped, and `[meta]` is recomputed via `plan_io.py:update_plan_meta`. `--force` overwrites a better match without prompting; otherwise prompt `[y/N]` per improved match.
2. `tidal2ytm review` — rich TUI for low-confidence matches. Done when the first write triggers `backup_plan()` to `transfer_plan.YYYYMMDD_HHMMSS.toml` and decisions persist immediately.
3. `tidal2ytm transfer --track <11-char-id> | --album <match_id> | --artist <match_id> | --all [--dry-run]` — exactly one scope required. Done when `transfer.py` saves the plan after each track and `pending` → `transferred`/`failed` is reflected in `[meta]`.
4. `tidal2ytm status [--artist <match_id>] [--album <match_id>]` — offline-safe. Smoke test: `tidal2ytm --help` succeeds.

`uv run tidal2ytm <subcmd> ...` works equivalently from the repo root and is preferred when iterating on the source.

## Project layout

```
.
├── pyproject.toml        # uv-managed project; tidal2ytm = "tidal2ytm.cli:main"
├── .pre-commit-config.yaml
├── .github/workflows/ci.yml
├── README.md
├── data/                 # runtime state (git-ignored)
│   ├── transfer_plan.toml            # source of truth
│   ├── transfer_plan.YYYYMMDD_HHMMSS.toml   # review backup
│   ├── ytm_auth.json                 # cached YTM token
│   ├── client_secret_*.json         # Google Cloud OAuth client (TVs and Limited Input devices)
│   └── tidal_token.json              # cached Tidal token
├── tidal2ytm/
│   ├── __init__.py
│   ├── auth.py           # `auth` subcommand: run_ytm_auth, run_tidal_auth
│   ├── cli.py            # argparse entry, OAuth wiring, subcommand dispatch
│   ├── matcher.py        # ISRC / duration / fuzzy ranking
│   ├── models.py         # TrackStatus, MatchMethod, dataclasses
│   ├── paths.py          # DATA_DIR + token/plan paths; DATA_DIR.mkdir() on import
│   ├── plan.py           # `plan` subcommand: build/update transfer_plan.toml
│   ├── plan_io.py        # TOML load/save, _extract_video_id, iter/find helpers
│   ├── review.py         # `review` subcommand: rich TUI
│   ├── slugs.py          # artist_slug, album_slug, dedup_slugs (owns all slug logic)
│   ├── tidal_source.py   # get_liked_tracks
│   ├── transfer.py       # `transfer` subcommand
│   └── ytm_sink.py       # add_track_to_library (get_watch_playlist + edit_song_library_status)
└── tests/                # pytest; isolated via tests/conftest.py:isolated_data_dir
```

## Quality gates

`pyproject.toml` defines `tool.ruff`, `tool.pyright` (`typeCheckingMode = "strict"`, `reportMissingImports = false`), `tool.pytest`, and `tool.coverage` (report-only until sustained ≥80% coverage, at which point uncomment `fail_under` and add `--cov-fail-under` to the pre-push hook and CI).

The pre-commit, pre-push, and GitHub Actions workflows exercise these tools. Reproduce them locally with:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pyright`
- `uv run pytest -q`
- `uv run pytest --cov --cov-report=term-missing -q`

## Invariants

- `tidal_id` is track identity; tracks have no `match_id`. Tracks are addressed by a bare 11-char `yt_video_id` — never a URL. `plan_io.py:_extract_video_id` normalizes every accepted URL form; `review.py` deliberately imports this private helper.
- `match_id` is `artist_slug`/`album_slug` with album slugs capped at 15 chars and `-2`/`-3` dedup. All slug logic lives in `slugs.py`.
- `TrackStatus` and `MatchMethod` are string enums whose `.value` matches the TOML representation; the persisted plan file is the source of truth for `pending | transferred | skip | failed | needs_review`.
- `[meta]` is recomputed via `plan_io.py:update_plan_meta()` after any status change. `transfer.py` and `review.py` are responsible for calling it through `save_plan`.
- `cli.py` owns all OAuth wiring. The `yt._session.post` monkey-patch in `_ytm_login` (TVHTML5 client context, swap to WEB_REMIX for `/search?` and `/player?`, strip `authorization`/`X-Goog-Request-Time` for those endpoints) is load-bearing. `auth.py` is the interactive entry point but shares `cli.py`'s session/header logic.
- `paths.py` runs `DATA_DIR.mkdir()` on import. Tests redirect `DATA_DIR`, `YTM_AUTH_FILE`, `TIDAL_TOKEN_FILE`, and `PLAN_FILE` via `tests/conftest.py:isolated_data_dir`; the `DATA_DIR.mkdir` side effect itself is the contract and is never mocked. `STATE_FILE` and `REVIEW_FILE` are intentionally stale; never write them.
- `ytm_sink.add_track_to_library` uses `get_watch_playlist` + `edit_song_library_status` with `feedbackTokens.add`. `rate_song` / `LikeStatus.LIKE` only thumb-up a track and are wrong here.
- Every module begins with `from __future__ import annotations`; data containers are dataclasses; public functions are type-annotated.

## Disclosed reference

- OAuth setup, Google Cloud client creation, and the review TUI key map → `README.md` (Authenticate section).
