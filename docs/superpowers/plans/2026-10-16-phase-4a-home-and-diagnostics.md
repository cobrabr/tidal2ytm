# Phase 4a Home and Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first usable slice of the desktop GUI: one data directory, sign-in from the application, a Tidal favourites sync behind a job, and Home and Diagnostics as real screens.

**Architecture:** The host gains one data directory it names explicitly to the sidecar, a job manager that drives one job kind (`sync`), a log file, and three narrow Tauri plugin permissions. The sidecar gains a concurrent read loop, three operations (`auth.status` reworked, `auth.begin`, `favorites.list`), and its own Tidal refresh helper, because `tidalapi` has none that safely rewrites the token file. The store gains one write that touches only Tidal columns, so a re-sync cannot blank a match.

**Tech Stack:** Rust (tauri 2.12.1 host, three Tauri-free library crates), Python 3.12 (`uv` workspace member `tidal2ytm_sidecar`), Svelte 5 + TypeScript 6.0.3 + Vite, SQLite via `rusqlite` 0.40.2 bundled.

**Spec:** `docs/superpowers/specs/2026-10-14-phase-4a-home-and-diagnostics-design.md` — the plan argues from the spec, so the spec travels with it; executors read both.

## Global Constraints

- Tauri crate and `@tauri-apps/cli` are `2.12.1` and must move together. Every `@tauri-apps/*` and `tauri-plugin-*` version added in this plan is pinned exactly, with no `^` or `~`; a range in `package.json` is how a release someone else published becomes this project's outage.
- Python coexists until Phase 6. `ruff check`, `ruff format --check`, `pyright` and `pytest --cov --cov-fail-under=80` stay green and stay in `ci.yml`.
- All twelve gates must pass before any push. The Python gates are among them.
- `git diff` always uses `--no-ext-diff`.
- Never `cd`, `Set-Location` or `git -C` to the working directory.
- Nothing is posted, sent or published externally without showing the exact content and getting approval.
- Every new Tauri command is registered in `tauri::generate_handler!` in `src-tauri/src/lib.rs`. A command that is not registered is unreachable, and the frontend's `invoke` rejection with nothing to show is the only symptom.
- The `contracts/` files are authoritative for wire shape and both the Python and the Rust suites assert against them. `src/lib/types.ts` derives its payload types from `contracts/auth_status.json`; the recorded sample stays the **signed-out** one, because `Widen` turns a `null` literal into `string | null` and a signed-in sample would silently narrow that field.
- `src-tauri/capabilities/default.json` grants `core:default` and no plugin permissions. This plan adds three, each named in the task that needs it. No wildcard, and no plugin permission outside the three.
- Redaction is a recursive key-pattern denylist applied on both the Rust and Python sides; no new log record may carry a credential, an authorization header, or a raw error string that could contain a request URL.
- Tests use fictional artist and track names and synthetic ids only. No real catalogue data, and no real Tidal or YouTube Music calls in any test that runs in CI.

## Review Focus

The spec states what the software must do; its silence about an input is not permission for that input to break the program. These five are the ones most likely to bite a real person, and each has its test named in the task that owns the code.

1. **The machine has never run the CLI.** No token files, no Google credential, an empty database. The whole path to a working application runs through first-run: both providers signed out, the YouTube Music sign-in button gated behind an import that has never been done.
2. **The chosen `client_secret_*.json` is the wrong kind.** A Desktop-app credential imports cleanly — it is a JSON object with the right keys — and then Google rejects it. The failure must name the credential type, not say "sign-in failed".
3. **The Tidal session lapses part-way through a sync.** The mirror is partial, the job fails, and the next run must complete it. Nothing may be lost and the screen must not claim the mirror is finished.
4. **The sidecar is killed part-way through a sync and the application is closed.** On the next launch, Home must show *interrupted*, with a re-run offered — read from `meta`, because the answer outlived the process that could have remembered it.
5. **A favourited track has no album.** It must become one visible `Unknown album` row for its artist, not a dropped track and not a foreign-key failure that aborts the page.

## File Structure

**Created**

| Path | Responsibility |
| --- | --- |
| `src-tauri/src/logging.rs` (grown) | Host log file sink, 5 MB × 3 rotation, level read before the sink opens. |
| `src-tauri/src/jobs.rs` | The job manager: one job kind now, three-shaped from the start. Progress channel, cooperative cancel. |
| `src-tauri/src/sync.rs` | The `sync` job body. Pages `favorites.list`, upserts each page, owns the `meta` key. |
| `src/routes/Diagnostics.svelte` | Diagnostics screen. |
| `src/lib/components/ProviderRow.svelte` | One provider's three states and its sign-in action. |
| `src/lib/components/JobProgress.svelte` | Job state, `done`, optional `total`, stop control. |
| `src/lib/jobs.ts` | Frontend job subscription over the Tauri event channel. |
| `src/lib/ui-log.ts` | Frontend log writer, capped, under the app data `logs/` directory. |
| `sidecar/tidal2ytm_sidecar/refresh.py` | The Tidal refresh-and-rewrite helper. |

**Modified**

| Path | Change |
| --- | --- |
| `tidal2ytm/paths.py` | `DATA_DIR` honours `TIDAL2YTM_DATA_DIR`. |
| `tidal2ytm/auth.py` | One credential check that raises instead of exiting; three-state cache checks. |
| `tidal2ytm/tidal_source.py` | Paging split so one page can be fetched at a time. |
| `sidecar/tidal2ytm_sidecar/runner.py` | Concurrent dispatch, locked writes. |
| `sidecar/tidal2ytm_sidecar/protocol.py` | Capability list and dispatch table grow. |
| `sidecar/tidal2ytm_sidecar/ops.py` | Three states, client-secret flag, `auth.begin`, `favorites.list`. |
| `contracts/auth_status.json` | New `auth.status` sample. |
| `contracts/favorites_list.json` | New envelope. |
| `crates/tidal2ytm-ipc/src/protocol.rs` | `REQUIRED_CAPABILITIES` grows. |
| `crates/tidal2ytm-store/src/store.rs` | `upsert_track_source`. |
| `src-tauri/src/lib.rs` | Commands registered; data directory and env wired; plugins initialised. |
| `src-tauri/src/state.rs` | `ProviderState` gains its fields; respawn rebuilds the level. |
| `src-tauri/src/commands.rs` | The new commands. |
| `src-tauri/src/sidecar_path.rs` | Unchanged unless a task says otherwise. |
| `src-tauri/Cargo.toml`, `package.json`, `Cargo.lock`, `pnpm-lock.yaml` | Three plugins, pinned. |
| `src/App.svelte` | Placeholder branch replaced by the real screens. |
| `src/lib/ipc.ts`, `src/lib/types.ts` | New calls; types still derived. |
| `AGENTS.md`, `README.md` | Documented behaviour that changed. |

**Not touched:** `sidecar/tidal2ytm-sidecar.spec`, the DDL in `crates/tidal2ytm-store/src/schema.rs`, and the untracked `docs/tidal2ytm-desktop-ui-migration-preliminary-plan.md`.

