# Phase 3 domain core port implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the matching and plan rules out of Python into Rust, give the application a SQLite database through libsql, and prove the port against a frozen record of the Python's own answers.

**Architecture:** `tidal2ytm-core` stays pure and gains the ported modules plus a committed golden-corpus replay suite. A new `tidal2ytm-store` crate owns all SQL. `tidal2ytm-ipc` depends on core so the typed candidate envelope can be deserialized without core depending on the crate that spawns processes. The host opens and migrates the database inside `run()` and exposes two read-only commands; `src/` is untouched.

**Tech Stack:** Rust 2024 (rustc 1.98.1), `libsql` 0.9.30 with `default-features = false, features = ["core"]`, `unicode-normalization` 0.1.25, `serde` 1.0.229 / `serde_json` 1.0.151 (workspace-pinned), uv-managed CPython 3.14.7 for the fixture generator, pnpm 11.28.2.

**Spec:** `docs/superpowers/specs/2026-10-07-phase-3-domain-core-port-design.md` (Phase 3). Program-level design, which governs where the two overlap: `docs/superpowers/specs/2026-09-29-desktop-gui-migration-design.md`.

## Global Constraints

- `tidal2ytm-core` contains no `libsql`, no filesystem, no network and no Tauri. `cargo test -p tidal2ytm-core` runs with no database and no temporary files.
- One Rust function owns every write of `track.status`. No other statement assigns `status`, and `status` is never touched by the track upsert.
- A write that moves a row away from `transferred` requires an explicit review override, and that override inserts the `transfer_attempt` row **in the same transaction**.
- `TrackStatus` and `MatchMethod` serialize to the exact persisted strings, so the serde name and `as_str()` agree with the `CHECK` constraint literals in the schema DDL.
- Schema DDL is copied verbatim from the program design's Storage section. No ISRC columns, no stored counters, no slug columns, singular table names, and `PRAGMA foreign_keys = ON` on every open.
- **No test recomputes an expectation.** An expectation computed by the implementation under test, or by calling Python at test time, cannot fail for the reason the test exists. The corpus is generated from Python once and frozen.
- **A Rust-computed value is never committed as an oracle.** CI never regenerates the corpus; the JSON is committed data, so CI structurally cannot bless a behaviour change.
- Fixtures live at `crates/tidal2ytm-core/tests/fixtures/*.json`, one file per ported module, each a list of `{input, expected}` pairs. Regeneration is `uv run python crates/tidal2ytm-core/tools/generate_fixtures.py`, and **the resulting diff is the review artifact**.
- Fixture data is fictional: invented artists, albums, tracks, numeric Tidal ids, 11-character video ids, synthetic durations and qualifiers. Never real catalogue data.
- Comments explain present state and why, never change history. Rust uses rustfmt defaults (4 spaces). Prose in comments and docs uses Canadian spellings.
- Tests assert behaviour through public APIs with structural checks. Never assert exact user-facing copy, whitespace or ordering that carries no contract.
- `libsql` is pinned to the exact version in `Cargo.toml`; `cargo update` moves `Cargo.lock` only. `Cargo.lock` and `pnpm-lock.yaml` are committed.

## Review Focus

Five input classes or conditions the spec implies that no single task's tests obviously cover. Each line names the input, the behaviour a reasonable person would expect, and the task whose tests pin it.

1. **A title made only of characters that `text.normalize` drops entirely** (a non-Latin script with no ASCII decomposition). Expected: the track does not crash and does not match anything; today it silently vanishes from both search paths because both the containment test and the ratio operate on the same empty string. Pinned by Task 5 (`core::search`).
2. **A title longer than 200 characters** — a compilation name or a description field mis-parsed as a title. Expected: a near-identical pair still scores high. With difflib's default `autojunk=True` the space character exceeds the 1% heuristic and scores collapse toward zero. Pinned by Task 1's length sweep.
3. **Two albums by one artist whose names differ only by case or punctuation** ("Song" vs "song!"). Expected: one album row, not two. `name_key` is described in the DDL as `casefold(name)`, but the string that identifies an album elsewhere in this project is `text.normalize`, which also strips punctuation and folds Unicode. Pinned by Task 2.
4. **A database file whose `PRAGMA user_version` is higher than this build knows about** — a downgrade after using a newer build. Expected: a named error at startup naming both versions, not a panic, not a silent no-op that leaves the schema unmigrated. Pinned by Task 2.
5. **A remaster: the source has a version qualifier and the candidate does not, or vice versa.** `version_tokens.qualifier_similarity` returns `0.4` when exactly one side has qualifiers, and `track_scoring` weights that component at `0.15`, so a perfect title and artist match loses `0.09` of the overall score and can land below `CONFIDENCE_THRESHOLD`. Expected: the track lands in `needs_review` rather than being silently transferred. Pinned by Task 5.

## Deviations from the spec's letter

Planning found four places where the spec does not match the code as written. Each is decided here, and each needs the reviewer's eye.

1. **`search.albums`, `search.songs` and `album.fetch` are not dispatched in Phase 3.** The spec says their output shape changes, but they do not exist: `sidecar/tidal2ytm_sidecar/ops.py` is 70 lines, implements only `auth_status`, and the sidecar has no YTM client. Phase 3 fixes their *shapes* (the normalizers, the contracts and the Rust types) so Phase 4 can implement the calls without a second contract change. Consequently `protocol::REQUIRED_CAPABILITIES` **stays `["auth.status"]`** — adding the three names would make the handshake fail against a sidecar that does not advertise them.
2. **A third contract type, `AlbumDetail`.** `_best_track_fit` and `_map_winner_tracks` consume an album's fetched tracklist, and `_dedupe_details` and `_harvest_sibling_candidates` consume its `other_versions` shelf. There is nowhere else for that shape to be fixed, so `AlbumDetail { summary: AlbumSummary, tracks: Vec<Candidate>, other_versions: Vec<AlbumSummary> }` is added alongside the two types the spec names.
3. **`AlbumSummary.artist` is singular, which degenerates two of the ported rules.** `_album_meta_score` takes `max(similarity(source.artist, n) for n in names)` over an artist *list*, and `_dedupe_details` keys on the tuple of those names. With one string the maximum becomes a single comparison. The corpus therefore exercises single-artist albums, and the generator records in a comment that multi-artist album matching is unexercised through the envelope and moves to Phase 4, where a real client can supply the full list.
4. **`str.casefold()` has no `std` equivalent.** `_dedupe_details` builds its title key with `casefold().replace("&", " and ").split()`, and Rust's `to_lowercase` differs from Python's `casefold` on `ß`, `ς` and `İ` (`ß` stays one character instead of becoming `ss`). The port uses `to_lowercase` and no new dependency is added. All album titles in the corpus are ASCII, so parity holds; a production album title containing those three characters would partition differently in Rust than in Python. Recorded as Review Focus item 3's sibling and named in the generator comment beside deviation 3.
5. **`matcher.py`'s `_build_query` and `_build_fuzzy_summary` are not ported.** The spec lists them as porting; both exist only to serve the fuzzy driver, and the fuzzy driver cannot exist until Phase 4 has a YTM client. Porting them now writes two functions with no caller, which is dead code by definition. They move with their driver.
6. **The spec's sequencing step 2 is split into Tasks 2 and 3.** The spec bundles "storage substrate and host wiring" into one step. Storage and host wiring are separately reviewable — a reviewer could accept the store and reject the wiring — and splitting them keeps each task's test cycle inside one crate boundary, which is what the task-right-sizing rule asks for. The spec's reason for landing storage early, that libsql-in-a-packaged-app is the one unknown that could invalidate the foundation, is preserved: it is still the second task.

---

### Task 1: Fixture harness and `core::text`

Proves the fixture format, the difflib port and the NFKD pipeline before several hundred further lines are measured against them.

**Files:**
- Create: `crates/tidal2ytm-core/tools/generate_fixtures.py`
- Create: `crates/tidal2ytm-core/tests/fixtures/text.json` (generated, committed)
- Create: `crates/tidal2ytm-core/tests/parity_text.rs`
- Create: `crates/tidal2ytm-core/src/text.rs`
- Modify: `crates/tidal2ytm-core/src/lib.rs` (add `pub mod text;`)
- Modify: `crates/tidal2ytm-core/Cargo.toml`

