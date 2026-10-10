//! How well one candidate explains one source track.
//!
//! The port of `tidal2ytm/track_scoring.py`. Four similarities are weighted
//! into one number, a track-number agreement adds a bonus, and a duration
//! tolerance decides whether the pair is worth scoring at all. Nothing here
//! reaches a client or a database: a caller that has a `Candidate` in hand calls
//! [`duration_ok`] to drop the hopeless ones and [`score_track_fit`] to rank the
//! rest.

use crate::candidate::Candidate;
use crate::model::SourceTrack;
use crate::text::similarity;
use crate::version_tokens::{qualifier_similarity, split_base_qualifiers};

/// Whole seconds two durations may differ by before a candidate is dropped.
///
/// The floor of the tolerance: a short track has no room for a percentage,
/// because two recordings of a 30-second jingle differing by 1 second are
/// genuinely different recordings.
pub const DURATION_ABS_TOL_SEC: i64 = 4;

/// The share of the source duration two durations may differ by.
pub const DURATION_REL_TOL: f64 = 0.02;

/// The four weighted similarities, the total they make, and the two facts the
/// review queue needs alongside them.
#[derive(Debug, Clone, PartialEq)]
pub struct TrackFit {
    pub overall: f64,
    pub title_similarity: f64,
    pub artist_similarity: f64,
    pub album_similarity: f64,
    pub version_similarity: f64,
    pub track_num_match: bool,
    pub duration_delta_sec: Option<i64>,
}

/// Whether a candidate's duration is close enough to the source's to be scored.
///
/// A source of zero duration is one Tidal reported nothing for, so there is
/// nothing to disagree with and the candidate is kept; an absent candidate
/// duration is the candidate declining to say, which is not agreement. The
/// tolerance is the wider of the two arms, so a long track is judged
/// proportionally and a short one absolutely.
#[must_use]
pub fn duration_ok(source_sec: i64, candidate_sec: Option<i64>) -> bool {
    if source_sec <= 0 {
        return true;
    }
    let Some(candidate_sec) = candidate_sec else {
        return false;
    };
    let delta = (candidate_sec - source_sec).abs();
    // `(source_sec as f64 * DURATION_REL_TOL) as i64` truncates toward zero,
    // which is what Python's `int()` does to the same product. Rounding here
    // instead would widen the window by a second on some durations and narrow it
    // on others.
    delta <= DURATION_ABS_TOL_SEC.max((source_sec as f64 * DURATION_REL_TOL) as i64)
}

/// The best similarity between any source artist and any candidate artist.
///
/// A maximum over the whole cross product, not a positional comparison: a
/// track's credits are a set, and Tidal and YouTube Music disagree about the
/// order often enough that comparing positionally would score correct pairs as
/// wrong.
fn artist_similarity(source: &SourceTrack, candidate: &Candidate) -> f64 {
    let source_artists: Vec<&str> = if source.artists.is_empty() {
        vec![source.artist.as_str()]
    } else {
        source.artists.iter().map(String::as_str).collect()
    };
    if source_artists.is_empty() || candidate.artists.is_empty() {
        return 0.0;
    }
    source_artists
        .iter()
        .flat_map(|from| candidate.artists.iter().map(move |to| similarity(from, to)))
        .fold(0.0_f64, f64::max)
}