---

### Task 1: One data directory, named explicitly

Closes the bug where a packaged build reports signed out and a development build reports signed in. Nothing else depends on it being right, and everything depends on it being right.

**Files:**
- Modify: `tidal2ytm/paths.py:5-12`
- Modify: `src-tauri/src/lib.rs` (the `setup` closure, around lines 60-90)
- Test: `tests/test_paths.py` (new), `src-tauri/src/lib.rs` (its existing `#[cfg(test)]` module)

**Interfaces:**
- Consumes: nothing.
- Produces: `paths.DATA_DIR` — a `Path`, equal to `Path("data")` when `TIDAL2YTM_DATA_DIR` is unset or empty, and to `Path(value)` otherwise. `TIDAL_TOKEN_FILE`, `YTM_AUTH_FILE` and `PLAN_FILE` derive from it as today. The host sets `TIDAL2YTM_DATA_DIR` to `<app_data_dir>\data` in `SidecarConfig.env`, and creates that directory before the spawn.

- [ ] **Step 1: Write the failing test**

In `tests/test_paths.py`:

```python
def test_data_dir_is_relative_when_the_variable_is_absent(monkeypatch):
    monkeypatch.delenv("TIDAL2YTM_DATA_DIR", raising=False)
    importlib.reload(paths)
    assert paths.DATA_DIR == Path("data")


def test_data_dir_follows_the_variable_when_it_is_set(monkeypatch, tmp_path):
    monkeypatch.setenv("TIDAL2YTM_DATA_DIR", str(tmp_path))
    importlib.reload(paths)
    assert paths.DATA_DIR == tmp_path


def test_an_empty_variable_is_ignored(monkeypatch):
    monkeypatch.setenv("TIDAL2YTM_DATA_DIR", "")
    importlib.reload(paths)
    assert paths.DATA_DIR == Path("data")


def test_the_token_paths_follow_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TIDAL2YTM_DATA_DIR", str(tmp_path / "elsewhere"))
    importlib.reload(paths)
    assert paths.TIDAL_TOKEN_FILE == tmp_path / "elsewhere" / "tidal_token.json"
```

Reload `paths` at the end of each test rather than only at the start, so a test that fails does not leave the module pointing at a deleted `tmp_path` for the next one. `monkeypatch` undoes the environment; the reload is what undoes the module.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -v`
Expected: the three tests that set the variable FAIL; the one that deletes it PASSES.

- [ ] **Step 3: Implement the resolution in `tidal2ytm/paths.py`**

Replace line 7 with a two-line resolution and import `os`. Keep the comment's claim accurate: the directory is still created lazily by `ensure_data_dir()`, and importing the module still touches no disk.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: 4 passed.

- [ ] **Step 5: Add the host side**

In `src-tauri/src/lib.rs`'s `setup` closure, compute `let data_dir = cwd.join("data");` beside the existing `cwd`, `std::fs::create_dir_all(&data_dir)` immediately after `create_dir_all(&cwd)`, and push `("TIDAL2YTM_DATA_DIR".to_string(), data_dir.display().to_string())` onto the `SidecarConfig.env` that the launch already builds. The existing comment on `create_dir_all(&cwd)` says the directory is wanted because *"the token caches are meant to live"*; extend it to name the `data` subdirectory, because after this task that is where they live and the old wording now describes a path nothing writes to.

- [ ] **Step 6: Add a host test for the wiring**

In `lib.rs`'s test module, assert that the `SidecarConfig` the setup closure builds carries a `TIDAL2YTM_DATA_DIR` entry ending in `data`. Extract the closure's config-building into a free function `fn sidecar_config(launch: &Launch, cwd: &Path, data_dir: &Path) -> SidecarConfig` so the test can call it without a `tauri::App`, which nothing in this crate can build.

- [ ] **Step 7: Run the host tests**

Run: `cargo test -p tidal2ytm`
Expected: the existing 20 pass, plus the new one.

- [ ] **Step 8: Run the full gate set**

Run: `uv run pytest -q` then `cargo test -p tidal2ytm` then `cargo clippy -p tidal2ytm --all-targets -- -D warnings`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add tidal2ytm/paths.py tests/test_paths.py src-tauri/src/lib.rs
git commit -m "fix: name the data directory instead of inferring it from the cwd"
```

---

### Task 2: The read loop becomes concurrent

The most invasive change in the slice, and nothing depends on it yet — which is exactly why it goes second. `auth.begin` blocks on a human for minutes, and under the current serial loop that silence would also silence `auth.status`, which the host gives five seconds.

**Files:**
- Modify: `sidecar/tidal2ytm_sidecar/runner.py:19-95`
- Test: `sidecar/tests/test_runner.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `run(stdin: IO[str], stdout: IO[str]) -> int` with the same signature. `_write` becomes safe to call from more than one thread. `protocol.dispatch(op, params, rid)` is unchanged.

- [ ] **Step 1: Write the failing test**

In `sidecar/tests/test_runner.py`, write a test that feeds two request lines and asserts both answers arrive:

```python
def test_two_operations_overlap_and_both_answer():
    stdin = io.StringIO('{"id": 1, "op": "auth.status", "params": {}}\n'
                        '{"id": 2, "op": "auth.status", "params": {}}\n')
    stdout = io.StringIO()
    threading.Timer(...)  # see note below
    runner.run(stdin, stdout)
```

The test cannot simply read two lines: under a serial loop the first `auth.status` returns before the second line is even read, so the test would pass and prove nothing. The defect to catch is *overlap*, so the second op must be one that blocks — stub `protocol.dispatch` with a fake whose first call waits on an `threading.Event` the test only sets after the second line has been read. Assert that both ids appear in the output, and that the second line was read before the first op returned. Put the shared state in an `Event`, not a `Mock`'s `call_count`, which is only meaningful after the fact.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest sidecar/tests/test_runner.py -v`
Expected: FAIL — the stub's first call never returns, because the serial loop never reads line two.

- [ ] **Step 3: Make the loop concurrent**

In `runner.py`:

- Wrap `_write` in a module-level `threading.Lock`. The function stays `def _write(stream, envelope)`; lock inside it so no caller can forget.
- Change the dispatch site from a direct `_write(stdout, protocol.dispatch(op, params, rid))` to a `threading.Thread(target=..., daemon=True).start()` that performs the dispatch and writes, keeping the existing `try/except Exception` arm so one bad op still answers rather than dying.
- Keep the `except ValueError` around `protocol.read_message` on the reading thread: an unreadable line must not end the session, and that is cheaper to guarantee on the thread that reads.
- Do not bound the thread count. Three operations is the ceiling in this slice, and a pool would be a limit nothing needs.

**One subtlety the implementer must not get wrong:** the `rid` is read from the message before the thread starts and captured by value. A closure that reads `rid` from an enclosing loop variable will answer every op with the last id.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest sidecar/tests/test_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Run both Python suites**

Run: `uv run pytest -q`
Expected: green. The sidecar's 34 tests and the package's suite both still pass; this change touches `runner.py`, which the sidecar suite exercises.

