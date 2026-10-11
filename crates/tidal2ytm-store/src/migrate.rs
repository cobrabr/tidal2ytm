//! Applying the schema, resumably and idempotently.
//!
//! `PRAGMA user_version` is the whole state of this module: it is a single
//! integer in the database header, so there is no second place to record which
//! migrations ran and nothing to fall out of sync with the tables themselves.

use rusqlite::Connection;

use crate::schema::MIGRATIONS;
use crate::store::StoreError;

/// Apply every migration above the connection's current `PRAGMA user_version`.
///
/// Each migration is its own transaction, and the version bump travels inside
/// it. That pairing is what makes a failure partway resumable: the steps that
/// did apply stay applied *and stay recorded*, so the next run picks up at the
/// first one that did not rather than re-running everything or skipping a gap.
pub fn migrate(conn: &mut Connection) -> Result<(), StoreError> {
    let current = read_user_version(conn)?;
    let known = MIGRATIONS.len() as i64;
    if current > known {
        // A newer build wrote this file. Continuing would apply this build's
        // idea of the schema on top of a schema it has never seen, and the
        // failure would surface much later as a missing column. Naming both
        // versions is the difference between a five-second diagnosis and an
        // afternoon.
        return Err(StoreError::UnknownSchemaVersion {
            found: current,
            known,
        });
    }

    for (index, ddl) in MIGRATIONS.iter().enumerate() {
        let version = index as i64 + 1;
        if version <= current {
            continue;
        }
        let tx = conn.transaction()?;
        tx.execute_batch(ddl)?;
        // The bump is inside the transaction on purpose. rusqlite's
        // `Transaction` rolls back when it is dropped, so the explicit
        // `commit()` below is mandatory and its absence discards the DDL and
        // the version together, silently, with no error to notice. Writing the
        // bump outside the transaction instead would leave the opposite
        // failure: a database that records itself as current when the migration
        // that should have run never landed.
        //
        // `PRAGMA user_version` does not accept a bound parameter, so the
        // number is formatted into the statement text. It comes from an array
        // index rather than from input, so there is nothing here to inject.
        //
        // `execute_batch` rather than `execute` because
        // `PRAGMA user_version = N` returns the new value as a row, and
        // `execute` rejects a statement that produced rows.
        tx.execute_batch(&format!("PRAGMA user_version = {version}"))?;
        tx.commit()?;
    }
    Ok(())
}

/// Read `PRAGMA user_version`.
///
/// A `PRAGMA` that sets nothing always returns exactly one row, so a missing
/// one would mean the statement did not run, and treating that as version 0
/// would silently re-run every migration over a database that had them.
/// `query_row` reports the empty result as `QueryReturnedNoRows`, which is that
/// error rather than a default.
pub(crate) fn read_user_version(conn: &Connection) -> Result<i64, StoreError> {
    Ok(conn.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))?)
}
