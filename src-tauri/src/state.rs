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

/// The advice for a child that started, answered, and was then killed for what
/// it answered.
///
/// Its stderr is not where the answer is: the mismatch is between the `hello` it
/// sent and the one this build reads, and rerunning the command in a terminal
/// would start a *different* sidecar — the source one, through the checkout
/// fallback — which very likely works. The fix is to rebuild and restage, not to
/// go looking in a pipe.
const WRONG_BUILD: &str = "The sidecar answered but this host cannot read the answer — that is a version \
     mismatch or a missing capability, so the packaged sidecar is stale or wrong. \
     Rebuild it with `pwsh sidecar/build_sidecar.ps1` and restart the app.";

/// The advice for a child that never said anything within the handshake timeout.
///
/// Nothing was written to stderr, so there is nothing to read and nothing to run
/// the command for; a slow start and a wedged one are indistinguishable from out
/// here.
const SAID_NOTHING: &str = "The sidecar produced no output at all within the handshake timeout — it may be \
     slow to start, or wedged; nothing was written to stderr.";

/// The advice that accompanies a failure the sidecar produced for itself.
///
/// The process wrote this to stderr and nobody will ever read it again, so the
/// only way to see it is to run the command. Nothing else in the message can do
/// that job.
const RUN_IT_YOURSELF: &str =
    "Whatever it wrote to stderr went with it — run that command in a terminal to see why.";

/// What a person should be told to do about a startup failure, or `None` when
/// there is nothing to run and so nothing to advise.
///
/// Split out of `describe` because *which* failures carry advice is a decision
/// about the four kinds of failure rather than about any wording, and it is the
/// part a reader acts on: telling someone to inspect a stderr that was never
/// written sends them looking for a file that cannot exist, and telling them to
/// rerun the command for a failure the rerun cannot reproduce sends them
/// somewhere useless.
///
/// The four kinds are distinguished by how much the child actually did, because
/// that is what decides where the answer is:
///
/// - `Spawn` — the command never ran. No advice; there is no output to read.
/// - `SidecarLost` — the child ran and stopped answering. Its stderr is gone with
///   it, so the reader has to produce it again by running the command.
/// - `MalformedResponse`, `ProtocolMismatch`, `MissingCapability` — the child ran
///   *and* completed a handshake this build then refused. The answer is on stdout
///   and already reported; stderr is beside the point.
/// - `Timeout` — the child produced no output within the timeout. Named by `op`,
///   because `SidecarClient::call` reports the same variant for a request that
///   went unanswered and only a startup failure ever reaches here. Nothing was
///   written, so neither of the other two advices applies.
fn remedy(error: &IpcError) -> Option<&'static str> {
    match error {
        // The operating system refused to start the process, so it wrote
        // nothing anywhere.
        IpcError::Spawn { .. } => None,
        IpcError::SidecarLost => Some(RUN_IT_YOURSELF),
        IpcError::ProtocolMismatch { .. }
        | IpcError::MissingCapability { .. }
        | IpcError::MalformedResponse { .. } => Some(WRONG_BUILD),
        // Only a `Timeout` on `hello` reaches this function; `Sidecar`'s own
        // errors are reported by `call`, not by startup.
        IpcError::Timeout { op, .. } if op == "hello" => Some(SAID_NOTHING),
        _ => None,
    }
}

