# Main TUI as gateway design

Date: 2026-09-11. Goal: bare `tidal2ytm` opens a main TUI that gateways everything the app can do (plan, match, review, transfer, auth) while the CLI subcommands keep working unchanged.

## Decisions locked during brainstorming

The in-process gateway won over subprocess dispatch (slow, loses session state, TTY quoting pain) and read-only hints (does not meet the do-everything goal). Transfer from the menu is pending-only and never includes needs_review; actual transfer sits on `t` and dry-run transfer sits on `d`, each with its own `Y/n` confirm. Review from the menu opens the full review TUI, not a filtered subset. Status has no separate entry and is always displayed on the menu from the plan file meta, with zeros when no plan exists. Auth is an action entry prompting scope both/tidal/ytm defaulting to both; the menu shows cheap file presence only (no network per render) and full expiry checks run inside the auth action and before transfer.

## Architecture and CLI surface

Bare `tidal2ytm` stays in `planning.py:run_planning` and `_tui_loop`, which becomes the gateway. New menu entries call `review.run_review`, `transfer.run_transfer`, `plan_io` meta readers, and `auth.run_ytm_auth` plus `auth.run_tidal_auth` in-process; the `argparse` subcommands in `cli.py` remain as thin wrappers over the same functions with unchanged flags and semantics. The plan file remains the source of truth and the menu re-reads it on each render for live counts. The YTM login reuses the existing `cli._ytm_login` wiring including the TVHTML5 client patch, and the Tidal session fetched once at startup is reused.

## Menu components and keys

Existing keys `e`, `/`, `s`, `v`, `m`, `ctrl+o`/`O`, `?`, `h`, and `q` keep their behaviour. New keys are `r` for full review, `t` for transfer pending-only, `d` for dry-run transfer pending-only, and `a` for the auth action; all work in TTY readchar mode and fallback line mode, and the help text plus footer hints are updated. The menu body always shows plan counts (total, pending, needs_review, transferred, skip, failed) and cheap auth file presence (`ytm_auth.json` plus `client_secret_*.json` present or missing, `tidal_token.json` present or missing) with no network calls during render. A single TOML read per render is the expected cost; if large plans prove slow, an mtime cache is the approved follow-up, not live network probing.

## Data flow

Menu render performs only local reads: plan meta via `plan_io.load_plan` (zeros when the file is absent) and auth file presence via `paths.DATA_DIR` globs. Review runs its nested TUI in-process and returns to the main loop, after which the re-render picks up new counts. Match keeps its current write path (merge, `update_plan_meta`, `backup_plan` on first write, `save_plan`) and returns. Transfer entries confirm with `Y/n`, then call `_ytm_login` and `run_transfer` with `all_tracks=True`, `dry_run` false for `t` and true for `d`, `include_needs_review=False`, and the shared plan path; the existing per-track output shows progress and the action pauses before the menu redraws. The auth action prompts for scope (both/tidal/ytm, default both), calls the matching `auth_mod.run_*` functions with `force=False` so valid cached tokens are skipped, reports per-provider results, and pauses.

## Errors, interruptions, and edge cases

A missing plan file shows zeros on the menu; review and transfer entries print the same guidance as the CLI (build a plan first via the planning flow) and pause instead of crashing. YTM auth expiry surfaces the existing re-auth guidance through the `SystemExit` path and pauses, preserving the selection and returning to the menu. The auth action reports per-provider success or failure without aborting the session. `Ctrl+C` or EOF during a nested confirm returns to the menu where the surrounding code already handles it, while `Ctrl+C` at the top-level key read quits the session; matched and saved work persists because every writer saves through `save_plan` with refreshed meta. Unexpected exceptions from nested actions print a red message and pause rather than killing the TUI.

## Testing and quality gates

Pure helpers (plan-count reader, auth file-presence reader, menu-body count rendering) get unit tests with synthetic plans and isolated data dirs. TUI dispatch for `r`, `t`, `d`, and `a` is tested with mocked `run_review`, `run_transfer`, and auth functions plus stubbed input, covering confirms, declines, missing-plan guidance, and pause behaviour. The existing planning, review, transfer, matcher, and CLI suites keep passing, and the gates stay `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, and `uv run pytest -q`. `README.md` usage and `AGENTS.md` workflow notes are updated so the main TUI reads as the gateway and the subcommands read as scriptable equivalents.

## Out of scope

No new plan file format, no transfer scopes beyond all-pending from the menu (scoped transfers stay CLI-only), no needs_review transfers from the menu, no live auth probing on menu render, no refactor of the review session code beyond the import, and no removal of any CLI subcommand.
