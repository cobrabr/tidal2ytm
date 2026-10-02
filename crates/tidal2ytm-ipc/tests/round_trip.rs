//! Proves the Rust host and the Python sidecar agree on the wire.
//!
//! This spawns the real sidecar. That is the point: a stub would only prove
//! the client can talk to itself. It is also what keeps the boundary honest on
//! a headless Linux runner, where no Tauri and no display exist.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::Value;
use tidal2ytm_ipc::{ErrorKind, IpcError, SidecarClient, SidecarConfig, log_level_env};

/// The repository root, derived from this crate's manifest so the test does
/// not depend on the working directory cargo happened to choose. Walking up
/// with `ancestors` rather than joining `../..` keeps `..` and Windows'
/// verbatim `\\?\` prefixes out of the paths handed to the child.
fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .expect("the crate lives two levels below the workspace root")
        .to_path_buf()
}

/// The interpreter to run the sidecar with.
///
/// `TIDAL2YTM_SIDECAR_PYTHON` wins. Otherwise the uv venv is preferred, because
/// `uv sync --all-groups` builds the only interpreter that carries the sidecar's
/// dependencies and it is *not* on `PATH`: `python`/`python3` is the system or
/// per-user install, which has neither `tidal2ytm_sidecar` nor `tidalapi`/
/// `ytmusicapi`, and the sidecar dies on its first import. The bare name stays
/// as the fallback so a checkout with no `.venv` still has something to try.
fn interpreter(root: &Path) -> String {
    if let Ok(explicit) = std::env::var("TIDAL2YTM_SIDECAR_PYTHON") {
        return explicit;
    }
    let venv = if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    };
    if venv.is_file() {
        return venv.to_string_lossy().into_owned();
    }
    if cfg!(windows) {
        "python".to_string()
    } else {
        "python3".to_string()
    }
}

fn config() -> SidecarConfig {
    let root = repo_root();
    // The sidecar package is imported from the checkout rather than from the
    // venv, so a `uv sync` that has not installed the workspace member still
    // starts. Its own dependencies still have to come from the interpreter.
    let package_root = root.join("sidecar");
    let (_level, value) = log_level_env("INFO").expect("INFO is a valid level");
    SidecarConfig {
        program: interpreter(&root).into(),
        args: vec!["-m".to_string(), "tidal2ytm_sidecar".to_string()],
        // `tidal2ytm.paths.DATA_DIR` is the relative `data`, so the token caches
        // the sidecar reads resolve here and not the developer's real ones.
        cwd: package_root.clone(),
        env: vec![
            ("TIDAL2YTM_LOG_LEVEL".to_string(), value),
            (
                "PYTHONPATH".to_string(),
                package_root.to_string_lossy().into_owned(),
            ),
        ],
        handshake_timeout: Duration::from_secs(30),
    }
}

async fn spawn_or_explain(config: &SidecarConfig) -> SidecarClient {
    match SidecarClient::spawn(config).await {
        Ok(client) => client,
        Err(IpcError::Spawn { message }) => panic!(
            "could not start the sidecar with {:?}: {message}\n\
             Run `uv sync --all-groups` first, or set TIDAL2YTM_SIDECAR_PYTHON to an \
             interpreter that has tidal2ytm-sidecar installed.",
            config.program
        ),
        // Not a `Spawn` error means the interpreter ran and the process exited
        // on its own, which in practice is the sidecar dying on an import. Its
        // stderr went to the client's one-shot channel and is gone with it, so
        // the reason is named here rather than made to be rediscovered.
        Err(other) => panic!(
            "sidecar failed to start with {:?}: {other}\n\
             The interpreter must be the one `uv sync --all-groups` builds and must \
             have tidal2ytm and its dependencies importable. Set \
             TIDAL2YTM_SIDECAR_PYTHON to override.",
            config.program
        ),
    }
}

