# Phase 3 domain core port design

## Purpose

Phase 3 moves the matching and plan rules out of Python and into Rust, gives the application a database, and proves the port against a frozen record of the Python's own answers — before any GUI exists to depend on it. It is the largest single phase of the migration and the one with the most room to go quietly wrong, because a port that compiles, passes its own tests, and behaves differently from the code it replaced is the default outcome rather than the exceptional one.

This document is the design for Phase 3 only. The program-level design remains `docs/superpowers/specs/2026-09-29-desktop-gui-migration-design.md`, and where the two overlap the program design governs unless this document says otherwise in "Deviations" below.

## Deviations from the program design

The program design's Phase 3 paragraph ends: *"deletion of each piece of Python as its Rust counterpart lands with parity proven… The Python package ends this phase as a pure adapter: no scoring, no plan logic, no slugs."*

**That deletion does not happen in Phase 3.** The Python logic is retained until Phase 6, which is where `docs/superpowers/specs/2026-09-29-desktop-gui-migration-design.md` already schedules the TUI and CLI deletion. The two statements conflict, because `planning.py`, `review.py` and `transfer.py` all import the scoring and plan logic, so deleting it in Phase 3 would leave the project with no working transfer path for three phases while the GUI does not yet exist.

This was a deliberate decision, not an oversight, and the cost is stated rather than hidden: **for three phases, two implementations of the matching rules coexist.** The frozen golden corpus is what makes that safe instead of merely risky — it is a recorded, diffable statement of what the Python decided, so the two can be compared rather than assumed equal.

The program design's invariant that every derived value is computed in Rust applies, from Phase 3 onward, to the host's path. It does not constrain the CLI, which keeps using Python until it is deleted.

## Scope

### In scope

- The libsql storage substrate: schema, migration runner, store operations.
- The ported pure logic: text normalization, version tokens, track scoring, library search, album grouping and ranking, merge policy, and the track state machine.
- The typed candidate envelope, its cross-language contracts, and the sidecar normalization that produces it.
- The golden-fixture generator, the committed corpus, and the replay tests.
- The minimal host wiring: open and migrate at startup, plus two read-only commands.
- `plan_io.extract_video_id` is **not** deleted in this phase. Its Rust counterpart already exists as `core::video_id` with parity tests over every accepted URL form, and the Python original leaves with `plan_io.py` in Phase 6.

### Out of scope, and why

- **Transfer orchestration and the job manager.** Phase 5. The `transfer_attempt` table and the insert path exist here because the `transferred` invariant requires them; nothing drives them yet.
- **The Tidal→database ingest path.** Not assigned to any phase by the program design. Without it the database holds only rows a human or test created, which is why Phase 3's host surface is counts and a version rather than a library view.
- **GUI screens.** Phase 4. Phase 3 makes no change under `src/`.
- **Deleting any Python module.** Phase 6, per "Deviations".

## The port set

Derived by reading the modules rather than by trusting the program design's "roughly 800 lines" estimate. That estimate is sound: the enumerated set below is ~512 lines of module-level pure logic, plus the state machine, whose transitions currently live partly in `planning_merge.classify_track`, `transfer.py`, and `planning.py`'s match-action helpers.

| Module | Lines | Disposition |
| --- | --- | --- |
| `text.py` | 13 | Port to `core::text` |
| `version_tokens.py` | 18 | Port to `core::version_tokens` |
| `track_scoring.py` | 64 | Port to `core::track_scoring` |
| `planning_search.py` | 58 | Port to `core::search` |
| `models.py` | 92 | Becomes `core::model` types and enums |
| `album_matching.py` | 302 | Split; see below |
| `planning_merge.py` | 137 | Re-targeted onto the store |

### Pure modules, ported close to verbatim

`text.py`, `version_tokens.py`, `track_scoring.py` and `planning_search.py` have no I/O and no third-party imports. `planning_search.py` states this in its own docstring and the other three are trivially true. They port as functions over Rust types with the same names, so the fixtures name the same things on both sides.

