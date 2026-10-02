//! Extract a YouTube video ID from a bare ID or any of the nine URL shapes a
//! user might paste.

/// Raised when the input matches none of the accepted forms.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("Cannot parse YouTube video ID from: {input}")]
pub struct VideoIdError {
    input: String,
}

fn is_video_id(candidate: &str) -> bool {
    candidate.len() == 11
        && candidate
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// Split `url` into a lowercased host and the path-and-query remainder, with
/// `www.` and `m.` prefixes stripped. The remainder keeps its leading `/` so
/// that callers can match absolute path prefixes against it.
fn split_url(url: &str) -> (String, &str) {
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    let (authority, path) = match rest.find('/') {
        Some(slash) => rest.split_at(slash),
        None => (rest, ""),
    };
    let lowered = authority.to_ascii_lowercase();
    let host = lowered
        .strip_prefix("www.")
        .or_else(|| lowered.strip_prefix("m."))
        .unwrap_or(&lowered)
        .to_string();
    (host, path)
}

/// Pull the `v` query parameter out of a path-and-query string.
fn query_v(path_and_query: &str) -> &str {
    path_and_query.split_once('?').map_or("", |(_, query)| {
        query
            .split('&')
            .find_map(|pair| pair.strip_prefix("v="))
            .unwrap_or("")
    })
}

/// Extract and validate an 11-character YouTube video ID.
///
/// Accepts a bare ID, or `/watch?v=ID`, `/v/ID`, `/embed/ID`, `/shorts/ID`,
/// and `/live/ID` on `youtube.com`, `music.youtube.com` and
/// `youtube-nocookie.com`, or a path ID on `youtu.be`. Host matching is
/// `www.`/`m.`-insensitive.
pub fn extract_video_id(raw: &str) -> Result<String, VideoIdError> {
    let trimmed = raw.trim();
    if is_video_id(trimmed) {
        return Ok(trimmed.to_string());
    }

    let (host, remainder) = split_url(trimmed);
    // A fragment is never part of the ID, and `urlparse` drops it before the
    // query is read, so drop it here too — otherwise it rides along on the
    // value of the last query parameter.
    let path_and_query = remainder.split('#').next().unwrap_or_default();
    let path = path_and_query.split('?').next().unwrap_or_default();
    let candidate = match host.as_str() {
        "youtube.com" | "music.youtube.com" | "youtube-nocookie.com" => {
            ["/embed/", "/shorts/", "/live/", "/v/"]
                .iter()
                .find_map(|prefix| path.strip_prefix(prefix))
                .map_or_else(
                    || query_v(path_and_query),
                    |tail| tail.split('/').next().unwrap_or_default(),
                )
        }
        "youtu.be" => path
            .trim_start_matches('/')
            .split('/')
            .next()
            .unwrap_or_default(),
        _ => {
            return Err(VideoIdError {
                input: raw.to_string(),
            });
        }
    };

    if is_video_id(candidate) {
        Ok(candidate.to_string())
    } else {
        Err(VideoIdError {
            input: raw.to_string(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_every_documented_form() {
        let cases = [
            ("CCCCCCCCCCC", "CCCCCCCCCCC"),
            ("https://www.youtube.com/watch?v=CCCCCCCCCCC", "CCCCCCCCCCC"),
            ("https://youtu.be/CCCCCCCCCCC?t=10", "CCCCCCCCCCC"),
            ("https://www.youtube.com/v/CCCCCCCCCCC?foo=1", "CCCCCCCCCCC"),
            (
                "https://music.youtube.com/watch?v=CCCCCCCCCCC&list=PL",
                "CCCCCCCCCCC",
            ),
            ("https://m.youtube.com/watch?v=AAAAAAAAAAA", "AAAAAAAAAAA"),
            ("https://www.youtube.com/embed/AAAAAAAAAAA", "AAAAAAAAAAA"),
            ("https://www.youtube.com/shorts/AAAAAAAAAAA", "AAAAAAAAAAA"),
            ("https://www.youtube.com/live/AAAAAAAAAAA", "AAAAAAAAAAA"),
            (
                "https://www.youtube-nocookie.com/embed/AAAAAAAAAAA",
                "AAAAAAAAAAA",
            ),
        ];
        for (input, expected) in cases {
            assert_eq!(
                extract_video_id(input).as_deref(),
                Ok(expected),
                "input: {input}"
            );
        }
    }

    #[test]
    fn strips_surrounding_whitespace() {
        assert_eq!(
            extract_video_id("  CCCCCCCCCCC \n").as_deref(),
            Ok("CCCCCCCCCCC")
        );
    }

    #[test]
    fn discards_a_fragment() {
        assert_eq!(
            extract_video_id("https://www.youtube.com/watch?v=CCCCCCCCCCC#t=30").as_deref(),
            Ok("CCCCCCCCCCC")
        );
        assert_eq!(
            extract_video_id("https://www.youtube.com/embed/AAAAAAAAAAA#t=5").as_deref(),
            Ok("AAAAAAAAAAA")
        );
    }

    #[test]
    fn rejects_a_non_youtube_url() {
        assert!(extract_video_id("https://example.com/watch?v=CCCCCCCCCCC").is_err());
    }

    #[test]
    fn rejects_a_wrong_length_id() {
        assert!(extract_video_id("CCCCCCCCCC").is_err());
    }

    #[test]
    fn rejects_an_id_with_illegal_characters() {
        assert!(extract_video_id("CCCC!CCCCCC").is_err());
    }

    #[test]
    fn error_names_the_offending_input() {
        let err = extract_video_id("nonsense").unwrap_err();
        assert_eq!(
            err.to_string(),
            "Cannot parse YouTube video ID from: nonsense"
        );
    }
}