**Interfaces:**
- Consumes: nothing. This is the first task.
- Produces:
  - `pub fn normalize(value: &str) -> String` — in `core::text`.
  - `pub fn similarity(a: &str, b: &str) -> f64` — in `core::text`, returns `0.0` when either normalized side is empty.
  - `crates/tidal2ytm-core/tests/fixtures/text.json` — a JSON list of `{"input": …, "expected": …}`. `normalize` cases carry `input` as a string and `expected` as a string. `similarity` cases carry `input` as a two-element array `[a, b]` and `expected` as a number.
  - A reusable pattern for Tasks 5, 6 and 7: one generator module per ported module, one fixture file per ported module, one `tests/parity_<module>.rs` per ported module.

- [ ] **Step 1: Read `tidal2ytm/album_matching.py` and confirm the pure/impure split before writing any Rust**

Read the module. The spec's criterion is *a function belongs to the pure half if it takes no YTM client and performs no I/O*, and it names the pure half as `group_by_album_id`, `_candidate_artist_names`, `_version_term`, `_album_meta_score`, `_sibling_year`, `_sibling_entry`, `_dedupe_details`, `_best_track_fit`, `_rank_candidates`, `_map_winner_tracks`.

The one seam to check is `_fetch_album_details` / `_dedupe_details`: confirm `_dedupe_details` reads only already-fetched `detail` dictionaries and performs no fetch of its own. If it does fetch, the pure half is smaller than the spec estimates and Task 6's surface shrinks.

Write the confirmed pure-half list into the `//!` doc of `crates/tidal2ytm-core/src/album_match.rs` now, as a comment, so Task 6's implementer and reviewer both see the decision. Do not create that file until Task 6 — record the list in the generator's module docstring instead, and cross-reference it from `album_match.rs` in Task 6.

Expected: the spec's list is confirmed. **If it is not, stop and report which function moved sides before continuing.**

- [ ] **Step 2: Add the module and its dependencies**

In `crates/tidal2ytm-core/Cargo.toml` add `unicode-normalization = "0.1.25"` to `[dependencies]` and `serde_json = { workspace = true }` to a new `[dev-dependencies]` section. In `crates/tidal2ytm-core/src/lib.rs` add `pub mod text;` alongside the existing `pub mod video_id;`.

Run: `cargo build -p tidal2ytm-core`
Expected: compiles with a `text` module that is empty, which the next steps fill.

- [ ] **Step 3: Write the generator's shared helpers and the `normalize` sweep**

Create `crates/tidal2ytm-core/tools/generate_fixtures.py`. It runs through `uv run python` from the repo root, imports `tidal2ytm.text` and `tidal2ytm.track_scoring` and the other Python modules as later tasks need them, and writes one JSON file per module to `crates/tidal2ytm-core/tests/fixtures/`. Give it a `write_fixture(module: str, cases: list[dict[str, object]]) -> None` helper that writes `crates/tidal2ytm-core/tests/fixtures/<module>.json` with `json.dumps(cases, indent=2) + "\n"`, creating the directory if needed.

Its `normalize` sweep must cover, as individual labelled cases: the empty string; a single character; a whitespace-only string; a string with punctuation only; a string that lowercases to nothing; ASCII text; accented Latin that NFKD decomposes (`é` → `e`); accented Latin that does **not** decompose (`ø`); a non-Latin script with no ASCII decomposition (`한`); a mixed string of decomposing and non-decomposing non-ASCII; a string of 250 repeated spaces around a token, and a 250-character token, which is the length at which difflib's `autojunk` heuristic would have applied; a string with tabs and newlines; and a string that is entirely a combining mark.

Each case is an explicit row in a list. Do not sample randomly — the matrix must be auditable by reading the generator.

- [ ] **Step 4: Generate `text.json` and confirm the expected values come from Python**

Run: `uv run python crates/tidal2ytm-core/tools/generate_fixtures.py`
Expected: `crates/tidal2ytm-core/tests/fixtures/text.json` is written and `git status` shows it as untracked. Every `expected` in it is the value `tidal2ytm.text.normalize` returned; the generator calls Python and never reimplements the rule.

- [ ] **Step 5: Write the failing parity test**

Create `crates/tidal2ytm-core/tests/parity_text.rs`. It reads the fixture from `Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/text.json")`, deserializes into `Vec<FixtureCase>` where `FixtureCase { input: serde_json::Value, expected: serde_json::Value }`, and for each `normalize` case asserts `text::normalize(input.as_str().unwrap()) == expected.as_str().unwrap()`. On mismatch the panic names the case index and prints both sides with `{:?}`.

Comment the test with why the fixture is committed data: `// The corpus is generated from Python and frozen; CI never regenerates it, so a rule change in either language shows up here as a diff rather than as a test that cannot fail.`

Run: `cargo test -p tidal2ytm-core --test parity_text`
Expected: FAIL to compile — `text::normalize` does not exist yet.

- [ ] **Step 6: Implement `normalize`**

In `crates/tidal2ytm-core/src/text.rs` write `pub fn normalize(value: &str) -> String` with the module `//!` doc stating why the pipeline is NFKD first and ASCII-ignore second.

The pipeline, in this order, is: `unicode_normalization::UnicodeNormalization::nfkd(value)`, then for each resulting `char` keep it if it is ASCII and drop it otherwise, then `to_lowercase()`, then drop every character that is not `a`-`z`, `0`-`9` or ASCII whitespace, then `trim()`.

The second step is the landmine and needs a comment explaining it: decomposition happens *first*, so `é` becomes `e` plus a combining accent, the accent is dropped by the ASCII filter and `e` survives, while a character with no ASCII decomposition is dropped whole. Filtering non-ASCII before decomposing produces different text for every input that decomposes, and the sweep in Step 3 is what catches that mistake.

Note that Python's `str.strip()` with no argument strips whitespace; Rust's `trim()` strips Unicode whitespace. After the filter only ASCII space can remain, so the two agree on every input this function can produce.

- [ ] **Step 7: Run the parity test and confirm it passes**

Run: `cargo test -p tidal2ytm-core --test parity_text`
Expected: PASS, every case green.

- [ ] **Step 8: Prove the sweep catches the wrong order**

Temporarily change `normalize` to drop non-ASCII before decomposing (that is, map the input through the ASCII filter and only then apply NFKD), run the parity test, and confirm it fails on the accented-Latin case. Then restore the correct order and confirm it passes again.

This step is the point of the sweep. If the wrong order passes, the sweep does not cover the landmine and the generator must be extended before continuing. Report the failing case name in the commit message.

- [ ] **Step 9: Extend the generator with the `similarity` matrix and regenerate**

Add cases to the generator's `text` section whose `input` is `[a, b]`. Use an enumerated matrix, not a sample: both empty; one empty and one not; identical strings; a single character changed in the middle; a single character appended; a transposition of two adjacent characters; a repeated character run of length 1, 5 and 50 in one and both operands; one operand a prefix of the other; one operand a suffix of the other; one operand a substring of the other at the start, the middle and the end; both operands above 200 characters and identical except for one character; both operands above 200 characters and completely different; a pair that differs only by a space in one place; a pair where one side is empty **after normalization** (which is different from being empty before it).

For each case `expected` is the value `tidal2ytm.text.similarity` returned.

Run: `uv run python crates/tidal2ytm-core/tools/generate_fixtures.py`
Expected: `text.json` now carries both the `normalize` and `similarity` sections, and `git diff` shows only added `similarity` cases — the `normalize` expectations must not move, and if they do the generator changed the rule rather than the code.

- [ ] **Step 10: Write the `similarity` half of the parity test**

Extend `parity_text.rs` with a second loop over the `similarity` cases, asserting `text::similarity(&input[0], &input[1]) == expected` as an exact `f64` comparison.

Comment why exact equality is sound here rather than an epsilon: the implementation reduces to two integers, the total number of matched characters and the total length, and divides in the same order Python's `difflib` does. Both sides therefore produce the same IEEE-754 double, and an epsilon would hide exactly the divergence the test exists to find.

- [ ] **Step 11: Implement `similarity` as a faithful port of difflib**

Add `pub fn similarity(a: &str, b: &str) -> f64` to `core::text`. Normalize both sides; if either is empty return `0.0`; otherwise return the ratio.

The algorithm's specification is CPython's own `difflib.py` in the interpreter that runs `uv run python`. Locate it with `uv run python -c "import difflib; print(difflib.__file__)"` and read `SequenceMatcher` — specifically `find_longest_match`, `get_matching_blocks`, `get_opcodes`, `ratio` and `_calculate_ratio`. Implement from that source.

Three things in it are easy to get wrong and each deserves a comment:

