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
pub fn install_debug_logger() {
    if !cfg!(debug_assertions) {
        return;
    }
    // A logger that cannot be installed has nothing left to say: the record it
    // would have dropped is the whole reason it exists. It is also not
    // something the person running the application can act on.
    let _ = log::set_logger(&STDERR).map(|()| log::set_max_level(log::LevelFilter::Debug));
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
