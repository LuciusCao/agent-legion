"""Schema v59: jobs(run_id) index for run-scoped job lookups.

The batch-queue intake upsert's not-exists guard, ``count_jobs_in_run`` and
the requeue-depleted subqueries all filter jobs by ``run_id`` on every run
create/poll; without the index each is a seq scan over the whole jobs table
(130k-260k rows on a busy instance).

The pin test replaces the one previously held by
tests/db/test_retire_global_register_tokens_migration.py (v58 → v59).
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
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


@pytest.mark.fresh_schema
def test_v58_database_upgrades_gain_the_index() -> None:
    """A database recorded at v58 (post-runs cutover, pre-index) must gain
    idx_jobs_run_id via init_db: the v53 migrate_runs does NOT re-run for it,
    so the index creation has to live in the v59 migration itself.

    fresh_schema: the test rewrites schema_migrations and re-runs init_db —
    DDL-level state that must not leak into the shared per-worker schema.
    """
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("drop index if exists idx_jobs_run_id")
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select to_regclass('idx_jobs_run_id') as reg").fetchone()
    assert row is not None and row["reg"] is not None, "v58→v59 upgrade must create idx_jobs_run_id"
