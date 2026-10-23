//! The sync job: the loop that turns Tidal favourites into store rows.
//!
//! One favourites page is one fetch, one transaction and one progress event.
//! The job owns the page loop because the unit of work a cancel or a progress
//! event addresses is a page: nothing inside the page consults the cancel
//! flag, so a committed page is a page and a page held open is one write.
//!
//! Sync never deletes. The loop upserts only — artist, album, track in the
//! foreign-key order, page by page — and a favourite this walk does not repeat
//! keeps its row. Absence from a paginated fetch is not proof of absence from
//! Tidal, and a delete would also erase the match state a transferred or
//! reviewed row holds. A delete may only ever run against a fetch that
//! completed every page, in one transaction at the end, and never over a
//! `transferred` or `skip` row; that guard does not exist yet, so there is
//! deliberately no delete superseded by `upsert_track_source`'s write in this
//! file at all.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};
use tidal2ytm_core::model::SourceTrack;
use tidal2ytm_ipc::SidecarClient;
use tidal2ytm_store::{SourceRow, Store};

use crate::jobs::{JobState, KIND, Progress};

/// How many favourites one request carries. The op validates `offset` and
/// `limit` itself; the page size is the caller's, so the bound lives with the
/// loop that walks the pages.
pub const PAGE_LIMIT: i64 = 100;

/// The `meta` keys the sync owns. `sync_started_at` set and uncleared reads as
/// "a sync began and did not finish" — it is set before the first request and
/// cleared by success alone, which is why a failure, a cancellation or a crash
/// still leaves Home able to say interrupted without asking Tidal anything.
pub const SYNC_STARTED_AT: &str = "sync_started_at";
/// Written beside the clearing of `sync_started_at`, at the same moment: the
/// two reads a Home can make about the last attempt.
pub const SYNC_LAST_COMPLETED_AT: &str = "sync_last_completed_at";

/// One round trip per page, bounded so a wedged sidecar cannot hang the job
/// between checks. Generous: the op includes a Tidal token refresh, and its
/// expiry answer travels the same failure path every other page failure does.
const PAGE_TIMEOUT: Duration = Duration::from_secs(60);

/// The wire shape of one `favorites.list` answer.
///
/// `SourceTrack` is the sidecar's own vocabulary for a track — the eleven keys
/// `contracts/favorites_list.json` records — so the wire names and these
/// binding names are one set, not two copies.
#[derive(Debug, serde::Deserialize)]
struct FavoritesPage {
    tracks: Vec<SourceTrack>,
    page_complete: bool,
}

/// Walk the Tidal favourites into the store, emitting progress per page.
///
/// The caller owns the framing: `emit` carries every state change including
/// the terminal one, and `cancel` is the flag the manager set on the request.
/// The loop consults the flag only at page boundaries — before each request
/// and after each commit — so a cancel request never interrupts a page's
/// write, and a cancel can only be honoured at the boundary.
///
/// On success the completion stamps are written: `sync_last_completed_at` is
/// set first, then `sync_started_at` is cleared, so a crash between the two
/// leaves both reads saying "the tracks are committed" rather than "it began
/// and never completed". On a failure or a cancellation neither stamp moves:
/// the failed job's report says Failed and the stamped beginning keeps
/// "interrupted" readable after this process is gone.
pub async fn run(
    client: &SidecarClient,
    store: &Store,
    jid: u64,
    emit: &impl Fn(Progress),
    cancel: &Arc<AtomicBool>,
) -> Result<(), String> {
    // Before the first request, so even a crash between here and the first
    // commit names the sync that died. Nothing clears it but the completion
    // below; that asymmetry is what the key's meaning is.
    if let Err(error) = store.set_meta(SYNC_STARTED_AT, &now_stamp()) {
        return Err(report_failure(
            emit,
            jid,
            0,
            format!("the sync could not record that it began: {error}"),
        ));
    }

    let mut offset = 0_i64;
    let mut done = 0_i64;
    loop {
        // The boundary before the request.
        if cancel.load(Ordering::Relaxed) {
            emit(cancelled(jid, done));
            return Ok(());
        }

        let answer = client
            .call(
                "favorites.list",
                json!({ "offset": offset, "limit": PAGE_LIMIT }),
                Some(PAGE_TIMEOUT),
            )
            .await;
        let answer = match answer {
            Ok(answer) => answer,
            Err(error) => {
                return Err(report_failure(
                    emit,
                    jid,
                    done,
                    format!("the sync could not read a page of favourites: {error}"),
                ));
            }
        };
        let page = match read_page(answer) {
            Ok(page) => page,
            Err(message) => return Err(report_failure(emit, jid, done, message)),
        };
        let rows: Vec<SourceRow> = page.tracks.iter().map(source_row).collect();
        if let Err(error) = store.upsert_source_page(&rows) {
            // Nothing of this page landed: the transaction rolled back but
            // every earlier page did land, so this failure is reported with
            // the rows the store actually holds.
            return Err(report_failure(
                emit,
                jid,
                done,
                format!("the sync could not commit its page: {error}"),
            ));
        }

        done += rows.len() as i64;
        offset += rows.len() as i64;
        emit(Progress {
            jid,
            kind: KIND,
            state: JobState::Running,
            done,
            total: page.page_complete.then_some(done),
        });

        // The boundary after the commit. On the last page it matters: a cancel
        // request the job accepted here is answered by Cancelled, not by a
        // completion the job never got to stamp.
        if cancel.load(Ordering::Relaxed) {
            emit(cancelled(jid, done));
            return Ok(());
        }
        if page.page_complete {
            break;
        }
    }

    // The two writes are separate statements, and their order is chosen for
    // the read that comes after a crash between them: a completion stamp
    // without the cleared beginning states done work; a cleared beginning
    // without a completion stamp would erase the success entirely.
    let finished = now_stamp();
    if let Err(error) = store.set_meta(SYNC_LAST_COMPLETED_AT, &finished) {
        return Err(report_failure(
            emit,
            jid,
            done,
            format!("the sync completed but could not record when: {error}"),
        ));
    }
    if let Err(error) = store.delete_meta(SYNC_STARTED_AT) {
        return Err(report_failure(
            emit,
            jid,
            done,
            format!("the sync completed but could not clear that it began: {error}"),
        ));
    }
    emit(Progress {
        jid,
        kind: KIND,
        state: JobState::Done,
        done,
        total: Some(done),
    });
    Ok(())
}

