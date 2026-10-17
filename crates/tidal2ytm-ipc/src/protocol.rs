//! Wire types for the sidecar's NDJSON protocol.
//!
//! `contracts/*.json` holds a canonical example of every envelope. The Python
//! and Rust suites both assert against those files, so a rename on one side
//! fails the other suite's tests instead of production.

use std::fmt;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Bumped whenever a wire shape changes incompatibly. The host refuses to
/// start against a sidecar reporting anything else, so a stale packaged
/// sidecar produces a clear startup error instead of a failing job.
pub const PROTOCOL_VERSION: u32 = 1;

/// Capabilities the host cannot run without. A sidecar missing any of these
/// is refused at startup rather than failing later inside a job.
pub const REQUIRED_CAPABILITIES: &[&str] = &["auth.status", "auth.begin"];

/// The closed set of failure categories the sidecar reports.
///
/// A `kind` this build does not recognise fails to deserialise and surfaces as
/// `IpcError::MalformedResponse`. That is deliberate: guessing that an unknown
/// kind is `Upstream` would make a newer sidecar's `quota_exceeded` look like
/// a generic failure and lose the distinction the host branches on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    AuthExpired,
    RateLimited,
    NotFound,
    Upstream,
    BadRequest,
    SidecarLost,
}

impl ErrorKind {
    /// The name that appears on the wire.
    #[must_use]
    pub fn as_wire(self) -> &'static str {
        match self {
            Self::AuthExpired => "auth_expired",
            Self::RateLimited => "rate_limited",
            Self::NotFound => "not_found",
            Self::Upstream => "upstream",
            Self::BadRequest => "bad_request",
            Self::SidecarLost => "sidecar_lost",
        }
    }
}

impl fmt::Display for ErrorKind {
    /// Render the wire name, so an error surfaced to the user quotes exactly
    /// what the sidecar sent rather than a Rust variant spelling.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_wire())
    }
}

/// A failed call, as reported by the sidecar.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireError {
    pub kind: ErrorKind,
    pub message: String,
}

/// One request, written as a single NDJSON line.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Request<'a> {
    pub id: u64,
    pub op: &'a str,
    pub params: Value,
}

/// One response envelope.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Response {
    pub id: u64,
    pub ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<WireError>,
}

/// An unsolicited message from the sidecar. Only the handshake exists today.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "event")]
pub enum ServerEvent {
    #[serde(rename = "hello")]
    Hello {
        protocol: u32,
        capabilities: Vec<String>,
    },
}

/// Reject a handshake the host cannot safely run against.
pub fn validate_hello(event: &ServerEvent) -> Result<(), IpcError> {
    let ServerEvent::Hello {
        protocol,
        capabilities,
    } = event;
    if *protocol != PROTOCOL_VERSION {
        return Err(IpcError::ProtocolMismatch {
            expected: PROTOCOL_VERSION,
            got: *protocol,
        });
    }
    for name in REQUIRED_CAPABILITIES {
        if !capabilities.iter().any(|c| c == name) {
            return Err(IpcError::MissingCapability {
                name: (*name).to_string(),
            });
        }
    }
    Ok(())
}

/// Parse a stdout line as a server event, or `None` if it is not one.
///
/// Returning `None` rather than an error is the whole point: stdout also
/// carries response envelopes, and may carry a stray print or a library
/// banner. The reader steps over anything it cannot use.
#[must_use]
pub fn parse_server_event(line: &str) -> Option<ServerEvent> {
    serde_json::from_str::<ServerEvent>(line.trim()).ok()
}

/// Every way a sidecar interaction can fail.
#[derive(Debug, Clone, PartialEq, thiserror::Error)]
pub enum IpcError {
    #[error("sidecar reports protocol version {got}, host requires {expected}")]
    ProtocolMismatch { expected: u32, got: u32 },
    #[error("sidecar does not implement required capability `{name}`")]
    MissingCapability { name: String },
    #[error("sidecar exited or closed its output stream")]
    SidecarLost,
    #[error("sidecar call `{op}` timed out after {ms} ms")]
    Timeout { op: String, ms: u64 },
    #[error("could not start the sidecar: {message}")]
    Spawn { message: String },
    #[error("sidecar reported `{kind}` for `{op}`: {message}")]
    Sidecar {
        op: String,
        kind: ErrorKind,
        message: String,
    },
    #[error("sidecar sent a line that is not a valid response envelope: {line}")]
    MalformedResponse { line: String },
}

