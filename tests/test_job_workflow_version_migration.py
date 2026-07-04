from contextlib import closing
from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.migrations.v017_job_workflow_version import MIGRATION


def test_v017_adds_and_backfills_job_workflow_version(tmp_path: Path) -> None:
    db_path = tmp_path / "pre_v017.sqlite"
    with closing(connect_sqlite(db_path)) as conn, conn:
        conn.executescript(
            """
            create table jobs (
              id text primary key,
              workflow_revision_id text
            );
            create table workflow_revisions (
              id text primary key,
              version integer not null
            );
            insert into workflow_revisions(id, version) values ('rev-3', 3);
            insert into jobs(id, workflow_revision_id) values ('job-1', 'rev-3');
            insert into jobs(id, workflow_revision_id) values ('job-2', 'missing');
            """
        )

        MIGRATION.apply(conn)

        columns = {row["name"] for row in conn.execute("pragma table_info(jobs)")}
        rows = {
            row["id"]: row["workflow_version"]
            for row in conn.execute("select id, workflow_version from jobs")
        }

    assert "workflow_version" in columns
    assert rows == {"job-1": 3, "job-2": None}


def test_v017_is_idempotent_when_column_already_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "v017_idempotent.sqlite"
    with closing(connect_sqlite(db_path)) as conn, conn:
        conn.executescript(
            """
            create table jobs (
              id text primary key,
              workflow_revision_id text,
              workflow_version integer
            );
            create table workflow_revisions (
              id text primary key,
              version integer not null
            );
            insert into workflow_revisions(id, version) values ('rev-4', 4);
            insert into jobs(id, workflow_revision_id, workflow_version)
              values ('job-1', 'rev-4', null);
            """
        )

        MIGRATION.apply(conn)
        row = conn.execute("select workflow_version from jobs where id='job-1'").fetchone()

    assert row["workflow_version"] == 4
