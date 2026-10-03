# AGENTS.md

Python CLI (`tidal2ytm`) that transfers Tidal liked tracks to YouTube Music — album-anchored matching with duration and fuzzy fallbacks, managed with `uv` from the repo root.

## Workflow

0. `tidal2ytm auth [--ytm-only|--tidal-only] [--re-auth] [--client-id X --client-secret Y]` — create or refresh OAuth tokens. Default authenticates both YTM and Tidal; a provider whose cached token still validates is skipped. YTM flow wraps `ytmusicapi.setup.setup_oauth`; Tidal flow wraps `tidalapi.Session.login_oauth`.
1. `tidal2ytm` (no args) — main-menu gateway (`planning.py`): fetch `tidal_source.py:get_favorite_tracks` once → search/filter locally → accumulate a selection → explicit match via `matcher.py:match_track` → merge into `data/transfer_plan.toml`, with review (`r`), transfer pending (`t`), dry-run (`d`), and auth (`a`) from the main menu; the `review`/`transfer`/`auth` subcommands remain as scriptable equivalents. Done when the selection is matched, `transferred` tracks are skipped, and `[meta]` is recomputed via `plan_io.py:update_plan_meta`. Improved matches prompt `[y/N]` per track unless the `ctrl+o` override banner is on.
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
│   ├── logging_setup.py   # LOG_LEVELS, resolve_level, setup_logging, setup_stderr_logging, log_call, redact_payload, redact_text (owns all logging config)
│   ├── matcher.py        # duration / fuzzy ranking for a single track
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
│   ├── tidal_source.py   # get_favorite_tracks
│   ├── transfer.py       # `transfer` subcommand
│   ├── ytm_client.py     # YTMClient: authenticated YTMusic build (TVHTML5 + WEB_REMIX patched POST)
│   └── ytm_sink.py       # add_track_to_library (get_watch_playlist + edit_song_library_status)
└── tests/                # pytest; isolated via conftest.py:isolated_data_dir
```

`sidecar/` is a second uv workspace member (`tidal2ytm_sidecar`) holding the NDJSON sidecar: `protocol.py` owns the wire envelopes, `ops.py` answers questions about the outside world, `runner.py` is the read/answer loop, and `sidecar_logging.py` reads the host-supplied level. `contracts/*.json` is the cross-language wire contract, asserted by both the Python and the Rust suites.

`crates/`, `src-tauri/`, and `src/` are the desktop GUI's three roots. `crates/tidal2ytm-core` and `crates/tidal2ytm-ipc` are Tauri-free libraries — `Cargo.toml`'s `default-members` is what keeps a bare `cargo test` off the host on a Linux runner with no webkit — and `Cargo.lock` plus `pnpm-lock.yaml` are committed, so CI's frozen installs match. `src-tauri/` is the host package (`tidal2ytm`, library target `tidal2ytm_host`): `sidecar_path.rs` works out how to start the sidecar, `state.rs` owns the client and the wire-shaped `AuthStatus`, `commands.rs` is the command surface, `logging.rs` is the DEBUG stderr sink Phase 4 replaces with the log file, and `main.rs` is the entry point. `capabilities/default.json` grants `core:default` and no plugin permissions, so the frontend cannot spawn a process at all. `src/` is the Svelte 5 frontend, and `src/lib/ipc.ts` is the only place `invoke` is called.

## Quality gates

`pyproject.toml` defines `tool.ruff`, `tool.pyright` (`typeCheckingMode = "strict"`, `reportMissingImports = false`), `tool.pytest`, and `tool.coverage` with an enforced `fail_under = 80` gate (pre-push hook runs `pytest -q -x --cov --cov-fail-under=80`; CI runs `pytest --cov --cov-fail-under=80 ...`).

The pre-commit, pre-push, and GitHub Actions workflows exercise these tools. Reproduce them locally with:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pyright`
- `uv run pytest -q`
- `uv run pytest --cov --cov-report=term-missing -q`
- `pnpm format` → `pnpm lint:fix` → `pnpm lint` → `pnpm format:check` → `pnpm check` → `pnpm test` → `pnpm build`
- `cargo fmt --all --check` · `cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc --all-targets -- -D warnings` · `cargo clippy -p tidal2ytm --all-targets -- -D warnings` · `cargo test -p tidal2ytm`

Tests use fictional artist/track names and synthetic IDs only, never real catalogue data.

## CI parity (CI is Linux-only; dev is usually Windows)

- CI (`.github/workflows/ci.yml`, ubuntu-latest) runs `check-toml`, `ruff check`, `ruff format --check`, `pyright`, then the full suite as `uv run pytest --cov --cov-fail-under=80 --cov-report=xml --cov-report=term-missing` (no `-x`, no `-q`). Run that exact sequence before pushing — the pre-push hook's `pytest -q -x` can hide order-dependent and coverage-path failures.
- Platform-branched code (`os.name`, `termios`/`msvcrt` guards) has a path CI exercises that Windows never does: force both branches in tests (monkeypatch `os.name`, stub the guarded import), because a green local run proves nothing about the Linux path.
- Test doubles must implement the full interface production code touches (a stdin stand-in needs `fileno()` when the reader calls it); never stub narrower than the real collaborator. When production branches on a capability (`termios` import, `isatty`), the double must drive the same branch CI will take.
- **ALWAYS watch the CI run after a `git push`**: `gh run list` to find it, `gh run watch <run-id> --exit-status` to block on it. The pre-push hook runs on the dev platform, so local green proves nothing. Markdown-only pushes queue no run (`paths-ignore: ['**.md']`), so there's nothing to watch.

## Versioning

- `pyproject.toml:version` is the release number (bare `X.Y.Z`, no prefix). The feature branch sets the upcoming version once its scope is known (patch for bug fixes with no behaviour change, minor for new features and CLI surface changes, major for breaking `transfer_plan.toml` format or token/auth changes); no repeated bumps per change on the branch. Tags are created on `main` only, at release time.
- Keep `uv.lock` consistent: after bumping, run the gates once so the build rewrites the root-package version line, and stage `uv.lock` in the same commit. If the pre-commit pytest hook aborts with "files were modified", that rewrite is the cause — stage the lockfile and recommit.
- Tag the release commit with `git tag vX.Y.Z` (`v` prefix, matching `version`). Tags stay local unless the release is pushed.

## Invariants

- `tidal_id` is track identity; tracks have no `match_id`. Tracks are addressed by a bare 11-char `yt_video_id` — never a URL. `plan_io.py:extract_video_id` normalizes every accepted URL form. Video-id parsing is public `plan_io.extract_video_id`; first-party cross-module private imports in `tidal2ytm/` prod code are banned (pyright `reportPrivateUsage` must stay clean there; known exceptions are the test-file convention and justified third-party private access).
- `match_id` is `artist_slug`/`album_slug` with album slugs capped at 15 chars and `-2`/`-3` dedup. All slug logic lives in `slugs.py`.
- `TrackStatus` and `MatchMethod` are string enums whose `.value` matches the TOML representation; the persisted plan file is the source of truth for `pending | transferred | skip | failed | needs_review`.
- `[meta]` is recomputed via `plan_io.py:update_plan_meta()` after any status change. `transfer.py` calls it through `plan_io.save_with_meta` while `review.py` and `planning.py` use the `update_plan_meta` + `save_plan` equivalent (meta + atomic write); never call `save_plan` in a loop.
- `auth.py` owns token validity, client-secret reading, the tidal token schema, and the two token files' expiry formats — `ytm_expiry_iso` is why a `ytmusicapi` epoch integer never reaches the wire; `ytm_client.YTMClient` owns the session/header logic (WEB_REMIX swap for `/search?` and `/player?`) transplanted from `cli._ytm_login`. `cli.py` only wires argparse → runners.
- `paths.py` never touches disk on import. `ensure_logs_dir()` is called by `logging_setup.setup_logging`; `ensure_data_dir()` currently has **no production caller** — the CLI entry points stopped calling it and the sidecar only reads the token caches — so treat it as dead until something needs it rather than as a rule to follow. `STATE_FILE`/`REVIEW_FILE` were deleted; do not reintroduce stale path constants.
- `ytm_sink.add_track_to_library` uses `get_watch_playlist` + `edit_song_library_status` with `feedbackTokens.add`. `rate_song` / `LikeStatus.LIKE` only thumb-up a track and are wrong here.
- Logging is file-only: records go to the per-run `logs/` file, never to the console, and console behaviour never changes for the sake of logging. `logging_setup.py` owns levels, precedence, formatter, handler, and the per-run file; other modules only `logging.getLogger(__name__)` and emit — never reconfigure handlers or the root logger.
- `--log-level` flag beats `TIDAL2YTM_LOG_LEVEL` env beats default `INFO`; the accepted set is owned by `logging_setup.LOG_LEVELS`. An invalid env value is a hard CLI error, never a silent fallback. The GUI host passes the same env var to the sidecar at spawn, so the level applies even if the sidecar dies before its handshake.
- Log records never contain tokens, client secrets, or authorization headers; DEBUG payload logging uses `logging_setup.redact_payload` and logs payload shape (key names), never token values.
- `log_call`'s `except Exception` arm logs and always re-raises — a documented observer, not a suppressor; do not reuse the pattern to swallow errors elsewhere.
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
- `contracts/auth_status.json` records the **signed-out** sample on purpose. `src/lib/types.ts` derives its payload types from that file and `Widen` turns the `null` literal into `string | null`, so replacing the sample with a signed-in payload silently narrows the frontend field to `string` and the nullable case stops compiling — the contract's key set is authoritative, its *values* are not.
- The sidecar's `expires_at` is an ISO-8601 UTC string or `None` and nothing else, and both providers go through `auth.py` (`tidal_expiry_iso` for the string `auth.py` itself writes, `ytm_expiry_iso` for the epoch integer `ytmusicapi` writes). `ops._cached` returns `dict[str, object]`, never `dict[str, Any]`: `json.loads` hands back `Any`, and `Any` launders through pyright in strict mode without a complaint, which is exactly how a raw epoch integer reached the wire while every gate stayed green. `dict[str, object]` plus the `_ProviderState` return types make removing either normalisation a `reportReturnType` compile error.
- The host's DEBUG log sink is installed by `state`/`run()`, never by `main()` alone, because `run()` is what drains the sidecar's stderr into the `log` facade. Level and logger are set as separate statements: `set_logger` failing skips `set_max_level`, and `MAX_LOG_LEVEL_FILTER` defaults to `Off`, so the skipped level is a silent loss of every forwarded diagnostic.
- `invoke` rejects with whatever a Rust command returned, and a command declared `Result<T, String>` therefore rejects with a **bare string**. `src/lib/ipc.ts` normalises every rejection into an `Error` so a screen can rely on `instanceof`; without that, a screen that checks `instanceof Error` shows a generic line and discards the only text that says what went wrong.
- `$state<T | null>(null)` that is never assigned is a non-reactive constant to the Svelte compiler, so `{#if x !== null}` folds away and the branch — scoped CSS included — is dropped from `dist/` with no warning from any gate. Assign it, and assert that each screen renders what it imports: a card missing from a bundle that builds clean is the failure mode, not a hypothetical.
- `SidecarClient::spawn` hands the child's stderr to its caller and nobody else can drain it. Dropping that receiver does not close the pipe: the sidecar blocks writing its own diagnostics once the OS buffer fills, and every request it owed then times out with no cause. The host takes it and holds it for the process's life; a spawn that fails takes the child's diagnostics with it, so the failure message names the command it tried.
- The host respawns on `IpcError::SidecarLost` — the error the client reports once it has seen EOF — and never on `is_alive`, which is still `true` in the window between the child dying and that EOF arriving. Identity (`Arc::ptr_eq`), not a flag, decides whether a respawn is needed, which is also what keeps a burst of failed calls from spawning one sidecar each.
- `sidecar_path::resolve` looks for `tidal2ytm-sidecar.exe` **without** a target triple: `tauri_build` strips the triple from the name it copies next to the binary, so a `binaries/tidal2ytm-sidecar-<triple>.exe` candidate can never exist. A directory handed to a child as its working directory must exist first — `app_data_dir()` does not on a first run, and a missing one fails the spawn with `os error 267`, which names neither the path nor the reason.
- Building the host needs `dist/` and a `src-tauri/binaries/tidal2ytm-sidecar-<triple>.exe` placeholder on disk: `tauri_build` refuses a config whose `frontendDist` is missing and an `externalBin` whose file is absent, and `icons/icon.ico` is required on Windows. Run `pnpm build` first, and create the placeholder as an empty file that is never committed (`.gitignore` already excludes `src-tauri/binaries/`).

## Disclosed reference

- OAuth setup and Google Cloud client creation → `README.md` (Authenticate section); review and planning TUI key maps → `README.md` (Usage).
