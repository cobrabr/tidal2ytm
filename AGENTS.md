# AGENTS.md

Python CLI (`tidal2ytm`) that transfers Tidal liked tracks to YouTube Music — ISRC > duration (±4 s) > fuzzy, managed with `uv` from repo root. No tests, lint, typecheck, or CI — verify manually.

## Workflow — ordered steps

1. `uv run tidal2ytm plan [--force]` — fetch `tidal_source.py:get_liked_tracks` → `matcher.py:match_track` → merge into `data/transfer_plan.toml`. Done when plan exists, `transferred` tracks skipped, and `[meta]` recomputed via `plan_io.py:update_plan_meta`. `--force` overwrites a better match without prompting; otherwise prompt `[y/N]` per improved match.
2. `uv run tidal2ytm review` — rich TUI for low-confidence matches. Done when first write triggers `backup_plan()` to `transfer_plan.YYYYMMDD_HHMMSS.toml` and decisions persist immediately.
3. `uv run tidal2ytm transfer --track <11-char-id> | --album <match_id> | --artist <match_id> | --all [--dry-run]` — exactly one scope required. Done when `transfer.py` saves plan after each track and `pending` → `transferred`/`failed` reflected in meta.
4. `uv run tidal2ytm status [--artist <match_id>] [--album <match_id>]` — offline-safe. Smoke test: `uv run tidal2ytm --help` succeeds.

## Reference — invariants and ownership

- Identity: `tidal_id` is track identity; tracks have no `match_id`, addressed by bare `yt_video_id` (11-char, never a URL). `plan_io.py:_extract_video_id` normalizes every URL form (`youtube.com`, `music.youtube.com`, `youtu.be`); `review.py` deliberately imports this private helper.
- `match_id` = `artist_slug`/`album_slug` (album ≤15 chars, `-2`/`-3` dedup). All slug logic lives in `slugs.py` — keep there.
- Statuses `pending | transferred | skip | failed | needs_review`; `TrackStatus`/`MatchMethod` string-enum `.value` matches TOML (`models.py`).
- `cli.py` owns all OAuth. `_ytm_login()` monkey-patch of `yt._session.post` (swaps TVHTML5/WEB_REMIX client context, strips auth headers for `/search?` and `/player?`) is load-bearing — do not refactor casually.
- `paths.py:5` runs `DATA_DIR.mkdir()` on import. `data/transfer_plan.toml` is the source of truth via `plan_io.py`; `data/ytm_auth.json` + `data/client_secret_*.json` (TVs and Limited Input devices) and `data/tidal_token.json` are cached OAuth. Leave `STATE_FILE`/`REVIEW_FILE` (`paths.py:11`) stale; never write them.
- `ytm_sink.py` must use `get_watch_playlist` + `edit_song_library_status` (`feedbackTokens.add`); `rate_song`/`LikeStatus.LIKE` only thumbs-up and is wrong.
- Conventions: `from __future__ import annotations` at top of every module, dataclasses, string-enum values match TOML.

## Pointer — disclosed reference

- OAuth setup and TUI keys → `README.md:42` (Google Cloud “TVs and Limited Input devices”, `ytmusicapi oauth` flow, key map).