`models.py`'s dataclasses become Rust structs and its two string enums become Rust enums whose serialized form is the string value, preserving the persisted representation exactly as the program design requires of `TrackStatus` and `MatchMethod`.

### The `album_matching` split

This module is not pure logic. It imports `requests` and `ytmusicapi.exceptions.YTMusicError` and calls `yt.search` and `yt.get_album`, interleaved with scoring in the same functions. It cannot be copied; it has to be **cut**.

The criterion is: *a function belongs to the pure half if it takes no YTM client and performs no I/O.* On that criterion the pure half is `group_by_album_id`, `_candidate_artist_names`, `_version_term`, `_album_meta_score`, `_sibling_year`, `_sibling_entry`, `_dedupe_details`, `_best_track_fit`, `_rank_candidates` and `_map_winner_tracks` — roughly 130 lines. The impure half is `_review_results`, `_try_fetch_album`, `_fetch_album_details`, `_harvest_sibling_candidates` and the search half of `resolve_album_group`, and it becomes sidecar work behind the `search.albums`, `search.songs` and `album.fetch` operations the program design already specifies.

The plan's first task confirms this boundary by reading the module, because the seam between `_fetch_album_details` and `_dedupe_details` is the one place where a fetch could be hiding behind what looks like a pure transformation. If it is, the fetch moves to the sidecar and the pure half is smaller than estimated here.

### Re-targeted rather than copied

`planning_merge.py` writes TOML row dictionaries. The Rust version writes database rows, so this is a re-targeting, not a transcription: `classify_track`'s decision becomes `core::merge` returning an action enum, and `insert_track`'s slug-dedup pass becomes two upserts against `name_key`. The slug cap, the `-2`/`-3` dedup and `slugs.py` itself leave the project entirely, as the program design specifies.

The `skip-transferred` arm of `classify_track` does **not** port as a caller-side check. It moves into the store's guarded write, described below.

### Not ported

- `slugs.py` (84 lines) — removed by the program design's schema decisions.
- `confidence.py` (10 lines) and `format.py` (6 lines) — Rich colour and `m:ss` display formatting. The GUI formats its own; porting them would carry a presentation dependency into the domain.
- `matcher.py:match_track` (117 lines) — a driver, not logic. Its query and summary builders port; the `yt.search` call becomes a sidecar operation and the ranking becomes `core::track_scoring`.

## Parity landmines

Two functions will fail a naive port. Both are named here because a plan that discovers them mid-task has already spent the task.

### The similarity ratio

`text.similarity` is `difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()`. That is **not** Levenshtein, **not** Jaro-Winkler, and **not** any of the similarity ratios the Rust ecosystem ships as a crate. `ratio()` is `2M / T` over the total size of difflib's recursively-found longest matching blocks, with a specific tie-break (earliest longest block wins) and a specific left-then-right extension of each block. `autojunk=False` disables difflib's heuristic that discards elements appearing in more than 1% of a sequence of 200 or more characters, which matters because normalization can produce strings that long.

Substituting `strsim` or any other crate would silently change every score in the application, and the result would still look plausible. This is a faithful port of difflib's algorithm, and the golden corpus is the arbiter of whether it is faithful.

### NFKD normalization

`text.normalize` is: NFKD decompose, encode to ASCII ignoring everything unencodable, lowercase, strip `[^a-z0-9\s]`, strip. The second step drops every non-ASCII code point *after* decomposition — so `é` decomposes to `e` plus a combining accent, the accent is dropped and `e` survives, while a character with no ASCII decomposition is dropped entirely. Approximating this as "strip non-ASCII first" produces different text for any input that decomposes, and `unicode-normalization` gives NFKD but not Python's exact encode-and-ignore semantics.

The corpus's deterministic Unicode sweep exists to catch exactly this.

## Crate architecture

