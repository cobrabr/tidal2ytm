//! The commands the frontend can call.
//!
//! Each one is a thin translation: a Tauri call becomes an IPC call, and the
//! result is either a plain value or a string the UI can show. No business rule
//! lives here — that is what the domain core is for.

use std::collections::BTreeMap;
use std::time::Duration;

use serde_json::{Value, json};
use tauri::{Emitter, State};
use tidal2ytm_core::model::TrackStatus;
use tidal2ytm_ipc::IpcError;

use crate::jobs::{JobManager, Progress};
use crate::state::{AppState, AuthStatus};

/// `auth.status` is a local file read behind a pipe, so the bound is tight: a
/// sidecar wedged on something else surfaces as a refused request the screen can
/// explain, rather than a window that never finishes loading.
const AUTH_STATUS_TIMEOUT: Duration = Duration::from_secs(5);

/// The providers `auth.begin` can start a sign-in for.
///
/// Checked here rather than only in the sidecar, because this string is what
/// the frontend supplied and the pipe is the boundary: an unrecognised one must
/// not be sent for the sidecar to refuse while the screen waits on it.
const AUTH_PROVIDERS: [&str; 2] = ["tidal", "ytm"];

#[tauri::command]
pub async fn get_auth_status(state: State<'_, AppState>) -> Result<AuthStatus, String> {
    let data = call(&state, "auth.status", json!({}), Some(AUTH_STATUS_TIMEOUT)).await?;
    serde_json::from_value::<AuthStatus>(data)
        .map_err(|e| format!("the sidecar returned an auth status this build cannot read: {e}"))
}

/// Start one provider's sign-in and wait for it to finish.
///
/// `None` for the timeout, and that is the whole difference from every other
/// command here: the op waits on a person and a browser, and a person may take
/// two minutes to click a link. Any bound would fail a sign-in that was about to
/// succeed, so the answer comes when it comes.
///
/// Answering this does not block the sidecar, which is why the absence of a
/// timeout is safe rather than merely tolerated: the read loop answers each
/// request on a thread of its own, so the screen keeps polling `auth.status`
/// throughout.
#[tauri::command]
pub async fn begin_auth(state: State<'_, AppState>, provider: String) -> Result<(), String> {
    check_provider(&provider)?;
    call(&state, "auth.begin", json!({ "provider": provider }), None).await?;
    Ok(())
}

/// Refuse a provider this build has no sign-in for, naming it back.
///
/// A free function so the check is testable without a `tauri::App`, which nothing
/// in this crate can build.
fn check_provider(provider: &str) -> Result<(), String> {
    if AUTH_PROVIDERS.contains(&provider) {
        return Ok(());
    }
    Err(format!("unknown auth provider: {provider}"))
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
    let counts = state.store().status_counts().map_err(|e| e.to_string())?;
    Ok(counts_by_name(counts))
}

/// Key the counts by the persisted status string.
///
/// A free function so the vocabulary can be tested without a `tauri::App`, which
/// nothing in this crate can build. What is worth pinning is not that the
/// counts arrived — the store's own suite covers that — but that every key is
/// `as_str()`'s own output, so a second hand-written copy of the status literals
/// cannot appear here unnoticed.
fn counts_by_name(counts: BTreeMap<TrackStatus, i64>) -> BTreeMap<String, i64> {
    counts
        .into_iter()
        .map(|(status, count)| (status.as_str().to_string(), count))
        .collect()
}

/// Which schema version the database is at.
///
/// Separate from `get_status_counts` because the two answer different questions:
/// this one is how a bug report says which build wrote the file, and a count
/// beside it would be read as part of the same answer.
#[tauri::command]
pub async fn get_schema_version(state: State<'_, AppState>) -> Result<i64, String> {
    state.store().schema_version().map_err(|e| e.to_string())
}

/// The Tauri event name the sync job's progress streams on.
///
/// Exactly this literal is the contract Task 11's Home screen subscribes to;
/// it is named here because this file is where the payload meets the wire.
pub const JOB_PROGRESS_EVENT: &str = "job-progress";

/// Start one sync of the Tidal favourites into the store.
///
/// The answer is the job id only; the pages travel on `job-progress` events
/// from the worker thread, which is where the long work they describe lives.
/// The client and the store come from `State<'_, AppState>` — not a second
/// `manage`, since `AppState` already holds both — and are handed over as
/// shared handles the worker can keep: the job outlives every await that
/// started it.
#[tauri::command]
pub async fn start_sync(
    state: State<'_, AppState>,
    manager: State<'_, JobManager>,
    app: tauri::AppHandle,
) -> Result<u64, String> {
    let client = state.client().await;
    manager.start_sync(client, state.store_arc(), move |progress| {
        emit_progress(&app, progress);
    })
}

