# Desktop GUI migration design

Date: 2026-09-29
Status: approved design, pending implementation plan
Owner: tidal2ytm
Supersedes: `docs/tidal2ytm-desktop-ui-migration-preliminary-plan.md` (a preliminary plan, never an approved design)

## Purpose

Replace the Rich terminal UI with a desktop application that makes the album-and-track context behind every match, the competing candidates, and the uncertainty legible in a way a text viewport cannot. The tool's behaviour is already correct; this changes how a person sees and steers it. The Rich TUI and the argparse CLI are both retired — the GUI replaces the CLI entirely, not alongside it.

Success means: a person can sync their Tidal favourites, match them non-interactively, resolve the uncertain cases in a review screen that shows *why* a match was uncertain, transfer the result, close the application mid-flight, reopen it, and resume without losing or duplicating anything.

## Scope decisions

These were decided before any design work and constrain everything below.

- **Platform:** Windows first. CI is Linux-only, so every new layer must be testable headlessly on Linux from the first commit. A CI gate is never removed until the code it covers is deleted.
- **CLI:** deleted. The `review`, `transfer`, `status`, and `auth` subcommands go, along with the bare-invocation gateway menu.
- **Plan storage:** Turso (SQLite-compatible), accessed only from the Rust host.
- **ISRC:** removed from the codebase as part of this work, not deferred. YTM's ISRC data is not usable, so the feature does not work in practice and the columns and matching strategy go away permanently.
- **Application core:** Rust. The Python package becomes a thin Tidal/YTM API adapter.
- **Certainty:** an album-anchored clear winner — the existing album-group test (score ≥ `ALBUM_CERTAIN_THRESHOLD`, margin ≥ `ALBUM_MARGIN_EPS`) passes *and* that track found an in-album fit. No new thresholds are introduced.
- **Review alternatives:** top-N candidates are persisted at match time, but only when the match was not certain. A later live re-search from review is possible, so candidates live in their own table rather than a JSON blob on the track.

## Architecture

Three processes, with one rule that keeps them honest: **the sidecar answers questions about the outside world, and nothing else.**

```
Tauri host (Rust)
  • Turso connection and schema          — sole writer
  • domain core: scoring, matching policy, plan state machine,
    transfer orchestration
  • job manager
  • logging and redaction
  • sidecar supervision
  └── Tauri commands ⇄ Svelte 5 frontend

                  │  NDJSON over stdin/stdout
                  ▼

Python sidecar
  tidalapi and ytmusicapi sessions, token files, auth flows.
  No scoring. No plan. No database.
```

The Svelte frontend is presentation and user intent only. It holds no credentials, spawns no processes, and never touches the database.

### Why the core is in Rust

The reuse argument for a Python core is weaker than it appears. Of the existing Python, roughly 800 lines are pure logic worth porting (scoring, version tokens, text normalization, album grouping, merge policy, state machine). Roughly 4,000 lines — `planning.py` (1,842), `review.py` (1,170), `cli.py` (443), `keys.py` (368), `picker_rows.py` (172) — are terminal rendering and key dispatch. That code is **deleted, not ported**: no GUI reuses a Rich `Live` viewport or a raw-mode key loop.

Beyond the port size, two structural facts decide it. Turso's multi-process access to one file is an experimental sidecar mechanism, so the database must have a single writer; a Python core would place both the database and the domain logic behind IPC for every screen render. And matching scores every candidate of every track — roughly ten thousand comparisons for a five-hundred-track library — which is trivial in-process and absurd as IPC round trips.

The Python that survives is the part that cannot be ported without reimplementing two unaudited API clients: session construction, OAuth flows, request/response shaping, and the WEB_REMIX header patching that ytmusicapi needs.

### The sidecar surface

The complete surface is these seven operations in six groups. Nothing else crosses the boundary.

| Op | Purpose |
|---|---|
| `auth.begin` | Run one OAuth flow; blocks on the human and a browser. |
| `auth.status` | Which providers are usable, with expiry. Never token material. |
| `favorites.list` | Raw Tidal source tracks. |
| `search.albums`, `search.songs` | Raw YTM candidates. |
| `album.fetch` | Raw YTM album tracks. |
| `library.add` | Add one video ID; returns the outcome. |

