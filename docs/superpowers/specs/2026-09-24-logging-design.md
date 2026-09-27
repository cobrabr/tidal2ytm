# Logging design

Date: 2026-09-24
Status: approved design, pending implementation plan
Owner: tidal2ytm

## Purpose

Diagnose matching, auth, and transfer problems after the fact. When matching misbehaves, the operator should be able to open one log file and see what was attempted, what the algorithm decided, and why — without reproducing the run or copying console output by hand. Console output stays the interactive UI and is unchanged by this design.

## Approach

Python standard library `logging`. No new dependency. Levels, formatters, handlers, and per-module loggers are all native; third-party transport libraries (`requests`, `urllib3`, `ytmusicapi`) already log through the stdlib root logger, so their transport-level signals land in the same file without bridges.

## Level vocabulary

CLI-accepted level names, case-insensitive, mapped onto stdlib levels:

| Name | Stdlib | Meaning |
|---|---|---|
| `OFF` | (no handler) | Logging disabled entirely; no log file is created. |
| `CRITICAL` | CRITICAL (50) | Catastrophic conditions only: connection-level failure (unreachable network, DNS failure, refused connection) aborting an operation such as auth, liked-track fetch, or transfer. Ordinary errors are below this level. |
| `ERROR` | ERROR (40) | Operation failures: swallowed album-fetch failures, album resolve failures, per-track fallback errors, auth failures, transfer failures. |
| `WARN` | WARNING (30) | Degraded outcomes worth scanning for: album abstentions (close call, no in-album fit, no resolve), per-track fallback routing. |
| `INFO` | INFO (20) | The always-on core: every YTM/Tidal network call (endpoint, duration, outcome), auth login/refresh steps, album resolve verdicts (winner album, per-track mapping), per-track transfer results, liked-tracks fetch. |
| `DEBUG` | DEBUG (10) | Verbose detail: candidate scoring detail, cache hits, request/response payloads, plan saves and backups. |

Default level: `INFO`. All accepted names map one-to-one onto stdlib level names.

## Control surface

- Top-level CLI flag on every entry point: `tidal2ytm --log-level DEBUG review`. One flag, defined on the root parser, honoured by all subcommands and the interactive menu.
- Environment variable `TIDAL2YTM_LOG_LEVEL` as the fallback when the flag is absent.
- Precedence: flag > env var > default `INFO`.
- Invalid level names are a hard CLI error (argparse choice validation), not a silent fallback.
- The interactive main menu inherits the level the process launched with. No runtime toggle.

## File handling

- Directory: `logs/`, a sibling of `data/`, added to `.gitignore`.
- One file per process run: `logs/YYYYMMDD-HHMMSS-ffffff.log` (microsecond precision, matching the plan-backup stamp convention).
- Files are kept forever; CLI runs are short and files are small.
- `OFF` attaches no handler and creates no file or directory.
- The directory is created lazily at logger setup, never on import (same rule as `paths.py`).
- The directory is resolved relative to the repo root, alongside `data/`.

## New module: `logging_setup.py`

Single owner of everything logging-related:

- `LOG_LEVELS`: mapping of CLI level names to stdlib levels, including `OFF`.
- `resolve_level(flag_value: str | None, env_value: str | None) -> str`: precedence resolution and case-insensitive name mapping; returns the level *name* (e.g. `"DEBUG"`), raising `ValueError` on an unknown name; `OFF` is returned as a name and handled by `setup_logging`.
- `setup_logging(level_name: str) -> Path | None`: creates the per-run file handler and formatter when the level is not `OFF`, attaches it to the root logger, returns the log path (or `None` for `OFF`). Formatter: `%(asctime)s %(levelname)-5s %(name)s %(message)s`.
- `log_call(logger, endpoint, func, *args, **kwargs)`: adapter that wraps one network call, records duration in milliseconds, and logs the outcome at `INFO` (or `ERROR`/`CRITICAL` per the table above on failure) before propagating the result or exception.

`from __future__ import annotations` first line; public functions type-annotated; no disk access on import.

## Emit sites

Module loggers via `logging.getLogger(__name__)`; each external-facing module gains one logger and the calls below. Console output is never used for log records.

- `ytm_client.py`: every outbound request logged at `INFO` with endpoint path and duration; connection-level failures at `CRITICAL`, other failures at `ERROR`. Search/player/browse endpoint swapping noted in the endpoint field.
- `tidal_source.py`: liked-tracks fetch at `INFO`; connection-level failure at `CRITICAL`.
- `album_matching.py`: album search at `INFO`; per-candidate tracklist fetch at `INFO` (cache hits at `DEBUG`); candidate scores at `DEBUG`; resolve verdict at `INFO` for a win (winner album title) and `WARN` for abstention with the abstain reason; unreachable-candidate skip at `ERROR`.
- `matcher.py`: per-track search at `INFO`; candidate scores and final selection at `DEBUG`.
- `planning.py`: fallback routing at `WARN` with the chained reason; per-track match failures at `ERROR`; group resolve failure at `ERROR`.
- `auth.py`: login and refresh steps at `INFO`; failures at `ERROR`; connection-level failure at `CRITICAL`.
- `transfer.py` / `ytm_sink.py`: per-track add results at `INFO`; failures at `ERROR`; batch summary at `INFO`.
- `plan_io.py`: plan save, backup, and metadata recompute at `DEBUG`.

## Third-party records

`requests`, `urllib3`, and `ytmusicapi` propagate to the root logger unchanged. Their warnings are visible from `INFO` upward; their debug chatter appears only at `DEBUG`. No capture bridges are added.

## Errors and edge cases

- Invalid `--log-level` value: argparse rejects the run; nothing executes.
- `logs/` not writable: logger setup raises a clear `RuntimeError` naming the path rather than silently dropping records.
- `OFF`: no file, no directory creation, zero overhead beyond flag parsing.
- Log records never contain tokens, secrets, or full authorization headers. Endpoints are logged as paths with query strings containing credentials redacted; request/response payload logging at `DEBUG` redacts token-bearing fields.
- Timezone: timestamps use local time via the default formatter.

## Testing

- Level-name parsing: all six names round-trip; case-insensitive; invalid name raises.
- Precedence: flag beats env beats default; absent both → `INFO`.
- `OFF` attaches no handler, creates no file.
- Per-run file naming: timestamped file created inside `logs/`; two setups in one process reuse the same run file.
- Emit-site integration: one structural assertion per site class — an album resolve win produces an `INFO` record naming the winner album; an abstention produces a `WARN` with the reason; a plan save produces a `DEBUG` record; a connection failure produces a `CRITICAL` record. Assertions inspect record level/name/message membership through `caplog` or a memory handler, never exact formatted strings.
- No test locks colours, glyphs, or console copy; console output tests are untouched by this design.

## Out of scope

- Runtime level switching, log rotation, compression or pruning of old logs, structured JSON output, remote shipping, console echo of log records.