/// Hand one progress emission to the event web.
///
/// The `Result` the emit answered with is logged, not propagated, because a
/// job run on its own thread has no command frame left to return an error
/// through and a listener that shut down mid-sync is not the sync's business.
fn emit_progress(app: &tauri::AppHandle, progress: Progress) {
    if let Err(error) = app.emit(JOB_PROGRESS_EVENT, progress) {
        log::warn!("could not emit sync progress: {error}");
    }
}

/// Ask one job to stop at its next page boundary.
///
/// A refusal leaves the error for the caller to read: a job that already
/// ended, or an id never issued, is exactly the state the screen that still
/// shows a live progress panel should be told it cannot act on.
#[tauri::command]
pub async fn cancel_job(manager: State<'_, JobManager>, jid: u64) -> Result<(), String> {
    if manager.cancel(jid) {
        Ok(())
    } else {
        Err(format!(
            "job {jid} cannot be cancelled: it is not running, or was never issued"
        ))
    }
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
///
/// `timeout` is `None` for `auth.begin` alone, because it waits on a human and a
/// browser. One parameter that may be absent rather than a second helper doing
/// the same round trip.
async fn call(
    state: &AppState,
    op: &str,
    params: Value,
    timeout: Option<Duration>,
) -> Result<Value, String> {
    let client = state.client().await;
    let result = client.call(op, params.clone(), timeout).await;
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
        .call(op, params, timeout)
        .await
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every status reaches the wire under `as_str`'s own name, with its count
    /// intact.
    ///
    /// The expected side is built by iterating the same statuses and calling the
    /// same `as_str`, so what is checked is that the mapping preserves each key
    /// and each count and emits one entry per status.
    ///
    /// What this cannot catch, stated plainly so the next reader does not assume
    /// it: a rewrite that hardcoded today's five literals would pass, because
    /// those literals are what `as_str` returns today. What it does catch is a
    /// key that is not `as_str`'s output at all — a `Debug` name, or a hand-copied
    /// set that has since drifted from the schema — and both of those fail
    /// immediately. Adding a sixth status also does not fail this on its own;
    /// the list below would need extending. `counts_by_name` taking its keys
    /// from `as_str` is the property, and this is what keeps that honest.
    #[test]
    fn every_status_reaches_the_wire_under_its_persisted_name() {
        let all = [
            TrackStatus::Pending,
            TrackStatus::Transferred,
            TrackStatus::Skip,
            TrackStatus::Failed,
            TrackStatus::NeedsReview,
        ];
        let counts: BTreeMap<TrackStatus, i64> = all
            .iter()
            .enumerate()
            .map(|(index, status)| (*status, index as i64 + 1))
            .collect();
        let expected: BTreeMap<String, i64> = counts
            .iter()
            .map(|(status, count)| (status.as_str().to_string(), *count))
            .collect();

        let named = counts_by_name(counts);
        assert_eq!(named, expected);
        assert_eq!(named.len(), all.len());
    }

    /// A status absent from the rows is absent from the answer, rather than
    /// present with a zero.
    ///
    /// `status_counts` is computed by a `GROUP BY`, so it reports only statuses
    /// that exist. Forwarding that as-is is the point — the frontend can tell
    /// "none transferred" from "this build cannot count transferred" — and a
    /// rewrite that pre-filled all five keys would erase that distinction while
    /// still passing the test above.
    #[test]
    fn a_status_with_no_rows_does_not_appear() {
        let counts = BTreeMap::from([(TrackStatus::Pending, 7)]);

        let named = counts_by_name(counts);

        assert_eq!(named.len(), 1);
        assert_eq!(named.get(TrackStatus::Pending.as_str()), Some(&7));
        assert!(!named.contains_key(TrackStatus::Transferred.as_str()));
    }

    /// A provider the sidecar does not have a sign-in for is refused at the
    /// host, before it reaches the pipe.
    ///
    /// What this catches is a value that is neither of the two literals: a typo
    /// from the frontend, or a provider added to one side only. Both would
    /// otherwise be sent, and the sidecar's refusal would arrive as the answer
    /// to a sign-in the screen is waiting on — after however long the wire takes,
    /// which for `auth.begin` is unbounded by design.
    #[test]
    fn only_the_two_known_providers_are_accepted() {
        for provider in AUTH_PROVIDERS {
            assert!(check_provider(provider).is_ok(), "{provider} is a provider");
        }
        for provider in ["", "YouTube", "spotify"] {
            assert!(check_provider(provider).is_err(), "{provider} is not");
        }
    }

    /// The event name is the one ruling Task 11's subscription pairs with, so
    /// it is pinned where the payload meets the wire: a renamed constant
    /// compiles green and silently strands every listener waiting on the
    /// name they were given.
    #[test]
    fn the_progress_event_is_named_by_the_ruling_it_serves() {
        assert_eq!(JOB_PROGRESS_EVENT, "job-progress");
    }
}