- The index map it builds records the **last** position each element occurs at, not the first.
- When two blocks are the same length, the **earliest** one wins, compared by position in `a` first and then position in `b`.
- `autojunk=False` must be honoured. With the default, any element appearing in more than 1% of a sequence of 200 or more characters is treated as junk and discarded, which for normal titles means the space character, which collapses long near-identical pairs toward zero. Review Focus item 2.

Compute the final value as `2.0 * matched as f64 / total as f64`, where `matched` is the summed length of the recursively-found matching blocks and `total` is `a.len() + b.len()`. Using Python's exact expression and order is what makes Step 10's exact comparison valid.

Do not use `strsim`, `levenshtein`, `rapidfuzz` or any other crate. The program design's Phase 3 statement that every derived value is computed in Rust is satisfied by computing it in Rust; it does not license changing which function is computed.

- [ ] **Step 12: Run the parity test and the crate's gates**

Run: `cargo test -p tidal2ytm-core`
Expected: PASS, including the pre-existing `video_id` tests.

Run: `cargo fmt --all --check`
Expected: no output.

Run: `cargo clippy -p tidal2ytm-core --all-targets -- -D warnings`
Expected: no warnings.

- [ ] **Step 13: Record the corpus command in `AGENTS.md`**

Add one line to the project's Quality gates list, next to the other `cargo` commands, reading: the golden corpus is regenerated with `uv run python crates/tidal2ytm-core/tools/generate_fixtures.py`, CI never regenerates it, and the resulting diff is the review artifact for any intentional rule change. One sentence is enough; do not restate the design.

- [ ] **Step 14: Commit**

```bash
git add crates/tidal2ytm-core/Cargo.toml crates/tidal2ytm-core/src/lib.rs crates/tidal2ytm-core/src/text.rs crates/tidal2ytm-core/tools/generate_fixtures.py crates/tidal2ytm-core/tests/fixtures/text.json crates/tidal2ytm-core/tests/parity_text.rs Cargo.lock AGENTS.md
git commit -m "feat: port text normalization with a frozen Python-generated corpus"
```

The commit message body must name the case that failed in Step 8, so the reviewer can see the sweep is live rather than decorative.

---

### Task 2: The `tidal2ytm-store` crate

Lands the storage substrate early because whether libsql links and works inside a packaged Tauri application is the one unknown that could invalidate the phase's foundation.

**Files:**
- Create: `crates/tidal2ytm-store/Cargo.toml`
- Create: `crates/tidal2ytm-store/src/lib.rs`
- Create: `crates/tidal2ytm-store/src/schema.rs`
- Create: `crates/tidal2ytm-store/src/migrate.rs`
- Create: `crates/tidal2ytm-store/src/store.rs`
- Modify: `Cargo.toml` (add the crate to `members` and to `default-members`)
- Modify: `crates/tidal2ytm-core/src/lib.rs`, `crates/tidal2ytm-core/src/model.rs` (create the enums and the confidence struct)
- Modify: `Cargo.lock`
- Modify: `.github/workflows/ci.yml`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: nothing from Task 1 except the crate's existing `thiserror` convention.
- Produces:
  - `core::model::TrackStatus` — `Pending, Transferred, Skip, Failed, NeedsReview`, deriving `Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash`, with `pub fn as_str(self) -> &'static str` and `impl FromStr`.
  - `core::model::MatchMethod` — `Duration, Fuzzy, Album, NoMatch, Manual`. The variant is named `NoMatch`, not `None`, so that a `match` on it cannot shadow `Option::None`; `as_str()` and `FromStr` both return and accept `"none"`.
  - `core::model::ConfidenceBreakdown` — `overall: f64` plus `title_similarity`, `artist_similarity`, `album_similarity`, `version_similarity`, `duration_delta_sec`, `album_coherence` all `Option<f64>` or `Option<i64>` as in the Python, `track_num_match: Option<bool>`, `summary: Option<String>`. Derives `Debug, Clone, PartialEq`.
  - `store::Store`, `store::StoreError`, `store::SCHEMA_VERSION: i64`, `store::TrackRow`, `store::NewCandidate`, `store::ReviewOverride`.
  - `store::Store::open(path: &Path) -> Result<Store, StoreError>`, `open_in_memory()`, `migrate()`, `schema_version()`, `status_counts()`, `upsert_artist()`, `upsert_album()`, `upsert_track()`, `upsert_match_confidence()`, `insert_candidate()`, `insert_transfer_attempt()`, `write_status()`.

- [ ] **Step 1: Add the crate skeleton and confirm the libsql open call compiles at the pinned version**

Create `crates/tidal2ytm-store/Cargo.toml` with `tidal2ytm-core = { path = "../tidal2ytm-core" }`, `serde_json = { workspace = true }` and:

```toml
libsql = { version = "0.9.30", default-features = false, features = ["core"] }
```

Comment why the features are narrowed in the file itself: libsql's default set is `[core, replication, remote, sync, tls]`, which drags the whole Turso network stack into a single-user desktop application that never contacts a server.

Add the crate to `members` and to `default-members` in the root `Cargo.toml`, so a bare `cargo test` covers it and the `rust` CI job compiles it on a runner with no webkit.

Write `crates/tidal2ytm-store/src/lib.rs` with a crate `//!` doc saying what the crate owns (all SQL, the schema and the migration runner) and what it deliberately does not (no Tauri type, no business rules) and a `Store::open` that calls the libsql open API on a path.

Run: `cargo build -p tidal2ytm-store`
Expected: it compiles, or it fails with a "not found" naming the open call. The expected call is `libsql::Builder::new_local(path)?.build().await?` followed by `db.connect()?`, which returns a `libsql::Connection`.

If that is not the API at 0.9.30, fall back in this order and record which one worked in the file's `//!` doc: `libsql::Database::open_with_opts`, then `libsql_client::local::Client` via `Client::from_config`, whose sync variant exposes `in_memory`, `transaction` and `tx.commit`. Do not add a second database dependency to work around this.

- [ ] **Step 2: Write the schema**

In `crates/tidal2ytm-store/src/schema.rs` write `pub const MIGRATIONS: &[&str]` with one element containing the DDL copied **verbatim** from the program design's Storage section: the `artist`, `album`, `track`, `match_confidence`, `candidate`, `transfer_attempt` and `meta` tables with their columns, `CHECK` constraints, `UNIQUE` clauses and the two inline comments naming what `match_conf`/`match_margin`/`match_reason` and `rank` mean.

Do not add, rename or reorder anything. In particular there are no ISRC columns, no stored counter columns, no slug columns, and `name_key` appears on `artist` and `album` only — `track` is keyed by `tidal_id`, which the spec's phrase "upserts of artist, album and track by `name_key`" compresses imprecisely.

Add the module `//!` doc stating that this is the program design's DDL with no local edits, so a diff here means the program design changed.

- [ ] **Step 3: Write the migration runner**

In `crates/tidal2ytm-store/src/migrate.rs` write `pub async fn migrate(conn: &libsql::Connection) -> Result<(), StoreError>`.

It reads `PRAGMA user_version` into an `i64`. If that value is greater than `MIGRATIONS.len() as i64`, return `StoreError::UnknownSchemaVersion { found, known }` — Review Focus item 4 — with a doc comment saying a newer build wrote this file and the error must name both versions rather than proceeding.

Otherwise, for each migration at index `i` whose version `i + 1` is above the current version, and in ascending order: begin a transaction, `execute_batch` the DDL, set `PRAGMA user_version = <version>` **inside that transaction**, then `commit()`.

Two things must be commented because getting either wrong is silent:

- `PRAGMA user_version` cannot take a bound parameter, so the version is formatted into the statement text. It is derived from an array index, so there is nothing to inject.
- libsql's `Transaction` **rolls back when it is dropped**; `DropBehavior::Rollback` is the default. An explicit `commit()` is mandatory, and its absence discards the migration and the version bump together. Do not write the bump outside the transaction either: a bump that outlives a rolled-back migration leaves a database that believes it is current when it is not. Per-migration granularity is what makes a failure partway resumable, because the earlier steps stay applied *and recorded*.

- [ ] **Step 4: Write the store's open path and the foreign-key pragma**

`Store::open(path)` creates the parent directory if it does not exist, opens the connection, then executes `PRAGMA foreign_keys = ON` before returning. Comment that Turso requires the pragma explicitly and that SQLite defaults it off **per connection**, so a connection opened without it enforces nothing.

`Store::open_in_memory()` opens `":memory:"` and applies the same pragma, so every store test gets a fresh database with foreign keys actually on.

- [ ] **Step 5: Write the type declarations**

