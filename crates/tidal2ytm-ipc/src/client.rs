//! The sidecar process client.
//!
//! Responsibilities: spawn the process, complete the `hello` handshake, match
//! responses to requests by `id`, fail in-flight calls when the process dies,
//! and forward the sidecar's stderr. It never writes to the host's stdout and
//! never interprets a response beyond unwrapping its envelope.
//!
//! # Lines the reader cannot use
//!
//! stdout carries responses, the one handshake event, and whatever a stray
//! `print` or a library banner adds to the channel. Those need three different
//! answers, and `classify` is where the difference is drawn:
//!
//! - A JSON object carrying an `event` is an announcement, not an answer. A
//!   readable `ServerEvent` is handled; one this build cannot read is a
//!   protocol violation. During the handshake that is unambiguous: `hello` is
//!   the only event that exists and the only thing being waited for, so it kills
//!   the process and returns `MalformedResponse`. Reporting a handshake timeout
//!   instead would blame the clock for a sidecar that had already answered.
//!   *After* the handshake it is forward-compatibility surface — a newer sidecar
//!   may announce something this build has never heard of — so it is stepped
//!   over rather than tearing down a healthy session. It is never attributed by
//!   an `id` it happens to carry: the sidecar never sends both keys, and failing
//!   a live call over an announcement would blame that call for news that is not
//!   its own.
//! - A JSON object carrying an unsigned integer `id` is a response *addressed to
//!   someone*. If it fails strict deserialisation — an error `kind` this build
//!   does not know, a missing `ok` — the line is still attributed by that `id`
//!   and the one call it belongs to fails with `MalformedResponse`. Stepping
//!   over it instead would leave that caller waiting out its per-op timeout,
//!   which is exactly the outcome `protocol::ErrorKind` documents that it is
//!   avoiding, and it would hide the offending line from the person debugging
//!   it.
//! - Anything else belongs to nobody: not JSON, or JSON with neither an `event`
//!   nor a usable `id`, including an `id` that is a string, a float or negative.
//!   No pending call is keyed by such an id, so failing one would mean guessing
//!   whose call to blame. It is stepped over, before and after the handshake.
//!
//! In-flight calls are never failed by a line that cannot be attributed to
//! them: that would blame an unrelated request for someone else's bad news. The
//! caller's per-op timeout is the backstop for a response that truly never
//! arrives, and a late response to a call that already gave up is discarded.

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex as StdMutex, PoisonError};
use std::time::Duration;

use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{Mutex, mpsc, oneshot};

use crate::protocol::{
    ErrorKind, IpcError, Request, Response, ServerEvent, WireError, parse_server_event,
    validate_hello,
};

/// Level names the host and the sidecar agree on, mirroring
/// `tidal2ytm.logging_setup.LOG_LEVELS`. An unknown name is refused rather than
/// defaulted, so a typo in the GUI selector surfaces immediately.
pub const LOG_LEVELS: &[&str] = &["OFF", "CRITICAL", "ERROR", "WARN", "INFO", "DEBUG"];

/// Validate a level name and return `(canonical_name, env_value)` to hand the
/// sidecar at spawn.
pub fn log_level_env(level: &str) -> Result<(&'static str, String), IpcError> {
    let upper = level.trim().to_ascii_uppercase();
    LOG_LEVELS
        .iter()
        .copied()
        .find(|name| *name == upper)
        .map(|name| (name, name.to_string()))
        .ok_or_else(|| IpcError::Spawn {
            message: format!("unknown log level: {level:?}"),
        })
}

/// How to start the sidecar.
#[derive(Debug, Clone)]
pub struct SidecarConfig {
    /// The executable. A packaged build passes the bundled binary; dev and CI
    /// pass an interpreter with `-m tidal2ytm_sidecar`.
    pub program: PathBuf,
    pub args: Vec<String>,
    /// Working directory. The sidecar resolves nothing relative to this, but
    /// setting it keeps a crash report pointing somewhere meaningful.
    pub cwd: PathBuf,
    /// Passed at spawn so the level applies even if the sidecar dies before its
    /// handshake.
    pub env: Vec<(String, String)>,
    pub handshake_timeout: Duration,
}

