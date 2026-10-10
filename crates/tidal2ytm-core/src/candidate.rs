//! The shapes a YouTube Music answer is normalized into before it reaches the
//! domain core.
//!
//! The core never sees a YTM field name. Request and response shaping is the
//! Python that cannot be ported without reimplementing two unaudited API
//! clients, so normalization belongs on the Python side of the boundary, and a
//! YTM field rename then touches `sidecar/tidal2ytm_sidecar/envelope.py` and
//! nothing else. The field names here are the wire, and
//! `contracts/candidate.json`, `contracts/album_summary.json` and
//! `contracts/album_detail.json` are the one place that wire is written down.
//!
//! `AlbumDetail` is not in the program design's two types. It exists because
//! `_best_track_fit` and `_map_winner_tracks` consume an album's fetched
//! tracklist and `_dedupe_details` consumes its `other_versions` shelf, so that
//! shape has to be fixed somewhere, and a contract is the only place it can be
//! fixed once.

use serde::{Deserialize, Serialize};

/// One song, as `search.songs` returns it and as an album's tracklist holds it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Candidate {
    pub video_id: String,
    pub title: String,
    pub artists: Vec<String>,
    pub album: Option<String>,
    pub album_track_num: Option<i64>,
    pub duration_sec: Option<i64>,
}

/// One album, as `search.albums` returns it and as an album's `other_versions`
/// shelf holds it.
///
/// `artist` is singular where a `Candidate`'s is a list: the Python it replaces
/// takes a maximum over an album's whole artist list, and taking one name here
/// means multi-artist album matching is unexercised through the envelope until
/// a real client can supply the full list.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AlbumSummary {
    pub album_id: String,
    pub title: String,
    pub artist: String,
    pub year: Option<i64>,
}

/// One album with the two shelves that ranking reads off it: its tracklist, and
/// the other versions of itself that are candidates for the same source tracks.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AlbumDetail {
    pub summary: AlbumSummary,
    pub tracks: Vec<Candidate>,
    pub other_versions: Vec<AlbumSummary>,
}
