"""Schema v59: index for run-scoped job lookups (batch-queue hot paths).

The intake upsert's not-exists guard, ``count_jobs_in_run`` and the
requeue-depleted subqueries all filter jobs by ``run_id`` on every run
create/poll; without the index each is a seq scan over the whole jobs table.

The DDL lives HERE, not in ``postgres_schema.sql``: init_db upgrades an older
database by replaying the full schema file BEFORE running the data migrations,
and a v52-shape database still names the column ``jobs.batch_id`` until
migrate_runs (v53) renames it — a schema-file entry would reference a column
that does not exist during the replay. As the v59 migration it runs at the
right point on every path: fresh databases (all migrations replay), v52-shape
upgrades (after the v53 rename), and v53–v58 upgrades (where migrate_runs is
already recorded and must not be the carrier — see
tests/db/test_jobs_run_id_index.py::test_v58_database_upgrades_gain_the_index).
"""

from __future__ import annotations

from typing import Any


def migrate_jobs_run_id_index(conn: Any) -> None:
    conn.execute("create index if not exists idx_jobs_run_id on jobs(run_id)")
