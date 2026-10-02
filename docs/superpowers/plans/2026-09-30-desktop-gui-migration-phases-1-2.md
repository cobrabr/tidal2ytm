# Desktop GUI migration — Phases 1 and 2 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove ISRC from the Python codebase, then prove the three-process topology works by building a Rust workspace, a Python sidecar speaking NDJSON, and a Tauri window that spawns the sidecar and renders an `auth.status` result.

**Architecture:** Three processes. The Python sidecar is a thin adapter that answers questions about Tidal and YouTube Music and nothing else. A Rust host owns the domain core, the database, and the sidecar's lifecycle, and is the only process that will ever hold a match score or a plan row. A Svelte 5 frontend renders what the host exposes as Tauri commands. The domain logic lives in sibling crates that do not depend on `tauri`, so `cargo test` runs on a bare Linux runner with no `libwebkit2gtk`.

**Tech stack:** Python 3.14 (uv-managed, hatchling), Rust edition 2024 (`tokio`, `serde`, `serde_json`, `thiserror`), Tauri 2.12.1, Svelte 5.57.1 + Vite 8.3.1 + TypeScript 6.0.3, pnpm 11.28.2, Vitest 5.0.3. Gates: ruff / pyright / pytest at 80% coverage, cargo test / clippy / fmt, svelte-check / ESLint / Prettier.

**Spec:** `docs/superpowers/specs/2026-09-29-desktop-gui-migration-design.md`

**Branch:** `feat/desktop-gui-migration`, created by Task 0. **Nothing in this plan lands on `main` except through a merge of that branch.**

**How to read this plan.** It is long because it carries real code, not because it is complicated. Each task is self-contained: its `**Files:**` block says what it touches, its `**Interfaces:**` block says what it consumes and produces, and nothing in one task depends on reading another. **Read only the task you are assigned** — the sections above the tasks (`Global constraints`, `Review focus`) apply to all of them, and everything after a task belongs to the next one. The tree below is the whole shape of the change; the task index further down maps each file to the task that owns it.

## File structure

Everything this plan creates or changes, and what each piece is for. Paths are relative to the repo root. Files not listed here are untouched. Phases 3–7 of the spec add more; this is the end state of Phases 1 and 2 only.

```
.
├── pyproject.toml                    # modified: version 0.7.0, sidecar as a uv workspace member, testpaths
├── uv.lock                           # modified: rewritten by every `uv sync`; always staged with the change that caused it
├── conftest.py                       # created: moved up from tests/, so sidecar/tests sees isolated_data_dir
├── README.md                         # modified: ISRC removed, build and packaging section added
├── AGENTS.md                         # modified: ISRC removed, log-level + fixture-path + dependency-update invariants
├── .gitignore                        # modified: node_modules, .vite, src-tauri/gen, src-tauri/binaries, sidecar/build
├── .npmrc                            # created: engine-strict=true, so an unsupported Node fails the install
├── .prettierrc.toml                  # created: tabs, width 120, double quotes, trailing commas, svelte parser
├── .prettierignore                   # created: keeps Prettier off Python, Markdown, TOML, and the lockfiles
├── .github/
│   ├── workflows/ci.yml              # modified: rust / host / frontend jobs, path-filtered, check job untouched
│   ├── dependabot.yml                # created: security-updates only, for npm, cargo, and uv
│   └── …
├── contracts/                        # created: the wire format, as data both languages are tested against
│   ├── hello.json                    #   the handshake, including the protocol version and capabilities
│   ├── auth_status.json              #   the one op this plan implements
│   └── error_auth_expired.json       #   the error envelope's shape, pinned before anything can return one
├── crates/
│   ├── tidal2ytm-core/               # the domain core. No tauri, no I/O — this is what keeps CI sane
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs                #   module root only
│   │       └── video_id.rs           #   parse a video ID from a bare ID or any accepted URL shape
│   └── tidal2ytm-ipc/                # the sidecar boundary, also free of tauri so the round-trip test runs headless
│       ├── Cargo.toml
│       ├── src/
│       │   ├── lib.rs                #   module root
│       │   ├── protocol.rs           #   the NDJSON codec, both directions, and the closed error-kind enum
│       │   └── client.rs             #   spawn, handshake, id correlation, per-op timeouts, EOF -> sidecar_lost
│       └── tests/
│           └── round_trip.rs         #   spawns the real Python sidecar and proves one round trip
├── sidecar/                          # a uv workspace member, so one lockfile covers both packages
│   ├── pyproject.toml
│   ├── tidal2ytm-sidecar.spec        # created in Task 11: PyInstaller, produces tidal2ytm-sidecar.exe
│   ├── build_sidecar.ps1             # created in Task 11: Windows build + copy into src-tauri/binaries/
│   ├── tidal2ytm_sidecar/
│   │   ├── __init__.py
│   │   ├── __main__.py               #   `python -m tidal2ytm_sidecar`, the entry point both run and test use
│   │   ├── protocol.py               #   the Python side of the same codec as protocol.rs
│   │   ├── ops.py                    #   the ops. Phase 2 implements auth.status and nothing else
│   │   ├── sidecar_logging.py        #   stderr logging and the shared redaction filter
│   │   └── runner.py                 #   the stdin/stdout loop
│   └── tests/
│       ├── test_protocol.py
│       ├── test_ops.py
│       └── test_runner.py
├── src-tauri/                        # the host. Package name tidal2ytm, so cargo emits tidal2ytm.exe
│   ├── Cargo.toml
│   ├── build.rs                      #   exports the target triple for sidecar path resolution
│   ├── tauri.conf.json               #   productName, window, CSP, externalBin
│   ├── capabilities/default.json     #   core:default only — the frontend cannot spawn anything
│   ├── icons/
│   │   ├── 32x32.png
│   │   ├── 128x128.png
│   │   └── icon.ico
│   ├── binaries/                     #   git-ignored; holds the packaged sidecar for bundling
│   └── src/
│       ├── lib.rs                    #   build + run, sidecar supervision, shutdown on RunEvent::Exit
│       ├── main.rs                   #   the binary entry point
│       ├── sidecar_path.rs           #   where the sidecar is: dev interpreter vs bundled executable
│       ├── state.rs                  #   the shared state holding the SidecarClient
│       └── commands.rs               #   the #[tauri::command] surface — get_auth_status in Phase 2
├── package.json                      # created: every version pinned exactly, engines.node >= 24
├── pnpm-lock.yaml                    # created by pnpm install
├── svelte.config.ts                  # created: vitePreprocess({ script: true }); loaded via Node type stripping
├── vite.config.ts                    # created
├── vitest.config.ts                  # created
├── vitest.setup.ts                   # created
├── tsconfig.json                     # created: includes the two config modules so svelte-check sees them
├── eslint.config.ts                  # created: includeIgnoreFile(.gitignore), eslint-config-prettier last
├── index.html                        # created: the Vite entry document
└── src/                              # the frontend. Presentation and user intent; no domain rules
    ├── main.ts                       #   mounts the app
    ├── App.svelte                    #   the shell, with the left rail
    ├── styles/app.css                #   design tokens; dark mode only, no TUI heritage
    ├── lib/
    │   ├── ipc.ts                    #   the typed wrapper over the host's Tauri commands
    │   ├── router.ts                 #   a hand-rolled client-side router; no SvelteKit
    │   ├── types.ts                  #   AuthStatus and ProviderState
    │   └── components/
    │       ├── AuthStatus.svelte     #   one provider's sign-in state
    │       └── AuthStatus.test.ts
    └── routes/
        └── Home.svelte               #   auth status for both providers, wired to get_auth_status
```

`crates/`, `sidecar/`, `src-tauri/`, and `src/` are the four new roots. The rule that keeps them from tangling: **derived values live in Rust, facts about the outside world live in Python, and the frontend holds neither.** If a file in one of those roots starts making decisions that belong to another, that is the bug this layout exists to prevent.

## Global constraints

These apply to every task. Values are copied from the spec; do not substitute your own.

- **Every name in this project derives from `tidal2ytm`.** The application executable is `tidal2ytm.exe`. Every component with project files of its own is `tidal2ytm-<role>`: the Rust domain crate is `tidal2ytm-core`, the Rust IPC crate is `tidal2ytm-ipc`, the Python sidecar distribution is `tidal2ytm-sidecar` (imported as `tidal2ytm_sidecar`), the Tauri host package is `tidal2ytm` in `src-tauri/`, the npm package is `tidal2ytm`, and the PyInstaller output is `tidal2ytm-sidecar.exe`. The Tauri host package is named `tidal2ytm` rather than `tidal2ytm-host` on purpose: a virtual cargo workspace manifest leaves the package name free to be the app name, and the package name is what cargo turns into the binary name. No `mainBinaryName` override is used, and none should be added — Tauri's own documentation says to change the package name instead. The one exception is the host's *library target* inside `src-tauri/Cargo.toml`, which is `tidal2ytm_host` because a library target cannot share its package's name; this mirrors the conventional Tauri layout and is not a separate project.

- **Protocol version is `1`.** The sidecar emits `{"event":"hello","protocol":1,"capabilities":[…]}` before it will answer any request. The host **refuses to start** on a version mismatch or a missing required capability, reporting a startup error rather than degrading into a mysterious job failure later.
- **Required capability: `auth.status`.** This plan implements `auth.status` and nothing else. The other six ops in the spec's surface (`auth.begin`, `favorites.list`, `search.albums`, `search.songs`, `album.fetch`, `library.add`) arrive in Phases 3–5.
- **stdout is the IPC channel. Diagnostics go to stderr.** The sidecar must never print anything but NDJSON envelopes to stdout.
- **Every derived value is computed in Rust.** The sidecar returns raw upstream fields. It never returns a score, a rank, a certainty, a plan row, a status, or a count.
- **Credentials never leave the sidecar.** `auth.status` returns booleans and expiry timestamps, never token material.
- **The host spawns the sidecar with `tokio::process`.** Do not add `@tauri-apps/plugin-shell`. There is no `shell:allow-execute` or `shell:allow-spawn` capability, and the frontend cannot spawn a process.
- **Shutdown is closing stdin.** The sidecar sees EOF and exits. There is no shutdown op.
- **The domain crates must not depend on `tauri`.** `cargo test`, `cargo clippy`, and `cargo fmt` at the repo root must succeed on a Linux runner with no `libwebkit2gtk` installed.
- **The sidecar's log level arrives as the `TIDAL2YTM_LOG_LEVEL` environment variable, set at spawn**, so it still takes effect if the sidecar dies before the handshake. Phase 2 uses the env var only; the GUI selector that persists it in `meta` is Phase 4. An unrecognised value is a hard error, never a silent fallback to `INFO`.
- **Redaction is a recursive, case-insensitive key-pattern denylist**, matched as a substring of the key name, applied on both the Rust and Python sides: `token`, `secret`, `cookie`, `authorization`, `bearer`, `refresh`, `access`, `api_key`, `client_secret`, `session`. `auth.*` payloads are logged shape-only (key names, never values). Every other value is logged for real, so a matching bug is debuggable. **Every error message passes through the same filter**, because ytmusicapi and tidalapi put auth-bearing URLs inside exception text.
- **Tool policy: fix what a tool can fix, fail on what it cannot.** Formatters (`prettier --write`, `ruff format`, `cargo fmt`) rewrite and continue. Linters (`eslint`, `ruff check`, `clippy`) run their `--fix` first, then fail on the remainder. Type checkers (`pyright`, `svelte-check`) never auto-fix.
- **The Python gates stay green through Phases 1 and 2.** ruff check, ruff format, pyright, pytest at 80% coverage. They are dropped in Phase 6, in the same commit as the code they cover.
- **Coverage stays at 80% and grows to include the sidecar** the moment the sidecar package exists.
- **`pyproject.toml` version becomes `0.7.0` in Task 0 and stays there through this plan.** Removing ISRC is a behaviour change, so it is a minor bump. The spec's `1.0.0` lands in Phase 6, because deleting the CLI is the breaking change. `package.json` and `src-tauri/tauri.conf.json` carry the same `0.7.0`, because a packaged app whose three manifests disagree about its version is a support problem waiting to happen.
- **Config files use the best format available, preferring TOML, then YAML, then JSONC, then JSON. Config *modules* are TypeScript, then ESM, then CommonJS.** So: `svelte.config.ts`, `eslint.config.ts`, `vite.config.ts`, `vitest.config.ts`, and `.prettierrc.toml`. The formats that stay JSON are the ones with no alternative: `package.json` (npm and pnpm require it), `tsconfig.json` (no TypeScript alternative exists; it is written as JSONC with comments where a comment earns its place), and `tauri.conf.json` (Tauri requires it). Anything Tauri or shadcn-svelte generates as JSON stays JSON because fighting the tool's own expectations costs more than the format preference is worth.
- **Node is pinned to 24, and that pin is load-bearing.** `svelte.config.ts` is loaded by `vite-plugin-svelte` through a bare dynamic `import()` and therefore relies on Node's native TypeScript type stripping, which is why it works at all; `eslint.config.ts` is loaded through `jiti`. Both work on Node 24. `.npmrc` sets `engine-strict=true` and `package.json` declares `engines.node`, so a contributor on an older Node gets a clear install failure instead of a mystifying config error later. CI pins `node-version: 24`.
- **pnpm is the package manager** for the frontend, per the repo's global instructions.
- **Dependency versions are pinned exactly, with no range characters.** `package.json` uses `"6.0.3"`, never `"^6.0.3"`. Three reasons, and all are load-bearing. **TypeScript is capped at `6.0.3` because 7.x does not exist in a form the toolchain can use**: `typescript@7.0.2` is Microsoft's native Go port and ships no JavaScript whatsoever — only 19 platform binaries (`@typescript/typescript-win32-x64` and siblings) plus a `bin/tsc` shim. Both type-checking tools reach TypeScript through its JS compiler API; `@typescript-eslint/typescript-estree` literally does `require("typescript")` and walks the type system, and that API is absent from the native build. Hence `svelte-check@4.7.6` peers `typescript: ^5.0.0 || ^6.0.0` and `typescript-eslint@8.71.0` peers `>=4.8.4 <6.1.0`. `6.0.3` is the last pure-JS release — no dependencies, no optional dependencies — and it is the highest version inside both ranges, so it is the ceiling, not a cautious guess. A caret would let a routine `pnpm update` install npm's current default (7.0.2) and break both tools. Second: Rust `Cargo.toml` uses `"2.12.1"`, not `"^2.12.1"`, because a caret on `tauri` would let a minor bump drift away from the `@tauri-apps/cli` the frontend is locked to, and the two are released in lockstep. Third: `Cargo.lock` and `pnpm-lock.yaml` are committed and CI installs with `--frozen-lockfile`, so a pin that drifts is a diff CI sees rather than a resolution it silently takes.
- **The TypeScript ceiling moves only when the peer ranges move.** Do not read this pin as permanent and do not work around it. The pin can go to `7.x` the moment `svelte-check` and `typescript-eslint` both widen their peer ranges to include it, and that is a dependency bump like any other: bump it, then re-read both peer ranges before touching the pin. Do not reach for `pnpm install --force`, `strict-peer-dependencies: false`, or an `overrides` block to paper over the mismatch — a suppressed peer warning here means `typescript-estree` silently analysing against a compiler it cannot load.
- **Updating dependencies is a deliberate, manual act, with one exception.** Dependabot is configured for *security updates only* across the `npm`, `cargo`, and `uv` ecosystems, because a vulnerable dependency should not wait for a human to notice. Everything else is bumped by hand, one ecosystem at a time: `uv lock --upgrade`, `pnpm update --latest`, `cargo update`. The procedure is documented in `AGENTS.md` by Task 10. After any bump, re-read the peer-dependency constraints above, because they are exactly what a version bump breaks.
- **No SvelteKit.** The app has no server, so file-based routing, SSR, and adapters are all unused. A Vite SPA with a hand-rolled client-side router.
- **The `tauri` crate and `@tauri-apps/cli` must be the same version** (`2.12.1`). They are released in lockstep.
- **No `assert` for validation, no broad `except`/`unwrap` in production paths, no secrets in any log.** The existing project invariants in `AGENTS.md` still govern every Python file this plan touches.

## Review focus

The spec is a vision document: it says what the software must do, not everything it will meet. These five input classes and failure modes are implied by the spec but not directly stated in it, so no task below would test them on its own. Each has a test pinned to the task that owns the code, written in that task's own step style. Most likely to bite first is first.

1. **Two requests in flight, responses arriving out of order.** The sidecar answers request `2` before request `1`. A reader that pairs by arrival order rather than by `id` resolves call `1` with call `2`'s data, and since `auth.status` is the first thing the app calls, that surfaces as one provider's state under the other's label. Each call must resolve with its own response. Pinned in Task 6.
2. **The sidecar dies while a request is in flight** — user kills the process, Python segfaults, the machine swaps. The in-flight call must fail with `sidecar_lost`: not hang until the UI's spinner runs forever, and not return a fabricated success. Pinned in Task 6.
3. **The sidecar writes a non-JSON line to stdout.** A stray `print`, a library banner, a `SyntaxWarning` on some Python builds. The reader must step over the line and still resolve the real response, because the alternative is that one noisy line kills every job for the rest of the session. Pinned in Task 4.
4. **The sidecar is sent an op it does not implement.** The host names an op the sidecar has not grown yet, or a stale packaged sidecar is bundled. Expect `bad_request` naming the op — not a crash, not a hang, not a silent success with empty data. Pinned in Task 5.
5. **`data/tidal_token.json` exists but is corrupt** — truncated by a crash mid-write, or hand-edited into invalid JSON. Expect `auth.status` to report `valid: false`. A corrupt cache must degrade to "not signed in", never to an app that cannot start.

## Task index

Where each piece of work lives, and which task owns it. The task's own `**Files:**` block is authoritative; this table is for finding the right task from a file name.

| Task | Delivers | Owns |
| --- | --- | --- |
| 0 | Branch `feat/desktop-gui-migration`, version `0.7.0` | `pyproject.toml`, `uv.lock` |
| 1 | ISRC gone from the Python code and its tests | `tidal2ytm/{models,matcher,album_matching,tidal_source,planning_merge,plan_io,confidence,review}.py` · `tests/test_{matcher,review,tidal_source,planning,planning_merge,planning_search,logging_emit_sites}.py` · `tests/fixtures/sample_plan.toml` |
| 2 | ISRC gone from the prose | `README.md`, `AGENTS.md` |
| 3 | A cargo workspace and a Tauri-free domain crate | `Cargo.toml`, `Cargo.lock` · `crates/tidal2ytm-core/` |
| 4 | The wire format, as a codec and as fixtures | `crates/tidal2ytm-ipc/src/protocol.rs` · `contracts/*.json` |
| 5 | The Python sidecar | `sidecar/` · root `conftest.py` · `tidal2ytm/logging_setup.py` |
| 6 | The host's client for that sidecar | `crates/tidal2ytm-ipc/src/client.rs` |
| 7 | Proof the boundary works end to end | `crates/tidal2ytm-ipc/tests/round_trip.rs` |
| 8 | A buildable, lintable, testable frontend | `package.json`, `pnpm-lock.yaml`, the four config modules, `index.html` · `src/` |
| 9 | A window that spawns the sidecar and shows the result | `src-tauri/` · `src/lib/ipc.ts` · `src/routes/Home.svelte` |
| 10 | Gates for all three ecosystems, and the update procedure | `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.pre-commit-config.yaml`, `AGENTS.md` |
| 11 | A packaged sidecar and a packaged app | `sidecar/tidal2ytm-sidecar.spec`, `sidecar/build_sidecar.ps1` · `.gitignore`, `README.md` |

Phase 1 is Tasks 0–2. Phase 2 is Tasks 3–11, and Task 11 is the phase's exit evidence: it is the only task where a human runs the built `.exe`.

---

# Phase 1 — ISRC removal

Phase 1 is a single mechanical deletion threaded through every layer: `SourceTrack.isrc` is read by `matcher`, `MatchResult.yt_isrc` is written by `matcher` and `album_matching`, and both are copied into plan rows by `planning_merge` and rendered by `review`. It therefore cannot be split across commits without leaving the suite red. The test deletions here are the only legitimate test deletions in this project, and every one of them removes retired behaviour rather than coverage.

## Task 0: Open the feature branch and set the version

`AGENTS.md` requires the feature branch to set the upcoming release version once its scope is known, and this work is far too large to land on `main` incrementally. Both happen here, before any code moves, so every later commit in the plan carries the right version and the right branch.

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (rewritten by `uv sync`, staged in the same commit)

**Interfaces:**
- Consumes: nothing.
- Produces: branch `feat/desktop-gui-migration`, with `pyproject.toml` at `version = "0.7.0"`. Tasks 8 and 9 read that version and copy it into `package.json` and `src-tauri/tauri.conf.json`.

- [ ] **Step 1: Create the branch**

Run:

```bash
git switch -c feat/desktop-gui-migration
```

