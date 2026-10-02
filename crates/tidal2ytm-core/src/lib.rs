//! Domain logic for tidal2ytm.
//!
//! This crate is deliberately free of `tauri` and of all I/O so that
//! `cargo test` runs on a bare Linux runner. Anything that needs a process, a
//! network, or a window belongs in `tidal2ytm-ipc` or the `tidal2ytm` host.

pub mod video_id;
