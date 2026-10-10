//! Shared fixture-row support for the parity tests.
//!
//! `SourceTrack` derives no serde impls of its own, so every parity test that
//! reads a `SourceTrack` row needs the same translation from the committed JSON
//! into the domain type. Three copies of that translation would be three places
//! for the field list to drift from `SourceTrack`, which is the point of a
//! fixture: a field the port dropped would then go unnoticed by whichever copy
//! still happened to name it. This module is that translation, once.

use serde::Deserialize;
use serde_json::Value;
use tidal2ytm_core::model::SourceTrack;

/// The `SourceTrack` as a fixture row carries it.
///
/// Every field is required: a row that omitted one would be asserting a default
/// the port never had a rule for.
#[derive(Deserialize)]
struct SourceTrackRow {
    tidal_id: i64,
    title: String,
    artist: String,
    artists: Vec<String>,
    album: String,
    album_id: i64,
    album_year: Option<i64>,
    duration_sec: i64,
    track_num: i64,
    disc_num: i64,
    version: Option<String>,
}

impl From<SourceTrackRow> for SourceTrack {
    fn from(row: SourceTrackRow) -> Self {
        Self {
            tidal_id: row.tidal_id,
            title: row.title,
            artist: row.artist,
            artists: row.artists,
            album: row.album,
            album_id: row.album_id,
            album_year: row.album_year,
            duration_sec: row.duration_sec,
            track_num: row.track_num,
            disc_num: row.disc_num,
            version: row.version,
        }
    }
}

/// Deserialize a fixture `Value` into a `SourceTrack`, naming the case on failure.
pub fn track_from_value(value: &Value, case: usize, what: &str) -> SourceTrack {
    let row: SourceTrackRow = serde_json::from_value(value.clone())
        .unwrap_or_else(|e| panic!("case {case}: {what} is a SourceTrack row: {e}"));
    row.into()
}