`pub struct Store { conn: libsql::Connection }`, plus `pub struct TrackRow` carrying `tidal_id: i64`, `album_id: i64`, `title: String`, `artist: String`, `duration_sec: i64`, `track_num: i64`, `disc_num: i64`, `version: Option<String>`, `yt_video_id`, `yt_title`, `yt_artist`, `yt_album` as `Option<String>`, `yt_album_track_num` and `yt_duration_sec` as `Option<i64>`, `match_method: MatchMethod`, `confidence_overall: f64`, `review_reason: Option<String>`.

`pub struct NewCandidate { rank: i64, yt_video_id: String, yt_title: String, yt_artist: String, yt_album: Option<String>, yt_duration_sec: Option<i64>, method: String, score: f64, summary: Option<String> }`.

`pub struct ReviewOverride { pub yt_video_id: String, pub started_at: String, pub outcome: String }`, with `outcome` validated against `'started' | 'added' | 'already_present' | 'failed'` in `write_status` and `StoreError::BadOutcome` returned for anything else, so the `CHECK` constraint is never the first thing to notice.

`StoreError` is a `thiserror` enum with variants `Libsql(#[from] libsql::Error)`, `Io(#[from] std::io::Error)`, `UnknownSchemaVersion { found: i64, known: i64 }`, `UnknownStatus(String)` and `BadOutcome(String)`.

- [ ] **Step 6: Write `upsert_track` so it cannot change a status**

`pub async fn upsert_track(&self, row: &TrackRow) -> Result<i64, StoreError>` issues `INSERT INTO track (…) VALUES (…) ON CONFLICT(tidal_id) DO UPDATE SET …` listing every column **except `status`**. On insert it writes `status = 'pending'` literally. On conflict it does not mention `status` at all.

Comment why: `status` is written by exactly one function, `write_status`, and a second writer is what would break the `transferred` invariant. Making the upsert structurally unable to set it is the point.

- [ ] **Step 7: Write `write_status`, the one writer of `status`**

`pub async fn write_status(&self, track_id: i64, new: TrackStatus, over: Option<ReviewOverride>) -> Result<(), StoreError>`.

Inside one transaction:

1. `SELECT status FROM track WHERE id = ?1` and return `StoreError::NotFound` when no row exists.
2. If the current status is `Transferred` and `new` is not `Transferred`, then when `over` is `None` return `StoreError::TransferredIsSacred { track_id }`, and otherwise `INSERT INTO transfer_attempt (track_id, yt_video_id, started_at, outcome) VALUES (…)` using the override's four values.
3. `UPDATE track SET status = ?2 WHERE id = ?1`.
4. `commit()`.

Comment why the read is inside the writing transaction rather than before it: a read outside it is a race, and the invariant has to hold for the write that actually lands.

- [ ] **Step 8: Write the remaining operations**

- `upsert_artist(name: &str, name_key: &str) -> Result<i64, StoreError>` — `INSERT … ON CONFLICT(name_key) DO UPDATE SET name = excluded.name`, then select the id.
- `upsert_album(artist_id, name, name_key, year: Option<i64>) -> Result<i64, StoreError>` — same shape against `UNIQUE (artist_id, name_key)`.
- `upsert_match_confidence(track_id, c: &ConfidenceBreakdown) -> Result<(), StoreError>` — `INSERT … ON CONFLICT(track_id) DO UPDATE SET …` against the table's `INTEGER PRIMARY KEY` id, which is what makes the 1:1 hold.
- `insert_candidate(track_id, c: &NewCandidate) -> Result<i64, StoreError>` — plain insert; `UNIQUE (track_id, yt_video_id)` makes a repeat an error rather than a duplicate.
- `insert_transfer_attempt(track_id, yt_video_id, started_at, outcome) -> Result<i64, StoreError>` — plain insert.
- `status_counts() -> Result<BTreeMap<TrackStatus, i64>, StoreError>` — `SELECT status, COUNT(*) FROM track GROUP BY status`, mapping each string through `TrackStatus::from_str` and returning `StoreError::UnknownStatus` for a value the enum does not know. Comment that this is the query the program design's deletion of the recomputed `[meta]` counters rests on: a relational count cannot drift out of sync with the rows it counts.
- `schema_version() -> Result<i64, StoreError>` — reads `PRAGMA user_version`.

- [ ] **Step 9: Decide `name_key` and pin it in a test**

Read the DDL comment: `name_key TEXT NOT NULL UNIQUE -- casefold(name)`. The rest of this project identifies a name with `text::normalize`, which is NFKD, ASCII-folded, lowercased and stripped of punctuation — strictly stronger than casefold. A program that writes `name_key = text::normalize(name)` is the one the corpus and every other comparison agree with; a program that writes `casefold` produces two album rows for "Song" and "song!".

Add a test in `store.rs` that upserts the album "Song" and then "song!" for one artist and asserts **one** row comes back, and a second test asserting `upsert_album` returns the same `album_id` for both. Comment the test with Review Focus item 3.

Document the choice in the function's doc comment: `name_key` is `text::normalize(name)`, and the DDL's `casefold(name)` comment describes the intent rather than a literal.

If you decide the DDL comment must be honoured literally instead, stop and report — it is a change to the program design's schema decisions, not a local choice.

- [ ] **Step 10: Write the store tests**

Each test opens `Store::open_in_memory()` for a fresh database.

- Migrating an empty database leaves `schema_version() == 1` and every one of the seven tables present in `sqlite_master`.
- Running `migrate()` twice is a no-op: the version is still 1 and no table was recreated. Comment that idempotency is a tested property and not an incidental one, because the host opens the database on every launch.
- With foreign keys on, inserting a `track` whose `album_id` references a non-existent album fails, and inserting one that references a real album succeeds. The test must first prove the pragma took effect, because an insert that fails for an unrelated reason would otherwise pass this assertion.
- Write a row as `Pending`, then `write_status` it to `Transferred`, then attempt to move it to `Skip` with no override, and assert `StoreError::TransferredIsSacred`. Then repeat with an override and assert it succeeds and that `transfer_attempt` has exactly one row with the override's `yt_video_id` and `outcome`.
- Assert the invariant through the *absence* of another path: after `upsert_track` on an existing transferred row with a fresh `TrackRow`, the row is still `Transferred`. Comment that this is the test that would fail if someone later added `status` to the upsert's conflict clause.
- Insert a known mix of statuses and assert `status_counts()` equals a `BTreeMap` built from the rows written, then delete one row and assert the count moves. The `DELETE` proves the count is computed rather than stored.
- Insert a `transfer_attempt` with `outcome = 'banana'` and assert the database rejects it.

- [ ] **Step 11: Lint the new crate in CI**

In `.github/workflows/ci.yml`, change the `rust` job's clippy step to `cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc -p tidal2ytm-store --all-targets -- -D warnings`, and change its `cargo test` step to add `-p tidal2ytm-store`.

Comment the clippy change in place: without this the new crate is compiled as a dependency of the host but never linted, which is the same class of omission as a path-filter entry left out. No path-filter change is needed — the `rust` filter already covers `crates/**`.

- [ ] **Step 12: Update `AGENTS.md`**

Add `crates/tidal2ytm-store` to the project layout paragraph, describing it as owning the schema, the migration runner and every SQL statement. Extend the `cargo clippy` and `cargo test` lines in Quality gates to name all three Tauri-free crates. Do not restate the design.

- [ ] **Step 13: Run the gates**

Run, in order:
`cargo fmt --all --check`
`cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc -p tidal2ytm-store --all-targets -- -D warnings`
`cargo test -p tidal2ytm-core -p tidal2ytm-ipc -p tidal2ytm-store`
`uvx check-jsonschema --builtin-schema github-workflows .github/workflows/ci.yml`

Expected: no output, no warnings, all tests pass, `ok -- validation done`.

- [ ] **Step 14: Commit**

```bash
git add Cargo.toml Cargo.lock crates/tidal2ytm-store crates/tidal2ytm-core/src/lib.rs crates/tidal2ytm-core/src/model.rs .github/workflows/ci.yml AGENTS.md
git commit -m "feat: add the tidal2ytm-store crate with the schema and migration runner"
```

The body must state which libsql open API Step 1 settled on, because that is the one fact in this task that was not verifiable from documentation before it was written.

---

### Task 3: Host wiring

Proves the real application creates and migrates its own database, which no test can prove.