#[tokio::test]
async fn the_real_sidecar_answers_auth_status() {
    let config = config();
    let client = spawn_or_explain(&config).await;

    let data: Value = client
        .call(
            "auth.status",
            serde_json::json!({}),
            Some(Duration::from_secs(20)),
        )
        .await
        .expect("auth.status is answered");

    for provider in ["tidal", "ytm"] {
        let state = data
            .get(provider)
            .unwrap_or_else(|| panic!("auth.status reports {provider}"));
        assert!(
            state["valid"].is_boolean(),
            "{provider}.valid must be a boolean, got {state}"
        );
        assert!(
            state["expires_at"].is_null() || state["expires_at"].is_string(),
            "{provider}.expires_at must be a string or null, got {state}"
        );
        // `contracts/auth_status.json` pins the provider object to exactly these
        // two keys, which is what keeps a future field from quietly shipping
        // cache contents over the boundary.
        let keys: Vec<&str> = state
            .as_object()
            .expect("a provider is an object")
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            keys.iter()
                .copied()
                .collect::<std::collections::BTreeSet<_>>(),
            ["expires_at", "valid"].into_iter().collect(),
            "{provider} carries an unexpected field"
        );
    }
    // Token material must never cross the boundary, whatever the cache holds.
    assert!(
        !data.to_string().to_lowercase().contains("token"),
        "auth.status leaked something token-shaped: {data}"
    );

    client.shutdown().await.expect("sidecar shuts down");
}

#[tokio::test]
async fn the_real_sidecar_refuses_an_op_it_does_not_implement() {
    let config = config();
    let client = spawn_or_explain(&config).await;

    let error = client
        .call(
            "library.add",
            serde_json::json!({}),
            Some(Duration::from_secs(20)),
        )
        .await
        .expect_err("library.add is not implemented in this build");
    // A missing op is `bad_request`, not a crash and not a generic upstream
    // failure: the host branches on the kind, so agreeing on the name is the
    // thing being proven here.
    assert!(
        matches!(
            error,
            IpcError::Sidecar {
                kind: ErrorKind::BadRequest,
                ..
            }
        ),
        "an unimplemented op must be a bad_request, got: {error:?}"
    );
    assert!(
        error.to_string().contains("library.add"),
        "the error must name the op, got: {error}"
    );

    client.shutdown().await.expect("sidecar shuts down");
}

#[tokio::test]
async fn closing_stdin_stops_the_real_sidecar() {
    let config = config();
    let client = spawn_or_explain(&config).await;
    client.shutdown().await.expect("sidecar shuts down");
    assert!(!client.is_alive());

    // A shut-down sidecar is not a sidecar that answers slowly: the next call
    // must fail now rather than sit on its per-op timeout waiting for a
    // response from a process that is gone.
    let after = client
        .call("auth.status", serde_json::json!({}), None)
        .await;
    assert!(
        matches!(after, Err(IpcError::SidecarLost)),
        "got: {after:?}"
    );
}

// Four calls in flight at once against a real process, mixing a success and an
// error envelope so both kinds are resolved from real traffic.
//
// This does *not* prove id correlation on its own, and it is worth being honest
// about why: the sidecar reads and answers one line at a time, so its replies
// arrive in request order and an arrival-order pairing in the client coincides
// with the right answer. `client::tests::concurrent_calls_each_receive_their_own_response`
// is where correlation is proven, against a stub that answers out of order. What
// this proves is the half a stub cannot: four in-flight calls survive the real
// process and the real runner, and each resolves without a hang.
#[tokio::test]
async fn concurrent_calls_each_receive_their_own_answer() {
    let config = config();
    let client = std::sync::Arc::new(spawn_or_explain(&config).await);

    let calls: Vec<(&str, tokio::task::JoinHandle<Result<Value, IpcError>>)> = [
        "auth.status",
        "library.add",
        "auth.status",
        "favorites.list",
    ]
    .into_iter()
    .map(|op| {
        let client = std::sync::Arc::clone(&client);
        (
            op,
            tokio::spawn(async move {
                client
                    .call(op, serde_json::json!({}), Some(Duration::from_secs(20)))
                    .await
            }),
        )
    })
    .collect();

    for (op, handle) in calls {
        let result = handle.await.expect("task joins");
        if op == "auth.status" {
            let data = result.expect("auth.status succeeds");
            assert!(data["tidal"]["valid"].is_boolean(), "got {data}");
            assert!(data["ytm"]["valid"].is_boolean(), "got {data}");
        } else {
            let error = result.expect_err("an unimplemented op fails");
            assert!(
                error.to_string().contains(op),
                "{op} received another call's answer: {error}"
            );
        }
    }
}
