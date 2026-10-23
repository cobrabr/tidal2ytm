//! Test doubles for the host's suites: a stub sidecar program that answers
//! `favorites.list` from fixed envelopes, and a per-test temporary store.
//!
//! Nothing here touches Tidal or YouTube Music or the network — the only thing
//! that starts is a Python interpreter talking to us over a pipe, exactly as
//! the real sidecar would.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use tidal2ytm_ipc::{SidecarClient, SidecarConfig, sidecar_python};
use tidal2ytm_store::Store;
/// The repository root, derived from this crate's manifest so the tests do not
/// depend on the working directory cargo happened to choose.
#[must_use]
pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(1)
        .expect("the host crate lives one level below the workspace root")
        .to_path_buf()
}

/// The stub's handshake line, read from `contracts/hello.json` rather than
/// retyped here. That file is the one place the handshake shape is written
/// down, and a stub carrying its own copy would keep passing after the
/// contract it claims to honour had moved on.
fn hello() -> String {
    let path = repo_root().join("contracts").join("hello.json");
    let contract =
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    let value: serde_json::Value =
        serde_json::from_str(&contract).unwrap_or_else(|e| panic!("parse {}: {e}", path.display()));
    value.to_string()
}

/// One page of favourites as an answer envelope, carrying the eleven keys
/// `contracts/favorites_list.json` records, in fictional names. `page_complete`
/// is false so an honest two-page loop asks again. The `id` is a placeholder
/// the stub rewrites to each caller's id.
const PAGE_ONE_ANSWER: &str = r##"{"id":0,"ok":true,"data":{"tracks":[{"tidal_id":174839201,"title":"Paper Tide","artist":"Ilse Vandermeer","artists":["Ilse Vandermeer","Bram Oduya"],"album":"Lantern Season","album_id":91247733,"album_year":2014,"duration_sec":217,"track_num":4,"disc_num":1,"version":null},{"tidal_id":174839202,"title":"Salt Bell (Reprise)","artist":"Bram Oduya","artists":["Bram Oduya"],"album":"Fen Hymns","album_id":91247734,"album_year":null,"duration_sec":141,"track_num":11,"disc_num":2,"version":"Reprise"}],"page_complete":false}}"##;

/// A second, final page: shorter than `PAGE_LIMIT`, so `page_complete` is true.
const PAGE_TWO_ANSWER: &str = r##"{"id":0,"ok":true,"data":{"tracks":[{"tidal_id":174839203,"title":"Winterlawn","artist":"Ilse Vandermeer","artists":["Ilse Vandermeer"],"album":"Lantern Season","album_id":91247733,"album_year":2014,"duration_sec":184,"track_num":5,"disc_num":1,"version":null}],"page_complete":true}}"##;

/// What the second request gets instead of a second page, shaped as
/// `contracts/error_auth_expired.json` records the kind. The message is
/// worded after the sidecar's own: a credential that could not be refreshed.
const AUTH_EXPIRED_ANSWER: &str = r##"{"id":0,"ok":false,"error":{"kind":"auth_expired","message":"Tidal credentials could not be refreshed; sign in again."}}"##;

/// What the second request answers with.
///
/// `More` is a full continuation — the case a two-page success needs.
/// `AuthExpired` is the carried Review Focus case: a Tidal lapse partway
/// through a sync.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SecondPage {
    More,
    AuthExpired,
}

/// The stub program for one `favorites.list` session.
///
/// `pause_ms` is how long the stub waits before answering a *second* request.
/// Tests that cancel from inside a progress event need the pause: the cancel is
/// dispatched while the loop is still inside its progress callback, well before
/// the second answer is even read.
fn favorites_config(second: &SecondPage, pause_ms: u64) -> SidecarConfig {
    let answer = match second {
        SecondPage::More => PAGE_TWO_ANSWER,
        SecondPage::AuthExpired => AUTH_EXPIRED_ANSWER,
    };
    // Each envelope travels as a single-quoted Python string and `json.loads`
    // turns it back into an envelope: both envelopes hold JSON literals
    // (`null`, `false`) that are not valid Python, so the raw JSON text cannot
    // be embedded as a dict literal. Neither contains an apostrophe.
    let escape = |text: &str| text.replace('\\', "\\\\").replace('\'', "\\'");
    SidecarConfig {
        program: sidecar_python(&repo_root()),
        args: vec![
            "-c".to_string(),
            format!(
                "import json,sys,time\n\
             print('{hello}', flush=True)\n\
             FIRST, SECOND = '{first}', '{second_answer}'\n\
             n = 0\n\
             for line in sys.stdin:\n\
             \x20   n += 1\n\
             \x20   req = json.loads(line)\n\
             \x20   if n > 1:\n\
             \x20       time.sleep({pause_ms} / 1000)\n\
             \x20   envelope = json.loads(FIRST if n == 1 else SECOND)\n\
             \x20   envelope['id'] = req['id']\n\
             \x20   print(json.dumps(envelope), flush=True)\n",
                hello = escape(&hello()),
                first = escape(PAGE_ONE_ANSWER),
                second_answer = escape(answer),
            ),
        ],
        cwd: std::env::temp_dir(),
        env: vec![("TIDAL2YTM_LOG_LEVEL".to_string(), "INFO".to_string())],
        handshake_timeout: Duration::from_secs(20),
    }
}

/// A spawned stub the tests can hand to a job or await directly.
///
/// `pause_ms` delays the stub's second *answer*, not its receipt of the
/// request, so a test that cancels between pages still sees the request arrive
/// — the pause is only there so the answer, when it eventually comes, does so
/// after every decision about it has been made.
pub async fn page_stub(second: SecondPage, pause_ms: u64) -> Arc<SidecarClient> {
    let config = favorites_config(&second, pause_ms);
    Arc::new(
        SidecarClient::spawn(&config)
            .await
            .expect("the stub sidecar starts"),
    )
}

/// One test's own database, migrated, in a directory nothing else uses.
///
/// A directory rather than a shared in-memory name for the reason
/// `lib.rs`'s `scratch_dir` comment says: two tests running in parallel cannot
/// collide on a name that carries their own label. The caller removes the
/// directory when it is done with the store.
pub fn temp_store(label: &str) -> (Store, PathBuf) {
    let dir = std::env::temp_dir().join(format!(
        "tidal2ytm-host-jobs-{label}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("the clock is past the epoch")
            .as_nanos()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("the scratch directory is creatable");
    let store = Store::open(&dir.join("tidal2ytm.db")).expect("the store opens");
    store.migrate().expect("the store migrates");
    (store, dir)
}
