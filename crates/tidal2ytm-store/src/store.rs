//! Every SQL statement in the project, and the one function allowed to write a
//! track's status.
//!
//! The store knows nothing about matching, scoring or transfer policy: it takes
//! rows and writes them. That boundary is deliberate, because the alternative
//! is a second implementation of the rules somewhere below this layer, and the
//! one invariant that matters most here — a transferred track stays transferred
//! unless a human overrides it — holds structurally rather than by every caller
//! remembering to check.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::{Mutex, MutexGuard, PoisonError};

use rusqlite::Connection;
use tidal2ytm_core::model::{ConfidenceBreakdown, MatchMethod, TrackStatus};

use crate::migrate;
use crate::schema::MIGRATIONS;

/// The schema version this build knows, which is how many migrations exist.
pub const SCHEMA_VERSION: i64 = MIGRATIONS.len() as i64;

/// Everything that can go wrong on the way to or inside the database.
#[derive(Debug, thiserror::Error)]
// `rusqlite::Error` is `#[non_exhaustive]` and implements `PartialEq` but not
// `Clone`, so this enum cannot carry the derive the other error types in the
// workspace use. Matching on a variant is the way to assert on one.
pub enum StoreError {
    #[error("database error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("i/o error: {0}")]
    Io(#[from] std::io::Error),
    #[error("database schema is version {found}, which is newer than this build knows ({known})")]
    UnknownSchemaVersion { found: i64, known: i64 },
    #[error("not a track status: {0}")]
    UnknownStatus(String),
    #[error("not a transfer outcome: {0}")]
    BadOutcome(String),
    #[error("no track with id {track_id}")]
    NotFound { track_id: i64 },
    #[error("track {track_id} is already transferred; moving it needs a review override")]
    TransferredIsSacred { track_id: i64 },
}

/// A track as it is written. Carries no status: the field does not exist on
/// this struct precisely so that constructing one cannot express an intent to
/// set one.
pub struct TrackRow {
    pub tidal_id: i64,
    pub album_id: i64,
    pub title: String,
    pub artist: String,
    pub duration_sec: i64,
    pub track_num: i64,
    pub disc_num: i64,
    pub version: Option<String>,
    pub yt_video_id: Option<String>,
    pub yt_title: Option<String>,
    pub yt_artist: Option<String>,
    pub yt_album: Option<String>,
    pub yt_album_track_num: Option<i64>,
    pub yt_duration_sec: Option<i64>,
    pub match_method: MatchMethod,
    pub confidence_overall: f64,
    pub review_reason: Option<String>,
}

/// One track's Tidal metadata as the sync writes it: the columns
/// [`Store::upsert_track_source`] names, with the resolved `album_id` replaced
/// by the artist and album names it is resolved from. The Tidal-side
/// `album_id` and `artists` list are not carried because the schema identifies
/// those rows by normalized name — [`Store::upsert_source_page`] resolves the
/// keys with the same `text::normalize` rule `TrackRow`'s callers apply.
pub struct SourceRow {
    pub tidal_id: i64,
    pub title: String,
    pub artist: String,
    pub album: String,
    pub album_year: Option<i64>,
    pub duration_sec: i64,
    pub track_num: i64,
    pub disc_num: i64,
    pub version: Option<String>,
}

/// One alternative the matcher considered, written only when the match was not
/// certain enough to act on.
pub struct NewCandidate {
    pub rank: i64,
    pub yt_video_id: String,
    pub yt_title: String,
    pub yt_artist: String,
    pub yt_album: Option<String>,
    pub yt_duration_sec: Option<i64>,
    /// How this candidate was found, free text rather than a `MatchMethod`: a
    /// candidate is a candidate because no method won.
    pub method: String,
    pub score: f64,
    pub summary: Option<String>,
}

/// The audit record a human's decision to move an already-transferred track
/// leaves behind. It is written in the same transaction as the status change,
/// so a status that moved is never a status whose reason is missing.
pub struct ReviewOverride {
    pub yt_video_id: String,
    pub started_at: String,
    pub outcome: String,
}

/// The outcomes the `transfer_attempt` `CHECK` constraint permits.
const OUTCOMES: [&str; 4] = ["started", "added", "already_present", "failed"];

/// `upsert_artist` and [`Store::upsert_source_page`] run the same statement —
/// the page's transaction borrows the const rather than carrying a second copy
/// of its text, because a second copy is a second place to drift from the
/// conflict clause that makes the artist row one per name.
const UPSERT_ARTIST_SQL: &str = "INSERT INTO artist (name, name_key) VALUES (?1, ?2)
     ON CONFLICT(name_key) DO UPDATE SET name = excluded.name";

/// [Store::upsert_album]'s statement, shared the same way.
const UPSERT_ALBUM_SQL: &str =
    "INSERT INTO album (artist_id, name, name_key, year) VALUES (?1, ?2, ?3, ?4)
     ON CONFLICT(artist_id, name_key) DO UPDATE SET
       name = excluded.name,
       year = excluded.year";

/// [Store::upsert_track_source]'s statement, shared the same way — the sync's
/// per-page transaction runs it as `upsert_track_source` writes it, one
/// statement text for one rule about which columns a source write owns.
const UPSERT_TRACK_SOURCE_SQL: &str = "INSERT INTO track (
           album_id, tidal_id, title, artist, duration_sec, track_num, disc_num,
           version, status, match_method
         ) VALUES (
           ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'pending', 'none'
         )
         ON CONFLICT(tidal_id) DO UPDATE SET
           album_id = excluded.album_id,
           title = excluded.title,
           artist = excluded.artist,
           duration_sec = excluded.duration_sec,
           track_num = excluded.track_num,
           disc_num = excluded.disc_num,
           version = excluded.version
         WHERE track.status <> 'transferred'";

/// A connection to the application's database.
///
/// One connection behind one lock, and deliberately not a pool. `rusqlite`'s
/// `Connection` is `Send` but not `Sync`, so `Mutex<Connection>` is what makes
/// this struct shareable at all: it is held in Tauri's managed state as a plain
/// field with no lock of its own, and every command reaches the same one handle
/// through this one mutex. A pool would be a second thing to reason about for a
/// single-writer desktop application whose working set is a few thousand rows,
/// and it would spread the concurrency question across N connections instead of
/// answering it once, here.
pub struct Store {
    conn: Mutex<Connection>,
}

impl Store {
    /// Open the database file at `path`, creating its parent directory if it is
    /// missing.
    ///
    /// The parent directory is created here rather than by the caller because
    /// the first run of a packaged application has no data directory yet, and a
    /// missing directory surfaces as an opaque `unable to open database file`
    /// rather than as the thing that is actually wrong.
    pub fn open(path: &Path) -> Result<Self, StoreError> {
        if let Some(parent) = path.parent()
            && !parent.as_os_str().is_empty()
        {
            std::fs::create_dir_all(parent)?;
        }
        Self::connect(path)
    }

    /// Open a private, empty, in-memory database.
    ///
    /// Every call gets its own database, because an in-memory SQLite database
    /// lives in the connection that opened it — two stores opened this way share
    /// nothing. That is what lets a test assert on counts without a teardown
    /// step.
    pub fn open_in_memory() -> Result<Self, StoreError> {
        Self::connect(Path::new(":memory:"))
    }

