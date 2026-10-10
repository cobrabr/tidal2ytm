//! Merge policy and the state machine: what a fresh match does to a stored
//! track, and which status it lands in.
//!
//! The re-targeted port of `tidal2ytm/planning_merge.py`'s decision half, plus
//! the tail of `tidal2ytm/matcher.py:match_track`. The TOML row dictionaries
//! `planning_merge` wrote are gone: a caller here gets a [`MergeAction`] and a
//! [`MatchDecision`], and `tidal2ytm-store`'s upserts do the writing. Slug dedup
//! left the project with the schema, so nothing here builds an `artist_slug` or
//! an `album_slug`.
//!
//! `to_lowercase` stands in for Python's `str.casefold` in the selection sort
//! key. The two agree on most names but diverge on case-folding special cases,
//! where full folding expands where a simple lowercase mapping does not: `ß`
//! (→ `ss`), the `ﬁ` ligature (→ `fi`), a final sigma (`ς` → `σ`), and several
//! hundred further code points concentrated in Cherokee, Greek Extended and
//! Adlam. That deviation is accepted here once rather than repeated per call.

use std::collections::BTreeMap;

use crate::model::{MatchMethod, MatchResult, SourceTrack, TrackStatus};

/// The overall confidence below which a match needs a human.
pub const CONFIDENCE_THRESHOLD: f64 = 0.70;

/// The album similarity below which a match is flagged as the wrong album.
///
/// A `(Deluxe Version)`-style suffix scores around `0.84`, an exact album
/// `1.0`, so `0.85` separates "a different pressing" from "a different album".
pub const WRONG_ALBUM_THRESHOLD: f64 = 0.85;

/// The album similarity below which the match is attributed to duration alone.
pub const DURATION_METHOD_CUTOFF: f64 = 0.3;

/// What a fresh match means for the row it would be written against.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MergeAction {
    /// The stored row is transferred. It must not be touched.
    SkipTransferred,
    /// There is no stored row, so this is a new track.
    AddNew,
    /// The stored and fresh video ids agree, so the match is silent.
    KeepSame,
    /// The two disagree or neither has an id; the caller must ask.
    Ask,
}

/// The stored row's facts that decide a merge, and nothing else.
///
/// Deliberately not a `TrackRow`: the store hands out rows of eighteen fields,
/// and a merge decision reads two of them, so depending on the wide type would
/// couple this policy to a schema it never looks at.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExistingMatch {
    pub status: TrackStatus,
    pub yt_video_id: Option<String>,
}

/// The outcome of deciding one fresh match: the merge action beside the status,
/// method and reason to write if the caller proceeds.
#[derive(Debug, Clone, PartialEq)]
pub struct MatchDecision {
    pub action: MergeAction,
    pub status: TrackStatus,
    pub match_method: MatchMethod,
    pub review_reason: Option<String>,
}

/// Decide what a fresh match means for a stored row.
///
/// The arm order is the behaviour. `SkipTransferred` is returned here **and**
/// `Store::write_status` refuses the same change; that is deliberate
/// belt-and-braces rather than redundancy. The enum arm stops a caller that
/// consulted this function from attempting the write at all, and the store's
/// guard holds for a caller that reaches the write from a path which never
/// consulted this one.
///
/// Both ids are flattened with an empty-string fallback, because the Python
/// flattened `None` and `""` with `or ""` and a stored row can carry either.
#[must_use]
pub fn classify_track(existing: Option<&ExistingMatch>, new_video_id: Option<&str>) -> MergeAction {
    let Some(existing) = existing else {
        return MergeAction::AddNew;
    };
    if existing.status == TrackStatus::Transferred {
        return MergeAction::SkipTransferred;
    }
    let stored = existing.yt_video_id.as_deref().unwrap_or("");
    let new = new_video_id.unwrap_or("");
    // Neither side has an id: an abstention, not an agreement. Checked before
    // equality, because two empty strings are equal and would otherwise read as
    // a silent "keep same".
    if stored.is_empty() && new.is_empty() {
        return MergeAction::Ask;
    }
    if stored == new {
        return MergeAction::KeepSame;
    }
    MergeAction::Ask
}

