//! Working out how to start the sidecar.
//!
//! The order is deliberate:
//!
//! 1. `TIDAL2YTM_SIDECAR_BIN` — an explicit override, and the escape hatch if
//!    the bundled name ever differs from what is expected here.
//! 2. A single-file `tidal2ytm-sidecar` beside the running executable. That is
//!    both where a bundled app keeps its resources and where `tauri_build`
//!    copies an `externalBin` for `cargo run`, which is why no target triple
//!    appears anywhere below: `tauri_build` strips the triple from the name it
//!    copies, so a `binaries/tidal2ytm-sidecar-<triple>.exe` candidate could
//!    never exist next to the binary.
//! 3. The checkout's own interpreter, running the package from source.
//!
//! Step 3 is not restricted to debug builds. A release binary run from inside a
//! checkout should show that developer's real sign-in state, and a release binary
//! run anywhere else finds no checkout and falls through to the error — which
//! lists everywhere it looked.

use std::path::{Path, PathBuf};

use tauri::Manager;
use tidal2ytm_ipc::sidecar_python;

/// Environment variable that replaces the search entirely.
pub const OVERRIDE_VAR: &str = "TIDAL2YTM_SIDECAR_BIN";

/// The sidecar's file name, before the platform's executable suffix.
const SIDECAR_BASE: &str = "tidal2ytm-sidecar";

/// How to start the sidecar.
#[derive(Debug)]
pub struct Launch {
    pub program: PathBuf,
    /// Empty for a packaged executable: the sidecar reads `stdin` the moment it
    /// starts and has no argument parser, so a flag here would be read by
    /// nobody.
    pub args: Vec<String>,
    /// What the child needs beyond the log level to find the sidecar. Empty for
    /// a packaged executable, `PYTHONPATH` for a checkout.
    pub env: Vec<(String, String)>,
    /// Where to run it. `None` leaves it in the host's own working directory,
    /// which for a real app is the application data directory.
    pub cwd: Option<PathBuf>,
}

/// Every place the sidecar might be, in the order it is tried.
///
/// Returned rather than picked so a failure message can list what was checked
/// instead of only saying "not found".
pub fn candidates(app: &tauri::AppHandle) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();

    if let Some(explicit) = std::env::var_os(OVERRIDE_VAR) {
        out.push(PathBuf::from(explicit));
    }
    if let Ok(resource_dir) = app.path().resource_dir() {
        out.push(executable_in(&resource_dir));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            out.push(executable_in(dir));
        }
    }

    out
}

/// The command to run the sidecar with, or why there is none.
pub fn resolve(app: &tauri::AppHandle) -> Result<Launch, String> {
    let tried = candidates(app);

    if let Some(binary) = tried.iter().find(|path| path.is_file()) {
        return Ok(Launch {
            program: binary.clone(),
            args: Vec::new(),
            env: Vec::new(),
            cwd: None,
        });
    }

    if let Some(root) = checkout_root() {
        return checkout_launch(&root);
    }

    Err(format!(
        "could not find the {SIDECAR_BASE} binary. Looked in: {}. \
         Set {OVERRIDE_VAR} to point at one.",
        display_all(&tried)
    ))
}

/// Start the sidecar from a checkout: the interpreter `uv sync` built, running
/// the package out of the repository root.
///
/// The working directory is the root rather than `sidecar/` because
/// `tidal2ytm.paths` resolves its relative `data` directory against the current
/// directory, and the root is where the CLI's token caches live. That is what
/// makes a development window report the developer's real sign-in state instead
/// of a hard-coded "signed out", and it puts the sidecar's own relative paths
/// in the same place the CLI puts them.
pub fn checkout_launch(root: &Path) -> Result<Launch, String> {
    if !package_marker(root).is_file() {
        return Err(format!("{} is not a tidal2ytm checkout", root.display()));
    }
    let package = root.join("sidecar");
    Ok(Launch {
        program: sidecar_python(root),
        args: vec!["-m".to_string(), "tidal2ytm_sidecar".to_string()],
        env: vec![(
            "PYTHONPATH".to_string(),
            package.to_string_lossy().into_owned(),
        )],
        cwd: Some(root.to_path_buf()),
    })
}

/// The checkout the running binary belongs to, if it is still on disk.
///
/// `tauri dev` runs from `target/<profile>`, but the depth is searched rather
/// than assumed, and the marker is a file inside the package rather than the
/// directory: a stray empty `sidecar/` is not a checkout, and an installed
/// binary three levels from nowhere must report "no checkout" rather than a
/// path that does not exist.
fn checkout_root() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    exe.ancestors()
        .skip(1)
        .find(|dir| package_marker(dir).is_file())
        .map(Path::to_path_buf)
}

/// The file that says a directory is the root of a tidal2ytm checkout.
fn package_marker(root: &Path) -> PathBuf {
    root.join("sidecar")
        .join("tidal2ytm_sidecar")
        .join("__init__.py")
}

/// The sidecar executable in `dir`.
fn executable_in(dir: &Path) -> PathBuf {
    dir.join(format!("{SIDECAR_BASE}{}", std::env::consts::EXE_SUFFIX))
}

fn display_all(paths: &[PathBuf]) -> String {
    paths
        .iter()
        .map(|path| path.display().to_string())
        .collect::<Vec<_>>()
        .join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The interpreter fallback's whole content, and the only part of this
    /// module that can be exercised without a live `AppHandle`.
    #[test]
    fn a_checkout_runs_the_package_from_the_repository_root() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(1)
            .expect("the host crate lives one level below the workspace root");
        let launch = checkout_launch(root).expect("this is a checkout");
        assert_eq!(
            launch.args,
            vec!["-m".to_string(), "tidal2ytm_sidecar".to_string()],
            "the sidecar has no console entry point; it is a module"
        );
        assert_eq!(launch.cwd.as_deref(), Some(root));
        assert_eq!(
            launch.env,
            vec![(
                "PYTHONPATH".to_string(),
                root.join("sidecar").to_string_lossy().into_owned()
            )],
            "`-m` needs the package root on the path"
        );
    }

    // Without this, `checkout_root` would hand `checkout_launch` a directory it
    // never looked at, the resolution error would be replaced by "not a
    // checkout" pointing at an unrelated path, and the message would name
    // something the user cannot act on.
    #[test]
    fn a_directory_that_is_not_a_checkout_is_refused_by_name() {
        let root = std::env::temp_dir().join("tidal2ytm-not-a-checkout");
        let error = checkout_launch(&root).expect_err("there is no sidecar package here");
        assert!(
            error.contains("not a tidal2ytm checkout"),
            "the error should say what is wrong: {error}"
        );
    }

    // The name the sidecar is looked for under is the whole of tier two, and a
    // suffix-less name is a candidate that can never exist on Windows or macOS.
    #[test]
    fn the_candidate_name_carries_the_platform_executable_suffix() {
        let found = executable_in(Path::new("C:\\anywhere"));
        assert!(
            found
                .to_string_lossy()
                .contains(std::env::consts::EXE_SUFFIX),
            "got {found:?}"
        );
    }
}
