//! The vocabulary every persisted row is written in.
//!
//! `TrackStatus` and `MatchMethod` are the Rust side of the schema's `CHECK`
//! constraints: each variant's `as_str()` returns exactly one of the literals
//! the DDL permits, and their serde names are the same literals, so a value
//! that reaches the database is always one the constraint would have accepted
//! however it got there. `ConfidenceBreakdown` is the port of
//! `tidal2ytm.models.ConfidenceBreakdown`, the score components the matcher
//! computes and `match_confidence` stores. `SourceTrack` and `MatchResult` are
//! the ports of the two dataclasses the matcher returns.

use std::str::FromStr;

use serde::{Deserialize, Serialize};

/// Where a track sits in the transfer workflow.
///
/// Every literal here appears in the `track.status` `CHECK` constraint in
/// `tidal2ytm-store`'s schema, so a status can never be written that the
/// database would reject.
///
/// `Ord` exists because `status_counts` returns a `BTreeMap` keyed by this
/// type: the counts have to come back in a defined order, and the declaration
/// order is the workflow's own order, earliest first.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TrackStatus {
    Pending,
    Transferred,
    Skip,
    Failed,
    NeedsReview,
}

impl TrackStatus {
    /// The persisted form, identical to the `CHECK` constraint's literal.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Transferred => "transferred",
            Self::Skip => "skip",
            Self::Failed => "failed",
            Self::NeedsReview => "needs_review",
        }
    }
}

impl FromStr for TrackStatus {
    type Err = String;

    fn from_str(raw: &str) -> Result<Self, Self::Err> {
        match raw {
            "pending" => Ok(Self::Pending),
            "transferred" => Ok(Self::Transferred),
            "skip" => Ok(Self::Skip),
            "failed" => Ok(Self::Failed),
            "needs_review" => Ok(Self::NeedsReview),
            other => Err(other.to_string()),
        }
    }
}

/// How a track came to be paired with a YouTube video.
///
/// The serde names are the schema's `CHECK` literals, exactly as `as_str()`
/// returns them. `rename_all` lowercases every variant the DDL spells in lower
/// case; the per-variant rename on `NoMatch` overrides the `no_match` that
/// `snake_case` would otherwise produce, because the persisted string is `none`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MatchMethod {
    Duration,
    Fuzzy,
    Album,
    /// The variant is named for what it means rather than for its wire value:
    /// a `None` variant would shadow `Option::None` in any `match` that also
    /// handles an absent match. It serializes as `"none"`, and so does its
    /// `FromStr`.
    #[serde(rename = "none")]
    NoMatch,
    /// A human pasted the video ID. It was previously indistinguishable from
    /// `NoMatch`, which left the review queue unable to tell an override from
    /// an abstention.
    Manual,
}

impl MatchMethod {
    /// The persisted form, identical to the `CHECK` constraint's literal.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Duration => "duration",
            Self::Fuzzy => "fuzzy",
            Self::Album => "album",
            Self::NoMatch => "none",
            Self::Manual => "manual",
        }
    }
}

impl FromStr for MatchMethod {
    type Err = String;

    fn from_str(raw: &str) -> Result<Self, Self::Err> {
        match raw {
            "duration" => Ok(Self::Duration),
            "fuzzy" => Ok(Self::Fuzzy),
            "album" => Ok(Self::Album),
            "none" => Ok(Self::NoMatch),
            "manual" => Ok(Self::Manual),
            other => Err(other.to_string()),
        }
    }
}

/// The per-track score components behind one `match_confidence` row.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfidenceBreakdown {
    pub overall: f64,
    pub title_similarity: Option<f64>,
    pub artist_similarity: Option<f64>,
    pub album_similarity: Option<f64>,
    /// Whole seconds, matching the Python dataclass: the delta is a difference
    /// of two integer durations, so a float here would imply a precision the
    /// inputs never had.
    pub duration_delta_sec: Option<i64>,
    pub version_similarity: Option<f64>,
    pub track_num_match: Option<bool>,
    pub album_coherence: Option<f64>,
    pub summary: Option<String>,
}

/// One track as Tidal reports it, after normalization.
///
/// There is deliberately no `from_dict` here. The Python dataclass has one
/// because Tidal's payload reaches it as an untyped dictionary and the
/// coercions — `int()` on an id, a string coerced to a one-element artist list —
/// have to happen exactly once, at the boundary. On the Rust side of the wire
/// nothing is untyped: `sidecar/tidal2ytm_sidecar/envelope.py` has already
/// turned every field into its declared type, so a second set of coercions here
/// would be a second place for the same rule to be wrong, reached by no input.
///
/// `Serialize` and `Deserialize` are on the struct rather than on a wire-shaped
/// double because the field names already *are* the wire keys
/// `contracts/favorites_list.json` records; a second shape would be a second
/// copy of those eleven names for a rename to miss.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SourceTrack {
    pub tidal_id: i64,
    pub title: String,
    pub artist: String,
    pub artists: Vec<String>,
    pub album: String,
    pub album_id: i64,
    pub album_year: Option<i64>,
    pub duration_sec: i64,
    pub track_num: i64,
    pub disc_num: i64,
    pub version: Option<String>,
}