struct Pending {
    op: String,
    sender: oneshot::Sender<Result<Value, IpcError>>,
}

struct Inner {
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<u64, Pending>>,
    alive: AtomicBool,
}

impl Inner {
    /// Hand one line's outcome to the single caller it was addressed to. An
    /// `id` nobody is waiting for is a late answer to a call that already gave
    /// up: dropped, without touching any other caller's state.
    async fn resolve(&self, id: u64, make: impl FnOnce(&str) -> Result<Value, IpcError>) {
        let entry = self.pending.lock().await.remove(&id);
        if let Some(pending) = entry {
            let _ = pending.sender.send(make(&pending.op));
        }
    }
}

/// A live sidecar. One per app; shared by reference.
pub struct SidecarClient {
    inner: Arc<Inner>,
    next_id: AtomicU64,
    stderr_rx: StdMutex<Option<mpsc::UnboundedReceiver<String>>>,
}

/// What one stdout line turned out to be.
enum Line {
    /// A response envelope that parsed strictly.
    Response(Response),
    /// Addressed to an unsigned integer `id`, but shaped in a way this build
    /// cannot read.
    Unreadable { id: u64 },
    /// A server event, currently only the handshake.
    Event(ServerEvent),
    /// Carries an `event` key that is not a server event this build knows.
    UnknownEvent,
    /// Belongs to nobody: a stray print, a library banner, a blank line, an `id`
    /// no pending call is keyed by.
    Noise,
}

/// How the handshake loop ended.
enum Handshake {
    Event(ServerEvent),
    /// The sidecar announced an event this build cannot read.
    Violation(String),
    /// The sidecar closed stdout without handshaking.
    Lost,
}

