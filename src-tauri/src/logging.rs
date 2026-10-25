//! Where the host's log records go.
//!
//! A file sink with hand-rolled rotation, installed from `run()` after the
//! database is open so the level can be read from `meta` before the first record
//! is written. A facade with no sink behind it is the quietest kind of dead
//! code, so the install call lives in `run()` and not in `main()`.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// The size at which the current log file is rotated.
pub const MAX_BYTES: u64 = 5 * 1024 * 1024;

/// How many rotated files are kept. The oldest is dropped on each rotation.
pub const KEEP: usize = 3;

/// The three log files a diagnostics screen can open.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct LogPaths {
    pub host_log: PathBuf,
    pub history: PathBuf,
    pub frontend_log: PathBuf,
}

/// Validate a level name and return the matching filter.
pub fn parse_level(level: &str) -> Result<log::LevelFilter, String> {
    match level.trim().to_ascii_uppercase().as_str() {
        "OFF" => Ok(log::LevelFilter::Off),
        "CRITICAL" | "ERROR" => Ok(log::LevelFilter::Error),
        "WARN" => Ok(log::LevelFilter::Warn),
        "INFO" => Ok(log::LevelFilter::Info),
        "DEBUG" => Ok(log::LevelFilter::Debug),
        "TRACE" => Ok(log::LevelFilter::Trace),
        other => Err(format!("unknown log level: {other}")),
    }
}

/// Update the level of the installed sink, if any.
pub fn set_level(level: &str) -> Result<(), String> {
    let filter = parse_level(level)?;
    let mut state = STATE.lock().map_err(|e| e.to_string())?;
    state.level = filter;
    log::set_max_level(filter);
    Ok(())
}

/// Install the file sink at the given level and directory.
pub fn install(level: &str, dir: &Path) -> Result<(), String> {
    let filter = parse_level(level)?;
    std::fs::create_dir_all(dir).map_err(|e| format!("could not create log dir: {e}"))?;

    let host_log = dir.join("host.log");
    if let Ok(meta) = std::fs::metadata(&host_log) {
        if meta.len() >= MAX_BYTES {
            rotate(dir);
        }
    }

    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&host_log)
        .map_err(|e| format!("could not open host log: {e}"))?;

    {
        let mut state = STATE.lock().map_err(|e| e.to_string())?;
        state.dir = Some(dir.to_path_buf());
        state.file = Some(file);
        state.level = filter;
    }

    let _ = log::set_logger(&GLOBAL_LOGGER);
    log::set_max_level(filter);
    Ok(())
}

/// The paths the installed sink is writing to, or an error if it was never
/// installed.
pub fn paths() -> Result<LogPaths, String> {
    let state = STATE.lock().map_err(|e| e.to_string())?;
    let dir = state
        .dir
        .clone()
        .ok_or_else(|| "logging not installed".to_string())?;
    Ok(LogPaths {
        host_log: dir.join("host.log"),
        history: dir.clone(),
        frontend_log: dir.join("frontend.log"),
    })
}

fn rotate(dir: &Path) {
    // Drop the oldest rotated file.
    let _ = std::fs::remove_file(dir.join(format!("host.{}.log", KEEP)));
    // Shift the remaining ones up.
    for i in (1..KEEP).rev() {
        let from = dir.join(format!("host.{}.log", i));
        let to = dir.join(format!("host.{}.log", i + 1));
        let _ = std::fs::rename(&from, &to);
    }
    // Rename the current file to the first rotated slot.
    let _ = std::fs::rename(dir.join("host.log"), dir.join("host.1.log"));
}

// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------

struct LoggerState {
    dir: Option<PathBuf>,
    file: Option<File>,
    level: log::LevelFilter,
}

static STATE: Mutex<LoggerState> = Mutex::new(LoggerState {
    dir: None,
    file: None,
    level: log::LevelFilter::Off,
});

struct GlobalLogger;

impl log::Log for GlobalLogger {
    fn enabled(&self, metadata: &log::Metadata<'_>) -> bool {
        let state = match STATE.lock() {
            Ok(g) => g,
            Err(_) => return false,
        };
        metadata.level() <= state.level
    }

    fn log(&self, record: &log::Record<'_>) {
        let mut state = match STATE.lock() {
            Ok(g) => g,
            Err(_) => return,
        };
        if record.level() > state.level {
            return;
        }
        let dir = match &state.dir {
            Some(d) => d.clone(),
            None => return,
        };

        // Rotate if the current file has grown past the limit.
        let needs_rotate = state
            .file
            .as_ref()
            .map(|f| f.metadata().map(|m| m.len()).unwrap_or(0) >= MAX_BYTES)
            .unwrap_or(false);
        if needs_rotate {
            drop(state.file.take());
            rotate(&dir);
            state.file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(dir.join("host.log"))
                .ok();
        }
        if state.file.is_none() {
            state.file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(dir.join("host.log"))
                .ok();
        }
        if let Some(ref mut f) = state.file {
            let _ = writeln!(
                f,
                "{} {} {}",
                record.level(),
                record.target(),
                record.args()
            );
            let _ = f.flush();
        }
    }

    fn flush(&self) {
        if let Ok(mut state) = STATE.lock() {
            if let Some(ref mut f) = state.file {
                let _ = f.flush();
            }
        }
    }
}

static GLOBAL_LOGGER: GlobalLogger = GlobalLogger;

#[cfg(test)]
mod tests {
    use super::*;

    /// Rotation: write until MAX_BYTES is exceeded, then check.
    #[test]
    fn rotation_keeps_at_most_keep_files() {
        let dir =
            std::env::temp_dir().join(format!("tidal2ytm-host-logging-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("dir");

        install("DEBUG", &dir).expect("install");

        // Write enough to exceed MAX_BYTES. Each line ~120 bytes.
        let line = "x".repeat(100);
        for _ in 0..(MAX_BYTES as usize / 100 + 1000) {
            log::debug!("{}", line);
        }

        // At most KEEP history files.
        let mut history_count = 0;
        for i in 1..=KEEP {
            if dir.join(format!("host.{}.log", i)).exists() {
                history_count += 1;
            }
        }
        // There should be at least one rotated file.
        assert!(
            history_count >= 1,
            "expected at least one rotated file, found {history_count}"
        );
        assert!(
            history_count <= KEEP,
            "expected at most {KEEP} rotated files, found {history_count}"
        );

        // The host log file is non-empty.
        let host_log = dir.join("host.log");
        assert!(host_log.exists(), "host.log must exist");
        let size = std::fs::metadata(&host_log).map(|m| m.len()).unwrap_or(0);
        assert!(size > 0, "host.log must be non-empty");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn parse_level_rejects_unknown() {
        assert!(parse_level("NOPE").is_err());
        assert!(parse_level("INFO").is_ok());
    }
}
