//! The commands the frontend can call.
//!
//! Each one is a thin translation: a Tauri call becomes an IPC call, and the
//! result is either a plain value or a string the UI can show. No business rule
//! lives here — that is what the domain core is for.

use std::collections::BTreeMap;
use std::time::Duration;

use serde_json::{Value, json};
use tauri::State;
use tidal2ytm_ipc::IpcError;

use crate::state::{AppState, AuthStatus};

/// `auth.status` is a local file read behind a pipe, so the bound is tight: a
/// sidecar wedged on something else surfaces as a refused request the screen can
/// explain, rather than a window that never finishes loading.
const AUTH_STATUS_TIMEOUT: Duration = Duration::from_secs(5);

#[tauri::command]
pub async fn get_auth_status(state: State<'_, AppState>) -> Result<AuthStatus, String> {
    let data = call(&state, "auth.status", AUTH_STATUS_TIMEOUT).await?;
    serde_json::from_value::<AuthStatus>(data)
        .map_err(|e| format!("the sidecar returned an auth status this build cannot read: {e}"))
}

/// How many tracks sit in each status.
///
/// Reads the database directly rather than going through `call`, because the
/// sidecar is not involved at all: retrying on `SidecarLost` would be a respawn
/// of a process whose answer never depended on it.
///
/// Both this and `get_schema_version` surface a `StoreError` through its own
/// `Display` and add nothing around it. `UnknownSchemaVersion` is the case that
/// decides this: naming both the version on disk and the one this build knows
/// *is* the diagnosis, so a wrapper like "the database could not be read" would
/// throw both numbers away and send the reader to the schema instead of to the
/// two numbers that disagree.
///
/// The keys cross the wire as the persisted status strings rather than as Rust
/// variants, because `as_str` is the one owner of what a status is written as —
/// the same literals the `CHECK` constraint in the schema permits — and a second
/// copy of that vocabulary here is a second thing to forget to update.
#[tauri::command]
pub async fn get_status_counts(
    state: State<'_, AppState>,
) -> Result<BTreeMap<String, i64>, String> {
    let counts = state
        .store()
        .status_counts()
        .await
        .map_err(|e| e.to_string())?;
    Ok(counts
        .into_iter()
        .map(|(status, count)| (status.as_str().to_string(), count))
        .collect())
}

/// Which schema version the database is at.
///
/// Separate from `get_status_counts` because the two answer different questions:
/// this one is how a bug report says which build wrote the file, and a count
/// beside it would be read as part of the same answer.
#[tauri::command]
pub async fn get_schema_version(state: State<'_, AppState>) -> Result<i64, String> {
    state
        .store()
        .schema_version()
        .await
        .map_err(|e| e.to_string())
}

/// One round trip, with one respawn if the sidecar had already gone.
///
/// The retry is keyed on `IpcError::SidecarLost` — the error the client reports
/// once it has seen EOF on stdout — and never on `is_alive`, which is still
/// `true` in the window between the child dying and that EOF arriving. Keyed on
/// the flag, a dead sidecar leaves the window showing a state nothing can answer
/// for, with no retry scheduled.
///
/// A single retry per call is the whole policy here, deliberately. The spec's
/// bounded backoff — three attempts, then stop and prompt — belongs with the job
/// manager that owns long work, and its rule that a crash must never present a
/// logged-out application as ready is already met: the retry performs a real
/// `auth.status` and returns that answer, never a synthesised one.
async fn call(state: &AppState, op: &str, timeout: Duration) -> Result<Value, String> {
    let client = state.client().await;
    let result = client.call(op, json!({}), Some(timeout)).await;
    let error = match result {
        Ok(data) => return Ok(data),
        Err(error) => error,
    };
    if !matches!(error, IpcError::SidecarLost) {
        return Err(error.to_string());
    }
    state
        .replace(&client)
        .await?
        .call(op, json!({}), Some(timeout))
        .await
        .map_err(|e| e.to_string())
}