- [ ] **Step 6: Commit**

```bash
git add sidecar/tidal2ytm_sidecar/runner.py sidecar/tests/test_runner.py
git commit -m "fix(sidecar): answer requests while another operation is blocked"
```

---

### Task 3: `auth.status` grows a third state and a credential flag

The first wire-shape change, and the one with a frontend consequence: `contracts/auth_status.json` is authoritative and `src/lib/types.ts` derives from it.

**One deliberate departure from the spec.** The spec says the payload gains a three-valued `status` *and* a `refreshable` boolean. The plan drops the boolean. `status` already says it, a second field restating one fact is a second thing to forget to update, and `Widen` in `types.ts` maps a `null` literal to `string | null`, so a nullable flag would arrive in TypeScript typed as a string. The UI rule becomes: the sign-in button shows when `status === "signed_out"`, and for YouTube Music the import dialog shows when `status === "signed_out"` and `has_client_secret` is false.

**Files:**
- Modify: `tidal2ytm/auth.py:79-108` (`tidal_cache_valid`, `ytm_cache_valid`)
- Modify: `sidecar/tidal2ytm_sidecar/ops.py:30-89`
- Modify: `contracts/auth_status.json`
- Modify: `crates/tidal2ytm-ipc/src/protocol.rs:19`
- Modify: `src-tauri/src/state.rs` (`ProviderState`)
- Modify: `src/lib/components/AuthStatus.svelte:28-29` (the only reader of `state.valid`)
- Test: `sidecar/tests/test_ops.py`, `crates/tidal2ytm-ipc/tests/round_trip.rs`, `src/lib/components/AuthStatus.test.ts`

**Interfaces:**
- Consumes: `paths.DATA_DIR` from Task 1.
- Produces: `auth.status` data is `{"tidal": ProviderState, "ytm": ProviderState}` where `ProviderState` is `{"status": "valid" | "refreshable" | "signed_out", "expires_at": string | null, "has_client_secret": boolean}`. On the Python side, `_ProviderState = dict[str, bool | str | None]` is unchanged in shape; three new helpers `tidal_cache_state(path) -> str`, `ytm_cache_state(path) -> str`, and `client_secret_present() -> bool` are added to `auth.py`, each returning one of the three literals. `tidal_cache_valid` and `ytm_cache_valid` stay, because `auth.py`'s own sign-in paths use them.

- [ ] **Step 1: Write the failing Python tests**

In `sidecar/tests/test_ops.py`, three cases, each over a temporary token file written with `TIDAL2YTM_DATA_DIR` pointed at it:

- a Tidal cache with `expiry_time` in the past and a non-empty `refresh_token` reports `refreshable`, not `signed_out`;
- a Tidal cache with `expiry_time` in the past and no `refresh_token` reports `signed_out`;
- a YouTube Music cache with a past `expires_at` and a `refresh_token` reports `refreshable`, which is what `ytm_cache_valid` already decides — so this one guards the mapping rather than the rule.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest sidecar/tests/test_ops.py -v`
Expected: the two Tidal cases FAIL with `status` absent, because no `status` key exists yet.

- [ ] **Step 3: Implement the three states in `auth.py`**

Add `tidal_cache_state` and `ytm_cache_state`. Each reads the cache the way its `*_cache_valid` sibling does — reusing that function rather than re-reading, so the expiry arithmetic has one owner — and maps the pair of booleans onto the three literals. `tidal_cache_valid` must change first: it currently ignores `refresh_token`, so it must become "unexpired, or refreshable", or the new state function has nothing to distinguish.

Add `client_secret_present()` as `len(sorted(paths.DATA_DIR.glob("client_secret_*.json"))) > 0`. It is a question about a directory, not about a credential's contents, so it deliberately does not call `read_client_secret` and cannot raise on a malformed file.

- [ ] **Step 4: Implement the payload in `ops.py`**

Replace the `valid` key with `status` in both `_tidal_state` and `_ytm_state`, and add `has_client_secret`. `tidal`'s value is always `True`: device authorisation carries no client file, and a uniform struct keeps `ProviderState` a single type in `types.ts` instead of a union the screen has to narrow.

- [ ] **Step 5: Write the contract sample**

Replace `contracts/auth_status.json` with the signed-out sample:

```json
{"id": 1, "ok": true, "data": {"tidal": {"status": "signed_out", "expires_at": null, "has_client_secret": true}, "ytm": {"status": "signed_out", "expires_at": null, "has_client_secret": false}}}
```

- [ ] **Step 6: Update the Rust and TypeScript sides to match**

- `crates/tidal2ytm-ipc/src/protocol.rs`: leave `REQUIRED_CAPABILITIES` at `["auth.status"]` — Task 4 is what grows it.
- `src-tauri/src/state.rs`: `ProviderState` becomes `pub status: AuthState`, `pub expires_at: Option<String>`, `pub has_client_secret: bool`, with `AuthState` a `#[serde(rename_all = "snake_case")]` enum of `Valid`, `Refreshable`, `SignedOut`. The enum's serde names must be the three literals, which is the same seam `TrackStatus` already has.
- `src/lib/components/AuthStatus.svelte:28-29`: `state.valid` becomes `state.status !== "signed_out"`, and the `data-state` attribute becomes one of `valid`, `refreshable`, `signed-out`. `Home.svelte` passes the object through untouched, so it is `AuthStatus.svelte` and not `Home.svelte` that stops compiling.
- `src/lib/components/AuthStatus.test.ts`: its `data-state` assertions change with it. That attribute is the reason it exists — a screen-reader user needs the outcome readable without depending on the wording — so keep emitting it rather than switching to a class.
- `src/lib/types.ts`: unchanged. It derives, and that is the point — if it needs editing, the contract and the two asserting suites are what should change first.

- [ ] **Step 7: Run the contract suites**

Run: `uv run pytest sidecar/tests/test_protocol.py -v` then `cargo test -p tidal2ytm-ipc`
Expected: both green against the new sample. `round_trip.rs` spawns the real sidecar, so it exercises the Python and Rust shapes against each other.

- [ ] **Step 8: Run the frontend gates**

Run: `pnpm check` then `pnpm test`
Expected: green, with `AuthStatus.svelte` on the new shape.

- [ ] **Step 9: Commit**

```bash
git add contracts/auth_status.json tidal2ytm/auth.py sidecar/tidal2ytm_sidecar/ops.py crates/tidal2ytm-ipc/src/protocol.rs src-tauri/src/state.rs sidecar/tests/test_ops.py src/routes/Home.svelte
git commit -m "feat(auth): distinguish refreshable from signed out"
```

---

### Task 4: `auth.begin`, and the credential check that stops killing the sidecar

`auth.py`'s no-client-secret path calls `sys.exit(1)`. `SystemExit` is a `BaseException`, so it escapes `runner.py`'s `except Exception` and takes the sidecar down — and the host would respawn into the same failure. This task gives that check one owner that raises, and adds the operation that uses it.

