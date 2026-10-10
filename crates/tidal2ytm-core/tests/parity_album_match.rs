//! Replay the frozen `album_match.json` corpus against the Rust port of the
//! pure half of `tidal2ytm/album_matching.py`.
//!
//! The corpus is generated from Python and frozen; CI never regenerates it, so
//! a rule change in either language shows up here as a diff rather than as a
//! test that cannot fail. The `map` section compares every field except the
//! `summary` copy string, which is user-facing and carries no contract.

use std::fs;

use serde_json::Value;
use tidal2ytm_core::album_match;
use tidal2ytm_core::candidate::{AlbumDetail, AlbumSummary, Candidate};
use tidal2ytm_core::model::{MatchResult, SourceTrack};

mod common;
use common::track_from_value;

/// Every op the generator emits, one per `album_match` section.
const OPS: &[&str] = &[
    "group",
    "version",
    "meta",
    "sibling_year",
    "sibling_entry",
    "dedupe",
    "fit",
    "rank",
    "abstain",
    "map",
];

#[derive(serde::Deserialize)]
struct FixtureCase {
    input: Value,
    expected: Value,
}

/// The fields a `map` case asserts, with the `summary` copy string excluded.
#[derive(serde::Deserialize)]
struct MapExpected {
    yt_video_id: Option<String>,
    yt_title: Option<String>,
    yt_artist: Option<String>,
    yt_album: Option<String>,
    yt_album_track_num: Option<i64>,
    yt_duration_sec: Option<i64>,
    match_method: String,
    status: String,
    review_reason_present: bool,
    overall: f64,
    title_similarity: Option<f64>,
    artist_similarity: Option<f64>,
    album_similarity: Option<f64>,
    duration_delta_sec: Option<i64>,
    version_similarity: Option<f64>,
    track_num_match: Option<bool>,
    album_coherence: Option<f64>,
}

fn cases() -> Vec<FixtureCase> {
    let path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/album_match.json");
    let raw = fs::read_to_string(&path).expect("tests/fixtures/album_match.json is committed data");
    serde_json::from_str::<Vec<FixtureCase>>(&raw)
        .expect("every fixture case has input and expected")
}

fn cases_for(op: &str) -> Vec<FixtureCase> {
    cases()
        .into_iter()
        .filter(|case| case.input["op"].as_str() == Some(op))
        .collect()
}

/// Requiring all ten sections turns an emptied generator section, or a hand-edit
/// that drops one, into a failure rather than a test that asserts nothing.
#[test]
fn every_section_is_populated() {
    for op in OPS {
        assert!(
            !cases_for(op).is_empty(),
            "album_match.json carries no {op} cases; the generator must emit all ten sections"
        );
    }
}

fn strings(value: &Value, case: usize, what: &str) -> Vec<String> {
    value
        .as_array()
        .unwrap_or_else(|| panic!("case {case}: {what} is an array of strings"))
        .iter()
        .map(|entry| {
            entry
                .as_str()
                .unwrap_or_else(|| panic!("case {case}: {what} holds strings"))
                .to_string()
        })
        .collect()
}

fn summary(value: &Value, case: usize, what: &str) -> AlbumSummary {
    serde_json::from_value(value.clone())
        .unwrap_or_else(|e| panic!("case {case}: {what} is an AlbumSummary: {e}"))
}

fn detail(value: &Value, case: usize, what: &str) -> AlbumDetail {
    serde_json::from_value(value.clone())
        .unwrap_or_else(|e| panic!("case {case}: {what} is an AlbumDetail: {e}"))
}

fn candidate(value: &Value, case: usize) -> Candidate {
    serde_json::from_value(value.clone())
        .unwrap_or_else(|e| panic!("case {case}: a track is a Candidate: {e}"))
}

/// The `(summary, detail)` pairs a `dedupe` or `rank` case ranks.
fn detail_pairs(value: &Value, case: usize) -> Vec<(AlbumSummary, AlbumDetail)> {
    value
        .as_array()
        .unwrap_or_else(|| panic!("case {case}: details is an array of pairs"))
        .iter()
        .map(|entry| {
            let pair = entry
                .as_array()
                .unwrap_or_else(|| panic!("case {case}: a detail pair is a two-element array"));
            let [candidate, detail] = pair.as_slice() else {
                panic!("case {case}: a detail pair is exactly a summary and a detail");
            };
            (
                summary(candidate, case, "a pair summary"),
                self::detail(detail, case, "a pair detail"),
            )
        })
        .collect()
}

fn indices(value: &Value, case: usize) -> Vec<usize> {
    value
        .as_array()
        .unwrap_or_else(|| panic!("case {case}: expected is an array of indices"))
        .iter()
        .map(|entry| {
            entry
                .as_u64()
                .unwrap_or_else(|| panic!("case {case}: an index is a whole number"))
                as usize
        })
        .collect()
}

