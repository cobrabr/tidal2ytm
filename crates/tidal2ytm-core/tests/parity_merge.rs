//! Replay the frozen `merge.json` corpus against the Rust port in
//! `tidal2ytm_core::merge`.
//!
//! The corpus is generated from Python and frozen; CI never regenerates it, so a
//! rule change in either language shows up here as a diff rather than as a test
//! that cannot fail. `classify` and `ordered` rows freeze the real Python
//! functions; `decide` rows freeze a transcription of `matcher.match_track`'s
//! tail, which is where those decisions live. The corpus records the decision —
//! action, status, method and whether a reason exists — never the reason's text.

use std::collections::BTreeMap;
use std::fs;
use std::str::FromStr;

use serde_json::Value;
use tidal2ytm_core::merge::{
    ExistingMatch, MergeAction, classify_track, decide_status, ordered_selection,
};
use tidal2ytm_core::model::{
    ConfidenceBreakdown, MatchMethod, MatchResult, SourceTrack, TrackStatus,
};

mod common;
use common::track_from_value;

/// Every op the generator emits, one per `merge` section.
const OPS: &[&str] = &["classify", "decide", "ordered"];

#[derive(serde::Deserialize)]
struct FixtureCase {
    input: Value,
    expected: Value,
}

fn cases() -> Vec<FixtureCase> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/merge.json");
    let raw = fs::read_to_string(&path).expect("tests/fixtures/merge.json is committed data");
    serde_json::from_str::<Vec<FixtureCase>>(&raw)
        .expect("every fixture case has input and expected")
}

fn cases_for(op: &str) -> Vec<FixtureCase> {
    cases()
        .into_iter()
        .filter(|case| case.input["op"].as_str() == Some(op))
        .collect()
}

/// Requiring all three sections turns an emptied generator section, or a hand
/// edit that drops one, into a failure rather than a test that asserts nothing.
#[test]
fn every_section_is_populated() {
    for op in OPS {
        assert!(
            !cases_for(op).is_empty(),
            "merge.json carries no {op} cases; the generator must emit all three sections"
        );
    }
}

fn action_of(raw: &str, case: usize) -> MergeAction {
    match raw {
        "skip-transferred" => MergeAction::SkipTransferred,
        "add-new" => MergeAction::AddNew,
        "keep-same" => MergeAction::KeepSame,
        "ask" => MergeAction::Ask,
        other => panic!("case {case}: unknown merge action {other:?}"),
    }
}

fn existing_of(value: &Value, case: usize) -> Option<ExistingMatch> {
    if value.is_null() {
        return None;
    }
    let raw = value["status"]
        .as_str()
        .unwrap_or_else(|| panic!("case {case}: an existing row carries a status"));
    let status = TrackStatus::from_str(raw)
        .unwrap_or_else(|_| panic!("case {case}: {raw:?} is a track status"));
    Some(ExistingMatch {
        status,
        yt_video_id: value["yt_video_id"].as_str().map(str::to_string),
    })
}

/// The `SourceTrack` a `decide` case never scores but `MatchResult` requires.
fn placeholder_source() -> SourceTrack {
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

/// Build the `MatchResult` a `decide` case is about, from the four fields the
/// decision reads.
fn match_result_of(value: &Value, case: usize) -> MatchResult {
    let method_raw = value["method"]
        .as_str()
        .unwrap_or_else(|| panic!("case {case}: a decision carries a method"));
    let match_method = MatchMethod::from_str(method_raw)
        .unwrap_or_else(|_| panic!("case {case}: {method_raw:?} is a match method"));
    let overall = value["overall"]
        .as_f64()
        .unwrap_or_else(|| panic!("case {case}: a decision carries an overall score"));
    let album_similarity = value["album_similarity"].as_f64();
    MatchResult {
        source: placeholder_source(),
        yt_video_id: value["video_id"].as_str().map(str::to_string),
        yt_title: None,
        yt_artist: None,
        yt_album: None,
        yt_album_track_num: None,
        yt_duration_sec: None,
        match_method,
        confidence: ConfidenceBreakdown {
            overall,
            title_similarity: None,
            artist_similarity: None,
            album_similarity,
            duration_delta_sec: None,
            version_similarity: None,
            track_num_match: None,
            album_coherence: None,
            summary: None,
        },
        status: TrackStatus::Pending,
        review_reason: None,
    }
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

#[test]
fn classify_track_matches_python() {
    for (index, case) in cases_for("classify").iter().enumerate() {
        let existing = existing_of(&case.input["existing"], index);
        let video_id = case.input["video_id"].as_str();
        let expected = case
            .expected
            .as_str()
            .unwrap_or_else(|| panic!("case {index}: a classify expectation is an action string"));
        assert_eq!(
            classify_track(existing.as_ref(), video_id),
            action_of(expected, index),
            "case {index}"
        );
    }
}

#[test]
fn decide_status_matches_the_frozen_transcription() {
    for (index, case) in cases_for("decide").iter().enumerate() {
        let result = match_result_of(&case.input, index);
        let existing = existing_of(&case.input["existing"], index);
        let decision = decide_status(&result, existing.as_ref());
        let expected = &case.expected;
        assert_eq!(
            decision.action,
            action_of(expected["action"].as_str().unwrap(), index),
            "case {index}: action"
        );
        assert_eq!(
            decision.status.as_str(),
            expected["status"].as_str().unwrap(),
            "case {index}: status"
        );
        assert_eq!(
            decision.match_method.as_str(),
            expected["match_method"].as_str().unwrap(),
            "case {index}: match_method"
        );
        assert_eq!(
            decision.review_reason.is_some(),
            expected["review_reason_present"].as_bool().unwrap(),
            "case {index}: whether a review reason is present"
        );
    }
}

#[test]
fn ordered_selection_matches_python() {
    for (index, case) in cases_for("ordered").iter().enumerate() {
        let tracks: Vec<SourceTrack> = case.input["tracks"]
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: an ordered case carries a tracks array"))
            .iter()
            .map(|value| track_from_value(value, index, "a selected track"))
            .collect();
        let mut selection: BTreeMap<i64, SourceTrack> = BTreeMap::new();
        for track in tracks {
            selection.insert(track.tidal_id, track);
        }
        assert_eq!(
            ordered_selection(&selection),
            indices(&case.expected, index),
            "case {index}"
        );
    }
}
