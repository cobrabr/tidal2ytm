//! Pure library search: filter a liked-tracks list by a query string.
//!
//! The port of `tidal2ytm/planning_search.py`. Nothing here logs in or touches
//! the network: every function filters an in-memory list, which is what lets the
//! planning screen answer "which of my liked songs is this" without a round
//! trip.

use crate::model::SourceTrack;
use crate::text::{normalize, similarity};

/// The score below which a fuzzy result is dropped.
///
/// `0.6` is carried over from the Python unchanged and has no recorded
/// justification, so nothing here says why it is `0.6` rather than `0.5` or
/// `0.7`. Moving it moves which tracks the fallback returns, and the only thing
/// that would explain the move is a note that does not exist yet.
pub const FUZZY_CUTOFF: f64 = 0.6;

/// The searchable fields of one track, each paired with the name a score trace
/// prints beside it.
///
/// The order and the names are a contract: `title`, then `artist`, then one
/// `artistN` per credit — the index is that credit's position in `artists` —
/// then `album`. An empty field is omitted rather than scored as an empty
/// string, because an empty haystack can only produce a zero the caller would
/// have to ignore anyway.
///
/// The names are owned rather than `&'static str`. The three fixed ones could be
/// static, but `artistN` is built from the credit's index, and a `&'static str`
/// for it would have to be leaked so it outlived the call — and
/// [`search_library`] builds this list once per track per query, so a leak there
/// grows with the library and with every keystroke.
#[must_use]
pub fn named_haystacks(track: &SourceTrack) -> Vec<(String, &str)> {
    let mut fields: Vec<(String, &str)> = Vec::new();
    if !track.title.is_empty() {
        fields.push(("title".to_string(), track.title.as_str()));
    }
    if !track.artist.is_empty() {
        fields.push(("artist".to_string(), track.artist.as_str()));
    }
    for (index, artist) in track.artists.iter().enumerate() {
        if !artist.is_empty() {
            fields.push((format!("artist{index}"), artist.as_str()));
        }
    }
    if !track.album.is_empty() {
        fields.push(("album".to_string(), track.album.as_str()));
    }
    fields
}

/// The indices of the tracks matching `query`, and whether the match was direct.
///
/// `direct` is `true` when at least one track *contained* the query, and `false`
/// when only the fuzzy fallback found anything, so a caller can distinguish
/// "these contain what you typed" from "nothing did, here are the closest".
///
/// The result is indices into `tracks` rather than the tracks themselves. A
/// fixture row is `{input, expected}` and has to serialize, and an index is a
/// number where a `SourceTrack` would have to be repeated in every row that
/// mentioned one; an index into the slice the caller passed also carries the
/// identity the Python's returned tracks did.
#[must_use]
pub fn search_library(tracks: &[SourceTrack], query: &str) -> (Vec<usize>, bool) {
    let normalized_query = normalize(query);
    // A query with nothing left after normalization cannot select anything, so
    // the whole library is the answer: an empty query means "show everything",
    // not "find nothing".
    if normalized_query.is_empty() {
        return ((0..tracks.len()).collect(), true);
    }

    let mut scored: Vec<(f64, usize)> = Vec::with_capacity(tracks.len());
    let mut contained: Vec<usize> = Vec::new();
    for (index, track) in tracks.iter().enumerate() {
        let fields = named_haystacks(track);
        // Containment is a substring test on the *normalized* strings, and it is
        // independent of the similarity algorithm: a title can contain the query
        // while scoring below one when the query is shorter than the title.
        if fields
            .iter()
            .any(|(_, haystack)| normalize(haystack).contains(&normalized_query))
        {
            contained.push(index);
        }
        // The best score any single field reaches. A track with no fields is
        // unscoreable, and the starting zero is that, not a comparison that
        // failed to happen.
        let best = fields
            .iter()
            .map(|(_, haystack)| similarity(haystack, query))
            .fold(0.0_f64, f64::max);
        scored.push((best, index));
    }

    if !contained.is_empty() {
        // Exact hits — a field whose normalized form equals the query — rank
        // above the tracks that merely contain it. Both passes walk `scored` in
        // order, so tracks keep the order the caller declared them in.
        let mut ordered: Vec<usize> = Vec::new();
        for (score, index) in &scored {
            if contained.contains(index) && *score == 1.0 {
                ordered.push(*index);
            }
        }
        for (score, index) in &scored {
            if contained.contains(index) && *score != 1.0 {
                ordered.push(*index);
            }
        }
        (ordered, true)
    } else {
        // The fallback is the only branch that depends on `similarity`: it keeps
        // scores at or above `FUZZY_CUTOFF` and strictly below one, best first.
        // `sort_by` is stable, so a tie keeps declaration order, exactly as the
        // Python's `sorted(reverse=True)` does.
        let mut fuzzy: Vec<(f64, usize)> = scored
            .into_iter()
            .filter(|(score, _)| *score >= FUZZY_CUTOFF && *score < 1.0)
            .collect();
        fuzzy.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        (fuzzy.into_iter().map(|(_, index)| index).collect(), false)
    }
}