fn scores(value: &Value, case: usize) -> Vec<(f64, usize)> {
    value
        .as_array()
        .unwrap_or_else(|| panic!("case {case}: expected is an array of score/index pairs"))
        .iter()
        .map(|entry| {
            let pair = entry
                .as_array()
                .unwrap_or_else(|| panic!("case {case}: a score row is a two-element array"));
            let [score, index] = pair.as_slice() else {
                panic!("case {case}: a score row is exactly a score and an index");
            };
            (
                score
                    .as_f64()
                    .unwrap_or_else(|| panic!("case {case}: a score is a float")),
                index
                    .as_u64()
                    .unwrap_or_else(|| panic!("case {case}: an index is a whole number"))
                    as usize,
            )
        })
        .collect()
}

#[test]
fn group_by_album_id_matches_python() {
    for (index, case) in cases_for("group").iter().enumerate() {
        let tracks: Vec<SourceTrack> = case.input["tracks"]
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: a group case carries a tracks array"))
            .iter()
            .map(|value| track_from_value(value, index, "a library track"))
            .collect();
        let expected: Vec<(i64, Vec<usize>)> = case
            .expected
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: expected is a list of album groups"))
            .iter()
            .map(|entry| {
                let pair = entry
                    .as_array()
                    .unwrap_or_else(|| panic!("case {index}: a group is a two-element array"));
                let [album_id, members] = pair.as_slice() else {
                    panic!("case {index}: a group is exactly an id and its members");
                };
                (
                    album_id
                        .as_i64()
                        .unwrap_or_else(|| panic!("case {index}: an album id is an integer")),
                    indices(members, index),
                )
            })
            .collect();
        let actual: Vec<(i64, Vec<usize>)> = album_match::group_by_album_id(&tracks)
            .into_iter()
            .collect();
        assert_eq!(actual, expected, "case {index}");
    }
}

#[test]
fn version_term_matches_python() {
    for (index, case) in cases_for("version").iter().enumerate() {
        let source = strings(&case.input["source"], index, "the source qualifiers");
        let cand = strings(&case.input["cand"], index, "the candidate qualifiers");
        let expected = case
            .expected
            .as_f64()
            .unwrap_or_else(|| panic!("case {index}: version_term expects a float"));
        // Exact equality: the overlap term is summed in a fixed order in both
        // languages, so a reassociation shows here and nowhere else.
        assert_eq!(
            album_match::version_term(&source, &cand),
            expected,
            "case {index}"
        );
    }
}

#[test]
fn album_meta_score_matches_python() {
    for (index, case) in cases_for("meta").iter().enumerate() {
        let source = track_from_value(&case.input["source"], index, "the source");
        let candidate = summary(&case.input["candidate"], index, "the candidate");
        let expected = case
            .expected
            .as_f64()
            .unwrap_or_else(|| panic!("case {index}: album_meta_score expects a float"));
        assert_eq!(
            album_match::album_meta_score(&source, &candidate),
            expected,
            "case {index}"
        );
    }
}

#[test]
fn sibling_year_matches_python() {
    for (index, case) in cases_for("sibling_year").iter().enumerate() {
        let sib = case.input["sib"].as_i64();
        let parent = case.input["parent"].as_i64();
        let expected = case.expected.as_i64();
        assert_eq!(
            album_match::sibling_year(sib, parent),
            expected,
            "case {index}"
        );
    }
}

#[test]
fn sibling_entry_matches_python() {
    for (index, case) in cases_for("sibling_entry").iter().enumerate() {
        let parent = summary(&case.input["parent"], index, "the parent");
        let sib = summary(&case.input["sib"], index, "the sibling");
        let expected = summary(&case.expected, index, "the entry");
        assert_eq!(
            album_match::sibling_entry(&parent, &sib),
            expected,
            "case {index}"
        );
    }
}

#[test]
fn dedupe_details_matches_python() {
    for (index, case) in cases_for("dedupe").iter().enumerate() {
        let details = detail_pairs(&case.input["details"], index);
        let expected = indices(&case.expected, index);
        assert_eq!(
            album_match::dedupe_details(&details),
            expected,
            "case {index}"
        );
    }
}

#[test]
fn best_track_fit_matches_python() {
    for (index, case) in cases_for("fit").iter().enumerate() {
        let source = track_from_value(&case.input["source"], index, "the source");
        let tracks: Vec<Candidate> = case.input["tracks"]
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: a fit case carries a tracks array"))
            .iter()
            .map(|value| candidate(value, index))
            .collect();
        let album_title = case.input["album_title"]
            .as_str()
            .unwrap_or_else(|| panic!("case {index}: a fit case carries an album title"));
        let actual = album_match::best_track_fit(&source, &tracks, album_title);

        let Some((fit, row_index)) = actual else {
            assert!(
                case.expected.is_null(),
                "case {index}: the port found no fit but the corpus expects one"
            );
            continue;
        };
        let expected = &case.expected;
        assert!(
            !expected.is_null(),
            "case {index}: the port found a fit but the corpus expects none"
        );
        assert_eq!(
            fit.overall,
            expected["overall"].as_f64().unwrap(),
            "case {index}: overall"
        );
        assert_eq!(
            fit.title_similarity,
            expected["title_similarity"].as_f64().unwrap(),
            "case {index}: title_similarity"
        );
        assert_eq!(
            fit.artist_similarity,
            expected["artist_similarity"].as_f64().unwrap(),
            "case {index}: artist_similarity"
        );
        assert_eq!(
            fit.album_similarity,
            expected["album_similarity"].as_f64().unwrap(),
            "case {index}: album_similarity"
        );
        assert_eq!(
            fit.version_similarity,
            expected["version_similarity"].as_f64().unwrap(),
            "case {index}: version_similarity"
        );
        assert_eq!(
            fit.track_num_match,
            expected["track_num_match"].as_bool().unwrap(),
            "case {index}: track_num_match"
        );
        assert_eq!(
            fit.duration_delta_sec,
            expected["duration_delta_sec"].as_i64(),
            "case {index}: duration_delta_sec"
        );
        assert_eq!(
            row_index,
            expected["index"].as_u64().unwrap() as usize,
            "case {index}: the winning row"
        );
    }
}