/// A startup failure, in terms a person can act on.
///
/// The command is named in every arm because it is the one fact that is always
/// actionable, and the advice is attached by `remedy` so the two cannot drift
/// into telling the reader the wrong thing.
///
/// `IpcError::Spawn`'s own `Display` already opens with "could not start the
/// sidecar", so that arm uses the inner message rather than repeating it. The
/// other arms split on whether the child *answered*, for the same reason
/// `remedy` does: "started and then stopped" is false of a child that completed
/// a handshake this host refused to read, and the difference is the difference
/// between advice that helps and advice that wastes an afternoon.
fn describe(error: &IpcError, config: &SidecarConfig) -> String {
    let command = std::iter::once(config.program.display().to_string())
        .chain(config.args.iter().cloned())
        .collect::<Vec<_>>()
        .join(" ");
    let what = match error {
        IpcError::Spawn { message } => {
            format!("could not start the sidecar with `{command}`: {message}")
        }
        IpcError::ProtocolMismatch { .. }
        | IpcError::MissingCapability { .. }
        | IpcError::MalformedResponse { .. } => format!(
            "the sidecar started as `{command}` and answered, but this host refused the answer: {error}"
        ),
        other => format!("the sidecar started as `{command}` and then stopped: {other}"),
    };
    match remedy(error) {
        Some(advice) => format!("{what}. {advice}"),
        None => what,
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

    /// A command with an argument, so the assertions can tell "the command is
    /// missing" from "one word of it is missing".
    fn config() -> SidecarConfig {
        SidecarConfig {
            program: std::path::PathBuf::from("sidecars/tidal2ytm-sidecar.exe"),
            args: vec!["-m".to_string(), "tidal2ytm_sidecar".to_string()],
            cwd: std::env::temp_dir(),
            env: Vec::new(),
            handshake_timeout: std::time::Duration::from_secs(20),
        }
    }

    /// The wording the operating system gives when the sidecar's working
    /// directory does not exist. Fixture and assertion share one owner so the
    /// test is not a lock on wording: what is checked is that the cause the OS
    /// gave reaches the reader, not how it is phrased around it.
    const REFUSED: &str = "The directory name is invalid. (os error 267)";

    fn assert_names_the_command(message: &str, config: &SidecarConfig) {
        assert!(
            message.contains(&config.program.display().to_string()),
            "the command is missing the program: {message}"
        );
        for arg in &config.args {
            assert!(
                message.contains(arg),
                "the command omits `{arg}`: {message}"
            );
        }
    }

    fn refused() -> IpcError {
        IpcError::Spawn {
            message: REFUSED.to_string(),
        }
    }

    // A refusal has no stderr to look at: the operating system never started the
    // process, so it wrote nothing. Telling the reader to inspect a diagnostic
    // that cannot exist sends them hunting for a file that was never made, which
    // is how "it would not start" becomes an afternoon.
    #[test]
    fn a_spawn_refusal_names_the_command_and_carries_no_advice() {
        let config = config();
        let message = describe(&refused(), &config);

        assert_names_the_command(&message, &config);
        assert!(
            !message.contains(RUN_IT_YOURSELF),
            "a process that never started has no diagnostics to run a command for: {message}"
        );
    }

    // The other arm is the one where the diagnostics existed and are gone, so it
    // has to say both that and what to do about it. Without the second half the
    // reader is told something was lost and given no way to see it.
    #[test]
    fn a_sidecar_that_stopped_names_the_command_and_how_to_see_the_diagnostics() {
        let config = config();
        let message = describe(&IpcError::SidecarLost, &config);

        assert_names_the_command(&message, &config);
        assert!(
            message.contains(RUN_IT_YOURSELF),
            "the reader is not told how to see the diagnostics that were lost: {message}"
        );
    }

    // The two arms exist because the two failures are different, and swapping
    // them is the regression: each arm read plausibly on its own. Comparing the
    // two is what catches it, and it also catches both arms losing whatever told
    // them apart — a rewrite that flattened them into one sentence passes both
    // tests above.
    #[test]
    fn the_two_failures_do_not_produce_the_same_message() {
        let config = config();
        let refusal = describe(&refused(), &config);
        let stopped = describe(&IpcError::SidecarLost, &config);

        assert_ne!(
            refusal, stopped,
            "a refusal and a crash must not read the same to whoever sees them"
        );
        assert!(
            !refusal.contains(RUN_IT_YOURSELF) && stopped.contains(RUN_IT_YOURSELF),
            "the advice belongs on exactly the arm that lost diagnostics"
        );
    }

    // The defect this prevents: a sidecar that starts, handshakes cleanly, and
    // is killed for reporting protocol version 2 is the stale-packaged-sidecar
    // case, and the advice that sends the reader to a terminal is worse than none
    // — rerunning there starts the *source* sidecar through the checkout
    // fallback, which works, so the reader is told to verify a failure they
    // cannot reproduce. Its stderr is beside the point: the mismatch is in the
    // `hello` already reported above.
    #[test]
    fn a_sidecar_that_answered_with_something_unreadable_is_blamed_on_the_build_not_its_stderr() {
        let config = config();
        for error in [
            IpcError::ProtocolMismatch {
                expected: 1,
                got: 2,
            },
            IpcError::MissingCapability {
                name: "auth.status".to_string(),
            },
            IpcError::MalformedResponse {
                line: "{\"event\":\"hello\"".to_string(),
            },
        ] {
            let message = describe(&error, &config);

            assert_names_the_command(&message, &config);
            assert!(
                !message.contains(RUN_IT_YOURSELF),
                "the child answered, so stderr is not where its problem is: {message}"
            );
            assert!(
                message.contains(WRONG_BUILD),
                "the reader is not told to rebuild the packaged sidecar: {message}"
            );
        }
    }

    // The defect this prevents: the handshake timeout covers a sidecar that is
    // merely slow, and a slow one wrote nothing to stderr. Both `RUN_IT_YOURSELF`
    // arms assert that diagnostics existed and are gone, which is false here —
    // the reader goes to a terminal, runs a sidecar that starts perfectly, and
    // concludes the app is wrong.
    #[test]
    fn a_handshake_that_timed_out_says_nothing_was_written() {
        let config = config();
        let message = describe(
            &IpcError::Timeout {
                op: "hello".to_string(),
                ms: 20_000,
            },
            &config,
        );

        assert_names_the_command(&message, &config);
        assert!(
            !message.contains(RUN_IT_YOURSELF) && !message.contains(WRONG_BUILD),
            "neither of the advice sentences fits a child that never spoke: {message}"
        );
        assert!(
            message.contains(SAID_NOTHING),
            "the reader is not told that no output was produced at all: {message}"
        );
    }

    // Four kinds of failure, four different next steps. Collapsing any two of them
    // into one message passes the per-arm tests above — each asserts only that its
    // own advice is present and the other two are absent, so a rewrite that
    // flattened, say, the handshake timeout into "read its stderr" would satisfy
    // each in turn while being wrong in all of them at once.
    #[test]
    fn each_kind_of_startup_failure_gets_its_own_message() {
        let config = config();
        let refused = describe(&refused(), &config);
        let lost = describe(&IpcError::SidecarLost, &config);
        let unreadable = describe(
            &IpcError::ProtocolMismatch {
                expected: 1,
                got: 2,
            },
            &config,
        );
        let silent = describe(
            &IpcError::Timeout {
                op: "hello".to_string(),
                ms: 20_000,
            },
            &config,
        );

        for (first, second) in [
            (&refused, &lost),
            (&refused, &unreadable),
            (&refused, &silent),
            (&lost, &unreadable),
            (&lost, &silent),
            (&unreadable, &silent),
        ] {
            assert_ne!(
                first, second,
                "two different startup failures must not read the same"
            );
        }
    }

    // A child that read our request and answered with a failure is not a startup
    // problem and never reaches `describe`, but the guard is one word wide, so
    // assert it stays that way: widening it to cover `Timeout` for any op would
    // have this function attribute a hung *request* to startup.
    #[test]
    fn a_timeout_on_another_op_carries_no_startup_advice() {
        let message = describe(
            &IpcError::Timeout {
                op: "favorites.list".to_string(),
                ms: 30_000,
            },
            &config(),
        );

        for advice in [RUN_IT_YOURSELF, WRONG_BUILD, SAID_NOTHING] {
            assert!(
                !message.contains(advice),
                "a non-handshake timeout is not a startup failure: {message}"
            );
        }
    }

    // The cause the operating system gave is the actionable half of this message
    // — everything else in it is the host describing its own plumbing. Dropping
    // it leaves a reader who knows only that "the sidecar" would not start.
    #[test]
    fn a_spawn_refusal_keeps_the_operating_systems_own_wording() {
        let message = describe(&refused(), &config());
        assert!(message.contains(REFUSED), "the cause is dropped: {message}");
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