impl SidecarClient {
    /// Start the sidecar and complete the handshake.
    ///
    /// A protocol mismatch or a missing required capability kills the process
    /// and returns the error, so the host can report a clear startup failure
    /// instead of letting every later job fail mysteriously. Request ids count
    /// from 1, which is what `contracts/auth_status.json` pins.
    pub async fn spawn(config: &SidecarConfig) -> Result<Self, IpcError> {
        let mut command = Command::new(&config.program);
        command
            .args(&config.args)
            .current_dir(&config.cwd)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            // The host owns the level; a stale value inherited from the
            // developer's shell must not win.
            .env_remove("TIDAL2YTM_LOG_LEVEL");
        for (key, value) in &config.env {
            command.env(key, value);
        }

        let mut child = command.spawn().map_err(|e| IpcError::Spawn {
            message: e.to_string(),
        })?;
        let stdin = child.stdin.take().ok_or(IpcError::Spawn {
            message: "sidecar stdin was not piped".to_string(),
        })?;
        let stdout = child.stdout.take().ok_or(IpcError::Spawn {
            message: "sidecar stdout was not piped".to_string(),
        })?;
        let stderr = child.stderr.take().ok_or(IpcError::Spawn {
            message: "sidecar stderr was not piped".to_string(),
        })?;

        let (line_tx, mut line_rx) = mpsc::unbounded_channel::<Option<String>>();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            // EOF or an unreadable pipe ends the loop, and the sentinel then
            // says once that the sidecar is gone. If nobody is listening any
            // more there is nothing to tell, so drop out without it.
            while let Ok(Some(line)) = lines.next_line().await {
                if line_tx.send(Some(line)).is_err() {
                    return;
                }
            }
            let _ = line_tx.send(None);
        });

        let (stderr_tx, stderr_rx) = mpsc::unbounded_channel();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if stderr_tx.send(line).is_err() {
                    return;
                }
            }
        });

        let client = Self {
            inner: Arc::new(Inner {
                child: Mutex::new(child),
                stdin: Mutex::new(Some(stdin)),
                pending: Mutex::new(HashMap::new()),
                alive: AtomicBool::new(true),
            }),
            next_id: AtomicU64::new(1),
            stderr_rx: StdMutex::new(Some(stderr_rx)),
        };

        // Handshake: read lines until a server event arrives. Noise is stepped
        // over; an event this build cannot read is a violation, not noise. The
        // nested pattern ends the loop on the EOF sentinel and on a closed
        // channel, which are the same thing: gone.
        let awaited = tokio::time::timeout(config.handshake_timeout, async {
            while let Some(Some(line)) = line_rx.recv().await {
                match classify(&line) {
                    Line::Event(event) => return Handshake::Event(event),
                    Line::UnknownEvent => return Handshake::Violation(line),
                    Line::Response(_) | Line::Unreadable { .. } | Line::Noise => {}
                }
            }
            Handshake::Lost
        })
        .await;

        let event = match awaited {
            Err(_) => {
                client.force_kill().await;
                return Err(IpcError::Timeout {
                    op: "hello".to_string(),
                    ms: config.handshake_timeout.as_millis() as u64,
                });
            }
            Ok(Handshake::Lost) => {
                client.force_kill().await;
                return Err(IpcError::SidecarLost);
            }
            Ok(Handshake::Violation(line)) => {
                client.force_kill().await;
                return Err(IpcError::MalformedResponse {
                    line: excerpt(&line),
                });
            }
            Ok(Handshake::Event(event)) => event,
        };

        if let Err(error) = validate_hello(&event) {
            client.force_kill().await;
            return Err(error);
        }

        // Hand the reader to the dispatcher now that the handshake is done.
        let inner = Arc::clone(&client.inner);
        tokio::spawn(async move {
            // `Some(Some(line))` ends on the EOF sentinel and on a closed
            // channel; both mean the sidecar is gone.
            while let Some(Some(line)) = line_rx.recv().await {
                match classify(&line) {
                    Line::Response(response) => {
                        let id = response.id;
                        inner.resolve(id, move |op| to_result(op, response)).await;
                    }
                    Line::Unreadable { id } => {
                        let excerpt = excerpt(&line);
                        inner
                            .resolve(id, move |_| {
                                Err(IpcError::MalformedResponse { line: excerpt })
                            })
                            .await;
                    }
                    Line::Event(_) | Line::UnknownEvent | Line::Noise => {}
                }
            }
            // EOF: the sidecar is gone. Every call still waiting fails now
            // instead of waiting out its timeout.
            inner.alive.store(false, Ordering::Release);
            let drained: Vec<Pending> = {
                let mut pending = inner.pending.lock().await;
                pending.drain().map(|(_, entry)| entry).collect()
            };
            for entry in drained {
                let _ = entry.sender.send(Err(IpcError::SidecarLost));
            }
        });

        Ok(client)
    }

    /// Send one request and wait for its response.
    ///
    /// `timeout` is per-op and comes from the spec: 30 s for search and album
    /// fetches, 15 s for `library.add`, none for `auth.begin`. On timeout the
    /// request leaves the pending table, so a response arriving later is
    /// discarded rather than resolving a call that already gave up.
    pub async fn call(
        &self,
        op: &str,
        params: Value,
        timeout: Option<Duration>,
    ) -> Result<Value, IpcError> {
        if !self.is_alive() {
            return Err(IpcError::SidecarLost);
        }
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (sender, receiver) = oneshot::channel();
        self.inner.pending.lock().await.insert(
            id,
            Pending {
                op: op.to_string(),
                sender,
            },
        );

        // `Request` is a `u64`, a `&str` and a `Value`, so there is nothing here
        // a serialiser can reject.
        let line = serde_json::to_string(&Request { id, op, params })
            .expect("a u64, a string, and a Value always serialise");

        let written = {
            let mut guard = self.inner.stdin.lock().await;
            match guard.as_mut() {
                Some(stdin) => {
                    stdin.write_all(line.as_bytes()).await.is_ok()
                        && stdin.write_all(b"\n").await.is_ok()
                        && stdin.flush().await.is_ok()
                }
                None => false,
            }
        };
        if !written {
            self.inner.pending.lock().await.remove(&id);
            return Err(IpcError::SidecarLost);
        }

        match timeout {
            Some(limit) => match tokio::time::timeout(limit, receiver).await {
                Ok(result) => result.map_err(|_| IpcError::SidecarLost)?,
                Err(_) => {
                    // Drop the entry so the response, when it does arrive, finds
                    // no caller and is discarded.
                    self.inner.pending.lock().await.remove(&id);
                    Err(IpcError::Timeout {
                        op: op.to_string(),
                        ms: limit.as_millis() as u64,
                    })
                }
            },
            None => receiver.await.map_err(|_| IpcError::SidecarLost)?,
        }
    }

    /// Close stdin. The sidecar sees EOF and exits on its own; there is no
    /// shutdown op. If it has not exited shortly after, it is killed.
    pub async fn shutdown(&self) -> Result<(), IpcError> {
        self.inner.stdin.lock().await.take();
        let mut child = self.inner.child.lock().await;
        match tokio::time::timeout(Duration::from_secs(3), child.wait()).await {
            Ok(Ok(_)) => {}
            Ok(Err(e)) => {
                return Err(IpcError::Spawn {
                    message: e.to_string(),
                });
            }
            Err(_) => {
                let _ = child.start_kill();
            }
        }
        self.inner.alive.store(false, Ordering::Release);
        Ok(())
    }

    /// False once the process is gone. Not a promise it is healthy: the spec
    /// requires a fresh `auth.status` before a respawned sidecar is reported
    /// good.
    #[must_use]
    pub fn is_alive(&self) -> bool {
        self.inner.alive.load(Ordering::Acquire)
    }

    /// Take the forwarded stderr lines. The caller owns them from here.
    pub fn take_stderr(&self) -> Option<mpsc::UnboundedReceiver<String>> {
        // The lock is never held across an await or a panic, so recovering from
        // poisoning is safe and beats reporting "already taken" for a lock that
        // is in fact fine.
        self.stderr_rx
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take()
    }

    async fn force_kill(&self) {
        let mut child = self.inner.child.lock().await;
        let _ = child.start_kill();
        let _ = child.wait().await;
        self.inner.alive.store(false, Ordering::Release);
    }
}