#[test]
fn rank_candidates_matches_python() {
    for (index, case) in cases_for("rank").iter().enumerate() {
        let first = track_from_value(&case.input["first"], index, "the first source");
        let sources: Vec<SourceTrack> = case.input["sources"]
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: a rank case carries a sources array"))
            .iter()
            .map(|value| track_from_value(value, index, "a source"))
            .collect();
        let details = detail_pairs(&case.input["details"], index);
        let expected = scores(&case.expected, index);
        assert_eq!(
            album_match::rank_candidates(&first, &sources, &details),
            expected,
            "case {index}"
        );
    }
}

#[test]
fn album_abstains_matches_python() {
    for (index, case) in cases_for("abstain").iter().enumerate() {
        let scored = scores(&case.input["scored"], index);
        let expected = case
            .expected
            .as_bool()
            .unwrap_or_else(|| panic!("case {index}: album_abstains expects a boolean"));
        assert_eq!(
            album_match::album_abstains(&scored),
            expected,
            "case {index}"
        );
    }
}

fn assert_map_result(result: &MatchResult, want: &MapExpected, label: &str) {
    assert_eq!(result.yt_video_id, want.yt_video_id, "{label}: yt_video_id");
    assert_eq!(result.yt_title, want.yt_title, "{label}: yt_title");
    assert_eq!(result.yt_artist, want.yt_artist, "{label}: yt_artist");
    assert_eq!(result.yt_album, want.yt_album, "{label}: yt_album");
    assert_eq!(
        result.yt_album_track_num, want.yt_album_track_num,
        "{label}: yt_album_track_num"
    );
    assert_eq!(
        result.yt_duration_sec, want.yt_duration_sec,
        "{label}: yt_duration_sec"
    );
    // The persisted strings, not the Rust variant names: the two must agree with
    // the schema's CHECK literals, and this is the seam that would notice if the
    // serde renames ever drifted.
    assert_eq!(
        result.match_method.as_str(),
        want.match_method,
        "{label}: match_method"
    );
    assert_eq!(result.status.as_str(), want.status, "{label}: status");
    assert_eq!(
        result.review_reason.is_some(),
        want.review_reason_present,
        "{label}: whether a review reason is present"
    );
    let confidence = &result.confidence;
    assert_eq!(confidence.overall, want.overall, "{label}: overall");
    assert_eq!(
        confidence.title_similarity, want.title_similarity,
        "{label}: title_similarity"
    );
    assert_eq!(
        confidence.artist_similarity, want.artist_similarity,
        "{label}: artist_similarity"
    );
    assert_eq!(
        confidence.album_similarity, want.album_similarity,
        "{label}: album_similarity"
    );
    assert_eq!(
        confidence.duration_delta_sec, want.duration_delta_sec,
        "{label}: duration_delta_sec"
    );
    assert_eq!(
        confidence.version_similarity, want.version_similarity,
        "{label}: version_similarity"
    );
    assert_eq!(
        confidence.track_num_match, want.track_num_match,
        "{label}: track_num_match"
    );
    assert_eq!(
        confidence.album_coherence, want.album_coherence,
        "{label}: album_coherence"
    );
}

#[test]
fn map_winner_tracks_matches_python() {
    for (index, case) in cases_for("map").iter().enumerate() {
        let sources: Vec<SourceTrack> = case.input["sources"]
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: a map case carries a sources array"))
            .iter()
            .map(|value| track_from_value(value, index, "a source"))
            .collect();
        let winner = detail(&case.input["winner"], index, "the winner");
        let expected: Vec<MapExpected> = case
            .expected
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: expected is a list of results"))
            .iter()
            .map(|value| {
                serde_json::from_value(value.clone())
                    .unwrap_or_else(|e| panic!("case {index}: a result is a projection: {e}"))
            })
            .collect();

        let actual = album_match::map_winner_tracks(&sources, &winner);
        assert_eq!(actual.len(), expected.len(), "case {index}: result count");
        for (position, (result, want)) in actual.iter().zip(&expected).enumerate() {
            assert_map_result(result, want, &format!("case {index} result {position}"));
        }
    }
}