Three Tauri-free crates, split by whether they touch a database. All three stay in `Cargo.toml`'s `default-members`, so a bare `cargo test` covers them and the `rust` CI job compiles all of them on a runner with no webkit.

### `crates/tidal2ytm-core`

Pure domain logic. **No `libsql`, no filesystem, no network, no Tauri.** Modules: `text`, `version_tokens`, `track_scoring`, `album_match`, `search`, `merge`, `model`, `candidate`, and the existing `video_id`.

The consequence that earns the split: `cargo test -p tidal2ytm-core` needs no database and no temporary files, so the parity suite is hermetic and fast, and a regression in a matching rule cannot be blamed on storage.

### `crates/tidal2ytm-store` (new)

Everything SQL: schema DDL, the migration runner, and the store operations. It depends on `tidal2ytm-core` for domain types and on `libsql`; it depends on no Tauri type. Splitting it out keeps the native database dependency out of the pure crate's test binary.

### `crates/tidal2ytm-ipc`

Unchanged except for one new dependency: it depends on `tidal2ytm-core` so it can deserialize the new operations' payloads into `core::candidate` types.

This direction is deliberate. The alternative — putting the envelope in `tidal2ytm-ipc` — makes the pure core depend on the crate that spawns processes and resolves interpreters, which drags sidecar-spawn code into a test binary with no use for it.

### The host

Consumes both. The host remains the sole writer; nothing else opens the database.

## Storage

### Opening

`libsql::Builder::new_local(app_data_dir().join("tidal2ytm.db"))`, which is Turso's embedded local-file mode over a SQLite-compatible file. No network, no token, no account, and the program design's production reset — "close, delete the file, reopen" — is literally true.

The program design's mention of `Builder::new_local(":memory:")` names this crate's API, so the storage technology was already settled by that document; what was confirmed is that it is the embedded file mode rather than Turso Cloud, since a hosted database would contradict the reset procedure and add an account dependency a single-user desktop application has no use for.

Every open executes `PRAGMA foreign_keys = ON`, because Turso requires it explicitly and SQLite defaults it off per-connection. Journal mode is left at the default: the host is the single writer in a single process, so WAL would be complexity nothing asked for.

### Migrations

`PRAGMA user_version` drives a `const MIGRATIONS: &[&str]` indexed by version. The runner reads the current version, then applies every migration above it **one transaction per migration, each carrying that step's own version bump inside it**. A version bump that outlives a rolled-back migration leaves a database that believes it is current when it is not; per-migration granularity means a failure partway leaves the earlier steps applied *and recorded*, so the next launch resumes rather than restarting. Re-running with no pending migrations is a no-op, and that idempotency is a tested property rather than an incidental one, because the host opens the database on every launch.

Schema, table names, columns, CHECK constraints and indexes are exactly those in the program design's Storage section. Singular table names, no ISRC columns, no stored counters, no slug columns.

### Store surface

Phase 3's operations, and nothing more:

- `open` / `migrate` / `schema_version`
- `status_counts` — `SELECT status, COUNT(*) … GROUP BY status`. This is the query that makes the program design's deletion of the recomputed `[meta]` counters sound: a relational count cannot drift out of sync with the rows it counts.
- Upserts of artist, album and track by `name_key`
- The 1:1 `match_confidence` upsert
- `candidate` inserts
- `transfer_attempt` inserts
- The guarded status write

### The `transferred` invariant

The program design states this as "not expressible as a database constraint" and requires that one Rust function own every track write, check the current status, and refuse to modify a transferred row except through an explicit review action that records a new transfer attempt.

It is implemented as exactly that: one write path reads the current status inside the writing transaction and refuses any change away from `transferred`, unless the caller presents an explicit review override, in which case the override also inserts the `transfer_attempt` row in that same transaction. The invariant therefore holds because there is no other way to write a track status, not because every caller remembers to check. Moving it out of `classify_track` is the whole point of the re-targeting.