    fn connect(path: &Path) -> Result<Self, StoreError> {
        let conn = Connection::open(path)?;
        // Foreign keys are off by default in SQLite, and the pragma is per
        // connection rather than per database, so a connection opened without
        // it enforces none of the `REFERENCES` clauses in the schema. Setting it
        // once at open is the only place it has to be set: every later
        // connection this process makes goes through here.
        conn.execute("PRAGMA foreign_keys = ON", [])?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    /// The connection, locked for the length of one operation.
    ///
    /// Poisoning is recovered from rather than propagated. A panic partway
    /// through an operation would otherwise make every later call to this
    /// process's database fail with "a previous call panicked", turning one
    /// bug into a permanently unusable application. It is safe to carry on
    /// because every operation reachable through this guard is either a single
    /// statement or a transaction that rolls itself back when it unwinds, so a
    /// half-applied operation cannot outlive the panic that interrupted it.
    fn conn(&self) -> MutexGuard<'_, Connection> {
        self.conn.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Apply any migration this database has not had yet.
    pub fn migrate(&self) -> Result<(), StoreError> {
        let mut conn = self.conn();
        migrate::migrate(&mut conn)
    }

    /// The schema version currently recorded in the database.
    pub fn schema_version(&self) -> Result<i64, StoreError> {
        migrate::read_user_version(&self.conn())
    }

    /// Insert the artist, or update its display name, and return its id.
    ///
    /// `name_key` is `text::normalize(name)`, which is NFKD decomposition, an
    /// ASCII fold, lowercasing and punctuation removal. The DDL's `-- casefold(name)`
    /// comment describes the intent — one row per name regardless of spelling —
    /// not the literal operation, and the whole rest of this project identifies a
    /// name with `normalize`. Honouring `casefold` literally would give "Song" and
    /// "song!" two album rows, which is the bug the key exists to prevent.
    pub fn upsert_artist(&self, name: &str, name_key: &str) -> Result<i64, StoreError> {
        let conn = self.conn();
        conn.execute(UPSERT_ARTIST_SQL, rusqlite::params![name, name_key])?;
        Self::artist_id(&conn, name_key)
    }

    /// Insert the album, or update its name and year, and return its id.
    ///
    /// The album-level `match_conf`, `match_margin` and `match_reason` columns
    /// are deliberately absent from both the insert and the conflict clause: they
    /// belong to album matching, which runs after the album exists, and an
    /// upsert that rewrote them would erase a resolved winner every time the
    /// library is re-scanned.
    pub fn upsert_album(
        &self,
        artist_id: i64,
        name: &str,
        name_key: &str,
        year: Option<i64>,
    ) -> Result<i64, StoreError> {
        let conn = self.conn();
        conn.execute(
            UPSERT_ALBUM_SQL,
            rusqlite::params![artist_id, name, name_key, year],
        )?;
        Self::album_id(&conn, artist_id, name_key)
    }

    /// Insert the track, or update everything about it except its status, and
    /// return its id.
    ///
    /// The conflict clause does not mention `status`, and a `WHERE` guard makes
    /// it skip the whole update over a transferred row.
    ///
    /// Omitting `status` alone is not enough. A library re-scan calls this for
    /// every Tidal track with a freshly computed match and `review_reason: None`,
    /// so an unguarded update overwrites a human's review note, replaces
    /// `yt_video_id` with whatever the new matcher found — or with nothing — and
    /// leaves the row no longer corresponding to its own `transfer_attempt`. None
    /// of that goes through [`Store::write_status`], so none of it would raise an
    /// error or record an override. Guarding the clause is also what makes the
    /// omission sufficient: with the guard in place `status` could be listed in
    /// the `SET` without consequence, and leaving it out says so.
    ///
    /// `WHERE track.status <> 'transferred'` states that rule in one clause
    /// instead of nine per-column `CASE` expressions, and a row it skips is
    /// skipped entirely rather than half-updated.
    ///
    /// The cost is deliberate and is a behaviour change worth knowing: a Tidal
    /// metadata correction no longer propagates into an already-transferred row.
    /// Such a row is a frozen record of what was transferred, which is the same
    /// reasoning `classify_track`'s `skip-transferred` arm applies before this
    /// call is ever made.
    ///
    /// On insert the status is the literal `'pending'`, because a track that has
    /// never been examined cannot be anything else.
    pub fn upsert_track(&self, row: &TrackRow) -> Result<i64, StoreError> {
        let conn = self.conn();
        conn.execute(
            "INSERT INTO track (
                   album_id, tidal_id, title, artist, duration_sec, track_num, disc_num,
                   version, status, yt_video_id, yt_title, yt_artist, yt_album,
                   yt_album_track_num, yt_duration_sec, match_method, confidence_overall,
                   review_reason
                 ) VALUES (
                   ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'pending', ?9, ?10, ?11, ?12, ?13, ?14, ?15,
                   ?16, ?17
                 )
                 ON CONFLICT(tidal_id) DO UPDATE SET
                   album_id = excluded.album_id,
                   title = excluded.title,
                   artist = excluded.artist,
                   duration_sec = excluded.duration_sec,
                   track_num = excluded.track_num,
                   disc_num = excluded.disc_num,
                   version = excluded.version,
                   yt_video_id = excluded.yt_video_id,
                   yt_title = excluded.yt_title,
                   yt_artist = excluded.yt_artist,
                   yt_album = excluded.yt_album,
                   yt_album_track_num = excluded.yt_album_track_num,
                   yt_duration_sec = excluded.yt_duration_sec,
                   match_method = excluded.match_method,
                   confidence_overall = excluded.confidence_overall,
                   review_reason = excluded.review_reason
                 WHERE track.status <> 'transferred'",
            rusqlite::params![
                row.album_id,
                row.tidal_id,
                row.title.as_str(),
                row.artist.as_str(),
                row.duration_sec,
                row.track_num,
                row.disc_num,
                row.version.as_deref(),
                row.yt_video_id.as_deref(),
                row.yt_title.as_deref(),
                row.yt_artist.as_deref(),
                row.yt_album.as_deref(),
                row.yt_album_track_num,
                row.yt_duration_sec,
                row.match_method.as_str(),
                row.confidence_overall,
                row.review_reason.as_deref(),
            ],
        )?;
        Self::track_id(&conn, row.tidal_id)
    }

    /// Insert the track from its Tidal metadata alone, or refresh only that
    /// metadata, and return its id.
    ///
    /// Same table as [`Store::upsert_track`], disjoint column sets, disjoint
    /// callers — the split is the design, not duplication.
    /// [`Store::upsert_track`] is the matcher's write and owns the `yt_*`
    /// columns, `match_method`, `confidence_overall` and `review_reason`; the
    /// sync that pages in favourite tracks knows only Tidal metadata, so its
    /// write — this one — names only the eight Tidal columns in its
    /// `ON CONFLICT` `SET`. Folding the two into one statement would put both
    /// column lists into every caller's hands: a sync running the merged
    /// statement would null every match and review note on the row, because a
    /// source row carries no match, and a matcher running it could not express
    /// an unexamined source-only track at all.
    ///
    /// `WHERE track.status <> 'transferred'` is [`Store::upsert_track`]'s
    /// guard, kept verbatim for the reason the clause exists there: a
    /// transferred row is a frozen record of what was transferred, so a
    /// metadata refresh must not rewrite the Tidal facts it was built from
    /// either.
    ///
    /// On insert the row needs the `NOT NULL` columns Tidal metadata cannot
    /// supply: `status` is the literal `'pending'` (a track that has never
    /// been examined cannot be anything else) and `match_method` the literal
    /// `'none'` (nothing has matched it yet). `confidence_overall` is left to
    /// its column default. What this write never updates has no room in the
    /// statement at all, so "cannot blank a match" holds by construction
    /// rather than by every caller passing the right things.
    pub fn upsert_track_source(&self, row: &TrackRow) -> Result<i64, StoreError> {
        let conn = self.conn();
        conn.execute(
            UPSERT_TRACK_SOURCE_SQL,
            rusqlite::params![
                row.album_id,
                row.tidal_id,
                row.title.as_str(),
                row.artist.as_str(),
                row.duration_sec,
                row.track_num,
                row.disc_num,
                row.version.as_deref(),
            ],
        )?;
        Self::track_id(&conn, row.tidal_id)
    }

