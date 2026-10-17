# Phase 4a: Home and Diagnostics design

## Purpose

Phase 4 of the desktop GUI migration is six screens, three job kinds, and the plumbing they all sit on. This document is the first slice of that phase, and it exists to prove the plumbing on two screens before five screens are built on it.

The risky part of Phase 4 is not any one screen. It is a long-running job in the Rust host reporting `{done, total, label, current_track}` over a Tauri channel, cancellable cooperatively, with `cancelling` and `cancelled` as distinct states. If that shape is wrong, every screen built on it is wrong. Phase 2 was the same move one level down: prove packaged IPC on real hardware before eight hundred lines of domain logic land inside it. This is the same argument again, one level up.

The second thing this slice does is make the application *usable by a person*. The packaged app built at the end of Phase 3 rendered its window and reported both providers as signed out. That was not an auth failure; it was two halves of the application disagreeing about where user state lives, and it is closed here.

When this slice lands, a person can open the application, sign in to both providers from it, import the one Google credential file it needs, pull their Tidal favourites into the local mirror, watch the sync report progress, change the log level and see it apply, read database statistics, and find out from a log file why something broke. That is the whole of the promise here.

## Deviations from the program design

The master design is `docs/superpowers/specs/2026-09-29-desktop-gui-migration-design.md`. This slice departs from it in five places, each deliberate.

**Phase 4 is cut in two.** The master design's Phasing section already names the order — *"Home and Diagnostics first, proving the command and event plumbing. Then Library with its search, Match, Review, and Transfer"* — but treats it as one phase. The same section requires that *"each phase after the first two gets its own spec-to-plan cycle"*. Six screens in one cycle is not plannable, so 4a is this document and 4b is the rest. The rule is the design's own; only the unit of work changes.

**Sync is on demand, not automatic at startup.** The master design says the `sync` job *"Runs automatically at startup and on demand."* In 4a it runs on demand only. Startup sync lands in 4b with the Library screen that consumes the mirror. Firing it at launch populates data that nothing in 4a can show, and it spends the person's Tidal quota before they asked for anything.

**Sync upserts and never deletes.** The master design calls the job *"Reconcile the local library mirror from Tidal favourites"*. In 4a the mirror only ever grows: a track that is no longer favourited stays in the database, with whatever match and whatever status it had. The person this application is for does not un-favourite anything, so the behaviour costs nothing in practice, and it removes a class of bug that a partial fetch could cause. **This reopens.** If un-favouriting becomes real, deletion can only ever run against a fetch that completed every page, in one transaction at the end, and never against a `transferred` or `skip` row. That constraint belongs to the implementation that does it, not to this document.

**The sidecar's data directory becomes an explicit path rather than a function of the working directory.** See "Where user state lives".

**Two Tauri plugins are added to the frontend capability list**, which today is `core:default` and nothing else. See "The client secret".

## Scope

### In scope

- One data directory for all user state, owned by the host and named explicitly to the sidecar.
- The sidecar's read loop made concurrent, because `auth.begin` blocks on a human.
- `auth.begin` for both providers, so the application can sign a person in without a terminal.
- The `favorites.list` sidecar operation, paged.
- The sidecar's own refresh-and-rewrite helper, because `tidalapi` has none that fits.
- `auth.status` gaining a three-state model that distinguishes "needs a new sign-in" from "will refresh itself".
- The client-secret import path.
- The job machinery, with one job kind: `sync`.
- **Home**, real.
- **Diagnostics**, real.
- The host's log file, its rotation, the frontend's log file, and the level selector that drives both processes.

### Out of scope, and why

- **Library, Match, Review, Transfer.** 4b. They are the reason 4a exists, not part of it.
- **`search.albums`, `search.songs`, `album.fetch`, `library.add`.** Nothing in 4a calls them. Adding operations nothing invokes is speculative surface; each lands with the screen that needs it.
- **Automatic sync at startup.** 4b, with the screen that reads the mirror.
- **Deleting tracks on sync.** Deliberate, above.
- **The `match` and `transfer` job kinds.** The job machinery is built to hold them — the enum is `Sync` with two more variants to come — but they are 4b and Phase 5.
- **The resume banner.** The master design offers a resume-or-discard choice at Home for a job interrupted by a crash or a quit. 4a's only job is `sync`, which is idempotent on `tidal_id` and cheap to re-run: an interrupted sync leaves an incomplete mirror, and running it again completes it. A banner offering a choice between two equivalent actions is worse than no banner. It lands in 4b with `match`, where the job is expensive enough to be worth resuming.
- **A refresh action the person triggers.** Refresh happens when a network call needs it. See "Auth state has three states, not two".
- **Showing the authorisation URL in the application.** `auth.begin` opens the browser and blocks. The URL goes to the log and into the failure message. If a browser that fails to open turns out to matter, an in-app URL is a 4b addition.
- **Deleting the Python CLI.** Phase 6. Its gates stay green through this phase.