Expected: `Switched to a new branch 'feat/desktop-gui-migration'`. Confirm you are not carrying anything unexpected forward:

```bash
git status --short
git log --oneline -3
```

Expected: the only entry from `git status --short` is the untracked `docs/superpowers/plans/2026-09-30-desktop-gui-migration-phases-1-2.md` and the untracked, superseded `docs/tidal2ytm-desktop-ui-migration-preliminary-plan.md`. `git log` shows `8045e8d docs: add desktop GUI migration design spec` at the top. If the working tree is dirty with anything else, stop and ask before continuing — a stray edit would end up in the branch's first commit.

- [ ] **Step 2: Bump the version**

In `pyproject.toml`, change `version = "0.6.0"` to `version = "0.7.0"`.

Removing ISRC changes observable behaviour: `match_method` loses the `isrc` value, plan rows lose `tidal_isrc` and `yt_isrc`, and the certainty test that keyed on ISRC no longer exists. That is a minor bump. The spec reserves `1.0.0` for Phase 6, where the CLI is deleted.

- [ ] **Step 3: Run the gates so the lockfile records the new version**

Run:

```bash
uv sync --all-groups
uv run pytest -q
```

Expected: `356 passed`. `uv sync` rewrites the root-package version line in `uv.lock`. That rewrite is expected, and staging `uv.lock` in the next step is not optional — the pre-push pytest hook aborts with "files were modified" if the lockfile is left dirty, and an unstaged `uv.lock` is the documented cause.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: start the desktop GUI migration at 0.7.0"
```

## Task 1: Remove ISRC from the Python code and its tests

**Files:**
- Modify: `tidal2ytm/models.py`, `tidal2ytm/matcher.py`, `tidal2ytm/album_matching.py`, `tidal2ytm/tidal_source.py`, `tidal2ytm/planning_merge.py`, `tidal2ytm/plan_io.py`, `tidal2ytm/confidence.py`, `tidal2ytm/review.py`
- Modify: `tests/test_matcher.py`, `tests/test_review.py`, `tests/test_tidal_source.py`, `tests/test_planning.py`, `tests/test_planning_merge.py`, `tests/test_planning_search.py`, `tests/test_logging_emit_sites.py`
- Modify: `tests/fixtures/sample_plan.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: `MatchMethod` loses its `ISRC` member and keeps `DURATION | FUZZY | ALBUM | NONE`. `SourceTrack` loses `isrc`. `MatchResult` loses `yt_isrc`. `tidal2ytm.confidence.is_certain` and `CERTAIN_CONFIDENCE` are deleted, leaving `color_for` as the module's only public function. `planning_merge.match_result_to_track_dict` and `planning_merge.unmatched_track_dict` stop emitting `tidal_isrc` and `yt_isrc` keys. `review._confidence_text(value: float | None) -> Text` loses its second parameter.

- [ ] **Step 1: Delete the two tests that exist only to test ISRC**

In `tests/test_matcher.py`, delete `test_matcher_isrc_via_candidate` (lines 52–70) in its entirety.

In `tests/test_review.py`, delete `test_detail_body_shows_exact_match_label_for_isrc` (lines 877–882) in its entirety.

- [ ] **Step 2: Rewrite the one test whose real subject survives**

`tests/test_matcher.py:257-287` is currently `test_isrc_miss_falls_back_to_duration_over_fuzzy_title`. Its actual contract — the part with nothing to do with ISRC — is that the duration gate outranks title similarity. Delete it and put this in its place:

```python
def test_duration_gate_outranks_title_similarity() -> None:
    """A wrong-duration exact title loses to a right-duration weaker title.

    The duration gate is what decides here: the exact-title candidate is 91 s
    outside the tolerance, so it never reaches the title comparison at all.
    """
    track = _source(
        {
            "title": "Ember Fall",
            "artist": "Hollow Atlas",
            "album": "Quartz Sea",
            "duration_sec": 209.0,
        }
    )
    candidates = [
        {
            "videoId": "AAAAAAAAAAA",
            "title": "Ember Fall (Live)",
            "artists": ["Hollow Atlas"],
            "album": {"name": "Quartz Sea", "id": "ALBUM1"},
            "duration": 209,
        },
        {
            "videoId": "BBBBBBBBBBB",
            "title": "Ember Fall",
            "artists": ["Hollow Atlas"],
            "album": {"name": "Quartz Sea", "id": "ALBUM1"},
            "duration": 300,
        },
    ]
    res = matcher.match_track(track, _yt_with_candidates(candidates))
    assert res.yt_video_id == "AAAAAAAAAAA"
    assert res.match_method == MatchMethod.DURATION
```

- [ ] **Step 3: Strip `isrc` from the domain models**

In `tidal2ytm/models.py`, delete the `ISRC = "isrc"` member from `MatchMethod` so the enum reads:

```python
class MatchMethod(StrEnum):
    DURATION = "duration"
    FUZZY = "fuzzy"
    ALBUM = "album"
    NONE = "none"
```

Delete the `isrc: str | None` field from `SourceTrack` (it sits between `duration_sec` and `track_num`) and delete `isrc=data.get("isrc"),` from its `from_dict`. Delete `yt_isrc: str | None` from `MatchResult`.

- [ ] **Step 4: Strip ISRC from the matching layer**

In `tidal2ytm/matcher.py`:

- Delete the `_isrc_from_candidate` helper in its entirety.
- Delete `c_isrc: str | None = _isrc_from_candidate(candidate)`.
- Delete the whole Strategy-1 early-return block: the `# Strategy 1: …` comment together with the `if track.isrc and c_isrc and …` arm and its `return MatchResult(match_method=MatchMethod.ISRC, …)`.
- Rewrite the surviving comment at what was line 100 to `# Duration + fuzzy via the shared track scorer.`
- Delete `"isrc": c_isrc,` from the `best_meta` dict and delete `yt_isrc=best_meta.get("isrc"),` from the final `MatchResult`.

In `tidal2ytm/album_matching.py`, delete `yt_isrc=None,` from both `MatchResult` constructions: the one in `_review_results` (line 97) and the winner in `resolve_album_group` (line 287).

In `tidal2ytm/tidal_source.py`, delete `isrc=t.isrc,` from the `SourceTrack(...)` call in `_to_source_track`.

- [ ] **Step 5: Run the matcher tests and confirm the failure is only about ISRC**

Run: `uv run pytest tests/test_matcher.py tests/test_tidal_source.py -q`

Expected: FAIL, with `AttributeError` or `TypeError` naming `isrc` or `yt_isrc` — the fixtures and `SourceTrack` still expect the fields you just deleted.

- [ ] **Step 6: Strip ISRC from the test fixtures**

In `tests/test_matcher.py`, delete every `"isrc": …` and `"isrc": None,` key from the `_source({...})` fixtures (previously at lines 36, 58, 66, 79, 117, 130, 160, 181, 201, 222, 243, 267, 282, 296, 320) and from the candidate dicts.

In `tests/test_tidal_source.py`, delete the `isrc: str | None = None` field from the `FakeTrack` dataclass and the `isrc="USABC1234567"` kwarg from the `FakeTrack(...)` construction.

In `tests/test_review.py`, delete the `"tidal_isrc": ""` and `"yt_isrc": ""` keys from the `_review_track` helper.

In `tests/test_planning.py`, `tests/test_planning_merge.py`, `tests/test_planning_search.py`, and `tests/test_logging_emit_sites.py`, delete the mechanical `isrc=None` and `yt_isrc=None` kwargs. These are key removals, not test deletions: every test in those files survives and still asserts its own behaviour.

- [ ] **Step 7: Run those tests again**

Run: `uv run pytest tests/test_matcher.py tests/test_tidal_source.py tests/test_review.py -q`

Expected: PASS.

- [ ] **Step 8: Strip ISRC from the plan representation**

In `tidal2ytm/planning_merge.py`, delete `"tidal_isrc": src.isrc or "",` and `"yt_isrc": result.yt_isrc or "",` from `match_result_to_track_dict`, and `"tidal_isrc": src.isrc or "",` and `"yt_isrc": "",` from `unmatched_track_dict`.

In `tidal2ytm/plan_io.py`, change line 37 of `_PLAN_HEADER` from `# match_method: isrc | duration | fuzzy | album | none` to `# match_method: duration | fuzzy | album | none`.

In `tests/fixtures/sample_plan.toml`, delete the `isrc = "USABC1234567"` line and change `match_method = "isrc"` to `match_method = "fuzzy"`.

- [ ] **Step 9: Strip ISRC from the review display**

`review.py`'s `_confidence_text` has exactly two call sites: line 273 in `_track_head_line` and line 536 in `detail_body`.

Reduce the helper to this shape by deleting the `match_method` parameter and the `if match_method == "isrc": return Text("exact match", style="blue")` arm:

```python
def _confidence_text(value: float | None) -> Text:
    if value is None:
        return Text("—")
    return Text(f"{value:.2f}", style=color_for(value))
```

In `_track_head_line`, delete `match_method = track.get("match_method", "none")` and the `if match_method != MatchMethod.ISRC.value:` guard, leaving the `"conf. "` prefix unconditional:

```python
    line.append("conf. ")
    line.append_text(_confidence_text(conf.get("overall")))
```

In `detail_body`, delete the `("ISRC", track.get("tidal_isrc") or "—"),` row (line 502) from the Tidal rows and the `("ISRC", track.get("yt_isrc") or "—", False),` row (line 517) from `yt_rows`. Keep the `match_method` local and the `f"  Match method:  {match_method}\n"` line at 534 — only the argument to `_confidence_text` goes away:

```python
    line.append_text(_confidence_text(conf.get("overall")))
```

In `KeyRouter.dispatch_decision`, delete the `was_isrc = (key == "r" and track.get("match_method") == "isrc" and …)` computation and the `if was_isrc:` arm that appended the "this was an ISRC match" flash.

`review.py` keeps its `MatchMethod` import: line 445 still uses `MatchMethod.ALBUM.value`.

- [ ] **Step 10: Delete the now-dead certainty helper**

`confidence.is_certain` had zero callers before this change and its body tested `method == MatchMethod.ISRC`; `CERTAIN_CONFIDENCE` was already uncalled. Delete both, and drop the "blue is reserved for the ISRC label" sentence from `color_for`'s comment, leaving `confidence.py` as:

```python
from __future__ import annotations

from rich.text import Text


def color_for(confidence: float) -> str:
    """Map a confidence score onto the review palette."""
    if confidence >= 0.9:
        return "green"
    if confidence >= 0.7:
        return "yellow"
    return "red"
```

- [ ] **Step 11: Run the full Python suite**

Run: `uv run pytest -q`

Expected: PASS, with 354 tests (356 minus the two deleted ISRC-only tests). If the count is different, you deleted a test that was not ISRC-only — restore it.

- [ ] **Step 12: Run the Python gates and drop the now-stale noqa**

Run: `uv run ruff check .`

Expected: possibly `RUF100 Unused blanket-type-noqa` on `tidal2ytm/matcher.py`. Removing one `if` arm drops `match_track` to complexity 10, which is ruff's default `max-complexity` and therefore no longer reported, so the `noqa: C901` on its `def` line has nothing left to suppress. Delete the `# noqa: C901` and re-run.

If `ruff check` still reports `C901` for `match_track` after the noqa is gone, put the noqa back. `AGENTS.md` permits exactly one pre-existing `C901` noqa in this project and bans new ones; this is not a new one.

Then run, in order:

```
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
```

Expected: all four clean.

- [ ] **Step 13: Commit**

```bash
git add tidal2ytm tests
git commit -m "refactor: remove ISRC matching and the data it carried"
```

## Task 2: Remove ISRC from the documentation

**Files:**
- Modify: `README.md`, `AGENTS.md`

**Interfaces:**
- Consumes: the field list from Task 1.
- Produces: no code. The docs stop advertising a match mode the code no longer has.

- [ ] **Step 1: Fix the README matching table**

In `README.md` line 17, delete the row:

```
| 1        | **ISRC**         | Exact recording identifier. Unambiguous. Confidence: 1.0 … |
```

Renumber the remaining rows so the table reads 1, 2, 3 in descending-confidence order with no gap.

- [ ] **Step 2: Fix the README caveats**

In `README.md`, delete the `- **ISRC on YTM**: …` bullet (line 233).

Rewrite line 234 so the caveat survives without the ISRC claim. The reasoning — classical catalogues have thin track-level metadata and therefore need human review — is still true; the specific advice about ISRC is not:

```
- **Classical music**: Tidal's track-level metadata is thin, so album-level matching carries more of the weight. Expect a larger review queue.
```

- [ ] **Step 3: Fix the two stale lines in AGENTS.md**

In `AGENTS.md` line 3, change:

```
Python CLI (`tidal2ytm`) that transfers Tidal liked tracks to YouTube Music — ISRC > duration (±4 s) > fuzzy, managed with `uv` from the repo root.
```

to:

```
Python CLI (`tidal2ytm`) that transfers Tidal liked tracks to YouTube Music — album-anchored matching with duration and fuzzy fallbacks, managed with `uv` from the repo root.
```

In `AGENTS.md` line 38, change the layout-tree entry:

```
│   ├── matcher.py        # ISRC / duration / fuzzy ranking
```

to:

```
│   ├── matcher.py        # duration / fuzzy ranking for a single track
```

- [ ] **Step 4: Confirm no ISRC reference survives outside the historical records**

Run:

```bash
rg -ni isrc --glob '!docs/superpowers/**' --glob '!docs/tidal2ytm-desktop-ui-migration-preliminary-plan.md'
```

Expected: no matches. `docs/superpowers/plans/` and `docs/superpowers/specs/` are records of past work and are deliberately not rewritten; the preliminary plan is superseded and stays untracked.

- [ ] **Step 5: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: drop ISRC from the matching documentation"
```

---

# Phase 2 — The packaged IPC vertical slice

## Task 3: Cargo workspace and the Tauri-free domain core

The workspace exists to make one structural promise: the domain core and the protocol codec are reachable by `cargo test` on a Linux runner with no `libwebkit2gtk`. Phase 2 proves that promise with one real module rather than an empty crate, because an empty crate proves nothing about whether the boundary is real.

`video_id` is the right module for that proof. It is pure, it is small, and it has a hard, already-specified contract — `tidal2ytm.plan_io.extract_video_id` accepts a bare 11-character ID plus nine URL forms — so the parity test pins behaviour the spec explicitly requires the port to preserve. The domain *types* are deliberately deferred to Phase 3: they have no consumer until something crosses the wire carrying them, and shipping structs nothing reads is exactly the speculation the spec forbids.

**Files:**
- Create: `Cargo.toml`, `Cargo.lock` (generated)
- Create: `crates/tidal2ytm-core/Cargo.toml`, `crates/tidal2ytm-core/src/lib.rs`, `crates/tidal2ytm-core/src/video_id.rs`

**Interfaces:**
- Consumes: nothing.
- Produces: a cargo workspace named `tidal2ytm` whose members are `crates/tidal2ytm-core`, `crates/tidal2ytm-ipc`, and `src-tauri`, with `default-members` limited to the first two. Crate `tidal2ytm-core` exports `tidal2ytm_core::video_id::{extract_video_id, VideoIdError}`. `extract_video_id(raw: &str) -> Result<String, VideoIdError>` returns the bare 11-character ID. `VideoIdError` displays `Cannot parse YouTube video ID from: {input}`.

- [ ] **Step 1: Create the workspace manifest**

Create `Cargo.toml` at the repo root:

```toml
[workspace]
resolver = "3"
members = ["crates/tidal2ytm-core", "crates/tidal2ytm-ipc", "src-tauri"]
default-members = ["crates/tidal2ytm-core", "crates/tidal2ytm-ipc"]

[workspace.package]
version = "0.1.0"
edition = "2024"
rust-version = "1.85"
license = "MIT"
publish = false

[workspace.dependencies]
serde = { version = "1.0.229", features = ["derive"] }
serde_json = "1.0.151"
thiserror = "2.0.21"
```

`default-members` is the load-bearing line. It means a bare `cargo test` at the repo root builds and tests only the two Tauri-free crates, so the Linux runner never needs webkit. `src-tauri` is a member — so `cargo fmt --all` still formats it — but not a default member, so it is never compiled by an unqualified command.

`members` listing `src-tauri` and `crates/tidal2ytm-ipc` before they exist makes every cargo command fail until Tasks 4 and 9 create them. Create those two directories with a placeholder `Cargo.toml` and a `src/lib.rs` containing only a module doc comment, so the workspace resolves from this step on:

`crates/tidal2ytm-ipc/Cargo.toml` — Task 4 replaces this wholesale:

```toml
[package]
name = "tidal2ytm-ipc"
version.workspace = true
edition.workspace = true
publish.workspace = true
```

`crates/tidal2ytm-ipc/src/lib.rs`:

```rust
//! Placeholder. Task 4 writes the real protocol codec.
```

`src-tauri/Cargo.toml` — Task 9 replaces this wholesale:

```toml
[package]
name = "tidal2ytm"
version.workspace = true
edition.workspace = true
publish.workspace = true

[lib]
name = "tidal2ytm_host"
crate-type = ["rlib"]
```

The package is named `tidal2ytm` so that the binary cargo builds is `tidal2ytm.exe` with no `mainBinaryName` override — a virtual workspace manifest leaves the package name free, and Tauri's own documentation says to change the package name rather than the binary name. The library target is `tidal2ytm_host` because a library target cannot share its package's name; this is the conventional Tauri layout, not a second project.

`src-tauri/src/lib.rs`:

```rust
//! Placeholder. Task 9 writes the real Tauri host.
```

- [ ] **Step 2: Create the core crate manifest**

Create `crates/tidal2ytm-core/Cargo.toml`:

```toml
[package]
name = "tidal2ytm-core"
version.workspace = true
edition.workspace = true
rust-version.workspace = true
license.workspace = true
publish.workspace = true
description = "Domain logic for tidal2ytm. No Tauri, no I/O."

[dependencies]
thiserror.workspace = true
```

There is no `tauri` here and there must never be one. There is no `serde` either: nothing in this crate serialises yet, and a dependency nothing uses is a dependency that rots. Phase 3 adds it when the domain types first cross the IPC boundary.

- [ ] **Step 3: Write the failing parity test**

Create `crates/tidal2ytm-core/src/lib.rs`:

```rust
//! Domain logic for tidal2ytm.
//!
//! This crate is deliberately free of `tauri` and of all I/O so that
//! `cargo test` runs on a bare Linux runner. Anything that needs a process, a
//! network, or a window belongs in `tidal2ytm-ipc` or the `tidal2ytm` host.

pub mod video_id;
```

Create `crates/tidal2ytm-core/src/video_id.rs` containing the module doc comment, the `VideoIdError` type, and a `todo!()` body, so the tests have something to compile against:

```rust
//! Extract a YouTube video ID from a bare ID or any of the nine URL shapes a
//! user might paste.

/// Raised when the input matches none of the accepted forms.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("Cannot parse YouTube video ID from: {input}")]
pub struct VideoIdError {
    input: String,
}

pub fn extract_video_id(_raw: &str) -> Result<String, VideoIdError> {
    todo!("port plan_io.extract_video_id")
}
```

Append the test module. The table is a direct port of `tests/test_plan_io.py:175-186`, which is the behaviour the spec requires the Rust port to preserve:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_every_documented_form() {
        let cases = [
            ("CCCCCCCCCCC", "CCCCCCCCCCC"),
            ("https://www.youtube.com/watch?v=CCCCCCCCCCC", "CCCCCCCCCCC"),
            ("https://youtu.be/CCCCCCCCCCC?t=10", "CCCCCCCCCCC"),
            ("https://www.youtube.com/v/CCCCCCCCCCC?foo=1", "CCCCCCCCCCC"),
            ("https://music.youtube.com/watch?v=CCCCCCCCCCC&list=PL", "CCCCCCCCCCC"),
            ("https://m.youtube.com/watch?v=AAAAAAAAAAA", "AAAAAAAAAAA"),
            ("https://www.youtube.com/embed/AAAAAAAAAAA", "AAAAAAAAAAA"),
            ("https://www.youtube.com/shorts/AAAAAAAAAAA", "AAAAAAAAAAA"),
            ("https://www.youtube.com/live/AAAAAAAAAAA", "AAAAAAAAAAA"),
            ("https://www.youtube-nocookie.com/embed/AAAAAAAAAAA", "AAAAAAAAAAA"),
        ];
        for (input, expected) in cases {
            assert_eq!(
                extract_video_id(input).as_deref(),
                Ok(expected),
                "input: {input}"
            );
        }
    }

    #[test]
    fn strips_surrounding_whitespace() {
        assert_eq!(
            extract_video_id("  CCCCCCCCCCC \n").as_deref(),
            Ok("CCCCCCCCCCC")
        );
    }

    #[test]
    fn rejects_a_non_youtube_url() {
        assert!(extract_video_id("https://example.com/watch?v=CCCCCCCCCCC").is_err());
    }

    #[test]
    fn rejects_a_wrong_length_id() {
        assert!(extract_video_id("CCCCCCCCCC").is_err());
    }

    #[test]
    fn rejects_an_id_with_illegal_characters() {
        assert!(extract_video_id("CCCC!CCCCCC").is_err());
    }

    #[test]
    fn error_names_the_offending_input() {
        let err = extract_video_id("nonsense").unwrap_err();
        assert_eq!(
            err.to_string(),
            "Cannot parse YouTube video ID from: nonsense"
        );
    }
}
```

