//! The schema, copied verbatim from the program design's Storage section.
//!
//! There are no local edits here, and that is the point: a diff in this file
//! means the program design changed, not that a column moved because it was
//! tidier somewhere else. Every absence in the DDL is a recorded decision
//! rather than an oversight — no ISRC columns, no stored counters, no slug
//! columns — and undoing one of them here would undo the decision silently.

/// Applied in order, one version per element. `PRAGMA user_version` holds the
/// index of the last element applied, so the array length is the version this
/// build knows and nothing above it is.
pub const MIGRATIONS: &[&str] = &[r#"
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
"#];
