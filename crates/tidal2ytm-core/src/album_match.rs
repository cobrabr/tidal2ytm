//! Ranking candidate albums for a group of source tracks, and mapping the
//! winner's tracklist back onto them.
//!
//! The port of the pure half of `tidal2ytm/album_matching.py`. The split is
//! recorded in `tools/generate_fixtures.py`'s module docstring; a function
//! belongs here when it takes no YTM client and performs no I/O. This module
//! owns [`group_by_album_id`], [`version_term`], [`album_meta_score`],
//! [`sibling_year`], [`sibling_entry`], [`dedupe_details`], [`best_track_fit`],
//! [`rank_candidates`], [`album_abstains`] and [`map_winner_tracks`].
//!
//! The impure half stays Python until Phase 4 has a YTM client:
//! `_review_results`, `_try_fetch_album`, `_harvest_sibling_candidates`,
//! `_fetch_album_details`, and the search half of `resolve_album_group` all
//! reach a client or a clock. `ALBUM_SEARCH_LIMIT`, `ALBUM_FETCH_LIMIT` and
//! `ALBUM_VERSION_FETCH_LIMIT` stay Python-side with them because they cap
//! network calls, not scoring.

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashSet};

use crate::candidate::{AlbumDetail, AlbumSummary, Candidate};
use crate::model::{ConfidenceBreakdown, MatchMethod, MatchResult, SourceTrack, TrackStatus};
use crate::text::similarity;
use crate::track_scoring::{TrackFit, duration_ok, score_track_fit};
use crate::version_tokens::split_base_qualifiers;

/// How close two albums' scores may be before the winner is a guess.
pub const ALBUM_MARGIN_EPS: f64 = 0.05;

/// A score above which the winner is certain enough to ignore a close second.
///
/// Near-certain winners ignore a close second: at this score both the album
/// metadata and the track fits are excellent, so hesitation only loses matches.
pub const ALBUM_CERTAIN_THRESHOLD: f64 = 0.95;

/// The indices of the tracks that share each album id.
///
/// A `BTreeMap` rather than a `HashMap` for the same reason [`search`] returns
/// indices: the shape has to serialize into a fixture row, and a map with a
/// defined order is the one a corpus can compare. The order within each group
/// is declaration order, because a `Vec` push keeps it.
///
/// [`search`]: crate::search
#[must_use]
pub fn group_by_album_id(tracks: &[SourceTrack]) -> BTreeMap<i64, Vec<usize>> {
    let mut groups: BTreeMap<i64, Vec<usize>> = BTreeMap::new();
    for (index, track) in tracks.iter().enumerate() {
        groups.entry(track.album_id).or_default().push(index);
    }
    groups
}

/// How alike two albums' version markers are, compared as word sets.
///
/// Both sides unmarked is agreement, a marker on only one side is a mismatch,
/// equal word sets are agreement, and one set containing the other is partial
/// agreement. The word sets are what let `Deluxe Version` match `Deluxe` while
/// `Remix` against `Live` scores near zero.
#[must_use]
pub fn version_term(source_quals: &[String], cand_quals: &[String]) -> f64 {
    if source_quals.is_empty() && cand_quals.is_empty() {
        return 1.0;
    }
    if source_quals.is_empty() || cand_quals.is_empty() {
        return 0.0;
    }
    let source_words: BTreeSet<&str> = source_quals
        .iter()
        .flat_map(|qualifier| qualifier.split_whitespace())
        .collect();
    let cand_words: BTreeSet<&str> = cand_quals
        .iter()
        .flat_map(|qualifier| qualifier.split_whitespace())
        .collect();
    if source_words == cand_words {
        return 1.0;
    }
    if source_words.is_subset(&cand_words) || cand_words.is_subset(&source_words) {
        return 0.8;
    }
    let overlap = source_words.intersection(&cand_words).count() as f64
        / source_words.union(&cand_words).count() as f64;
    // `0.6 * overlap`, then `0.2 +`, in that order and not reassociated: Python
    // divides into `overlap` first and multiplies that, and the corpus compares
    // these values exactly, so a different grouping changes the last bit.
    0.2 + 0.6 * overlap
}

