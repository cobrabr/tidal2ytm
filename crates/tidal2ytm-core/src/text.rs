//! Text normalization and similarity.
//!
//! The port of `tidal2ytm/text.py`. Both functions are pure: no I/O, no
//! globals, no state carried between calls.

use std::collections::HashMap;

use unicode_normalization::UnicodeNormalization as _;

/// The ASCII code points Python's `\s` matches inside `[^a-z0-9\s]`.
///
/// This is wider than `char::is_ascii_whitespace`, which omits `0x0B` and
/// `0x1C`–`0x1F`. Python's `\s` and `str.isspace()` both accept those, and
/// `text.py`'s pipeline makes them reachable: the ASCII-ignore encode runs
/// before the filter, so the operand reaching the filter is already pure ASCII
/// and only this set can be matched. Using the Rust predicate instead would
/// delete a code point Python keeps, shortening the string `similarity` then
/// divides by.
const PYTHON_ASCII_WHITESPACE: &[char] = &[
    '\u{09}', '\u{0a}', '\u{0b}', '\u{0c}', '\u{0d}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{1f}',
    '\u{20}',
];

/// Whether Python's `[^a-z0-9\s]` keeps `c`.
fn survives_the_alnum_filter(c: char) -> bool {
    c.is_ascii_lowercase() || c.is_ascii_digit() || PYTHON_ASCII_WHITESPACE.contains(&c)
}

/// Python's `str.isspace()` set: Unicode's `White_Space` plus the four ASCII
/// separators `U+001C`–`U+001F`.
///
/// `char::is_whitespace` is Unicode `White_Space`, which Python's `str.isspace()`
/// exceeds by those four. The set is shared by a trim and a split, so this is
/// named for the predicate rather than for trimming: `str.strip()` and
/// `str.split()` both accept whatever this returns.
///
/// `slice::split` with this predicate does not coalesce runs of separators and
/// does not drop a leading or trailing one the way Python's `str.split()` and
/// Rust's `split_whitespace` do — it yields an empty string between two adjacent
/// separators. A caller splitting on it must drop the empties to keep Python's
/// behaviour.
pub(crate) fn is_python_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}

/// Reduce `value` to the alphabet the matcher compares on: lowercase ASCII
/// letters, digits and Python's ASCII whitespace.
///
/// The pipeline is NFKD first and an ASCII filter second, and the order is the
/// whole subtlety. Decomposition turns `é` into `e` plus a combining accent, so
/// the ASCII filter drops the accent and the letter survives; a character with
/// no ASCII decomposition is dropped whole, which is why `ø` and Hangul vanish
/// and `é` does not. Filtering non-ASCII before decomposing produces different
/// text for every input that decomposes, and the committed corpus is what
/// catches that mistake.
#[must_use]
pub fn normalize(value: &str) -> String {
    value
        .nfkd()
        .filter(char::is_ascii)
        .collect::<String>()
        .to_lowercase()
        .chars()
        .filter(|c| survives_the_alnum_filter(*c))
        .collect::<String>()
        // `str.strip()` strips whatever `str.isspace()` accepts, and Rust's
        // `trim()` strips Unicode's `White_Space`. Those disagree on
        // `0x1C`–`0x1F`: Python accepts them, Unicode does not, so `trim()`
        // would leave them where `strip()` removes them. Trimming against the
        // same set the filter keeps makes the two agree on every string this
        // function can produce.
        .trim_matches(|c| PYTHON_ASCII_WHITESPACE.contains(&c))
        .to_string()
}

/// Index in `b` of every occurrence of each byte, in increasing order.
///
/// `autojunk` is off here, matching the Python call site's `autojunk=False`. The
/// difflib default would drop any element occurring in more than 1% of a
/// sequence of 200 or more elements, which for normalized titles is the space
/// character: two long near-identical titles would collapse toward zero
/// instead of scoring close to one.
fn index_of_each_element(b: &[u8]) -> HashMap<u8, Vec<usize>> {
    let mut map: HashMap<u8, Vec<usize>> = HashMap::new();
    for (index, &element) in b.iter().enumerate() {
        map.entry(element).or_default().push(index);
    }
    map
}