## Where user state lives

The application data directory owns everything transient. One directory, one owner, stated once.

```
%APPDATA%\dev.cobra.tidal2ytm\
├── tidal2ytm.db          the SQLite database
├── data\                 tidal_token.json, ytm_auth.json, client_secret_*.json
└── logs\                 the host log, its rotation, the frontend log
```

This is a change of principle, not a path constant. The state of play before it:

`tidal2ytm/paths.py:7` declares `DATA_DIR = Path("data")`, relative to the current working directory. The host spawns the sidecar with `current_dir` set to the application data directory — `src-tauri/src/lib.rs:66` — and its own comment says this is deliberate, because *"a token cache written by the sidecar and a row written by the host cannot drift apart"*. The two were indeed in one directory. They were simply the wrong directory: nothing ever wrote a token there, and the only sign-in path was the CLI, which writes to the checkout.

The result was a split that no gate could see. `sidecar_path::checkout_launch` sets the working directory to the checkout root, so a **development** build found the real token caches and reported signed in. `sidecar_path::resolve` returns `cwd: None` for a bundled binary, so a **packaged** build fell through to `app_data_dir()`, found an empty `data\`, and reported signed out. The database, meanwhile, was genuinely in application data the whole time. Two surfaces, one of them permanently lying.

The fix is that the working directory stops implying anything. `paths.DATA_DIR` becomes:

```
TIDAL2YTM_DATA_DIR if set and non-empty, otherwise Path("data")
```

and the host sets `TIDAL2YTM_DATA_DIR` to `<appdata>\data` in the sidecar's environment, creating it before the spawn the way the working directory is created today.

This is deliberately additive at the Python end. With the variable absent — every CLI invocation, every test, every hook — `DATA_DIR` is exactly `Path("data")` and the CLI is byte-for-byte unchanged. With it set, `TIDAL2YTM_DATA_DIR=%APPDATA%\dev.cobra.tidal2ytm\data tidal2ytm status` reads the GUI's token caches, so a person can sign in once and use both surfaces until Phase 6 deletes the CLI.

The sidecar's working directory stays what it is. It is no longer load-bearing for anything, which is the point; leaving it alone keeps the `os error 267` fix and the create-if-missing behaviour intact.

`search_debug.log` in the checkout's `data\` is a leftover from the Python CLI and is not migrated.

## Auth state has three states, not two

`auth.status` currently returns a boolean per provider, computed by an offline check of the token cache. That boolean is a lie for the case that will dominate: a Tidal access token expires in hours, and a YTM one in days, but both carry a refresh token that is good for months.

The evidence is already in the codebase. `auth.ytm_cache_valid`'s own docstring says *"a stored `refresh_token` (refreshable credentials stay valid offline even when expired)"* counts as usable. `auth.tidal_cache_valid` does not do this — it compares `expiry_time` against the clock and nothing else. A person signed in yesterday is reported signed out today, through no action of their own.

So the model becomes three states per provider, all computable offline:

| State | Meaning | What the GUI shows |
|---|---|---|
| `valid` | The access token is unexpired. | Signed in, with the expiry time. |
| `refreshable` | Expired, but a refresh token exists. | Signed in; a note that the session renews itself on next use. No action offered. |
| `signed_out` | No usable cache. | A sign-in button. |

`auth.status` stays an offline read. It performs no network call, blocks for nothing, and is safe to call on every Home mount.

### The sidecar refreshes, because tidalapi will not

The refresh happens where the network already is, which means the sidecar has to do it itself. That is not a design choice; it is what the library requires.

`tidalapi` 0.8.11 has no `load_oauth_token`. The relevant surface is two methods:

- `load_oauth_session(token_type, access_token, refresh_token, expiry_time, is_pkce)` sets the five fields and performs a `GET /sessions`. It **does not refresh** the access token despite what its name suggests, and it **does not persist** anything.
- `login_session_file(path, do_pkce, fn_print)` is the only call that loads, refreshes and rewrites. It cannot be used: its `save_session_to_file` writes `{token_type, session_id, access_token, refresh_token, is_pkce}`, which is missing `expiry_time`, `user_id` and `country_code`, so pointing it at `tidal_token.json` destroys a file whose six-key shape `auth.py` guarantees everywhere else. Worse, when `check_login()` fails it calls `login_oauth_simple`, which blocks on a human — inside `favorites.list`.

So the sidecar owns a small helper: load with `load_oauth_session`, and if the access token had lapsed and the call returned a fresh one, write the six keys back itself. It is roughly fifteen lines, it is the only writer of that file besides `auth.py`'s sign-in, and it is covered by a test that asserts the file still has six keys afterwards.

**A failed refresh deletes nothing.** The token file is left exactly as it was. A transient network failure is not grounds for destroying a refresh token, and a file deleted on one would mean signing in again by hand. The consequence is honest and small: `refreshable` is an optimistic claim, and the sync job corrects it by failing with `auth_expired`, at the moment it actually matters.

`ytmusicapi` rewrites its own file on refresh, so the two providers are genuinely asymmetric here. The sidecar's helper is Tidal's; YouTube Music needs none, and its `auth.begin` is what puts a fresh token on disk.

`contracts/auth_status.json` records the **signed-out** sample on purpose, and that stays true — it is the sample with the most `null` in it, which is what the frontend's `Widen` helper exists for. Its key set is authoritative and changes here: each provider gains a three-valued `status`, a `refreshable` boolean that says whether a sign-in button should appear at all, and YouTube Music gains a `has_client_secret` boolean for the reason given under "The client secret".

## The client secret

YouTube Music sign-in needs a Google Cloud OAuth client, which arrives as a `client_secret_*.json` file. `tidal2ytm/auth.py` reads it, refuses more than one match, and names both Google console locations when none is found. That behaviour is not changing; it is being reached from a GUI instead of a terminal.

The file exists today in the checkout's `data\`, and will not exist in application data on a machine that has never run the CLI. Nothing can conjure one. So the application asks for it.

**The path comes from a native file dialog, not a text field.** `src-tauri/capabilities/default.json` grants `core:default` and no plugin permissions, deliberately, so the frontend can neither spawn a process nor touch the filesystem. A text field would require granting the host arbitrary filesystem read, which is the posture that file exists to refuse. A dialog grant is scoped to the single file the person chose, which is the entire reason that plugin is written the way it is. `tauri-plugin-dialog` selects; the host copies.

**Copy, not reference.** The application must not depend on a file still sitting in a Downloads folder. The host copies the chosen file to `<appdata>\data\client_secret_*.json`, keeping its name, so the existing strict "exactly one match" rule in `auth.py` still means what it means.

**An import replaces.** Importing a second credential without this rule would leave two files in the directory and make YouTube Music sign-in fail with a message about multiple files — the import would make things worse, silently, until someone opened the folder. So: validate the chosen file first, and only on success remove every existing `client_secret_*.json`. On failure nothing is touched, so a failed import cannot break a working credential.

**The copy is validated as a file; the credential is not.** The host checks that what it copied parses as a JSON object, because a truncated or wrong file must be reported at import rather than discovered at sign-in, and because the host cannot honestly report a successful copy of something unreadable. What that JSON object *means* stays with `auth.py`, which already emits a specific message for a missing or unparseable credential. Two owners of "what a client secret looks like" would be a duplicate; one owner is the auth module.

**The missing-credential path is fixed on the way past, because it kills the sidecar today.** `auth.py`'s no-secret branch does `print(..., file=sys.stderr)` and then `sys.exit(1)`. `SystemExit` is a `BaseException`, so it escapes `runner.py`'s `except Exception` and takes the sidecar process down — and the host would see `sidecar_lost` and respawn into the same failure. So the credential requirement becomes one function that raises `ValueError`: the CLI entry converts it to a message and an exit code as it does today, and the sidecar's operation converts it to `bad_request`. The check that raises is shared; the two callers decide what a `ValueError` means.

**Tidal needs no equivalent.** Device authorisation carries no client file.

## The job machinery

One job kind exists after this slice. The machinery is built for three.

### Shape

A job is a Rust-side handle with an ID, a kind, a unit-of-work loop, and a progress channel. It is started by a Tauri command and observed by the frontend as an event stream over a Tauri channel. Every progress event the UI sees originates in Rust; the sidecar stays pure request/response and never streams progress, because Rust drives the loop and already knows what the progress is.

```
{ "jid": 3, "kind": "sync", "state": "running", "done": 120, "total": null, "label": "…" }
```

States are `running`, `cancelling`, `cancelled`, `done`, and `failed`. `cancelling` and `cancelled` are distinct because conflating them produces a progress bar that lies: `cancelling` means the flag is set and the loop breaks after the current unit; `cancelled` means the loop exited and what it committed is durable.

### One job

`sync` asks the sidecar for Tidal favourites **one page at a time** and upserts each page before requesting the next.

`favorites.list` therefore takes `{offset, limit}` rather than returning everything. That is what makes a page the unit of work and the largest durable checkpoint available: if the sidecar returned the whole list in one response, nothing would be durable until that single response landed, and the per-page-commit guarantee could not be tested.

**`total` is unknown until the fetch finishes.** Tidal's favourites endpoint returns no count. So `total` is `null` in every progress event until the last page is empty, and Home shows a running count with no denominator rather than inventing one. This is the same rule as everywhere else in the application: a number it does not know is not displayed as though it did.

It upserts artists, albums, and tracks. It never writes a track's status, never writes a match field, and never touches anything already transferred. That last part is the `transferred` invariant and it needs a store change to state correctly.

**Sync does not use `upsert_track`.** That operation's conflict clause writes `yt_video_id`, `yt_title`, `yt_artist`, `yt_album`, `yt_album_track_num`, `yt_duration_sec`, `match_method`, `confidence_overall`, and `review_reason` — the exact fields matching fills in. Using it on a re-sync would blank every match in the database and destroy every review note, which is the opposite of what sync is for. So the store gains **`upsert_track_source`**, which writes only the Tidal columns and never the YTM ones. Two functions over one table, with disjoint column sets and disjoint callers, is one honest owner of two different writes rather than a second owner of the same one.

**A track with no album gets a placeholder.** `SourceTrack` yields `album = ""` and `album_id = -1` when the track has no album, and `album` is unique on `(artist_id, name_key)` where `name_key` is the normalised name — so an empty name would collapse every album-less track by one artist onto a single album row. Sync therefore creates one placeholder album per artist for album-less tracks, and the UI renders it as "Unknown album". Nothing is dropped, and 4b decides how album anchoring treats those groups when matching arrives.

### Durability of the mirror

Home distinguishes four states, and one of them needs a durable source that 4a must create now.

The sync job writes `sync_started_at` into `meta` before its first page and clears it when the loop exits, whatever the reason. Home reads that key, not memory: a frontend that asked the host "were you interrupted?" would be asking a question whose answer died with the process. This is also exactly what 4b's resume banner will read, so it is written once and reused rather than invented twice.

### Cancellation

Cooperative between units. The flag is checked after each page commits. A page in flight is not interrupted, because there is nothing to interrupt it with. The GUI shows "stopping…" until the loop actually exits.

### Timeouts

Per-operation and owned by Rust, at the values the master design fixes: 30 seconds for fetch, no timeout on `auth.begin` because that operation waits on a human and a browser.

## The sidecar surface

The master design's complete surface is seven operations in six groups. This slice adds three of them.

| Op | Status in 4a | Purpose |
|---|---|---|
| `auth.status` | **changed** | Three states per provider, plus a client-secret flag. Computed offline. |
| `auth.begin` | **new** | Run one OAuth flow. Blocks on the human and a browser. |
| `favorites.list` | **new** | Raw Tidal source tracks, one page: `{offset, limit}`. |
| `search.albums`, `search.songs`, `album.fetch`, `library.add` | 4b / 5 | Nothing in 4a calls them. |

`REQUIRED_CAPABILITIES` in `crates/tidal2ytm-ipc/src/protocol.rs:19` grows to match. It is a startup gate, and that is the point: a host built against this document meeting a sidecar that predates `favorites.list` reports a startup error rather than failing mysteriously several minutes into a sync. The Python and Rust contract tests in `contracts/` grow with it, because the wire shape is the contract.

`favorites.list` returns raw source tracks and no derived value. Every score, every key, every status, every count and every ordering is computed in Rust. That invariant is what prevents a second implementation of the matching rules, and it is the line to hold in review.

The Python serving `favorites.list` is `tidal_source.get_favorite_tracks`, unchanged in what it maps. What changes is that it is now driven page by page rather than called once for the whole library, so its paging loop is split into a generator the sidecar can pull one page from.

### The read loop becomes concurrent

`runner.py` is strictly serial today: read a line, dispatch, write a response. `auth.begin` blocks on a human and a browser — minutes, by design. Under a serial loop, a person signing in would find that the sidecar answers **nothing**: not `auth.status`, which the host gives five seconds, and not a `favorites.list` already in flight.

That directly contradicts the master design's requirement that `auth.begin` be *"an `auth.begin` operation on the same sidecar instance the job was using, so there is no restart and no state loss"*. That requirement only holds if the sidecar can answer while something else is blocked.

So the loop dispatches off-thread, and writes to stdout behind a lock. The wire is already built for this: every response carries the request `id` it answers, which exists precisely so that two replies may cross. Nothing downstream has to change, because the host has always matched responses by id and never by arrival order. Both Python suites — the sidecar's and the package's — grow a case where two operations overlap and both answer.

## Screens

### Home

Home is the resume screen and the one a normal session needs. In 4a it carries the two provider rows, the plan progress breakdown, and the sync action with live progress. Everything else the master design gives Home — the refresh-on-startup, the resumable job banner, the start-match and start-transfer actions — depends on something 4b builds.

**Provider rows.** Two, one per provider, each showing the three auth states. `signed_out` gets a sign-in button, and for YouTube Music that button is preceded by the client-secret import when `has_client_secret` is false. `refreshable` shows the expiry and a note, no action. `valid` shows the expiry and nothing to do.

**Signing in.** Pressing the button calls `auth.begin` with that provider and waits. There is no timeout, and the UI says so, because the operation is waiting on a browser window that a person may not open for two minutes. Tidal opens its device URL; YouTube Music opens the browser against a loopback server. Both block inside the sidecar on code that already exists in `auth.py`. Because the read loop is concurrent, the rest of the application keeps answering throughout — Home re-reads `auth.status` on a timer and the count is live rather than frozen. When the operation returns, Home re-reads `auth.status`, which is authoritative for the first time in the session.

**Plan progress.** A status breakdown from `Store::status_counts()`, which already exists. In 4a with a freshly synced mirror every track is `pending` and the screen says so. That is an honest reading, not a stub: the numbers are real and the breakdown component is the one Match and Transfer will reuse.

**Sync.** One action, showing the job's state, its `done`, its `total` once known, and a stop control. Four states are distinguished, and the master design is explicit that this is not optional: *"the screen must distinguish 'not synced yet' and 'sync in progress' from 'synced and genuinely empty' — three states that look identical otherwise, and the first two are ordinary rather than exceptional."* The fourth is interrupted, read from `sync_started_at`, and offers a re-run — which for an idempotent job is the honest minimum version of the master's resume-or-discard choice.

Because sync never deletes, a track that is no longer favourited is still in the mirror. The three states remain reachable — a fetch that returns nothing genuinely leaves it empty — and the "synced and empty" reading is therefore truthful in every case where it appears.

### Diagnostics

Re-authentication, the log level, the log file paths with a button that opens their directory, and database statistics.

**Sign-in and client secret.** The two provider actions live here as well as on Home, because this is where a person goes when something is wrong with a provider. The client-secret import is here, shown whenever `has_client_secret` is false.

**Log level.** A selector over `LOG_LEVELS`, defaulting to `INFO`, persisted in the `meta` table, rejected at the setter rather than silently falling back. The host passes the value to the sidecar as `TIDAL2YTM_LOG_LEVEL` at spawn. One knob, both processes, no way for them to disagree.

Two consequences follow, and both are the kind that only show up after something has already gone wrong.

A level change reaches the sidecar on **next start**, not immediately, and the UI says exactly that rather than implying otherwise. Respawning a running sidecar to change a log level would discard in-flight work.

A respawned sidecar gets the **currently persisted** level, not the one captured at startup. `AppState` holds a frozen `SidecarConfig` so that a changed `TIDAL2YTM_SIDECAR_BIN` cannot swap the binary under a running application — that is a good invariant and it stays — but the level inside that frozen copy would then be stale, and a sidecar that crashed and came back would log at a level the person had already changed. So the environment is rebuilt from `meta` at spawn time, and the frozen copy keeps holding the binary and the arguments.

And the host reads the level **before** it opens its own log file. A sink installed first would write a misconfigured run's earliest records at the wrong level, and those records are the ones that explain the misconfiguration.

**Log files.** Three paths, listed, with one button that opens the containing directory. "Three" resolves as the host log, the host's rolling history, and the frontend log. The sidecar has no file of its own: its records go to stderr, which the host already drains and forwards into the host log tagged `proc=sidecar`. That is the mechanism the master design already specifies, and the host log and its history are the two entries it produces. The frontend log is capped at the same 5 MB; the master design caps only the host's, and an uncapped `ui.log` in a long-running desktop application is a defect rather than a style question.

**Database statistics.** Schema version from `Store::schema_version()`, plan counts from `Store::status_counts()`, and the database file's path and size on disk. The first two commands already exist as Tauri commands; the file size is a new, trivial read.

### States

Empty, loading, offline, auth-expired, interrupted, and no-data are designed per screen, not once globally. In 4a that means four distinct states in Home's sync area and three in its provider area, each with its own text, and one shared rule: no state renders as an empty list. An empty mirror and a mirror that was never fetched are different facts and say so.

## Logging

The master design's logging section is adopted as written, because Phase 2 already built half of it. What changes:

- **The host gains a log file** in `<appdata>\logs\`, size-capped at 5 MB with three rotations. `logging.rs` today installs a DEBUG stderr sink; that becomes the development floor and the file is the production sink. The rotation is hand-written in `logging.rs` rather than taken from a crate — the whole requirement is "rename the current file, keep three", and a dependency for four lines of file renaming is a dependency someone then has to keep patched.
- **The frontend gains a log file** through `tauri-plugin-fs`, scoped to the application data directory, capped the same way. It does not route UI records through the host, because when the UI is broken *because of* an IPC failure, the lines describing that failure are the ones a host-mediated path drops.
- **The level selector** is described under Diagnostics above.
- **Redaction is unchanged** and is the host's and the sidecar's shared rule: a recursive key-pattern denylist over the whole payload, applied on both sides, so it does not depend on the caller remembering.

## Testing

The master design's nine durable contracts are the project's list. Five are in reach of 4a, and they are the ones this slice adds:

| Contract | Defect it catches |
|---|---|
| The sidecar's data directory is passed explicitly, not inferred from the working directory | A packaged build reports signed out while a development build reports signed in, and nothing says why |
| A refresh leaves `tidal_token.json` with all six keys | The self-service refresh silently replaces a credential file with a shape the rest of the application cannot read |
| A failed refresh deletes nothing | A dropped connection destroys a refresh token and forces a manual sign-in |
| A `transferred` track is unchanged by sync, and its match fields survive | A re-sync blanks every match and every review note in the database |
| `auth.status` distinguishes refreshable from signed out | A person is told to sign in again because a token aged out |
| Two overlapping operations both answer | Making the read loop concurrent serialised it instead, and `auth.begin` once again blocked everything |

The first is the one that cannot be written as a unit test and must be a check a person performs: build the packaged executable, run it from a directory with no `data\` beside it, and see the signed-in state that a development build shows. That is the same class of check as the Phase 2 gate, for the same reason — CI is Linux, and this bug only appears in the bundled path.

The frontend gains the existing `pnpm` gates and nothing new. `svelte-check` covers the new components' types and accessibility; the component tests follow the existing pattern in `src/routes/Home.test.ts` and `src/lib/components/AuthStatus.test.ts`, asserting behaviour through public APIs and never exact colours, glyphs, or copy.

There is one test-shaped trap worth naming because it has already bitten this project once, in `merge.rs`: a `$state<T | null>(null)` that is never assigned is a non-reactive constant to the Svelte compiler, the branch folds away, scoped CSS and all, and `pnpm build` still succeeds with the card missing from `dist/`. Every screen this slice ships asserts that it renders what it imports.

## Gates

Unchanged. All twelve remain, including the Python suite: Python coexists until Phase 6 and its gates stay green until then. `cargo fmt`, `clippy` for the three libraries and the host, the Rust tests, the seven `pnpm` gates, `ruff check`, `ruff format --check`, `pyright`, and `pytest` with its coverage floor.

Building the host still requires `dist/` and the staged sidecar placeholder on disk, and any job or hook that compiles the host must create the placeholder first or it fails inside the build script before compiling a line of host code.

## Sequencing

Each step is independently verifiable and the order is a dependency order, not a preference.

1. **Data directory.** `paths.py` reads `TIDAL2YTM_DATA_DIR`; the host sets it and creates `<appdata>\data`. The split-sign-in bug is closed here and is provable before a line of GUI lands.
2. **The read loop goes concurrent**, with both Python suites extended for overlap. Nothing depends on it yet, and it is the change most likely to be got wrong.
3. **Sidecar operations.** `favorites.list` and `auth.begin`; `auth.status` gains its three states and the client-secret flag; the credential check stops exiting; the refresh helper lands; `contracts/` and `REQUIRED_CAPABILITIES` move together; the round-trip suite grows a case per operation.
4. **Store.** `upsert_track_source`, and a test that a transferred row and a matched row both survive a sync.
5. **Job machinery.** `sync` end to end, with progress, per-page commits, the `meta` key, and cancellation. No screen consumes it yet, so this is provable from a test or a command.
6. **Plugins and capabilities.** `tauri-plugin-dialog` and `tauri-plugin-fs`, with the smallest capability set that makes each work and no more.
7. **Host logging.** The file sink and its rotation, the level read before the sink opens, and the level selector persisted in `meta`.
8. **Diagnostics.** All four sections, including the client-secret import and its replace rule.
9. **Home.** Provider rows, sign-in, the progress breakdown, the sync action and its four states.
10. **The manual check.** Package, run, sign in, sync, read the logs, kill the sidecar mid-sync and re-run it.

Steps 6 through 9 are what a person looks at, and they are last so that step 1 is already proven when they first look.

## Exit criteria

- The packaged executable, run from a directory with no `data\` beside it, reports the person's real sign-in state — the same state a development build reports.
- A person can sign in to both providers from the application, with no terminal, and the rest of the application stays responsive while the browser is open.
- A refresh after the access token has lapsed happens on the next sync, leaves the token file with all six keys, and asks for nothing.
- A sync reports progress while it runs, stops when asked, and leaves an incomplete mirror if killed, which a re-run completes.
- A `transferred` track, and a track with a match and a review note, are both unchanged by a sync.
- Diagnostics shows three log paths that exist, opens their directory, and a level change that reaches a respawned sidecar.
- The log level, the plan counts, and the schema version read back correctly after a restart.
- All twelve gates green, and `gh run watch` green on the push.

## Risks

**The data directory change touches the Python that still ships.** It is three lines in `paths.py` with a fallback that preserves today's behaviour exactly, and the Python suite proves it, but it is the one edit in this slice that can break a surface nobody is looking at.

**Making the read loop concurrent is the most invasive change in the slice**, and it is a change to a file whose whole value is that it has never had a concurrency bug. Off-thread dispatch plus a locked write is simple in principle; the risk is in the tests, which have never made two operations overlap.

**The Tidal refresh helper is new code rewriting a credential file.** It exists because `tidalapi` will not do it safely, and the alternative is a person re-authenticating every few hours. It gets a test asserting the six-key shape survives, and it never deletes.

**Three states is a contract change with a frontend consequence.** `contracts/auth_status.json` gains keys, and `src/lib/types.ts` derives from it. A wrong sample there silently narrows a field type and stops compiling in a place that looks unrelated. The signed-out sample stays signed-out for exactly this reason.

**Two plugins is two permissions.** The capability list currently reads `core:default` and no plugin permissions, and that is a deliberate boundary rather than an accident. Adding `dialog` and `fs` to it is the first time the frontend gains any reach, and the grant must stay scoped to what is described here. If it turns out to need a wildcard, that is a design failure, not a configuration detail.

**Sync writes to the database for the first time in the application.** Phase 3 built the store and proved its invariants in tests; nothing has ever written a track through a real sync. `upsert_artist`, `upsert_album`, and `upsert_track_source` are exercised for the first time by this slice, in that order, which is the order the foreign keys demand.