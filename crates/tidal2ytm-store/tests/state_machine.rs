//! The merge decision and the store's transferred invariant, end to end.
//!
//! Every piece of Phase 3 so far was built and proved in isolation. This is the
//! one test that drives a track from a fresh match through the real database:
//! `decide_status` says what to write, `upsert_track` writes it, `write_status`
//! moves it, and the store's guard refuses to move it back.
//!
//! `classify_track`'s `SkipTransferred` arm and the store's
//! `TransferredIsSacred` guard are two separate defences, so the test exercises
//! both: a test that checked only the enum arm would not notice the store guard
//! being removed, and one that checked only the guard would not notice the enum
//! arm regressing to `Ask`.
//!
//! The store exposes no public row reader, so the audit trail's exact row count
//! is asserted by the store's own unit test
//! (`a_transferred_track_needs_an_override_to_move`, which has connection
//! access). Here the override is asserted to commit by its effect on the row's
//! status: the `transfer_attempt` insert and the status write share one
//! transaction, so a committed move cannot be missing its audit row.

use std::collections::BTreeMap;

use tidal2ytm_core::merge::{ExistingMatch, MergeAction, classify_track, decide_status};
use tidal2ytm_core::model::{
    ConfidenceBreakdown, MatchMethod, MatchResult, SourceTrack, TrackStatus,
};
use tidal2ytm_store::{ReviewOverride, Store, StoreError, TrackRow};

const ORIGINAL_VIDEO: &str = "AAAAAAAAAAA";
const OVERRIDE_VIDEO: &str = "CCCCCCCCCCC";
const TIDAL_ID: i64 = 100_001;

fn source_track() -> SourceTrack {
    SourceTrack {
        tidal_id: TIDAL_ID,
        title: "Fictional Song".to_string(),
        artist: "Fictional Artist".to_string(),
        artists: vec!["Fictional Artist".to_string()],
        album: "Fictional Album".to_string(),
        album_id: 200_001,
        album_year: Some(2024),
        duration_sec: 200,
        track_num: 3,
        disc_num: 1,
        version: None,
    }
}

/// A match that would land `pending`: a video id, a high total, and an album
/// that is not the wrong one.
fn match_result(video_id: &str) -> MatchResult {
    MatchResult {
        source: source_track(),
        yt_video_id: Some(video_id.to_string()),
        yt_title: Some("Fictional Song".to_string()),
        yt_artist: Some("Fictional Artist".to_string()),
        yt_album: Some("Fictional Album".to_string()),
        yt_album_track_num: Some(3),
        yt_duration_sec: Some(200),
        match_method: MatchMethod::Fuzzy,
        confidence: ConfidenceBreakdown {
            overall: 0.9,
            title_similarity: Some(1.0),
            artist_similarity: Some(1.0),
            album_similarity: Some(0.95),
            duration_delta_sec: Some(0),
            version_similarity: Some(1.0),
            track_num_match: Some(true),
            album_coherence: Some(1.0),
            summary: Some("fictional".to_string()),
        },
        status: TrackStatus::Pending,
        review_reason: None,
    }
}

fn track_row(
    album_id: i64,
    decision: &tidal2ytm_core::merge::MatchDecision,
    video_id: &str,
) -> TrackRow {
    TrackRow {
        tidal_id: TIDAL_ID,
        album_id,
        title: "Fictional Song".to_string(),
        artist: "Fictional Artist".to_string(),
        duration_sec: 200,
        track_num: 3,
        disc_num: 1,
        version: None,
        yt_video_id: Some(video_id.to_string()),
        yt_title: Some("Fictional Song".to_string()),
        yt_artist: Some("Fictional Artist".to_string()),
        yt_album: Some("Fictional Album".to_string()),
        yt_album_track_num: Some(3),
        yt_duration_sec: Some(200),
        match_method: decision.match_method,
        confidence_overall: 0.9,
        review_reason: decision.review_reason.clone(),
    }
}

#[test]
fn a_transferred_track_survives_a_rematch_until_an_override_moves_it() {
    let store = Store::open_in_memory().expect("open a store");
    store.migrate().expect("migrate");
    let artist = store
        .upsert_artist("Fictional Artist", "fictional artist")
        .expect("artist");
    let album = store
        .upsert_album(artist, "Fictional Album", "fictional album", Some(2024))
        .expect("album");

    // A fresh match, decided and written with the decision's method.
    let decision = decide_status(&match_result(ORIGINAL_VIDEO), None);
    assert_eq!(decision.action, MergeAction::AddNew);
    assert_eq!(decision.status, TrackStatus::Pending);
    assert_eq!(decision.match_method, MatchMethod::Fuzzy);
    let id = store
        .upsert_track(&track_row(album, &decision, ORIGINAL_VIDEO))
        .expect("track");
    // `upsert_track` inserts every new row as `pending` and skips the status
    // column; the method it stores is the decision's, which is asserted above.
    assert_eq!(
        store.status_counts().expect("counts"),
        BTreeMap::from([(TrackStatus::Pending, 1)])
    );

    // The track transfers.
    store
        .write_status(id, TrackStatus::Transferred, None)
        .expect("transferred");
    assert_eq!(
        store.status_counts().expect("counts"),
        BTreeMap::from([(TrackStatus::Transferred, 1)])
    );

    // A re-match finds a different video. The enum arm is the first defence.
    let existing = ExistingMatch {
        status: TrackStatus::Transferred,
        yt_video_id: Some(ORIGINAL_VIDEO.to_string()),
    };
    let rematch = decide_status(&match_result(OVERRIDE_VIDEO), Some(&existing));
    assert_eq!(
        classify_track(Some(&existing), Some(OVERRIDE_VIDEO)),
        MergeAction::SkipTransferred
    );
    assert_eq!(rematch.action, MergeAction::SkipTransferred);

    // The store guard is the second: a caller that ignored the enum arm still
    // cannot move the row, and the refusal leaves it exactly where it was.
    let refused = store
        .write_status(id, rematch.status, None)
        .expect_err("a transferred row must not move without an override");
    assert!(matches!(refused, StoreError::TransferredIsSacred { track_id } if track_id == id));
    assert_eq!(
        store.status_counts().expect("counts"),
        BTreeMap::from([(TrackStatus::Transferred, 1)])
    );

    // With an explicit override the row moves, and the audit is written in the
    // same transaction, so a committed move cannot be missing its reason.
    store
        .write_status(
            id,
            TrackStatus::Pending,
            Some(ReviewOverride {
                yt_video_id: OVERRIDE_VIDEO.to_string(),
                started_at: "2026-10-09T00:00:00Z".to_string(),
                outcome: "already_present".to_string(),
            }),
        )
        .expect("override");
    assert_eq!(
        store.status_counts().expect("counts"),
        BTreeMap::from([(TrackStatus::Pending, 1)])
    );
}
