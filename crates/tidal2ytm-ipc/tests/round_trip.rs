//! Proves the Rust host and the Python sidecar agree on the wire.
//!
//! This spawns the real sidecar. That is the point: a stub would only prove
//! the client can talk to itself. It is also what keeps the boundary honest on
//! a headless Linux runner, where no Tauri and no display exist.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::Value;
use tidal2ytm_ipc::{
    ErrorKind, IpcError, SidecarClient, SidecarConfig, log_level_env, sidecar_python,
};

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

fn config() -> SidecarConfig {
    let root = repo_root();
    let package_root = root.join("sidecar");
    // Belt and braces, not the mechanism. `cwd` below is already the package
    // root and `python -m` puts it on `sys.path[0]`, and `uv sync --all-groups`
    // installs both workspace packages into the venv regardless. Nothing about
    // this test depends on it.
    let (_level, value) = log_level_env("INFO").expect("INFO is a valid level");
    SidecarConfig {
        program: sidecar_python(&root),
        args: vec!["-m".to_string(), "tidal2ytm_sidecar".to_string()],
        // `tidal2ytm.paths.DATA_DIR` is the relative `data`, so the token caches
        // resolve here and not the developer's real ones. `sidecar/data` does not
        // exist in a checkout, which is what keeps `auth.status` off the network
        // — and it means this test only ever sees the signed-out answer.
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

/// The canonical `auth.status` answer from `contracts/auth_status.json`, read
/// rather than restated. That file is the one place the cross-language shape is
/// written down, and both suites assert against it. A third copy written here
/// would fail on a legitimate contract change with a diff pointing at production
/// code, and whoever authored the change would have no way to guess the copy
/// exists.
fn auth_status_contract() -> Value {
    let path = repo_root().join("contracts").join("auth_status.json");
    let text =
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("parse {}: {e}", path.display()))
}

/// An object's keys, order-free, so two payloads can be compared as shapes.
fn keys_of<'a>(value: &'a Value, what: &str) -> std::collections::BTreeSet<&'a str> {
    value
        .as_object()
        .unwrap_or_else(|| panic!("{what} is a JSON object, got {value}"))
        .keys()
        .map(String::as_str)
        .collect()
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

    // The shape is compared against the contract's, so a rename or an added
    // field on either side is a diff against `contracts/auth_status.json` and not
    // against a third restatement of it. What the *values* hold is deliberately
    // not asserted: with no token cache reachable the sidecar is signed out on
    // both providers, so this exercises `ops._signed_out` and nothing else.
    // Signed-in values are `ops.auth_status`'s contract with its own suite
    // (`sidecar/tests/test_ops.py`).
    let contract = auth_status_contract()["data"].clone();
    let providers = contract
        .as_object()
        .unwrap_or_else(|| panic!("the contract's data is an object, got {contract}"));
    assert!(!providers.is_empty(), "the contract names no providers");
    for (provider, expected) in providers {
        let actual = data
            .get(provider.as_str())
            .unwrap_or_else(|| panic!("auth.status does not report {provider}"));
        assert_eq!(
            keys_of(actual, provider),
            keys_of(expected, provider),
            "{provider} does not match contracts/auth_status.json"
        );
    }

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

// Closing stdin is the whole shutdown mechanism, and nothing shorter of the
// sidecar's own voice says it worked. `shutdown` returns `Ok` down both branches
// — the child exited and the child was killed after the grace period — and so
// does every other observable left: `is_alive` is cleared on both, and a
// subsequent `call` short-circuits on it. The one thing only the sidecar can
// report is that its read loop reached EOF, and that is exactly the
// two-implementations-agree fact this file exists to prove: the host closed the
// pipe, and Python's loop honoured it.
#[tokio::test]
async fn closing_stdin_stops_the_real_sidecar() {
    let config = config();
    let client = spawn_or_explain(&config).await;
    // Taken before the shutdown, so the lines the sidecar writes on its way out
    // are queued rather than dropped with the client.
    let mut stderr = client.take_stderr().expect("stderr channel is present");

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

    let mut honoured_eof = false;
    tokio::time::timeout(Duration::from_secs(10), async {
        while let Some(line) = stderr.recv().await {
            if line.contains("stdin closed, exiting") {
                honoured_eof = true;
                break;
            }
        }
    })
    .await
    .expect("the sidecar's stderr closed rather than the test hanging on it");
    assert!(
        honoured_eof,
        "the sidecar never reported stdin closing, so it was killed rather than \
         shut down and this test proved nothing about close-stdin"
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
