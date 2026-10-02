//! The sidecar's NDJSON protocol and the client that speaks it.
//!
//! Like `tidal2ytm-core`, this crate must not depend on `tauri`: the
//! round-trip test in `tests/round_trip.rs` spawns a real Python process, and
//! that test is what proves the boundary works on a headless Linux runner.

pub mod protocol;

pub use protocol::{ErrorKind, IpcError};