**Files:**
- Modify: `tidal2ytm/auth.py:192-224` (`_ensure_client_secret`)
- Modify: `sidecar/tidal2ytm_sidecar/ops.py` (new `auth_begin`)
- Modify: `sidecar/tidal2ytm_sidecar/protocol.py:20,61-74`
- Modify: `crates/tidal2ytm-ipc/src/protocol.rs:19`
- Create: `contracts/auth_begin.json`
- Modify: `src-tauri/src/commands.rs` (new `begin_auth`)
- Test: `sidecar/tests/test_ops.py`, `sidecar/tests/test_protocol.py`, `crates/tidal2ytm-ipc/tests/round_trip.rs`

**Interfaces:**
- Consumes: `paths.DATA_DIR` (Task 1), the three states (Task 3).
- Produces: `auth.begin` takes `{"provider": "tidal" | "ytm"}` and returns `{"provider": "ytm"}` — an echo, so the caller can tell which answer it got if two sign-ins overlap. `auth.require_client_secret(data_dir: Path) -> tuple[str, str]` in `auth.py` raises `ValueError` and is what both `run_ytm_auth` and the sidecar's `auth_begin` call. New Tauri command `begin_auth(provider: String) -> Result<(), String>`, with **no timeout** — the operation waits on a human and a browser, so `call` is given `None`.

- [ ] **Step 1: Write the failing tests**

Three, in `sidecar/tests/`:

- `auth.begin` with `provider: "ytm"` and no `client_secret_*.json` present answers `{"ok": false, "error": {"kind": "bad_request", ...}}` and does **not** raise. That is the defect: today it exits the process.
- The same op with an unknown provider answers `bad_request` rather than falling through to a Tidal login.
- `contracts/auth_begin.json` round-trips: `test_protocol.py` asserts the sidecar's envelope equals the file, and `round_trip.rs` asserts the same on the Rust side.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest sidecar/tests/test_ops.py -v -k begin`
Expected: FAIL — the op does not exist.

- [ ] **Step 3: Refactor the credential check to raise**

In `auth.py`, split `_ensure_client_secret` into a `auth.require_client_secret(data_dir)` that raises `ValueError` with the current message text, and keep `_ensure_client_secret`'s CLI-only behaviour — the synthetic paste path and the `sys.exit` — on top of it. `read_client_secret` keeps raising `FileNotFoundError`/`RuntimeError`/`ValueError`; `require_client_secret` catches those and re-raises one `ValueError` carrying the same text, so the sidecar has a single exception type to catch and the CLI still prints what it prints today.

- [ ] **Step 4: Implement `auth_begin` in `ops.py`**

```python
def auth_begin(provider: str) -> dict[str, str]:
```
Dispatch on `provider` to `auth.run_tidal_auth()` or `auth.run_ytm_auth()`, and return `{"provider": provider}`. Both of those return a `Path`, not a token; the return value is not used. Any exception from them is converted by `dispatch` into the existing `upstream` arm, so the only error this op raises deliberately is the credential one, which it maps to `bad_request` itself.

**Blocking is the point.** This op does not return until the browser flow completes. That is safe only because Task 2 made the loop concurrent.

- [ ] **Step 5: Wire it through the protocol and both contracts**

`protocol.py`: add `"auth.begin"` to `CAPABILITIES` and add the dispatch arm reading `params["provider"]`. `REQUIRED_CAPABILITIES` in `protocol.rs:19` becomes `&["auth.status", "auth.begin"]`, and the comment above it stays true: a sidecar that predates this op is refused at startup rather than failing later.

- [ ] **Step 6: Add the Tauri command**

`begin_auth(provider: String)` in `commands.rs`, validating `provider` against the two literals before it crosses the pipe, and calling `call(&state, "auth.begin", json!({"provider": provider}), None)`. The existing `call` helper takes `Duration` and always passes `Some`; change it to take `Option<Duration>` and pass it through, so exactly one call site opts out of a timeout rather than a second helper existing for the same job.

- [ ] **Step 7: Run every suite that touches the wire**

Run: `uv run pytest -q` then `cargo test -p tidal2ytm-ipc -p tidal2ytm`
Expected: green. Note `round_trip.rs` spawns the real sidecar, so `auth.begin` must not actually block in it — that suite calls `auth.status`, which does not.

- [ ] **Step 8: Commit**

```bash
git add tidal2ytm/auth.py sidecar/tidal2ytm_sidecar/ops.py sidecar/tidal2ytm_sidecar/protocol.py contracts/auth_begin.json crates/tidal2ytm-ipc/src/protocol.rs src-tauri/src/commands.rs src-tauri/src/lib.rs sidecar/tests/
git commit -m "feat(sidecar): add auth.begin and stop a missing credential killing the sidecar"
```

---

### Task 5: `favorites.list`, one page at a time, with the refresh it needs

Tidal's self-service refresh has to exist here because `tidalapi` provides nothing usable. The builder check found this: there is no `load_oauth_token`; `load_oauth_session` loads without refreshing and without persisting, and `login_session_file` — the only refresh-and-write path — rewrites the token file into a five-key shape that destroys it and falls back to blocking on a human login.

**Files:**
- Create: `sidecar/tidal2ytm_sidecar/refresh.py`
- Modify: `tidal2ytm/tidal_source.py:43-65`
- Modify: `sidecar/tidal2ytm_sidecar/ops.py` (new `favorites_list`)
- Modify: `sidecar/tidal2ytm_sidecar/protocol.py`
- Modify: `crates/tidal2ytm-ipc/src/protocol.rs`
- Create: `contracts/favorites_list.json`
- Test: `sidecar/tests/test_ops.py`, `sidecar/tests/test_refresh.py` (new)

**Interfaces:**
- Consumes: `paths.DATA_DIR` (Task 1), `auth.paths.TIDAL_TOKEN_FILE`.
- Produces: `favorites.list` takes `{"offset": int, "limit": int}` and returns `{"tracks": [{"tidal_id": int, "title": str, "artist": str, "artists": [str], "album": str, "album_id": int, "album_year": int | null, "duration_sec": int, "track_num": int, "disc_num": int, "version": str | null}], "page_complete": bool}`. `page_complete` is `true` when the returned page was shorter than `limit`, so the host knows the fetch is finished without a further empty request. `refresh.load_session(token_file: Path) -> Session` and `refresh.persist_session(token_file: Path, session: Session) -> None`. `tidal_source.iter_favorite_pages(session, limit: int) -> Iterator[list[SourceTrack]]` replaces `get_favorite_tracks` as the entry point; `get_favorite_tracks` stays as a thin `list(iter_favorite_pages(...))` because the CLI still calls it.

- [ ] **Step 1: Write the failing refresh test**

In `sidecar/tests/test_refresh.py`, write a test that loads a synthetic token file, calls `refresh.persist_session` with a fake session, and asserts the file **still has exactly the six keys** `tidal2ytm/auth.py` guarantees: `token_type`, `access_token`, `refresh_token`, `expiry_time`, `user_id`, `country_code`. That is the defect this code exists to prevent — `save_session_to_file` writes five different ones.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest sidecar/tests/test_refresh.py -v`
Expected: FAIL — the module does not exist.

- [ ] **Step 3: Implement `refresh.py`**

```python
def load_session(token_file: Path) -> Session:
    """Load a session, refreshing the access token when it has lapsed.

    `tidalapi` has no refresh-and-persist call this code can use:
    `load_oauth_session` loads without refreshing, and `login_session_file`
    rewrites the token file into a five-key shape and blocks on a human when
    the session is not merely stale. So the refresh is done here and the six
    keys are written back by hand.
    """