**Files:**
- Modify: `src-tauri/Cargo.toml`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/state.rs`
- Modify: `src-tauri/src/commands.rs`

**Interfaces:**
- Consumes: `store::Store::{open, migrate, schema_version, status_counts}` from Task 2.
- Produces:
  - `AppState::start(config: SidecarConfig, store: Store) -> Result<AppState, String>` — the signature changes, and every existing caller and test must pass a store.
  - `AppState::store(&self) -> &Store`.
  - `#[tauri::command] commands::get_status_counts(state: State<'_, AppState>) -> Result<BTreeMap<TrackStatus, i64>, String>`
  - `#[tauri::command] commands::get_schema_version(state: State<'_, AppState>) -> Result<i64, String>`

- [ ] **Step 1: Add the dependency and change `AppState`**

Add `tidal2ytm-store = { path = "../crates/tidal2ytm-store" }` and `tidal2ytm-core = { path = "../crates/tidal2ytm-core" }` to `src-tauri/Cargo.toml`'s dependencies.

Add a `store: Store` field to `AppState`, take it as a second parameter of `AppState::start`, and add `pub fn store(&self) -> &Store`. Do not put it behind the existing `Mutex<Arc<SidecarClient>>`; the store is not respawned and does not need identity comparison.

Update the existing `state.rs` tests to construct `Store::open_in_memory().await` and pass it. Comment in `state.rs` that `libsql::Connection` is `Send + Sync` (SQLite is compiled thread-safe), which is what makes holding it in Tauri's managed state sound.

- [ ] **Step 2: Open and migrate inside `run()`**

In `run()`'s `.setup(|app| …)` closure, after the existing `std::fs::create_dir_all(&cwd)`, open the store at `cwd.join("tidal2ytm.db")` and call `migrate()`, mapping each error to a `String` that names what failed and where the file is.

Comment why it is here and not in `main()`: `main()` alone is an entry point that can lose everything the process is complaining about, which is the same reason the DEBUG log sink is installed in `run()`. Comment why `create_dir_all` runs first: `app_data_dir()` does not exist on a first run, and a missing working directory fails the sidecar spawn with `os error 267`.

Reuse the `cwd` variable the setup closure already computes rather than calling `app.path().app_data_dir()` a second time, so the database and the sidecar's working directory are provably the same directory.

- [ ] **Step 3: Add the two read-only commands**

In `commands.rs` add `get_status_counts` and `get_schema_version`. Neither goes through the existing private `call` helper: both read the database, and neither involves the sidecar, so the retry-on-`SidecarLost` path would be dead code for them.

Map `StoreError` to a `String` that carries the variant's message. Do not swallow an `UnknownSchemaVersion` into a generic failure — its whole point is naming both versions.

- [ ] **Step 4: Register the commands and extend the host tests**

Add both to the `tauri::generate_handler![…]` list in `lib.rs`.

Add tests in `state.rs`: `AppState::start` succeeds with an in-memory store and `store()` returns it; a second `AppState` over its own in-memory store has a different `schema_version` before migration and 1 after. The second assertion is what proves the host actually migrates rather than merely opening.

- [ ] **Step 5: Run the gates**

Run: `cargo fmt --all --check`
Run: `cargo clippy -p tidal2ytm --all-targets -- -D warnings`
Run: `cargo test -p tidal2ytm`

Expected: all green.

The host's tests require `src-tauri/binaries/tidal2ytm-sidecar-<triple><.exe>` and `dist/` to exist, because `tauri_build` refuses to compile otherwise. If either is missing, create the placeholder as an empty file — derive the triple from `rustc -vV`'s `host:` field — and run `pnpm build` first. Neither artefact is committed; `.gitignore` already excludes both.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/Cargo.toml src-tauri/src/lib.rs src-tauri/src/state.rs src-tauri/src/commands.rs Cargo.lock
git commit -m "feat: open and migrate the database at startup and expose read-only commands"
```

---

### Task 4: Typed envelope and contracts

Fixes the shape of the three unimplemented operations so Phase 4 can build the calls without a second contract change.

**Files:**
- Create: `crates/tidal2ytm-core/src/candidate.rs`
- Create: `contracts/candidate.json`, `contracts/album_summary.json`, `contracts/album_detail.json`
- Create: `sidecar/tidal2ytm_sidecar/envelope.py`
- Create: `sidecar/tests/test_envelope.py`
- Modify: `crates/tidal2ytm-core/Cargo.toml`, `crates/tidal2ytm-core/src/lib.rs`

**Interfaces:**
- Consumes: nothing from earlier tasks except the crate layout.
- Produces:
  - `core::candidate::Candidate { video_id: String, title: String, artists: Vec<String>, album: Option<String>, album_track_num: Option<i64>, duration_sec: Option<i64> }`
  - `core::candidate::AlbumSummary { album_id: String, title: String, artist: String, year: Option<i64> }`
  - `core::candidate::AlbumDetail { summary: AlbumSummary, tracks: Vec<Candidate>, other_versions: Vec<AlbumSummary> }`
  - `sidecar/tidal2ytm_sidecar/envelope.py`: `normalize_candidates(raw: list[Mapping[str, object]]) -> list[dict[str, object]]`, `normalize_album_detail(raw: Mapping[str, object]) -> dict[str, object]`, and `normalize_album_summaries(raw: Iterable[Mapping[str, object]]) -> list[dict[str, object]]`.

- [ ] **Step 1: Define the three types and point `tidal2ytm-ipc` at core**

In `crates/tidal2ytm-core/src/candidate.rs` define the three structs, deriving `Debug, Clone, PartialEq, Eq, Serialize` and `Deserialize`. Add `serde = { workspace = true }` to `tidal2ytm-core`'s `[dependencies]` and `pub mod candidate;` to its `lib.rs`.

Add `tidal2ytm-core = { path = "../tidal2ytm-core" }` to `tidal2ytm-ipc`'s `[dependencies]`, with a comment recording that the direction is the point: the alternative is putting the envelope in `tidal2ytm-ipc`, which makes the pure core depend on the crate that spawns processes and resolves interpreters, dragging sidecar-spawn code into a test binary with no use for it. The dependency is unused until Phase 4 dispatches the operations, and that is acceptable — it fixes the arrow direction in `Cargo.toml` now rather than leaving the next implementer to infer it.

The module `//!` doc states the boundary rule: the domain core never sees a YTM field name. Request and response shaping is the Python that cannot be ported without reimplementing two unaudited API clients, so normalization belongs on the Python side and a YTM field rename then touches the sidecar and nothing else.

Comment why `AlbumDetail` exists even though the spec names two types: `_best_track_fit` and `_map_winner_tracks` consume an album's fetched tracklist and `_dedupe_details` consumes its `other_versions` shelf, so that shape has to be fixed somewhere, and a contract is the only place it can be fixed once.

- [ ] **Step 2: Write the three contract files**

`contracts/candidate.json` is a single-song success response, shaped like the existing `auth_status.json`: `{"id": 1, "ok": true, "data": {…}}` with the `Candidate` fields as `data`. Use an invented 11-character video id, fictional title, two invented artist names, a fictional album, a track number and a duration.

`contracts/album_summary.json` wraps an `AlbumSummary`. `contracts/album_detail.json` wraps an `AlbumDetail` with one track and one `other_versions` entry, so the sibling shape is exercised rather than merely declared.

Register all three in the Rust contract test that already reads `contracts/`, alongside `hello.json` and `auth_status.json`, and add a Python test that reads the same three files through the existing `_contract` helper in `sidecar/tests/test_protocol.py`.

Both sides' contract assertions move in the same commit, which is what makes a shape change a single reviewable event.

- [ ] **Step 3: Write the sidecar normalizers**

In `sidecar/tidal2ytm_sidecar/envelope.py` write the three normalizers. Each takes a YTM-shaped mapping and returns `dict[str, object]` — never `dict[str, Any]`, for the reason already recorded in `AGENTS.md`: `json.loads` hands back `Any`, `Any` launders through pyright in strict mode, and that is exactly how a raw epoch integer once reached the wire while every gate stayed green.

Field mapping, and no field beyond these: `videoId` → `video_id`, `title` → `title`, `artists[].name` → `artists` (dropping empty names), `album.name` → `album`, `trackNumber` → `album_track_num`, `duration_seconds` → `duration_sec`, `browseId` → `album_id`, `year` → `year`.

A missing or unparseable `year` becomes `None`, never `0`. An album's `artist` is the first non-empty name from its `artists` list, falling back to a singular `artist` string; comment that this is deviation 3 from the spec's letter — the Python takes a maximum over the whole list and this takes one name, so multi-artist album matching is not exercised through the envelope until Phase 4 has a real client.

