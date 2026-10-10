//! The application's database: the schema, the migration runner, and every SQL
//! statement the program issues.
//!
//! This crate owns persistence and nothing else. It has no `tauri` dependency
//! and holds no business rules — matching, scoring and transfer policy live in
//! `tidal2ytm-core`, and the window lives in the host — so the crate builds and
//! its tests run on a bare Linux runner with no webkit and no display. That is
//! also why the dependency that matters most is `libsql`: whether it links and
//! behaves inside a packaged application is the one thing CI cannot answer from
//! a source review, so it is exercised here on every run rather than discovered
//! at the first `tauri build`.
//!
//! Two facts about the pinned `libsql` version shape this crate's code, and both
//! are silent when they are wrong:
//!
//! - `Builder::new_local(path)?.build().await?` then `db.connect()?` is the open
//!   path at 0.9.30. It is a future, so every operation here is `async`.
//! - `Transaction` rolls back when it is dropped — `DropBehavior::Rollback` is
//!   the default — so every transaction here commits explicitly. A missing
//!   `commit()` discards its work with no error to notice.
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