Every *derived* value is computed in Rust: scores, similarity components, certainty, ranking, abstention, plan rows, statuses, counts, and ordering. This is the invariant that prevents a second implementation of the matching rules, and it is the line to hold in review.

Three consequences follow, and all three are deliberate:

- **The sidecar never streams progress.** Rust drives the match loop, so it already knows what the progress is. Every progress event the UI sees originates in Rust, and the sidecar stays pure request/response.
- **Cancellation is best-effort, and the UI says so.** `ytmusicapi` calls are synchronous, so an in-flight search cannot be interrupted. Rust marks the operation aborted and discards the result. The UI shows "stopping" until the call returns rather than pretending it stopped.
- **Credentials never leave the sidecar.** `auth.status` returns something like `{"tidal": {"valid": true, "expires_at": "..."}}`. Token files stay sidecar-owned.

## Storage

Turso/SQLite, single writer, `PRAGMA foreign_keys = ON` (Turso requires it explicitly), `PRAGMA user_version` for schema migrations, and a `meta` table for application state. Table names are singular.

```sql
CREATE TABLE artist (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,
  name_key  TEXT NOT NULL UNIQUE          -- casefold(name)
);

CREATE TABLE album (
  id           INTEGER PRIMARY KEY,
  artist_id    INTEGER NOT NULL REFERENCES artist(id),
  name         TEXT NOT NULL,
  name_key     TEXT NOT NULL,
  year         INTEGER,
  match_conf   REAL,        -- album-group winner score; NULL if unresolved
  match_margin REAL,        -- winner minus runner-up
  match_reason TEXT,        -- e.g. "Close album call"
  UNIQUE (artist_id, name_key)
);

CREATE TABLE track (
  id                 INTEGER PRIMARY KEY,
  album_id           INTEGER NOT NULL REFERENCES album(id),
  tidal_id           INTEGER NOT NULL UNIQUE,     -- track identity
  title              TEXT NOT NULL,
  artist             TEXT NOT NULL,
  duration_sec       INTEGER NOT NULL,
  track_num          INTEGER NOT NULL DEFAULT 0,
  disc_num           INTEGER NOT NULL DEFAULT 0,
  version            TEXT,
  status             TEXT NOT NULL
                     CHECK (status IN ('pending','needs_review','transferred','skip','failed')),
  yt_video_id        TEXT,
  yt_title           TEXT,
  yt_artist          TEXT,
  yt_album           TEXT,
  yt_album_track_num INTEGER,
  yt_duration_sec    INTEGER,
  match_method       TEXT NOT NULL
                     CHECK (match_method IN ('album','duration','fuzzy','manual','none')),
  confidence_overall REAL NOT NULL DEFAULT 0.0,
  review_reason      TEXT
);

CREATE TABLE match_confidence (        -- 1:1, the score breakdown
  track_id           INTEGER PRIMARY KEY REFERENCES track(id) ON DELETE CASCADE,
  summary            TEXT,
  title_similarity   REAL,
  artist_similarity  REAL,
  album_similarity   REAL,
  version_similarity REAL,
  duration_delta_sec REAL,
  track_num_match    INTEGER,
  album_coherence    REAL
);

CREATE TABLE candidate (               -- written only when the match is NOT certain
  id               INTEGER PRIMARY KEY,
  track_id         INTEGER NOT NULL REFERENCES track(id) ON DELETE CASCADE,
  rank             INTEGER NOT NULL,
  yt_video_id      TEXT NOT NULL,
  yt_title         TEXT NOT NULL,
  yt_artist        TEXT NOT NULL,
  yt_album         TEXT,
  yt_duration_sec  INTEGER,
  method           TEXT NOT NULL,     -- how this candidate was found
  score            REAL NOT NULL,
  summary          TEXT,
  UNIQUE (track_id, yt_video_id)
);

CREATE TABLE transfer_attempt (        -- written BEFORE the remote add
  id           INTEGER PRIMARY KEY,
  track_id     INTEGER NOT NULL REFERENCES track(id),
  yt_video_id  TEXT NOT NULL,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  outcome      TEXT NOT NULL
               CHECK (outcome IN ('started','added','already_present','failed'))
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

### Schema decisions

Five things are absent, each a deliberate removal rather than an oversight:

- **No `tidal_isrc`, no `yt_isrc`.** The ISRC removal is complete; these columns never enter the new schema.
- **No stored counters.** The TOML plan's `[meta]` block held `total_tracks`, `transferred`, `pending`, and the rest, recomputed on every write because a text file cannot count its own rows. `SELECT status, COUNT(*) … GROUP BY status` is always correct, so the recompute step has no successor and cannot drift out of sync.
- **No `match_id` slugs.** Slugs existed so `--artist <match_id>` could address a group from a shell. The GUI addresses albums by integer key, and `insert_track`'s slug-dedup pass becomes two upserts against `name_key`. `slugs.py` and the 15-character album cap leave the port entirely.
- **No tidal album name on `track`.** `album.name` is the join target, exactly as today. No denormalisation, no possibility of drift.
- **No backups or exports.** The per-write `transfer_plan.YYYYMMDD_HHMMSS.toml` copy existed because a hand-editable text file can be destroyed by a partial write. A transactional database makes that failure impossible, and nobody has user plans to preserve. Testing needs a fresh database, which `Builder::new_local(":memory:")` provides per test with no filesystem at all. A production reset is "close, delete the file, reopen" — the host is the only writer, so that is trivially correct and needs no code beyond the open/close path that already exists.

### The `manual` match method

A hand-pasted override is currently stored as `match_method = "none"` with confidence `0.0`, which is indistinguishable from "nothing was found". In a GUI where a human decision and an abstention both sit in `needs_review`, that conflation is a latent bug: the review queue cannot tell the user which rows are already their decision. `manual` is added to the enum and set on override. The review UI then stops asking you to re-approve your own override.

### Where the YTM side of a track lives

`track` holds both the Tidal fields and the accepted YTM fields rather than a separate `match` table with a foreign key. The match is at most 1:1 with its track, usually exactly 1:1, and behaves as a value object with no lifecycle of its own; a separate table would add a join to the hottest read path in the application and force a null row on every abstention. Where a match *does* have a lifecycle, it already has a table: `candidate` records the alternatives that were rejected, and `transfer_attempt` records the (track, video ID) pairs actually acted on. That is the history a `match` table would have provided, already captured by two tables that earn their place.

This is a judgement call rather than a law. Full timestamped, revertible override history would tip it, and nothing else in the design requires it.

## The two invariants

**`transferred` is sacred.** Not expressible as a database constraint. One Rust function owns every track write, checks the current status, and refuses to modify a transferred row except through an explicit review action that records a new transfer attempt. Matching never reconsiders a transferred track, transfer never reconsiders one, and sync never touches plan rows at all. This replaces `classify_track`'s `skip-transferred` arm at the write site rather than relying on callers to check.

**Invalid video IDs are rejected at entry.** Today `load_plan` rewrites a malformed ID to `""` and forces `needs_review` on load. That remains an input check at the two places IDs arrive — a pasted URL and a YTM response — because no CHECK constraint can express the accepted URL forms. Those forms are exactly the ones `plan_io.extract_video_id` handles today: a bare 11-character ID, and eight URL shapes. The behaviour ports to Rust with a parity test; the Python function leaves with `plan_io.py` in Phase 6.

## Protocol, jobs, and failure

The sidecar is a persistent child process spawned by the Rust host with `tokio::process`, speaking newline-delimited JSON over stdin and stdout. Rust reads the pipe line by line and never acts on a partial line. There is no port, no loopback HTTP server, and no handshake beyond `hello`.

```jsonc
// Rust → sidecar
{"id": 41, "op": "search.albums", "params": {"query": "…", "limit": 10}}
{"id": 42, "op": "cancel", "params": {"target": 41}}

