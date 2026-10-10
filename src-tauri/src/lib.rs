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
            // and a row written by the host cannot drift apart.
            //
            // The ordering is for the sidecar's sake alone. `Store::open`
            // creates its own parent directory — it has to, for the same
            // first-run reason — so the database would land here either way;
            // what it would not do is wait for the sidecar's spawn, which fails
            // against a directory that does not exist.
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

#[cfg(test)]
mod tests {
    use super::*;
    use tidal2ytm_store::SCHEMA_VERSION;

    /// A directory of this process's own, empty and gone afterwards.
    ///
    /// Unique per run because a fixed name collides when the suite runs twice at
    /// once, and the second run then fails in `open` for a reason that has
    /// nothing to do with what it is testing. Matches what
    /// `tidal2ytm-store`'s own file-opening test does, so there is one
    /// convention for a scratch path rather than three.
    fn scratch_dir() -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "tidal2ytm-host-open-database-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("the scratch directory is creatable");
        dir
    }

    /// The host opens a migrated database, and names the file when it cannot.
    ///
    /// `open_database` is the only production logic in this file that is not
    /// `run()`'s wiring, and `run()` needs a real `tauri::App` that no test can
    /// build. This is the part that could regress underneath it, and the two
    /// halves are separate claims: a host that opened the file and stopped
    /// leaves a version of `0` here, and a host that migrated to a *different*
    /// version than the store knows also fails it. Removing the `migrate()`
    /// call, or pointing `DATABASE_FILE` somewhere the schema does not land,
    /// turns this red — which is the point, since nothing else in the crate
    /// covers either.
    #[tokio::test]
    async fn opening_the_database_leaves_it_migrated() {
        let dir = scratch_dir();
        let store = open_database(&dir).await.expect("open_database");

        assert_eq!(
            store.schema_version().await.expect("version"),
            SCHEMA_VERSION,
            "open_database returned a database it did not migrate"
        );
        assert!(
            dir.join(DATABASE_FILE).is_file(),
            "open_database did not write the file where DATABASE_FILE says"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A failure names the file, so the reader is sent to the database rather
    /// than to the source tree.
    ///
    /// Asserted on the path appearing in the message rather than on the wording
    /// around it: a reader who knows only "the database could not be opened"
    /// has nowhere to look, and which is the one fact that survives every
    /// rewording. The failure is provoked by pointing the function at a *file*,
    /// because a path whose parent is a file cannot be created and so fails in
    /// `open` rather than in `migrate` — the half whose message is checked.
    #[tokio::test]
    async fn a_failure_names_the_database_it_could_not_use() {
        let dir = scratch_dir();
        let blocked = dir.join("not-a-directory");
        std::fs::write(&blocked, b"").expect("the blocking file is writable");
        let wanted = blocked.join(DATABASE_FILE);

        // `expect_err` would need `Store: Debug`, and adding that to a crate
        // this one does not own to serve a test is not a trade worth making.
        // Matching on `Err` instead reads the same and asserts more precisely:
        // the success case is left unhandled rather than asserted to be
        // unreachable.
        let message = match open_database(&blocked).await {
            Ok(_) => panic!("a path whose parent is a file cannot hold a database"),
            Err(message) => message,
        };

        assert!(
            message.contains(&wanted.display().to_string()),
            "the error does not name the database it could not open: {message}"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }
}