#[cfg(test)]
mod tests {
    use super::*;
    use tidal2ytm_core::candidate::{AlbumDetail, AlbumSummary, Candidate};

    fn fixture(name: &str) -> String {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../contracts")
            .join(name);
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
    }

    #[test]
    fn parses_the_hello_contract() {
        let event = parse_server_event(&fixture("hello.json")).expect("hello is a server event");
        validate_hello(&event).expect("the contract handshake is valid");
    }

    #[test]
    fn parses_the_success_contract() {
        let response: Response =
            serde_json::from_str(&fixture("auth_status.json")).expect("success envelope parses");
        assert!(response.ok);
        assert_eq!(response.id, 1);
        assert!(response.error.is_none());
        let data = response.data.expect("ok responses carry data");
        assert_eq!(data["tidal"]["status"], serde_json::json!("signed_out"));
        assert!(data["tidal"]["expires_at"].is_null());
    }

    /// The `data` of a success contract, read rather than restated: a fourth
    /// copy of a shape would fail on a legitimate change with a diff pointing at
    /// code that did not change.
    fn contract_data(name: &str) -> Value {
        let response: Response =
            serde_json::from_str(&fixture(name)).unwrap_or_else(|e| panic!("{name} parses: {e}"));
        assert!(response.ok, "{name} is a success envelope");
        response
            .data
            .unwrap_or_else(|| panic!("{name} carries data"))
    }

    /// Parse `name`'s `data` as `T` and write it back out.
    ///
    /// The round trip is the assertion. Deserialising alone would pass for a
    /// type that silently drops a field the contract writes — `serde` ignores
    /// what it has no member for, and ignores what it has no member for on the
    /// Python side too — so a renamed or deleted field would reach production
    /// with both suites green. Writing the value back has to reproduce the
    /// contract byte for byte.
    fn assert_contract_round_trips<T>(name: &str) -> T
    where
        T: serde::de::DeserializeOwned + Serialize,
    {
        let data = contract_data(name);
        let typed: T = serde_json::from_value(data.clone())
            .unwrap_or_else(|e| panic!("{name} deserialises into the Rust type: {e}"));
        let written = serde_json::to_value(&typed).expect("the type serialises");
        assert_eq!(
            written, data,
            "{name} and the Rust type disagree on the key set"
        );
        typed
    }

    #[test]
    fn the_candidate_contract_round_trips_through_the_rust_type() {
        let candidate: Candidate = assert_contract_round_trips("candidate.json");
        // The contract's whole point is that these are the domain's names, so
        // spot-check that the values landed rather than arriving as defaults.
        assert_eq!(candidate.video_id.len(), 11, "a video id is 11 characters");
        assert_eq!(candidate.artists.len(), 2, "the contract lists two artists");
        assert_eq!(candidate.album_track_num, Some(4));
        assert_eq!(candidate.duration_sec, Some(217));
    }

    #[test]
    fn the_album_summary_contract_round_trips_through_the_rust_type() {
        let summary: AlbumSummary = assert_contract_round_trips("album_summary.json");
        assert_eq!(summary.album_id, "MPREb_00000042");
        assert_eq!(summary.year, Some(2014));
    }

    // One track and one sibling, so the sibling shape is exercised rather than
    // merely declared: an `other_versions` that is always empty would satisfy
    // any assertion that only reads `summary` and `tracks`.
    #[test]
    fn the_album_detail_contract_round_trips_through_the_rust_type() {
        let detail: AlbumDetail = assert_contract_round_trips("album_detail.json");
        assert_eq!(detail.tracks.len(), 1);
        assert_eq!(detail.other_versions.len(), 1);
        assert_eq!(
            detail.other_versions[0].title, "Lantern Season (Deluxe)",
            "the sibling is a summary, not a bare id"
        );
    }