/// Sort a stdout line into what it is, so nothing that cannot be attributed to a
/// request is mistaken for one. See the module docs for why the unattributable
/// cases are treated differently.
fn classify(line: &str) -> Line {
    // `protocol::parse_server_event` is the one owner of what a server event is.
    if let Some(event) = parse_server_event(line) {
        return Line::Event(event);
    }
    let Ok(value) = serde_json::from_str::<Value>(line.trim()) else {
        return Line::Noise;
    };
    // An `event` key settles what the line is before an `id` is considered. The
    // sidecar never emits both, so this only decides the case a *newer* sidecar
    // introduces, and attributing that by its id would fail a live call over an
    // announcement.
    if value.get("event").is_some() {
        return Line::UnknownEvent;
    }
    // Only an unsigned integer can name a pending call, because that is the only
    // key `pending` holds. An `id` of any other type addresses nobody.
    if let Some(id) = value.get("id").and_then(Value::as_u64) {
        return match serde_json::from_value::<Response>(value) {
            Ok(response) => Line::Response(response),
            Err(_) => Line::Unreadable { id },
        };
    }
    Line::Noise
}

/// Cap an unusable line so a stray multi-kilobyte dump cannot travel through an
/// error message and the log file with it.
fn excerpt(line: &str) -> String {
    const LIMIT: usize = 200;
    let mut text: String = line.chars().take(LIMIT).collect();
    if line.chars().nth(LIMIT).is_some() {
        text.push_str("...");
    }
    text
}

