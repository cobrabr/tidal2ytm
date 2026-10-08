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

use tidal2ytm_core::model::{ConfidenceBreakdown, MatchMethod, TrackStatus};

use crate::migrate;
use crate::schema::MIGRATIONS;

/// The schema version this build knows, which is how many migrations exist.
pub const SCHEMA_VERSION: i64 = MIGRATIONS.len() as i64;

/// Everything that can go wrong on the way to or inside the database.
#[derive(Debug, thiserror::Error)]
// `libsql::Error` is `#[non_exhaustive]` and derives neither `Clone` nor
// `PartialEq`, so this enum cannot carry the two derives the other error types
// in the workspace use. Matching on a variant is the way to assert on one.
pub enum StoreError {
    #[error("database error: {0}")]
    Libsql(#[from] libsql::Error),
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

/// A connection to the application's database.
pub struct Store {
    conn: libsql::Connection,
}

impl Store {
    /// Open the database file at `path`, creating its parent directory if it is
    /// missing.
    ///
    /// The parent directory is created here rather than by the caller because
    /// the first run of a packaged application has no data directory yet, and a
    /// missing directory surfaces as an opaque `unable to open database file`
    /// rather than as the thing that is actually wrong.
    pub async fn open(path: &Path) -> Result<Self, StoreError> {
        if let Some(parent) = path.parent()
            && !parent.as_os_str().is_empty()
        {
            std::fs::create_dir_all(parent)?;
        }
        Self::connect(path).await
    }

    /// Open a private, empty, in-memory database.
    ///
    /// Every call gets its own database, because libsql opens `:memory:` fresh
    /// on each `connect` — two stores opened this way share nothing. That is
    /// what lets a test assert on counts without a teardown step.
    pub async fn open_in_memory() -> Result<Self, StoreError> {
        Self::connect(Path::new(":memory:")).await
    }

    async fn connect(path: &Path) -> Result<Self, StoreError> {
        let db = libsql::Builder::new_local(path).build().await?;
        let conn = db.connect()?;
        // Foreign keys are off by default in SQLite, and the pragma is per
        // connection rather than per database, so a connection opened without
        // it enforces none of the `REFERENCES` clauses in the schema. Setting it
        // once at open is the only place it has to be set: every later
        // connection this process makes goes through here.
        conn.execute("PRAGMA foreign_keys = ON", ()).await?;
        Ok(Self { conn })
    }

    /// Apply any migration this database has not had yet.
    pub async fn migrate(&self) -> Result<(), StoreError> {
        migrate::migrate(&self.conn).await
    }

    /// The schema version currently recorded in the database.
    pub async fn schema_version(&self) -> Result<i64, StoreError> {
        migrate::read_user_version(&self.conn).await
    }

    /// Insert the artist, or update its display name, and return its id.
    ///
    /// `name_key` is `text::normalize(name)`, which is NFKD decomposition, an
    /// ASCII fold, lowercasing and punctuation removal. The DDL's `-- casefold(name)`
    /// comment describes the intent — one row per name regardless of spelling —
    /// not the literal operation, and the whole rest of this project identifies a
    /// name with `normalize`. Honouring `casefold` literally would give "Song" and
    /// "song!" two album rows, which is the bug the key exists to prevent.
    pub async fn upsert_artist(&self, name: &str, name_key: &str) -> Result<i64, StoreError> {
        self.conn
            .execute(
                "INSERT INTO artist (name, name_key) VALUES (?1, ?2)
                 ON CONFLICT(name_key) DO UPDATE SET name = excluded.name",
                libsql::params![name, name_key],
            )
            .await?;
        self.artist_id(name_key).await
    }

    /// Insert the album, or update its name and year, and return its id.
    ///
    /// The album-level `match_conf`, `match_margin` and `match_reason` columns
    /// are deliberately absent from both the insert and the conflict clause: they
    /// belong to album matching, which runs after the album exists, and an
    /// upsert that rewrote them would erase a resolved winner every time the
    /// library is re-scanned.
    pub async fn upsert_album(
        &self,
        artist_id: i64,
        name: &str,
        name_key: &str,
        year: Option<i64>,
    ) -> Result<i64, StoreError> {
        self.conn
            .execute(
                "INSERT INTO album (artist_id, name, name_key, year) VALUES (?1, ?2, ?3, ?4)
                 ON CONFLICT(artist_id, name_key) DO UPDATE SET
                   name = excluded.name,
                   year = excluded.year",
                libsql::params![artist_id, name, name_key, year],
            )
            .await?;
        self.album_id(artist_id, name_key).await
    }

    /// Insert the track, or update everything about it except its status, and
    /// return its id.
    ///
    /// The conflict clause omits `status`, and that omission is the design.
    /// `status` is written by exactly one function, [`Store::write_status`], and
    /// a second writer is what would let a routine re-scan quietly un-transfer a
    /// track. Making the upsert structurally unable to set it is stronger than
    /// asking every caller to remember not to, and it costs one column's absence.
    ///
    /// On insert the status is the literal `'pending'`, because a track that has
    /// never been examined cannot be anything else.
    pub async fn upsert_track(&self, row: &TrackRow) -> Result<i64, StoreError> {
        self.conn
            .execute(
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
                   review_reason = excluded.review_reason",
                libsql::params![
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
            )
            .await?;
        self.track_id(row.tidal_id).await
    }

    /// Write the track's status, and return.
    ///
    /// This is the only function in the project that assigns `status`. Moving a
    /// row away from `transferred` requires `over`, and the `transfer_attempt`
    /// row it names is written in the same transaction — so an override cannot
    /// be recorded without taking effect, or take effect without being
    /// recorded.
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
    pub async fn write_status(
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

        let tx = self
            .conn
            .transaction_with_behavior(libsql::TransactionBehavior::Immediate)
            .await?;
        // The read belongs inside the transaction, not before it. A read
        // outside is a race with any writer that commits before this one starts,
        // and the invariant has to hold for the write that actually lands.
        let current = Self::status_in(&tx, track_id).await?;
        if current == TrackStatus::Transferred && new != TrackStatus::Transferred {
            let Some(o) = over else {
                // Returning here drops `tx`, and libsql's `Transaction` rolls
                // back when dropped, so nothing has been written.
                return Err(StoreError::TransferredIsSacred { track_id });
            };
            tx.execute(
                "INSERT INTO transfer_attempt (track_id, yt_video_id, started_at, outcome)
                 VALUES (?1, ?2, ?3, ?4)",
                libsql::params![
                    track_id,
                    o.yt_video_id.as_str(),
                    o.started_at.as_str(),
                    o.outcome.as_str()
                ],
            )
            .await?;
        }
        tx.execute(
            "UPDATE track SET status = ?1 WHERE id = ?2",
            libsql::params![new.as_str(), track_id],
        )
        .await?;
        // Mandatory: libsql's `Transaction` rolls back when it is dropped, so a
        // missing `commit()` discards both the override row and the status
        // change with no error to show for it.
        tx.commit().await?;
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
    pub async fn upsert_match_confidence(
        &self,
        track_id: i64,
        breakdown: &ConfidenceBreakdown,
    ) -> Result<(), StoreError> {
        self.conn
            .execute(
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
                libsql::params![
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
            )
            .await?;
        Ok(())
    }

    /// Record one candidate and return its id.
    ///
    /// A plain insert: `UNIQUE (track_id, yt_video_id)` means the same video
    /// offered twice is an error rather than a duplicate row, and a candidate
    /// list that silently grows on every re-scan is worse than one that says so.
    pub async fn insert_candidate(
        &self,
        track_id: i64,
        candidate: &NewCandidate,
    ) -> Result<i64, StoreError> {
        self.conn
            .execute(
                "INSERT INTO candidate (
                   track_id, rank, yt_video_id, yt_title, yt_artist, yt_album, yt_duration_sec,
                   method, score, summary
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                libsql::params![
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
            )
            .await?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Record a transfer attempt and return its id.
    ///
    /// Unlike `write_status`, this does not validate `outcome`: it is the raw
    /// write the transfer flow uses to log an attempt before the remote add, and
    /// it accepts whatever the database's `CHECK` constraint accepts, which is
    /// the one list of legal outcomes.
    pub async fn insert_transfer_attempt(
        &self,
        track_id: i64,
        yt_video_id: &str,
        started_at: &str,
        outcome: &str,
    ) -> Result<i64, StoreError> {
        self.conn
            .execute(
                "INSERT INTO transfer_attempt (track_id, yt_video_id, started_at, outcome)
                 VALUES (?1, ?2, ?3, ?4)",
                libsql::params![track_id, yt_video_id, started_at, outcome],
            )
            .await?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Count the tracks in each status.
    ///
    /// This count is computed by the database from the rows themselves, which is
    /// the whole reason the program design deleted the TOML plan's recomputed
    /// `[meta]` counters: a number derived by a query cannot disagree with the
    /// rows it counts, because there is only one of it.
    pub async fn status_counts(&self) -> Result<BTreeMap<TrackStatus, i64>, StoreError> {
        let mut rows = self
            .conn
            .query("SELECT status, COUNT(*) FROM track GROUP BY status", ())
            .await?;
        let mut counts = BTreeMap::new();
        while let Some(row) = rows.next().await? {
            let raw: String = row.get(0)?;
            let count: i64 = row.get(1)?;
            let status = raw
                .parse::<TrackStatus>()
                .map_err(|_| StoreError::UnknownStatus(raw.clone()))?;
            counts.insert(status, count);
        }
        Ok(counts)
    }

    async fn artist_id(&self, name_key: &str) -> Result<i64, StoreError> {
        let mut rows = self
            .conn
            .query("SELECT id FROM artist WHERE name_key = ?1", [name_key])
            .await?;
        Self::one_id(&mut rows).await
    }

    async fn album_id(&self, artist_id: i64, name_key: &str) -> Result<i64, StoreError> {
        let mut rows = self
            .conn
            .query(
                "SELECT id FROM album WHERE artist_id = ?1 AND name_key = ?2",
                libsql::params![artist_id, name_key],
            )
            .await?;
        Self::one_id(&mut rows).await
    }

    async fn track_id(&self, tidal_id: i64) -> Result<i64, StoreError> {
        let mut rows = self
            .conn
            .query("SELECT id FROM track WHERE tidal_id = ?1", [tidal_id])
            .await?;
        Self::one_id(&mut rows).await
    }

    async fn one_id(rows: &mut libsql::Rows) -> Result<i64, StoreError> {
        let row = rows
            .next()
            .await?
            .ok_or(libsql::Error::QueryReturnedNoRows)?;
        Ok(row.get::<i64>(0)?)
    }

    /// Read a track's status. Missing row is an error rather than a default:
    /// `pending` would let a write against a nonexistent id look like it worked.
    async fn status_in(
        conn: &impl std::ops::Deref<Target = libsql::Connection>,
        track_id: i64,
    ) -> Result<TrackStatus, StoreError> {
        let mut rows = conn
            .query("SELECT status FROM track WHERE id = ?1", [track_id])
            .await?;
        let row = rows
            .next()
            .await?
            .ok_or(StoreError::NotFound { track_id })?;
        let raw: String = row.get(0)?;
        raw.parse::<TrackStatus>()
            .map_err(|_| StoreError::UnknownStatus(raw.clone()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn migrated() -> Store {
        let store = Store::open_in_memory().await.expect("open");
        store.migrate().await.expect("migrate");
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

    async fn one_album(store: &Store) -> i64 {
        let artist = store
            .upsert_artist("Fictional Artist", "fictional artist")
            .await
            .expect("artist");
        store
            .upsert_album(artist, "Fictional Album", "fictional album", Some(2024))
            .await
            .expect("album")
    }

    async fn scalars(store: &Store, sql: &str) -> Vec<String> {
        let mut rows = store.conn.query(sql, ()).await.expect("query");
        let mut out = Vec::new();
        while let Some(row) = rows.next().await.expect("next") {
            out.push(row.get::<String>(0).expect("scalar"));
        }
        out
    }

    /// A fictional Tidal id. Bound as a parameter everywhere it reaches SQL,
    /// which is also what keeps a Rust numeric literal's `_` separators out of a
    /// statement string.
    const FIRST_TIDAL_ID: i64 = 1_234_567;

    async fn count(store: &Store, sql: &str) -> i64 {
        let mut rows = store.conn.query(sql, ()).await.expect("query");
        rows.next()
            .await
            .expect("next")
            .expect("one row")
            .get::<i64>(0)
            .expect("count")
    }

    #[tokio::test]
    async fn migrating_an_empty_database_creates_every_table() {
        let store = migrated().await;
        assert_eq!(store.schema_version().await.expect("version"), 1);

        let mut names = scalars(
            &store,
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
        )
        .await;
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

    #[tokio::test]
    async fn migrating_twice_changes_nothing() {
        // Idempotency is a tested property rather than an incidental one: the
        // host opens the database on every launch, so a second run is the normal
        // case and not an edge case. The DDL is plain `CREATE TABLE` with no
        // `IF NOT EXISTS`, so a runner that re-applied step 1 would fail on the
        // first existing table; the row written here is what proves the tables
        // were left alone rather than rebuilt.
        let store = migrated().await;
        let album = one_album(&store).await;
        let id = store
            .upsert_track(&track(album, 1_234_567))
            .await
            .expect("track");

        store.migrate().await.expect("second migrate");

        assert_eq!(store.schema_version().await.expect("version"), 1);
        assert_eq!(count(&store, "SELECT COUNT(*) FROM track").await, 1);
        assert_eq!(store.status_counts().await.expect("counts").len(), 1);
        assert!(id > 0);
    }

    #[tokio::test]
    async fn foreign_keys_are_enforced() {
        let store = migrated().await;
        // Read the pragma before asserting on a rejection. "The insert failed"
        // is otherwise indistinguishable from "the constraint is not on", and
        // this test would pass for the wrong reason on a connection opened
        // without the pragma.
        let mut pragma = store
            .conn
            .query("PRAGMA foreign_keys", ())
            .await
            .expect("pragma");
        let on = pragma
            .next()
            .await
            .expect("next")
            .expect("one row")
            .get::<bool>(0)
            .expect("pragma value");
        assert!(on, "PRAGMA foreign_keys did not take on this connection");

        let orphan = store
            .upsert_track(&track(9_999, 1_234_567))
            .await
            .expect_err("a track with no album must be rejected");
        assert!(matches!(orphan, StoreError::Libsql(_)));

        let album = one_album(&store).await;
        assert!(store.upsert_track(&track(album, 1_234_567)).await.is_ok());
    }

    #[tokio::test]
    async fn a_transferred_track_needs_an_override_to_move() {
        let store = migrated().await;
        let album = one_album(&store).await;
        let id = store
            .upsert_track(&track(album, 1_234_567))
            .await
            .expect("track");

        store
            .write_status(id, TrackStatus::Transferred, None)
            .await
            .expect("transferred");

        let refused = store
            .write_status(id, TrackStatus::Skip, None)
            .await
            .expect_err("a transferred track must not move on its own");
        assert!(matches!(
            refused,
            StoreError::TransferredIsSacred { track_id } if track_id == id
        ));
        // The refusal has to leave nothing behind: not the status change, and
        // not an audit row for an override that was never supplied.
        assert_eq!(
            store.status_counts().await.expect("counts"),
            BTreeMap::from([(TrackStatus::Transferred, 1)])
        );
        assert_eq!(
            count(&store, "SELECT COUNT(*) FROM transfer_attempt").await,
            0
        );

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
            .await
            .expect("override");

        assert_eq!(
            store.status_counts().await.expect("counts"),
            BTreeMap::from([(TrackStatus::Skip, 1)])
        );
        let recorded = scalars(
            &store,
            "SELECT yt_video_id || '|' || outcome FROM transfer_attempt",
        )
        .await;
        assert_eq!(recorded, ["AAAAAAAAAAA|already_present"]);
    }

    #[tokio::test]
    async fn an_override_with_an_unknown_outcome_is_rejected_before_the_write() {
        let store = migrated().await;
        let album = one_album(&store).await;
        let id = store
            .upsert_track(&track(album, 1_234_567))
            .await
            .expect("track");

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
            .await
            .expect_err("an unknown outcome must not reach the CHECK constraint");
        assert!(matches!(refused, StoreError::BadOutcome(outcome) if outcome == "banana"));
        // A rejected override must not have moved the track: the error names the
        // bad field, and a half-applied review would be worse than the bug.
        assert_eq!(
            store.status_counts().await.expect("counts"),
            BTreeMap::from([(TrackStatus::Pending, 1)])
        );
    }

    #[tokio::test]
    async fn writing_the_status_of_a_missing_track_is_an_error() {
        let store = migrated().await;
        let refused = store
            .write_status(4_242, TrackStatus::Pending, None)
            .await
            .expect_err("no such track");
        assert!(matches!(
            refused,
            StoreError::NotFound { track_id } if track_id == 4_242
        ));
    }

    #[tokio::test]
    async fn upserting_a_transferred_track_leaves_its_status_alone() {
        // The invariant asserted through the *absence* of another path. If
        // someone later adds `status` to the upsert's conflict clause, this is
        // the test that fails — a re-scan of the library silently un-transferring
        // every track it touches.
        let store = migrated().await;
        let album = one_album(&store).await;
        let id = store
            .upsert_track(&track(album, 1_234_567))
            .await
            .expect("track");
        store
            .write_status(id, TrackStatus::Transferred, None)
            .await
            .expect("transferred");

        let mut fresh = track(album, 1_234_567);
        fresh.title = "Renamed Song".to_string();
        fresh.confidence_overall = 0.1;
        let again = store.upsert_track(&fresh).await.expect("re-upsert");
        assert_eq!(again, id);

        assert_eq!(
            store.status_counts().await.expect("counts"),
            BTreeMap::from([(TrackStatus::Transferred, 1)])
        );
        // The rest of the upsert did land, so this is not a test that passes
        // because the second write did nothing at all.
        assert_eq!(
            scalars(&store, "SELECT title FROM track").await,
            ["Renamed Song"]
        );
    }

    #[tokio::test]
    async fn status_counts_follow_the_rows() {
        let store = migrated().await;
        let album = one_album(&store).await;
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
                .await
                .expect("track");
            store.write_status(id, status, None).await.expect("status");
            *expected.entry(status).or_insert(0) += 1;
        }
        assert_eq!(store.status_counts().await.expect("counts"), expected);

        // The delete is the point: it proves the count is computed by a query
        // rather than stored and maintained, which is the property the program
        // design's removal of the `[meta]` counters rests on.
        store
            .conn
            .execute(
                "DELETE FROM track WHERE tidal_id = ?1",
                libsql::params![FIRST_TIDAL_ID],
            )
            .await
            .expect("delete");
        let after = store.status_counts().await.expect("counts");
        assert_eq!(after.get(&TrackStatus::Pending), Some(&1));
        assert_eq!(after.get(&TrackStatus::NeedsReview), Some(&1));
        assert_eq!(after.get(&TrackStatus::Failed), Some(&1));
        assert!(!after.contains_key(&TrackStatus::Transferred));
    }

    #[tokio::test]
    async fn the_database_rejects_an_unknown_transfer_outcome() {
        let store = migrated().await;
        let album = one_album(&store).await;
        let id = store
            .upsert_track(&track(album, 1_234_567))
            .await
            .expect("track");

        store
            .insert_transfer_attempt(id, "CCCCCCCCCCC", "2026-10-07T00:00:00Z", "added")
            .await
            .expect("a legal outcome");

        let refused = store
            .insert_transfer_attempt(id, "CCCCCCCCCCC", "2026-10-07T00:00:01Z", "banana")
            .await
            .expect_err("the CHECK constraint must reject this");
        assert!(matches!(refused, StoreError::Libsql(_)));
        assert_eq!(
            count(&store, "SELECT COUNT(*) FROM transfer_attempt").await,
            1
        );
    }

    #[tokio::test]
    async fn a_candidate_cannot_be_recorded_twice_for_one_track() {
        let store = migrated().await;
        let album = one_album(&store).await;
        let id = store
            .upsert_track(&track(album, 1_234_567))
            .await
            .expect("track");
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

        let first = store.insert_candidate(id, &candidate).await.expect("first");
        assert!(store.insert_candidate(id, &candidate).await.is_err());
        assert_eq!(count(&store, "SELECT COUNT(*) FROM candidate").await, 1);
        assert!(first > 0);
    }

    #[tokio::test]
    async fn opening_a_file_creates_its_directory_and_survives_a_reopen() {
        // The first run of a packaged application has no data directory yet, so
        // `open` creating the parent is load-bearing rather than a convenience,
        // and the data has to still be there on the next launch. Both are
        // invisible to an in-memory store, which is every other test's subject.
        let root = std::env::temp_dir().join("tidal2ytm-store-open-test/nested");
        let _ = std::fs::remove_dir_all(&root);
        let path = root.join("transfer.db");

        let store = Store::open(&path)
            .await
            .expect("first open creates the directory");
        store.migrate().await.expect("migrate");
        let album = one_album(&store).await;
        let id = store
            .upsert_track(&track(album, FIRST_TIDAL_ID))
            .await
            .expect("track");
        store
            .write_status(id, TrackStatus::Transferred, None)
            .await
            .expect("transferred");
        drop(store);

        let reopened = Store::open(&path).await.expect("reopen");
        assert!(path.is_file());
        assert_eq!(reopened.schema_version().await.expect("version"), 1);
        assert_eq!(
            reopened.status_counts().await.expect("counts"),
            BTreeMap::from([(TrackStatus::Transferred, 1)])
        );

        let _ = std::fs::remove_dir_all(&root);
    }

    #[tokio::test]
    async fn a_newer_schema_is_refused_rather_than_migrated_over() {
        // A database written by a build with more migrations than this one knows
        // must stop here. Applying this build's idea of the schema on top would
        // either fail much later on a missing column or, worse, succeed against
        // a table this build has never seen a row of.
        let store = migrated().await;
        store
            .conn
            .execute(&format!("PRAGMA user_version = {}", SCHEMA_VERSION + 1), ())
            .await
            .expect("pretend a newer build wrote this file");

        let refused = store
            .migrate()
            .await
            .expect_err("a newer schema must be refused");
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

    #[tokio::test]
    async fn a_track_has_at_most_one_score_breakdown() {
        let store = migrated().await;
        let album = one_album(&store).await;
        let id = store
            .upsert_track(&track(album, 1_234_567))
            .await
            .expect("track");
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

        store
            .upsert_match_confidence(id, &first)
            .await
            .expect("first");
        store
            .upsert_match_confidence(id, &second)
            .await
            .expect("second replaces the first");

        assert_eq!(
            count(&store, "SELECT COUNT(*) FROM match_confidence").await,
            1
        );
        assert_eq!(
            scalars(&store, "SELECT summary FROM match_confidence").await,
            ["Re-scored"]
        );
        // `overall` has no column in `match_confidence`, so it must not have
        // leaked into one; the row's own columns are what the table asserts.
        assert_eq!(
            scalars(
                &store,
                "SELECT CAST(track_num_match AS TEXT) FROM match_confidence"
            )
            .await,
            ["1"]
        );
    }

    #[tokio::test]
    async fn album_names_differing_only_by_case_and_punctuation_are_one_row() {
        // The `name_key` decision, pinned. The DDL comment says
        // `casefold(name)`, but this project identifies a name with
        // `text::normalize` everywhere else — NFKD, ASCII-folded, lowercased,
        // punctuation stripped — and that is strictly stronger. A literal
        // `casefold` gives "Song" and "song!" two album rows, which is the bug
        // the key exists to prevent.
        let store = migrated().await;
        let artist = store
            .upsert_artist("Fictional Artist", "fictional artist")
            .await
            .expect("artist");

        let first = store
            .upsert_album(
                artist,
                "Song",
                &tidal2ytm_core::text::normalize("Song"),
                None,
            )
            .await
            .expect("first album");
        let second = store
            .upsert_album(
                artist,
                "song!",
                &tidal2ytm_core::text::normalize("song!"),
                None,
            )
            .await
            .expect("second album");

        assert_eq!(first, second);
        assert_eq!(count(&store, "SELECT COUNT(*) FROM album").await, 1);
        // The display name is the later one: the upsert's job is to keep the
        // row's name current, and the key's job is to make it the same row.
        assert_eq!(scalars(&store, "SELECT name FROM album").await, ["song!"]);
    }

    #[tokio::test]
    async fn an_album_key_scopes_to_one_artist() {
        let store = migrated().await;
        let first = store
            .upsert_artist("Fictional Artist", "fictional artist")
            .await
            .expect("artist");
        let second = store
            .upsert_artist("Other Artist", "other artist")
            .await
            .expect("artist");

        let a = store
            .upsert_album(first, "Fictional Album", "fictional album", None)
            .await
            .expect("album");
        let b = store
            .upsert_album(second, "Fictional Album", "fictional album", None)
            .await
            .expect("album");

        // Two artists can each have an album with the same name, so the key is
        // the pair and not the name alone.
        assert_ne!(a, b);
        assert_eq!(count(&store, "SELECT COUNT(*) FROM album").await, 2);
    }
}