/// Whether `haystack[at..]` starts with `needle`, ignoring ASCII case.
fn starts_with_ascii_case_insensitive(haystack: &[u8], at: usize, needle: &[u8]) -> bool {
    haystack.len() >= at + needle.len()
        && haystack[at..at + needle.len()].eq_ignore_ascii_case(needle)
}

/// The kind and numeric id of the first Tidal link in `raw`.
///
/// The pattern is `tidal\.com/(?:browse/)?(track|album)/(\d+)`, case-insensitive
/// and unanchored, so a link embedded in a sentence is found and the first
/// well-formed one wins. It is scanned by hand rather than with a regex engine:
/// the pattern is fixed, the whole crate has no regex dependency, and each byte
/// compared here is ASCII, so a match can never begin inside a multi-byte
/// character.
#[must_use]
pub fn parse_tidal_link(raw: &str) -> Option<(&'static str, i64)> {
    let bytes = raw.as_bytes();
    let host = b"tidal.com/";
    let mut start = 0;
    while start < bytes.len() {
        if !starts_with_ascii_case_insensitive(bytes, start, host) {
            start += 1;
            continue;
        }
        let mut at = start + host.len();
        if starts_with_ascii_case_insensitive(bytes, at, b"browse/") {
            at += b"browse/".len();
        }
        let kind = if starts_with_ascii_case_insensitive(bytes, at, b"track") {
            at += b"track".len();
            "track"
        } else if starts_with_ascii_case_insensitive(bytes, at, b"album") {
            at += b"album".len();
            "album"
        } else {
            // This `tidal.com/` is not the start of a link; the pattern could
            // still begin one byte later.
            start += 1;
            continue;
        };
        if bytes.get(at) != Some(&b'/') {
            start += 1;
            continue;
        }
        at += 1;
        let digits_start = at;
        while bytes.get(at).is_some_and(u8::is_ascii_digit) {
            at += 1;
        }
        if at == digits_start {
            start += 1;
            continue;
        }
        let id = std::str::from_utf8(&bytes[digits_start..at])
            .ok()?
            .parse()
            .ok()?;
        return Some((kind, id));
    }
    None
}

/// The indices of the tracks whose album is exactly `album_id`.
///
/// The canonical album rule: the liked songs of that exact album, never the
/// album entity. An album link resolves to the library's tracks that carry that
/// id, in declaration order, so a liked song the user removed cannot come back
/// through the album.
#[must_use]
pub fn resolve_album_link(tracks: &[SourceTrack], album_id: i64) -> Vec<usize> {
    tracks
        .iter()
        .enumerate()
        .filter(|(_, track)| track.album_id == album_id)
        .map(|(index, _)| index)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn track(title: &str) -> SourceTrack {
        SourceTrack {
            tidal_id: 100_001,
            title: title.to_string(),
            artist: String::new(),
            artists: Vec::new(),
            album: String::new(),
            album_id: 200_001,
            album_year: None,
            duration_sec: 200,
            track_num: 1,
            disc_num: 1,
            version: None,
        }
    }

    #[test]
    fn a_score_at_the_cutoff_is_kept_and_one_below_it_is_dropped() {
        // `similarity("abcde", "abcxy")` is `2 * 3 / 10`, exactly `0.6`, and
        // neither string contains the other, so the track reaches the fallback
        // on the inclusive side of the cutoff. The second pair scores just under
        // it and must be dropped, which is what makes the first pair a boundary
        // rather than a value that merely happens to pass.
        let (at_cutoff, direct) = search_library(&[track("abcde")], "abcxy");
        assert_eq!(at_cutoff, vec![0]);
        assert!(!direct);

        // `similarity("abcde", "abzzz")` is `2 * 2 / 10`, exactly `0.4`.
        let (below, direct) = search_library(&[track("abcde")], "abzzz");
        assert!(below.is_empty());
        assert!(!direct);
    }

    #[test]
    fn a_track_that_scores_one_is_a_direct_hit_not_a_fuzzy_one() {
        // A score of exactly `1.0` means a normalized field equals the query,
        // and an equal non-empty string is a substring of itself, so such a
        // track is always contained and never reaches the fallback's `< 1.0`
        // arm. This pins the observable consequence: an exact match comes back
        // `direct`, and the fuzzy branch excludes the `1.0` scores it can never
        // see.
        let (exact, direct) = search_library(&[track("aurora drift")], "Aurora Drift");
        assert_eq!(exact, vec![0]);
        assert!(direct);
    }
}