/// One track's match, and everything the review queue needs to judge it.
///
/// `yt_video_id` is a bare 11-character id and never a URL, for the same reason
/// `SourceTrack` is addressed by `tidal_id`: a URL has three spellings per video
/// and every one of them would need normalizing before it could be compared.
#[derive(Debug, Clone, PartialEq)]
pub struct MatchResult {
    pub source: SourceTrack,
    pub yt_video_id: Option<String>,
    pub yt_title: Option<String>,
    pub yt_artist: Option<String>,
    pub yt_album: Option<String>,
    pub yt_album_track_num: Option<i64>,
    pub yt_duration_sec: Option<i64>,
    pub match_method: MatchMethod,
    pub confidence: ConfidenceBreakdown,
    pub status: TrackStatus,
    pub review_reason: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_status_round_trips_through_its_persisted_form() {
        let all = [
            TrackStatus::Pending,
            TrackStatus::Transferred,
            TrackStatus::Skip,
            TrackStatus::Failed,
            TrackStatus::NeedsReview,
        ];
        for status in all {
            assert_eq!(TrackStatus::from_str(status.as_str()), Ok(status));
        }
    }

    #[test]
    fn every_match_method_round_trips_through_its_persisted_form() {
        let all = [
            MatchMethod::Duration,
            MatchMethod::Fuzzy,
            MatchMethod::Album,
            MatchMethod::NoMatch,
            MatchMethod::Manual,
        ];
        for method in all {
            assert_eq!(MatchMethod::from_str(method.as_str()), Ok(method));
        }
    }

    #[test]
    fn no_match_persists_as_none() {
        assert_eq!(MatchMethod::NoMatch.as_str(), "none");
        assert_eq!(MatchMethod::from_str("none"), Ok(MatchMethod::NoMatch));
    }

    #[test]
    fn an_unknown_string_is_rejected_and_echoed_back() {
        assert_eq!(
            TrackStatus::from_str("archived"),
            Err("archived".to_string())
        );
        assert_eq!(
            MatchMethod::from_str("guesswork"),
            Err("guesswork".to_string())
        );
    }

    #[test]
    fn serde_writes_the_same_literal_as_str_for_every_variant() {
        // `as_str()` and serde's renames are two declarations of the schema's
        // `CHECK` list, and `tidal2ytm-store`'s constraint test exercises only
        // the first. Two mistakes have to be absent at once: without
        // `rename_all`, every variant emits its Rust name (`"Duration"`), and
        // with `snake_case` alone `NoMatch` emits `"no_match"`. Either one is a
        // literal the database would reject and every other gate here accepts.
        let statuses = [
            TrackStatus::Pending,
            TrackStatus::Transferred,
            TrackStatus::Skip,
            TrackStatus::Failed,
            TrackStatus::NeedsReview,
        ];
        for status in statuses {
            let json = serde_json::to_string(&status).unwrap();
            // `json` is the serialized form, quotes included, so the literal it
            // carries is the inner text; comparing it to the bare `as_str()`
            // would be comparing a quoted string to an unquoted one.
            assert_eq!(json, format!("\"{}\"", status.as_str()));
            assert_eq!(serde_json::from_str::<TrackStatus>(&json).unwrap(), status);
        }

        let methods = [
            MatchMethod::Duration,
            MatchMethod::Fuzzy,
            MatchMethod::Album,
            MatchMethod::NoMatch,
            MatchMethod::Manual,
        ];
        for method in methods {
            let json = serde_json::to_string(&method).unwrap();
            assert_eq!(json, format!("\"{}\"", method.as_str()));
            assert_eq!(serde_json::from_str::<MatchMethod>(&json).unwrap(), method);
        }
    }

    #[test]
    fn statuses_sort_in_workflow_order() {
        let mut all = [
            TrackStatus::NeedsReview,
            TrackStatus::Failed,
            TrackStatus::Pending,
            TrackStatus::Skip,
            TrackStatus::Transferred,
        ];
        all.sort();
        assert_eq!(
            all,
            [
                TrackStatus::Pending,
                TrackStatus::Transferred,
                TrackStatus::Skip,
                TrackStatus::Failed,
                TrackStatus::NeedsReview,
            ]
        );
    }
}
