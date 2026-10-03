//! The Tauri host: it owns the sidecar's lifetime and hands the frontend
//! commands and nothing else.
//!
//! The frontend holds no credentials and spawns no processes. `capabilities/`
//! grants it `core:default` and no plugin permissions, so there is no permission
//! it could use to start anything even if it wanted to.

pub mod commands;
pub mod logging;
pub mod sidecar_path;
pub mod state;

use std::time::Duration;

use tauri::Manager;
use tidal2ytm_ipc::{SidecarConfig, log_level_env};

use state::AppState;

/// How long the sidecar has to handshake before the host calls it a failure.
/// The handshake is what turns a stale or broken sidecar into a startup error
/// instead of a window that opens and then quietly fails every request, so this
/// is not something to shorten or skip.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(20);

/// Start the sidecar, supervise it, and expose the commands.
pub fn run() {
    // Before anything can emit. `AppState::start` drains the sidecar's stderr
    // into the `log` facade, and a facade with no sink is a no-op call, so a
    // record from the process most likely to be complaining arrives nowhere.
    logging::install_debug_logger();

    let app = tauri::Builder::default()
        .setup(|app| {
            let launch = sidecar_path::resolve(app.handle())?;
            // Phase 4 owns the log level control and the host's log file, so
            // Phase 2 resolves the default and hands the value straight
            // through — which is what makes the level apply even if the sidecar
            // dies before its handshake.
            let (_, level) = log_level_env("INFO")?;
            let cwd = launch.cwd.clone().unwrap_or(app.path().app_data_dir()?);
            // The sidecar's working directory is handed to the child as
            // `current_dir`, and a working directory that does not exist fails
            // the spawn with `os error 267` — "the directory name is invalid",
            // which names neither the path nor the reason. On a first run there
            // is no application data directory at all, so this is the only thing
            // standing between a new machine and a sidecar that cannot start. It
            // is also where the token caches are meant to live, so the directory
            // is wanted whether or not anything has written to it yet.
            std::fs::create_dir_all(&cwd).map_err(|e| {
                format!(
                    "could not create {} for the sidecar to run in: {e}",
                    cwd.display()
                )
            })?;
            let config = SidecarConfig {
                program: launch.program.clone(),
                args: launch.args.clone(),
                cwd,
                env: [
                    launch.env.clone(),
                    vec![("TIDAL2YTM_LOG_LEVEL".to_string(), level)],
                ]
                .concat(),
                handshake_timeout: STARTUP_TIMEOUT,
            };
            let state = tauri::async_runtime::block_on(AppState::start(config))
                .inspect_err(|error| log::error!("tidal2ytm: {error}"))?;
            app.manage(state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![commands::get_auth_status])
        .build(tauri::generate_context!())
        .expect("the Tauri application builds");

    // Shutdown belongs on `RunEvent::Exit`, not on `WindowEvent::Destroyed`:
    // the event loop is ending, so blocking here costs nothing, whereas
    // blocking inside a window callback stalls the loop that is still
    // delivering events.
    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit)
            && let Some(state) = handle.try_state::<AppState>()
        {
            let client = state.client();
            tauri::async_runtime::block_on(async move {
                let client = client.await;
                if let Err(error) = client.shutdown().await {
                    log::warn!("the sidecar did not shut down cleanly: {error}");
                }
            });
        }
    });
}