## The candidate envelope

The sidecar normalizes YTM payloads into two small types, described in `contracts/` alongside the three that already exist and asserted by both the Python and the Rust suites:

- **`Candidate`** — a song: `video_id`, `title`, `artists: Vec<String>`, `album: Option<String>`, `album_track_num: Option<i64>`, `duration_sec: Option<i64>`.
- **`AlbumSummary`** — an album: `album_id`, `title`, `artist`, `year: Option<i64>`.

The domain core never sees a YTM field name. This follows from the program design's own reasoning that request/response shaping is the Python that cannot be ported without reimplementing two unaudited API clients — so normalization belongs on the Python side of the boundary, and the benefit is that a YTM field rename touches the sidecar and nothing else.

Changing the shape of `search.albums`, `search.songs` and `album.fetch` output is a breaking change to those operations, and it is safe only because nothing consumes them yet. Both sides' contract tests move in the same commit.

## The parity harness

### The generator

A committed Python script, run through `uv run`, that emits the corpus. It synthesizes everything: fictional artists, albums and tracks, invented numeric Tidal ids, invented 11-character video ids, synthetic durations and parenthetical qualifiers. **It never reads real catalogue data**, per the rule already recorded in `AGENTS.md`.

### Corpus shape

Two kinds of case, because they fail for different reasons.

**Discrete branches come from an enumerated matrix, not random sampling.** Qualifier sets (absent, identical, partially overlapping, disjoint), duration deltas on both sides of `DURATION_TOLERANCE_SEC`, single versus multi-artist credits, compilations, and each status transition. Every case is a labelled row in that matrix, so coverage is auditable by reading the generator rather than inferred from a seed, and a reviewer can see which combination nobody thought to test.

**Continuous values get a small deterministic sweep** aimed at where the two landmines live: empty strings after normalization, single characters, repeated characters, non-ASCII that NFKD decomposes, non-ASCII that does not, and strings long enough that `autojunk` would have mattered.

Files are JSON, one per ported module, under `crates/tidal2ytm-core/tests/fixtures/`, each a list of `{input, expected}` pairs.

### Replay

One Rust test per module reads its fixture, runs the Rust implementation over each input, and asserts deep equality against the expected value. On mismatch the failure names the case index and prints both sides, because a parity failure that does not show the offending case is a parity failure you cannot act on.

### Lifecycle and the rules that keep it honest

Regeneration is `uv run python crates/tidal2ytm-core/tools/generate_fixtures.py`, and **the resulting diff is the review artifact**: when an intentional rule change lands, the diff shows exactly which cases moved and by how much.

Two rules are load-bearing:

- **No test ever recomputes an expectation.** An expectation computed by calling the implementation under test, or by calling Python at test time, cannot fail for the reason the test exists. A Python rule change would move both sides and leave parity green while behaviour changed — a test that cannot fail, which `AGENTS.md` already forbids.
- **A snapshot of a Rust run is never committed as the oracle.** The corpus is generated from Python, once, and frozen.

**CI never regenerates the corpus.** The files are committed data, so CI structurally cannot bless a behaviour change.

In Phase 6 the generator is deleted along with the Python it reads, and the committed JSON remains as the frozen oracle — the same shape as `contracts/*.json`, which both suites assert and which has already survived this migration.

## Host wiring

The database opens inside `run()`, not `main()`, for the same reason the DEBUG log sink does: `main()` alone is an entry point that can lose everything the process is complaining about. `app_data_dir()` is created first, since the program design and the existing `sidecar_path` invariant both record that it does not exist on a first run. The store joins the existing `State` and lives as long as the process.

Two read-only commands are added — status counts and schema version — which is precisely the payload Phase 4's Diagnostics screen needs, and the minimum that proves the schema was created and migrated by the real application rather than by a test.

**The frontend is untouched.** No screens, no `src/` change, no new `invoke` wrapper.