- [ ] **Step 4: Test the normalizers against synthetic YTM shapes**

In `sidecar/tests/test_envelope.py` assert: an artist list of two names plus an empty name yields two names; a payload with only a singular `artist` yields that one name; a payload with neither yields an empty list; a missing `year` becomes `None`; a non-numeric `year` becomes `None`; and each normalizer's output equals the corresponding committed contract file when fed a hand-written raw payload that mirrors it.

Use synthetic field values only.

- [ ] **Step 5: Do not register the operations, and say why in the code**

Add nothing to `protocol.py`'s `dispatch` and nothing to `protocol::REQUIRED_CAPABILITIES`.

Add a comment at the top of `envelope.py` recording that these three operations are shaped but not dispatched because the sidecar has no YTM client, and that `REQUIRED_CAPABILITIES` therefore stays `["auth.status"]` — adding the three names would make the host's handshake reject a sidecar that does not advertise them.

- [ ] **Step 6: Run the gates**

Run: `cargo fmt --all --check`
Run: `cargo clippy -p tidal2ytm-core --all-targets -- -D warnings`
Run: `cargo test -p tidal2ytm-core`
Run: `uv run pytest -q`
Run: `uv run ruff check .`
Run: `uv run ruff format --check .`
Run: `uv run pyright`

Expected: all green. The Python suite includes the new `test_envelope.py` through the existing `testpaths` entry.

- [ ] **Step 7: Commit**

```bash
git add crates/tidal2ytm-core/Cargo.toml crates/tidal2ytm-core/src/lib.rs crates/tidal2ytm-core/src/candidate.rs crates/tidal2ytm-ipc/Cargo.toml contracts sidecar/tidal2ytm_sidecar/envelope.py sidecar/tests/test_envelope.py Cargo.lock
git commit -m "feat: fix the candidate envelope shape in contracts and the sidecar normalizers"
```

---

### Task 5: `core::model`, `core::version_tokens`, `core::track_scoring`, `core::search`

Ports the rest of the pure logic, now scoring against the envelope.

**Files:**
- Create: `crates/tidal2ytm-core/src/version_tokens.rs`, `track_scoring.rs`, `search.rs`
- Modify: `crates/tidal2ytm-core/src/model.rs`, `crates/tidal2ytm-core/src/lib.rs`
- Modify: `crates/tidal2ytm-core/tools/generate_fixtures.py`
- Create: `crates/tidal2ytm-core/tests/fixtures/{version_tokens,track_scoring,search}.json`
- Create: `crates/tidal2ytm-core/tests/parity_{version_tokens,track_scoring,search}.rs`

**Interfaces:**
- Consumes: `core::candidate::Candidate` from Task 4; `core::text` from Task 1.
- Produces:
  - `core::model::SourceTrack` — `tidal_id: i64`, `title: String`, `artist: String`, `artists: Vec<String>`, `album: String`, `album_id: i64`, `album_year: Option<i64>`, `duration_sec: i64`, `track_num: i64`, `disc_num: i64`, `version: Option<String>`. Derives `Debug, Clone, PartialEq`. `from_dict` is **not** ported: the Rust side never parses untyped dictionaries.
  - `core::model::MatchResult` — the Python's fields, deriving `Debug, Clone, PartialEq`.
  - `core::version_tokens::split_base_qualifiers(text: &str) -> (String, Vec<String>)` and `qualifier_similarity(source: &[String], target: &[String]) -> f64`.
  - `core::track_scoring::{DURATION_ABS_TOL_SEC: i64, DURATION_REL_TOL: f64, TrackFit, duration_ok, score_track_fit}`.
  - `core::search::{FUZZY_CUTOFF: f64, named_haystacks, search_library, parse_tidal_link, resolve_album_link}`.

- [ ] **Step 1: Add `SourceTrack` and `MatchResult`**

Add both structs to `core::model`. `MatchResult` carries `source: SourceTrack`, `yt_video_id`, `yt_title`, `yt_artist`, `yt_album` as `Option<String>`, `yt_album_track_num` and `yt_duration_sec` as `Option<i64>`, `match_method: MatchMethod`, `confidence: ConfidenceBreakdown`, `status: TrackStatus`, `review_reason: Option<String>`.

Comment that `SourceTrack::from_dict` has no Rust counterpart on purpose: it is the strict boundary where untyped dictionaries become typed data, and nothing on the Rust side of the wire receives one.

- [ ] **Step 2: Port `version_tokens` and generate its matrix**

Write both functions with the same names. `split_base_qualifiers` strips `[\(\[](.*?)[\)\]]` occurrences, casefolds and trims each captured group, and returns the base with those occurrences removed and trimmed. `qualifier_similarity` returns `1.0` when both sides are empty, `0.4` when exactly one side is empty, `1.0` when the sets are equal, and otherwise `0.4 + 0.6 * len(intersection) / len(union)`.

Its matrix, as explicit generator rows: both empty; source empty only; target empty only; identical single qualifier; identical two qualifiers in different order; one side a subset; disjoint; partial overlap; a qualifier that differs only by case; a qualifier that differs only by surrounding whitespace.

Add a threshold-edge test for the `0.4` one-sided value and for `0.4 + 0.6 * overlap` at overlap `0.0` and `1.0`. That one-sided `0.4` is Review Focus item 5: it is worth `0.09` of `overall` in the next task, and the comment must say so.

Run: `uv run python crates/tidal2ytm-core/tools/generate_fixtures.py` then `cargo test -p tidal2ytm-core --test parity_version_tokens`
Expected: PASS.

- [ ] **Step 3: Port `track_scoring`**

Write `TrackFit` with `overall: f64`, `title_similarity`, `artist_similarity`, `album_similarity`, `version_similarity` all `f64`, `track_num_match: bool`, `duration_delta_sec: Option<i64>`.

`duration_ok(source_sec: i64, candidate_sec: Option<i64>) -> bool` returns true when `source_sec <= 0`, false when the candidate duration is absent, and otherwise `delta <= max(DURATION_ABS_TOL_SEC, (source_sec as f64 * DURATION_REL_TOL) as i64)`.

`score_track_fit(source: &SourceTrack, candidate: &Candidate) -> TrackFit` — note the signature change from Python's five positional arguments. This is the re-targeting onto the envelope: the candidate is now a `Candidate`, so the callers pass one value instead of unpacking five.

Its matrix must include, as explicit rows: duration delta of `DURATION_ABS_TOL_SEC - 1`, exactly at it, and `+ 1`, for a short track; the same three for a long track where the relative tolerance dominates the absolute one; a source duration of `0`; a candidate with no duration; single and multi-artist credits on both sides; a candidate with no album; matching track numbers, mismatching track numbers, source track number `0`, and candidate track number absent.

Comment the weight line: `overall = title_sim * 0.45 + artist_sim * 0.2 + album_sim * 0.2 + version_sim * 0.15`, evaluated left to right in that order, then `min(1.0, overall + 0.08)` when the track numbers match. The order is not cosmetic — the parity test asserts exact `f64` equality, and a different grouping of the same four products gives a different last bit.

Add the threshold-edge tests at both ends of the `+ 0.08` bonus and at the `1.0` cap.

Run the generator, then `cargo test -p tidal2ytm-core --test parity_track_scoring`
Expected: PASS.

- [ ] **Step 4: Port `search`**

`named_haystacks(t: &SourceTrack) -> Vec<(&'static str, &str)>` returns the non-empty fields paired with their names: `title`, `artist`, `artist0`…`artistN`, `album`, in that order.

`search_library(tracks: &[SourceTrack], query: &str) -> (Vec<usize>, bool)` returns **indices** into `tracks`, not owned tracks. Comment why: a fixture of `{input, expected}` pairs must be serializable, and an index is, where a `SourceTrack` would have to be duplicated into every case. The indices are the same values Python's list of tracks would have held.

It normalizes the query, returns every index and `true` when the query normalizes to empty, scores each track by the maximum `similarity` over its haystacks, and then partitions: if any track's normalized haystack *contains* the normalized query, the result is the contained tracks with `score == 1.0` first, then the rest of the contained tracks, and `direct == true`; otherwise the result is the tracks whose best score falls in `FUZZY_CUTOFF <= score < 1.0`, sorted descending by score, and `direct == false`.

Two comments the code needs: the containment test is a substring check on the normalized strings and is independent of the similarity algorithm, while the fallback is the only path that depends on it; and `FUZZY_CUTOFF` is `0.6` with no recorded justification, so a change to it moves results without anything saying why.