```

`load_session` reads the six keys, calls `session.load_oauth_session(...)`, and — only when the pre-read `expiry_time` was in the past — calls `persist_session`. `persist_session` rewrites all six keys from the session, preserving `user_id` and `country_code` read from the old file. **It never deletes the file**, on any path: a dropped connection is not grounds for destroying a refresh token.

- [ ] **Step 4: Split `tidal_source`'s paging**

Turn the nested `_fetch_all` into a generator. Keep the `seen: set[int]` de-duplication, but note that with a caller-driven page loop the set must live in the caller — otherwise the second page re-emits a track the first page carried, and the host upserts it twice. The generator therefore yields `(offset, page)` pairs and the caller owns the set; `get_favorite_tracks` owns one and passes it down.

- [ ] **Step 5: Write the failing op test**

In `sidecar/tests/test_ops.py`: `favorites.list` with a stubbed `refresh.load_session` and a stubbed `tidal_source` page iterator returns `page_complete: false` for a full page and `true` for a short one, and passes `offset` and `limit` through unchanged.

- [ ] **Step 6: Implement `favorites_list` in `ops.py`**

Serialize each `SourceTrack` to the eleven keys above. `page_complete` is `len(tracks) < limit`. A `tidalapi` exception that means the refresh failed is caught narrowly and re-raised as `AuthExpired`, so the host's job reports `auth_expired` and offers sign-in rather than a generic failure. Broad `except Exception` here would turn a real bug into "signed out", which is exactly what `ops.py`'s existing `_CACHE_READ_ERRORS` comment warns against.

- [ ] **Step 7: Wire the protocol and both contracts**

Add `"favorites.list"` to `CAPABILITIES` and to `REQUIRED_CAPABILITIES` in `protocol.rs`, and write `contracts/favorites_list.json` with a two-track example. Both suites assert against it.

- [ ] **Step 8: Run every suite that touches the wire or Python**

Run: `uv run pytest -q` then `cargo test -p tidal2ytm-ipc`
Expected: green.

- [ ] **Step 9: Commit**

```bash
git add sidecar/tidal2ytm_sidecar/refresh.py sidecar/tidal2ytm_sidecar/ops.py sidecar/tidal2ytm_sidecar/protocol.py tidal2ytm/tidal_source.py contracts/favorites_list.json crates/tidal2ytm-ipc/src/protocol.rs sidecar/tests/
git commit -m "feat(sidecar): add paged favorites.list with its own Tidal refresh"
```

---

### Task 6: `upsert_track_source`, the write that cannot blank a match

**Files:**
- Modify: `crates/tidal2ytm-store/src/store.rs` (after `upsert_track`, around line 318)
- Test: `crates/tidal2ytm-store/src/store.rs` (its `#[cfg(test)]` module)

**Interfaces:**
- Consumes: `Store`, `upsert_artist`, `upsert_album`.
- Produces: `pub fn upsert_track_source(&self, row: &TrackRow) -> Result<i64, StoreError>` — same signature and same `ON CONFLICT` guard as `upsert_track`, writing only `tidal_id`, `album_id`, `title`, `artist`, `duration_sec`, `track_num`, `disc_num`, `version`. It sets no status, no YTM column, no `match_method`, no `confidence_overall` and no `review_reason`.

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn a_resync_leaves_a_match_and_a_transferred_row_untouched() { /* migrated() store;
    upsert_track with a full match, then upsert_track_source with the same tidal_id
    and a changed title; assert yt_video_id, match_method, confidence_overall and
    status are all unchanged */ }

#[test]
fn a_resync_updates_the_tidal_columns() { /* changed title, duration, version are
    visible after upsert_track_source */ }
```

The first is the one that matters. Its defect: a re-sync over a matched library erases every match and every review note.

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p tidal2ytm-store --lib -- upsert_track_source`
Expected: FAIL — the method does not exist.

- [ ] **Step 3: Implement it**

Copy `upsert_track`'s `INSERT ... ON CONFLICT (tidal_id) DO UPDATE SET ... WHERE track.status <> 'transferred'` and reduce the `SET` list to the eight Tidal columns. Keep the guard verbatim: it is the `transferred` invariant expressed in SQL, and a shorter one would be a weaker one. Comment the reason the two functions coexist — same table, disjoint column sets, disjoint callers — so a later reader does not "simplify" one into the other.

- [ ] **Step 4: Run it to verify it passes**

Run: `cargo test -p tidal2ytm-store --lib`
Expected: 18 pass, plus the two new.

- [ ] **Step 5: Run the gates**

Run: `cargo fmt --all --check` then `cargo clippy -p tidal2ytm-store --all-targets -- -D warnings` then `cargo test -p tidal2ytm-store --lib`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add crates/tidal2ytm-store/src/store.rs
git commit -m "feat(store): add a sync write that cannot touch a match"
```

---

### Task 7: The job manager and the sync job

**Files:**
- Create: `src-tauri/src/jobs.rs`
- Create: `src-tauri/src/sync.rs`
- Modify: `src-tauri/src/lib.rs` (`mod`, `manage`, `generate_handler!`)
- Modify: `src-tauri/src/commands.rs` (`start_sync`, `cancel_job`)
- Test: `src-tauri/src/jobs.rs`, `src-tauri/src/sync.rs` (each its own `#[cfg(test)]` module)

**Interfaces:**
- Consumes: `Store::upsert_track_source`, `Store::upsert_artist`, `Store::upsert_album`, `SidecarClient::call`, `favorites.list`.
- Produces: `pub struct JobManager` with `pub fn start_sync(&self, client: Arc<SidecarClient>, store: Arc<Store>, emit: impl Fn(Progress) + Send + 'static) -> Result<u64, String>` and `pub fn cancel(&self, jid: u64) -> bool`. `pub struct Progress { pub jid: u64, pub kind: &'static str, pub state: JobState, pub done: i64, pub total: Option<i64> }`, with `JobState` an enum of `Running`, `Cancelling`, `Cancelled`, `Done`, `Failed`. `Store` gains `pub fn set_meta(&self, key: &str, value: &str) -> Result<(), StoreError>` and `pub fn get_meta(&self, key: &str) -> Result<Option<String>, StoreError>`, owning two keys with these meanings: `sync_started_at` is set when a sync begins and **cleared only when it completes successfully**, so it reads as "a sync began and did not finish" after a failure, a cancellation or a crash; `sync_last_completed_at` is written on successful completion. New Tauri commands `start_sync() -> Result<u64, String>` and `cancel_job(jid: u64) -> Result<(), String>`.

- [ ] **Step 1: Write the failing job test**

