//! The application's database: the schema, the migration runner, and every SQL
//! statement the program issues.
//!
//! This crate owns persistence and nothing else. It has no `tauri` dependency
//! and holds no business rules — matching, scoring and transfer policy live in
//! `tidal2ytm-core`, and the window lives in the host — so the crate builds and
//! its tests run on a bare Linux runner with no webkit and no display. That is
//! also why the dependency that matters most is the SQLite binding: whether it
//! links and behaves inside a packaged application is the one thing CI cannot
//! answer from a source review, so it is exercised here on every run rather than
//! discovered at the first `tauri build`.
//!
//! The binding is `rusqlite` with `bundled`, which compiles SQLite itself rather
//! than linking the system's copy. It was `libsql` until a heap-corruption fault
//! killed the test process; `crates/tidal2ytm-store/Cargo.toml` records that in
//! full. What the swap costs is Turso's remote sync protocol, which nothing here
//! ever used.
//!
//! Three facts about the pinned `rusqlite` version shape this crate's code, and
//! all three are silent when they are wrong:
//!
//! - `Connection` is `Send` but not `Sync`, so `Store` holds it behind a
//!   `std::sync::Mutex` and every operation takes the guard once. Nothing here is
//!   `async`: the binding is synchronous, so an `async` signature would describe
//!   blocking work as awaitable and hold a lock across an await point.
//! - `Transaction` rolls back when it is dropped, so every transaction here
//!   commits explicitly. A missing `commit()` discards its work with no error to
//!   notice.
//! - `execute` rejects a statement that produced rows, so a `PRAGMA` that returns
//!   a value is run through `execute_batch`.
//!
//! The one invariant this crate exists to hold: no function assigns the status
//! of a row that already exists except [`store::Store::write_status`], and
//! moving such a row away from `transferred` requires an explicit
//! [`store::ReviewOverride`], whose audit row is written in the same transaction
//! as the status change it justifies. `upsert_track` inserts new rows as
//! `'pending'` and skips the update entirely over a transferred one.

pub mod migrate;
pub mod schema;
pub mod store;

pub use schema::MIGRATIONS;
pub use store::{NewCandidate, ReviewOverride, SCHEMA_VERSION, Store, StoreError, TrackRow};
