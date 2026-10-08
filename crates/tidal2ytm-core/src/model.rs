//! The vocabulary every persisted row is written in.
//!
//! `TrackStatus` and `MatchMethod` are the Rust side of the schema's `CHECK`
//! constraints: each variant's `as_str()` returns exactly one of the literals
//! the DDL permits, so a value that reaches the database is always one the
//! constraint would have accepted. `ConfidenceBreakdown` is the port of
//! `tidal2ytm.models.ConfidenceBreakdown`, the score components the matcher
//! computes and `match_confidence` stores.

use std::str::FromStr;

/// Where a track sits in the transfer workflow.
///
/// Every literal here appears in the `track.status` `CHECK` constraint in
/// `tidal2ytm-store`'s schema, so a status can never be written that the
/// database would reject.
///
/// `Ord` exists because `status_counts` returns a `BTreeMap` keyed by this
/// type: the counts have to come back in a defined order, and the declaration
/// order is the workflow's own order, earliest first.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
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
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum MatchMethod {
    Duration,
    Fuzzy,
    Album,
    /// The variant is named for what it means rather than for its wire value:
    /// a `None` variant would shadow `Option::None` in any `match` that also
    /// handles an absent match. It serializes as `"none"`, and so does its
    /// `FromStr`.
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