    /// Write one page of the sync: every row's artist, album and track, in the
    /// foreign-key order, in one transaction.
    ///
    /// A page is committed whole or not at all, because page boundaries are
    /// what a partial mirror is readable through: a sync that failed partway
    /// must show exactly the pages it did commit, and a half-committed page
    /// would blur every count the surface can produce.
    ///
    /// The writes go through the same statement text the single-row methods
    /// own, so a page write cannot grow a second dialect.
    ///
    /// Sync upserts and never deletes. Nothing here removes a track or an
    /// album this fetch did not repeat: a track's absence from one paginated
    /// page is not proof a fetch overview said so, and a delete removes the
    /// row's whole history. A delete may only ever run against a fetch that
    /// completed every page, in one transaction, and never over a
    /// `transferred` or `skip` row.
    pub fn upsert_source_page(&self, rows: &[SourceRow]) -> Result<(), StoreError> {
        let mut conn = self.conn();
        let tx = conn.transaction()?;
        for row in rows {
            let artist_key = tidal2ytm_core::text::normalize(&row.artist);
            tx.execute(UPSERT_ARTIST_SQL, rusqlite::params![row.artist, artist_key])?;
            let artist_id = Self::artist_id(&tx, &artist_key)?;

            let album_key = tidal2ytm_core::text::normalize(&row.album);
            tx.execute(
                UPSERT_ALBUM_SQL,
                rusqlite::params![artist_id, row.album, album_key, row.album_year],
            )?;
            let album_id = Self::album_id(&tx, artist_id, &album_key)?;

            tx.execute(
                UPSERT_TRACK_SOURCE_SQL,
                rusqlite::params![
                    album_id,
                    row.tidal_id,
                    row.title,
                    row.artist,
                    row.duration_sec,
                    row.track_num,
                    row.disc_num,
                    row.version.as_deref(),
                ],
            )?;
        }
        // Mandatory: rusqlite's `Transaction` rolls back when it is dropped, so
        // a missing `commit()` would end an erroring page in silence.
        tx.commit()?;
        Ok(())
    }

    /// Write one meta value, or replace the one it has.
    pub fn set_meta(&self, key: &str, value: &str) -> Result<(), StoreError> {
        self.conn().execute(
            "INSERT INTO meta (key, value) VALUES (?1, ?2)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            rusqlite::params![key, value],
        )?;
        Ok(())
    }