- [ ] **Step 4: Run the test and confirm it fails**

Run: `cargo test -p tidal2ytm-core`

Expected: FAIL — the `todo!()` panics on every case.

- [ ] **Step 5: Implement the parser**

Replace the `todo!()` body in `crates/tidal2ytm-core/src/video_id.rs` with this. It is a port of `tidal2ytm/plan_io.py:49-92`, using a hand-rolled split instead of `urlparse`/`parse_qs` because the accepted grammar is narrow enough that a general URL parser would be more code than the parser itself:

```rust
fn is_video_id(candidate: &str) -> bool {
    candidate.len() == 11
        && candidate
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// Split `url` into a lowercased host and the part after the host, with
/// `www.` and `m.` prefixes stripped.
fn split_url(url: &str) -> (String, &str) {
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    let (authority, path) = rest.split_once('/').unwrap_or((rest, ""));
    let lowered = authority.to_ascii_lowercase();
    let host = lowered
        .strip_prefix("www.")
        .or_else(|| lowered.strip_prefix("m."))
        .unwrap_or(&lowered)
        .to_string();
    (host, path)
}

/// Pull the `v` query parameter out of a path-and-query string.
fn query_v(path_and_query: &str) -> &str {
    path_and_query
        .split_once('?')
        .map_or("", |(_, query)| {
            query
                .split('&')
                .find_map(|pair| pair.strip_prefix("v="))
                .unwrap_or("")
        })
}

/// Extract and validate an 11-character YouTube video ID.
///
/// Accepts a bare ID, or `/watch?v=ID`, `/v/ID`, `/embed/ID`, `/shorts/ID`,
/// and `/live/ID` on `youtube.com`, `music.youtube.com` and
/// `youtube-nocookie.com`, or a path ID on `youtu.be`. Host matching is
/// `www.`/`m.`-insensitive.
pub fn extract_video_id(raw: &str) -> Result<String, VideoIdError> {
    let trimmed = raw.trim();
    if is_video_id(trimmed) {
        return Ok(trimmed.to_string());
    }

    let (host, path_and_query) = split_url(trimmed);
    let path = path_and_query
        .split(['?', '#'])
        .next()
        .unwrap_or_default();
    let candidate = match host.as_str() {
        "youtube.com" | "music.youtube.com" | "youtube-nocookie.com" => ["/embed/", "/shorts/", "/live/", "/v/"]
            .iter()
            .find_map(|prefix| path.strip_prefix(prefix))
            .map_or_else(|| query_v(path_and_query), |tail| tail.split('/').next().unwrap_or_default()),
        "youtu.be" => path.trim_start_matches('/').split('/').next().unwrap_or_default(),
        _ => {
            return Err(VideoIdError {
                input: raw.to_string(),
            });
        }
    };

    if is_video_id(candidate) {
        Ok(candidate.to_string())
    } else {
        Err(VideoIdError {
            input: raw.to_string(),
        })
    }
}
```

- [ ] **Step 6: Run the test and confirm it passes**

Run: `cargo test -p tidal2ytm-core`

Expected: PASS, 6 tests.

- [ ] **Step 7: Run the Rust gates**

Run:

```
cargo fmt --all --check
cargo clippy -p tidal2ytm-core --all-targets -- -D warnings
```

Expected: both clean. If `cargo fmt --check` reports differences, run `cargo fmt --all` and re-check — the formatter rewrites and you continue, per the tool policy.

- [ ] **Step 8: Confirm the crate has no Tauri in its dependency graph**

Run:

```bash
cargo tree -p tidal2ytm-core --depth 1
```

Expected: only `thiserror` (plus its transitive deps). Anything named `tauri` or `serde` here means the structure is already wrong.

- [ ] **Step 9: Commit**

```bash
git add Cargo.toml Cargo.lock crates src-tauri
git commit -m "feat: add the Tauri-free domain core crate with video ID validation"
```

## Task 4: The NDJSON protocol codec and the shared contract fixtures

The wire format is the one thing in this project that two languages have to agree on without being able to type-check each other. `contracts/*.json` is how they check: the Python suite and the Rust suite both assert against the same canonical envelopes, so a rename on one side fails the other side's tests rather than production.

**Files:**
- Modify: `crates/tidal2ytm-ipc/Cargo.toml`, `crates/tidal2ytm-ipc/src/lib.rs`
- Create: `crates/tidal2ytm-ipc/src/protocol.rs`
- Create: `contracts/hello.json`, `contracts/auth_status.json`, `contracts/error_auth_expired.json`

**Interfaces:**
- Consumes: the workspace from Task 3.
- Produces: `tidal2ytm_ipc::protocol::{PROTOCOL_VERSION, REQUIRED_CAPABILITIES, ErrorKind, WireError, Request, Response, ServerEvent, validate_hello, parse_server_event}`. `PROTOCOL_VERSION: u32 = 1`. `REQUIRED_CAPABILITIES: &[&str] = &["auth.status"]`. `ErrorKind` is `AuthExpired | RateLimited | NotFound | Upstream | BadRequest | SidecarLost`, each with `as_wire() -> &'static str` returning its snake_case name. `Request<'a> { id: u64, op: &'a str, params: Value }` serialises to one NDJSON line. `Response { id: u64, ok: bool, data: Option<Value>, error: Option<WireError> }`. `ServerEvent` is an enum with one variant `Hello { protocol: u32, capabilities: Vec<String> }`, tagged on `"event"` renamed to `"hello"`. `validate_hello(&ServerEvent) -> Result<(), IpcError>` returns `Err(IpcError::ProtocolMismatch { expected, got })` or `Err(IpcError::MissingCapability { name })`. `parse_server_event(line: &str) -> Option<ServerEvent>` returns `None` for anything that is not a server event, so a non-JSON line or a response line is stepped over rather than fatal.
- Produces: `tidal2ytm_ipc::IpcError`, the crate-wide error enum. Variants: `ProtocolMismatch { expected: u32, got: u32 }`, `MissingCapability { name: String }`, `SidecarLost`, `Timeout { op: String, ms: u64 }`, `Spawn { message: String }`, `Sidecar { op: String, kind: ErrorKind, message: String }`, `MalformedResponse { line: String }`.

- [ ] **Step 1: Create the contract fixtures**

Create `contracts/hello.json`:

```json
{"event": "hello", "protocol": 1, "capabilities": ["auth.status"]}
```

Create `contracts/auth_status.json`:

```json
{"id": 1, "ok": true, "data": {"tidal": {"valid": false, "expires_at": null}, "ytm": {"valid": false, "expires_at": null}}}
```

Create `contracts/error_auth_expired.json`:

```json
{"id": 1, "ok": false, "error": {"kind": "auth_expired", "message": "YouTube Music token expired; re-authenticate."}}
```

The `id` is fixed at `1` in all three so both suites can assert on them without a substitution step. The Rust round-trip in Task 7 sends its first request with `id = 1` for exactly this reason.

- [ ] **Step 2: Replace the ipc crate manifest**

Replace `crates/tidal2ytm-ipc/Cargo.toml` with:

```toml
[package]
name = "tidal2ytm-ipc"
version.workspace = true
edition.workspace = true
rust-version.workspace = true
license.workspace = true
publish.workspace = true
description = "NDJSON protocol and sidecar process client for tidal2ytm."

[dependencies]
serde.workspace = true
serde_json.workspace = true
thiserror.workspace = true
tokio = { version = "1.53.1", features = [
    "io-util",
    "macros",
    "process",
    "rt",
    "sync",
    "time",
] }
tidal2ytm-core = { path = "../tidal2ytm-core" }
```

- [ ] **Step 3: Write the failing codec tests**

Replace `crates/tidal2ytm-ipc/src/lib.rs` with:

```rust
//! The sidecar's NDJSON protocol and the client that speaks it.
//!
//! Like `tidal2ytm-core`, this crate must not depend on `tauri`: the
//! round-trip test in `tests/round_trip.rs` spawns a real Python process, and
//! that test is what proves the boundary works on a headless Linux runner.

pub mod protocol;

pub use protocol::{ErrorKind, IpcError};
```

Create `crates/tidal2ytm-ipc/src/protocol.rs` containing only the module doc comment and the test module below, so Step 5 has something to fail against:

```rust
//! Wire types for the sidecar's NDJSON protocol.
//!
//! `contracts/*.json` holds a canonical example of every envelope. The Python
//! and Rust suites both assert against those files, so a rename on one side
//! fails the other side's tests instead of production.

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> String {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../contracts")
            .join(name);
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
    }

    #[test]
    fn parses_the_hello_contract() {
        let event = parse_server_event(&fixture("hello.json")).expect("hello is a server event");
        validate_hello(&event).expect("the contract handshake is valid");
    }

    #[test]
    fn parses_the_success_contract() {
        let response: Response =
            serde_json::from_str(&fixture("auth_status.json")).expect("success envelope parses");
        assert!(response.ok);
        assert_eq!(response.id, 1);
        assert!(response.error.is_none());
        let data = response.data.expect("ok responses carry data");
        assert_eq!(data["tidal"]["valid"], serde_json::json!(false));
        assert!(data["tidal"]["expires_at"].is_null());
    }

    #[test]
    fn parses_the_error_contract() {
        let response: Response = serde_json::from_str(&fixture("error_auth_expired.json"))
            .expect("error envelope parses");
        assert!(!response.ok);
        let error = response.error.expect("failed responses carry an error");
        assert_eq!(error.kind, ErrorKind::AuthExpired);
        assert!(!error.message.is_empty());
    }

    #[test]
    fn serialises_a_request_as_one_line() {
        let line = serde_json::to_string(&Request {
            id: 1,
            op: "auth.status",
            params: serde_json::json!({}),
        })
        .expect("request serialises");
        assert!(!line.contains('\n'));
        assert_eq!(line, r#"{"id":1,"op":"auth.status","params":{}}"#);
    }

    // Review Focus #3: a stray print, a library banner, or a SyntaxWarning on
    // some Python builds must not kill the session. The reader has to be able
    // to step over a line it cannot use and keep going.
    #[test]
    fn a_non_json_line_is_not_a_server_event() {
        assert!(parse_server_event("not json at all").is_none());
    }

    #[test]
    fn a_response_line_is_not_a_server_event() {
        assert!(parse_server_event(&fixture("auth_status.json")).is_none());
    }

    #[test]
    fn a_protocol_mismatch_is_refused() {
        let event = ServerEvent::Hello {
            protocol: 2,
            capabilities: vec!["auth.status".to_string()],
        };
        assert!(matches!(
            validate_hello(&event),
            Err(IpcError::ProtocolMismatch {
                expected: 1,
                got: 2
            })
        ));
    }

    #[test]
    fn a_missing_required_capability_is_refused() {
        let event = ServerEvent::Hello {
            protocol: PROTOCOL_VERSION,
            capabilities: vec!["favorites.list".to_string()],
        };
        assert!(matches!(
            validate_hello(&event),
            Err(IpcError::MissingCapability { ref name }) if name == "auth.status"
        ));
    }

    #[test]
    fn an_unexpected_capability_does_not_block_startup() {
        let event = ServerEvent::Hello {
            protocol: PROTOCOL_VERSION,
            capabilities: vec!["auth.status".to_string(), "library.add".to_string()],
        };
        validate_hello(&event).expect("extra capabilities are fine");
    }

    #[test]
    fn error_kinds_round_trip_through_their_wire_names() {
        let all = [
            ErrorKind::AuthExpired,
            ErrorKind::RateLimited,
            ErrorKind::NotFound,
            ErrorKind::Upstream,
            ErrorKind::BadRequest,
            ErrorKind::SidecarLost,
        ];
        for kind in all {
            let encoded = serde_json::to_value(kind).expect("kind serialises");
            assert_eq!(encoded, serde_json::json!(kind.as_wire()));
            assert_eq!(
                serde_json::from_value::<ErrorKind>(encoded).expect("kind parses"),
                kind
            );
        }
    }
}
```

- [ ] **Step 4: Run the tests and confirm they fail**

Run: `cargo test -p tidal2ytm-ipc`

Expected: FAIL to compile — the types are undefined.

- [ ] **Step 5: Implement the codec**

Prepend this to `crates/tidal2ytm-ipc/src/protocol.rs`, above the `#[cfg(test)]` module:

```rust
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Bumped whenever a wire shape changes incompatibly. The host refuses to
/// start against a sidecar reporting anything else, so a stale packaged
/// sidecar produces a clear startup error instead of a failing job.
pub const PROTOCOL_VERSION: u32 = 1;

/// Capabilities the host cannot run without. A sidecar missing any of these
/// is refused at startup rather than failing later inside a job.
pub const REQUIRED_CAPABILITIES: &[&str] = &["auth.status"];

/// The closed set of failure categories the sidecar reports.
///
/// A `kind` this build does not recognise fails to deserialise and surfaces as
/// `IpcError::MalformedResponse`. That is deliberate: guessing that an unknown
/// kind is `Upstream` would make a newer sidecar's `quota_exceeded` look like
/// a generic failure and lose the distinction the host branches on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    AuthExpired,
    RateLimited,
    NotFound,
    Upstream,
    BadRequest,
    SidecarLost,
}

impl ErrorKind {
    /// The name that appears on the wire.
    #[must_use]
    pub fn as_wire(self) -> &'static str {
        match self {
            Self::AuthExpired => "auth_expired",
            Self::RateLimited => "rate_limited",
            Self::NotFound => "not_found",
            Self::Upstream => "upstream",
            Self::BadRequest => "bad_request",
            Self::SidecarLost => "sidecar_lost",
        }
    }
}

/// A failed call, as reported by the sidecar.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireError {
    pub kind: ErrorKind,
    pub message: String,
}

/// One request, written as a single NDJSON line.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Request<'a> {
    pub id: u64,
    pub op: &'a str,
    pub params: Value,
}

/// One response envelope.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Response {
    pub id: u64,
    pub ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<WireError>,
}

/// An unsolicited message from the sidecar. Only the handshake exists today.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "event", rename = "hello")]
pub enum ServerEvent {
    Hello {
        protocol: u32,
        capabilities: Vec<String>,
    },
}

/// Reject a handshake the host cannot safely run against.
pub fn validate_hello(event: &ServerEvent) -> Result<(), IpcError> {
    let ServerEvent::Hello {
        protocol,
        capabilities,
    } = event;
    if *protocol != PROTOCOL_VERSION {
        return Err(IpcError::ProtocolMismatch {
            expected: PROTOCOL_VERSION,
            got: *protocol,
        });
    }
    for name in REQUIRED_CAPABILITIES {
        if !capabilities.iter().any(|c| c == name) {
            return Err(IpcError::MissingCapability {
                name: (*name).to_string(),
            });
        }
    }
    Ok(())
}

/// Parse a stdout line as a server event, or `None` if it is not one.
///
/// Returning `None` rather than an error is the whole point: stdout also
/// carries response envelopes, and may carry a stray print or a library
/// banner. The reader steps over anything it cannot use.
#[must_use]
pub fn parse_server_event(line: &str) -> Option<ServerEvent> {
    serde_json::from_str::<ServerEvent>(line.trim()).ok()
}

/// Every way a sidecar interaction can fail.
#[derive(Debug, Clone, PartialEq, thiserror::Error)]
pub enum IpcError {
    #[error("sidecar reports protocol version {got}, host requires {expected}")]
    ProtocolMismatch { expected: u32, got: u32 },
    #[error("sidecar does not implement required capability `{name}`")]
    MissingCapability { name: String },
    #[error("sidecar exited or closed its output stream")]
    SidecarLost,
    #[error("sidecar call `{op}` timed out after {ms} ms")]
    Timeout { op: String, ms: u64 },
    #[error("could not start the sidecar: {message}")]
    Spawn { message: String },
    #[error("sidecar reported `{kind}` for `{op}`: {message}")]
    Sidecar {
        op: String,
        kind: ErrorKind,
        message: String,
    },
    #[error("sidecar sent a line that is not a valid response envelope: {line}")]
    MalformedResponse { line: String },
}
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cargo test -p tidal2ytm-ipc`

Expected: PASS, 10 tests.

- [ ] **Step 7: Run the Rust gates**

Run:

```
cargo fmt --all --check
cargo clippy -p tidal2ytm-ipc --all-targets -- -D warnings
```

Expected: both clean. If `cargo fmt --check` complains, run `cargo fmt --all` and continue.

- [ ] **Step 8: Commit**

```bash
git add Cargo.toml Cargo.lock crates/tidal2ytm-ipc contracts
git commit -m "feat: add the sidecar NDJSON protocol codec and its shared contracts"
```

## Task 5: The Python sidecar

The sidecar is a uv workspace member rather than a second project, so it shares the existing `uv.lock` and both `uv run python -m tidal2ytm_sidecar` and the later PyInstaller build work without a second lockfile to keep in sync.

Two existing pieces of Python are deliberately *reused* rather than reimplemented. `tidal2ytm.auth.tidal_cache_valid` and `ytm_cache_valid` stay the single implementation of "is this provider signed in", and `tidal2ytm.logging_setup` stays the single owner of the level vocabulary. Reimplementing either inside the sidecar would be the exact "two implementations of one rule" failure the spec forbids.

**Files:**
- Create: `sidecar/pyproject.toml`, `sidecar/tidal2ytm_sidecar/__init__.py`, `sidecar/tidal2ytm_sidecar/__main__.py`, `sidecar/tidal2ytm_sidecar/protocol.py`, `sidecar/tidal2ytm_sidecar/ops.py`, `sidecar/tidal2ytm_sidecar/sidecar_logging.py`, `sidecar/tidal2ytm_sidecar/runner.py`
- Create: `conftest.py` (moved up from `tests/conftest.py`), `sidecar/tests/test_protocol.py`, `sidecar/tests/test_ops.py`, `sidecar/tests/test_runner.py`
- Delete: `tests/conftest.py`
- Modify: `pyproject.toml`, `tidal2ytm/logging_setup.py`, `AGENTS.md`, `.gitignore`

**Interfaces:**
- Consumes: `tidal2ytm.auth.paths.TIDAL_TOKEN_FILE`, `tidal2ytm.auth.paths.YTM_AUTH_FILE`, `tidal2ytm.auth.parse_token_expiry`, `tidal2ytm.auth.token_usable`, `tidal2ytm.auth.ytm_cache_valid`, `tidal2ytm.logging_setup.{LOG_LEVELS, resolve_level, redact_payload}`. Consumes the JSON files in `contracts/`.
- Produces: `tidal2ytm_sidecar.protocol.PROTOCOL_VERSION: int = 1`, `CAPABILITIES: tuple[str, ...] = ("auth.status",)`, string constants `AUTH_EXPIRED`/`RATE_LIMITED`/`NOT_FOUND`/`UPSTREAM`/`BAD_REQUEST`/`SIDECAR_LOST`, and functions `hello() -> dict[str, Any]`, `read_message(line: str) -> dict[str, Any]`, `success_response(rid: int, data: object) -> dict[str, Any]`, `error_response(rid: int, kind: str, message: str) -> dict[str, Any]`, `dispatch(op: str, params: Mapping[str, Any], rid: int | None) -> dict[str, Any]`.
- Produces: `tidal2ytm_sidecar.ops.auth_status() -> dict[str, Any]`, `tidal2ytm_sidecar.runner.run(stdin: IO[str], stdout: IO[str]) -> int`, `tidal2ytm_sidecar.runner.main() -> int`, `tidal2ytm_sidecar.sidecar_logging.level_from_env() -> str`.
- Produces: `tidal2ytm.logging_setup.redact_text(value: str) -> str` and `tidal2ytm.logging_setup.setup_stderr_logging(level_name: str) -> None`, both new public functions.

- [ ] **Step 1: Widen the redaction denylist to key patterns**

`logging_setup.py` currently matches four exact key names. The spec requires a case-insensitive substring denylist of ten patterns applied on both sides, and requires free-text error messages to pass through the same filter — ytmusicapi and tidalapi put auth-bearing URLs inside exception text, so a key-only filter leaks the token through the error string.

In `tidal2ytm/logging_setup.py`, replace the `_REDACT_KEYS` constant, `redact_payload`, and add `redact_text`:

```python
# Single owner of the redaction vocabulary. Matched as a case-insensitive
# substring of the key name, because upstream payloads nest the sensitive
# value under a varying prefix: `access_token`, `tokenValue`, `session_id`.
_REDACT_PATTERNS = (
    "token",
    "secret",
    "cookie",
    "authorization",
    "bearer",
    "refresh",
    "access",
    "api_key",
    "client_secret",
    "session",
)

# Long opaque strings preceded by a credential-ish label. Upstream exception
# text embeds the request URL, so a structure-only filter leaks the token
# through the error message.
_CREDENTIAL_RE = re.compile(r"(?i)\b(?:bearer\s+)?[A-Za-z0-9_\-]{20,}\b")


def _is_sensitive(key: object) -> bool:
    lowered = str(key).lower()
    return any(pattern in lowered for pattern in _REDACT_PATTERNS)


def redact_payload(payload: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
    """Replace sensitive keys with '[redacted]', recursively."""
    if isinstance(payload, dict):
        return {
            k: "[redacted]"
            if _is_sensitive(k)
            else (
                redact_payload(cast(dict[str, Any] | list[Any], v))
                if isinstance(v, dict | list)
                else v
            )
            for k, v in payload.items()
        }
    return [
        redact_payload(cast(dict[str, Any] | list[Any], v)) if isinstance(v, dict | list) else v
        for v in payload
    ]


def redact_text(value: str) -> str:
    """Blank out long opaque strings that look like credentials in free text."""
    return _CREDENTIAL_RE.sub("[redacted]", value)
```

Add `import re` to the module's imports. Keep `LOG_LEVELS`, `resolve_level`, `setup_logging`, and `log_call` unchanged — the CLI's file handler is unaffected.

- [ ] **Step 2: Test the redaction change**

`tests/test_logging_emit_sites.py` already has `test_redact_payload_strips_token_fields`, which asserts `access_token` and `Authorization` are blanked. Pattern matching satisfies it. Append two tests next to it:

```python
def test_redact_payload_strips_nested_prefixed_keys() -> None:
    payload: dict[str, Any] = {
        "sessionId": "sess_1",
        "client_secret": "shh",
        "items": [{"apiKey": "k1", "title": "Ember Fall"}],
    }
    out = logging_setup.redact_payload(payload)
    out_d = cast(dict[str, Any], out)
    assert out_d["sessionId"] == "[redacted]"  # noqa: S105
    assert out_d["client_secret"] == "[redacted]"  # noqa: S105
    items = cast(list[dict[str, Any]], out_d["items"])
    assert items[0]["apiKey"] == "[redacted]"  # noqa: S105
    assert items[0]["title"] == "Ember Fall"


def test_redact_text_blanks_credentials_in_upstream_messages() -> None:
    message = "GET /oauth2/token failed: https://x.test/a?k=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    redacted = logging_setup.redact_text(message)
    assert "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" not in redacted
    assert "/oauth2/token" in redacted
```

Run: `uv run pytest tests/test_logging_emit_sites.py -q`

Expected: PASS. If `test_redact_text_blanks_credentials_in_upstream_messages` fails, the regular expression is too narrow; widen `_CREDENTIAL_RE` rather than weakening the assertion.

- [ ] **Step 3: Add the stderr log setup to the shared module**

The CLI logs to a file; the sidecar logs to stderr because its stdout is the IPC channel. Both share the level vocabulary and the formatter, so add a sibling entry point to `tidal2ytm/logging_setup.py`:

```python
def setup_stderr_logging(level_name: str) -> None:
    """Send records to stderr. Used by the sidecar, whose stdout is the IPC channel."""
    name = resolve_level(level_name, None)
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_tidal2ytm_stderr", False):
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s %(message)s"))
    handler._tidal2ytm_stderr = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(LOG_LEVELS[name])
    handler.setLevel(LOG_LEVELS[name])
```

This is a public function because the spec requires the two processes to share one level vocabulary. The module does not currently import `sys` — `logging_setup.py` imports `datetime`, `logging`, `time`, `Callable`, `Path`, and `cast` — so add `import sys` alongside them.

- [ ] **Step 4: Create the sidecar package**

Create `sidecar/pyproject.toml`:

```toml
[project]
name = "tidal2ytm-sidecar"
version = "0.1.0"
description = "Thin Tidal/YouTube Music adapter spoken over NDJSON by the tidal2ytm host."
requires-python = ">=3.11"
dependencies = [
    "tidal2ytm",
    "tidalapi>=0.8.3",
    "ytmusicapi>=1.7.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["tidal2ytm_sidecar"]
```

Create `sidecar/tidal2ytm_sidecar/__init__.py` as an empty file — it exists so the package is importable and so pyright has an anchor.

Add the workspace declaration to the root `pyproject.toml`, immediately after the `[project]` table:

```toml
[tool.uv.workspace]
members = ["sidecar"]
```

- [ ] **Step 5: Relock**

Run: `uv lock`

Expected: `uv.lock` gains a `tidal2ytm-sidecar` entry. Stage `uv.lock` in the commit at the end of this task — the pre-push pytest hook will otherwise abort with "files were modified", and that is the documented cause of that abort, not a failure.

- [ ] **Step 6: Move the test fixture to a root conftest**

`isolated_data_dir` has to be visible to both `tests/` and `sidecar/tests/`. Move it out of `tests/conftest.py` into a new `conftest.py` at the repo root — pytest collects the rootdir conftest for every test beneath it — and delete `tests/conftest.py`. Copy the fixture body verbatim from `tests/conftest.py`; the seed `client_secret_test.json` is still needed by the CLI auth tests.

In `pyproject.toml`, change the pytest `testpaths` to:

```toml
testpaths = ["tests", "sidecar/tests"]
```

- [ ] **Step 7: Write the failing protocol tests**

Create `sidecar/tests/test_protocol.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tidal2ytm_sidecar import protocol

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def _contract(name: str) -> Any:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_hello_matches_the_shared_contract() -> None:
    assert protocol.hello() == _contract("hello.json")


def test_success_response_matches_the_shared_contract() -> None:
    contract = _contract("auth_status.json")
    assert protocol.success_response(1, contract["data"]) == contract


def test_error_response_matches_the_shared_contract() -> None:
    assert (
        protocol.error_response(1, "auth_expired", "YouTube Music token expired; re-authenticate.")
        == _contract("error_auth_expired.json")
    )


def test_read_message_rejects_a_json_array() -> None:
    with pytest.raises(ValueError):
        protocol.read_message("[1, 2, 3]")


def test_read_message_rejects_a_non_json_line() -> None:
    with pytest.raises(ValueError):
        protocol.read_message("not json at all")


# Review Focus #4: the host can name an op the sidecar has not grown yet, and a
# stale packaged sidecar is a normal thing to run into. That has to come back
# as bad_request, not a crash and not a silent empty success.
def test_dispatch_rejects_an_unimplemented_op() -> None:
    response = protocol.dispatch("library.add", {}, rid=7)
    assert response == {
        "id": 7,
        "ok": False,
        "error": {
            "kind": "bad_request",
            "message": "Sidecar does not implement op 'library.add'",
        },
    }


def test_dispatch_rejects_a_request_with_no_id() -> None:
    response = protocol.dispatch("auth.status", {}, rid=None)
    assert response["ok"] is False
    assert response["error"]["kind"] == "bad_request"


def test_error_response_refuses_an_unknown_kind() -> None:
    with pytest.raises(ValueError):
        protocol.error_response(1, "quota_exceeded", "nope")
```

- [ ] **Step 8: Write the failing op tests**

Create `sidecar/tests/test_ops.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from tidal2ytm_sidecar import ops

_SIGNED_OUT = {"valid": False, "expires_at": None}


def test_auth_status_reports_signed_out_when_no_token_is_cached(
    isolated_data_dir: Path,
) -> None:
    assert ops.auth_status() == {"tidal": _SIGNED_OUT, "ytm": _SIGNED_OUT}


def test_auth_status_reports_a_fresh_tidal_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "tidal_token.json").write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "a",
                "refresh_token": "r",
                "expiry_time": "2099-01-01T00:00:00.000000+00:00",
                "user_id": 1,
                "country_code": "US",
            }
        ),
        encoding="utf-8",
    )
    tidal = ops.auth_status()["tidal"]
    assert tidal["valid"] is True
    assert tidal["expires_at"] == "2099-01-01T00:00:00.000000+00:00"


def test_auth_status_reports_a_fresh_ytm_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text(
        json.dumps({"refresh_token": "r", "expires_at": 4102444800}), encoding="utf-8"
    )
    assert ops.auth_status()["ytm"]["valid"] is True


# Review Focus #5: a token file truncated by a crash mid-write must degrade to
# "not signed in". If this raises, one bad file makes the whole app
# unstartable and the only remedy is deleting the data directory by hand.
def test_auth_status_survives_a_corrupt_tidal_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "tidal_token.json").write_text('{"access_token": "tr', encoding="utf-8")
    assert ops.auth_status()["tidal"] == _SIGNED_OUT


def test_auth_status_survives_a_corrupt_ytm_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "ytm_auth.json").write_text("{{{{", encoding="utf-8")
    assert ops.auth_status()["ytm"] == _SIGNED_OUT


def test_auth_status_survives_an_unreadable_token(isolated_data_dir: Path) -> None:
    (isolated_data_dir / "tidal_token.json").mkdir()
    assert ops.auth_status()["tidal"] == _SIGNED_OUT
```

- [ ] **Step 9: Run the new tests and confirm they fail**

Run: `uv run pytest sidecar/tests -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'tidal2ytm_sidecar'`.

- [ ] **Step 10: Write the protocol module**

Create `sidecar/tidal2ytm_sidecar/protocol.py`:

```python
"""NDJSON envelopes for the sidecar protocol.

`contracts/*.json` holds a canonical example of every envelope this module
produces; the Rust suite asserts against the same files, so a rename here fails
`tidal2ytm-ipc`'s tests rather than production.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from . import ops

PROTOCOL_VERSION = 1

# The ops this build implements. The host requires at least `auth.status` and
# refuses to start against a sidecar that does not advertise it.
CAPABILITIES: tuple[str, ...] = ("auth.status",)

# The closed set of failure categories the host understands.
AUTH_EXPIRED = "auth_expired"
RATE_LIMITED = "rate_limited"
NOT_FOUND = "not_found"
UPSTREAM = "upstream"
BAD_REQUEST = "bad_request"
SIDECAR_LOST = "sidecar_lost"

_ERROR_KINDS = frozenset(
    {AUTH_EXPIRED, RATE_LIMITED, NOT_FOUND, UPSTREAM, BAD_REQUEST, SIDECAR_LOST}
)


def hello() -> dict[str, Any]:
    """The handshake, emitted once on startup before any request is answered."""
    return {"event": "hello", "protocol": PROTOCOL_VERSION, "capabilities": list(CAPABILITIES)}


def read_message(line: str) -> dict[str, Any]:
    """Parse one request line. Raises ValueError on anything that is not a JSON object."""
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {line!r}") from exc
    if not isinstance(message, dict):
        raise ValueError(f"expected a JSON object, got {type(message).__name__}: {line!r}")
    return message


def success_response(rid: int, data: object) -> dict[str, Any]:
    return {"id": rid, "ok": True, "data": data}


def error_response(rid: int, kind: str, message: str) -> dict[str, Any]:
    if kind not in _ERROR_KINDS:
        raise ValueError(f"unknown error kind: {kind!r}")
    return {"id": rid, "ok": False, "error": {"kind": kind, "message": message}}


def dispatch(op: str, params: Mapping[str, Any], rid: int | None = None) -> dict[str, Any]:
    """Run one op and return its response envelope.

    An op this build does not implement is a `bad_request`, not a crash: the
    host can name an op ahead of the sidecar, and a stale packaged sidecar is a
    normal thing to run into. `params` is accepted and ignored because
    `auth.status` takes none; ops added later will read it here.
    """
    _ = params
    if rid is None:
        return error_response(0, BAD_REQUEST, "Request is missing an integer `id`")
    if op == "auth.status":
        return success_response(rid, ops.auth_status())
    return error_response(rid, BAD_REQUEST, f"Sidecar does not implement op {op!r}")
```

- [ ] **Step 11: Write the ops module**

Create `sidecar/tidal2ytm_sidecar/ops.py`:

```python
"""The ops the sidecar answers. Every one is a question about the outside world.

Nothing here computes a score, a rank, a certainty, a plan row, a status, or a
count. Those are the host's job, and a value produced here would be a second
implementation of a rule that must exist in exactly one place.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tidal2ytm import auth

_LOG = logging.getLogger(__name__)

# Narrow on purpose. A corrupt cache file is a state to report, not a transport
# or programming bug to swallow: a broad `except Exception` here would turn a
# real bug into a silent "not signed in".
_CACHE_READ_ERRORS = (OSError, ValueError, TypeError)


def _signed_out(reason: Exception) -> dict[str, Any]:
    _LOG.info("token cache unusable, reporting signed out: %s", reason)
    return {"valid": False, "expires_at": None}


def _tidal_state() -> dict[str, Any]:
    try:
        raw = auth.paths.TIDAL_TOKEN_FILE.read_text(encoding="utf-8")
        expires_at = auth.parse_token_expiry(json.loads(raw).get("expiry_time"))
        return {"valid": auth.token_usable(expires_at), "expires_at": expires_at}
    except _CACHE_READ_ERRORS as exc:
        return _signed_out(exc)


def _ytm_state() -> dict[str, Any]:
    try:
        raw = auth.paths.YTM_AUTH_FILE.read_text(encoding="utf-8")
        return {"valid": auth.ytm_cache_valid(), "expires_at": json.loads(raw).get("expires_at")}
    except _CACHE_READ_ERRORS as exc:
        return _signed_out(exc)


def auth_status() -> dict[str, Any]:
    """Report whether each provider has a usable cached token.

    Token material never leaves this process: the host gets a boolean and an
    expiry, nothing else. Validity itself is decided by `tidal2ytm.auth`, which
    stays the single implementation of that rule.
    """
    return {"tidal": _tidal_state(), "ytm": _ytm_state()}
```

- [ ] **Step 12: Write the sidecar logging and the runner**

Create `sidecar/tidal2ytm_sidecar/sidecar_logging.py`:

```python
"""Sidecar logging setup. Records go to stderr because stdout is the IPC channel."""

from __future__ import annotations

import os

from tidal2ytm.logging_setup import LOG_LEVELS, setup_stderr_logging

__all__ = ["LOG_LEVELS", "level_from_env", "setup_stderr_logging"]

ENV_VAR = "TIDAL2YTM_LOG_LEVEL"


def level_from_env() -> str:
    """The level the host set at spawn. An unrecognised value is a hard error.

    Falling back to INFO here would hide the fact that the host sent something
    the sidecar cannot honour.
    """
    raw = os.environ.get(ENV_VAR, "INFO").strip().upper()
    if raw not in LOG_LEVELS:
        raise ValueError(f"Unknown log level in {ENV_VAR}: {raw!r}")
    return raw
```

Create `sidecar/tidal2ytm_sidecar/runner.py`:

```python
"""The NDJSON read/answer loop."""

from __future__ import annotations

import json
import logging
import sys
from typing import IO

from tidal2ytm.logging_setup import redact_payload, redact_text

from . import protocol
from .sidecar_logging import level_from_env, setup_stderr_logging

_LOG = logging.getLogger("tidal2ytm.sidecar")


def _write(stream: IO[str], envelope: dict[str, object]) -> None:
    stream.write(json.dumps(envelope, separators=(",", ":")) + "\n")
    stream.flush()


def run(stdin: IO[str], stdout: IO[str]) -> int:
    """Answer requests until stdin closes. Returns the process exit code."""
    setup_stderr_logging(level_from_env())
    _LOG.info(
        "sidecar up, protocol %s, capabilities %s",
        protocol.PROTOCOL_VERSION,
        list(protocol.CAPABILITIES),
    )
    _write(stdout, protocol.hello())

    for line in stdin:
        if not line.strip():
            continue
        try:
            message = protocol.read_message(line)
        except ValueError as exc:
            _LOG.warning("unreadable request line dropped: %s", redact_text(str(exc)))
            continue

        raw_id = message.get("id")
        rid = raw_id if isinstance(raw_id, int) else None
        op = message.get("op")
        params = message.get("params", {})
        _LOG.info("rid=%s op=%s params=%s", rid, op, json.dumps(redact_payload(params)))

        if not isinstance(op, str):
            _write(
                stdout,
                protocol.error_response(rid or 0, protocol.BAD_REQUEST, "Request is missing a string `op`"),
            )
            continue

        try:
            _write(stdout, protocol.dispatch(op, params, rid))
        except Exception as exc:  # one bad op must not end a long-lived session
            _LOG.exception("op %s failed", op)
            _write(
                stdout,
                protocol.error_response(rid or 0, protocol.UPSTREAM, redact_text(str(exc))),
            )

    _LOG.info("stdin closed, exiting")
    return 0


def main() -> int:
    try:
        return run(sys.stdin, sys.stdout)
    except ValueError as exc:
        # An unusable TIDAL2YTM_LOG_LEVEL is a configuration error the host has
        # to hear about, so it goes to stderr and a non-zero exit rather than
        # silently defaulting.
        print(f"tidal2ytm-sidecar: {exc}", file=sys.stderr)
        return 2
```

Create `sidecar/tidal2ytm_sidecar/__main__.py`:

```python
from __future__ import annotations

import sys

from .runner import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 13: Write the failing runner tests**

Create `sidecar/tests/test_runner.py`:

```python
from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

from tidal2ytm_sidecar import runner


def _run(lines: str) -> list[dict[str, Any]]:
    stdout = StringIO()
    runner.run(StringIO(lines), stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_handshake_is_the_first_thing_written() -> None:
    envelopes = _run("")
    assert envelopes[0]["event"] == "hello"
    assert envelopes[0]["protocol"] == 1
    assert envelopes[0]["capabilities"] == ["auth.status"]


def test_auth_status_is_answered_inline(isolated_data_dir: Path) -> None:
    envelopes = _run('{"id": 1, "op": "auth.status", "params": {}}\n')
    assert envelopes[1]["id"] == 1
    assert envelopes[1]["ok"] is True


def test_a_blank_line_is_ignored(isolated_data_dir: Path) -> None:
    envelopes = _run('\n{"id": 2, "op": "auth.status", "params": {}}\n\n')
    assert len(envelopes) == 2


def test_an_unparseable_line_does_not_end_the_session(isolated_data_dir: Path) -> None:
    envelopes = _run('garbage\n{"id": 3, "op": "auth.status", "params": {}}\n')
    assert envelopes[1]["id"] == 3


def test_a_request_with_a_non_string_op_is_answered_not_dropped(
    isolated_data_dir: Path,
) -> None:
    envelopes = _run('{"id": 4, "op": 5, "params": {}}\n')
    assert envelopes[1]["ok"] is False
    assert envelopes[1]["error"]["kind"] == "bad_request"
```

- [ ] **Step 14: Run the sidecar suite and confirm it passes**

Run: `uv run pytest sidecar/tests -q`

Expected: PASS, 19 tests. If `isolated_data_dir` is reported as an unknown fixture, Step 6's root `conftest.py` is missing or not at the repo root.

- [ ] **Step 15: Update the AGENTS.md log-level invariant**

`AGENTS.md` says `--log-level` beats `TIDAL2YTM_LOG_LEVEL` beats `INFO` and that `logging_setup.LOG_LEVELS` owns the accepted set. That is still true, but the flag is no longer the only way a level arrives. Change that bullet to:

```
- `--log-level` flag beats `TIDAL2YTM_LOG_LEVEL` env beats default `INFO`; the accepted set is owned by `logging_setup.LOG_LEVELS`. An invalid env value is a hard CLI error, never a silent fallback. The GUI host passes the same env var to the sidecar at spawn, so the level applies even if the sidecar dies before its handshake.
```

Also update the testing-layout reference in `AGENTS.md` from `tests/conftest.py:isolated_data_dir` to `conftest.py:isolated_data_dir`, since the fixture moved in Step 6.

- [ ] **Step 16: Update .gitignore for the build artefacts this task introduces**

Append to `.gitignore`:

```gitignore
# Rust, frontend, and sidecar build output
target/
node_modules/
.vite/
dist/
src-tauri/gen/
src-tauri/binaries/
sidecar/build/
sidecar/dist/
```

`target/` and `dist/` already exist as rules from the Python template, so those two lines are redundant; keep only what is new:

```gitignore
node_modules/
.vite/
src-tauri/gen/
src-tauri/binaries/
sidecar/build/
sidecar/dist/
```

Note the existing `*.spec` rule at line 55 will swallow the PyInstaller spec file written in Task 11. Add a negation for it in the same commit as that file, not now.

- [ ] **Step 17: Run the whole Python gate set**

Run, in order:

```
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
```

Expected: all four clean. pyright checks `sidecar/` too, because it defaults to the whole project directory — the sidecar is fully annotated and must pass strict mode.

- [ ] **Step 18: Commit**

```bash
git add sidecar conftest.py tests pyproject.toml uv.lock tidal2ytm/logging_setup.py AGENTS.md .gitignore
git commit -m "feat: add the NDJSON sidecar with auth status and shared log redaction"
```

## Task 6: The sidecar client

This is the host's half of the boundary. Its hard requirements: correlate responses by `id` rather than by arrival order, fail in-flight calls when the process dies, and forward the sidecar's stderr without ever writing to the host's own stdout.

The tests here drive a tiny Python stub, so they run anywhere `python` exists. Task 7 then repeats the important ones against the real sidecar.

**Files:**
- Create: `crates/tidal2ytm-ipc/src/client.rs`
- Modify: `crates/tidal2ytm-ipc/src/lib.rs`

**Interfaces:**
- Consumes: everything from `protocol.rs` in Task 4.
- Produces: `tidal2ytm_ipc::{SidecarConfig, SidecarClient, log_level_env, LOG_LEVELS}`. `SidecarConfig { program: PathBuf, args: Vec<String>, cwd: PathBuf, env: Vec<(String, String)>, handshake_timeout: Duration }`. `SidecarClient::spawn(&SidecarConfig) -> Result<SidecarClient, IpcError>` starts the process and performs the `hello` handshake, refusing a mismatch. `SidecarClient::call(&self, op: &str, params: Value, timeout: Option<Duration>) -> Result<Value, IpcError>`. `SidecarClient::shutdown(&self) -> Result<(), IpcError>` closes stdin, waits briefly, then kills. `SidecarClient::is_alive(&self) -> bool`. `SidecarClient::take_stderr(&self) -> Option<mpsc::UnboundedReceiver<String>>` hands the forwarded lines to the caller, which owns them from that point on. `log_level_env(level: &str) -> Result<(&'static str, String), IpcError>` validates against `LOG_LEVELS` and returns `(canonical_name, env_value)`.

- [ ] **Step 1: Write the failing tests**

Create `crates/tidal2ytm-ipc/src/client.rs` with the tests plus `todo!()` bodies:

```rust
//! The sidecar process client.
//!
//! Responsibilities: spawn the process, complete the `hello` handshake, match
//! responses to requests by `id`, fail in-flight calls when the process dies,
//! and forward the sidecar's stderr. It never writes to the host's stdout and
//! never interprets a response beyond unwrapping its envelope.

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    const HELLO: &str =
        r#"import json,sys
print(json.dumps({"event":"hello","protocol":1,"capabilities":["auth.status"]}), flush=True)
"#;

    fn config(script: &str) -> SidecarConfig {
        SidecarConfig {
            program: interpreter().into(),
            args: vec!["-c".to_string(), script.to_string()],
            cwd: std::env::temp_dir(),
            env: vec![("TIDAL2YTM_LOG_LEVEL".to_string(), "INFO".to_string())],
            handshake_timeout: Duration::from_secs(10),
        }
    }

    fn interpreter() -> String {
        if let Ok(explicit) = std::env::var("TIDAL2YTM_TEST_PYTHON") {
            return explicit;
        }
        if cfg!(windows) { "python".to_string() } else { "python3".to_string() }
    }

    #[tokio::test]
    async fn a_sidecar_that_exits_before_the_handshake_is_a_loss() {
        let cfg = config("import sys; sys.exit(3)");
        assert!(matches!(SidecarClient::spawn(&cfg).await, Err(IpcError::SidecarLost)));
    }

    #[tokio::test]
    async fn a_handshake_that_never_arrives_times_out() {
        let mut cfg = config("import time; time.sleep(30)");
        cfg.handshake_timeout = Duration::from_millis(400);
        assert!(matches!(
            SidecarClient::spawn(&cfg).await,
            Err(IpcError::Timeout { .. })
        ));
    }

    #[tokio::test]
    async fn a_protocol_mismatch_is_refused_at_startup() {
        let script = r#"import json
print(json.dumps({"event":"hello","protocol":99,"capabilities":["auth.status"]}), flush=True)
"#;
        assert!(matches!(
            SidecarClient::spawn(&config(script)).await,
            Err(IpcError::ProtocolMismatch { expected: 1, got: 99 })
        ));
    }

    #[tokio::test]
    async fn a_sidecar_missing_a_required_capability_is_refused_at_startup() {
        let script = r#"import json
print(json.dumps({"event":"hello","protocol":1,"capabilities":[]}), flush=True)
"#;
        assert!(matches!(
            SidecarClient::spawn(&config(script)).await,
            Err(IpcError::MissingCapability { .. })
        ));
    }

    #[tokio::test]
    async fn a_sidecar_that_writes_junk_before_the_handshake_still_starts() {
        let script = format!("import json,sys\nprint('a library banner', file=sys.stderr)\nprint('more noise')\n{HELLO}");
        assert!(SidecarClient::spawn(&config(&script)).await.is_ok());
    }

    #[tokio::test]
    async fn sidecar_stderr_is_forwarded_and_stdout_stays_clean() {
        let script = format!("import sys\nprint('diagnostic line', file=sys.stderr, flush=True)\n{HELLO}for _ in sys.stdin:\n    pass\n");
        let client = SidecarClient::spawn(&config(&script)).await.expect("starts");
        let mut lines = client.take_stderr().expect("stderr channel is present");
        let forwarded = tokio::time::timeout(Duration::from_secs(5), lines.recv())
            .await
            .expect("a line arrives")
            .expect("the channel is open");
        assert!(forwarded.contains("diagnostic line"), "got: {forwarded}");
    }

    // Review Focus #1: the sidecar is free to answer request 2 before request
    // 1. Pairing by arrival order instead of by `id` resolves each call with
    // the other's data, and since `auth.status` is the first thing the app
    // calls, that surfaces as one provider's state under the other's label.
    #[tokio::test]
    async fn concurrent_calls_each_receive_their_own_response() {
        let script = format!("import json,sys,time\n{HELLO}reqs=[json.loads(l) for l in sys.stdin]\nfor m in sorted(reqs, key=lambda r: -r['id']):\n    time.sleep(0.05)\n    print(json.dumps({{'id': m['id'], 'ok': True, 'data': {{'echo': m['params']['tag']}}}}), flush=True)\n");
        let client = Arc::new(SidecarClient::spawn(&config(&script)).await.expect("starts"));

        let a = {
            let client = Arc::clone(&client);
            tokio::spawn(async move {
                client.call("auth.status", json!({ "tag": "a" }), None).await
            })
        };
        let b = {
            let client = Arc::clone(&client);
            tokio::spawn(async move {
                client.call("auth.status", json!({ "tag": "b" }), None).await
            })
        };

        assert_eq!(a.await.expect("a joins").expect("a ok")["echo"], "a");
        assert_eq!(b.await.expect("b joins").expect("b ok")["echo"], "b");
    }

    // Review Focus #2: the in-flight call must fail, not hang and not invent a
    // success.
    #[tokio::test]
    async fn a_sidecar_that_dies_mid_request_fails_the_in_flight_call() {
        let script = format!("import sys\n{HELLO}sys.stdin.readline()\nsys.exit(9)\n");
        let client = SidecarClient::spawn(&config(&script)).await.expect("starts");
        let result = client
            .call("auth.status", json!({}), Some(Duration::from_secs(5)))
            .await;
        assert!(matches!(result, Err(IpcError::SidecarLost)), "got: {result:?}");
        assert!(!client.is_alive());
    }

    #[tokio::test]
    async fn a_call_after_the_sidecar_died_fails_immediately() {
        let script = format!("import sys\n{HELLO}sys.stdin.readline()\nsys.exit(9)\n");
        let client = SidecarClient::spawn(&config(&script)).await.expect("starts");
        let _ = client.call("auth.status", json!({}), Some(Duration::from_secs(5))).await;
        let second = client.call("auth.status", json!({}), None).await;
        assert!(matches!(second, Err(IpcError::SidecarLost)));
    }

    #[tokio::test]
    async fn a_timed_out_call_is_dropped_and_later_calls_still_work() {
        let script = format!("import json,sys,time\n{HELLO}for line in sys.stdin:\n    m=json.loads(line)\n    time.sleep(0.3)\n    print(json.dumps({{'id': m['id'], 'ok': True, 'data': {{'ok': True}}}}), flush=True)\n");
        let client = SidecarClient::spawn(&config(&script)).await.expect("starts");
        let first = client
            .call("auth.status", json!({}), Some(Duration::from_millis(50)))
            .await;
        assert!(matches!(first, Err(IpcError::Timeout { .. })), "got: {first:?}");
        let second = client
            .call("auth.status", json!({}), Some(Duration::from_secs(10)))
            .await;
        assert_eq!(second.expect("the next call still works")["ok"], json!(true));
    }

    #[test]
    fn a_valid_log_level_becomes_an_env_pair() {
        assert_eq!(
            log_level_env("debug").unwrap(),
            ("DEBUG", "DEBUG".to_string())
        );
    }

    #[test]
    fn an_invalid_log_level_is_refused() {
        assert!(log_level_env("LOUD").is_err());
    }
}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cargo test -p tidal2ytm-ipc`

Expected: FAIL to compile — `SidecarConfig`, `SidecarClient`, and `log_level_env` are undefined.

- [ ] **Step 3: Implement the client**

Prepend this to `crates/tidal2ytm-ipc/src/client.rs`, above the `#[cfg(test)]` module:

```rust
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{Mutex, mpsc, oneshot};

use crate::protocol::{
    ErrorKind, IpcError, Request, Response, WireError, parse_server_event, validate_hello,
};

/// Level names the host and the sidecar agree on, mirroring
/// `tidal2ytm.logging_setup.LOG_LEVELS`. An unknown name is refused rather than
/// defaulted, so a typo in the GUI selector surfaces immediately.
pub const LOG_LEVELS: &[&str] = &["OFF", "CRITICAL", "ERROR", "WARN", "INFO", "DEBUG"];

/// Validate a level name and return `(canonical_name, env_value)` to hand the
/// sidecar at spawn.
pub fn log_level_env(level: &str) -> Result<(&'static str, String), IpcError> {
    let upper = level.trim().to_ascii_uppercase();
    LOG_LEVELS
        .iter()
        .copied()
        .find(|name| *name == upper)
        .map(|name| (name, name.to_string()))
        .ok_or_else(|| IpcError::Spawn {
            message: format!("unknown log level: {level:?}"),
        })
}

/// How to start the sidecar.
#[derive(Debug, Clone)]
pub struct SidecarConfig {
    /// The executable. A packaged build passes the bundled binary; dev and CI
    /// pass an interpreter with `-m tidal2ytm_sidecar`.
    pub program: PathBuf,
    pub args: Vec<String>,
    /// Working directory. The sidecar resolves nothing relative to this, but
    /// setting it keeps a crash report pointing somewhere meaningful.
    pub cwd: PathBuf,
    /// Passed at spawn so the level applies even if the sidecar dies before its
    /// handshake.
    pub env: Vec<(String, String)>,
    pub handshake_timeout: Duration,
}

struct Pending {
    op: String,
    sender: oneshot::Sender<Result<Value, IpcError>>,
}

struct Inner {
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<u64, Pending>>,
    alive: AtomicBool,
}

/// A live sidecar. One per app; shared by reference.
pub struct SidecarClient {
    inner: Arc<Inner>,
    next_id: AtomicU64,
    stderr_rx: Mutex<Option<mpsc::UnboundedReceiver<String>>>,
}

impl SidecarClient {
    /// Start the sidecar and complete the handshake.
    ///
    /// A protocol mismatch or a missing required capability kills the process
    /// and returns the error, so the host can report a clear startup failure
    /// instead of letting every later job fail mysteriously. Request ids count
    /// from 1, which is what `contracts/auth_status.json` pins.
    pub async fn spawn(config: &SidecarConfig) -> Result<Self, IpcError> {
        let mut command = Command::new(&config.program);
        command
            .args(&config.args)
            .current_dir(&config.cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            // The host owns the level; a stale value inherited from the
            // developer's shell must not win.
            .env_remove("TIDAL2YTM_LOG_LEVEL");
        for (key, value) in &config.env {
            command.env(key, value);
        }

        let mut child = command.spawn().map_err(|e| IpcError::Spawn {
            message: e.to_string(),
        })?;
        let stdin = child.stdin.take().ok_or(IpcError::Spawn {
            message: "sidecar stdin was not piped".to_string(),
        })?;
        let stdout = child.stdout.take().ok_or(IpcError::Spawn {
            message: "sidecar stdout was not piped".to_string(),
        })?;
        let stderr = child.stderr.take().ok_or(IpcError::Spawn {
            message: "sidecar stderr was not piped".to_string(),
        })?;

        let (line_tx, mut line_rx) = mpsc::unbounded_channel::<Option<String>>();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            loop {
                match lines.next_line().await {
                    Ok(Some(line)) => {
                        if line_tx.send(Some(line)).is_err() {
                            return;
                        }
                    }
                    // EOF, or an unreadable pipe. Either way the sidecar is gone.
                    Ok(None) | Err(_) => break,
                }
            }
            let _ = line_tx.send(None);
        });

        let (stderr_tx, stderr_rx) = mpsc::unbounded_channel();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if stderr_tx.send(line).is_err() {
                    return;
                }
            }
        });

        let client = Self {
            inner: Arc::new(Inner {
                child: Mutex::new(child),
                stdin: Mutex::new(Some(stdin)),
                pending: Mutex::new(HashMap::new()),
                alive: AtomicBool::new(true),
            }),
            next_id: AtomicU64::new(1),
            stderr_rx: Mutex::new(Some(stderr_rx)),
        };

        // Handshake: read lines until a server event arrives, stepping over
        // anything that is not one.
        let awaited = tokio::time::timeout(config.handshake_timeout, async {
            while let Some(line) = line_rx.recv().await {
                if let Some(event) = parse_server_event(&line) {
                    return Some(event);
                }
            }
            None
        })
        .await;

        let event = match awaited {
            Err(_) => {
                client.force_kill().await;
                return Err(IpcError::Timeout {
                    op: "hello".to_string(),
                    ms: config.handshake_timeout.as_millis() as u64,
                });
            }
            Ok(None) => {
                client.force_kill().await;
                return Err(IpcError::SidecarLost);
            }
            Ok(Some(event)) => event,
        };

        if let Err(error) = validate_hello(&event) {
            client.force_kill().await;
            return Err(error);
        }

        // Hand the reader to the dispatcher now that the handshake is done.
        let inner = Arc::clone(&client.inner);
        tokio::spawn(async move {
            while let Some(line) = line_rx.recv().await {
                if parse_server_event(&line).is_some() {
                    continue;
                }
                // An unparseable line cannot be attributed to a request, so it
                // is stepped over. The caller's per-op timeout is the backstop:
                // a response that never arrives fails the call rather than
                // hanging it.
                let Ok(response) = serde_json::from_str::<Response>(&line) else {
                    continue;
                };
                let entry = inner.pending.lock().await.remove(&response.id);
                if let Some(pending) = entry {
                    let _ = pending.sender.send(to_result(&pending.op, response));
                }
            }
            // EOF: the sidecar is gone. Every call still waiting fails now
            // instead of waiting out its timeout.
            inner.alive.store(false, Ordering::Release);
            let drained: Vec<Pending> = {
                let mut pending = inner.pending.lock().await;
                pending.drain().map(|(_, entry)| entry).collect()
            };
            for entry in drained {
                let _ = entry.sender.send(Err(IpcError::SidecarLost));
            }
        });

        Ok(client)
    }

    /// Send one request and wait for its response.
    ///
    /// `timeout` is per-op and comes from the spec: 30 s for search and album
    /// fetches, 15 s for `library.add`, none for `auth.begin`. On timeout the
    /// request leaves the pending table, so a response arriving later is
    /// discarded rather than resolving a call that already gave up.
    pub async fn call(
        &self,
        op: &str,
        params: Value,
        timeout: Option<Duration>,
    ) -> Result<Value, IpcError> {
        if !self.is_alive() {
            return Err(IpcError::SidecarLost);
        }
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (sender, receiver) = oneshot::channel();
        self.inner
            .pending
            .lock()
            .await
            .insert(id, Pending {
                op: op.to_string(),
                sender,
            });

        let Ok(line) = serde_json::to_string(&Request { id, op, params }) else {
            self.inner.pending.lock().await.remove(&id);
            return Err(IpcError::MalformedResponse {
                line: format!("could not serialise the request for op {op:?}"),
            });
        };

        let written = {
            let mut guard = self.inner.stdin.lock().await;
            match guard.as_mut() {
                Some(stdin) => stdin
                    .write_all(line.as_bytes())
                    .await
                    .and_then(|()| stdin.write_all(b"\n").await)
                    .and_then(|()| stdin.flush().await)
                    .is_ok(),
                None => false,
            }
        };
        if !written {
            self.inner.pending.lock().await.remove(&id);
            return Err(IpcError::SidecarLost);
        }

        let received = match timeout {
            Some(limit) => match tokio::time::timeout(limit, receiver).await {
                Ok(result) => result.map_err(|_| IpcError::SidecarLost)?,
                Err(_) => {
                    self.inner.pending.lock().await.remove(&id);
                    return Err(IpcError::Timeout {
                        op: op.to_string(),
                        ms: limit.as_millis() as u64,
                    });
                }
            },
            None => receiver.await.map_err(|_| IpcError::SidecarLost)?,
        };
        received
    }

    /// Close stdin. The sidecar sees EOF and exits on its own; there is no
    /// shutdown op. If it has not exited shortly after, it is killed.
    pub async fn shutdown(&self) -> Result<(), IpcError> {
        self.inner.stdin.lock().await.take();
        let mut child = self.inner.child.lock().await;
        match tokio::time::timeout(Duration::from_secs(3), child.wait()).await {
            Ok(Ok(_)) => {}
            Ok(Err(e)) => {
                return Err(IpcError::Spawn {
                    message: e.to_string(),
                });
            }
            Err(_) => {
                let _ = child.start_kill();
            }
        }
        self.inner.alive.store(false, Ordering::Release);
        Ok(())
    }

    /// False once the process is gone. Not a promise it is healthy: the spec
    /// requires a fresh `auth.status` before a respawned sidecar is reported
    /// good.
    #[must_use]
    pub fn is_alive(&self) -> bool {
        self.inner.alive.load(Ordering::Acquire)
    }

    /// Take the forwarded stderr lines. The caller owns them from here.
    pub async fn take_stderr(&self) -> Option<mpsc::UnboundedReceiver<String>> {
        self.stderr_rx.lock().await.take()
    }

    async fn force_kill(&self) {
        let mut child = self.inner.child.lock().await;
        let _ = child.start_kill();
        let _ = child.wait().await;
        self.inner.alive.store(false, Ordering::Release);
    }
}

fn to_result(op: &str, response: Response) -> Result<Value, IpcError> {
    if response.ok {
        return Ok(response.data.unwrap_or(Value::Null));
    }
    let WireError { kind, message } = response.error.unwrap_or(WireError {
        kind: ErrorKind::Upstream,
        message: "sidecar reported a failure with no error detail".to_string(),
    });
    Err(IpcError::Sidecar {
        op: op.to_string(),
        kind,
        message,
    })
}
```

Delete the two sentences immediately following that code block — they referred to an earlier draft of the error arm and no longer apply. Then update `crates/tidal2ytm-ipc/src/lib.rs` to re-export the client:

```rust
//! The sidecar's NDJSON protocol and the client that speaks it.
//!
//! Like `tidal2ytm-core`, this crate must not depend on `tauri`: the
//! round-trip test in `tests/round_trip.rs` spawns a real Python process, and
//! that test is what proves the boundary works on a headless Linux runner.

pub mod client;
pub mod protocol;

pub use client::{LOG_LEVELS, SidecarClient, SidecarConfig, log_level_env};
pub use protocol::{ErrorKind, IpcError};
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run on Windows:

```powershell
$env:TIDAL2YTM_TEST_PYTHON = (uv run python -c "import sys; print(sys.executable)")
cargo test -p tidal2ytm-ipc
```

Expected: PASS, 22 tests (10 protocol, 12 client). On a machine without `TIDAL2YTM_TEST_PYTHON`, the Python-stub tests fail with a `Spawn` error naming the interpreter; set the variable as above. They are not silently skipped, because a skipped boundary test is worse than a red one.

- [ ] **Step 5: Run the Rust gates**

Run:

```
cargo fmt --all --check
cargo clippy -p tidal2ytm-ipc --all-targets -- -D warnings
```

Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add crates/tidal2ytm-ipc
git commit -m "feat: add the sidecar client with id correlation and crash handling"
```

## Task 7: The round-trip test against the real sidecar

This is the Phase 2 gate test. It spawns the actual Python sidecar, not a stub, because the thing being proven is that two independently written implementations of one protocol agree. It also proves the sidecar is startable with nothing but `uv sync`, which is what Linux CI relies on.

`auth.status` is the right op: with no cached tokens it reads two absent files and touches no network, so the test is hermetic and fast.

**Files:**
- Create: `crates/tidal2ytm-ipc/tests/round_trip.rs`

**Interfaces:**
- Consumes: `SidecarClient`, `SidecarConfig`, `log_level_env` from Task 6; the `sidecar` package from Task 5.
- Produces: no production API. The env var `TIDAL2YTM_SIDECAR_PYTHON` names the interpreter, falling back to `python3` on unix and `python` on Windows.

- [ ] **Step 1: Write the test**

Create `crates/tidal2ytm-ipc/tests/round_trip.rs`:

```rust
//! Proves the Rust host and the Python sidecar agree on the wire.
//!
//! This spawns the real sidecar. That is the point: a stub would only prove
//! the client can talk to itself. It is also what keeps the boundary honest on
//! a headless Linux runner, where no Tauri and no display exist.

use std::path::PathBuf;
use std::time::Duration;

use serde_json::Value;
use tidal2ytm_ipc::{IpcError, SidecarClient, SidecarConfig, log_level_env};

/// The repository root, derived from this crate's manifest so the test does
/// not depend on the working directory cargo happened to choose.
fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("the workspace root exists")
}

fn interpreter() -> String {
    if let Ok(explicit) = std::env::var("TIDAL2YTM_SIDECAR_PYTHON") {
        return explicit;
    }
    if cfg!(windows) {
        "python".to_string()
    } else {
        "python3".to_string()
    }
}

fn config() -> SidecarConfig {
    let root = repo_root();
    let (level, value) = log_level_env("INFO").expect("INFO is a valid level");
    assert_eq!(level, "INFO");
    SidecarConfig {
        program: interpreter().into(),
        args: vec!["-m".to_string(), "tidal2ytm_sidecar".to_string()],
        cwd: root.join("sidecar"),
        env: vec![
            ("TIDAL2YTM_LOG_LEVEL".to_string(), value),
            (
                "PYTHONPATH".to_string(),
                root.join("sidecar").to_string_lossy().into_owned(),
            ),
        ],
        handshake_timeout: Duration::from_secs(20),
    }
}

async fn spawn_or_explain(config: &SidecarConfig) -> SidecarClient {
    match SidecarClient::spawn(config).await {
        Ok(client) => client,
        Err(IpcError::Spawn { message }) => panic!(
            "could not start the sidecar with {:?}: {message}\n\
             Run `uv sync --all-groups` first, or set TIDAL2YTM_SIDECAR_PYTHON to an \
             interpreter that has tidal2ytm-sidecar installed.",
            config.program
        ),
        Err(other) => panic!("sidecar failed to start: {other}"),
    }
}

#[tokio::test]
async fn the_real_sidecar_answers_auth_status() {
    let config = config();
    let client = spawn_or_explain(&config).await;

    let data: Value = client
        .call(
            "auth.status",
            serde_json::json!({}),
            Some(Duration::from_secs(20)),
        )
        .await
        .expect("auth.status is answered");

    for provider in ["tidal", "ytm"] {
        let state = &data[provider];
        assert!(
            state["valid"].is_boolean(),
            "{provider}.valid must be a boolean, got {state}"
        );
        assert!(
            state["expires_at"].is_null() || state["expires_at"].is_string(),
            "{provider}.expires_at must be a string or null, got {state}"
        );
    }
    // Token material must never cross the boundary, whatever the cache holds.
    assert!(
        data.to_string().to_lowercase().contains("token") == false,
        "auth.status leaked something token-shaped: {data}"
    );

    client.shutdown().await.expect("sidecar shuts down");
}

#[tokio::test]
async fn the_real_sidecar_reports_a_bad_request_for_an_op_it_lacks() {
    let config = config();
    let client = spawn_or_explain(&config).await;

    let error = client
        .call(
            "library.add",
            serde_json::json!({}),
            Some(Duration::from_secs(20)),
        )
        .await
        .expect_err("library.add is not implemented in this build");
    assert!(
        error.to_string().contains("library.add"),
        "the error must name the op, got: {error}"
    );

    client.shutdown().await.expect("sidecar shuts down");
}

#[tokio::test]
async fn closing_stdin_stops_the_real_sidecar() {
    let config = config();
    let client = spawn_or_explain(&config).await;
    client.shutdown().await.expect("sidecar shuts down");
    assert!(!client.is_alive());
}

#[tokio::test]
async fn concurrent_auth_status_calls_each_get_their_own_answer() {
    let config = config();
    let client = std::sync::Arc::new(spawn_or_explain(&config).await);

    let a = {
        let client = std::sync::Arc::clone(&client);
        tokio::spawn(async move {
            client
                .call("auth.status", serde_json::json!({}), Some(Duration::from_secs(20)))
                .await
        })
    };
    let b = {
        let client = std::sync::Arc::clone(&client);
        tokio::spawn(async move {
            client
                .call("auth.status", serde_json::json!({}), Some(Duration::from_secs(20)))
                .await
        })
    };

    for handle in [a, b] {
        let data = handle.await.expect("task joins").expect("call succeeds");
        assert!(data["tidal"]["valid"].is_boolean());
        assert!(data["ytm"]["valid"].is_boolean());
    }
}
```

- [ ] **Step 2: Run the round-trip test**

Run on Windows:

```powershell
$env:TIDAL2YTM_SIDECAR_PYTHON = (uv run python -c "import sys; print(sys.executable)")
cargo test -p tidal2ytm-ipc --test round_trip
```

Expected: PASS, 4 tests. If `spawn_or_explain` panics with "could not start the sidecar", run `uv sync --all-groups` and retry — the panic says so.

- [ ] **Step 3: Run the whole Rust suite**

Run:

```
cargo test -p tidal2ytm-core -p tidal2ytm-ipc
```

Expected: PASS, 32 tests (6 core, 26 ipc).

- [ ] **Step 4: Verify the sidecar starts from the interpreter CI will use**

Run:

```bash
uv sync --all-groups
cargo test -p tidal2ytm-ipc --test round_trip
```

Expected: PASS without `TIDAL2YTM_SIDECAR_PYTHON` set, because the uv venv's `python` is the one carrying the sidecar's dependencies. If it fails with a `Spawn` error, the failure message is the actionable part — that is what Step 1's panic text is for.

- [ ] **Step 5: Run the Rust gates**

Run:

```
cargo fmt --all --check
cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc --all-targets -- -D warnings
```

Expected: both clean. Clippy must be clean on the integration-test target too, which `--all-targets` covers.

- [ ] **Step 6: Commit**

```bash
git add crates/tidal2ytm-ipc/tests
git commit -m "test: round-trip the real Python sidecar from the Rust host"
```

## Task 8: The frontend toolchain, the app shell, and its first test

Nothing here reaches a Tauri command yet. This task is the frontend's toolchain plus a shell that builds, lints, type-checks, and tests clean, so Task 9 can add one command and one screen without also debugging five tools.

**Files:**
- Create: `package.json`, `pnpm-lock.yaml` (generated), `.npmrc`, `svelte.config.ts`, `vite.config.ts`, `vitest.config.ts`, `vitest.setup.ts`, `tsconfig.json`, `eslint.config.ts`, `.prettierrc.toml`, `.prettierignore`, `index.html`
- Create: `src/main.ts`, `src/App.svelte`, `src/styles/app.css`, `src/lib/router.ts`, `src/lib/types.ts`, `src/lib/components/AuthStatus.svelte`, `src/lib/components/AuthStatus.test.ts`, `src/routes/Home.svelte`

**Interfaces:**
- Consumes: nothing.
- Produces: `src/lib/types.ts` exports `interface ProviderState { valid: boolean; expires_at: string | null }` and `interface AuthStatus { tidal: ProviderState; ytm: ProviderState }`. `src/lib/router.ts` exports `type RouteId`, `ROUTES: readonly RouteId[]`, `currentRoute: Readable<RouteId>`, and `navigate(route: RouteId): void`. `src/lib/components/AuthStatus.svelte` takes props `label: string` and `state: ProviderState`. npm scripts: `dev`, `build`, `preview`, `check`, `lint`, `lint:fix`, `format`, `format:check`, `test`, `tauri`.

- [ ] **Step 1: Create package.json with every version pinned**

Create `package.json`:

```json
{
  "name": "tidal2ytm",
  "private": true,
  "version": "0.7.0",
  "type": "module",
  "packageManager": "pnpm@11.28.2",
  "engines": {
    "node": ">=24.0.0"
  },
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "check": "svelte-check --tsconfig ./tsconfig.json",
    "lint": "eslint .",
    "lint:fix": "eslint . --fix",
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "test": "vitest run",
    "tauri": "tauri"
  },
  "dependencies": {
    "@tauri-apps/api": "2.12.1"
  },
  "devDependencies": {
    "@eslint/compat": "2.1.1",
    "@eslint/js": "10.0.1",
    "@sveltejs/vite-plugin-svelte": "7.3.1",
    "@tauri-apps/cli": "2.12.1",
    "@testing-library/svelte": "5.4.2",
    "@types/node": "24.19.0",
    "eslint": "10.11.0",
    "eslint-config-prettier": "10.1.8",
    "eslint-plugin-svelte": "3.23.0",
    "globals": "17.12.0",
    "jiti": "2.7.0",
    "jsdom": "30.1.1",
    "prettier": "3.9.9",
    "prettier-plugin-svelte": "4.1.1",
    "svelte": "5.57.1",
    "svelte-check": "4.7.6",
    "typescript": "6.0.3",
    "typescript-eslint": "8.71.0",
    "vite": "8.3.1",
    "vitest": "5.0.3"
  }
}
```

Four of these pins are load-bearing. `typescript` is `6.0.3` because it is the highest version both `svelte-check` (`^5.0.0 || ^6.0.0`) and `typescript-eslint` (`>=4.8.4 <6.1.0`) accept, and it is the last pure-JS release — TypeScript 7 is a native Go port with no compiler API for `typescript-estree` to call, so 7.x is npm's current default and unusable here. `@tauri-apps/cli` is `2.12.1` to match the `tauri` crate that Task 9 adds; the two are released in lockstep and a mismatch breaks the build. `jiti` is `2.7.0` because ESLint 10 dropped support for jiti below `2.2.0` and refuses to load `eslint.config.ts` without it. `@eslint/compat` and `eslint-config-prettier` exist for `includeIgnoreFile` and for turning off the stylistic rules Prettier owns, so the two tools never argue. There is deliberately no `@tauri-apps/plugin-shell` (banned by the spec) and no `@tauri-apps/plugin-fs` (Phase 4).

`"name": "tidal2ytm"` and `"version": "0.7.0"` are not decoration. The name keeps the project's naming rule intact across the one manifest that cannot be TOML, and the version must match `pyproject.toml` and `tauri.conf.json` — a Tauri build stamps its version into the bundle from `tauri.conf.json`, and three files disagreeing about it produces a binary that reports one version and installs another. `engines.node` is enforced by `.npmrc` in the next step.

- [ ] **Step 2: Pin the Node version pnpm will refuse to ignore**

Create `.npmrc`:

```
engine-strict=true
```

`svelte.config.ts` and `eslint.config.ts` are both loaded from disk at runtime — the first by `vite-plugin-svelte`'s bare `import()`, the second by ESLint's jiti transpiler — so both need Node to strip TypeScript types natively, which it does without a flag at 24. That is what the two `node-version: 24` pins in CI select, and it is why the version is pinned rather than floated. `engine-strict=true` is what turns "unsupported" into a failed install instead of a mystery at startup.

- [ ] **Step 3: Install and confirm the lockfile**

Run: `pnpm install`

Expected: `pnpm-lock.yaml` appears. If pnpm reports an unmet peer dependency, the offending pin is one of the four above; fix the version rather than reaching for `--force`. If pnpm refuses outright, your Node is not 24 — that is the `.npmrc` doing its job, not a broken pin.

- [ ] **Step 4: Create the Svelte and Vite configuration**

Create `svelte.config.ts`:

```ts
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import type { Config } from "svelte/compiler";

// `{ script: true }` is required: without it, TypeScript in <script> blocks is
// not processed. This is the documented requirement for a Vite SPA with no
// SvelteKit. The file is `.ts` rather than `.js` because a config module should
// be a config module, and `Config` gives the compiler something to check
// against.
const config: Config = {
	preprocess: vitePreprocess({ script: true }),
};

export default config;
```

`vite-plugin-svelte` probes `svelte.config.js`, then `svelte.config.ts`, then `.mjs`, then `.mts`, and loads the first one it finds. A bare `.ts` has no other candidate to shadow it, so the TypeScript types are real rather than a JSDoc approximation.

Create `vite.config.ts`:

```ts
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// Tauri serves the built assets from disk, so asset URLs must be relative.
export default defineConfig({
	plugins: [svelte()],
	base: "./",
	build: {
		outDir: "dist",
		emptyOutDir: true,
		target: "es2022",
	},
	server: {
		port: 5173,
		strictPort: true,
	},
	clearScreen: false,
});
```

Create `vitest.config.ts`:

```ts
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { svelteTesting } from "@testing-library/svelte/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
	plugins: [svelte(), svelteTesting()],
	test: {
		environment: "jsdom",
		setupFiles: ["./vitest.setup.ts"],
		include: ["src/**/*.test.ts"],
	},
});
```

Create `vitest.setup.ts`:

```ts
import "@testing-library/svelte/vitest";
```

- [ ] **Step 5: Create the TypeScript configuration**

Create `tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "types": ["vite/client", "node"],
    "strict": true,
    "noEmit": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "isolatedModules": true,
    "verbatimModuleSyntax": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": [
    "src/**/*.ts",
    "src/**/*.svelte",
    "svelte.config.ts",
    "vite.config.ts",
    "vitest.config.ts",
    "vitest.setup.ts",
    "eslint.config.ts"
  ]
}
```

`verbatimModuleSyntax` and `isolatedModules` are both required by Svelte's documented Vite setup, and `target` sits above the ES2015 floor for the same reason. `svelte.config.ts` and `eslint.config.ts` are in `include` so `svelte-check` type-checks them like any other module — a config module that the type checker never sees is a config module that breaks at runtime instead of in the gate. `types: ["node"]` is what lets `eslint.config.ts` use `node:url`.

- [ ] **Step 6: Create the ESLint and Prettier configuration**

Create `eslint.config.ts`:

```ts
import { includeIgnoreFile } from "@eslint/compat";
import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import svelte from "eslint-plugin-svelte";
import globals from "globals";
import { fileURLToPath } from "node:url";
import ts from "typescript-eslint";

