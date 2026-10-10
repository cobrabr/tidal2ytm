//! Replay the frozen `track_scoring.json` corpus against the Rust port of
//! `tidal2ytm/track_scoring.py`.
//!
//! The corpus is generated from Python and frozen; CI never regenerates it, so
//! a rule change in either language shows up here as a diff rather than as a
//! test that cannot fail.

use std::fs;

use serde_json::Value;
use tidal2ytm_core::candidate::Candidate;
use tidal2ytm_core::model::SourceTrack;
use tidal2ytm_core::track_scoring;

mod common;
use common::track_from_value;

#[derive(serde::Deserialize)]
struct FixtureCase {
    input: Value,
    expected: Value,
}

fn cases() -> Vec<FixtureCase> {
    let path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/track_scoring.json");
    let raw =
        fs::read_to_string(&path).expect("tests/fixtures/track_scoring.json is committed data");
    serde_json::from_str::<Vec<FixtureCase>>(&raw)
        .expect("every fixture case has input and expected")
}

/// The two sections of the corpus, keyed by which test owns them.
fn split_sections() -> (Vec<FixtureCase>, Vec<FixtureCase>) {
    let all = cases();
    let (mut duration, mut score) = (Vec::new(), Vec::new());
    for case in all {
        match case.input["op"].as_str() {
            Some("duration_ok") => duration.push(case),
            Some("score") => score.push(case),
            other => panic!("input names no known op: {other:?}"),
        }
    }
    // Each test skips the section it does not own, so a section that loses all
    // its rows would leave that test asserting nothing while still reporting
    // green. Requiring both halves turns an emptied generator section, or a
    // hand-edit that drops one, into a failure.
    assert!(
        !duration.is_empty(),
        "track_scoring.json carries no duration cases; the generator must emit both sections"
    );
    assert!(
        !score.is_empty(),
        "track_scoring.json carries no scoring cases; the generator must emit both sections"
    );
    (duration, score)
}

/// The `SourceTrack` and `Candidate` a scoring case scores.
fn operands(case: &FixtureCase, index: usize) -> (SourceTrack, Candidate) {
    let source = track_from_value(&case.input["source"], index, "source");
    let candidate: Candidate = serde_json::from_value(case.input["candidate"].clone())
        .unwrap_or_else(|e| panic!("case {index}: candidate is a Candidate row: {e}"));
    (source, candidate)
}

#[test]
fn duration_ok_matches_python() {
    for (index, case) in split_sections().0.iter().enumerate() {
        let source_sec = case.input["source_sec"]
            .as_i64()
            .unwrap_or_else(|| panic!("case {index}: a duration case carries a source duration"));
        let candidate_sec = match &case.input["candidate_sec"] {
            // A row whose candidate has no duration says so with an explicit
            // null, which is the `None` the function takes rather than a missing
            // field.
            Value::Null => None,
            other => Some(other.as_i64().unwrap_or_else(|| {
                panic!("case {index}: a duration case carries whole seconds or a null")
            })),
        };
        let expected = case
            .expected
            .as_bool()
            .unwrap_or_else(|| panic!("case {index}: a duration case expects a boolean"));
        assert_eq!(
            track_scoring::duration_ok(source_sec, candidate_sec),
            expected,
            "case {index}: duration_ok({source_sec}, {candidate_sec:?})"
        );
    }
}

#[test]
fn score_track_fit_matches_python() {
    for (index, case) in split_sections().1.iter().enumerate() {
        let (source, candidate) = operands(case, index);
        let fit = track_scoring::score_track_fit(&source, &candidate);
        let field = |name: &str| {
            case.expected
                .get(name)
                .unwrap_or_else(|| panic!("case {index}: no expected {name}"))
                .clone()
        };

        // Exact equality on every component, for the reason `parity_text.rs`
        // gives: the four products are summed in a fixed left-to-right order in
        // both languages, so each produces the same IEEE-754 double or the
        // corpus is stale. An epsilon here would hide a reassociation, which is
        // the one thing this comparison exists to catch.
        assert_eq!(
            fit.overall,
            field("overall").as_f64().unwrap(),
            "case {index}: overall"
        );
        assert_eq!(
            fit.title_similarity,
            field("title_similarity").as_f64().unwrap(),
            "case {index}: title_similarity"
        );
        assert_eq!(
            fit.artist_similarity,
            field("artist_similarity").as_f64().unwrap(),
            "case {index}: artist_similarity"
        );
        assert_eq!(
            fit.album_similarity,
            field("album_similarity").as_f64().unwrap(),
            "case {index}: album_similarity"
        );
        assert_eq!(
            fit.version_similarity,
            field("version_similarity").as_f64().unwrap(),
            "case {index}: version_similarity"
        );
        assert_eq!(
            fit.track_num_match,
            field("track_num_match").as_bool().unwrap(),
            "case {index}: track_num_match"
        );
        assert_eq!(
            fit.duration_delta_sec,
            field("duration_delta_sec").as_i64(),
            "case {index}: duration_delta_sec"
        );
    }
}