/// Score a candidate album's metadata against one source track.
///
/// The four terms are a similarity of the base titles, a similarity of the
/// artists, a year closeness, and the version-marker term.
#[must_use]
pub fn album_meta_score(source: &SourceTrack, candidate: &AlbumSummary) -> f64 {
    let (source_base, source_quals) = split_base_qualifiers(&source.album);
    let (candidate_base, candidate_quals) = split_base_qualifiers(&candidate.title);
    let title_sim = similarity(&source_base, &candidate_base);
    let version = version_term(&source_quals, &candidate_quals);
    // An empty artist is no artist, so there is nothing to compare and the score
    // is zero rather than the similarity of a real name against nothing.
    let artist_sim = if candidate.artist.is_empty() {
        0.0
    } else {
        similarity(&source.artist, &candidate.artist)
    };
    // `year_score` starts at the neutral `0.5` and moves only when both years
    // are known. A source with no album year keeps `0.5` even when the
    // candidate's year is known — and so does the reverse — because a delta
    // needs both sides. Making this symmetric is the easy "fix" that is a bug,
    // and the corpus points a row at each direction.
    let year_score = match (candidate.year, source.album_year) {
        (Some(candidate_year), Some(source_year)) => {
            1.0 / (1.0 + (candidate_year - source_year).abs() as f64)
        }
        _ => 0.5,
    };
    // Left to right, in this order. Not cosmetic: the corpus compares exactly,
    // and a different grouping of these same four products gives a different
    // last bit on values that are mathematically identical.
    title_sim * 0.45 + artist_sim * 0.25 + year_score * 0.1 + version * 0.2
}

/// The sibling's year when it carries one, otherwise the parent's.
#[must_use]
pub fn sibling_year(sib: Option<i64>, parent: Option<i64>) -> Option<i64> {
    sib.or(parent)
}

/// A search-hit-shaped summary for a sibling of `parent`.
///
/// A sibling ranks through the same scorer as a search hit, so it needs the
/// same shape; the fallbacks are why this function exists in Python and why it
/// has to keep existing after the cut. The title, artist and year fall back to
/// the parent's when the sibling's is absent, matching Python's
/// `_sibling_entry`. The id never falls back: `album_id` identifies the album,
/// so taking the parent's would make the sibling claim to be its parent. Python
/// takes the sibling's own id unconditionally.
#[must_use]
pub fn sibling_entry(parent: &AlbumSummary, sib: &AlbumSummary) -> AlbumSummary {
    AlbumSummary {
        album_id: sib.album_id.clone(),
        title: if sib.title.is_empty() {
            parent.title.clone()
        } else {
            sib.title.clone()
        },
        artist: if sib.artist.is_empty() {
            parent.artist.clone()
        } else {
            sib.artist.clone()
        },
        year: sibling_year(sib.year, parent.year),
    }
}

/// The indices of `details` to keep, collapsing pool entries for the same album.
///
/// Other-versions shelves routinely list the album itself, or a sibling lists an
/// album already pooled under another id; without this, identical twins tie at
/// the top and trip the close-call abstention. The first entry wins, because
/// search hits precede harvested siblings.
#[must_use]
pub fn dedupe_details(details: &[(AlbumSummary, AlbumDetail)]) -> Vec<usize> {
    let mut seen: HashSet<(String, Vec<String>)> = HashSet::new();
    let mut kept = Vec::new();
    for (index, (candidate, detail)) in details.iter().enumerate() {
        // `to_lowercase` stands in for Python's `casefold`. `std` has no
        // casefold and no crate is added for the three characters where the two
        // differ, none of which the ASCII-only corpus exercises.
        let title_key = detail
            .summary
            .title
            .to_lowercase()
            .replace('&', " and ")
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ");
        // Deviation 3: Python's `_candidate_artist_names` reads a candidate's
        // whole `artists` list; the envelope collapses that list to the one
        // `artist` string, so multi-artist albums deduplicate more aggressively
        // here than in Python, and the corpus emits single-artist albums only.
        let artists = if candidate.artist.is_empty() {
            Vec::new()
        } else {
            vec![candidate.artist.clone()]
        };
        if seen.insert((title_key, artists)) {
            kept.push(index);
        }
    }
    kept
}

/// The best-scoring row of an album's tracklist, and its index.
///
/// Rows whose duration fails [`duration_ok`] are skipped, and the first strict
/// maximum is kept (`>` rather than `>=`), so a tie resolves to the earlier row
/// exactly as Python does.
#[must_use]
pub fn best_track_fit(
    source: &SourceTrack,
    tracks: &[Candidate],
    album_title: &str,
) -> Option<(TrackFit, usize)> {
    let mut best: Option<(TrackFit, usize)> = None;
    for (index, candidate) in tracks.iter().enumerate() {
        if !duration_ok(source.duration_sec, candidate.duration_sec) {
            continue;
        }
        // The album title passed in wins over whatever the row itself carries:
        // an album tracklist often has no per-track album, and the caller knows
        // which album this tracklist belongs to.
        let scored = Candidate {
            album: Some(album_title.to_string()),
            ..candidate.clone()
        };
        let fit = score_track_fit(source, &scored);
        if best
            .as_ref()
            .is_none_or(|(current, _)| fit.overall > current.overall)
        {
            best = Some((fit, index));
        }
    }
    best
}