Its matrix must include, as explicit rows: an empty query; a query matching exactly; a query matching by substring; a query matching nothing; a title that normalizes to empty and therefore matches nothing — Review Focus item 1, whose test asserts the track is absent from both the direct and the fuzzy result rather than crashing; a multi-artist track; a track with an empty artist; a tie in scores, asserting the first-declared track comes first because both sides sort stably.

Add the `FUZZY_CUTOFF` threshold-edge tests: a score of exactly `0.6` is included and a score of exactly `1.0` is excluded from the fallback.

`parse_tidal_link(raw: &str) -> Option<(&'static str, i64)>` returns `("track", id)`, `("album", id)` or `None`, from the `tidal\.com/(?:browse/)?(track|album)/(\d+)` pattern, case-insensitively.

`resolve_album_link(tracks: &[SourceTrack], album_id: i64) -> Vec<usize>` returns the indices whose `album_id` matches. Comment that the rule is the liked songs of that exact album and never the album entity.

Run the generator, then `cargo test -p tidal2ytm-core --test parity_search`
Expected: PASS.

- [ ] **Step 5: Run the gates**

Run: `cargo fmt --all --check`
Run: `cargo clippy -p tidal2ytm-core --all-targets -- -D warnings`
Run: `cargo test -p tidal2ytm-core`

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add crates/tidal2ytm-core
git commit -m "feat: port version tokens, track scoring and library search"
```

---

### Task 6: `core::album_match`

Ports the pure half, scoring against the envelope.

**Files:**
- Create: `crates/tidal2ytm-core/src/album_match.rs`
- Modify: `crates/tidal2ytm-core/src/lib.rs`
- Modify: `crates/tidal2ytm-core/tools/generate_fixtures.py`
- Create: `crates/tidal2ytm-core/tests/fixtures/album_match.json`, `tests/parity_album_match.rs`

**Interfaces:**
- Consumes: `core::model::{SourceTrack, MatchResult}`, `core::candidate::{AlbumSummary, AlbumDetail, Candidate}`, `core::track_scoring::{score_track_fit, duration_ok, TrackFit}`, `core::version_tokens::split_base_qualifiers`, `core::text::similarity`.
- Produces:
  - `ALBUM_MARGIN_EPS: f64 = 0.05`, `ALBUM_CERTAIN_THRESHOLD: f64 = 0.95`.
  - `group_by_album_id(tracks: &[SourceTrack]) -> BTreeMap<i64, Vec<usize>>`
  - `version_term(source_quals: &[String], cand_quals: &[String]) -> f64`
  - `album_meta_score(source: &SourceTrack, candidate: &AlbumSummary) -> f64`
  - `sibling_year(sib: Option<i64>, parent: Option<i64>) -> Option<i64>`
  - `sibling_entry(parent: &AlbumSummary, sib: &AlbumSummary) -> AlbumSummary`
  - `dedupe_details(details: &[(AlbumSummary, AlbumDetail)]) -> Vec<usize>`
  - `best_track_fit(source: &SourceTrack, tracks: &[Candidate], album_title: &str) -> Option<(TrackFit, usize)>`
  - `rank_candidates(first: &SourceTrack, sources: &[SourceTrack], details: &[(AlbumSummary, AlbumDetail)]) -> Vec<(f64, usize)>`
  - `album_abstains(scored: &[(f64, usize)]) -> bool`
  - `map_winner_tracks(sources: &[SourceTrack], winner: &AlbumDetail) -> Vec<MatchResult>`

- [ ] **Step 1: Write the module doc with the confirmed pure half**

Create `album_match.rs` with a `//!` doc that lists the pure functions this module owns and the impure ones it does not: `_review_results`, `_try_fetch_album`, `_fetch_album_details`, `_harvest_sibling_candidates` and the search half of `resolve_album_group` stay in Python until Phase 4 has a YTM client. Point at the generator's comment recorded in Task 1 Step 1, and state that `ALBUM_SEARCH_LIMIT`, `ALBUM_FETCH_LIMIT` and `ALBUM_VERSION_FETCH_LIMIT` stay Python-side because they cap network calls, not scoring.

- [ ] **Step 2: Port the leaf functions**

`group_by_album_id` returns a `BTreeMap` of album id to indices, for the same serializability reason as `search_library`.

`version_term` returns `1.0` when both sides are empty, `0.0` when exactly one is, `1.0` when the word sets are equal, `0.8` when one set is a subset of the other, and otherwise `0.2 + 0.6 * overlap`. The comparison is on **word sets** — each qualifier split on whitespace — so "deluxe version" still matches "deluxe".

`album_meta_score` splits both album titles into base and qualifiers, takes `similarity` of the two bases, `version_term` of the qualifier tuples, and the artist similarity against the candidate's single `artist`. `year_score` starts at `0.5`; it becomes `1.0 / (1.0 + abs(delta))` **only** when the candidate's year parses *and* `source.album_year` is `Some`. A source with no album year stays at `0.5` even when the candidate's year is known — that asymmetry is easy to "fix" into a bug, so comment it.

The return is `title_sim * 0.45 + artist_sim * 0.25 + year_score * 0.1 + version_term * 0.2`, evaluated in that order for the same last-bit reason as Task 5.

`sibling_year(sib, parent)` returns the sibling's year when it is `Some`, otherwise the parent's.

`sibling_entry(parent, sib)` returns a summary carrying the sibling's own `album_id`, `title`, `artist` and `year`, with each field falling back to the parent's when the sibling's is absent or empty — so a sibling ranks through the same scorer as a search hit. The fallback is the reason this function exists in Python and the reason it must keep existing after the cut.

- [ ] **Step 3: Port `dedupe_details` and `best_track_fit`**

`dedupe_details` returns the indices to keep. The key is the album's title lowercased, with `&` replaced by ` and `, whitespace collapsed, paired with the candidate's artist name. The first entry for a key wins, because search hits precede harvested siblings.

Comment the two decisions: `to_lowercase` stands in for Python's `casefold` because `std` has no casefold and no crate is added for three characters that the ASCII-only corpus never exercises; and the collapse of the artist **list** to one string is deviation 3, which makes multi-artist albums deduplicate more aggressively than the Python does.

`best_track_fit` returns the best `TrackFit` and the index of the row that produced it, scanning in order and keeping the first strict maximum (`>` not `>=`), so ties resolve to the earlier row exactly as Python does. Rows whose duration fails `duration_ok` are skipped.

- [ ] **Step 4: Port `rank_candidates`, `album_abstains` and `map_winner_tracks`**

`rank_candidates` scores each `(candidate, detail)` pair as `meta * 0.4 + fit_mean * 0.6`, where `fit_mean` is the mean of `best_track_fit(...).overall` over `sources` with `0.0` for a source with no fitting row. It sorts **descending and stably**, returning `Vec<(f64, usize)>`.

Comment the stability requirement in the source: `sort_unstable_by` would reorder equal scores and could select a different winning album. Python's `sort(key=…, reverse=True)` preserves the input order among equal keys, and so does Rust's stable `sort_by` with a descending comparator.

`album_abstains(scored)` returns true when `scored` is empty, or when `scored.len() > 1` and the top score is below `ALBUM_CERTAIN_THRESHOLD` and `top - second < ALBUM_MARGIN_EPS`. Comment that a near-certain winner ignores a close second, because at that score the album metadata and the track fits are both excellent and hesitation only loses matches.

`map_winner_tracks` returns one `MatchResult` per source. A source with no fitting row in the winner's tracklist yields a `MatchResult` with no video id, `MatchMethod::NoMatch`, `TrackStatus::NeedsReview`, and a review reason. Otherwise it carries the winning row's video id, title, first artist name, the album title, track number and duration, `MatchMethod::Album`, `TrackStatus::Pending`, and a `ConfidenceBreakdown` populated from the fit with `album_coherence: Some(1.0)`.

The `summary` string is built with the same `:.2f` format as Python. Its text is user-facing copy and the tests must not assert it — assert the status, the method and whether a review reason is present.

- [ ] **Step 5: Generate the album matrix and run the parity test**

The generator emits single-artist albums only, with the deviation-3 comment beside that line.

Its matrix must include, as explicit rows: both album titles empty of qualifiers; identical qualifiers; a qualifier on one side only; subset qualifiers; disjoint qualifiers; a source with `album_year` `None` against a candidate with a year, and the reverse; a year delta of 0, 1 and 10; an `&` in the title against `and`; two album titles differing only by case; two entries that should deduplicate and two that should not; a tracklist where one row's duration fails `duration_ok`; a tracklist where two rows tie on `overall`; an empty tracklist; a candidate whose `other_versions` names the album itself.