const gitignorePath = fileURLToPath(new URL("./.gitignore", import.meta.url));

export default ts.config(
	includeIgnoreFile(gitignorePath),
	js.configs.recommended,
	...ts.configs.recommended,
	...svelte.configs.recommended,
	{
		languageOptions: {
			globals: { ...globals.browser, ...globals.node },
		},
	},
	{
		files: ["**/*.svelte"],
		languageOptions: {
			parserOptions: { parser: ts.parser },
		},
	},
	prettier,
);
```

Two details are load-bearing. `includeIgnoreFile(gitignorePath)` makes the lint scope and the git ignore scope the same list, so `target/`, `dist/`, `data/`, `logs/`, `build/`, and the packaged sidecar's `src-tauri/binaries/` are all excluded by one source of truth instead of a hand-maintained copy in two places that drifts. ESLint ignores `node_modules/` on its own, so nothing is lost by dropping the explicit `ignores` block. `eslint-config-prettier` comes **last**, because its only job is to switch off the stylistic rules Prettier already owns — putting it earlier lets a later config re-enable a rule the two tools will then disagree about.

ESLint 10 loads a TypeScript config through `jiti`, and refuses anything older than jiti `2.2.0`. That is why `jiti` is a devDependency and why `.npmrc` pins Node.

Create `.prettierrc.toml`:

```toml
useTabs = true
singleQuote = false
trailingComma = "all"
printWidth = 120
plugins = ["prettier-plugin-svelte"]

