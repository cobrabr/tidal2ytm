//! Which Python interpreter can import the sidecar.
//!
//! One owner. The host needs it for a development window, the round-trip test
//! needs it to reach the real sidecar, and the client's own tests need an
//! interpreter to run a stub with. Two of those already had their own copy of
//! the rule before this module existed, and a copy is worse than no rule at
//! all: it answers "which interpreter" differently depending on which copy ran,
//! so an import failure reproduces in one suite and not the other.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// Environment variable that replaces the search entirely.
pub const OVERRIDE_VAR: &str = "TIDAL2YTM_SIDECAR_PYTHON";

/// The interpreter to run the sidecar with, given the repository root.
///
/// `TIDAL2YTM_SIDECAR_PYTHON` wins. Otherwise the uv venv is preferred, because
/// `uv sync --all-groups` builds the only interpreter that carries the sidecar's
/// dependencies and it is *not* on `PATH`: `python`/`python3` is the system or
/// per-user install, which has neither `tidal2ytm_sidecar` nor `tidalapi`/
/// `ytmusicapi`, and the sidecar dies on its first import. The bare name stays
/// as the fallback so a checkout with no `.venv` still has something to try.
#[must_use]
pub fn sidecar_python(root: &Path) -> PathBuf {
    search(root, std::env::var_os(OVERRIDE_VAR))
}

/// `sidecar_python` with the override passed in, so the rule can be exercised
/// without mutating the process environment — which is shared with every other
/// test in this crate, and with the round-trip test in a separate process.
fn search(root: &Path, explicit: Option<OsString>) -> PathBuf {
    if let Some(explicit) = explicit {
        return PathBuf::from(explicit);
    }
    let venv = venv_interpreter(root);
    if venv.is_file() {
        return venv;
    }
    PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
}

/// The interpreter inside `root`'s uv venv.
fn venv_interpreter(root: &Path) -> PathBuf {
    let venv = root.join(".venv");
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A directory that does not exist, so `venv_interpreter` reports false for
    /// it whatever the machine does.
    fn absent_root() -> PathBuf {
        std::env::temp_dir().join("tidal2ytm-no-such-checkout")
    }

    // The venv preference is the whole point of this module. Without it the
    // resolver falls back to `python` from PATH, which on a developer's machine
    // is a system install with no `tidalapi` in it, and the failure surfaces as
    // an ImportError that names neither the venv nor the missing dependency.
    #[test]
    fn a_venv_interpreter_is_preferred_over_the_bare_name() {
        let root = std::env::temp_dir().join("tidal2ytm-interpreter-with-venv");
        let venv = venv_interpreter(&root);
        let _ = std::fs::create_dir_all(venv.parent().expect("the venv has a parent"));
        let _ = std::fs::write(&venv, b"");
        let found = search(&root, None);
        let _ = std::fs::remove_dir_all(&root);
        assert_eq!(found, venv, "the interpreter under .venv should win");
    }

    // The bare name is all a checkout with no `.venv` has. A resolver that
    // returned the `.venv` path whether or not it existed would turn "run
    // `uv sync`" into a "the system cannot find the file" with no hint why.
    #[test]
    fn a_checkout_without_a_venv_falls_back_to_the_bare_name() {
        let expected = PathBuf::from(if cfg!(windows) { "python" } else { "python3" });
        assert_eq!(search(&absent_root(), None), expected);
    }

    // One setting, one name. The client's stub tests used to read a second
    // variable (`TIDAL2YTM_TEST_PYTHON`) that meant this, so pointing the
    // sidecar at an interpreter fixed the round-trip test and left the stub
    // tests still looking at the system Python.
    #[test]
    fn the_override_replaces_the_whole_search() {
        let chosen = PathBuf::from("a-python-of-my-own");
        assert_eq!(
            search(&absent_root(), Some(chosen.clone().into_os_string())),
            chosen
        );
    }
}
