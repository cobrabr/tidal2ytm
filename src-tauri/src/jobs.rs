//! The job manager: ids, cancellation and final state for work that outlives
//! the request that started it.
//!
//! The manager holds no Tauri type. `start_sync` takes an emit callback rather
//! than an event-web handle, so `jobs.rs` needs no `tauri::App` in its tests —
//! the same rule `counts_by_name` follows in `commands.rs` — and the commands
//! remain the one place payloads meet the wire.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use serde::Serialize;
use tidal2ytm_ipc::SidecarClient;
use tidal2ytm_store::Store;

use crate::sync;

/// The only job kind this phase carries. The count already says what it
/// counts: a page of favourites, so progress carries no label and no track
/// name beside it.
pub const KIND: &str = "sync";

/// Where one job stands.
///
/// The serde names are the five literals the frontend sees on the
/// `job-progress` event; snake_case matches the auth status's own casing, so
/// one reading of a wire payload covers both. `Copy` because a state is a
/// value every listener keeps its own copy of, and manager and listener both
/// hold one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Running,
    Cancelling,
    Cancelled,
    Done,
    Failed,
}

impl JobState {
    /// Whether nothing about the job can move it again. A cancel request for a
    /// job in one of these states is a no-op — the next page it could stop
    /// ahead of was already its last, or its work never started for failure.
    fn is_terminal(self) -> bool {
        matches!(self, Self::Cancelled | Self::Done | Self::Failed)
    }
}

/// One job's progress, streamed on events.
///
/// `done` counts what is committed — the pages, for the kind this phase has —
/// and `total` is known only on the final page, because a paginated walk does
/// not know its page count until the short page arrives.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Progress {
    pub jid: u64,
    pub kind: &'static str,
    pub state: JobState,
    pub done: i64,
    pub total: Option<i64>,
}

/// One job's bookkeeping, kept so long as the process runs.
///
/// The state the UI sees and the state the map answers both come from the
/// loop's own emissions, which is why the two can never disagree about a
/// finished job; the cancel request's `Cancelling` is the only state written
/// from outside the loop, and the loop's *next* boundary is always the state
/// it answers the request with. Cancelling is revisable, as its pending
/// request is; the terminal states are the loop's answer and named here so the
/// cancel decision can respect them at one place.
struct JobEntry {
    state: Mutex<JobState>,
    cancel: Arc<AtomicBool>,
}

/// Runs background jobs and answers for them while they run.
pub struct JobManager {
    next_jid: AtomicU64,
    jobs: Mutex<HashMap<u64, Arc<JobEntry>>>,
}

impl JobManager {
    #[must_use]
    pub fn new() -> Self {
        Self {
            next_jid: AtomicU64::new(1),
            jobs: Mutex::new(HashMap::new()),
        }
    }

    /// Start one sync on its own thread and answer at once: the command
    /// returns the id and the progress arrives on events from the thread.
    ///
    /// The thread carries its own single-threaded runtime rather than running
    /// on Tauri's: the job's store pages hold a `std::sync::Mutex` for the
    /// length of a transaction, which is where a blocking lock has no right to
    /// sit on another caller's command being dispatched.
    pub fn start_sync(
        &self,
        client: Arc<SidecarClient>,
        store: Arc<Store>,
        emit: impl Fn(Progress) + Send + 'static,
    ) -> Result<u64, String> {
        let jid = self.next_jid.fetch_add(1, Ordering::Relaxed);
        let entry = Arc::new(JobEntry {
            state: Mutex::new(JobState::Running),
            cancel: Arc::new(AtomicBool::new(false)),
        });
        self.jobs().insert(jid, entry.clone());
        let spawned = std::thread::Builder::new()
            .name(format!("tidal2ytm-sync-{jid}"))
            .spawn(move || worker(jid, entry, client, store, emit));
        match spawned {
            Ok(_) => Ok(jid),
            Err(error) => {
                self.jobs().remove(&jid);
                Err(format!("could not start the sync worker thread: {error}"))
            }
        }
    }