    // A YTM hit that leaves an optional field out has to arrive as `None`, and
    // the way to guarantee that is to not default it: a `#[serde(default)]` here
    // would let a renamed field deserialise into a plausible-looking value and
    // score on it. No contract file carries a null, so this is the only place
    // that says so. `auth_status.json` records its signed-out sample for the
    // same reason.
    #[test]
    fn an_absent_optional_is_none_and_a_missing_required_field_is_refused() {
        let no_optionals = serde_json::json!({
            "video_id": "kQ7bZ3p1XtA",
            "title": "Paper Tide",
            "artists": [],
        });
        let candidate: Candidate = serde_json::from_value(no_optionals)
            .expect("a candidate with no optional fields parses");
        assert_eq!(candidate.album, None);
        assert_eq!(candidate.album_track_num, None);
        assert_eq!(candidate.duration_sec, None);

        // The other half, and the reason for the first: a missing `title` is a
        // payload this build does not understand, and it has to say so rather
        // than hand back a Candidate that scores on an empty string.
        let no_title = serde_json::json!({ "video_id": "kQ7bZ3p1XtA" });
        assert!(
            serde_json::from_value::<Candidate>(no_title).is_err(),
            "a hit with no title must not deserialise"
        );
    }

    #[test]
    fn parses_the_error_contract() {
        let response: Response = serde_json::from_str(&fixture("error_auth_expired.json"))
            .expect("error envelope parses");
        assert!(!response.ok);
        let error = response.error.expect("failed responses carry an error");
        assert_eq!(error.kind, ErrorKind::AuthExpired);
        assert!(!error.message.is_empty());
    }

    #[test]
    fn serialises_a_request_as_one_line() {
        let line = serde_json::to_string(&Request {
            id: 1,
            op: "auth.status",
            params: serde_json::json!({}),
        })
        .expect("request serialises");
        assert!(!line.contains('\n'));
        assert_eq!(line, r#"{"id":1,"op":"auth.status","params":{}}"#);
    }

    // Review Focus #3: a stray print, a library banner, or a SyntaxWarning on
    // some Python builds must not kill the session. The reader has to be able
    // to step over a line it cannot use and keep going.
    #[test]
    fn a_non_json_line_is_not_a_server_event() {
        assert!(parse_server_event("not json at all").is_none());
    }

    #[test]
    fn a_response_line_is_not_a_server_event() {
        assert!(parse_server_event(&fixture("auth_status.json")).is_none());
    }

    #[test]
    fn a_protocol_mismatch_is_refused() {
        let event = ServerEvent::Hello {
            protocol: 2,
            capabilities: vec!["auth.status".to_string()],
        };
        assert!(matches!(
            validate_hello(&event),
            Err(IpcError::ProtocolMismatch {
                expected: 1,
                got: 2
            })
        ));
    }

    #[test]
    fn a_missing_required_capability_is_refused() {
        let event = ServerEvent::Hello {
            protocol: PROTOCOL_VERSION,
            capabilities: vec!["favorites.list".to_string()],
        };
        assert!(matches!(
            validate_hello(&event),
            Err(IpcError::MissingCapability { ref name }) if name == "auth.status"
        ));
    }

    // Built from `REQUIRED_CAPABILITIES` rather than from its own list of them:
    // restating the required set here would let a capability added to one of the
    // two be missing from this test's answer and leave it green while it stopped
    // passing a handshake the real sidecar sends.
    #[test]
    fn an_unexpected_capability_does_not_block_startup() {
        let mut capabilities: Vec<String> = REQUIRED_CAPABILITIES
            .iter()
            .map(|name| (*name).to_string())
            .collect();
        capabilities.push("library.add".to_string());
        let event = ServerEvent::Hello {
            protocol: PROTOCOL_VERSION,
            capabilities,
        };
        validate_hello(&event).expect("extra capabilities are fine");
    }

    #[test]
    fn error_kinds_round_trip_through_their_wire_names() {
        let all = [
            ErrorKind::AuthExpired,
            ErrorKind::RateLimited,
            ErrorKind::NotFound,
            ErrorKind::Upstream,
            ErrorKind::BadRequest,
            ErrorKind::SidecarLost,
        ];
        for kind in all {
            let encoded = serde_json::to_value(kind).expect("kind serialises");
            assert_eq!(encoded, serde_json::json!(kind.as_wire()));
            assert_eq!(
                serde_json::from_value::<ErrorKind>(encoded).expect("kind parses"),
                kind
            );
        }
    }
}
