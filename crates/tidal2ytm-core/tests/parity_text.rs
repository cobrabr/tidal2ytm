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

#[test]
fn normalize_matches_python() {
    for (index, case) in cases().iter().enumerate() {
        if case.input.is_array() {
            continue;
        }
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
    for (index, case) in cases().iter().enumerate() {
        if case.input.is_string() {
            continue;
        }
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
