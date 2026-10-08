//! Replay the frozen `text.json` corpus against the Rust port of
//! `tidal2ytm/text.py`.
//!
//! The corpus is generated from Python and frozen; CI never regenerates it, so
//! a rule change in either language shows up here as a diff rather than as a
//! test that cannot fail.

use std::fs;

use serde_json::Value;
use tidal2ytm_core::text;

#[derive(serde::Deserialize)]
struct FixtureCase {
    input: Value,
    expected: Value,
}

fn cases() -> Vec<FixtureCase> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/text.json");
    let raw = fs::read_to_string(&path).expect("tests/fixtures/text.json is committed data");
    serde_json::from_str::<Vec<FixtureCase>>(&raw)
        .expect("every fixture case has input and expected")
}

/// The two sections of the corpus, keyed by which test owns them.
fn split_sections() -> (Vec<FixtureCase>, Vec<FixtureCase>) {
    let all = cases();
    let (mut normalize, mut similarity) = (Vec::new(), Vec::new());
    for case in all {
        match case.input {
            Value::String(_) => normalize.push(case),
            Value::Array(_) => similarity.push(case),
            _ => panic!("input is neither a string nor an array"),
        }
    }
    // Each test skips the section it does not own, so a section that loses all
    // its rows would leave that test asserting nothing while still reporting
    // green. Requiring both halves turns an emptied generator section, or a
    // hand-edit that drops one, into a failure.
    assert!(
        !normalize.is_empty(),
        "text.json carries no normalize cases; the generator must emit both sections"
    );
    assert!(
        !similarity.is_empty(),
        "text.json carries no similarity cases; the generator must emit both sections"
    );
    (normalize, similarity)
}

#[test]
fn normalize_matches_python() {
    for (index, case) in split_sections().0.iter().enumerate() {
        let (Some(input), Some(expected)) = (case.input.as_str(), case.expected.as_str()) else {
            panic!("case {index}: a normalize case is a string in and a string out");
        };
        assert_eq!(
            text::normalize(input),
            expected,
            "case {index}: normalize({input:?})"
        );
    }
}

#[test]
fn similarity_matches_python() {
    for (index, case) in split_sections().1.iter().enumerate() {
        let Some(operands) = case.input.as_array() else {
            panic!("case {index}: a similarity case is a two-element array");
        };
        let [first, second] = operands.as_slice() else {
            panic!("case {index}: a similarity case has exactly two operands");
        };
        let expected = case
            .expected
            .as_f64()
            .expect("a similarity case expects a number");
        // Exact equality, not an epsilon: both sides reduce to two integers,
        // the total matched characters and the total length, and divide them in
        // the same order CPython's `difflib` does, so each produces the same
        // IEEE-754 double. An epsilon would hide exactly the divergence this
        // test exists to find.
        assert_eq!(
            text::similarity(
                first.as_str().expect("an operand is a string"),
                second.as_str().expect("an operand is a string"),
            ),
            expected,
            "case {index}: similarity({first:?}, {second:?})"
        );
    }
}