In `jobs.rs`'s test module: start a sync against a stubbed client that returns two pages and asserts, at the end, that the second page was requested only after the first page's rows were committed. The defect: a loop that accumulates every page and commits at the end loses the entire library if it dies on the last page. Use a temp-directory `Store` and read the row count between the two calls.

Add the Review Focus case for a session lapsing mid-sync: a stubbed client whose second page answers `auth_expired`. Assert the job ends `Failed` and not `Cancelled`, that the first page's rows are still committed, and that `sync_started_at` is **still set** afterwards — a key cleared on every exit would make Home report a partial mirror as one that was never begun, which is the same lie the three sync states exist to prevent.

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p tidal2ytm --lib -- jobs`
Expected: FAIL — the module does not exist.

- [ ] **Step 3: Implement `jobs.rs`**

A `Mutex<HashMap<u64, Arc<JobState>>>` plus a cancellation flag per job. `cancel` sets the flag and returns whether the job existed; it does **not** set `JobState::Cancelled` — that transition belongs to the loop, so the two states the UI distinguishes cannot drift apart. `emit` is a callback rather than a Tauri channel type so `jobs.rs` carries no Tauri dependency and its tests need no app, which is the same rule `commands.rs` follows for `counts_by_name`.

- [ ] **Step 4: Implement `sync.rs`**

```rust
pub async fn run(client: &SidecarClient, store: &Store, jid: u64, emit: &impl Fn(Progress), cancel: &Arc<AtomicBool>) -> Result<(), String>
```

`store.set_meta("sync_started_at", <iso8601 utc now>)` before the first request; `offset` starts at 0; loop: call `favorites.list` with `{"offset": offset, "limit": PAGE_LIMIT}`, upsert each track with `upsert_track_source` inside one transaction per page, emit progress, break on `page_complete` or on the cancel flag. `PAGE_LIMIT` is 100. On successful completion, clear `sync_started_at` and write `sync_last_completed_at` in the same call. **On failure or cancellation, leave `sync_started_at` set** — that is what makes Home able to say "interrupted" after the process that knew about the failure is gone. **The `upsert_artist` → `upsert_album` → `upsert_track_source` order is the foreign-key order and is not negotiable.**

**Sync never deletes.** No `DELETE`, no orphan sweep of `album` or `artist` rows, and no removal of a track that is absent from this fetch. The master design calls this reconcile; 4a upserts only, and the deviation is recorded in the spec with the reopen condition — a delete may only ever run against a fetch that completed every page, in one transaction at the end, and never over a `transferred` or `skip` row. Writing the loop without that guard makes the guard harder to add back, so leave the shape obvious.

An album-less track (`album == ""`) upserts one album row named `""` for that artist, and the frontend renders that name as `Unknown album`. That is Review Focus item 5, and its test belongs here: a track with no album produces one row, not a foreign-key failure that aborts the page.

- [ ] **Step 5: Register and expose**

`mod jobs;` and `mod sync;` in `lib.rs`, `app.manage(JobManager::new())` in the `setup` closure, `start_sync` and `cancel_job` in `generate_handler!`. Both commands take `State<'_, JobManager>`; `start_sync` also needs the client and the store, so it reads them from `State<'_, AppState>` — a second `State` parameter, not a second `manage`, because `AppState` already holds both.

- [ ] **Step 6: Run the tests**

Run: `cargo test -p tidal2ytm --lib`
Expected: 20 pass, plus the new ones.

- [ ] **Step 7: Run the gates**

Run: `cargo clippy -p tidal2ytm --all-targets -- -D warnings` then `cargo test -p tidal2ytm`
Expected: green.

- [ ] **Step 8: Commit**

```bash
git add src-tauri/src/jobs.rs src-tauri/src/sync.rs src-tauri/src/lib.rs src-tauri/src/commands.rs
git commit -m "feat(host): add the job manager and the sync job"
```

---

### Task 8: The host log file, and a level that survives a respawn

**Files:**
- Modify: `src-tauri/src/logging.rs`
- Modify: `src-tauri/src/lib.rs` (the `run()` ordering, and the respawn in `state.rs`)
- Modify: `src-tauri/src/state.rs` (`replace`)
- Modify: `src-tauri/src/commands.rs` (`set_log_level`, `get_log_paths`)
- Test: `src-tauri/src/logging.rs` (its own `#[cfg(test)]` module)

**Interfaces:**
- Consumes: `Store` meta helpers from Task 7.
- Produces: `pub struct LogPaths { pub host_log: PathBuf, pub history: PathBuf, pub frontend_log: PathBuf }`, and `pub fn install(level: &str, dir: &Path) -> Result<(), String>` which replaces the current `install_debug_logger()`. `const MAX_BYTES: u64 = 5 * 1024 * 1024;` and `const KEEP: usize = 3;`. New Tauri commands `get_log_paths() -> Result<LogPaths, String>` and `set_log_level(level: String) -> Result<(), String>`. `AppState::replace` rebuilds `config.env`'s `TIDAL2YTM_LOG_LEVEL` from `meta` before respawning.

- [ ] **Step 1: Write the failing test**

Rotation: point `install` at a temp directory, write until `MAX_BYTES` is exceeded, and assert that at most `KEEP` history files exist and the newest is the most recently rotated. Also assert the host log file is non-empty afterwards — the defect being a sink installed but never wired to a file.

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p tidal2ytm --lib -- logging`
Expected: FAIL — `install` does not exist.

- [ ] **Step 3: Implement rotation by hand**

Rename `host.log` to `host.1.log`, shifting `host.N.log` up and dropping the oldest, before opening the new file. Four lines of `std::fs::rename`. The spec's reasoning is the one to preserve in a comment: a crate for this is a dependency somebody then has to keep patched.

- [ ] **Step 4: Read the level before the sink opens**

In `run()`, the order becomes: open the database, read `log_level` from `meta` (falling back to `INFO`), `logging::install(level, dir)`, then `setup`. `install_debug_logger()` currently runs first, before anything can emit. Reversing that is the whole point of the task — a sink installed at the wrong level has already written a misconfigured run's earliest records, and those records explain the misconfiguration.

- [ ] **Step 5: Rebuild the level on respawn**

In `state.rs`'s `replace`, before spawning, replace the `TIDAL2YTM_LOG_LEVEL` entry in a clone of `config.env` with the value currently in `meta`. The frozen `config` keeps holding the program and the arguments — that invariant is what stops a changed `TIDAL2YTM_SIDECAR_BIN` swapping the binary under a running application — but the level is re-read every spawn. Comment the split so a later reader does not "fix" it by freezing the level too.

- [ ] **Step 6: Run the tests and gates**

Run: `cargo test -p tidal2ytm --lib` then `cargo clippy -p tidal2ytm --all-targets -- -D warnings`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src-tauri/src/logging.rs src-tauri/src/lib.rs src-tauri/src/state.rs src-tauri/src/commands.rs
git commit -m "feat(host): write a log file and re-read the level on every spawn"
```

---

### Task 9: Three plugins, three narrow permissions

