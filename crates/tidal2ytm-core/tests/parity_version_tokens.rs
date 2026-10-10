//! Replay the frozen `version_tokens.json` corpus against the Rust port of
//! `tidal2ytm/version_tokens.py`.
//!
//! The corpus is generated from Python and frozen; CI never regenerates it, so
//! a rule change in either language shows up here as a diff rather than as a
//! test that cannot fail.

use std::fs;

use serde_json::Value;
use tidal2ytm_core::version_tokens;

#[derive(serde::Deserialize)]
struct FixtureCase {
    input: Value,
    expected: Value,
}

fn cases() -> Vec<FixtureCase> {
    let path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/version_tokens.json");
    let raw =
        fs::read_to_string(&path).expect("tests/fixtures/version_tokens.json is committed data");
    serde_json::from_str::<Vec<FixtureCase>>(&raw)
        .expect("every fixture case has input and expected")
}

/// The two sections of the corpus, keyed by which test owns them.
fn split_sections() -> (Vec<FixtureCase>, Vec<FixtureCase>) {
    let all = cases();
    let (mut split, mut qualify) = (Vec::new(), Vec::new());
    for case in all {
        match case.input["op"].as_str() {
            Some("split") => split.push(case),
            Some("qualify") => qualify.push(case),
            other => panic!("input names no known op: {other:?}"),
        }
    }
    // Each test skips the section it does not own, so a section that loses all
    // its rows would leave that test asserting nothing while still reporting
    // green. Requiring both halves turns an emptied generator section, or a
    // hand-edit that drops one, into a failure.
    assert!(
        !split.is_empty(),
        "version_tokens.json carries no split cases; the generator must emit both sections"
    );
    assert!(
        !qualify.is_empty(),
        "version_tokens.json carries no qualifier-similarity cases; the generator must emit both"
    );
    (split, qualify)
}

/// The qualifier lists a `qualify` case carries, in the shape the port takes.
fn qualifier_list(raw: &Value) -> Vec<String> {
    raw.as_array()
        .expect("a qualifier list is an array")
        .iter()
        .map(|entry| entry.as_str().expect("a qualifier is a string").to_string())
        .collect()
}

#[test]
fn split_base_qualifiers_matches_python() {
    for (index, case) in split_sections().0.iter().enumerate() {
        let input = case.input["text"]
            .as_str()
            .unwrap_or_else(|| panic!("case {index}: a split case carries a text input"));
        let [expected_base, expected_quals] = case
            .expected
            .as_array()
            .unwrap_or_else(|| panic!("case {index}: a split case expects [base, qualifiers]"))
            .as_slice()
        else {
            panic!("case {index}: a split case expects exactly a base and a qualifier list");
        };
        let expected_base = expected_base
            .as_str()
            .unwrap_or_else(|| panic!("case {index}: the expected base is a string"));
        let expected_quals = qualifier_list(expected_quals);

        assert_eq!(
            version_tokens::split_base_qualifiers(input),
            (expected_base.to_string(), expected_quals),
            "case {index}: split_base_qualifiers({input:?})"
        );
    }
}

#[test]
fn qualifier_similarity_matches_python() {
    for (index, case) in split_sections().1.iter().enumerate() {
        let source = qualifier_list(&case.input["source"]);
        let target = qualifier_list(&case.input["target"]);
        let expected = case
            .expected
            .as_f64()
            .unwrap_or_else(|| panic!("case {index}: a qualify case expects a number"));
        // Exact equality, not an epsilon, for the reason `parity_text.rs` gives:
        // both sides run the same three IEEE-754 operations over the same
        // integers, so they produce the same double or the corpus is stale.
        assert_eq!(
            version_tokens::qualifier_similarity(&source, &target),
            expected,
            "case {index}: qualifier_similarity({source:?}, {target:?})"
        );
    }
}