[[overrides]]
files = "*.svelte"

[overrides.options]
parser = "svelte"
```

TOML because Prettier reads it natively and because this repository prefers TOML for configuration. `useTabs` and `printWidth = 120` are the house style: tabs for indentation, a wider line budget than the Rust and Python budgets, double quotes, and trailing commas everywhere so a diff never moves a line because a value grew.

Create `.prettierignore`:

```
dist/
node_modules/
src-tauri/
coverage/
pnpm-lock.yaml
Cargo.lock
```

`pnpm format` runs Prettier over the whole repository, not just `src/`, so this list is what stops it reformatting Python, Markdown, and TOML. The Markdown exclusion is not cosmetic: Prettier's default `proseWrap: preserve` is safe, but the repo's convention of never hard-wrapping generated Markdown is easier to keep when the tool is not touching those files at all. Add these to the file now, not later:

```
docs/
*.md
sidecar/
uv.lock
```

- [ ] **Step 7: Create the design tokens, the HTML shell, and the entry point**

Create `index.html` at the repo root — Vite requires it, and Tauri points at the build output:

```html
<!doctype html>
<html lang="en">
	<head>
		<meta charset="utf-8" />
		<meta name="viewport" content="width=device-width, initial-scale=1" />
		<title>tidal2ytm</title>
	</head>
	<body>
		<div id="app"></div>
		<script type="module" src="/src/main.ts"></script>
	</body>
</html>
```

Create `src/styles/app.css`. The palette is the app's own: the TUI has no visual heritage to preserve, and the spec asks for dark mode only:

```css
:root {
	color-scheme: dark;
	--surface-0: #0f1115;
	--surface-1: #171a21;
	--surface-2: #1f232c;
	--border: #2b313d;
	--text-0: #e6e9ef;
	--text-1: #9aa3b2;
	--text-2: #6b7484;
	--accent: #6aa8ff;
	--ok: #57c78b;
	--warn: #e0b341;
	--bad: #e06c75;
	--radius: 6px;
	--space-1: 4px;
	--space-2: 8px;
	--space-3: 16px;
	--space-4: 24px;
	--font-ui: system-ui, "Segoe UI", sans-serif;
	--font-mono: "Cascadia Mono", "JetBrains Mono", ui-monospace, monospace;
}

* {
	box-sizing: border-box;
}

body {
	margin: 0;
	background: var(--surface-0);
	color: var(--text-0);
	font-family: var(--font-ui);
	font-size: 14px;
	line-height: 1.5;
}
```

Create `src/main.ts`:

```ts
import { mount } from "svelte";
import App from "./App.svelte";
import "./styles/app.css";

const target = document.getElementById("app");
if (!target) {
	throw new Error("index.html is missing the #app mount point");
}

export default mount(App, { target });
```

- [ ] **Step 8: Write the failing component test**

Create `src/lib/components/AuthStatus.test.ts`:

```ts
import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import AuthStatus from "./AuthStatus.svelte";

// A component that reports a signed-in state from the presence of an expiry
// string is the failure this file exists to catch. The sidecar sends
// `expires_at` for a cached token whether or not that token still works, so
// branching on the field's presence would report an expired session as a
// working one and send the user into a transfer that cannot authenticate.
describe("AuthStatus", () => {
	it("reports signed out when the token is expired even though an expiry is present", () => {
		render(AuthStatus, {
			props: { label: "Tidal", state: { valid: false, expires_at: "2020-01-01T00:00:00Z" } },
		});
		expect(screen.getByText("Not signed in")).toBeTruthy();
	});

	it("reports signed in for a valid token and shows the expiry", () => {
		render(AuthStatus, {
			props: { label: "Tidal", state: { valid: true, expires_at: "2099-01-01T00:00:00Z" } },
		});
		expect(screen.getByText("Signed in")).toBeTruthy();
		expect(screen.getByText(/2099-01-01/)).toBeTruthy();
	});

	it("reports signed in without an expiry when the provider reports none", () => {
		render(AuthStatus, {
			props: { label: "YouTube Music", state: { valid: true, expires_at: null } },
		});
		expect(screen.getByText("Signed in")).toBeTruthy();
	});

	it("names the provider it is reporting on", () => {
		render(AuthStatus, {
			props: { label: "Tidal", state: { valid: false, expires_at: null } },
		});
		expect(screen.getByText("Tidal")).toBeTruthy();
	});
});
```

- [ ] **Step 9: Run the test and confirm it fails**

Run: `pnpm test`

Expected: FAIL — `AuthStatus.svelte` does not exist.

- [ ] **Step 10: Write the types and the component**

Create `src/lib/types.ts`:

```ts
export interface ProviderState {
	valid: boolean;
	expires_at: string | null;
}

export interface AuthStatus {
	tidal: ProviderState;
	ytm: ProviderState;
}
```

Create `src/lib/components/AuthStatus.svelte`:

```svelte
<script lang="ts">
	import type { ProviderState } from "../types";

	interface Props {
		label: string;
		state: ProviderState;
	}

	let { label, state }: Props = $props();
</script>

