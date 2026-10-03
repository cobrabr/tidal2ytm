//! The state the Tauri commands read, and the sidecar's lifetime.

use std::sync::Arc;

use serde::{Deserialize, Serialize};
use tidal2ytm_ipc::{IpcError, SidecarClient, SidecarConfig};
use tokio::sync::Mutex;

/// Whether one provider has a usable cached token.
///
/// The field names are snake_case because they *are* the contract: they reach
/// the frontend verbatim and have to match `src/lib/types.ts`, which derives
/// its own shape from `contracts/auth_status.json`. No `rename_all` is applied
/// because none is needed, and adding one would only be a second place for a
/// rename to be forgotten.
///
/// `Clone` but not `Copy`: `expires_at` is an owned `String`, so a copy would
/// mean either a clone or a shared reference, and neither is wanted.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderState {
    pub valid: bool,
    pub expires_at: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuthStatus {
    pub tidal: ProviderState,
    pub ytm: ProviderState,
}

/// The sidecar, plus everything needed to start another one.
pub struct AppState {
    /// Kept so a respawn starts the same command, in the same directory, with
    /// the same environment as the first attempt. Resolving again at respawn
    /// time would let a changed `TIDAL2YTM_SIDECAR_BIN` swap the binary under
    /// a running application.
    config: SidecarConfig,
    /// Behind a lock rather than shared immutably because a respawn replaces it.
    /// The lock doubles as the respawn serialiser: see `replace`.
    current: Mutex<Arc<SidecarClient>>,
}

impl AppState {
    /// Start the first sidecar and take its stderr.
    pub async fn start(config: SidecarConfig) -> Result<Self, String> {
        let client = spawn(&config).await?;
        Ok(Self {
            config,
            current: Mutex::new(Arc::new(client)),
        })
    }

    /// The sidecar to talk to right now.
    pub async fn client(&self) -> Arc<SidecarClient> {
        let slot = self.current.lock().await;
        Arc::clone(&slot)
    }

    /// Replace a sidecar the caller has already been told is gone.
    ///
    /// Identity, not a liveness flag, decides. The caller arrives here only
    /// after the client reported `IpcError::SidecarLost`, which it does on EOF
    /// from stdout; `is_alive` is still `true` in the window between the child
    /// dying and that EOF arriving, so anything keyed on it leaves the window
    /// showing a state no process can answer for, with nothing scheduled to
    /// retry. Comparing the `Arc` pointers also means a burst of calls that all
    /// failed against one dead sidecar produces exactly one respawn: the losers
    /// find `dead` is no longer current and take the winner's client.
    pub async fn replace(&self, dead: &Arc<SidecarClient>) -> Result<Arc<SidecarClient>, String> {
        let mut slot = self.current.lock().await;
        if !Arc::ptr_eq(&slot, dead) {
            return Ok(Arc::clone(&slot));
        }
        let fresh = spawn(&self.config).await?;
        *slot = Arc::new(fresh);
        Ok(Arc::clone(&slot))
    }
}

/// Start one sidecar and take its stderr, which nobody else will.
async fn spawn(config: &SidecarConfig) -> Result<SidecarClient, String> {
    let client = SidecarClient::spawn(config)
        .await
        .map_err(|error| describe(&error, config))?;

    // Held for the life of the process, not read once. `Child::spawn` gives
    // back a handle for stderr as well as stdout, and a receiver that is
    // dropped closes nothing on the child's side: the child keeps writing until
    // the OS pipe buffer fills, then blocks inside its own logging, and every
    // request it was going to answer times out. The symptom is a silent wedge
    // that no gate can see, so the lines are drained here for as long as the
    // sidecar lives.
    //
    // `take_stderr` is one-shot and this is its only caller, so `None` would
    // mean the receiver had already gone. That is not a state to discover in
    // the field: it is the wedge above, arriving a moment later.
    let Some(mut lines) = client.take_stderr() else {
        return Err("the sidecar's stderr was already claimed and cannot be drained".to_string());
    };
    tauri::async_runtime::spawn(async move {
        while let Some(line) = lines.recv().await {
            log::debug!(target: "tidal2ytm::sidecar", "proc=sidecar {line}");
        }
    });

    Ok(client)
}

