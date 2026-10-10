//! Parenthesised qualifiers, split off a title and scored against each other.
//!
//! The port of `tidal2ytm/version_tokens.py`. A title's `(Live)` and
//! `[Remastered 2011]` are evidence about *which recording* this is, not about
//! what it is called, so scoring them inside `similarity` would let a candidate
//! with the right words and the wrong recording outrank the right one. Splitting
//! them out lets the title score ignore the qualifier and a second, separately
//! weighted score reward agreement about it.

use std::collections::BTreeSet;
use std::ops::Range;

/// `str.strip()`'s whitespace set.
///
/// `char::is_whitespace` is Unicode's `White_Space`, which Python's
/// `str.isspace()` — and so `str.strip()` — exceeds by the four ASCII
/// separators `U+001C`–`U+001F`. Where the two disagree Python deletes a
/// character this would keep, and a kept `U+001C` inside a qualifier is a
/// qualifier that can match nothing.
fn is_python_strippable(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}

/// The spans `[\(\[](.*?)[\)\]]` matches in `text`, left to right and
/// non-overlapping.
///
/// The opener and the closer are separate character classes, so `(live]` is a
/// match and not a typo to be caught, which is what the pattern says. `.` in
/// the body is Python's, without `re.DOTALL`, so a newline ends the attempt at
/// this position rather than being swallowed — a qualifier never spans a line.
/// Where an opener has no closer before the next newline the pattern moves on
/// to look for another opener, which is what resuming from `start + 1` does.
///
/// Scanning bytes is safe here because every byte this looks at is ASCII, and no
/// UTF-8 continuation byte can equal one. The ranges it hands back therefore
/// always start and end on a character boundary.
fn qualifier_spans(text: &str) -> Vec<Range<usize>> {
    let bytes = text.as_bytes();
    let mut spans = Vec::new();
    let mut cursor = 0;
    while cursor < bytes.len() {
        if bytes[cursor] != b'(' && bytes[cursor] != b'[' {
            cursor += 1;
            continue;
        }
        let mut probe = cursor + 1;
        let close = loop {
            match bytes.get(probe) {
                Some(b')' | b']') => break Some(probe),
                Some(b'\n') | None => break None,
                Some(_) => probe += 1,
            }
        };
        match close {
            Some(close) => {
                spans.push(cursor..close + 1);
                cursor = close + 1;
            }
            None => cursor += 1,
        }
    }
    spans
}

/// Split `text` into its base title and its qualifiers.
///
/// The qualifiers are casefolded and stripped, and one that is empty once
/// stripped is dropped: `()` carries no evidence, so it must not become a
/// qualifier that counts toward the overlap and cannot match anything. The base
/// is the text with those occurrences removed and trimmed.
#[must_use]
pub fn split_base_qualifiers(text: &str) -> (String, Vec<String>) {
    let spans = qualifier_spans(text);
    let mut base = String::with_capacity(text.len());
    let mut cursor = 0;
    for span in &spans {
        base.push_str(&text[cursor..span.start]);
        cursor = span.end;
    }
    base.push_str(&text[cursor..]);

    // `to_lowercase` stands in for Python's `casefold`. The two agree on every
    // qualifier a title actually carries — case, and accents — and differ only
    // where casefolding changes length (`ß` to `ss`, `ﬁ` to `fi`, `ς` to `σ`),
    // which is not a thing a title puts inside brackets.
    let qualifiers: Vec<String> = spans
        .iter()
        .map(|span| text[span.start + 1..span.end - 1].trim_matches(is_python_strippable))
        .map(str::to_lowercase)
        .filter(|qualifier| !qualifier.is_empty())
        .collect();

    (
        base.trim_matches(is_python_strippable).to_string(),
        qualifiers,
    )
}

/// How alike two qualifier sets are, in `[0.4, 1.0]`.
///
/// The floor is 0.4 rather than 0 because a disagreement about the recording is
/// weak evidence, not a refutation: one side naming a qualifier is common
/// (`Live`, `Remastered`) while the other naming none is equally common for the
/// same song, and scoring either as zero would let an inexact title outrank an
/// exact one. That floor is worth `0.6 * 0.15 = 0.09` of `overall` in
/// `track_scoring`, so it is a real share of the decision rather than a rounding
/// detail, which is why it is stated here and not left inside the arithmetic.
#[must_use]
pub fn qualifier_similarity(source: &[String], target: &[String]) -> f64 {
    if source.is_empty() && target.is_empty() {
        return 1.0;
    }
    if source.is_empty() || target.is_empty() {
        return 0.4;
    }
    // Sets, so a qualifier repeated on one side counts once, and so the order
    // the two sides happen to be in cannot affect the score.
    let set_source: BTreeSet<&str> = source.iter().map(String::as_str).collect();
    let set_target: BTreeSet<&str> = target.iter().map(String::as_str).collect();
    if set_source == set_target {
        return 1.0;
    }
    let overlap = set_source.intersection(&set_target).count() as f64
        / set_source.union(&set_target).count() as f64;
    // `0.6 * overlap`, then `0.4 +`, in that order and not reassociated. Python
    // divides into `overlap` first and multiplies that, and the corpus compares
    // these values exactly; folding the divide into the multiplier changes the
    // last bit for some ratios, `1 / 7` among them.
    0.4 + 0.6 * overlap
}

#[cfg(test)]
mod tests {
    use super::*;

    fn quals(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_string()).collect()
    }

    #[test]
    fn one_sided_qualifiers_score_exactly_the_floor() {
        // The floor is the value `0.4 + 0.6 * overlap` reaches at zero overlap,
        // and it is worth 0.09 of `overall` downstream, so it has to be the same
        // number the arithmetic produces rather than a nearby one.
        assert_eq!(qualifier_similarity(&[], &quals(&["live"])), 0.4);
        assert_eq!(qualifier_similarity(&quals(&["live"]), &[]), 0.4);
    }

    #[test]
    fn the_overlap_term_reaches_the_floor_and_the_ceiling() {
        // Both ends of `0.4 + 0.6 * overlap`, with neither set empty: at zero
        // overlap the expression must land on the same value the one-sided
        // branch returns, and at full overlap on the same value the equal-sets
        // branch returns, or a score would depend on which shape produced it.
        assert_eq!(
            qualifier_similarity(&quals(&["live"]), &quals(&["acoustic"])),
            0.4
        );
        assert_eq!(
            qualifier_similarity(&quals(&["live"]), &quals(&["live"])),
            1.0
        );
    }

    #[test]
    fn neither_side_naming_a_qualifier_is_full_agreement() {
        // The empty set is a real answer, not missing data: a title with no
        // parenthetical agrees exactly with another that has none.
        assert_eq!(qualifier_similarity(&[], &[]), 1.0);
    }
}