/// Decide the status, method and review reason of a fresh match.
///
/// The port of `matcher.match_track`'s tail. Only the `duration`/`fuzzy` pair is
/// ambiguous enough for a threshold to choose between them, so only that pair is
/// recomputed: `album`, `none` and `manual` are outcomes the thresholds cannot
/// re-derive and pass through as the matcher set them. `Manual` is set when the
/// caller supplies a review override and never by this function; the variant
/// exists because a hand-pasted override stored as `none` with confidence `0.0`
/// is indistinguishable from an abstention, which is the latent bug the program
/// design names.
///
/// The tests assert the reason's presence and the status, never its exact text:
/// the string is persisted in `review_reason` and shown in the review UI, so it
/// is copy, and copy is not a contract.
#[must_use]
pub fn decide_status(result: &MatchResult, existing: Option<&ExistingMatch>) -> MatchDecision {
    let album_similarity = result.confidence.album_similarity;
    let wrong_album = album_similarity.is_some_and(|similarity| similarity < WRONG_ALBUM_THRESHOLD);
    let match_method = match result.match_method {
        MatchMethod::Duration | MatchMethod::Fuzzy => {
            if album_similarity.is_some_and(|similarity| similarity < DURATION_METHOD_CUTOFF) {
                MatchMethod::Duration
            } else {
                MatchMethod::Fuzzy
            }
        }
        other => other,
    };

    let has_video = result.yt_video_id.is_some();
    let below_threshold = result.confidence.overall < CONFIDENCE_THRESHOLD;
    let status = if !has_video || below_threshold || wrong_album {
        TrackStatus::NeedsReview
    } else {
        TrackStatus::Pending
    };

    let review_reason = if status == TrackStatus::Pending {
        None
    } else if !has_video {
        Some("No candidates found".to_string())
    } else if wrong_album {
        // Two-decimal formatting is carried over from the Python, which wrote
        // it with `f"{value:.2f}"`.
        Some(format!(
            "Wrong album match ({:.2})",
            result.confidence.overall
        ))
    } else {
        Some(format!("Low confidence ({:.2})", result.confidence.overall))
    };

    MatchDecision {
        action: classify_track(existing, result.yt_video_id.as_deref()),
        status,
        match_method,
        review_reason,
    }
}

/// The positions of a selection's tracks in the order the planning screen shows
/// them: artist A–Z, then album year, then album name, then disc, then track.
///
/// The positions index the selection's values in key order, which is the order a
/// `BTreeMap` yields; the caller holds the same sequence.
#[must_use]
pub fn ordered_selection(selection: &BTreeMap<i64, SourceTrack>) -> Vec<usize> {
    let tracks: Vec<&SourceTrack> = selection.values().collect();
    // Keys are precomputed so the comparator never builds a lowercased string
    // per comparison, and the trailing index makes the sort deterministic on a
    // full tie, which is also what Python's stable `sorted` does.
    let mut keyed: Vec<(SortKey, usize)> = tracks
        .iter()
        .enumerate()
        .map(|(index, track)| (sort_key(track), index))
        .collect();
    keyed.sort();
    keyed.into_iter().map(|(_, index)| index).collect()
}

/// The ordering tuple: artist lowercased, album year, album lowercased, disc,
/// track.
type SortKey = (String, i64, String, i64, i64);

/// An unknown year sorts as `9999`, which puts an album with no known year after
/// every album that has one. Python's `or 9999` treats a year of `0` as absent
/// too, so a zero year is filtered out here rather than kept.
fn sort_key(track: &SourceTrack) -> SortKey {
    (
        track.artist.to_lowercase(),
        track.album_year.filter(|year| *year != 0).unwrap_or(9999),
        track.album.to_lowercase(),
        track.disc_num,
        track.track_num,
    )
}
