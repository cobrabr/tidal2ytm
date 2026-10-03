//! Where the host's log records go until Phase 4 gives them a file.
//!
//! A facade with no sink behind it is the quietest kind of dead code: `log::debug!`
//! is a no-op call, the build is clean, and the sidecar's forwarded stderr — the
//! only place its diagnostics exist — is discarded exactly as thoroughly as
//! dropping the receiver would discard it. So debug builds install a sink here.
//!
//! Release builds do not, because `main.rs` gives them no console to write to;
//! the records are dropped rather than silently written somewhere nobody will
//! look. Phase 4 replaces this module with the per-run log file, and the call
//! sites stay as they are.

use std::io::Write;

/// Install the stderr sink, once, for a debug build.
///
/// Called from `run()` rather than from `main.rs`: `run()` is what starts the
/// sidecar whose stderr is drained into this facade, so an entry point that
/// forgot the call would reproduce exactly the silent no-op the module docs warn
/// about — a clean build, and a discarded diagnostic.
pub fn install_debug_logger() {
    if !cfg!(debug_assertions) {
        return;
    }
    // Level and logger are two calls because the `log` facade keeps them apart,
    // and pairing them with `.map()` means a failed `set_logger` skips the level
    // entirely. `MAX_LOG_LEVEL_FILTER` starts at `Off`, so a second call from
    // anywhere else in the process would leave DEBUG switched off with nothing
    // said: every forwarded sidecar line dropped, and no error to trace it to.
    match log::set_logger(&STDERR) {
        Ok(()) => log::set_max_level(log::LevelFilter::Debug),
        // Someone got here first. Say so rather than leaving the level alone:
        // their logger is the one that will run, and it deserves to know why the
        // forwarded diagnostics are not appearing.
        Err(_) => log::warn!("a logger is already installed; not replacing it"),
    }
}

/// Everything at DEBUG, tagged with its target so a line can be traced back to
/// the process that wrote it.
static STDERR: Stderr = Stderr;

struct Stderr;

impl log::Log for Stderr {
    fn enabled(&self, _metadata: &log::Metadata<'_>) -> bool {
        true
    }

    fn log(&self, record: &log::Record<'_>) {
        let mut out = std::io::stderr().lock();
        let _ = writeln!(
            out,
            "{} {} {}",
            record.level(),
            record.target(),
            record.args()
        );
    }

    fn flush(&self) {
        let _ = std::io::stderr().lock().flush();
    }
}