Add threshold-edge tests for `ALBUM_CERTAIN_THRESHOLD` at exactly `0.95` and for `ALBUM_MARGIN_EPS` at exactly `0.05` below the top score.

Run the generator, then `cargo test -p tidal2ytm-core --test parity_album_match`
Expected: PASS.

- [ ] **Step 6: Run the gates**

Run: `cargo fmt --all --check`
Run: `cargo clippy -p tidal2ytm-core --all-targets -- -D warnings`
Run: `cargo test -p tidal2ytm-core`

- [ ] **Step 7: Commit**

```bash
git add crates/tidal2ytm-core
git commit -m "feat: port the pure half of album matching"
```

---

### Task 7: Merge policy and the state machine

Re-targets `planning_merge` onto the store and puts every status transition in one place.

**Files:**
- Create: `crates/tidal2ytm-core/src/merge.rs`
- Modify: `crates/tidal2ytm-core/src/lib.rs`
- Modify: `crates/tidal2ytm-core/tools/generate_fixtures.py`
- Create: `crates/tidal2ytm-core/tests/fixtures/merge.json`, `tests/parity_merge.rs`
- Create: `crates/tidal2ytm-store/tests/state_machine.rs` (integration test against a real in-memory database)

**Interfaces:**
- Consumes: `core::model::{MatchResult, TrackStatus, MatchMethod, ConfidenceBreakdown, SourceTrack}`; `store::{Store, TrackRow, NewCandidate, ReviewOverride}`.
- Produces:
  - `MergeAction { SkipTransferred, AddNew, KeepSame, Ask }`
  - `ExistingMatch { status: TrackStatus, yt_video_id: Option<String> }`
  - `classify_track(existing: Option<&ExistingMatch>, new_video_id: Option<&str>) -> MergeAction`
  - `CONFIDENCE_THRESHOLD: f64 = 0.70`, `WRONG_ALBUM_THRESHOLD: f64 = 0.85`, `DURATION_METHOD_CUTOFF: f64 = 0.3`
  - `MatchDecision { action: MergeAction, status: TrackStatus, match_method: MatchMethod, review_reason: Option<String> }`
  - `decide_status(result: &MatchResult, existing: Option<&ExistingMatch>) -> MatchDecision`
  - `ordered_selection(selection: &BTreeMap<i64, SourceTrack>) -> Vec<usize>` — the re-targeted `iter_selection_ordered`.

- [ ] **Step 1: Port `classify_track` as an action enum**

Write the four variants. The order of the arms is the behaviour and must be preserved: `existing is None` → `AddNew`; the existing status is `Transferred` → `SkipTransferred`; both the stored and the new video id empty → `Ask`; stored equal to new → `KeepSame`; otherwise `Ask`.

The Python flattens both ids with `or ""`, so `None` and `Some("")` are the same value. Port with an empty-string fallback on both sides and comment why.

Note in the module doc that `SkipTransferred` is returned here **and** the store refuses the same change. That is deliberate belt-and-braces, not redundancy: the enum arm stops the caller from even attempting the write, and the store's guard holds when a caller reaches it from a path that never consulted this function.

- [ ] **Step 2: Write the state machine's thresholds and decision**

`decide_status` reproduces `matcher.match_track`'s tail, which is where its decisions currently live:

- `wrong_album` when the confidence's `album_similarity` is `Some` and below `WRONG_ALBUM_THRESHOLD`.
- `match_method` is `Duration` when `album_similarity` is below `DURATION_METHOD_CUTOFF`, otherwise `Fuzzy` — except that an `Album` result keeps `Album`.
- `status` is `NeedsReview` when the video id is absent, when `overall` is below `CONFIDENCE_THRESHOLD`, or when `wrong_album` is true. Otherwise `Pending`.
- `review_reason` is `None` when the status is `Pending` and a short explanation otherwise. Copy the three reason shapes from `matcher.py` — no candidate found, wrong album match, low confidence — including the two-decimal formatting of the confidence value, because the string is persisted in `review_reason` and shown in the review UI. Comment that the tests assert the reason's presence and the status, never its exact text.

`Manual` is set when the caller supplies a review override, and never by this function. Comment that `manual` exists because a hand-pasted override stored as `none` with confidence `0.0` is indistinguishable from an abstention, which is the latent bug the program design names.

- [ ] **Step 3: Re-target the selection ordering**

`ordered_selection` returns indices sorted by `artist` casefolded, then `album_year` with `None` as `9999`, then `album` casefolded, then `disc_num`, then `track_num`. A comment must state that `None` sorting as `9999` puts an album with no known year after every album that has one.

Casefolding for the sort key has the same `ß`/`ς`/`İ` caveat as Task 6; use `to_lowercase` and say so once in this module rather than repeating it.

- [ ] **Step 4: Generate the merge matrix and run the parity test**

The matrix must include, as explicit rows, each arm of `classify_track` including both `None`-and-empty and equal-and-empty interactions; each status transition the machine can produce, including a `Manual` override; and each threshold at exactly its value and one ULP either side — `CONFIDENCE_THRESHOLD` at `0.70`, `WRONG_ALBUM_THRESHOLD` at `0.85`, `DURATION_METHOD_CUTOFF` at `0.3`.

Comment the ULP cases: a threshold compared with `<` versus `<=` decides a track's status, and a test that only checks a value clearly above or below the threshold never finds the off-by-one.

The fixture carries the *decision* — action, status, method, and whether a reason is present — not the reason's text.

Run the generator, then `cargo test -p tidal2ytm-core --test parity_merge`
Expected: PASS.

- [ ] **Step 5: Prove the invariant end to end against a real database**

Create `crates/tidal2ytm-store/tests/state_machine.rs`, an integration test rather than an inline one, so it exercises the crate's public surface the way the host will.

Build this sequence against `Store::open_in_memory()`: insert an artist, an album and a track; run `decide_status` for a fresh match and write it with `upsert_track`; assert the row is `Pending` with the expected method. Then `write_status` it to `Transferred`. Then attempt a re-match through `decide_status` with a different video id and assert `classify_track` says `SkipTransferred` **and** that calling `write_status` anyway returns `TransferredIsSacred` and leaves the row `Transferred`. Then do the same through `ReviewOverride` and assert the status moves and exactly one `transfer_attempt` row exists with the override's video id.

Comment why the test does both: the enum arm and the store guard are separate defences, and a test that only exercised the first would not notice the second being removed.

- [ ] **Step 6: Run every gate**

Run, in order:
`cargo fmt --all --check`
`cargo clippy -p tidal2ytm-core -p tidal2ytm-ipc -p tidal2ytm-store --all-targets -- -D warnings`
`cargo clippy -p tidal2ytm --all-targets -- -D warnings`
`cargo test -p tidal2ytm-core -p tidal2ytm-ipc -p tidal2ytm-store`
`cargo test -p tidal2ytm`
`uv run ruff check .`
`uv run ruff format --check .`
`uv run pyright`
`uv run pytest --cov --cov-fail-under=80 --cov-report=term-missing -q`
`pnpm format:check`
`pnpm lint`
`pnpm check`
`pnpm test`
`pnpm build`

Expected: all green. The Python gates are in this list because the CLI still works — Phase 3 deletes no Python, and a port that breaks the CLI has broken the only transfer path the project has.

- [ ] **Step 7: Commit**

```bash
git add crates/tidal2ytm-core crates/tidal2ytm-store Cargo.lock
git commit -m "feat: re-target merge policy and the state machine onto the store"
```

---

## Phase 3 exit check

Run every gate in Task 7 Step 6 once more, then the two things no gate covers.

- [ ] **Package the application and run it**

Run `pwsh sidecar/build_sidecar.ps1`, then `pnpm tauri build`, then launch the packaged executable from `target\release\`. Confirm a `tidal2ytm-sidecar` process appears, and confirm the application's data directory now contains `tidal2ytm.db` with `PRAGMA user_version` equal to 1 and all seven tables present.

This is the spec's exit criterion and the only layer that proves libsql links and behaves inside a packaged Tauri binary on a real platform. Nothing in the test suite can substitute for it.

- [ ] **Push and watch CI**

Commit any remaining changes, push the branch, then run `gh run list` and `gh run watch <run-id> --exit-status`.

Expected: the `rust` job compiles and tests all three crates, and the `host` job compiles and tests the host crate, both green on ubuntu-latest.