<div class="row">
	<span class="label">{label}</span>
	<!--
		`state.valid` is the only thing that decides this. `expires_at` is present
		for a cached token whether or not that token still works, so branching on
		its presence would report an expired session as a working one.
	-->
	<span class="state" class:ok={state.valid} class:bad={!state.valid}>
		{state.valid ? "Signed in" : "Not signed in"}
	</span>
	{#if state.valid && state.expires_at}
		<span class="expiry">{state.expires_at}</span>
	{/if}
</div>

<style>
	.row {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
	}
	.label {
		color: var(--text-1);
		min-width: 10rem;
	}
	.state.ok {
		color: var(--ok);
	}
	.state.bad {
		color: var(--bad);
	}
	.expiry {
		color: var(--text-2);
		font-family: var(--font-mono);
		font-size: 12px;
	}
</style>
```

- [ ] **Step 11: Write the router**

Create `src/lib/router.ts`:

```ts
import { readable } from "svelte/store";

export type RouteId = "home" | "library" | "match" | "review" | "transfer" | "diagnostics";

export const ROUTES: readonly RouteId[] = [
	"home",
	"library",
	"match",
	"review",
	"transfer",
	"diagnostics",
];

const initial = (): RouteId => {
	const hash = window.location.hash.replace(/^#\/?/, "");
	return (ROUTES as readonly string[]).includes(hash) ? (hash as RouteId) : "home";
};

export const currentRoute = readable<RouteId>(initial(), (set) => {
	const onChange = (): void => set(initial());
	window.addEventListener("hashchange", onChange);
	return () => window.removeEventListener("hashchange", onChange);
});

export function navigate(route: RouteId): void {
	window.location.hash = `#/${route}`;
}
```

A hand-rolled hash router is right here: six fixed routes, no server, no nested layouts, no data-loading conventions. SvelteKit's file-based routing would add a dependency, a build plugin, and an adapter decision in exchange for nothing this app uses.

- [ ] **Step 12: Write the Home route**

Create `src/routes/Home.svelte`:

```svelte
<script lang="ts">
	import AuthStatus from "../lib/components/AuthStatus.svelte";
	import type { AuthStatus as AuthStatusData } from "../lib/types";

	// Task 9 replaces this with a real fetch. Until then the shell must render
	// something honest rather than a fabricated "signed in".
	let status: AuthStatusData | null = null;
</script>

<section>
	<h1>Home</h1>
	{#if status === null}
		<p class="placeholder">Provider status loads here.</p>
	{:else}
		<AuthStatus label="Tidal" state={status.tidal} />
		<AuthStatus label="YouTube Music" state={status.ytm} />
	{/if}
</section>

<style>
	h1 {
		font-size: 18px;
		font-weight: 600;
		margin: 0 0 var(--space-3);
	}
	.placeholder {
		color: var(--text-2);
	}
</style>
```

- [ ] **Step 13: Write the app shell**

Create `src/App.svelte`:

```svelte
<script lang="ts">
	import Home from "./routes/Home.svelte";
	import { ROUTES, currentRoute, navigate, type RouteId } from "./lib/router";

	const titles: Record<RouteId, string> = {
		home: "Home",
		library: "Library",
		match: "Match",
		review: "Review",
		transfer: "Transfer",
		diagnostics: "Diagnostics",
	};
</script>

<div class="shell">
	<nav aria-label="Sections">
		<span class="brand">tidal2ytm</span>
		<ul>
			{#each ROUTES as route (route)}
				<li>
					<button
						type="button"
						aria-current={$currentRoute === route ? "page" : undefined}
						onclick={() => navigate(route)}
					>
						{titles[route]}
					</button>
				</li>
			{/each}
		</ul>
	</nav>
	<main>
		<!--
			Only Home exists in Phase 2. The other five are placeholders on purpose:
			a stub screen that looks finished is worse than an obvious gap, and their
			real content lands in Phase 4.
		-->
		{#if $currentRoute === "home"}
			<Home />
		{:else}
			<section>
				<h1>{titles[$currentRoute]}</h1>
				<p class="placeholder">Not built yet.</p>
			</section>
		{/if}
	</main>
</div>

<style>
	.shell {
		display: grid;
		grid-template-columns: 200px 1fr;
		height: 100vh;
	}
	nav {
		background: var(--surface-1);
		border-right: 1px solid var(--border);
		padding: var(--space-3);
	}
	.brand {
		display: block;
		font-weight: 600;
		margin-bottom: var(--space-4);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	button {
		width: 100%;
		text-align: left;
		background: transparent;
		border: 0;
		border-radius: var(--radius);
		color: var(--text-1);
		cursor: pointer;
		font: inherit;
		padding: var(--space-1) var(--space-2);
	}
	button:hover {
		background: var(--surface-2);
	}
	button[aria-current="page"] {
		background: var(--surface-2);
		color: var(--text-0);
	}
	main {
		overflow: auto;
		padding: var(--space-4);
	}
	h1 {
		font-size: 18px;
		font-weight: 600;
		margin: 0 0 var(--space-3);
	}
	.placeholder {
		color: var(--text-2);
	}
</style>
```

- [ ] **Step 14: Run all three frontend gates**

Run, in this order:

```
pnpm format
pnpm lint:fix
pnpm lint
pnpm format:check
pnpm check
pnpm test
pnpm build
```

Expected: all clean, four passing component tests, and a `dist/index.html` on disk. Running `pnpm format` before the checks is deliberate: Prettier rewriting the files just written is the expected outcome, and a later `format:check` failure would mean the committed state drifted from what the formatter wants.

`useTabs = true` applies to JSON as well as to TypeScript, so this step's first `pnpm format` re-indents `package.json` and `tsconfig.json` with tabs. That is the configured style, not drift; commit what Prettier wrote.

- [ ] **Step 15: Confirm .gitignore covers the generated output**

Run: `git status --short`

Expected: `node_modules/` and `dist/` do not appear as untracked. Task 5's Step 16 added those rules; if they are missing, add them now:

```gitignore
node_modules/
.vite/
src-tauri/gen/
src-tauri/binaries/
sidecar/build/
sidecar/dist/
```

Do **not** ignore `index.html` — it is a source file.

- [ ] **Step 16: Commit**

```bash
git add package.json pnpm-lock.yaml .npmrc svelte.config.ts vite.config.ts vitest.config.ts vitest.setup.ts tsconfig.json eslint.config.ts .prettierrc.toml .prettierignore index.html src .gitignore
git commit -m "feat: add the Svelte 5 frontend toolchain, shell, and provider status card"
```

## Task 9: The Tauri host — the vertical slice

This is the first thing a user can actually run, and it completes the spec's Phase 2 exit criterion. The host owns the sidecar's lifecycle; the frontend owns presentation only.

`get_auth_status` is a custom `#[tauri::command]`, which needs no capability permission — only plugin commands do — so `capabilities/default.json` grants nothing but `core:default`. That is the point: with no shell plugin, the frontend has no way to spawn a process at all.

**Files:**
- Modify: `src-tauri/Cargo.toml`, `src-tauri/src/lib.rs`
- Create: `src-tauri/build.rs`, `src-tauri/tauri.conf.json`, `src-tauri/capabilities/default.json`, `src-tauri/src/main.rs`, `src-tauri/src/sidecar_path.rs`, `src-tauri/src/state.rs`, `src-tauri/src/commands.rs`, `src-tauri/icons/32x32.png`, `src-tauri/icons/128x128.png`, `src-tauri/icons/icon.ico`
- Create: `src/lib/ipc.ts`
- Modify: `src/routes/Home.svelte`

**Interfaces:**
- Consumes: `SidecarClient`, `SidecarConfig`, `log_level_env` from Task 6.
- Produces: Tauri command `get_auth_status() -> Result<AuthStatus, String>`, where `AuthStatus` is `{ tidal: ProviderState, ytm: ProviderState }` and `ProviderState` is `{ valid: bool, expires_at: Option<String> }` — serialised with snake_case field names so they match `src/lib/types.ts` exactly, with no `rename_all`. `src/lib/ipc.ts` exports `getAuthStatus(): Promise<AuthStatus>`. Env var `TIDAL2YTM_SIDECAR_BIN` overrides sidecar path resolution.

- [ ] **Step 1: Replace the host crate manifest**

Replace `src-tauri/Cargo.toml` with:

```toml
[package]
name = "tidal2ytm"
version.workspace = true
edition.workspace = true
rust-version.workspace = true
license.workspace = true
publish.workspace = true
description = "Tauri host for tidal2ytm. Owns the domain core and the sidecar."

[lib]
name = "tidal2ytm_host"
crate-type = ["staticlib", "cdylib", "rlib"]

[build-dependencies]
tauri-build = "2.7.1"

[dependencies]
tauri = "2.12.1"
serde.workspace = true
serde_json.workspace = true
tidal2ytm-core = { path = "../crates/tidal2ytm-core" }
tidal2ytm-ipc = { path = "../crates/tidal2ytm-ipc" }
tokio = { version = "1.53.1", features = ["rt-multi-thread", "sync", "time"] }
```

The `tauri` crate is pinned to `2.12.1` to match `@tauri-apps/cli` in `package.json`. The two are released in lockstep, and a caret on either one lets a routine `cargo update` drift them apart. The package is `tidal2ytm` rather than `tidal2ytm-host` so that the executable cargo produces is `tidal2ytm.exe`; no `mainBinaryName` override is set, and none should be. The library target stays `tidal2ytm_host` because a library target cannot share its package name.

- [ ] **Step 2: Create the build script**

Create `src-tauri/build.rs`:

```rust
fn main() {
    // The bundled sidecar's filename carries the Rust target triple, and
    // resolving it in dev needs the same value the bundler will have used.
    println!(
        "cargo:rustc-env=TIDAL2YTM_TARGET_TRIPLE={}",
        std::env::var("TARGET").unwrap_or_default()
    );
    tauri_build::build();
}
```

- [ ] **Step 3: Create the Tauri configuration**

Create `src-tauri/tauri.conf.json`:

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "tidal2ytm",
  "version": "0.7.0",
  "identifier": "dev.cobra.tidal2ytm",
  "build": {
    "beforeDevCommand": "pnpm dev",
    "devUrl": "http://localhost:5173",
    "beforeBuildCommand": "pnpm build",
    "frontendDist": "../dist"
  },
  "app": {
    "windows": [
      {
        "label": "main",
        "title": "tidal2ytm",
        "width": 1180,
        "height": 800,
        "minWidth": 900,
        "minHeight": 600
      }
    ],
    "security": {
      "csp": "default-src 'self'; style-src 'self' 'unsafe-inline'"
    }
  },
  "bundle": {
    "active": true,
    "targets": "all",
    "icon": ["icons/32x32.png", "icons/128x128.png", "icons/icon.ico"],
    "externalBin": ["binaries/tidal2ytm-sidecar"]
  }
}
```

`version` matches `pyproject.toml` and `package.json` at `0.7.0`; the spec's `1.0.0` arrives in Phase 6 with the CLI deletion. `externalBin` names the sidecar without a target triple: Tauri appends the triple when it looks for `binaries/tidal2ytm-sidecar-x86_64-pc-windows-msvc.exe`, which is the filename Task 11 produces. There is no `mainBinaryName` key, and there does not need to be one: the crate at `src-tauri/` is itself named `tidal2ytm`, so cargo produces `tidal2ytm.exe` on its own and Tauri renames nothing.

The CSP has no remote `connect-src` because the frontend makes no network requests. The host is the only thing that talks to Tidal or YouTube Music.

- [ ] **Step 4: Create the capability file**

Create `src-tauri/capabilities/default.json`:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Base capability. tidal2ytm grants no plugin permissions: the sidecar is spawned from Rust, and every command the frontend calls is a custom #[tauri::command] that needs no permission.",
  "windows": ["main"],
  "permissions": ["core:default"]
}
```

If this file causes a schema validation failure before `src-tauri/gen/schemas/` exists, delete the `$schema` line. It is editor assistance, not a build input.

- [ ] **Step 5: Create the sidecar path resolution**

Create `src-tauri/src/sidecar_path.rs`:

```rust
//! Locating the sidecar executable.
//!
//! The order is deliberate:
//!   1. `TIDAL2YTM_SIDECAR_BIN` — an explicit override, and the escape hatch if
//!      the bundled name turns out to differ from what is expected here.
//!   2. The resource directory, for a bundled app.
//!   3. `binaries/tidal2ytm-sidecar-<triple>`, for `tauri dev`.

use std::path::PathBuf;

use tauri::Manager;

const SIDECAR_BASE: &str = "tidal2ytm-sidecar";

/// The target triple the bundler uses for `externalBin` filenames.
const TARGET_TRIPLE: &str = env!("TIDAL2YTM_TARGET_TRIPLE");

/// Every place the sidecar might be, in the order it is tried.
///
/// Returned rather than picked so a failure message can list what was checked
/// instead of just saying "not found".
pub fn candidates(app: &tauri::AppHandle) -> Vec<PathBuf> {
    let mut out = Vec::new();

    if let Some(explicit) = std::env::var_os("TIDAL2YTM_SIDECAR_BIN") {
        out.push(PathBuf::from(explicit));
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        out.push(resource_dir.join(SIDECAR_BASE));
        out.push(resource_dir.join(format!("{SIDECAR_BASE}-{TARGET_TRIPLE}")));
    }

    if let Ok(exe) = std::env::current_exe() {
        // target/{profile}/app.exe -> target/{profile} -> target
        if let Some(target_dir) = exe.parent().and_then(std::path::Path::parent) {
            let suffix = std::env::consts::EXE_SUFFIX;
            out.push(
                target_dir
                    .join("binaries")
                    .join(format!("{SIDECAR_BASE}-{TARGET_TRIPLE}{suffix}")),
            );
        }
    }

    out
}

/// The first candidate that exists on disk.
pub fn resolve(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let tried = candidates(app);
    tried
        .iter()
        .find(|path| path.is_file())
        .cloned()
        .ok_or_else(|| {
            format!(
                "could not find the {SIDECAR_BASE} binary. Looked in: {}. \
                 Set TIDAL2YTM_SIDECAR_BIN to override.",
                tried
                    .iter()
                    .map(|p| p.display().to_string())
                    .collect::<Vec<_>>()
                    .join(", ")
            )
        })
}
```

Step 3 of Task 11 records which candidate actually wins in a bundled build. Until that has been run on Windows once, the override is the supported path and `resolve` returning a clear error is the correct behaviour, not a gap.

- [ ] **Step 6: Create the application state**

Create `src-tauri/src/state.rs`:

```rust
//! Process-wide state the Tauri commands read.

use std::sync::Arc;

use serde::Deserialize;
use tidal2ytm_ipc::SidecarClient;

/// Whether one provider has a usable cached token.
///
/// Field names are snake_case on purpose: they land in the frontend verbatim
/// and must match `src/lib/types.ts` without a rename layer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct ProviderState {
    pub valid: bool,
    pub expires_at: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct AuthStatus {
    pub tidal: ProviderState,
    pub ytm: ProviderState,
}

pub struct AppState {
    pub client: Arc<SidecarClient>,
}
```

- [ ] **Step 7: Create the commands**

Create `src-tauri/src/commands.rs`:

```rust
//! The Tauri commands the frontend can call.
//!
//! Each one is a thin translation: a Tauri call becomes an IPC call, and the
//! result is either a plain value or a string the UI can show. No business
//! rule lives here — that is what the domain core is for.

use std::time::Duration;

use serde_json::json;
use tauri::State;

use crate::state::{AppState, AuthStatus};

/// The spec's per-op timeouts. `auth.status` is a local file read, so it gets
/// a tight bound; the 30 s and 15 s figures apply to network ops in later
/// phases and are recorded here so the vocabulary exists in one place.
const AUTH_STATUS_TIMEOUT: Duration = Duration::from_secs(5);

#[tauri::command]
pub async fn get_auth_status(state: State<'_, AppState>) -> Result<AuthStatus, String> {
    let data = state
        .client
        .call("auth.status", json!({}), Some(AUTH_STATUS_TIMEOUT))
        .await
        .map_err(|e| e.to_string())?;
    serde_json::from_value::<AuthStatus>(data)
        .map_err(|e| format!("the sidecar returned an auth status this build cannot read: {e}"))
}
```

The deserialisation error names the shape mismatch rather than surfacing a raw serde message, because the frontend shows this string to a person and a serde backtrace is not an explanation.

- [ ] **Step 8: Write the host library entry point**

Replace `src-tauri/src/lib.rs` with:

```rust
//! The Tauri host: it owns the domain core, the database, and the sidecar's
//! lifetime. The frontend gets commands and nothing else.

pub mod commands;
pub mod sidecar_path;
pub mod state;

use std::sync::Arc;
use std::time::Duration;

use tauri::Manager;
use tidal2ytm_ipc::{SidecarClient, SidecarConfig, log_level_env};

use state::AppState;

const STARTUP_TIMEOUT: Duration = Duration::from_secs(20);

/// Start the sidecar, supervise it, and expose the commands.
pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let program = sidecar_path::resolve(app.handle())?;
            // Phase 4 owns the host's log file, so Phase 2 discards the
            // canonical name and passes the value straight through.
            let (_, level_value) = log_level_env("INFO").map_err(|e| e.to_string())?;
            let config = SidecarConfig {
                program,
                args: vec!["--serve".to_string()],
                cwd: app.path().app_data_dir()?,
                env: vec![("TIDAL2YTM_LOG_LEVEL".to_string(), level_value)],
                handshake_timeout: STARTUP_TIMEOUT,
            };
            let client =
                tauri::async_runtime::block_on(SidecarClient::spawn(&config))
                    .map_err(|e| e.to_string())?;
            app.manage(AppState {
                client: Arc::new(client),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![commands::get_auth_status])
        .build(tauri::generate_context!())
        .expect("the Tauri application builds");

    // Shutdown belongs on `RunEvent::Exit`, not on `WindowEvent::Destroyed`:
    // the event loop is ending, so blocking here costs nothing, whereas
    // blocking inside a window callback stalls the loop that is still
    // delivering events.
    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(state) = handle.try_state::<AppState>() {
                let client = Arc::clone(&state.client);
                tauri::async_runtime::block_on(client.shutdown());
            }
        }
    });
}
```

`cwd` is the app data directory, which is where the sidecar's token cache lives — that is the "logs and data live in the platform app data dir" rule applied to the process's working directory, and it keeps a crash report pointing somewhere meaningful.

`tauri::async_runtime::block_on` in `setup` is the one construct here with a version-sensitivity risk: it runs before the event loop exists. If a future Tauri release makes that deadlock, the fix is to hold the client in a `tokio::sync::OnceCell`, spawn the sidecar with `tauri::async_runtime::spawn` instead, and have `get_auth_status` await the cell. Do not work around it by dropping the handshake — the handshake is the thing that makes a stale sidecar a clear startup error rather than a mysterious job failure.

- [ ] **Step 9: Create the binary entry point**

Create `src-tauri/src/main.rs`:

```rust
// Hide the console window on Windows release builds; debug keeps it so the
// sidecar's forwarded stderr is visible while developing.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tidal2ytm_host::run();
}
```

- [ ] **Step 10: Generate placeholder icons**

Tauri refuses to build without icons. Generate a single-colour 32×32 and 128×128 PNG plus an `.ico` with any tool at hand, or download the default set from `tauri-apps/tauri` (`icons/32x32.png`, `icons/128x128.png`, `icon.ico`). Place them in `src-tauri/icons/`. They are placeholders and get real artwork before the Phase 7 installer.

- [ ] **Step 11: Create the frontend IPC wrapper**

Create `src/lib/ipc.ts`:

```ts
import { invoke } from "@tauri-apps/api/core";
import type { AuthStatus } from "./types";

/**
 * Ask the host whether each provider has a usable cached token.
 *
 * Credentials never cross this boundary: the sidecar reports booleans and
 * expiry timestamps, and the host forwards them unchanged.
 */
export async function getAuthStatus(): Promise<AuthStatus> {
	return invoke<AuthStatus>("get_auth_status");
}
```

- [ ] **Step 12: Wire Home to the real command**

Replace `src/routes/Home.svelte` with:

```svelte
<script lang="ts">
	import { onMount } from "svelte";
	import AuthStatus from "../lib/components/AuthStatus.svelte";
	import { getAuthStatus } from "../lib/ipc";
	import type { AuthStatus as AuthStatusData } from "../lib/types";

	let status: AuthStatusData | null = null;
	let failure: string | null = null;
	let loading = true;

	onMount(async () => {
		try {
			status = await getAuthStatus();
		} catch (e) {
			// A traceback is not an explanation. The host's message is written to be
			// shown to a person; anything else gets a generic line and the detail
			// goes to the log file the Diagnostics screen will open.
			failure = e instanceof Error ? e.message : "Could not read provider status.";
		} finally {
			loading = false;
		}
	});
</script>

<section>
	<h1>Home</h1>

	<!--
		Three states, not two. "Not loaded yet", "loaded and both signed out", and
		"the sidecar is not running" look identical if collapse them into a single
		empty branch, and the fix for each is different.
	-->
	{#if loading}
		<p class="placeholder">Checking provider sign-in…</p>
	{:else if failure !== null}
		<p class="failure" role="alert">{failure}</p>
	{:else if status !== null}
		<div class="providers">
			<AuthStatus label="Tidal" state={status.tidal} />
			<AuthStatus label="YouTube Music" state={status.ytm} />
		</div>
	{/if}
</section>

<style>
	h1 {
		font-size: 18px;
		font-weight: 600;
		margin: 0 0 var(--space-3);
	}
	.placeholder {
		color: var(--text-2);
	}
	.failure {
		color: var(--bad);
	}
	.providers {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
</style>
```

- [ ] **Step 13: Run the frontend gates again**

Run:

```
pnpm format
pnpm check
pnpm test
pnpm build
```

Expected: clean, four passing tests, and a fresh `dist/`.

- [ ] **Step 14: Compile the host**

Run:

```bash
cargo check -p tidal2ytm
```

Expected: clean. This is the one command in the whole plan that compiles Tauri, which is why it is not in the default members.

- [ ] **Step 15: Build the desktop binary without bundling**

Run: `pnpm tauri build --no-bundle`

Expected: `src-tauri/target/release/tidal2ytm.exe` exists. Bundling is deferred to Task 11; this step only proves the host compiles, the frontend is embedded, and `generate_context!` accepts the config.

If the build fails because `src-tauri/binaries/tidal2ytm-sidecar-*` is missing, that is expected until Task 11. Create an empty placeholder file at the expected path to get past the check, and delete it before committing — it is not a deliverable:

```powershell
New-Item -ItemType Directory -Force -Path src-tauri\binaries | Out-Null
"placeholder" | Set-Content -NoNewline src-tauri\binaries\tidal2ytm-sidecar-x86_64-pc-windows-msvc.exe
```

- [ ] **Step 16: Run the host by hand**

Run: `src-tauri\target\release\tidal2ytm.exe`

Expected: a window opens showing "Checking provider sign-in…" and then two "Not signed in" rows, because no token cache exists yet. Confirm the sidecar process appears in Task Manager while the window is open, and that it exits when the window closes.

This is the human half of the spec's Phase 2 exit criterion, and it is also the step that reveals the real bundled-resource name for `sidecar_path::resolve`. If the window shows the resolution error, note which candidate path the error message lists and record it in the task's commit message; Task 11's Step 4 depends on knowing it.

- [ ] **Step 17: Run every gate**

Run, in order:

```
cargo fmt --all --check
cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc --all-targets -- -D warnings
cargo clippy -p tidal2ytm --all-targets -- -D warnings
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
pnpm lint
pnpm format:check
pnpm check
pnpm test
```

Expected: all twelve clean.

- [ ] **Step 18: Commit**

```bash
git add src-tauri src .gitignore
git commit -m "feat: add the Tauri host and render provider status in the window"
```

## Task 10: CI jobs, git hooks, and dependency upkeep

The existing Python job keeps its exact steps and gains nothing. Two new path-filtered jobs go alongside it so a change to `crates/` does not pay for a Node toolchain and a change to `src/` does not pay for a Rust one. A third job covers the host, which is the only crate that needs `libwebkit2gtk` to type-check, and installing that on a runner costs a minute — cheap enough to pay only when `src-tauri/` actually changed.

This task also closes the loop on dependency upkeep. Three ecosystems now have lockfiles, each with its own update command and its own failure mode, and an unpinned frontend is a frontend that breaks on someone else's release schedule. The division of labour is: Dependabot opens security pull requests, a human does everything else, and the procedure for that is written down in `AGENTS.md` rather than remembered.

**Files:**
- Modify: `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `AGENTS.md`
- Create: `.github/dependabot.yml`

**Interfaces:**
- Consumes: `cargo test -p tidal2ytm-core -p tidal2ytm-ipc`, `cargo clippy`, `cargo fmt --all --check`, `TIDAL2YTM_SIDECAR_PYTHON`, `TIDAL2YTM_LOG_LEVEL`; `pnpm check`, `pnpm lint`, `pnpm format:check`, `pnpm test`.
- Produces: three new CI jobs named `rust`, `host`, and `frontend`. Two new pre-commit hooks (`prettier`, `eslint`) and three new pre-push hooks (`cargo-fmt`, `cargo-clippy`, `cargo-test`). A `.github/dependabot.yml` whose only entries are `security-updates`. An `AGENTS.md` section titled "Dependency updates".

- [ ] **Step 1: Add the three jobs**

Append to `.github/workflows/ci.yml`, keeping the existing `check` job byte for byte:

```yaml
  rust:
    name: rust
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: rustfmt, clippy
      - uses: Swatinem/rust-cache@v2
        with:
          workspaces: ./crates/tidal2ytm-core,./crates/tidal2ytm-ipc
      - run: uv sync --all-groups --frozen
      - name: locate the sidecar interpreter
        run: echo "TIDAL2YTM_SIDECAR_PYTHON=$(uv run python -c 'import sys; print(sys.executable)')" >> "$GITHUB_ENV"
      - run: cargo fmt --all --check
      - run: cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc --all-targets -- -D warnings
      - run: cargo test -p tidal2ytm-core -p tidal2ytm-ipc

  host:
    name: host
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: rustfmt, clippy
      - uses: Swatinem/rust-cache@v2
        with:
          workspaces: ./src-tauri
      - name: install the Tauri Linux prerequisites
        run: |
          sudo apt-get update
          sudo apt-get install -y libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v5
        with:
          node-version: 24
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - run: pnpm build
      - run: cargo clippy -p tidal2ytm --all-targets -- -D warnings

  frontend:
    name: frontend
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: .
    steps:
      - uses: actions/checkout@v5
      - uses: pnpm/action-setup@v4
        with:
          version: 11.28.2
      - uses: actions/setup-node@v5
        with:
          node-version: 24
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - run: pnpm format:check
      - run: pnpm lint
      - run: pnpm check
      - run: pnpm test
      - run: pnpm build
```

- [ ] **Step 2: Add the path filters**

Job-level `paths` is what keeps a Python-only commit from paying for two toolchains. Add to each new job, immediately after `runs-on: ubuntu-latest`:

```yaml
    paths:
      - 'Cargo.toml'
      - 'Cargo.lock'
      - 'contracts/**'
```

for `rust`; and

```yaml
    paths:
      - 'src-tauri/**'
      - 'Cargo.toml'
      - 'Cargo.lock'
      - 'package.json'
      - 'pnpm-lock.yaml'
      - 'src/**'
      - 'index.html'
```

for `host`; and

```yaml
    paths:
      - 'package.json'
      - 'pnpm-lock.yaml'
      - '.npmrc'
      - 'svelte.config.ts'
      - 'vite.config.ts'
      - 'vitest.config.ts'
      - 'vitest.setup.ts'
      - 'tsconfig.json'
      - 'eslint.config.ts'
      - '.prettierrc.toml'
      - '.prettierignore'
      - 'index.html'
      - 'src/**'
```

for `frontend`.

`.npmrc` is in the filter because it is what makes `pnpm install` fail on the wrong Node, and a config change that only breaks CI when someone else's machine has a different Node is exactly the kind of change the filter exists to catch.

Leave the `check` job with no `paths` filter. It must run for any non-markdown change, including `sidecar/**`, because the sidecar is Python and the existing gates cover it.

Both `node-version: 24` pins in the `host` and `frontend` jobs are load-bearing, not housekeeping. `svelte.config.ts` is loaded by a bare `import()` of a `.ts` path and `eslint.config.ts` by ESLint's jiti transpiler, so both need Node's native type stripping. A job that quietly moved to Node 20 would fail with a module-resolution error that reads like a missing dependency.

- [ ] **Step 3: Validate the workflow syntax**

Run:

```bash
uv run python -c "import pathlib, tomllib; print(sorted(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['tool']['uv']['workspace']['members']))"
```

Expected: `['sidecar']`, confirming the sidecar is a workspace member and therefore covered by the `check` job's `uv sync`.

Then open `.github/workflows/ci.yml` and confirm it is valid YAML with the three new jobs nested under `jobs:` at the same indentation as `check:`. A malformed workflow fails silently in some editors and loudly only in CI, so read it back after editing.

- [ ] **Step 4: Extend the pre-commit hooks**

Add to the `local` repo block in `.pre-commit-config.yaml`, before the `pyright` hook:

```yaml
      - id: prettier
        name: prettier
        entry: pnpm format
        language: system
        files: '^(src/.*|index\.html|svelte\.config\.ts|vite\.config\.ts|vitest\.config\.ts|vitest\.setup\.ts|tsconfig\.json|eslint\.config\.ts|package\.json|\.prettierrc\.toml)$'
        pass_filenames: false
      - id: eslint-fix
        name: eslint
        entry: pnpm lint:fix
        language: system
        files: '^src/.*\.(ts|svelte)$'
        pass_filenames: false
        require_serial: true
```

Both are formatters or `--fix` linters, so they rewrite and let the commit proceed. `pass_filenames: false` is required because the pnpm scripts take no file arguments; a formatter that rewrites files mid-commit is the documented behaviour, not a fault.

The `files` patterns are triggers only, not arguments. Note that `types_or: [javascript, typescript]` would be wrong here: pre-commit-identify does not classify `.svelte` under either name, so a `types_or` filter would silently exclude every Svelte component from formatting — a gate that looks installed and never runs.

- [ ] **Step 5: Extend the pre-push hooks**

Add to the same `local` block:

```yaml
      - id: cargo-fmt
        name: cargo fmt
        entry: cargo fmt --all
        language: system
        types: [rust]
        stages: [pre-push]
        pass_filenames: false
      - id: cargo-clippy
        name: cargo clippy
        entry: cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc --all-targets --fix --allow-dirty -- -D warnings
        language: system
        types: [rust]
        stages: [pre-push]
        pass_filenames: false
      - id: cargo-test
        name: cargo test
        entry: cargo test -p tidal2ytm-core -p tidal2ytm-ipc
        language: system
        types: [rust]
        stages: [pre-push]
        pass_filenames: false
```

`--fix --allow-dirty` is what lets clippy apply its own suggestions and continue, matching the spec's tool policy. CI keeps `-D warnings` as the backstop, so a hook that fails to fix something still fails the run.

Note the hook set is *extended*, not replaced. The `pytest-push` hook already runs `uv run pytest -q -x --cov --cov-fail-under=80`, and `testpaths` now covers `sidecar/tests`, so the sidecar is gated without any change to that hook.

- [ ] **Step 6: Turn on security-only Dependabot**

Create `.github/dependabot.yml`:

```yaml
version: 2

updates:
  # Security updates only. Version updates are driven by hand — see the
  # "Dependency updates" section in AGENTS.md for the procedure and for why
  # each ecosystem is treated separately.
  - package-ecosystem: npm
    directory: /
    applies-to: security-updates
    schedule:
      interval: daily

  - package-ecosystem: cargo
    directory: /
    applies-to: security-updates
    schedule:
      interval: daily

  - package-ecosystem: uv
    directory: /
    applies-to: security-updates
    schedule:
      interval: daily
```

`applies-to: security-updates` is the whole point. Without it Dependabot would open a steady stream of routine version pull requests against three lockfiles, and a maintainer who wants to update deliberately would spend their attention triaging noise instead of reading the diff. With it, the only pull requests that arrive uninvited are the ones that matter, and they bypass Dependabot's default release cooldown so an actively exploited dependency is patched immediately.

Two caveats worth knowing rather than discovering. The `uv` ecosystem is comparatively new in Dependabot — astral-sh/uv#2512 tracks the known gaps — so a failed uv run is a Dependabot problem, not a broken `pyproject.toml`; check the repository's Dependabot run log before touching the lockfile. And `security-updates` entries still propose version bumps inside the range the manifest allows, so a lockfile that has drifted from its manifest will surface here first.

Validate the file:

```bash
uv run python -c "import pathlib, yaml; yaml.safe_load(pathlib.Path('.github/dependabot.yml').read_text()); print('valid')"
```

Expected: `valid`. If `yaml` is not importable in the project environment, that assertion step is skippable — read the file back instead. Do not add PyYAML as a project dependency to check a twelve-line configuration file.

- [ ] **Step 7: Document the manual update procedure**

Add a section to `AGENTS.md`, placed after the "Quality gates" section and before "Versioning", titled "Dependency updates":

```markdown
## Dependency updates

Three ecosystems, three lockfiles, three commands. Dependabot opens security pull requests and nothing else — see `.github/dependabot.yml`. Everything else is deliberate, one ecosystem at a time, because a bulk upgrade that breaks CI is indistinguishable from a bulk upgrade that found a real incompatibility.

| Ecosystem | Command | Updates |
| --- | --- | --- |
| Python | `uv lock --upgrade` | `uv.lock` (from the declared ranges in `pyproject.toml`) |
| Frontend | `pnpm update --latest` | `pnpm-lock.yaml`, and the exact pins in `package.json` |
| Rust | `cargo update` | `Cargo.lock`, and the exact pins in `Cargo.toml` |

Every one of these is followed by the full gate set and a single-purpose commit. Bump one ecosystem per commit; a three-ecosystem diff is unreviewable.

Two pins are not "latest wins". The `tauri` crate and `@tauri-apps/cli` are released in lockstep and must always match, so they move together. `typescript` is pinned to `6.0.3` because that is the ceiling, not a floor: it is the last pure-JS TypeScript, and both `svelte-check` and `typescript-eslint` drive TypeScript through the JS compiler API that the native TypeScript 7 does not ship. When a release lifts those peer ranges to include 7, take it deliberately, with `svelte-check` and `typescript-eslint` in the same commit — never as a side effect of `pnpm update --latest`.

After any bump, re-read the peer constraints rather than trusting the resolver: `pnpm install` failing on a peer conflict means the answer is a different version, not `--force`.
```

Then confirm the section landed:

```bash
rg -n "^## Dependency updates" AGENTS.md
```

Expected: one match.

- [ ] **Step 8: Run the extended hook set by hand**

Run:

```bash
uv run pre-commit run --all-files
```

Expected: all hooks pass, and `git status --short` shows any formatter rewrites the hooks made.

- [ ] **Step 9: Commit**

```bash
git add .github/workflows/ci.yml .pre-commit-config.yaml .github/dependabot.yml AGENTS.md
git commit -m "ci: add path-filtered gates, security-only Dependabot, and the update procedure"
```

## Task 11: Packaging the sidecar

The spec validates packaging by generation only: the question is whether the package builds, and the real proof is a human running the built `.exe` on Windows. This task produces the PyInstaller spec, wires the result into `bundle.externalBin`, and confirms the build completes.

**Files:**
- Create: `sidecar/tidal2ytm-sidecar.spec`, `sidecar/build_sidecar.ps1`
- Modify: `.gitignore`, `README.md`

**Interfaces:**
- Consumes: `bundle.externalBin: ["binaries/tidal2ytm-sidecar"]` from Task 9, which resolves to `src-tauri/binaries/tidal2ytm-sidecar-<target-triple><exe-suffix>`.
- Produces: `src-tauri/binaries/tidal2ytm-sidecar-x86_64-pc-windows-msvc.exe`, git-ignored. `TIDAL2YTM_SIDECAR_BIN` remains the override for a hand-built binary.

- [ ] **Step 1: Write the PyInstaller spec**

Create `sidecar/tidal2ytm-sidecar.spec`:

```python
# PyInstaller spec for the tidal2ytm sidecar.
#
# Run through: uv run --with pyinstaller pyinstaller sidecar/tidal2ytm-sidecar.spec
#
# The output filename carries no target triple; the copy step in
# build_sidecar.ps1 adds it, because that is what Tauri looks for.
"""PyInstaller build for the tidal2ytm sidecar."""

from pathlib import Path

# Analysis, with the repo root as the import path so `tidal2ytm` (the shared
# library) and `tidal2ytm_sidecar` both resolve.
analysis = Analysis(  # noqa: F821 - injected by PyInstaller
    ["sidecar/tidal2ytm_sidecar/__main__.py"],
    pathex=[str(Path(SPECPATH).resolve())],
    binaries=[],
    datas=[],
    hiddenimports=[
        "tidal2ytm.auth",
        "tidal2ytm.logging_setup",
        "tidal2ytm.paths",
        "tidalapi",
        "ytmusicapi",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The sidecar never touches a terminal UI, and PyInstaller cannot see
        # that these are dead because the CLI imports them behind a flag.
        "rich",
        "readchar",
        "tkinter",
        "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)  # noqa: F821
executable = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="tidal2ytm-sidecar",
    console=True,
    upx=False,
)
collection = COLLECT(  # noqa: F821
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="tidal2ytm-sidecar",
)
```

`console=True` is deliberate for Phase 2: the sidecar's stderr has to reach a terminal while the boundary is being proven. Phase 7 can revisit it once the host writes its own log file.

- [ ] **Step 2: Write the build script**

Create `sidecar/build_sidecar.ps1`:

```powershell
# Build the sidecar and stage it where Tauri expects to find it.
#
# Usage: pwsh sidecar/build_sidecar.ps1
$ErrorActionPreference = 'Stop'

$repo = Resolve-Path (Join-Path $PSScriptRoot '..')
$triple = 'x86_64-pc-windows-msvc'
$target = Join-Path $repo "src-tauri\binaries"
$built = Join-Path $repo 'sidecar\dist\tidal2ytm-sidecar\tidal2ytm-sidecar.exe'

Push-Location $repo
try {
    uv run --with pyinstaller pyinstaller sidecar\tidal2ytm-sidecar.spec --noconfirm
}
finally {
    Pop-Location
}

if (-not (Test-Path $built)) {
    throw "PyInstaller did not produce $built"
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Force $built (Join-Path $target "tidal2ytm-sidecar-$triple.exe")
Write-Host "staged $target\tidal2ytm-sidecar-$triple.exe"
```

- [ ] **Step 3: Un-ignore the spec file**

`.gitignore` line 55 has `*.spec`, which swallows `sidecar/tidal2ytm-sidecar.spec`. Append after that line's block, at the end of the file:

```gitignore
# PyInstaller spec for the sidecar is source, not a build artefact.
!sidecar/tidal2ytm-sidecar.spec
```

Verify with `git check-ignore -v sidecar/tidal2ytm-sidecar.spec` — it must print nothing, or print a `!` negation rather than a match.

- [ ] **Step 4: Build the sidecar and stage it**

Run: `pwsh sidecar/build_sidecar.ps1`

Expected: `src-tauri/binaries/tidal2ytm-sidecar-x86_64-pc-windows-msvc.exe` exists.

- [ ] **Step 5: Build the packaged app**

Run: `pnpm tauri build`

Expected: an installer and a portable executable under `src-tauri/target/release/bundle/`. This is the generation check the spec asks for; it is not a claim that the app works, which Step 6 establishes.

- [ ] **Step 6: Run the packaged app**

Run the produced executable from the bundle directory.

Expected: the window opens, the sidecar starts, and two "Not signed in" rows render. If the window reports that the sidecar could not be found, read the candidate list in the error message: it names every path that was tried. Add whichever path was missing as another candidate in `sidecar_path::candidates`, and record the correction in this task's commit message so the next person does not rediscover it.

- [ ] **Step 7: Document the build in the README**

Add a section to `README.md` describing the desktop app's build, after the existing install section. Use a four-backtick fence so the nested three-backtick blocks stay intact:

````markdown
### Building the desktop app

The GUI lives in this branch and is built with [Tauri 2](https://tauri.app). It needs Rust, Node 24, pnpm, and uv.

```bash
uv sync --all-groups
pnpm install
pwsh sidecar/build_sidecar.ps1   # Windows; builds the Python sidecar
pnpm tauri build
```

During development, `pnpm tauri dev` starts the window and a Vite dev server. The sidecar is spawned by the Rust host, never by the frontend.

The Python CLI is still present and is still the supported interface for anything not yet moved into the GUI. Phase 6 of `docs/superpowers/specs/2026-09-29-desktop-gui-migration-design.md` removes it.
````

- [ ] **Step 8: Run every gate one last time**

Run:

```
cargo fmt --all --check
cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc --all-targets -- -D warnings
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
pnpm lint
pnpm format:check
pnpm check
pnpm test
```

Expected: all ten clean. `cargo clippy -p tidal2ytm` is covered by the `host` CI job, not here, because it needs the Linux webkit packages on a runner and works fine on Windows.

- [ ] **Step 9: Commit**

```bash
git add sidecar/tidal2ytm-sidecar.spec sidecar/build_sidecar.ps1 .gitignore README.md
git commit -m "build: package the Python sidecar with PyInstaller and stage it for Tauri"
```

---

## What this plan does not cover

Phases 3 through 7 of the spec are deliberately absent. Each gets its own spec-to-plan cycle once Phase 2 has proven the boundary, because the thing Phase 2 proves — that a Rust host can drive a Python sidecar over NDJSON and that a Tauri window can show the result — is exactly the thing the rest of the plan is built on. If that proof fails, the topology is the first thing to revisit, and 800 lines of ported logic should not already be sitting on top of it.

Phase 3 (schema and ported domain logic) is the natural next plan.