/// Score one candidate against one source track.
#[must_use]
pub fn score_track_fit(source: &SourceTrack, candidate: &Candidate) -> TrackFit {
    let (source_base, mut source_quals) = split_base_qualifiers(&source.title);
    // A version Tidal reports separately is a qualifier the title does not
    // spell out, so it joins the ones it does.
    if let Some(version) = &source.version {
        source_quals.push(version.to_lowercase());
    }
    let (candidate_base, candidate_quals) = split_base_qualifiers(&candidate.title);

    let title_similarity = similarity(&source_base, &candidate_base);
    let version_similarity = qualifier_similarity(&source_quals, &candidate_quals);
    let artist_similarity = artist_similarity(source, candidate);
    // An absent album and an empty one are the same absence: the Python guard
    // was a truthiness test on a string the caller had already defaulted to
    // `""`, and scoring against an empty string would measure the source album
    // against nothing rather than recording that there is nothing to compare.
    let album_similarity = match &candidate.album {
        Some(album) if !album.is_empty() => similarity(&source.album, album),
        _ => 0.0,
    };

    let duration_delta_sec = candidate
        .duration_sec
        .map(|candidate_sec| (candidate_sec - source.duration_sec).abs());
    let track_num_match =
        source.track_num > 0 && candidate.album_track_num == Some(source.track_num);

    // Left to right, in this order: `0.45`, `0.2`, `0.2`, `0.15`. Not
    // cosmetic. The corpus compares `overall` exactly, and a different grouping
    // of these same four products — a loop over pairs, a reassociated sum, an
    // accumulated total — gives a different last bit on values that are
    // mathematically identical, which is a parity failure and nothing else.
    let mut overall = title_similarity * 0.45
        + artist_similarity * 0.2
        + album_similarity * 0.2
        + version_similarity * 0.15;
    if track_num_match {
        overall = (overall + 0.08).min(1.0);
    }

    TrackFit {
        overall,
        title_similarity,
        artist_similarity,
        album_similarity,
        version_similarity,
        track_num_match,
        duration_delta_sec,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn source() -> SourceTrack {
        SourceTrack {
            tidal_id: 100_001,
            title: "Aurora Drift".to_string(),
            artist: "Halcyon Bay".to_string(),
            artists: vec!["Halcyon Bay".to_string()],
            album: "Northern Signal".to_string(),
            album_id: 200_001,
            album_year: Some(2019),
            duration_sec: 200,
            track_num: 3,
            disc_num: 1,
            version: None,
        }
    }

    fn candidate(album: &str, album_track_num: Option<i64>) -> Candidate {
        Candidate {
            video_id: "9aB3xQ1lZ7c".to_string(),
            title: "Aurora Drift".to_string(),
            artists: vec!["Halcyon Bay".to_string()],
            album: Some(album.to_string()),
            album_track_num,
            duration_sec: Some(200),
        }
    }

    #[test]
    fn the_bonus_is_withheld_unless_the_track_numbers_agree() {
        // Four shapes of the same pair, differing only in the candidate's track
        // number. Three of them must take no bonus, and the one that does must
        // be the only one whose total moves.
        let agreed = score_track_fit(&source(), &candidate("Southern Static", Some(3)));
        for (label, candidate) in [
            ("one apart", candidate("Southern Static", Some(2))),
            ("source number zero", candidate("Southern Static", Some(0))),
            ("absent", candidate("Southern Static", None)),
        ] {
            let fit = score_track_fit(&source(), &candidate);
            assert!(!fit.track_num_match, "{label} must not match");
            assert!(
                fit.overall < agreed.overall,
                "{label} must score below the pair that agrees"
            );
        }
        assert!(agreed.track_num_match);
    }

    #[test]
    fn the_bonus_never_lifts_the_total_past_one() {
        // The cap, over a spread of base totals: a candidate whose unaided score
        // is already 1.0, one that sits just under the boundary, and one well
        // below it. Each is scored twice so the matching case — the only one that
        // takes the bonus — is compared against the same candidate without it.
        for album in ["Northern Signal", "Northern Signal Echo", "Southern Static"] {
            let with_bonus = score_track_fit(&source(), &candidate(album, Some(3)));
            let without = score_track_fit(&source(), &candidate(album, Some(2)));
            assert!(with_bonus.overall <= 1.0, "{album}: {with_bonus:?}");
            assert!(without.overall <= 1.0, "{album}: {without:?}");
            assert!(with_bonus.overall >= without.overall, "{album}");
        }
    }
}
