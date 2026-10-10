//! Replay the frozen `search.json` corpus against the Rust port of
//! `tidal2ytm/planning_search.py`.
//!
//! The corpus is generated from Python and frozen; CI never regenerates it, so
//! a rule change in either language shows up here as a diff rather than as a
//! test that cannot fail.

use std::fs;

use serde::Deserialize;
use serde_json::Value;
use tidal2ytm_core::model::SourceTrack;
use tidal2ytm_core::search;

#[derive(serde::Deserialize)]
struct FixtureCase {
    input: Value,
    expected: Value,
}

/// The `SourceTrack` as a fixture row carries it.
///
/// `SourceTrack` derives no serde impls of its own, so this is where a committed
/// row becomes the domain type. Every field is required: a row that omitted one
/// would be asserting a default the port never had a rule for.
#[derive(Deserialize)]
struct SourceTrackRow {
    tidal_id: i64,
    title: String,
    artist: String,
    artists: Vec<String>,
    album: String,
    album_id: i64,
    album_year: Option<i64>,
    duration_sec: i64,
    track_num: i64,
    disc_num: i64,
    version: Option<String>,
}

impl From<SourceTrackRow> for SourceTrack {
    fn from(row: SourceTrackRow) -> Self {
        Self {
            tidal_id: row.tidal_id,
            title: row.title,
            artist: row.artist,
            artists: row.artists,
            album: row.album,
            album_id: row.album_id,
            album_year: row.album_year,
            duration_sec: row.duration_sec,
            track_num: row.track_num,
            disc_num: row.disc_num,
            version: row.version,
        }
    }
}

fn track_from_value(value: &Value, case: usize, what: &str) -> SourceTrack {
    let row: SourceTrackRow = serde_json::from_value(value.clone())
        .unwrap_or_else(|e| panic!("case {case}: {what} is a SourceTrack row: {e}"));
    row.into()
}

fn cases() -> Vec<FixtureCase> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/search.json");
    let raw = fs::read_to_string(&path).expect("tests/fixtures/search.json is committed data");
    serde_json::from_str::<Vec<FixtureCase>>(&raw)
        .expect("every fixture case has input and expected")
}

/// The four sections of the corpus, keyed by which test owns them.
fn split_sections() -> (
    Vec<FixtureCase>,
    Vec<FixtureCase>,
    Vec<FixtureCase>,
    Vec<FixtureCase>,
) {
    let mut haystacks = Vec::new();
    let mut search = Vec::new();
    let mut parse_link = Vec::new();
    let mut resolve_album = Vec::new();
    for case in cases() {
        match case.input["op"].as_str() {
            Some("haystacks") => haystacks.push(case),
            Some("search") => search.push(case),
            Some("parse_link") => parse_link.push(case),
            Some("resolve_album") => resolve_album.push(case),
            other => panic!("input names no known op: {other:?}"),
        }
    }
    // Each test skips the sections it does not own, so a section that loses all
    // its rows would leave that test asserting nothing while still reporting
    // green. Requiring every section turns an emptied generator section, or a
    // hand-edit that drops one, into a failure.
    for (label, section) in [
        ("haystacks", &haystacks),
        ("search", &search),
        ("parse_link", &parse_link),
        ("resolve_album", &resolve_album),
    ] {
        assert!(
            !section.is_empty(),
            "search.json carries no {label} cases; the generator must emit all four sections"
        );
    }
    (haystacks, search, parse_link, resolve_album)
}

/// A JSON array of indices, as whole numbers.
fn index_array(value: &Value, index: usize, what: &str) -> Vec<usize> {
    value
        .as_array()
        .unwrap_or_else(|| panic!("case {index}: {what} is an array of indices"))
        .iter()
        .map(|entry| {
            entry
                .as_u64()
                .unwrap_or_else(|| panic!("case {index}: {what} holds whole numbers"))
                as usize
        })
        .collect()
}

#[test]
fn named_haystacks_matches_python() {
    for (index, case) in split_sections().0.iter().enumerate() {
        let track = track_from_value(&case.input["track"], index, "the track");
        let actual: Vec<(String, String)> = search::named_haystacks(&track)
            .into_iter()
            .map(|(name, field)| (name, field.to_string()))
            .collect();
        let expected: Vec<(String, String)> = case
            .expected
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: expected is a list of name/field pairs"))
            .iter()
            .map(|pair| {
                let [name, field] = pair
                    .as_array()
                    .unwrap_or_else(|| panic!("case {index}: a haystack is a two-element array"))
                    .as_slice()
                else {
                    panic!("case {index}: a haystack is exactly a name and a field");
                };
                (
                    name.as_str()
                        .unwrap_or_else(|| panic!("case {index}: a haystack name is a string"))
                        .to_string(),
                    field
                        .as_str()
                        .unwrap_or_else(|| panic!("case {index}: a haystack field is a string"))
                        .to_string(),
                )
            })
            .collect();
        assert_eq!(actual, expected, "case {index}");
    }
}

#[test]
fn search_library_matches_python() {
    for (index, case) in split_sections().1.iter().enumerate() {
        let tracks: Vec<SourceTrack> = case.input["tracks"]
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: a search case carries a tracks array"))
            .iter()
            .map(|value| track_from_value(value, index, "a library track"))
            .collect();
        let query = case.input["query"]
            .as_str()
            .unwrap_or_else(|| panic!("case {index}: a search case carries a query"));
        let direct = case.expected["direct"]
            .as_bool()
            .unwrap_or_else(|| panic!("case {index}: a search case expects a direct flag"));

        let (actual_indices, actual_direct) = search::search_library(&tracks, query);
        assert_eq!(
            actual_indices,
            index_array(&case.expected["indices"], index, "the expected indices"),
            "case {index}: search_library({query:?})"
        );
        assert_eq!(
            actual_direct, direct,
            "case {index}: search_library({query:?}) direct"
        );
    }
}

#[test]
fn parse_tidal_link_matches_python() {
    for (index, case) in split_sections().2.iter().enumerate() {
        let raw = case.input["raw"]
            .as_str()
            .unwrap_or_else(|| panic!("case {index}: a parse_link case carries a raw input"));
        let actual = search::parse_tidal_link(raw);
        let expected = if case.expected.is_null() {
            None
        } else {
            let pair = case
                .expected
                .as_array()
                .unwrap_or_else(|| panic!("case {index}: a parse_link result is a pair or null"));
            let [kind, id] = pair.as_slice() else {
                panic!("case {index}: a parse_link result is exactly a kind and an id");
            };
            Some((
                kind.as_str()
                    .unwrap_or_else(|| panic!("case {index}: a link kind is a string")),
                id.as_i64()
                    .unwrap_or_else(|| panic!("case {index}: a link id is an integer")),
            ))
        };
        assert_eq!(actual, expected, "case {index}: parse_tidal_link({raw:?})");
    }
}

#[test]
fn resolve_album_link_matches_python() {
    for (index, case) in split_sections().3.iter().enumerate() {
        let tracks: Vec<SourceTrack> = case.input["tracks"]
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: a resolve case carries a tracks array"))
            .iter()
            .map(|value| track_from_value(value, index, "a library track"))
            .collect();
        let album_id = case.input["album_id"]
            .as_i64()
            .unwrap_or_else(|| panic!("case {index}: a resolve case carries an album id"));
        assert_eq!(
            search::resolve_album_link(&tracks, album_id),
            index_array(&case.expected, index, "the expected indices"),
            "case {index}: resolve_album_link({album_id})"
        );
    }
}