The first time the frontend gains any reach at all. `capabilities/default.json` grants `core:default` and nothing else, deliberately; this task adds exactly three identifiers and a wildcard would be a design failure rather than a configuration detail.

**Files:**
- Modify: `src-tauri/Cargo.toml`, `package.json`, `src-tauri/capabilities/default.json`, `src-tauri/src/lib.rs`
- Test: the build itself — capability identifiers are validated against `gen/schemas/desktop-schema.json` during the host build, so a wrong name fails loudly rather than silently granting nothing.

**Interfaces:**
- Consumes: nothing.
- Produces: `permissions` in `capabilities/default.json` becomes `["core:default", "dialog:allow-open", "fs:allow-appdata-read-recursive", "fs:allow-appdata-write-recursive", { "identifier": "opener:allow-open-path", "allow": [{ "path": "$APPDATA/**" }] }]`. Frontend dependencies `@tauri-apps/plugin-dialog` and `@tauri-apps/plugin-fs`, both pinned to the same minor as `@tauri-apps/api` (`2.12.1`), and `@tauri-apps/plugin-opener` if the version line offers one at that pin — if it does not, take the plugin's own latest 2.x exact version and say so in the commit body.

- [ ] **Step 1: Add the Rust dependencies**

`tauri-plugin-dialog`, `tauri-plugin-fs`, `tauri-plugin-opener`, all pinned exactly. Comment each with the one thing it is granted for, so a fourth plugin added later has to explain itself in the same place.

- [ ] **Step 2: Register the plugins**

In `run()`, before `.setup(...)`: `.plugin(tauri_plugin_dialog::init()).plugin(tauri_plugin_fs::init()).plugin(tauri_plugin_opener::init())`. Registration order does not matter; doing it before `setup` means a command that uses one is reachable.

- [ ] **Step 3: Add the permissions**

Edit `capabilities/default.json` to exactly the array above, and extend its `description` to say what each permission is for — the current description's claim that *"tidal2ytm grants no plugin permissions"* becomes false, and a stale justification in a live file is a one-word lie.

- [ ] **Step 4: Add the frontend dependencies**

`pnpm add @tauri-apps/plugin-dialog@2.12.1 @tauri-apps/plugin-fs@2.12.1 @tauri-apps/plugin-opener@2.12.1`, then check `git diff package.json` for a `^` or a `~`. Every pin in this project is exact, and a range is how a release someone else published becomes this project's outage.

- [ ] **Step 5: Build the host**

Run: `pnpm build` then `cargo build -p tidal2ytm`
Expected: green, including the build script's capability validation. The placeholder `src-tauri/binaries/tidal2ytm-sidecar-<triple><.exe>` must exist first or the build script fails before compiling a line of host code; the triple comes from `rustc -vV`'s `host:` field.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/Cargo.toml package.json pnpm-lock.yaml Cargo.lock src-tauri/capabilities/default.json src-tauri/src/lib.rs
git commit -m "build: grant the frontend file dialog, app data and log directory access"
```

---

### Task 10: Diagnostics

**Files:**
- Create: `src/routes/Diagnostics.svelte`
- Create: `src/lib/ui-log.ts`
- Modify: `src/App.svelte` (route the real screen), `src/lib/ipc.ts` (new calls)
- Test: `src/routes/Diagnostics.test.ts` (new), `src/lib/ui-log.test.ts` (new)

**Interfaces:**
- Consumes: `get_log_paths`, `set_log_level`, `get_schema_version`, `get_status_counts` from Task 8 and Task 3, and `@tauri-apps/plugin-dialog`'s `open()` from Task 9.
- Produces: `import_client_secret(source_path: String) -> Result<(), String>` in `commands.rs`. `src/lib/ui-log.ts` exports `export function uiLog(level: "info" | "error", message: string): void` and `UI_LOG_PATH` resolution through `@tauri-apps/plugin-fs`.

- [ ] **Step 1: Write the failing component test**

`Diagnostics.test.ts` renders the screen with the host stubbed and asserts it renders: the two provider sign-in actions, the level selector, three log paths, and the database counts. Assert membership and presence, never exact copy — the project's rule is that no test asserts a colour, a glyph or a string.

Add a test for Review Focus item 2: when YouTube Music is `signed_out` with `has_client_secret: false`, the import action is offered; when it is `true`, it is not.

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm test`
Expected: FAIL — the module does not exist.

- [ ] **Step 3: Implement the client-secret import command**