// sidecar → Rust
{"id": 41, "ok": true, "data": {"albums": [...]}}
{"id": 41, "ok": false, "error": {"kind": "auth_expired", "message": "YTM token expired"}}
{"event": "hello", "protocol": 1, "capabilities": ["favorites.list", "…"]}
```

The host spawns the child directly rather than through `@tauri-apps/plugin-shell`. That plugin exists for JavaScript; the host is Rust, so the capability-permission layer would be pure overhead, and the frontend is thereby unable to spawn anything at all.

The `hello` handshake carries a protocol version and a capability list. The host refuses to proceed if a required operation is missing or the protocol version does not match, and reports that as a startup error rather than degrading into a job that fails mysteriously several minutes later.

`error.kind` is a closed enum, never prose: `auth_expired`, `rate_limited`, `not_found`, `upstream`, `bad_request`, `sidecar_lost`. Each maps to one remedy — only `auth_expired` prompts re-authentication, only `rate_limited` backs off. An unrecognised kind is a bug: it shows a generic message with the text intact, never a traceback.

### Jobs

Long work is a Rust-side job with a handle the GUI can observe and cancel, reporting `{done, total, label, current_track}` over a Tauri channel. Three kinds:

| Kind | What it does |
|---|---|
| `sync` | Reconcile the local library mirror from Tidal favourites. Runs automatically at startup and on demand. |
| `match` | Resolve album groups, then per-track fallback for whatever the group pass abstained on. |
| `transfer` | Add every eligible track to the YTM library. |

Each job commits **per unit of work** — per album group, per transferred track — never once at the end. This is the departure from `transfer.py`, which mutated in memory and saved at the end: a GUI will be closed mid-transfer by a person who found something else to do, and the rule is that a killed application never loses a completed unit.

**Resume is offered, not performed.** A job interrupted by a crash or a quit is recovered from the database, not from memory. On arrival at Home, the application shows a banner naming the job, its progress, and a resume or discard choice. Automatically restarting an expensive operation the moment the window opens is jarring, and it fires an operation without consent.

**`auth_expired` pauses; it does not fail.** A five-hundred-track match that dies at track 340 because a refresh token lapsed is a bad outcome. The job enters `paused_auth`, the GUI offers re-authentication inline, and the job resumes at the track it stopped on. Re-authentication is an `auth.begin` operation on the same sidecar instance the job was using, so there is no restart and no state loss.

**Timeouts** are per-operation and owned by Rust: 30 seconds for search and album fetches, 15 seconds for `library.add`, and no timeout on `auth.begin`, because that operation waits on a human and a browser. A timed-out operation yields `upstream`, which is retryable; a `library.add` that times out leaves its transfer attempt open rather than being retried, precisely because the write may have landed.

**Cancellation** is cooperative between units, and the UI distinguishes the two states because conflating them produces a progress bar that lies. `cancelling` means the flag is set and the loop breaks after the current group. `cancelled` means the loop exited and partial results are committed and durable. An in-flight `library.add` is never interrupted.

**Sidecar crash** is EOF on stdout. Rust fails every in-flight request with `sidecar_lost`, respawns with bounded backoff — three attempts, then stop and prompt the user — and re-runs `auth.status` before reporting healthy. A crash and respawn must never present a logged-out application as ready. Because each job commits at its own unit boundary, at most one album group is lost, and the job is resumable.

**Shutdown** is closing stdin; the sidecar's read loop sees EOF and exits. No shutdown op, no flush protocol, nothing to get wrong.

## Screens

A Vite SPA with a Svelte 5 small client-side router. No SvelteKit: there is no server, so file-based routing, SSR, and an adapter concept would all be unused machinery. Six screens, mapped to the workflow rather than to the modules being replaced, with a persistent left rail as the only navigation.

**Home** is the resume screen and the one a normal session needs. Auth state for both providers as a list — valid, expired, or never, with expiry time — plan progress as a status breakdown, a refresh action for the library mirror, and actions to start a match or a transfer. A resumable job banner appears here when one exists.

**Library** is the Tidal favourites mirror, as a single virtualized list with a group-by control. The three picker modes (flat, artist, compilation) were a workaround for fitting a text viewport; a group-by control is the honest equivalent. The mirror syncs at startup, so the screen must distinguish "not synced yet" and "sync in progress" from "synced and genuinely empty" — three states that look identical otherwise, and the first two are ordinary rather than exceptional. Search is a first-class feature, not a convenience: incremental with a debounce, matching title, artist, album, and year; case- and diacritic-insensitive; substring rather than prefix so partial words find partial tracks; a visible result count; the matched span highlighted in each row; and an explicit affordance when the filter is what is making the list look empty. Results rank exact-ish title prefix matches first, then everything else in the existing plan order. Selection lives in component state, not the database — a half-made selection is not durable state.

**Match** is a job view: the current album group, tracks resolved so far, abstentions with their reasons, and a live list of `needs_review` accumulating as the job runs, so uncertainty is visible while it builds rather than only as a final verdict.

**Review** is where the design effort belongs, and where the goal of making candidates and uncertainty legible actually lands. Two panes: a queue of tracks needing attention on the left, the selected track on the right. The detail pane shows the Tidal side and the accepted YTM side as a diff, so a mismatch in title, version qualifier, or duration is visible rather than described. Below it, the ranked candidates with their score components broken out as a per-component stacked bar rather than one opaque number, so that a 0.82 which failed on version similarity reads differently from a 0.82 which failed on duration. Actions: accept, accept a different candidate, paste any YouTube URL, skip, or leave unresolved. Each decision is one command and persists immediately, matching today's review behaviour. There is no optimistic local state, because a decision that can be lost is worse than a decision that is slow.

Empty, loading, offline, auth-expired, interrupted, and no-candidate states are designed per screen, not once globally. "No candidates" and "candidates all scored badly" are different screens.

**Transfer** transfers everything eligible. Scope is determined before the operation starts, by which tracks are eligible — not by a scope selector. A confirmation dialog states the exact count and the breakdown of what is included and excluded. Progress is per track, with failures listed individually and resumable.

**Diagnostics** holds re-authentication, the log level, the three log file paths with a button to open their directory, and database statistics.

Layout, hierarchy, and interaction are refined during implementation with the `design-taste-frontend` skill, in dark mode only. No TUI keyboard heritage is carried forward: there is no muscle memory to honour, and the GUI is designed on its own terms to be intuitive, efficient, and pleasant.

## Transfer and recovery

The whole area is about one sentence: **a remote write and a local status are two separate facts, and the application must never confuse them.**

**Eligibility is computed, never assumed.** One query before the job starts returns the eligible set with a reason for every exclusion. `transferred` and `skip` are excluded silently, because that is the normal case and a list of four hundred finished tracks teaches nothing. `needs_review` tracks are eligible only when the person has opted in, which is a checkbox on the transfer confirmation dialog whose state persists in `meta` and replaces the old `--include-needs-review` flag. The exclusion is stated in the dialog rather than hidden, so an opt-in is always a visible decision rather than a remembered setting. A track with an empty or malformed video ID is excluded with a reason, and it needs Review rather than a retry.

**The per-track sequence**, with a transaction boundary per track:

1. Insert a `transfer_attempt` row with `outcome = 'started'` and the video ID recorded.
2. Commit. The attempt is durable before the remote call.
3. Call `library.add`.
4. On success, update the track to `transferred` and close the attempt in one transaction.

Step 2 is the entire design. A crash between steps 2 and 4 leaves a row with `finished_at IS NULL` — a question, not a lie. Everything else the application stores is a fact.

**Reconciliation is read-only.** At startup, the count of open attempts is computed and Home shows a dismissible banner naming the affected tracks, with links into Review. Nothing is rewritten automatically and nothing is re-added automatically, because those are the same class of move as silently replacing an approved destination ID. Checking the YTM library side later is possible if it becomes necessary.

**Outcomes** follow `add_track_to_library`'s existing contract: a missing add token means the track is already in the library, which is a success recorded as `already_present`, not an error. A `failed` outcome leaves the track at `pending` so a later run retries it. A timeout is not a failure; the attempt stays open, because the write may have landed.

**Retry policy** for `rate_limited` and `upstream` is bounded exponential backoff within the job, three attempts, after which the track is `failed` with the reason recorded. `auth_expired` pauses the job rather than failing tracks, as described above.

## Logging

One sink per process, three sources, all of it logged, behind levels, with `INFO` as the default.

**The host owns its own log file**, plus a rolling history beside it. A desktop application may be launched and left running for long stretches, so files are size-capped at 5 MB × 3 rather than kept forever as they are for short CLI runs today.

All three log files live in a `logs/` directory inside the platform application data directory, not beside the executable. A packaged build has no meaningful working directory, and the current relative `logs/` path is a property of running from a source checkout.

**The sidecar cannot write to the host's file**, because its stdout is the IPC channel. Sidecar log records go to **stderr**, and the host captures the child's stderr and forwards each line into the host's log, tagged `proc=sidecar`. The protocol stays free of multiplexed log events, and child stderr — otherwise discarded — becomes the sidecar's log. In a development build it also means running the binary from a terminal shows the sidecar's own output directly.

**The frontend writes its own log file**, through `tauri-plugin-fs` scoped to the application data directory, and does not route UI log records through the host. When the UI is broken *because* of an IPC-layer failure, the log lines describing that failure are exactly the ones a host-mediated path would drop. Three files rather than one is the correct trade: each remains useful when the other two are not, and a diagnostics screen lists all three paths and opens the containing directory.

**Correlation** is what makes three interleaved processes readable. Two identifiers, both already in the design: `rid`, the IPC request ID, logged on both the host side and the sidecar side so one exchange is a greppable pair; and `jid`, the job ID, present on every line a job emits so a five-hundred-track match reads as one contiguous story. Track-scoped lines also carry `tidal_id` and `album_id`, because "track 340" is a position in a job while `tidal_id` is identity.

**Levels.** `INFO` records milestones a person would want if something went wrong. `DEBUG` records the full trace. There is no level at which logging is skipped.

| Source | INFO | DEBUG |
|---|---|---|
| Lifecycle | Sidecar spawn, exit, restart; `hello` and protocol version; auth state transitions | Full capability list |
| IPC | Op, `rid`, `jid`, outcome, duration, error kind | Request and response payload shape |
| Upstream API | Which provider, which op, result count, rate-limit signals | Response payload shape |
| Matching | Per album group: winner, margin, decision (certain / abstained / fallback) | Full ranked candidate table with every similarity component |
| Decisions | Every abstention with its reason; every override with the ID chosen | The score breakdown that produced it |
| Database | Schema version, migration applied, plan counts on commit | Every statement with bound parameters |
| Transfer | Per track: attempted, then `added` / `already_present` / `failed` | Response detail |
| UI | Screen entered and left, job started / stopped / cancelled, user operations | Component state transitions |

The database row is the one worth defending. Logging every statement at `DEBUG` is what makes "why does the application think this track is transferred" answerable, which is a class of question the TOML plan cannot answer at all.

### Redaction

Redaction is split by category, because a single uniform rule is either too weak to be safe or too strong to be useful.

- **Credentials: never, in any process.** Any key matching `token`, `secret`, `cookie`, `authorization`, `bearer`, `refresh`, `access`, `api_key`, `client_secret`, or `session` is stripped recursively wherever it appears. The filter is a recursive key-pattern denylist over the whole payload, applied on both the Rust and Python sides so it does not depend on the caller remembering to call it.
- **Authentication payloads: shape only.** `auth.*` never logs values, on the principle that those are the payloads where a miss would be catastrophic and where `auth.status`'s booleans already carry the debugging value.
- **Everything else: real values.** Candidate video IDs, titles, artists, durations, every similarity component, every score. This is the matching-debugging payload, and it is the reason the log exists at all — strictly more than today's logs carry, because per-candidate detail never reached a file.

Upstream error messages are the indirect path: `ytmusicapi` and `tidalapi` both place request URLs in exception text, and those URLs carry authentication. Every error message therefore passes the redaction filter on its way into a log record regardless of where it originated. The user sees a message; the log holds the full record; neither ever holds a credential.

**Level control** was flag, then environment variable, then `INFO`. With no CLI, the GUI offers a level selector in the diagnostics view, persisted in `meta`, defaulting to `INFO`. The host passes the level to the sidecar as an environment variable at spawn, which works even if the sidecar dies before a byte of IPC — unlike an op-based handshake. One knob, both processes, no way for them to disagree. Invalid values are rejected at the setter rather than silently falling back.

## Testing

The CI constraint is binding: every change to a gate is additive, and an existing gate keeps its trigger until the code it covers is deleted. The Python suite never goes red; it shrinks from 356 tests to adapter tests, which is a deletion rather than a failure.

**The domain core is a plain Rust library crate with no Tauri dependency.** This is an architectural requirement, not a testing preference: `cargo test` must compile and run on a Linux container with no webkit, no display server, and no application bundle. The Tauri shell is a thin layer over it. If the domain core imported Tauri, GUI code would have to be written to satisfy CI, which is precisely how CI breaks.

**Parity is established with golden files, not a live comparison.** Because the Python logic still exists during the port, fixtures are generated from it: tables of real-shaped Tidal and YTM pairs with their scored candidates, album-group decisions, merge actions, and resulting plan rows, serialised as JSON. Rust tests assert against those. This beats running both implementations side by side for two reasons: it needs no Python at test time, so it keeps working after the Python is deleted, and it pins outputs rather than mechanism, so a legitimate refactor inside Rust does not break it. During the port the fixtures are regenerable from Python; afterwards they are the durable contract. The ISRC removal happens first precisely so the fixtures are generated from code that no longer contains ISRC.

**Rust gates:** `cargo test`, `cargo clippy -- -D warnings`, `cargo fmt --check`. **Frontend gates:** `svelte-check`, ESLint, Prettier. `svelte-check` covers TypeScript errors in components plus Svelte compiler and accessibility warnings, playing the role `pyright` plays here; ESLint covers cross-file rules `svelte-check` has no opinion on. Mirroring `pyright` + `ruff check` + `ruff format` on the Python side, these are three gates for three distinct failure classes rather than redundancy.

**Tool policy: fix what a tool can fix, fail on what it cannot.** Formatters (`prettier --write`, `ruff format`, `cargo fmt`) rewrite and continue, in hooks and in CI alike; CI additionally runs `--check` as a backstop for anyone who bypassed hooks, and a failure there is a bug in the hooks rather than a style offence. Linters (`eslint`, `ruff check`, `clippy`) run `--fix` first and then fail on whatever remains, so mechanical corrections do not block and a real finding is still never silent. Type checkers never auto-fix; a type error is a decision, not a formatting artefact.

**Git hooks are extended, not replaced.** The existing `pre-commit` (ruff, ruff-format, check-toml, hygiene) and `pre-push` (pyright, pytest with the 80% coverage gate) stay exactly as they are for as long as the Python they cover exists. `prettier --write` and `eslint --fix` join the commit hook; `cargo fmt`, `clippy --fix`, and `cargo test` join the push hook alongside the Python gates, because a Rust push that skips tests is exactly the hole worth closing. The 80% coverage gate carries over to the ported Rust core and is dropped only when the code it measures is deleted.

**Nine durable contracts, each naming the defect it catches.** This list is the test plan, and it is a ceiling rather than a starting point: a test that cannot state a specific defect does not get written. Cosmetic properties, value-changed-from-last-time assertions, broad smoke tests, and redundant coverage are all excluded.

| Contract | Defect it catches |
|---|---|
| A transferred track is immutable | A re-match silently overwrites an approved destination |
| Eligibility excludes `needs_review` without opt-in | Unresolved tracks are shipped to the library |
| Attempt row written before the remote call | A crash loses the fact that a library write happened |
| IPC handles unknown op, bad params, unknown `error.kind` | One malformed sidecar response kills the job |
| Sidecar crash respawns and re-checks auth | The application presents as logged in when it is not |
| Golden scoring fixtures | Port drift silently changes which track matches |
| Video ID validation across all accepted URL forms | A garbage ID reaches the database or the library |
| Decisions survive restart | A review decision lives only in memory |
| A transferred track is never re-added | Duplicate entries accumulate in the library |

**Opt-in authenticated tests** are marked and skipped unless credentials are present: real search, a real album fetch, and one real `library.add` against a throwaway playlist. Never in CI, which has no credentials, where a real library write would be an incident.

**Packaging is validated by generation.** CI can build a Linux bundle; it cannot prove that a Windows installer starts a sidecar with the correct target triple, and no job will imply otherwise. The Phase 2 gate is a person running the packaged executable and watching a sidecar request go round — a real check, performed once, early, where failure is cheap.

## Phasing

Seven phases, each ending in something that runs. The sequence front-loads the riskiest unknown: Phase 2 proves packaged IPC on real hardware before any domain logic is ported. Everything after it is work whose shape is already known.

This document is the program-level design. It is too large for a single implementation plan, and pretending otherwise would produce a plan nobody can hold. The plan written from this spec covers **Phases 1 and 2 only** — the ISRC removal and the packaged IPC vertical slice. Each subsequent phase gets its own spec-to-plan cycle once the previous one has landed and proved out, because Phase 3 onward depends on what Phase 2 actually demonstrated about packaged sidecar startup.

**Phase 1 — ISRC removal.** Self-contained and ships within the branch. Removes ISRC from models, `tidal_source`, `matcher`, `planning_merge`, `album_matching`, the review display, `confidence.is_certain` (which dies with it), tests, fixtures, the README, and the two stale `AGENTS.md` lines. The test deletions here are the only legitimate test deletions in this project, and they remove retired behaviour rather than coverage. Every remaining matching rule keeps its tests, and those tests become the fixtures the Rust port is measured against.

**Phase 2 — Packaged IPC vertical slice.** The gate. A Tauri window that spawns the packaged Python sidecar, completes a `hello` handshake, calls `auth.status`, and renders the result — plus the domain core crate existing as a Tauri-free library with a passing `cargo test`, because that structure is what keeps CI sane and it needs proving before eight hundred lines land inside it. Exit criteria: the Windows executable starts a packaged sidecar and a request goes round; the same handshake test runs headlessly on Linux CI; `cargo test`, `clippy`, `fmt`, and the unchanged Python gates are all green. If this fails, the topology is reconsidered here rather than after a full port.

**Phase 3 — Domain core port.** Schema and migrations, the ported pure logic (scoring, version tokens, text normalization, album grouping, merge policy, state machine), golden fixtures generated from the Python, and deletion of each piece of Python as its Rust counterpart lands with parity proven. No GUI work. The Python package ends this phase as a pure adapter: no scoring, no plan logic, no slugs.

**Phase 4 — GUI, in dependency order.** Home and Diagnostics first, proving the command and event plumbing. Then Library with its search, Match, Review, and Transfer. Dark mode throughout, one screen merged at a time, each usable on arrival.

**Phase 5 — Transfer and recovery hardening.** Attempt-before-remote, per-track transactions, the open-attempt banner, resume, backoff, and the cancellation states. Parts of this land with the Phase 4 transfer screen; this phase is where the crash and rate-limit paths are tested properly.

**Phase 6 — Retire the TUI and CLI.** Delete `planning.py`, `review.py`, `keys.py`, `picker_rows.py`, `cli.py`, and `plan_io.py`, along with the `rich` and `readchar` dependencies. Rewrite `AGENTS.md` for the new architecture and its gates, and the README for the application. The Python gates are dropped here, in the same commit, after the code they cover is gone.

**Phase 7 — Packaging.** Windows installer, sidecar bundling with correct target triples, first-run experience.

The version is **1.0.0**. Deleting the CLI is a breaking change to the package's entire public surface.

## Risks

**Packaged sidecar startup on Windows is the largest unknown**, and Phase 2 exists to find out cheaply. Frozen Python binaries have their own failure modes — antivirus false positives, slow first-run extraction, DLL search paths — and none of them are visible from a Linux CI job.

**Port drift is the second.** Eight hundred lines of scoring logic reimplemented in another language is exactly the situation where a test that asserts "the value differs from before" is the only thing standing between a silent behaviour change and a working application. The golden fixtures are the mitigation, and they are generated from the Python rather than written by hand, which is the whole point.

**Three processes is more moving parts than one.** The single-writer constraint on Turso, the `finished_at IS NULL` recovery path, and the sidecar respawn logic are each individually simple and collectively a new failure surface that does not exist today. Logging is what makes it diagnosable, which is why it is specified in this much detail before a line of it is written.

## Out of scope

MusicBrainz, acoustic fingerprinting, new match modes, new services, a browser-hosted server, and any use of real catalogue data as test fixtures. There is no user data migration because there are no user plans, and no compatibility shim for the removed CLI.