    /// Read one meta value. A key that was never written reads as `None`,
    /// which is the distinction the sync's caller needs: `sync_started_at`
    /// carries its whole meaning in whether the row exists, never in the
    /// characters beside it.
    pub fn get_meta(&self, key: &str) -> Result<Option<String>, StoreError> {
        match self
            .conn()
            .query_row("SELECT value FROM meta WHERE key = ?1", [key], |row| {
                row.get::<_, String>(0)
            }) {
            Ok(value) => Ok(Some(value)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    /// Remove one meta value. This is the store's only operation that removes
    /// a row at all, and it is scoped to the `meta` table for the reason
    /// `upsert_source_page` denotes: tracks are never deleted here.
    pub fn delete_meta(&self, key: &str) -> Result<(), StoreError> {
        self.conn()
            .execute("DELETE FROM meta WHERE key = ?1", [key])?;
        Ok(())
    }

    /// Write the track's status, and return.
    ///
    /// This is the only function in the project that assigns the status of a row
    /// that already exists. (`upsert_track` assigns the literal `'pending'`, and
    /// only on insert.) Moving an existing row away from `transferred` requires
    /// `over`, and the `transfer_attempt` row it names is written in the same
    /// transaction — so an override cannot be recorded without taking effect, or
    /// take effect without being recorded.
    ///
    /// An `over` handed to a write that does not need one is not an error and is
    /// not recorded: that audit row exists to explain a status moving *away from*
    /// `transferred`, and a first transfer of a `pending` track is explained by
    /// the transfer flow's own attempt row. It is still validated, so a bad
    /// outcome string is caught at the call that carried it rather than nowhere.
    ///
    /// The transaction is IMMEDIATE, not the default DEFERRED. The decision
    /// depends on a read, and a DEFERRED transaction holds only a read lock
    /// until its first write: a writer committing in between turns the upgrade
    /// into `SQLITE_BUSY_SNAPSHOT` rather than into a status change decided
    /// against the current value. IMMEDIATE takes the write lock up front, so
    /// the read and the write it authorises are one indivisible decision.
    pub fn write_status(
        &self,
        track_id: i64,
        new: TrackStatus,
        over: Option<ReviewOverride>,
    ) -> Result<(), StoreError> {
        // Validate the override before opening the transaction, and whenever one
        // is supplied rather than only when it is consumed: a bad outcome string
        // is a caller bug whether or not this particular write ends up needing
        // the override, and the `CHECK` constraint's rejection message names no
        // field.
        if let Some(o) = &over
            && !OUTCOMES.contains(&o.outcome.as_str())
        {
            return Err(StoreError::BadOutcome(o.outcome.clone()));
        }

        let mut conn = self.conn();
        let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        // The read belongs inside the transaction, not before it. A read
        // outside is a race with any writer that commits before this one starts,
        // and the invariant has to hold for the write that actually lands.
        let current = Self::status_in(&tx, track_id)?;
        if current == TrackStatus::Transferred && new != TrackStatus::Transferred {
            let Some(o) = over else {
                // Returning here drops `tx`, and rusqlite's `Transaction` rolls
                // back when dropped, so nothing has been written.
                return Err(StoreError::TransferredIsSacred { track_id });
            };
            tx.execute(
                "INSERT INTO transfer_attempt (track_id, yt_video_id, started_at, outcome)
                 VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params![
                    track_id,
                    o.yt_video_id.as_str(),
                    o.started_at.as_str(),
                    o.outcome.as_str()
                ],
            )?;
        }
        tx.execute(
            "UPDATE track SET status = ?1 WHERE id = ?2",
            rusqlite::params![new.as_str(), track_id],
        )?;
        // Mandatory: rusqlite's `Transaction` rolls back when it is dropped, so a
        // missing `commit()` discards both the override row and the status
        // change with no error to show for it.
        tx.commit()?;
        Ok(())
    }

    /// Write the track's score breakdown, replacing any previous one.
    ///
    /// The table's `track_id` is an `INTEGER PRIMARY KEY`, which is what makes
    /// the 1:1 hold: it *is* the row's identity, so `ON CONFLICT(track_id)`
    /// has something to conflict on and the table cannot hold a second row for
    /// one track.
    ///
    /// `breakdown.overall` is not written here. The table has no such column:
    /// the overall score lives on `track.confidence_overall`, beside the fields
    /// it summarises, and duplicating it would be a second value to drift.
    pub fn upsert_match_confidence(
        &self,
        track_id: i64,
        breakdown: &ConfidenceBreakdown,
    ) -> Result<(), StoreError> {
        self.conn().execute(
            "INSERT INTO match_confidence (
                   track_id, summary, title_similarity, artist_similarity, album_similarity,
                   version_similarity, duration_delta_sec, track_num_match, album_coherence
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
                 ON CONFLICT(track_id) DO UPDATE SET
                   summary = excluded.summary,
                   title_similarity = excluded.title_similarity,
                   artist_similarity = excluded.artist_similarity,
                   album_similarity = excluded.album_similarity,
                   version_similarity = excluded.version_similarity,
                   duration_delta_sec = excluded.duration_delta_sec,
                   track_num_match = excluded.track_num_match,
                   album_coherence = excluded.album_coherence",
            rusqlite::params![
                track_id,
                breakdown.summary.as_deref(),
                breakdown.title_similarity,
                breakdown.artist_similarity,
                breakdown.album_similarity,
                breakdown.version_similarity,
                breakdown.duration_delta_sec,
                breakdown.track_num_match,
                breakdown.album_coherence,
            ],
        )?;
        Ok(())
    }

    /// Record one candidate and return its id.
    ///
    /// A plain insert: `UNIQUE (track_id, yt_video_id)` means the same video
    /// offered twice is an error rather than a duplicate row, and a candidate
    /// list that silently grows on every re-scan is worse than one that says so.
    pub fn insert_candidate(
        &self,
        track_id: i64,
        candidate: &NewCandidate,
    ) -> Result<i64, StoreError> {
        let conn = self.conn();
        conn.execute(
            "INSERT INTO candidate (
                   track_id, rank, yt_video_id, yt_title, yt_artist, yt_album, yt_duration_sec,
                   method, score, summary
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            rusqlite::params![
                track_id,
                candidate.rank,
                candidate.yt_video_id.as_str(),
                candidate.yt_title.as_str(),
                candidate.yt_artist.as_str(),
                candidate.yt_album.as_deref(),
                candidate.yt_duration_sec,
                candidate.method.as_str(),
                candidate.score,
                candidate.summary.as_deref(),
            ],
        )?;
        Ok(conn.last_insert_rowid())
    }

    /// Record a transfer attempt and return its id.
    ///
    /// Unlike `write_status`, this does not validate `outcome`: it is the raw
    /// write the transfer flow uses to log an attempt before the remote add, and
    /// it accepts whatever the database's `CHECK` constraint accepts, which is
    /// the one list of legal outcomes.
    pub fn insert_transfer_attempt(
        &self,
        track_id: i64,
        yt_video_id: &str,
        started_at: &str,
        outcome: &str,
    ) -> Result<i64, StoreError> {
        let conn = self.conn();
        conn.execute(
            "INSERT INTO transfer_attempt (track_id, yt_video_id, started_at, outcome)
                 VALUES (?1, ?2, ?3, ?4)",
            rusqlite::params![track_id, yt_video_id, started_at, outcome],
        )?;
        Ok(conn.last_insert_rowid())
    }

    /// Count the tracks in each status.
    ///
    /// This count is computed by the database from the rows themselves, which is
    /// the whole reason the program design deleted the TOML plan's recomputed
    /// `[meta]` counters: a number derived by a query cannot disagree with the
    /// rows it counts, because there is only one of it.
    pub fn status_counts(&self) -> Result<BTreeMap<TrackStatus, i64>, StoreError> {
        let conn = self.conn();
        let mut stmt = conn.prepare("SELECT status, COUNT(*) FROM track GROUP BY status")?;
        let rows = stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        })?;
        let mut counts = BTreeMap::new();
        for row in rows {
            let (raw, count) = row?;
            let status = raw
                .parse::<TrackStatus>()
                .map_err(|_| StoreError::UnknownStatus(raw.clone()))?;
            counts.insert(status, count);
        }
        Ok(counts)
    }

    fn artist_id(conn: &Connection, name_key: &str) -> Result<i64, StoreError> {
        // `query_row` rather than a prepared statement walked by hand: it is
        // exactly one row by construction, and it reports the empty result as
        // `QueryReturnedNoRows`, which is the error this wants.
        Ok(conn.query_row(
            "SELECT id FROM artist WHERE name_key = ?1",
            [name_key],
            |row| row.get::<_, i64>(0),
        )?)
    }

    fn album_id(conn: &Connection, artist_id: i64, name_key: &str) -> Result<i64, StoreError> {
        Ok(conn.query_row(
            "SELECT id FROM album WHERE artist_id = ?1 AND name_key = ?2",
            rusqlite::params![artist_id, name_key],
            |row| row.get::<_, i64>(0),
        )?)
    }

    fn track_id(conn: &Connection, tidal_id: i64) -> Result<i64, StoreError> {
        Ok(conn.query_row(
            "SELECT id FROM track WHERE tidal_id = ?1",
            [tidal_id],
            |row| row.get::<_, i64>(0),
        )?)
    }

    /// Read a track's status. Missing row is an error rather than a default:
    /// `pending` would let a write against a nonexistent id look like it worked.
    ///
    /// `&Connection` rather than the guard: a `Transaction` derefs to one, so
    /// the same function serves the reads inside `write_status` and the
    /// standalone ones without re-locking a mutex the caller already holds.
    ///
    /// The empty result is translated here rather than left as
    /// `QueryReturnedNoRows`, because the two say different things to a caller:
    /// one is "no row with that key", the other is "you asked for one row and
    /// got none", and the first names the id that is missing.
    fn status_in(conn: &Connection, track_id: i64) -> Result<TrackStatus, StoreError> {
        let raw = match conn.query_row(
            "SELECT status FROM track WHERE id = ?1",
            [track_id],
            |row| row.get::<_, String>(0),
        ) {
            Ok(raw) => raw,
            Err(rusqlite::Error::QueryReturnedNoRows) => {
                return Err(StoreError::NotFound { track_id });
            }
            Err(error) => return Err(error.into()),
        };
        raw.parse::<TrackStatus>()
            .map_err(|_| StoreError::UnknownStatus(raw.clone()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn migrated() -> Store {
        let store = Store::open_in_memory().expect("open");
        store.migrate().expect("migrate");
        store
    }

    fn track(album_id: i64, tidal_id: i64) -> TrackRow {
        TrackRow {
            tidal_id,
            album_id,
            title: "Fictional Song".to_string(),
            artist: "Fictional Artist".to_string(),
            duration_sec: 210,
            track_num: 3,
            disc_num: 1,
            version: None,
            yt_video_id: Some("CCCCCCCCCCC".to_string()),
            yt_title: Some("Fictional Song".to_string()),
            yt_artist: Some("Fictional Artist".to_string()),
            yt_album: Some("Fictional Album".to_string()),
            yt_album_track_num: Some(3),
            yt_duration_sec: Some(209),
            match_method: MatchMethod::Duration,
            confidence_overall: 0.82,
            review_reason: None,
        }
    }

    fn one_album(store: &Store) -> i64 {
        let artist = store
            .upsert_artist("Fictional Artist", "fictional artist")
            .expect("artist");
        store
            .upsert_album(artist, "Fictional Album", "fictional album", Some(2024))
            .expect("album")
    }

    fn scalars(store: &Store, sql: &str) -> Vec<String> {
        let conn = store.conn();
        let mut stmt = conn.prepare(sql).expect("prepare");
        let rows = stmt
            .query_map([], |row| row.get::<_, String>(0))
            .expect("query");
        rows.map(|value| value.expect("scalar")).collect()
    }

    /// A fictional Tidal id. Bound as a parameter everywhere it reaches SQL,
    /// which is also what keeps a Rust numeric literal's `_` separators out of a
    /// statement string.
    const FIRST_TIDAL_ID: i64 = 1_234_567;

    /// Every column `upsert_track` can write, so "the row is unchanged" can be
    /// asserted rather than asserted-about. `f64` needs no `Eq` for that.
    #[derive(Debug, PartialEq)]
    struct TrackSnapshot {
        album_id: i64,
        tidal_id: i64,
        title: String,
        artist: String,
        duration_sec: i64,
        track_num: i64,
        disc_num: i64,
        version: Option<String>,
        status: String,
        yt_video_id: Option<String>,
        yt_title: Option<String>,
        yt_artist: Option<String>,
        yt_album: Option<String>,
        yt_album_track_num: Option<i64>,
        yt_duration_sec: Option<i64>,
        match_method: String,
        confidence_overall: f64,
        review_reason: Option<String>,
    }

    fn whole_row(store: &Store, track_id: i64) -> TrackSnapshot {
        let conn = store.conn();
        conn.query_row(
            "SELECT album_id, tidal_id, title, artist, duration_sec, track_num, disc_num,
                        version, status, yt_video_id, yt_title, yt_artist, yt_album,
                        yt_album_track_num, yt_duration_sec, match_method, confidence_overall,
                        review_reason
                 FROM track WHERE id = ?1",
            [track_id],
            |row| {
                Ok(TrackSnapshot {
                    album_id: row.get(0)?,
                    tidal_id: row.get(1)?,
                    title: row.get(2)?,
                    artist: row.get(3)?,
                    duration_sec: row.get(4)?,
                    track_num: row.get(5)?,
                    disc_num: row.get(6)?,
                    version: row.get(7)?,
                    status: row.get(8)?,
                    yt_video_id: row.get(9)?,
                    yt_title: row.get(10)?,
                    yt_artist: row.get(11)?,
                    yt_album: row.get(12)?,
                    yt_album_track_num: row.get(13)?,
                    yt_duration_sec: row.get(14)?,
                    match_method: row.get(15)?,
                    confidence_overall: row.get(16)?,
                    review_reason: row.get(17)?,
                })
            },
        )
        .expect("the track still exists")
    }

    /// Assert that `error` is SQLite rejecting a constraint, and that the message
    /// names `expected`.
    ///
    /// SQLite phrases these rejections differently — `FOREIGN KEY constraint
    /// failed` for a reference, `CHECK constraint failed: transfer_attempt` for
    /// a `CHECK` — so this pins the *reason* rather than the error variant. A bare
    /// `Sqlite(_)` pattern also matches a mistyped statement, which is a failure
    /// this test would otherwise report as the constraint working.
    fn assert_constraint(error: &StoreError, expected: &str) {
        let text = error.to_string();
        assert!(matches!(error, StoreError::Sqlite(_)), "{text}");
        assert!(text.contains(expected), "expected {expected:?} in {text:?}");
    }

    fn count(store: &Store, sql: &str) -> i64 {
        store
            .conn()
            .query_row(sql, [], |row| row.get::<_, i64>(0))
            .expect("one row")
    }

    #[test]
    fn migrating_an_empty_database_creates_every_table() {
        let store = migrated();
        assert_eq!(store.schema_version().expect("version"), SCHEMA_VERSION);

        let mut names = scalars(
            &store,
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
        );
        names.retain(|n| !n.starts_with("sqlite_"));
        assert_eq!(
            names,
            [
                "album",
                "artist",
                "candidate",
                "match_confidence",
                "meta",
                "track",
                "transfer_attempt"
            ]
        );
    }

    #[test]
    fn migrating_twice_changes_nothing() {
        // Idempotency is a tested property rather than an incidental one: the
        // host opens the database on every launch, so a second run is the normal
        // case and not an edge case. The DDL is plain `CREATE TABLE` with no
        // `IF NOT EXISTS`, so a runner that re-applied step 1 would fail on the
        // first existing table; the row written here is what proves the tables
        // were left alone rather than rebuilt.
        let store = migrated();
        let album = one_album(&store);
        store
            .upsert_track(&track(album, FIRST_TIDAL_ID))
            .expect("track");

        store.migrate().expect("second migrate");

        assert_eq!(store.schema_version().expect("version"), SCHEMA_VERSION);
        assert_eq!(count(&store, "SELECT COUNT(*) FROM track"), 1);
        assert_eq!(store.status_counts().expect("counts").len(), 1);
    }

    #[test]
    fn foreign_keys_are_enforced() {
        let store = migrated();
        // Read the pragma before asserting on a rejection. "The insert failed"
        // is otherwise indistinguishable from "the constraint is not on", and
        // this test would pass for the wrong reason on a connection opened
        // without the pragma.
        let on = store
            .conn()
            .query_row("PRAGMA foreign_keys", [], |row| row.get::<_, bool>(0))
            .expect("pragma value");
        assert!(on, "PRAGMA foreign_keys did not take on this connection");

        let orphan = store
            .upsert_track(&track(9_999, FIRST_TIDAL_ID))
            .expect_err("a track with no album must be rejected");
        // Match the message, not merely the variant: a `Sqlite(_)` pattern also
        // matches a typo in the statement above, and this test would then pass
        // for a reason that has nothing to do with foreign keys.
        assert_constraint(&orphan, "FOREIGN KEY");

        let album = one_album(&store);
        assert!(store.upsert_track(&track(album, FIRST_TIDAL_ID)).is_ok());
    }

    #[test]
    fn a_transferred_track_needs_an_override_to_move() {
        let store = migrated();
        let album = one_album(&store);
        let id = store.upsert_track(&track(album, 1_234_567)).expect("track");

        store
            .write_status(id, TrackStatus::Transferred, None)
            .expect("transferred");

        let refused = store
            .write_status(id, TrackStatus::Skip, None)
            .expect_err("a transferred track must not move on its own");
        assert!(matches!(
            refused,
            StoreError::TransferredIsSacred { track_id } if track_id == id
        ));
        // The refusal has to leave nothing behind: not the status change, and
        // not an audit row for an override that was never supplied.
        assert_eq!(
            store.status_counts().expect("counts"),
            BTreeMap::from([(TrackStatus::Transferred, 1)])
        );
        assert_eq!(count(&store, "SELECT COUNT(*) FROM transfer_attempt"), 0);

        store
            .write_status(
                id,
                TrackStatus::Skip,
                Some(ReviewOverride {
                    yt_video_id: "AAAAAAAAAAA".to_string(),
                    started_at: "2026-10-07T00:00:00Z".to_string(),
                    outcome: "already_present".to_string(),
                }),
            )
            .expect("override");

        assert_eq!(
            store.status_counts().expect("counts"),
            BTreeMap::from([(TrackStatus::Skip, 1)])
        );
        let recorded = scalars(
            &store,
            "SELECT yt_video_id || '|' || outcome FROM transfer_attempt",
        );
        assert_eq!(recorded, ["AAAAAAAAAAA|already_present"]);
    }

    #[test]
    fn an_override_with_an_unknown_outcome_is_rejected_before_the_write() {
        let store = migrated();
        let album = one_album(&store);
        let id = store.upsert_track(&track(album, 1_234_567)).expect("track");

        let refused = store
            .write_status(
                id,
                TrackStatus::Skip,
                Some(ReviewOverride {
                    yt_video_id: "AAAAAAAAAAA".to_string(),
                    started_at: "2026-10-07T00:00:00Z".to_string(),
                    outcome: "banana".to_string(),
                }),
            )
            .expect_err("an unknown outcome must not reach the CHECK constraint");
        assert!(matches!(refused, StoreError::BadOutcome(outcome) if outcome == "banana"));
        // A rejected override must not have moved the track: the error names the
        // bad field, and a half-applied review would be worse than the bug.
        assert_eq!(
            store.status_counts().expect("counts"),
            BTreeMap::from([(TrackStatus::Pending, 1)])
        );
    }

    #[test]
    fn writing_the_status_of_a_missing_track_is_an_error() {
        let store = migrated();
        let refused = store
            .write_status(4_242, TrackStatus::Pending, None)
            .expect_err("no such track");
        assert!(matches!(
            refused,
            StoreError::NotFound { track_id } if track_id == 4_242
        ));
    }

    #[test]
    fn upserting_a_transferred_track_changes_nothing_at_all() {
        // The invariant asserted through the *absence* of another path, and
        // over the whole row rather than over `status` alone. Asserting only the
        // status is what let an earlier version of this function through: it
        // left `review_reason` and every `yt_*` column writable by a re-scan
        // that passes `review_reason: None`, which erases a human's decision and
        // points the row at a different video without any error.
        let store = migrated();
        let album = one_album(&store);
        let mut original = track(album, FIRST_TIDAL_ID);
        original.review_reason = Some("Checked against the vinyl".to_string());
        let id = store.upsert_track(&original).expect("track");
        store
            .write_status(id, TrackStatus::Transferred, None)
            .expect("transferred");
        store
            .insert_transfer_attempt(id, "CCCCCCCCCCC", "2026-10-07T00:00:00Z", "added")
            .expect("attempt");
        let before = whole_row(&store, id);

        // A re-scan's row: a different matcher outcome, no review note, and a
        // different video id — the three things that used to land silently.
        let mut fresh = track(album, FIRST_TIDAL_ID);
        fresh.title = "Renamed Song".to_string();
        fresh.confidence_overall = 0.1;
        fresh.match_method = MatchMethod::Fuzzy;
        fresh.review_reason = None;
        fresh.yt_video_id = Some("AAAAAAAAAAA".to_string());
        let again = store.upsert_track(&fresh).expect("re-upsert");

        assert_eq!(again, id);
        assert_eq!(whole_row(&store, id), before);
        assert_eq!(
            store.status_counts().expect("counts"),
            BTreeMap::from([(TrackStatus::Transferred, 1)])
        );
        // The attempt row still describes this row's video, which is the
        // correspondence a rewritten `yt_video_id` would have quietly broken.
        assert_eq!(
            scalars(&store, "SELECT yt_video_id FROM transfer_attempt"),
            ["CCCCCCCCCCC"]
        );
    }

    #[test]
    fn upserting_a_merely_pending_track_does_update_it() {
        // The other half of the guard: `WHERE track.status <> 'transferred'`
        // must skip only transferred rows. A guard that skipped every existing
        // row would satisfy the invariant test above and stop the store working.
        let store = migrated();
        let album = one_album(&store);
        let id = store
            .upsert_track(&track(album, FIRST_TIDAL_ID))
            .expect("track");

        let mut fresh = track(album, FIRST_TIDAL_ID);
        fresh.title = "Renamed Song".to_string();
        fresh.confidence_overall = 0.1;
        fresh.review_reason = Some("Needs a second look".to_string());
        store.upsert_track(&fresh).expect("re-upsert");

        let row = whole_row(&store, id);
        assert_eq!(row.status, "pending");
        assert_eq!(row.title, "Renamed Song");
        assert_eq!(row.review_reason.as_deref(), Some("Needs a second look"));
    }

    #[test]
    fn a_resync_leaves_a_match_and_a_transferred_row_untouched() {
        // The source write and the matcher's write must stay out of each
        // other's columns. A re-sync over a matched library arrives with a
        // freshly recomputed match row it must not apply, and over a
        // transferred row it must touch nothing at all: erasing either is the
        // bug this separate function exists so no caller can commit.
        let store = migrated();
        let album = one_album(&store);

        let mut matched = track(album, 1_234_567);
        matched.review_reason = Some("Checked against the vinyl".to_string());
        let matched_id = store.upsert_track(&matched).expect("track");

        let transferred_id = store.upsert_track(&track(album, 7_654_321)).expect("track");
        store
            .write_status(transferred_id, TrackStatus::Transferred, None)
            .expect("transferred");

        let matched_before = whole_row(&store, matched_id);
        let transferred_before = whole_row(&store, transferred_id);

        // The sync's row: new Tidal metadata plus a whole recomputed match —
        // every field the source write has no authority over.
        let mut resynced = track(album, 1_234_567);
        resynced.title = "Renamed Song".to_string();
        resynced.yt_video_id = Some("AAAAAAAAAAA".to_string());
        resynced.match_method = MatchMethod::Fuzzy;
        resynced.confidence_overall = 0.1;
        resynced.review_reason = None;
        assert_eq!(
            store.upsert_track_source(&resynced).expect("re-sync"),
            matched_id
        );

        let mut moved = track(album, 7_654_321);
        moved.title = "Renamed Song".to_string();
        moved.duration_sec = 195;
        assert_eq!(
            store.upsert_track_source(&moved).expect("re-sync"),
            transferred_id
        );

        // The optional columns and the status are exactly as the matcher and
        // the human left them; the title is refreshed, since Tidal metadata is
        // what this write owns.
        let matched_after = whole_row(&store, matched_id);
        assert_eq!(matched_after.title, "Renamed Song");
        assert_eq!(matched_after.yt_video_id, matched_before.yt_video_id);
        assert_eq!(matched_after.match_method, matched_before.match_method);
        assert_eq!(
            matched_after.confidence_overall,
            matched_before.confidence_overall
        );
        assert_eq!(matched_after.review_reason, matched_before.review_reason);
        assert_eq!(matched_after.status, matched_before.status);

        // A transferred row is skipped whole: its Tidal columns are a frozen
        // record of what was transferred too.
        assert_eq!(whole_row(&store, transferred_id), transferred_before);
    }

    #[test]
    fn a_resync_updates_the_tidal_columns() {
        // The other half of the split, the way `upserting_a_merely_pending_
        // track_does_update_it` guards against a guard that skips everything:
        // a source write that changed nothing would read as "safe" while the
        // library quietly drifts away from what Tidal says.
        let store = migrated();
        let album = one_album(&store);
        let mut first = track(album, FIRST_TIDAL_ID);
        first.version = Some("Remastered 2011".to_string());
        let id = store.upsert_track_source(&first).expect("insert");

        let mut resync = track(album, FIRST_TIDAL_ID);
        resync.title = "Renamed Song".to_string();
        resync.duration_sec = 195;
        resync.version = Some("Remastered 2024".to_string());
        store.upsert_track_source(&resync).expect("re-sync");

        let row = whole_row(&store, id);
        assert_eq!(row.title, "Renamed Song");
        assert_eq!(row.duration_sec, 195);
        assert_eq!(row.version.as_deref(), Some("Remastered 2024"));
        // What no source row can carry stays at its fresh-insert state: an
        // unexamined track is pending, unmatched, unscored and unmapped.
        assert_eq!(row.status, "pending");
        assert_eq!(row.yt_video_id, None);
        assert_eq!(row.match_method, "none");
        assert_eq!(row.confidence_overall, 0.0);
    }

    #[test]
    fn every_match_method_is_accepted_by_the_check_constraint() {
        // What is under test is the database *accepting* the value, not the
        // value. `MatchMethod::as_str()` and the DDL's `CHECK` list are two
        // declarations of the same set in two files, and nothing else in the
        // suite ties them together: a variant added to `model.rs` without a
        // matching literal passes every other test here and then fails in a
        // user's database.
        let store = migrated();
        let album = one_album(&store);
        let methods = [
            MatchMethod::Album,
            MatchMethod::Duration,
            MatchMethod::Fuzzy,
            MatchMethod::Manual,
            MatchMethod::NoMatch,
        ];
        for (index, method) in methods.into_iter().enumerate() {
            let mut row = track(album, FIRST_TIDAL_ID + index as i64);
            row.match_method = method;
            store
                .upsert_track(&row)
                .unwrap_or_else(|e| panic!("the CHECK rejected {}: {e}", method.as_str()));
        }
        let stored = scalars(&store, "SELECT DISTINCT match_method FROM track ORDER BY 1");
        assert_eq!(stored.len(), methods.len(), "{stored:?}");
    }

    #[test]
    fn every_transfer_outcome_is_accepted_by_the_check_constraint() {
        // The same cross-file agreement as `MatchMethod`, for the outcome list.
        // `write_status` validates `ReviewOverride::outcome` against its own
        // copy of that list, so a literal added to the DDL but not to `OUTCOMES`
        // is a row this crate cannot write at all.
        let store = migrated();
        let album = one_album(&store);
        let id = store
            .upsert_track(&track(album, FIRST_TIDAL_ID))
            .expect("track");
        for outcome in ["started", "added", "already_present", "failed"] {
            store
                .insert_transfer_attempt(id, "CCCCCCCCCCC", "2026-10-07T00:00:00Z", outcome)
                .unwrap_or_else(|e| panic!("the CHECK rejected {outcome}: {e}"));
        }
        assert_eq!(count(&store, "SELECT COUNT(*) FROM transfer_attempt"), 4);
    }

    #[test]
    fn status_counts_follow_the_rows() {
        let store = migrated();
        let album = one_album(&store);
        let mut expected = BTreeMap::new();
        for (index, status) in [
            TrackStatus::Pending,
            TrackStatus::NeedsReview,
            TrackStatus::Failed,
            TrackStatus::Pending,
        ]
        .into_iter()
        .enumerate()
        {
            let id = store
                .upsert_track(&track(album, 1_234_567 + index as i64))
                .expect("track");
            store.write_status(id, status, None).expect("status");
            *expected.entry(status).or_insert(0) += 1;
        }
        assert_eq!(store.status_counts().expect("counts"), expected);

        // The delete is the point: it proves the count is computed by a query
        // rather than stored and maintained, which is the property the program
        // design's removal of the `[meta]` counters rests on.
        store
            .conn()
            .execute(
                "DELETE FROM track WHERE tidal_id = ?1",
                rusqlite::params![FIRST_TIDAL_ID],
            )
            .expect("delete");
        let after = store.status_counts().expect("counts");
        assert_eq!(after.get(&TrackStatus::Pending), Some(&1));
        assert_eq!(after.get(&TrackStatus::NeedsReview), Some(&1));
        assert_eq!(after.get(&TrackStatus::Failed), Some(&1));
        assert!(!after.contains_key(&TrackStatus::Transferred));
    }

    #[test]
    fn the_database_rejects_an_unknown_transfer_outcome() {
        let store = migrated();
        let album = one_album(&store);
        let id = store.upsert_track(&track(album, 1_234_567)).expect("track");

        store
            .insert_transfer_attempt(id, "CCCCCCCCCCC", "2026-10-07T00:00:00Z", "added")
            .expect("a legal outcome");

        let refused = store
            .insert_transfer_attempt(id, "CCCCCCCCCCC", "2026-10-07T00:00:01Z", "banana")
            .expect_err("the CHECK constraint must reject this");
        assert_constraint(&refused, "CHECK constraint failed");
        assert_eq!(count(&store, "SELECT COUNT(*) FROM transfer_attempt"), 1);
    }

    #[test]
    fn a_candidate_cannot_be_recorded_twice_for_one_track() {
        let store = migrated();
        let album = one_album(&store);
        let id = store.upsert_track(&track(album, 1_234_567)).expect("track");
        let candidate = NewCandidate {
            rank: 1,
            yt_video_id: "AAAAAAAAAAA".to_string(),
            yt_title: "Fictional Song".to_string(),
            yt_artist: "Fictional Artist".to_string(),
            yt_album: Some("Fictional Album".to_string()),
            yt_duration_sec: Some(209),
            method: "fuzzy".to_string(),
            score: 0.61,
            summary: None,
        };

        store.insert_candidate(id, &candidate).expect("first");
        assert!(store.insert_candidate(id, &candidate).is_err());
        assert_eq!(count(&store, "SELECT COUNT(*) FROM candidate"), 1);
    }

    #[test]
    fn opening_a_file_creates_its_directory_and_survives_a_reopen() {
        // The first run of a packaged application has no data directory yet, so
        // `open` creating the parent is load-bearing rather than a convenience,
        // and the data has to still be there on the next launch. Both are
        // invisible to an in-memory store, which is every other test's subject.
        // Unique per run: a fixed name collides when the suite is run twice at
        // once, and the second run then fails in `open` for a reason that has
        // nothing to do with what it is testing.
        //
        // Only per *process*, not per test — this name has no test label in it,
        // so it is safe here solely because this is the module's only user of
        // it. libtest runs tests on parallel threads, so a second test that
        // reused this exact path would share the directory with this one and one
        // test's trailing `remove_dir_all` would delete the other's database
        // mid-flight. The next test to want a scratch directory here should
        // give it a label of its own; see `scratch_dir` in `src-tauri/src/lib.rs`
        // for the shape, where two callers already share a helper.
        let root = std::env::temp_dir().join(format!(
            "tidal2ytm-store-open-test-{}/nested",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let path = root.join("transfer.db");

        let store = Store::open(&path).expect("first open creates the directory");
        store.migrate().expect("migrate");
        let album = one_album(&store);
        let id = store
            .upsert_track(&track(album, FIRST_TIDAL_ID))
            .expect("track");
        store
            .write_status(id, TrackStatus::Transferred, None)
            .expect("transferred");
        drop(store);

        let reopened = Store::open(&path).expect("reopen");
        assert!(path.is_file());
        assert_eq!(reopened.schema_version().expect("version"), SCHEMA_VERSION);
        assert_eq!(
            reopened.status_counts().expect("counts"),
            BTreeMap::from([(TrackStatus::Transferred, 1)])
        );

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_newer_schema_is_refused_rather_than_migrated_over() {
        // A database written by a build with more migrations than this one knows
        // must stop here. Applying this build's idea of the schema on top would
        // either fail much later on a missing column or, worse, succeed against
        // a table this build has never seen a row of.
        let store = migrated();
        store
            .conn()
            .execute(&format!("PRAGMA user_version = {}", SCHEMA_VERSION + 1), [])
            .expect("pretend a newer build wrote this file");

        let refused = store.migrate().expect_err("a newer schema must be refused");
        assert!(matches!(
            refused,
            StoreError::UnknownSchemaVersion { found, known }
                if found == SCHEMA_VERSION + 1 && known == SCHEMA_VERSION
        ));
        // The error has to name both versions: "unknown schema version" alone
        // sends the reader to the schema instead of to the version numbers.
        let text = refused.to_string();
        assert!(text.contains(&(SCHEMA_VERSION + 1).to_string()), "{text}");
        assert!(text.contains(&SCHEMA_VERSION.to_string()), "{text}");
    }

    #[test]
    fn a_track_has_at_most_one_score_breakdown() {
        let store = migrated();
        let album = one_album(&store);
        let id = store.upsert_track(&track(album, 1_234_567)).expect("track");
        let first = ConfidenceBreakdown {
            overall: 0.82,
            title_similarity: Some(0.9),
            artist_similarity: Some(1.0),
            album_similarity: Some(0.8),
            duration_delta_sec: Some(-1),
            version_similarity: None,
            track_num_match: Some(true),
            album_coherence: Some(0.75),
            summary: Some("Close album call".to_string()),
        };
        let second = ConfidenceBreakdown {
            summary: Some("Re-scored".to_string()),
            ..first.clone()
        };

        store.upsert_match_confidence(id, &first).expect("first");
        store
            .upsert_match_confidence(id, &second)
            .expect("second replaces the first");

        assert_eq!(count(&store, "SELECT COUNT(*) FROM match_confidence"), 1);
        assert_eq!(
            scalars(&store, "SELECT summary FROM match_confidence"),
            ["Re-scored"]
        );
        // `overall` has no column in `match_confidence`, so it must not have
        // leaked into one; the row's own columns are what the table asserts.
        assert_eq!(
            scalars(
                &store,
                "SELECT CAST(track_num_match AS TEXT) FROM match_confidence"
            ),
            ["1"]
        );
    }

    #[test]
    fn album_names_differing_only_by_case_and_punctuation_are_one_row() {
        // The `name_key` decision, pinned. The DDL comment says
        // `casefold(name)`, but this project identifies a name with
        // `text::normalize` everywhere else — NFKD, ASCII-folded, lowercased,
        // punctuation stripped — and that is strictly stronger. A literal
        // `casefold` gives "Song" and "song!" two album rows, which is the bug
        // the key exists to prevent.
        let store = migrated();
        let artist = store
            .upsert_artist("Fictional Artist", "fictional artist")
            .expect("artist");

        let first = store
            .upsert_album(
                artist,
                "Song",
                &tidal2ytm_core::text::normalize("Song"),
                None,
            )
            .expect("first album");
        let second = store
            .upsert_album(
                artist,
                "song!",
                &tidal2ytm_core::text::normalize("song!"),
                None,
            )
            .expect("second album");

        assert_eq!(first, second);
        assert_eq!(count(&store, "SELECT COUNT(*) FROM album"), 1);
        // The display name is the later one: the upsert's job is to keep the
        // row's name current, and the key's job is to make it the same row.
        assert_eq!(scalars(&store, "SELECT name FROM album"), ["song!"]);
    }

    #[test]
    fn an_album_key_scopes_to_one_artist() {
        let store = migrated();
        let first = store
            .upsert_artist("Fictional Artist", "fictional artist")
            .expect("artist");
        let second = store
            .upsert_artist("Other Artist", "other artist")
            .expect("artist");

        let a = store
            .upsert_album(first, "Fictional Album", "fictional album", None)
            .expect("album");
        let b = store
            .upsert_album(second, "Fictional Album", "fictional album", None)
            .expect("album");

        // Two artists can each have an album with the same name, so the key is
        // the pair and not the name alone.
        assert_ne!(a, b);
        assert_eq!(count(&store, "SELECT COUNT(*) FROM album"), 2);
    }

    /// A page the sync writes, with every field filled as Tidal reports it.
    #[must_use]
    fn page_row(tidal_id: i64, artist: &str, album: &str) -> SourceRow {
        SourceRow {
            tidal_id,
            title: format!("{artist}'s track"),
            artist: artist.to_string(),
            album: album.to_string(),
            album_year: Some(2024),
            duration_sec: 210,
            track_num: 3,
            disc_num: 1,
            version: None,
        }
    }

    /// The `artist` → `album` → `track` chain the sync's write orders, seen
    /// through the rows it leaves behind: a page row becomes all three rows,
    /// joined, with the names keyed the way this project keys them — and the
    /// artist and album a *different* track shares are reused, not duplicated.
    #[test]
    fn a_source_page_writes_artist_album_and_track_in_foreign_key_order() {
        let store = migrated();
        let rows = [
            page_row(174_839_201, "Juna Reyes", "Marble Field"),
            page_row(174_839_202, "Juna Reyes", "Fen Hymns"),
            page_row(174_839_203, "Iris Moone", "Marble Field"),
        ];
        store.upsert_source_page(&rows).expect("the page commits");

        assert_eq!(count(&store, "SELECT COUNT(*) FROM track"), 3);
        // Albums are scoped per artist, so "Marble Field" is two rows.
        assert_eq!(count(&store, "SELECT COUNT(*) FROM album"), 3);
        assert_eq!(count(&store, "SELECT COUNT(*) FROM artist"), 2);
        assert_eq!(
            scalars(
                &store,
                "SELECT artist.name || '|' || album.name || '|' || track.title
                 FROM track
                 JOIN album ON album.id = track.album_id
                 JOIN artist ON artist.id = album.artist_id
                 ORDER BY track.tidal_id"
            ),
            [
                "Juna Reyes|Marble Field|Juna Reyes's track",
                "Juna Reyes|Fen Hymns|Juna Reyes's track",
                "Iris Moone|Marble Field|Iris Moone's track",
            ]
        );
        // The keys are `text::normalize`'s output, the same identity rule
        // `upsert_artist`'s own contract describes, computed here because the
        // sync's caller speaks Tidal's names and not keys.
        assert_eq!(
            scalars(&store, "SELECT name_key FROM artist ORDER BY name"),
            ["iris moone", "juna reyes"]
        );
    }

    /// The carried Review Focus case: a favourited track whose album name is
    /// empty or absent must not abort its page on a foreign key, but must also
    /// not hand every album-less track of one artist a separate album row more
    /// than names demand. One placeholder row named `""` per artist — the
    /// frontend renders it as `Unknown album` — keeps the foreign key resolvable
    /// and the artist's album-less tracks grouped where the schema groups them.
    #[test]
    fn an_album_less_favourite_upserts_one_placeholder_album_per_artist() {
        let store = migrated();
        let first = SourceRow {
            tidal_id: 174_839_204,
            title: "Farthest Row".to_string(),
            artist: "Ilse Vandermeer".to_string(),
            album: String::new(),
            album_year: None,
            duration_sec: 250,
            track_num: 1,
            disc_num: 1,
            version: Some("Single Edit".to_string()),
        };
        let second = page_row(174_839_205, "Ilse Vandermeer", "");
        let other = page_row(174_839_206, "Bram Oduya", "");

        store
            .upsert_source_page(&[first, second, other])
            .expect("an album-less track must not fail its page");
        store
            .upsert_source_page(&[page_row(174_839_207, "Ilse Vandermeer", "")])
            .expect("a repeated placeholder must not fail its page");

        // Two artists, two placeholder rows, and no third one after the repeat.
        assert_eq!(
            count(&store, "SELECT COUNT(*) FROM album WHERE name = ''"),
            2,
        );
        assert_eq!(count(&store, "SELECT COUNT(*) FROM artist"), 2,);
        assert_eq!(count(&store, "SELECT COUNT(*) FROM track"), 4);
        // Each artist's placeholder is theirs alone; neither artist's
        // album-less tracks landed on the other's row.
        assert_eq!(
            scalars(
                &store,
                "SELECT artist.name
                 FROM album JOIN artist ON artist.id = album.artist_id
                 WHERE album.name = ''
                 ORDER BY artist.name"
            ),
            ["Bram Oduya", "Ilse Vandermeer"]
        );
    }

    #[test]
    fn a_meta_key_reads_back_what_was_written_and_replaces_on_a_second_write() {
        let store = migrated();

        store
            .set_meta("sync_started_at", "2026-10-22T10:00:00Z")
            .expect("first write");
        assert_eq!(
            store.get_meta("sync_started_at").expect("read"),
            Some("2026-10-22T10:00:00Z".to_string())
        );

        store
            .set_meta("sync_started_at", "2026-10-22T11:30:00Z")
            .expect("second write");
        assert_eq!(
            store.get_meta("sync_started_at").expect("read"),
            Some("2026-10-22T11:30:00Z".to_string()),
            "the second write replaces the first, or a resumed sync would read stale"
        );
    }

    #[test]
    fn a_meta_key_nobody_wrote_or_that_was_deleted_reads_as_none() {
        let store = migrated();

        assert_eq!(
            store.get_meta("sync_last_completed_at").expect("read"),
            None,
            "an unwritten key must read as absent, not as an empty string"
        );

        store
            .set_meta("sync_started_at", "2026-10-22T10:00:00Z")
            .expect("write");
        store.delete_meta("sync_started_at").expect("delete");
        assert_eq!(
            store.get_meta("sync_started_at").expect("read"),
            None,
            "deleting the beginning stamp is what a successful completion does to it"
        );
    }
}