In `commands.rs`, `import_client_secret(source_path: String)`: read the chosen file, verify it parses as a JSON object (and **nothing more** — the credential's meaning stays in `auth.py`), remove every existing `client_secret_*.json` in `paths.DATA_DIR`, then write the chosen bytes under its original name. On any failure, touch nothing. The replace-then-write order matters and is the Review Focus answer: without the removal, a second import leaves two files and `read_client_secret`'s strict rule turns the import into an outage.

Note the direction of data flow: the *frontend* picks the path with `dialog.open()`, then passes the string to the host, which does the copy. The frontend never reads or writes the file itself, which is why `fs` is needed for the log and not for the credential.

- [ ] **Step 4: Implement `ui-log.ts`**

Append one line per record to `logs/ui.log` under app data, capping at 5 MB with the same shift-and-drop rule as the host. A screen that is broken because of an IPC failure produces exactly the log lines describing that failure, so this path must not go through the host.

- [ ] **Step 5: Implement the screen**

Four sections, in the order the master design names them: sign-in and client secret, log level, log files, database statistics. Level changes write to `meta` and the screen says in the text that the sidecar picks it up on next start — the UI does not imply otherwise. The log-files section's button calls the opener plugin's `openPath` on the `logs` directory.

- [ ] **Step 6: Route it**

`src/App.svelte`: replace the `{:else}` placeholder branch's `diagnostics` case with `<Diagnostics />`, leaving the other four stubs. Update the existing comment, which currently says the placeholders' content lands in Phase 4.

- [ ] **Step 7: Run the frontend gates**

Run: `pnpm format` then `pnpm lint:fix` then `pnpm lint` then `pnpm check` then `pnpm format:check` then `pnpm test` then `pnpm build`
Expected: all green.

- [ ] **Step 8: Assert the screen is in the bundle**

Run: `pnpm build` then `rg -c 'Diagnostics' dist/assets`
Expected: at least one hit. A `$state<T | null>(null)` that is never assigned is a non-reactive constant to the Svelte compiler: the branch folds away, scoped CSS and all, and the build still succeeds with the screen missing. This project has already been bitten by that in `merge.rs`.

- [ ] **Step 9: Commit**

```bash
git add src/routes/Diagnostics.svelte src/routes/Diagnostics.test.ts src/lib/ui-log.ts src/lib/ui-log.test.ts src/lib/ipc.ts src/App.svelte src-tauri/src/commands.rs
git commit -m "feat(diagnostics): sign in, set the level, and read the logs and database"
```

---

### Task 11: Home

**Files:**
- Modify: `src/routes/Home.svelte`, `src/lib/ipc.ts`, `src/lib/jobs.ts`
- Create: `src/lib/components/ProviderRow.svelte`, `src/lib/components/JobProgress.svelte`, `src/lib/jobs.test.ts` (new)
- Test: `src/routes/Home.test.ts` (extended), the two component tests (new)

**Interfaces:**
- Consumes: `get_auth_status` (Task 3's shape), `begin_auth` (Task 4), `start_sync`, `cancel_job` (Task 7), `get_status_counts`, `import_client_secret` (Task 10).
- Produces: `src/lib/jobs.ts` exports `export function subscribeJobs(handler: (p: Progress) => void): () => void`, listening on the Tauri event channel for the event name the host emits. `Progress` mirrors the Rust struct from Task 7: `{ jid: number; kind: string; state: "running" | "cancelling" | "cancelled" | "done" | "failed"; done: number; total: number | null }`.

- [ ] **Step 1: Write the failing component tests**

`ProviderRow.test.ts`: three cases, one per state — `signed_out` offers a sign-in button, `refreshable` offers none and names the expiry, `valid` offers none. `JobProgress.test.ts`: `cancelling` and `cancelled` read differently, and a `null` `total` renders no denominator rather than a zero.

`Home.test.ts` gains the Review Focus item 4 case: given `meta` reporting `sync_started_at` set and no running job, Home renders an interrupted state offering a re-run.

- [ ] **Step 2: Run them to verify they fail**

Run: `pnpm test`
Expected: FAIL — the components do not exist.

- [ ] **Step 3: Implement `ProviderRow`**

One provider, one of three states, one action. It must never render an empty branch for `refreshable`: that state is the common case after a day, and a blank row there reads as "signed out" to anyone not looking for the difference.

- [ ] **Step 4: Implement `JobProgress`**

State text, `done`, `total` only when it is not `null`, and a stop control whose label depends on `state` — "stop" while running, "stopping" while cancelling, absent otherwise.

- [ ] **Step 5: Implement the sync states on Home**

Four, each with its own text, and each reading a durable source rather than live memory where one exists. `sync_started_at` and `sync_last_completed_at` come from `meta` via a `get_meta` command added here; a frontend that asked the host "were you interrupted?" would be asking a question whose answer died with the process.

| State | How Home knows |
| --- | --- |
| never synced | neither meta key is set |
| in progress | a live `running` or `cancelling` job event |
| interrupted | `sync_started_at` is set, no job event, and `sync_last_completed_at` is not |
| synced, empty or not | `sync_last_completed_at` is set; the counts decide which of the two it is |

The last row is two states sharing a condition, and the counts are what separate them. That is the honest reason `sync_last_completed_at` exists: without it, a person who has never synced and a person whose library genuinely holds nothing read the same words.

- [ ] **Step 6: Wire sign-in**

`beginAuth(provider)` in `ipc.ts`, then a re-read of `get_auth_status` on completion. While it is in flight, Home polls `get_auth_status` on a timer and keeps it live — possible only because Task 2 made the loop concurrent, which is worth a comment because it is not obvious.

- [ ] **Step 7: Run the full frontend gate sequence**

Run: `pnpm format` then `pnpm lint:fix` then `pnpm lint` then `pnpm check` then `pnpm format:check` then `pnpm test` then `pnpm build`
Expected: all green.

- [ ] **Step 8: Assert both screens are in the bundle**

Run: `pnpm build` then `rg -c 'Diagnostics|Unknown album' dist/assets`
Expected: at least one hit for each.

- [ ] **Step 9: Commit**

```bash
git add src/routes/Home.svelte src/routes/Home.test.ts src/lib/components/ src/lib/jobs.ts src/lib/jobs.test.ts src/lib/ipc.ts
git commit -m "feat(home): sign in, sync, and show what state the mirror is in"
```

---

### Task 12: The manual check, the docs, and the push

**Files:**
- Modify: `AGENTS.md`, `README.md`

- [ ] **Step 1: Run all twelve gates**

Run, in this order: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run pytest --cov --cov-fail-under=80 --cov-report=term-missing`, `cargo fmt --all --check`, `cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc -p tidal2ytm-store --all-targets -- -D warnings`, `cargo test -p tidal2ytm-core -p tidal2ytm-ipc -p tidal2ytm-store`, `pnpm format:check`, `pnpm lint`, `pnpm check`, `pnpm test`, `pnpm build`.
Expected: twelve green. Do not skip `ruff check` and `ruff format --check` even though this phase changed almost no Python: a ruff release can add a rule inside the selected families and turn the gate red with no edit to any code.

- [ ] **Step 2: Run the host's own pair**

Run: `pnpm build` (so `dist/` exists), create `src-tauri/binaries/tidal2ytm-sidecar-<triple><.exe>` as an empty file if it is absent — the triple from `rustc -vV`'s `host:` field — then `cargo clippy -p tidal2ytm --all-targets -- -D warnings` and `cargo test -p tidal2ytm`.
Expected: green.

- [ ] **Step 3: Build the packaged application**

Run: `pwsh sidecar/build_sidecar.ps1`, then `pnpm build`, then `pnpm tauri build`.
Expected: the release binary plus the bundles. The placeholder from Step 2 is replaced by the real frozen sidecar.

- [ ] **Step 4: The check that cannot be written as a test**

Move any existing `tidal2ytm.db` aside rather than deleting it, then run `target\release\tidal2ytm.exe` from a directory that has no `data\` beside it. Confirm, in order:

1. Both providers show signed out, not an error — this is Review Focus item 1 and the defect Task 1 exists to close.
2. Diagnostics shows three log paths, all of which exist, and its button opens the directory.
3. Changing the level persists, and the text says it applies to the sidecar on next start.
4. Importing a `client_secret_*.json` enables YouTube Music sign-in; importing a second one replaces it and sign-in still works. This is Review Focus item 2.
5. Signing in to Tidal works with no terminal.
6. A sync reports progress with no denominator until the last page, then completes.
7. Kill the sidecar mid-sync, close the application, reopen it: Home shows interrupted with a re-run. This is Review Focus item 3.
8. Re-run; the mirror completes. This is Review Focus item 4.
9. `PRAGMA user_version` is 1 and `integrity_check` is ok.

Report what you saw, including anything that did not match. A manual check whose result is not written down is not a check.

- [ ] **Step 5: Update the documentation**

`AGENTS.md`: the project layout gains `jobs.rs`, `sync.rs`, and the data directory layout; the invariants section gains the three auth states, the grow-only mirror, and the `transferred` invariant as sync touches it. `README.md`: the GUI is the product now, so the usage section describes signing in from Diagnostics and syncing from Home rather than `tidal2ytm auth`.

Keep every existing rule that still holds. Edit, do not replace.

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs: describe the GUI's data directory, auth states and sync"
```

- [ ] **Step 7: Push and watch CI**

Run: `git push -u origin HEAD`, then `gh run list` and `gh run watch <run-id> --exit-status`.
Expected: green. The pre-push hook runs on this machine, so local green proves nothing. CI is Linux and this slice added a Windows-shaped capability and three plugins, which a Linux job validates differently.

Do not merge. The branch is finished when the whole plan is, not when this phase is.