/// The longest matching block of `a[alo..ahi]` against `b[blo..bhi]`, as
/// `(start in a, start in b, length)`.
///
/// Ties are broken by position: the earliest start in `a` wins, and among those
/// the earliest start in `b`. A range with nothing to match returns the range's
/// own corners with a length of zero.
fn find_longest_match(
    a: &[u8],
    b: &[u8],
    b2j: &HashMap<u8, Vec<usize>>,
    alo: usize,
    ahi: usize,
    blo: usize,
    bhi: usize,
) -> (usize, usize, usize) {
    let (mut best_i, mut best_j, mut best_size) = (alo, blo, 0);

    // `j2len[j]` is the length of the longest run of matches ending at `a[i-1]`
    // and `b[j]`, so a run ending at `j` is one longer than the run ending at
    // `j-1`. Only `j` values reachable from `b2j` are tracked, which is what
    // makes this linear in the number of candidate positions rather than in the
    // product of the two sequences.
    let mut j2len: HashMap<usize, usize> = HashMap::new();
    for (i, &element) in a.iter().enumerate().take(ahi).skip(alo) {
        let mut next_j2len: HashMap<usize, usize> = HashMap::new();
        let Some(candidates) = b2j.get(&element) else {
            // Every entry in `j2len` is only extended by a later `i`, so a
            // non-matching element starts a fresh row.
            j2len = next_j2len;
            continue;
        };
        for &j in candidates {
            if j < blo {
                continue;
            }
            if j >= bhi {
                // `b2j`'s indices are in increasing order.
                break;
            }
            let previous = j.checked_sub(1).and_then(|at| j2len.get(&at));
            let run = previous.copied().unwrap_or(0) + 1;
            next_j2len.insert(j, run);
            if run > best_size {
                (best_i, best_j, best_size) = (i + 1 - run, j + 1 - run, run);
            }
        }
        j2len = next_j2len;
    }

    // The loop above finds the longest match whose every element is in `b2j`.
    // Walk outward from it to pick up matches the loop cannot see, which is
    // exactly what difflib's "extend by non-junk elements" pass does. With no
    // junk predicate configured, difflib's two junk-sucking passes are inert,
    // so only these two run.
    while best_i > alo && best_j > blo && a[best_i - 1] == b[best_j - 1] {
        best_i -= 1;
        best_j -= 1;
        best_size += 1;
    }
    while best_i + best_size < ahi
        && best_j + best_size < bhi
        && a[best_i + best_size] == b[best_j + best_size]
    {
        best_size += 1;
    }

    (best_i, best_j, best_size)
}

/// The summed length of every matching block of `a` and `b`.
///
/// difflib separates the adjacent blocks it finds and then merges them back
/// together. Merging adds two lengths and drops a zero-length dummy, so the sum
/// is the same either way and the collapse is not reproduced here.
///
/// The recursion is an explicit stack rather than real recursion because difflib
/// makes the same choice: a pathological pair of sequences would otherwise
/// exhaust the call stack. Popping from the end visits the right-hand region
/// first, and the collected blocks are summed rather than used in order, so the
/// visit order cannot affect the result.
fn matched_length(a: &[u8], b: &[u8]) -> usize {
    let b2j = index_of_each_element(b);
    let mut queue = vec![(0, a.len(), 0, b.len())];
    let mut matched = 0;
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, k) = find_longest_match(a, b, &b2j, alo, ahi, blo, bhi);
        if k > 0 {
            matched += k;
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    matched
}

/// How alike `a` and `b` are, in `[0.0, 1.0]`.
///
/// A faithful port of `difflib.SequenceMatcher(None, a, b, autojunk=False)
/// .ratio()` over the normalized strings: `2.0 * matched / (len(a) + len(b))`,
/// where `matched` is the total length of the matching blocks.
#[must_use]
pub fn similarity(a: &str, b: &str) -> f64 {
    let (norm_a, norm_b) = (normalize(a), normalize(b));
    if norm_a.is_empty() || norm_b.is_empty() {
        return 0.0;
    }
    let (bytes_a, bytes_b) = (norm_a.as_bytes(), norm_b.as_bytes());
    let matched = matched_length(bytes_a, bytes_b);
    // difflib's `_calculate_ratio` writes `2.0 * matches / length` with both
    // operands as Python integers, promoted to double before the divide. The
    // same expression here keeps the two implementations bit-identical, which
    // is what lets the corpus compare exactly rather than by an epsilon.
    2.0 * matched as f64 / (bytes_a.len() + bytes_b.len()) as f64
}
