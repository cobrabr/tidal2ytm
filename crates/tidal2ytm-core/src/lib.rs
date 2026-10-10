//! Domain logic for tidal2ytm.
//!
//! This crate is deliberately free of `tauri` and of all I/O so that
//! `cargo test` runs on a bare Linux runner. Anything that needs a process, a
//! network, or a window belongs in `tidal2ytm-ipc` or the `tidal2ytm` host.

pub mod album_match;
pub mod candidate;
pub mod merge;
pub mod model;
pub mod search;
pub mod text;
pub mod track_scoring;
pub mod version_tokens;
pub mod video_id;