/// A startup failure, in terms a person can act on.
///
/// `Spawn` means the operating system refused to start the process, so there
/// was never a stderr to read and naming the command is the whole of the help.
/// Every other variant means the child ran and then stopped answering, and the
/// diagnostics it wrote are gone with it: `SidecarClient::spawn` hands its
/// stderr to the caller, and a caller that never receives a client never gets
/// to drain them. Saying so is the difference between "it would not start" and
/// "run this and read this".
fn describe(error: &IpcError, config: &SidecarConfig) -> String {
    let command = std::iter::once(config.program.display().to_string())
        .chain(config.args.iter().cloned())
        .collect::<Vec<_>>()
        .join(" ");
    match error {
        IpcError::Spawn { .. } => {
            format!("could not start the sidecar with `{command}`: {error}")
        }
        _ => format!(
            "the sidecar started as `{command}` and then stopped: {error}. \
             Whatever it wrote to stderr went with it — run that command in a \
             terminal to see why."
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    /// The canonical `auth.status` answer, read rather than restated.
    /// `contracts/auth_status.json` is the one place the cross-language shape is
    /// written down, and `src/lib/types.ts` derives its own copy from it. A
    /// fourth restatement here would fail on a legitimate change with a diff
    /// pointing at production code, and whoever made the change would have no
    /// way to guess the copy exists.
    fn contract() -> Value {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(1)
            .expect("the host crate lives one level below the workspace root")
            .join("contracts")
            .join("auth_status.json");
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
        serde_json::from_str(&text).expect("the contract parses")
    }

    fn keys_of<'a>(value: &'a Value, what: &str) -> std::collections::BTreeSet<&'a str> {
        value
            .as_object()
            .unwrap_or_else(|| panic!("{what} is a JSON object, got {value}"))
            .keys()
            .map(String::as_str)
            .collect()
    }

    // The gap this file exists for. `src/lib/types.ts` derives from the
    // contract, so a *rename* is a compile error in the frontend — but a field
    // *added* to the contract is accepted by every unchanged consumer: serde
    // ignores what the struct does not name, and the frontend never sees a key
    // it did not already have. Comparing the host's own answer against the
    // contract closes that direction, so the provider status the window renders
    // cannot quietly stop carrying a field the sidecar started sending.
    #[test]
    fn the_host_reads_exactly_the_shape_the_contract_records() {
        let contract = contract();
        let parsed: AuthStatus = serde_json::from_value(contract["data"].clone())
            .expect("the contract's data deserialises");
        let forwarded = serde_json::to_value(parsed).expect("AuthStatus serialises");
        assert_eq!(
            keys_of(&forwarded, "the answer"),
            keys_of(&contract["data"], "the contract")
        );
        for (provider, expected) in contract["data"].as_object().expect("data is an object") {
            let actual = &forwarded[provider.as_str()];
            assert_eq!(
                keys_of(actual, provider),
                keys_of(expected, provider),
                "{provider} does not match contracts/auth_status.json"
            );
        }
    }

    // A payload missing the decision field is an error, not a default. Serde
    // silently reads a missing `expires_at` as `None` — which happens to be what
    // a signed-out provider sends anyway, so that one is harmless — but a
    // missing `valid` has no such equivalent. A `#[serde(default)]` added for
    // tolerance would therefore turn a broken answer into "not signed in" for
    // both providers, and re-authenticating is not the fix for that.
    #[test]
    fn a_payload_missing_the_decision_field_is_refused() {
        let mut payload = contract()["data"].clone();
        payload["tidal"]
            .as_object_mut()
            .expect("tidal is an object")
            .remove("valid");
        let outcome: Result<AuthStatus, serde_json::Error> = serde_json::from_value(payload);
        assert!(
            outcome.is_err(),
            "a missing `valid` must not read as signed out"
        );
    }

    // Nothing is skipped on the way back, so the frontend receives both keys the
    // contract names even when one of them is empty. `src/lib/types.ts` widens a
    // nullable field to `string | null` and not to `string | undefined`, so an
    // omitted key would be a type error on one side and a runtime `undefined` on
    // the other.
    #[test]
    fn every_contract_field_is_sent_even_when_it_is_empty() {
        let parsed: AuthStatus = serde_json::from_value(contract()["data"].clone())
            .expect("the contract's data deserialises");
        let forwarded = serde_json::to_value(parsed).expect("AuthStatus serialises");
        for provider in ["tidal", "ytm"] {
            assert!(
                forwarded[provider].get("expires_at").is_some(),
                "{provider} reached the frontend without its expiry key"
            );
        }
    }
}