/// Score every candidate album, best first.
///
/// A candidate's score is `meta * 0.4 + fit_mean * 0.6`, where `fit_mean` is the
/// mean of the best track fit for each source, or `0.0` for a source with no
/// fitting row. The result pairs each score with the index of the candidate
/// that produced it.
#[must_use]
pub fn rank_candidates(
    first: &SourceTrack,
    sources: &[SourceTrack],
    details: &[(AlbumSummary, AlbumDetail)],
) -> Vec<(f64, usize)> {
    let mut scored: Vec<(f64, usize)> = Vec::with_capacity(details.len());
    for (index, (candidate, detail)) in details.iter().enumerate() {
        let meta = album_meta_score(first, candidate);
        let mut total = 0.0_f64;
        for source in sources {
            total += best_track_fit(source, &detail.tracks, &detail.summary.title)
                .map_or(0.0, |(fit, _)| fit.overall);
        }
        let fit_mean = if sources.is_empty() {
            0.0
        } else {
            total / sources.len() as f64
        };
        scored.push((meta * 0.4 + fit_mean * 0.6, index));
    }
    // Descending and *stable*: `sort_unstable_by` would reorder equal scores and
    // could select a different winning album. Python's stable
    // `sort(key=…, reverse=True)` preserves the input order among equal keys, and
    // so does a stable `sort_by` with a descending comparator.
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(Ordering::Equal));
    scored
}

/// Whether a ranked candidate list is too close to call.
///
/// Empty is always an abstention; a lone candidate is never one. Otherwise the
/// winner is abstained on when it sits below [`ALBUM_CERTAIN_THRESHOLD`] and the
/// runner-up is within [`ALBUM_MARGIN_EPS`] of it. A near-certain winner ignores
/// a close second, because at that score the album metadata and the track fits
/// are both excellent and hesitation only loses matches.
#[must_use]
pub fn album_abstains(scored: &[(f64, usize)]) -> bool {
    let Some((top, _)) = scored.first() else {
        return true;
    };
    *top < ALBUM_CERTAIN_THRESHOLD && scored.len() > 1 && (*top - scored[1].0) < ALBUM_MARGIN_EPS
}

/// Map each source track onto the best row of a winning album's tracklist.
///
/// A source with no fitting row gets a [`MatchResult`] that carries no video and
/// asks a human; a fitting one gets the row's video, title, first artist and
/// album fields with [`MatchMethod::Album`].
#[must_use]
pub fn map_winner_tracks(sources: &[SourceTrack], winner: &AlbumDetail) -> Vec<MatchResult> {
    let mut results = Vec::with_capacity(sources.len());
    for source in sources {
        match best_track_fit(source, &winner.tracks, &winner.summary.title) {
            Some((fit, index)) => {
                let row = &winner.tracks[index];
                results.push(MatchResult {
                    source: source.clone(),
                    yt_video_id: Some(row.video_id.clone()),
                    yt_title: Some(row.title.clone()),
                    yt_artist: row.artists.first().cloned(),
                    yt_album: Some(winner.summary.title.clone()),
                    yt_album_track_num: row.album_track_num,
                    yt_duration_sec: row.duration_sec,
                    match_method: MatchMethod::Album,
                    confidence: ConfidenceBreakdown {
                        overall: fit.overall,
                        title_similarity: Some(fit.title_similarity),
                        artist_similarity: Some(fit.artist_similarity),
                        album_similarity: Some(fit.album_similarity),
                        duration_delta_sec: fit.duration_delta_sec,
                        version_similarity: Some(fit.version_similarity),
                        track_num_match: Some(fit.track_num_match),
                        album_coherence: Some(1.0),
                        // User-facing copy, built with the same `:.2f` shape as
                        // Python. The parity test never asserts this text.
                        summary: Some(format!(
                            "title={:.2}, artist={:.2}, album={:.2}",
                            fit.title_similarity, fit.artist_similarity, fit.album_similarity
                        )),
                    },
                    status: TrackStatus::Pending,
                    review_reason: None,
                });
            }
            None => results.push(review_result(source)),
        }
    }
    results
}