fn to_result(op: &str, response: Response) -> Result<Value, IpcError> {
    if response.ok {
        return Ok(response.data.unwrap_or(Value::Null));
    }
    let WireError { kind, message } = response.error.unwrap_or(WireError {
        kind: ErrorKind::Upstream,
        message: "sidecar reported a failure with no error detail".to_string(),
    });
    Err(IpcError::Sidecar {
        op: op.to_string(),
        kind,
        message,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sidecar_python;
    use serde_json::json;
    use std::time::Duration;

    /// The stub's handshake line, read from `contracts/hello.json` rather than
    /// retyped here. That file is the one place the handshake shape is written
    /// down, and both suites assert against it; a stub carrying its own copy
    /// would keep passing after the contract it claims to honour had moved on.
    /// The parsed value is formatted as compact JSON, which the hello contract
    /// makes a valid Python dict literal — it holds strings and integers only.
    fn hello() -> String {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../contracts")
            .join("hello.json");
        let contract = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
        let value: Value = serde_json::from_str(&contract)
            .unwrap_or_else(|e| panic!("parse {}: {e}", path.display()));
        format!("import json,sys\nprint(json.dumps({value}), flush=True)\n")
    }

    fn config(script: &str) -> SidecarConfig {
        SidecarConfig {
            program: sidecar_python(&repo_root()),
            args: vec!["-c".to_string(), script.to_string()],
            cwd: std::env::temp_dir(),
            env: vec![("TIDAL2YTM_LOG_LEVEL".to_string(), "INFO".to_string())],
            handshake_timeout: Duration::from_secs(10),
        }
    }

    /// The repository root, derived from this crate's manifest so the tests do
    /// not depend on the working directory cargo happened to choose.
    fn repo_root() -> std::path::PathBuf {
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .expect("the crate lives two levels below the workspace root")
            .to_path_buf()
    }

    #[tokio::test]
    async fn a_sidecar_that_exits_before_the_handshake_is_a_loss() {
        let cfg = config("import sys; sys.exit(3)");
        assert!(matches!(
            SidecarClient::spawn(&cfg).await,
            Err(IpcError::SidecarLost)
        ));
    }

    #[tokio::test]
    async fn a_handshake_that_never_arrives_times_out() {
        let mut cfg = config("import time; time.sleep(30)");
        cfg.handshake_timeout = Duration::from_millis(400);
        assert!(matches!(
            SidecarClient::spawn(&cfg).await,
            Err(IpcError::Timeout { .. })
        ));
    }

    #[tokio::test]
    async fn a_protocol_mismatch_is_refused_at_startup() {
        let script = r#"import json
print(json.dumps({"event":"hello","protocol":99,"capabilities":["auth.status"]}), flush=True)
"#;
        assert!(matches!(
            SidecarClient::spawn(&config(script)).await,
            Err(IpcError::ProtocolMismatch {
                expected: 1,
                got: 99
            })
        ));
    }

    #[tokio::test]
    async fn a_sidecar_missing_a_required_capability_is_refused_at_startup() {
        let script = r#"import json
print(json.dumps({"event":"hello","protocol":1,"capabilities":[]}), flush=True)
"#;
        assert!(matches!(
            SidecarClient::spawn(&config(script)).await,
            Err(IpcError::MissingCapability { .. })
        ));
    }

    // A hello this build cannot deserialise is a protocol violation, not noise:
    // the sidecar tried to handshake and got the shape wrong. Reporting it as a
    // handshake timeout would blame the wrong thing and hide the offending line.
    #[tokio::test]
    async fn a_handshake_this_build_cannot_read_is_refused_not_timed_out() {
        let script = r#"import json
print(json.dumps({"event":"hello","protocol":"one","capabilities":["auth.status"]}), flush=True)
"#;
        let mut cfg = config(script);
        cfg.handshake_timeout = Duration::from_millis(400);
        assert!(
            matches!(
                SidecarClient::spawn(&cfg).await,
                Err(IpcError::MalformedResponse { .. })
            ),
            "a malformed hello must not masquerade as a timeout"
        );
    }

    #[tokio::test]
    async fn a_sidecar_that_writes_junk_before_the_handshake_still_starts() {
        let hello = hello();
        let script = format!(
            "import json,sys\nprint('a library banner', file=sys.stderr)\nprint('more noise')\n{hello}"
        );
        assert!(SidecarClient::spawn(&config(&script)).await.is_ok());
    }

    #[tokio::test]
    async fn sidecar_stderr_is_forwarded() {
        let hello = hello();
        let script = format!(
            "import sys\nprint('diagnostic line', file=sys.stderr, flush=True)\n{hello}for _ in sys.stdin:\n    pass\n"
        );
        let client = SidecarClient::spawn(&config(&script))
            .await
            .expect("starts");
        let mut lines = client.take_stderr().expect("stderr channel is present");
        let forwarded = tokio::time::timeout(Duration::from_secs(5), lines.recv())
            .await
            .expect("a line arrives")
            .expect("the channel is open");
        assert!(forwarded.contains("diagnostic line"), "got: {forwarded}");
    }

    // Review Focus #1: the sidecar is free to answer request 2 before request
    // 1. Pairing by arrival order instead of by `id` resolves each call with
    // the other's data, and since `auth.status` is the first thing the app
    // calls, that surfaces as one provider's state under the other's label.
    //
    // Four calls, answered in reverse, rather than the two a minimal reading
    // would use: an arrival-order implementation hands the first answer to
    // whichever entry its pending table iterates first, so a two-call version
    // passes whenever that order happens to match. Only real `id` correlation
    // satisfies all four every time.
    #[tokio::test]
    async fn concurrent_calls_each_receive_their_own_response() {
        const TAGS: [&str; 4] = ["a", "b", "c", "d"];
        let hello = hello();
        let script = format!(
            "import json,sys,time\n{hello}reqs=[json.loads(sys.stdin.readline()) for _ in range(4)]\nfor m in sorted(reqs, key=lambda r: -r['id']):\n    time.sleep(0.05)\n    print(json.dumps({{'id': m['id'], 'ok': True, 'data': {{'echo': m['params']['tag']}}}}), flush=True)\nfor _ in sys.stdin:\n    pass\n"
        );
        let client = Arc::new(
            SidecarClient::spawn(&config(&script))
                .await
                .expect("starts"),
        );

        let mut in_flight = Vec::new();
        for tag in TAGS {
            let client = Arc::clone(&client);
            in_flight.push(tokio::spawn(async move {
                client
                    .call("auth.status", json!({ "tag": tag }), None)
                    .await
            }));
        }

        for (tag, call) in TAGS.into_iter().zip(in_flight) {
            let answered = call.await.expect("the call joins").expect("the call ok");
            assert_eq!(answered["echo"], tag);
        }
    }

    // Review Focus #2: the in-flight call must fail, not hang and not invent a
    // success.
    #[tokio::test]
    async fn a_sidecar_that_dies_mid_request_fails_the_in_flight_call() {
        let hello = hello();
        let script = format!("import sys\n{hello}sys.stdin.readline()\nsys.exit(9)\n");
        let client = SidecarClient::spawn(&config(&script))
            .await
            .expect("starts");
        let result = client
            .call("auth.status", json!({}), Some(Duration::from_secs(5)))
            .await;
        assert!(
            matches!(result, Err(IpcError::SidecarLost)),
            "got: {result:?}"
        );
        assert!(!client.is_alive());
    }

    #[tokio::test]
    async fn a_call_after_the_sidecar_died_fails_immediately() {
        let hello = hello();
        let script = format!("import sys\n{hello}sys.stdin.readline()\nsys.exit(9)\n");
        let client = SidecarClient::spawn(&config(&script))
            .await
            .expect("starts");
        let _ = client
            .call("auth.status", json!({}), Some(Duration::from_secs(5)))
            .await;
        let second = client.call("auth.status", json!({}), None).await;
        assert!(matches!(second, Err(IpcError::SidecarLost)));
    }

    #[tokio::test]
    async fn a_timed_out_call_is_dropped_and_later_calls_still_work() {
        let hello = hello();
        let script = format!(
            "import json,sys,time\n{hello}for line in sys.stdin:\n    m=json.loads(line)\n    time.sleep(0.3)\n    print(json.dumps({{'id': m['id'], 'ok': True, 'data': {{'ok': True}}}}), flush=True)\n"
        );
        let client = SidecarClient::spawn(&config(&script))
            .await
            .expect("starts");
        let first = client
            .call("auth.status", json!({}), Some(Duration::from_millis(50)))
            .await;
        assert!(
            matches!(first, Err(IpcError::Timeout { .. })),
            "got: {first:?}"
        );
        let second = client
            .call("auth.status", json!({}), Some(Duration::from_secs(10)))
            .await;
        assert_eq!(
            second.expect("the next call still works")["ok"],
            json!(true)
        );
    }

    // A stray print after the handshake must not be mistaken for a response,
    // and must not stop the reader from serving later calls.
    #[tokio::test]
    async fn a_stray_stdout_line_does_not_break_later_calls() {
        let hello = hello();
        let script = format!(
            "import json,sys\n{hello}print('a stray print', flush=True)\nfor line in sys.stdin:\n    m=json.loads(line)\n    print(json.dumps({{'id': m['id'], 'ok': True, 'data': {{'ok': True}}}}), flush=True)\n"
        );
        let client = SidecarClient::spawn(&config(&script))
            .await
            .expect("starts");
        let result = client
            .call("auth.status", json!({}), Some(Duration::from_secs(10)))
            .await;
        assert_eq!(
            result.expect("the stray line was stepped over")["ok"],
            json!(true)
        );
    }

    // A newer sidecar may announce something this build has never heard of, and
    // may put the request id on the announcement so the host can watch a call
    // progress. Both an unreadable event and a readable one are announcements,
    // not answers: attributing either by its `id` would fail a live call over
    // news that is not its own, which is the one thing the module docs promise
    // cannot happen.
    #[tokio::test]
    async fn an_id_bearing_event_does_not_fail_the_call_it_names() {
        let hello = hello();
        let script = format!(
            "import json,sys\n{hello}m=json.loads(sys.stdin.readline())\nprint(json.dumps({{'id': m['id'], 'event': 'progress', 'done': 0.5}}), flush=True)\nprint(json.dumps({{'id': m['id'], 'event': 'hello', 'protocol': 1, 'capabilities': ['auth.status']}}), flush=True)\nprint(json.dumps({{'id': m['id'], 'ok': True, 'data': {{'ok': True}}}}), flush=True)\nfor _ in sys.stdin:\n    pass\n"
        );
        let client = SidecarClient::spawn(&config(&script))
            .await
            .expect("starts");
        let result = client
            .call("auth.status", json!({}), Some(Duration::from_secs(10)))
            .await;
        assert_eq!(
            result.expect("both announcements were stepped over")["ok"],
            json!(true)
        );
    }

    // `protocol.rs` promises that an unrecognised error kind surfaces as
    // `MalformedResponse`. That is only true if the reader attributes the line
    // by `id` *before* insisting it deserialises, and fails exactly that call
    // instead of leaving it to time out.
    #[tokio::test]
    async fn a_response_this_build_cannot_read_fails_only_its_own_call() {
        let hello = hello();
        let script = format!(
            "import json,sys\n{hello}for _ in range(2):\n    m=json.loads(sys.stdin.readline())\n    if m['params']['tag']=='bad':\n        print(json.dumps({{'id': m['id'], 'ok': False, 'error': {{'kind': 'quota_exceeded', 'message': 'slow down'}}}}), flush=True)\n    else:\n        print(json.dumps({{'id': m['id'], 'ok': True, 'data': {{'tag': m['params']['tag']}}}}), flush=True)\nfor _ in sys.stdin:\n    pass\n"
        );
        let client = Arc::new(
            SidecarClient::spawn(&config(&script))
                .await
                .expect("starts"),
        );

        let bad = {
            let client = Arc::clone(&client);
            tokio::spawn(async move {
                client
                    .call(
                        "auth.status",
                        json!({ "tag": "bad" }),
                        Some(Duration::from_secs(10)),
                    )
                    .await
            })
        };
        let good = {
            let client = Arc::clone(&client);
            tokio::spawn(async move {
                client
                    .call(
                        "auth.status",
                        json!({ "tag": "good" }),
                        Some(Duration::from_secs(10)),
                    )
                    .await
            })
        };

        assert!(matches!(
            bad.await.expect("bad joins"),
            Err(IpcError::MalformedResponse { .. })
        ));
        assert_eq!(
            good.await.expect("good joins").expect("good ok")["tag"],
            "good"
        );
    }

    #[test]
    fn a_valid_log_level_becomes_an_env_pair() {
        assert_eq!(
            log_level_env("debug").unwrap(),
            ("DEBUG", "DEBUG".to_string())
        );
    }

    #[test]
    fn an_invalid_log_level_is_refused() {
        assert!(log_level_env("LOUD").is_err());
    }
}
