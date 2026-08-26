"""Schema v59: jobs(run_id) index for run-scoped job lookups.

The batch-queue intake upsert's not-exists guard, ``count_jobs_in_run`` and
the requeue-depleted subqueries all filter jobs by ``run_id`` on every run
create/poll; without the index each is a seq scan over the whole jobs table
(130k-260k rows on a busy instance). DDL-only migration, so the registry test
only pins SCHEMA_VERSION, its recorded name, and the index-backed query plan
— replacing the pin previously held by
tests/db/test_retire_global_register_tokens_migration.py.
"""

from __future__ import annotations

from server.app.db.schema import SCHEMA_VERSION
from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def test_schema_version_pin() -> None:
    # The latest-migration record pin moved here from
    # test_retire_global_register_tokens_migration.py (v58 → v59).
    assert SCHEMA_VERSION == 59
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "jobs_run_id_index"


def test_jobs_run_id_index_exists_and_serves_lookups() -> None:
    """Run-scoped job lookups must be index-served, not a seq scan."""
    with read_connection(TEST_DATABASE_URL) as conn:
        conn.execute("set enable_seqscan=off")
        rows = conn.execute("explain select 1 from jobs where run_id = 'run-x'").fetchall()
    plan = "\n".join(str(row["QUERY PLAN"]) for row in rows)
    assert "idx_jobs_run_id" in plan