    /// Ask a job to stop, and hear whether the request could be accepted:
    /// `true` flaps the flag the loop reads at its next page
    /// boundary; `false` says the job was never issued or has already ended,
    /// and nothing exists to stop.
    ///
    /// The flag, not the state, is the write made here: the state's move to
    /// `Cancelled` belongs to the loop's own write, so what the listener sees
    /// and what the map holds are the same emission.
    #[must_use]
    pub fn cancel(&self, jid: u64) -> bool {
        let jobs = self.jobs();
        let Some(entry) = jobs.get(&jid) else {
            return false;
        };
        let mut state = entry.state.lock().unwrap_or_else(PoisonError::into_inner);
        if state.is_terminal() {
            return false;
        }
        entry.cancel.store(true, Ordering::Relaxed);
        *state = JobState::Cancelling;
        true
    }

    /// The state the job last reported, for callers that missed the events.
    #[must_use]
    pub fn state(&self, jid: u64) -> Option<JobState> {
        self.jobs()
            .get(&jid)
            .map(|entry| *entry.state.lock().unwrap_or_else(PoisonError::into_inner))
    }

    fn jobs(&self) -> MutexGuard<'_, HashMap<u64, Arc<JobEntry>>> {
        self.jobs.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

impl Default for JobManager {
    fn default() -> Self {
        Self::new()
    }
}

/// Fold every state the loop emitted into the job's own bookkeeping.
///
/// Guards, in one place: a terminal state is never moved again, because the
/// loop only ever emits an event after the state it names, and a later backstop
/// cannot contradict it; and a `Cancelling` must not be bent back to a
/// `Running` by a page's event that was already in flight, where the request
/// that flipped it may arrive a hair after the commit while the loop has not
/// reached the next boundary yet.
fn mirror(entry: &JobEntry, incoming: JobState) {
    let mut state = entry.state.lock().unwrap_or_else(PoisonError::into_inner);
    if state.is_terminal() {
        return;
    }
    if *state == JobState::Cancelling && incoming == JobState::Running {
        return;
    }
    *state = incoming;
}

/// Run one sync, mirror its emissions, and name the failures it could not
/// fail itself.
///
/// The backstop is for the paths the loop cannot express: a worker that could
/// not build its runtime, and a panic. Both still end the job in the state the
/// frontend can render, rather than leaving a `Running` nothing will ever
/// finish.
fn worker(
    jid: u64,
    entry: Arc<JobEntry>,
    client: Arc<SidecarClient>,
    store: Arc<Store>,
    emit: impl Fn(Progress) + Send + 'static,
) {
    let drive = || {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|error| format!("could not build the sync worker's runtime: {error}"))?;
        runtime.block_on(sync::run(
            &client,
            &store,
            jid,
            &|progress: Progress| {
                mirror(&entry, progress.state);
                emit(progress);
            },
            &entry.cancel,
        ))
    };
    // The catch is the one way a worker that panicked can still record where
    // the job should have said something. `AssertUnwindSafe` because the
    // closures own the process's own state, which by construction they drop
    // cleanly when the unwind happens.
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(drive));
    match outcome {
        Ok(Ok(())) => {}
        Ok(Err(error)) => {
            log::warn!("sync job {jid} failed: {error}");
            mirror(&entry, JobState::Failed);
        }
        Err(_) => {
            log::error!("sync job {jid} panicked and is being reported as failed");
            mirror(&entry, JobState::Failed);
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::mpsc;
    use std::time::Duration;

    use super::*;
    use crate::stub;
    use crate::sync;
    use tidal2ytm_store::Store;

    /// How much of the library the stub's first (incomplete) page carries.
    const PAGE_ONE_ROWS: i64 = 2;
    /// Every page together: the library the stub's session describes.
    const EVERY_ROW: i64 = 3;

    /// How many rows the store holds, summed over the statuses. The count the
    /// carried Review Focus case turns on: after a lapse the first page is
    /// still committed, so a partial mirror remains a mirror.
    #[must_use]
    fn track_rows(store: &Store) -> i64 {
        store
            .status_counts()
            .expect("the counts are read")
            .values()
            .sum()
    }

    /// Every progress event the job still has coming, or panic on a hang.
    ///
    /// The worker thread drops its sender copy when it exits, so the test sees
    /// end-of-stream as a natural return: an event the loop should have sent
    /// but never did shows up here as whatever arrived last, and a wedged
    /// worker as the timeout this helper waits no longer than.
    ///
    /// The channel read runs on the blocking pool rather than on the test's
    /// runtime thread, which is otherwise the thread its runtime — the one
    /// carrying the stub's reader tasks — would have no time for ever again.
    async fn drain(rx: mpsc::Receiver<Progress>) -> Vec<Progress> {
        tokio::task::spawn_blocking(move || {
            let mut seen = Vec::new();
            loop {
                match rx.recv_timeout(Duration::from_secs(10)) {
                    Ok(progress) => seen.push(progress),
                    Err(mpsc::RecvTimeoutError::Disconnected) => return seen,
                    Err(mpsc::RecvTimeoutError::Timeout) => {
                        panic!("the sync stopped streaming progress after {seen:?}")
                    }
                }
            }
        })
        .await
        .expect("the drainer task always runs")
    }

    /// The first progress event, with the cancel request dispatched the moment
    /// it is seen — the fixed point between the worker being parked inside its
    /// first progress callback and its next boundary check. The receiver comes
    /// back for the drain that follows.
    async fn first_event_then_cancel(
        rx: mpsc::Receiver<Progress>,
        manager: Arc<JobManager>,
        jid: u64,
    ) -> (mpsc::Receiver<Progress>, Progress, bool) {
        tokio::task::spawn_blocking(move || {
            let first = rx
                .recv_timeout(Duration::from_secs(10))
                .expect("the first page's progress arrives");
            let accepted = manager.cancel(jid);
            (rx, first, accepted)
        })
        .await
        .expect("the drainer task always runs")
    }

    /// The terminal event of whatever was sent, named for the claim made on it.
    fn final_event(events: &[Progress]) -> &Progress {
        events.last().expect("the sync sent no progress at all")
    }

    /// The one tuple a progress event reports, so every expectation below
    /// compares state, count and total in one place.
    #[must_use]
    fn reported(progress: &Progress) -> (JobState, i64, Option<i64>) {
        (progress.state, progress.done, progress.total)
    }

    /// The defect this catches: a loop that accumulates every page and commits
    /// once at the end commits nothing until the library has fully crossed the
    /// wire, so dying on the last page loses the whole first pages' work. The
    /// row count is read *inside* the progress callback — which runs between
    /// pages — and is compared with what each event claims was committed.
    #[tokio::test]
    async fn the_second_page_is_requested_only_after_the_first_page_was_committed() {
        let (store, dir) = stub::temp_store("paged");
        let store = Arc::new(store);
        let client = stub::page_stub(stub::SecondPage::More, 0).await;
        let manager = JobManager::new();

        let counts_at_emit: Arc<std::sync::Mutex<Vec<i64>>> = Arc::default();
        let rows_witness = Arc::clone(&counts_at_emit);
        let store_at_emit = Arc::clone(&store);
        let (tx, rx) = mpsc::channel::<Progress>();
        let jid = manager
            .start_sync(Arc::clone(&client), Arc::clone(&store), move |progress| {
                rows_witness
                    .lock()
                    .expect("the counts are not contested")
                    .push(track_rows(&store_at_emit));
                tx.send(progress).expect("the test still drains progress");
            })
            .expect("the sync starts");

        let events = drain(rx).await;
        assert_eq!(
            events.iter().map(reported).collect::<Vec<_>>(),
            vec![
                (JobState::Running, PAGE_ONE_ROWS, None),
                (JobState::Running, EVERY_ROW, Some(EVERY_ROW)),
                (JobState::Done, EVERY_ROW, Some(EVERY_ROW)),
            ],
            "each committed page must report itself before the next request, with the final page's total"
        );
        // What makes the pair of counts the proof: after the first page's
        // event the store holds only that page's rows, and not the library an
        // accumulate-then-commit loop would still be holding.
        assert_eq!(
            *counts_at_emit.lock().expect("the counts are not contested"),
            vec![PAGE_ONE_ROWS, EVERY_ROW, EVERY_ROW],
            "the rows for each page must be committed by the time their own event is sent"
        );
        assert_eq!(
            manager.state(jid).expect("a started job stays in the map"),
            JobState::Done
        );

        client.shutdown().await.expect("the stub sidecar stops");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The carried Review Focus case: a Tidal lapse mid-sync is a failure, and
    /// no code path may map an external lapse into a cancellation. The job's
    /// first page survives it, and `sync_started_at` survives it — a key dealt
    /// with on every exit would make Home read a partial mirror as one that
    /// was never begun, so the stamp is cleared by success alone.
    #[tokio::test]
    async fn a_tidal_lapse_mid_sync_fails_the_job_and_keeps_its_first_page() {
        let (store, dir) = stub::temp_store("lapse");
        let store = Arc::new(store);
        let client = stub::page_stub(stub::SecondPage::AuthExpired, 0).await;
        let manager = JobManager::new();

        let (tx, rx) = mpsc::channel::<Progress>();
        let jid = manager
            .start_sync(Arc::clone(&client), Arc::clone(&store), move |progress| {
                tx.send(progress).expect("the test still drains progress");
            })
            .expect("the sync starts");

        let events = drain(rx).await;
        assert_eq!(
            reported(final_event(&events)),
            (JobState::Failed, PAGE_ONE_ROWS, None),
            "the lapse must end the job in failure, with the committed rows counted"
        );
        assert!(
            events
                .iter()
                .all(|event| event.state != JobState::Cancelled),
            "no code path may turn the lapse into a cancellation: {events:?}"
        );
        // The failed run's trace: the first page committed, the beginning
        // named, and no completion stamp beside them.
        assert_eq!(track_rows(&store), PAGE_ONE_ROWS);
        assert!(
            store
                .get_meta(sync::SYNC_STARTED_AT)
                .expect("the meta table is not contested")
                .is_some(),
            "a failed sync must leave its beginning stamped"
        );
        assert!(
            store
                .get_meta(sync::SYNC_LAST_COMPLETED_AT)
                .expect("the meta table is not contested")
                .is_none(),
            "a failed sync must not carry a completion stamp"
        );
        assert_eq!(
            manager.state(jid).expect("a started job stays in the map"),
            JobState::Failed
        );
        assert!(
            !manager.cancel(jid),
            "a job past its last boundary, whether by failure or by success, refuses the request"
        );

        client.shutdown().await.expect("the stub sidecar stops");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The ruling the primer names: `sync_started_at` is cleared only on
    /// success, and the instant of its clearing is also the one
    /// `sync_last_completed_at` is written at. Failure and cancellation are
    /// covered by the tests above; this is the one exit that erases the stamp
    /// they exist to preserve.
    #[tokio::test]
    async fn a_success_clears_the_starting_stamp_and_writes_the_completion_stamp() {
        let (store, dir) = stub::temp_store("success");
        let store = Arc::new(store);
        let client = stub::page_stub(stub::SecondPage::More, 0).await;
        let manager = JobManager::new();

        let (tx, rx) = mpsc::channel::<Progress>();
        let jid = manager
            .start_sync(Arc::clone(&client), Arc::clone(&store), move |progress| {
                tx.send(progress).expect("the test still drains progress");
            })
            .expect("the sync starts");

        assert_eq!(
            reported(final_event(&drain(rx).await)),
            (JobState::Done, EVERY_ROW, Some(EVERY_ROW))
        );
        assert!(
            store
                .get_meta(sync::SYNC_STARTED_AT)
                .expect("the meta table is not contested")
                .is_none(),
            "a completed sync must not still claim to have begun and not finished"
        );
        assert!(
            store
                .get_meta(sync::SYNC_LAST_COMPLETED_AT)
                .expect("the meta table is not contested")
                .is_some(),
            "a completed sync must stamp when it last completed"
        );
        assert_eq!(
            manager.state(jid).expect("a started job stays in the map"),
            JobState::Done
        );

        client.shutdown().await.expect("the stub sidecar stops");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Cancellation is cooperative: the request flags the job, the loop
    /// answers at its next page boundary — and no timing decides the test, so
    /// the request is delivered from *inside* the first page's progress
    /// callback, where the loop is known to be parked. The stub's pause on the
    /// second answer is for any variant that did not cancel on its first
    /// event, not for the one that did.
    #[tokio::test]
    async fn a_cancel_between_pages_ends_cancelled_and_keeps_the_starting_stamp() {
        let (store, dir) = stub::temp_store("cancel");
        let store = Arc::new(store);
        let client = stub::page_stub(stub::SecondPage::More, 1_500).await;
        let manager = Arc::new(JobManager::new());

        let (tx, rx) = mpsc::channel::<Progress>();
        let first_event_seen = Arc::new(AtomicBool::new(false));
        let parked_between_pages = Arc::clone(&first_event_seen);
        let jid = manager
            .start_sync(Arc::clone(&client), Arc::clone(&store), move |progress| {
                tx.send(progress).expect("the test still drains progress");
                if !parked_between_pages.swap(true, Ordering::SeqCst) {
                    // Park the loop in the callback long enough for the cancel
                    // request — sent from the test's own thread, once this
                    // event is seen — to be the fixed thing the loop's next
                    // boundary check reads.
                    std::thread::sleep(Duration::from_millis(50));
                }
            })
            .expect("the sync starts");

        let (rx, first, accepted) = first_event_then_cancel(rx, Arc::clone(&manager), jid).await;
        assert_eq!(
            first.state,
            JobState::Running,
            "the first page commits before the cancel lands"
        );
        assert!(accepted, "a running job accepts the cancel request");

        let events = drain(rx).await;
        assert_eq!(
            reported(final_event(&events)),
            (JobState::Cancelled, PAGE_ONE_ROWS, None),
            "the loop must answer the request itself, counting only what was committed before it"
        );
        assert_eq!(
            track_rows(&store),
            PAGE_ONE_ROWS,
            "the second page must never be committed"
        );
        assert!(
            store
                .get_meta(sync::SYNC_STARTED_AT)
                .expect("the meta table is not contested")
                .is_some(),
            "a cancelled sync leaves its beginning stamped, like a failed one"
        );
        assert!(
            store
                .get_meta(sync::SYNC_LAST_COMPLETED_AT)
                .expect("the meta table is not contested")
                .is_none(),
            "a cancelled sync must not carry a completion stamp"
        );
        assert_eq!(
            manager.state(jid).expect("a started job stays in the map"),
            JobState::Cancelled
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The cancellation contract's refusals: a job that already ended here by
    /// success (failure is covered by the lapse test above) has no page to
    /// stop at, and an id that was never issued names a job nothing can stop.
    #[tokio::test]
    async fn a_cancel_of_a_finished_or_unknown_job_is_refused() {
        let (store, dir) = stub::temp_store("cancel-refused");
        let store = Arc::new(store);
        let client = stub::page_stub(stub::SecondPage::More, 0).await;
        let manager = JobManager::new();

        let (tx, rx) = mpsc::channel::<Progress>();
        let jid = manager
            .start_sync(Arc::clone(&client), Arc::clone(&store), move |progress| {
                tx.send(progress).expect("the test still drains progress");
            })
            .expect("the sync starts");

        assert_eq!(
            reported(final_event(&drain(rx).await)),
            (JobState::Done, EVERY_ROW, Some(EVERY_ROW))
        );
        assert!(
            !manager.cancel(jid),
            "a finished job must refuse a cancel request"
        );
        assert!(
            !manager.cancel(9_999),
            "a job id that was never issued must refuse a cancel request"
        );
        assert_eq!(
            manager.state(jid).expect("a started job stays in the map"),
            JobState::Done,
            "a refused request must not have moved the state"
        );

        client.shutdown().await.expect("the stub sidecar stops");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
