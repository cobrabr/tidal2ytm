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
pub const REQUIRED_CAPABILITIES: &[&str] = &["auth.status"];

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
        assert_eq!(data["tidal"]["valid"], serde_json::json!(false));
        assert!(data["tidal"]["expires_at"].is_null());
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

    #[test]
    fn an_unexpected_capability_does_not_block_startup() {
        let event = ServerEvent::Hello {
            protocol: PROTOCOL_VERSION,
            capabilities: vec!["auth.status".to_string(), "library.add".to_string()],
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