/// The terminal event of a cancel request the loop honoured: the committed
/// rows are what they are, and no total is claimed for a walk that stopped.
fn cancelled(jid: u64, done: i64) -> Progress {
    Progress {
        jid,
        kind: KIND,
        state: JobState::Cancelled,
        done,
        total: None,
    }
}

/// Emit the failure and hand back the message the worker logs, so the two
/// places a reader learns the reason are one wording.
fn report_failure(emit: &impl Fn(Progress), jid: u64, done: i64, message: String) -> String {
    emit(Progress {
        jid,
        kind: KIND,
        state: JobState::Failed,
        done,
        total: None,
    });
    message
}

/// Read the op's answer as a page, naming the wording every other message
/// about the wire already uses.
fn read_page(answer: Value) -> Result<FavoritesPage, String> {
    serde_json::from_value::<FavoritesPage>(answer)
        .map_err(|error| format!("the sidecar returned a page this build cannot read: {error}"))
}

/// One wire track as the store's write takes it.
///
/// The wire's `artists` list and Tidal-side `album_id` are dropped rather than
/// forwarded: the store identifies artist and album rows by normalized name —
/// which is the identity `upsert_track`'s callers key on too — and the schema
/// has no column a Tidal album id could be written into.
fn source_row(track: &SourceTrack) -> SourceRow {
    SourceRow {
        tidal_id: track.tidal_id,
        title: track.title.clone(),
        artist: track.artist.clone(),
        album: track.album.clone(),
        album_year: track.album_year,
        duration_sec: track.duration_sec,
        track_num: track.track_num,
        disc_num: track.disc_num,
        version: track.version.clone(),
    }
}

/// The current moment as ISO-8601 UTC with seconds' resolution.
fn now_stamp() -> String {
    stamp(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("the clock is past the epoch")
            .as_secs() as i64,
    )
}

/// A moment count as ISO-8601 UTC, including moments before the epoch.
fn stamp(total_seconds: i64) -> String {
    let days = total_seconds.div_euclid(86_400);
    let day_seconds = total_seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        day_seconds / 3600,
        day_seconds % 3600 / 60,
        day_seconds % 60,
    )
}

/// A day count as the date it lands on, by Hinnant's civil_from_days.
///
/// Hand-held rather than a dependency: the timestamps this store carries are
/// the ones the CLI already writes with the standard library's own
/// formatting, so seconds-since-epoch to civil date is the whole need.
/// `div_euclid` and `rem_euclid` keep the decomposition correct over the
/// negative side the algorithm's own bookkeeping covers.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let day_of_era = (z - era * 146_097) as u64;
    let years_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * years_of_era + years_of_era / 4 - years_of_era / 100);
    let month_shift = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * month_shift + 2) / 5 + 1) as u32;
    let month = if month_shift < 10 {
        (month_shift + 3) as u32
    } else {
        (month_shift - 9) as u32
    };
    let year = years_of_era as i64 + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_wire_contract_parses_as_a_page() {
        let path = crate::stub::repo_root()
            .join("contracts")
            .join("favorites_list.json");
        let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {path:?}: {e}"));
        let envelope: Value = serde_json::from_str(&text).expect("the contract parses as JSON");

        let page: FavoritesPage = serde_json::from_value(envelope["data"].clone())
            .expect("the contract's data is a favorites.list answer");

        assert_eq!(page.tracks.len(), 2);
        assert!(page.page_complete);
        assert_eq!(page.tracks[0].album, "Lantern Season");
        assert_eq!(page.tracks[0].tidal_id, 174_839_201);
    }

    #[test]
    fn the_stamp_naming_case_round_trips_the_dates_it_was_written_for() {
        assert_eq!(stamp(0), "1970-01-01T00:00:00Z");
        assert_eq!(stamp(1_000_000_000), "2001-09-09T01:46:40Z");
    }

    #[test]
    fn the_stamp_decomposition_survives_a_leap_day_and_the_epoch_negative() {
        // A leap day and the day after it, and a moment before the epoch. The
        // composition is `now_stamp`'s whole content, so an incorrect piece
        // would show back as a timestamp Home cannot parse against its own.
        assert_eq!(stamp(1_709_164_800), "2024-02-29T00:00:00Z");
        assert_eq!(stamp(1_709_251_200), "2024-03-01T00:00:00Z");
        assert_eq!(stamp(-1), "1969-12-31T23:59:59Z");
    }
}
