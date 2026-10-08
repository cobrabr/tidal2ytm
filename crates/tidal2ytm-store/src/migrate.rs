//! Applying the schema, resumably and idempotently.
//!
//! `PRAGMA user_version` is the whole state of this module: it is a single
//! integer in the database header, so there is no second place to record which
//! migrations ran and nothing to fall out of sync with the tables themselves.

use crate::schema::MIGRATIONS;
use crate::store::StoreError;

/// Apply every migration above the connection's current `PRAGMA user_version`.
///
/// Each migration is its own transaction, and the version bump travels inside
/// it. That pairing is what makes a failure partway resumable: the steps that
/// did apply stay applied *and stay recorded*, so the next run picks up at the
/// first one that did not rather than re-running everything or skipping a gap.
pub async fn migrate(conn: &libsql::Connection) -> Result<(), StoreError> {
    let current = read_user_version(conn).await?;
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
        let tx = conn.transaction().await?;
        tx.execute_batch(ddl).await?;
        // `PRAGMA user_version` does not accept a bound parameter, so the
        // number is formatted into the statement text. It comes from an array
        // index rather than from input, so there is nothing here to inject.
        //
        // The bump is inside the transaction on purpose. libsql's `Transaction`
        // rolls back when it is dropped — `DropBehavior::Rollback` is the
        // default — so the explicit `commit()` below is mandatory and its
        // absence discards the DDL and the version together, silently, with no
        // error to notice. Writing the bump outside the transaction instead
        // would leave the opposite failure: a database that records itself as
        // current when the migration that should have run never landed.
        tx.execute(&format!("PRAGMA user_version = {version}"), ())
            .await?;
        tx.commit().await?;
    }
    Ok(())
}

/// Read `PRAGMA user_version`.
///
/// A `PRAGMA` that sets nothing always returns exactly one row, so a missing
/// one would mean the statement did not run, and treating that as version 0
/// would silently re-run every migration over a database that had them.
pub(crate) async fn read_user_version(conn: &libsql::Connection) -> Result<i64, StoreError> {
    let mut rows = conn.query("PRAGMA user_version", ()).await?;
    let row = rows
        .next()
        .await?
        .ok_or(libsql::Error::QueryReturnedNoRows)?;
    Ok(row.get::<i64>(0)?)
}
