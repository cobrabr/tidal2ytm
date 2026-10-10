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

use std::path::Path;
use std::time::Duration;

use tauri::Manager;
use tidal2ytm_ipc::{SidecarConfig, log_level_env};
use tidal2ytm_store::Store;

use state::AppState;

/// How long the sidecar has to handshake before the host calls it a failure.
/// The handshake is what turns a stale or broken sidecar into a startup error
/// instead of a window that opens and then quietly fails every request, so this
/// is not something to shorten or skip.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(20);

/// The database's file name inside the directory the sidecar runs in.
const DATABASE_FILE: &str = "tidal2ytm.db";

/// Open the database and bring its schema up to date, or say where it is.
///
/// Both halves name the file, because a bare "the database could not be opened"
/// sends a reader looking through the source tree for something that is not
/// there. The schema-version refusal in particular already carries both version
/// numbers in its own message, and replacing that with anything vaguer would
/// throw away the one fact that says which build wrote the file.
async fn open_database(cwd: &Path) -> Result<Store, String> {
    let path = cwd.join(DATABASE_FILE);
    let store = Store::open(&path)
        .await
        .map_err(|error| format!("could not open the database at {}: {error}", path.display()))?;
    store.migrate().await.map_err(|error| {
        format!(
            "could not migrate the database at {}: {error}",
            path.display()
        )
    })?;
    Ok(store)
}

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
            // After `create_dir_all`, and from the same `cwd`: the database goes
            // beside the sidecar's working directory rather than wherever a
            // second `app_data_dir()` call would have put it, so the two are
            // provably one directory and a token cache written by the sidecar
            // and a row written by the host cannot drift apart. The directory
            // has to exist first for the sidecar's benefit, and the database
            // lands in it for the same reason.
            //
            // Here and not in `main()` because `main()` alone is an entry point
            // that can lose everything the process is complaining about: the
            // failure this reports is the reason the log file exists, and a
            // start-up error raised before the sink is installed is exactly the
            // one nobody can read. That is the same argument that puts
            // `install_debug_logger` here rather than in `main()`.
            let store = tauri::async_runtime::block_on(open_database(&cwd))
                .inspect_err(|error| log::error!("tidal2ytm: {error}"))?;
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
            let state = tauri::async_runtime::block_on(AppState::start(config, store))
                .inspect_err(|error| log::error!("tidal2ytm: {error}"))?;
            app.manage(state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_auth_status,
            commands::get_status_counts,
            commands::get_schema_version
        ])
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
