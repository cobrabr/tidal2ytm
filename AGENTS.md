# AGENTS.md

Python CLI (`tidal2ytm`) that transfers Tidal liked tracks to YouTube Music — ISRC > duration (±4 s) > fuzzy, managed with `uv` from the repo root.

## Workflow

0. `tidal2ytm auth [--ytm-only|--tidal-only] [--re-auth] [--client-id X --client-secret Y]` — create or refresh OAuth tokens. Default authenticates both YTM and Tidal; a provider whose cached token still validates is skipped. YTM flow wraps `ytmusicapi.setup.setup_oauth`; Tidal flow wraps `tidalapi.Session.login_oauth`.
1. `tidal2ytm` (no args) — main-menu gateway (`planning.py`): fetch `tidal_source.py:get_liked_tracks` once → search/filter locally → accumulate a selection → explicit match via `matcher.py:match_track` → merge into `data/transfer_plan.toml`, with review (`r`), transfer pending (`t`), dry-run (`d`), and auth (`a`) from the main menu; the `review`/`transfer`/`auth` subcommands remain as scriptable equivalents. Done when the selection is matched, `transferred` tracks are skipped, and `[meta]` is recomputed via `plan_io.py:update_plan_meta`. Improved matches prompt `[y/N]` per track unless the `ctrl+o` override banner is on.
2. `tidal2ytm review` — rich TUI for low-confidence matches. Done when the first write triggers `backup_plan()` to `transfer_plan.YYYYMMDD_HHMMSS.toml` and decisions persist immediately.
3. `tidal2ytm transfer --track <11-char-id> | --album <match_id> | --artist <match_id> | --all [--dry-run]` — exactly one scope required. Done when `transfer.py` batches in-memory updates, backs up once, and writes atomically at end (plus once on first failure); `pending` → `transferred`/`failed` reflected in `[meta]`.
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
│   ├── confidence.py     # confidence bands (color_for, is_certain)
│   ├── errors.py         # Tidal2YtmError hierarchy (PlanNotFoundError, InvalidScopeError)
│   ├── format.py         # fmt_duration (seconds → m:ss)
│   ├── keys.py           # shared terminal key/line reading (raw, Windows console, fallback)
│   ├── matcher.py        # ISRC / duration / fuzzy ranking
│   ├── models.py         # TrackStatus, MatchMethod, dataclasses
│   ├── paths.py          # DATA_DIR + token/plan paths; ensure_data_dir() (never touches disk on import)
│   ├── picker_rows.py    # pure picker row model: ListRow/PickerView, build_rows, toggles, viewport
│   ├── planning.py       # interactive planning TUI (bare `tidal2ytm`): selection + match action
│   ├── planning_merge.py # pure match/merge helpers (track-dict conversion, merge policy, insertion)
│   ├── planning_search.py # pure library search (two-tier ranking, Tidal-link resolution)
│   ├── plan_io.py        # TOML load/save, extract_video_id, iter/find helpers
│   ├── review.py         # `review` subcommand: rich TUI
│   ├── slugs.py          # artist_slug, album_slug, dedup_slugs (owns all slug logic)
│   ├── style.py          # STATUS_STYLE (status → rich style)
│   ├── text.py           # text normalization (normalize, similarity)
│   ├── tidal_source.py   # get_liked_tracks
│   ├── transfer.py       # `transfer` subcommand
│   ├── ytm_client.py     # YTMClient: authenticated YTMusic build (TVHTML5 + WEB_REMIX patched POST)
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

Tests use fictional artist/track names and synthetic IDs only, never real catalogue data.

## Versioning

- `pyproject.toml:version` is the release number (bare `X.Y.Z`, no prefix). The feature branch sets the upcoming version once its scope is known (patch for bug fixes with no behaviour change, minor for new features and CLI surface changes, major for breaking `transfer_plan.toml` format or token/auth changes); no repeated bumps per change on the branch. Tags are created on `main` only, at release time.
- Keep `uv.lock` consistent: after bumping, run the gates once so the build rewrites the root-package version line, and stage `uv.lock` in the same commit. If the pre-commit pytest hook aborts with "files were modified", that rewrite is the cause — stage the lockfile and recommit.
- Tag the release commit with `git tag vX.Y.Z` (`v` prefix, matching `version`). Tags stay local unless the release is pushed.

## Invariants