## Testing

Five layers, each proving something the others cannot:

1. **Parity** — the fixture replay, per ported module. Hermetic: no database, no filesystem, no network.
2. **Store** — one `new_local(":memory:")` database per test. Covers migration from empty, migration idempotency on re-run, foreign keys actually rejecting an orphan insert, the `transferred` refusal including the review-override path, and `status_counts` agreeing with the rows written.
3. **Contracts** — both suites assert the new envelope JSON, per the existing precedent.
4. **Host** — the existing host tests plus open/migrate and the two new commands.
5. **End to end** — the packaged application creates its own database on first launch and reports real counts from it. This is the exit criterion, and it is the only layer that exercises libsql inside a packaged Tauri binary.

## Gates

The gate set is unchanged. Concretely:

- `tidal2ytm-store` joins `Cargo.toml`'s `default-members`.
- The `rust` CI job's clippy invocation gains `-p tidal2ytm-store`. Without this the new crate is compiled as a dependency of the host but never linted, which is the same class of omission as a filter entry left out.
- The `rust` job's filter already covers `crates/**`, so no path-filter change is needed.
- The generator is never run in CI, so no new CI dependency appears.

## Sequencing

One spec, one plan, six steps. Each step proves its own riskiest assumption before anything depends on it.

1. **Fixture harness and `text.rs`.** Build the generator and the replay test, then port `text.py` — the smallest module and the hardest parity case. This proves the fixture format, the difflib port and the NFKD pipeline before several hundred further lines are measured against them.
2. **Storage substrate and host wiring.** libsql dependency, schema, migration runner, store operations, host open/migrate, the two read-only commands. This lands in the first third rather than last, because whether libsql links and works inside a packaged Tauri application on two platforms is the one unknown that could invalidate this phase's foundation. CI has proved the host *compiles* on Linux; nothing has proved libsql works.
3. **Typed envelope and contracts**, with the sidecar normalizing the three operations' payloads into it.
4. **Remaining pure port** — `version_tokens`, `track_scoring`, `models` to types, `planning_search`.
5. **`album_matching`'s pure half**, now scoring against the envelope.
6. **Merge policy and state machine**, re-targeted onto the store, with `transferred` enforced at the write site.

## Exit criteria

Phase 3 is done when all of the following hold:

- Every ported function has fixture parity against the committed corpus, and the corpus covers the enumerated matrix rather than a sample of it.
- The difflib port and the NFKD normalization pass the Unicode and length sweep.
- `cargo fmt --all --check`, `cargo clippy` for all three crates with `-D warnings`, and `cargo test` for all three are green.
- The Python gates are unchanged and still green, because the CLI still works.
- The packaged application creates its own database, migrates it, and the read-only surface reports real counts from it.
- CI is green on ubuntu-latest with the `host` job genuinely compiling and testing the host crate.

## Risks

- **The difflib port diverges.** The most likely way this phase ships a regression. Mitigated by a faithful implementation and the corpus being the arbiter, but only if the Unicode and length sweep is actually run rather than written and skipped.
- **libsql does not link or behave inside a packaged Tauri app.** Unproven today. Mitigated by landing it in step 2 rather than step 12.
- **Two implementations drift.** Inherent to the coexistence decision. Mitigated by a frozen, diffable corpus — and honestly, only by that.
- **Normalization differences between Python and Rust.** NFKD plus ASCII-ignore has no single-crate equivalent. Mitigated by the Unicode sweep and by pinning the exact crate that provides NFKD.
- **The `album_matching` seam is wrong.** `_fetch_album_details` may hide I/O behind a pure-looking signature, making the pure half smaller than estimated and the sidecar half larger. Mitigated by confirming the boundary as the plan's first task rather than discovering it mid-port.
- **Scope creep into sync or transfer orchestration.** Both are explicitly excluded above, and the database having no ingest path is a known, accepted condition of this phase rather than an oversight.