/// The single-source case of Python's `_review_results`: no fit means the album
/// abstained for this track, so it carries no video and asks a human.
fn review_result(source: &SourceTrack) -> MatchResult {
    let reason = "No in-album fit".to_string();
    MatchResult {
        source: source.clone(),
        yt_video_id: None,
        yt_title: None,
        yt_artist: None,
        yt_album: None,
        yt_album_track_num: None,
        yt_duration_sec: None,
        match_method: MatchMethod::NoMatch,
        confidence: ConfidenceBreakdown {
            overall: 0.0,
            title_similarity: None,
            artist_similarity: None,
            album_similarity: None,
            duration_delta_sec: None,
            version_similarity: None,
            track_num_match: None,
            album_coherence: None,
            summary: Some(reason.clone()),
        },
        status: TrackStatus::NeedsReview,
        review_reason: Some(reason),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn source(album: &str, year: Option<i64>) -> SourceTrack {
        SourceTrack {
            tidal_id: 100_001,
            title: "Paper Tide".to_string(),
            artist: "Halcyon Bay".to_string(),
            artists: vec!["Halcyon Bay".to_string()],
            album: album.to_string(),
            album_id: 200_001,
            album_year: year,
            duration_sec: 200,
            track_num: 1,
            disc_num: 1,
            version: None,
        }
    }

    fn summary(title: &str) -> AlbumSummary {
        AlbumSummary {
            album_id: "MPREb_00000042".to_string(),
            title: title.to_string(),
            artist: "Halcyon Bay".to_string(),
            year: Some(2019),
        }
    }

    #[test]
    fn a_year_score_needs_both_sides_and_is_neutral_otherwise() {
        // The asymmetry is the trap. A candidate year against an unknown source
        // year must score the same as the reverse, because a delta needs both
        // sides; and both must sit below the delta-of-zero score, or one of them
        // has silently become `1.0`.
        let known_candidate_unknown_source = album_meta_score(
            &source("Northern Signal", None),
            &summary("Northern Signal"),
        );
        let both_known_same_year = album_meta_score(
            &source("Northern Signal", Some(2019)),
            &summary("Northern Signal"),
        );
        let no_candidate_year = AlbumSummary {
            year: None,
            ..summary("Northern Signal")
        };
        let known_source_unknown_candidate =
            album_meta_score(&source("Northern Signal", Some(2019)), &no_candidate_year);
        assert_eq!(
            known_candidate_unknown_source,
            known_source_unknown_candidate
        );
        assert!(known_candidate_unknown_source < both_known_same_year);
    }

    #[test]
    fn the_first_strict_maximum_wins_a_tie() {
        // Two rows that score identically must resolve to the earlier one, which
        // `>` rather than `>=` is what guarantees; `sort_unstable_by` elsewhere
        // is the sibling hazard.
        let candidate = Candidate {
            video_id: "9aB3xQ1lZ7c".to_string(),
            title: "Paper Tide".to_string(),
            artists: vec!["Halcyon Bay".to_string()],
            album: None,
            album_track_num: Some(1),
            duration_sec: Some(200),
        };
        let second = Candidate {
            video_id: "Zq4Wm8n2RtY".to_string(),
            ..candidate.clone()
        };
        let found = best_track_fit(
            &source("Northern Signal", Some(2019)),
            &[candidate, second],
            "Northern Signal",
        );
        assert_eq!(found.map(|(_, index)| index), Some(0));
    }

    #[test]
    fn an_empty_ranking_abstains_and_a_lone_candidate_does_not() {
        assert!(album_abstains(&[]));
        assert!(!album_abstains(&[(0.1, 0)]));
    }

    #[test]
    fn a_certain_winner_ignores_a_close_second_below_the_margin() {
        // Exactly at the certain threshold is already safe; just below it with a
        // second inside the margin is not.
        assert!(!album_abstains(&[(ALBUM_CERTAIN_THRESHOLD, 0), (0.9, 1)]));
        assert!(album_abstains(&[(0.94, 0), (0.91, 1)]));
    }

    #[test]
    fn the_margin_test_is_strict_at_exactly_one_epsilon() {
        // Python's condition is `top - second < ALBUM_MARGIN_EPS`, so a gap of
        // exactly one epsilon must not abstain and a gap just under it must.
        // The top stays below `ALBUM_CERTAIN_THRESHOLD` so the margin alone
        // decides; `0.05 - 0.0` is exactly `ALBUM_MARGIN_EPS`, while
        // `0.5 - 0.45` lands just below it.
        assert!(!album_abstains(&[(ALBUM_MARGIN_EPS, 0), (0.0, 1)]));
        assert!(album_abstains(&[(0.5, 0), (0.45, 1)]));
    }
}