- `tidal_id` is track identity; tracks have no `match_id`. Tracks are addressed by a bare 11-char `yt_video_id` — never a URL. `plan_io.py:extract_video_id` normalizes every accepted URL form. Video-id parsing is public `plan_io.extract_video_id`; first-party cross-module private imports in `tidal2ytm/` prod code are banned (pyright `reportPrivateUsage` must stay clean there; known exceptions are the test-file convention and justified third-party private access).
- `match_id` is `artist_slug`/`album_slug` with album slugs capped at 15 chars and `-2`/`-3` dedup. All slug logic lives in `slugs.py`.
- `TrackStatus` and `MatchMethod` are string enums whose `.value` matches the TOML representation; the persisted plan file is the source of truth for `pending | transferred | skip | failed | needs_review`.
- `[meta]` is recomputed via `plan_io.py:update_plan_meta()` after any status change. `transfer.py` calls it through `plan_io.save_with_meta` while `review.py` and `planning.py` use the `update_plan_meta` + `save_plan` equivalent (meta + atomic write); never call `save_plan` in a loop.
- `auth.py` owns token validity, client-secret reading, and the tidal token schema; `ytm_client.YTMClient` owns the session/header logic (WEB_REMIX swap for `/search?` and `/player?`) transplanted from `cli._ytm_login`. `cli.py` only wires argparse → runners.
- `paths.py` never touches disk on import; entry points call `ensure_data_dir()`. `STATE_FILE`/`REVIEW_FILE` were deleted; do not reintroduce stale path constants.
- `ytm_sink.add_track_to_library` uses `get_watch_playlist` + `edit_song_library_status` with `feedbackTokens.add`. `rate_song` / `LikeStatus.LIKE` only thumb-up a track and are wrong here.
- Every module begins with `from __future__ import annotations`; data containers are dataclasses; public functions are type-annotated.
- `sys.exit` lives in `cli.main()` (and `--help` via argparse) plus the interactive error paths of `auth.run_ytm_auth` (missing/unparseable secrets, expired token). Library runners (`run_transfer`, `run_review`, logins, `YTMClient.login`) raise `errors.Tidal2YtmError` subclasses; tests assert raises, never `SystemExit` except at `main()`/auth-entry level.
- No `except Exception` / `suppress(Exception)` around auth probes: those catch the narrow `_AUTH_ERRORS` tuple only, so transport/auth bugs propagate instead of degrading into "re-authenticate" or "no match". Broad `except Exception` survives only at planning TUI crash barriers and in `ytm_sink`'s documented bool contract (returns `False` on failure).
- No `assert` for user-input validation; raise `ValueError`/`RuntimeError`. Narrow post-condition asserts remain in `planning.py`/`picker_rows.py`/`keys.py`. No `except SystemExit` except at planning TUI boundaries (absorb auth exits, keep the menu alive); no `except (X, Exception)` — the second arm subsumes the first.
- No `MagicMock`-tolerant production code (`hasattr` guards, try/except around attribute access). Boundaries validate (`SourceTrack.from_dict` raises `ValueError` on bad identity); tests still use `MagicMock` for YTM doubles (typed-fakes migration deferred).
- No nondeterminism in persisted ids: slug fallbacks hash (`sha1`), secret-file choice is `sorted()` + strict (0 → `FileNotFoundError`, >1 → `RuntimeError`), backup stamps include microseconds. `secrets`/random never feed plan content.
- One owner per constant: `DURATION_TOLERANCE_SEC`/`CONFIDENCE_THRESHOLD` live in `matcher.py`; `STATUS_STYLE` in `style.py`; confidence bands in `confidence.py`; text normalization in `text.py`. Duplicating a literal instead of importing is a review-blocking defect.
- Plan writes are atomic (temp + `os.replace`) with meta recomputed in the same call (`save_with_meta` in `transfer.py`; `update_plan_meta` + `save_plan` pair in `review.py`/`planning.py`); `update_track_in_plan` returns `bool` but current callers ignore it — check the return before relying on a `False` path. No per-track rewrite loops.
- Tidal token schema is `{token_type, access_token, refresh_token, expiry_time, user_id: int, country_code: str}`; both writers emit all six. Old files missing keys take the full-login path, never `KeyError`. Datetimes stay timezone-aware end to end.
- `transfer_plan.toml` accepts only `pending | transferred | skip | failed | needs_review`; unknown strings raise from `update_plan_meta`. Invalid video ids load as `""` + `needs_review` with a warning; corrupt TOML itself is fatal.
- TUI changes keep the readchar + fallback contract via `keys.read_key()` and one dispatch table per loop — never duplicate a keymap across `if use_readchar/else` branches. No new `C901 noqa` (`matcher.match_track` retains the one pre-existing noqa); split the function instead.
- Tests assert behaviour through public APIs with structural checks (membership, counts, outcomes), never exact colours/glyphs/whitespace/copy, never stdlib internals (`getsource`), never duplicated threshold suites. New constants need threshold-edge tests, not value locks.

## Disclosed reference

- OAuth setup and Google Cloud client creation → `README.md` (Authenticate section); review and planning TUI key maps → `README.md` (Usage).
