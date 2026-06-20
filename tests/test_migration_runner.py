import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from server.app.db.connection import connect_sqlite
from server.app.db.migrations import (
    MIGRATIONS,
    Migration,
    MigrationError,
    MigrationHistoryError,
    MigrationRegistryError,
    run_migrations,
)
from server.app.db.schema import init_db


def test_migrations_run_in_ascending_version_order(tmp_path: Path) -> None:
    """Guarantee 1: registered migrations run in ascending version order."""
    path = tmp_path / "order.sqlite"
    conn = connect_sqlite(path)
    order: list[int] = []

    def make_migration(version: int, name: str) -> Migration:
        def apply(conn: sqlite3.Connection) -> None:
            order.append(version)
            conn.execute(f"create table t_{version} (id integer primary key)")

        return Migration(version=version, name=name, apply=apply)

    migrations = (
        make_migration(3, "third"),
        make_migration(1, "first"),
        make_migration(2, "second"),
    )

    run_migrations(conn, migrations)

    versions = [
        row["version"]
        for row in conn.execute("select version from schema_migrations order by version").fetchall()
    ]
    conn.close()

    assert order == [1, 2, 3]
    assert versions == [1, 2, 3]


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    """Guarantee 2: applying the registry twice is idempotent."""
    path = tmp_path / "idempotent.sqlite"
    conn = connect_sqlite(path)

    def apply(conn: sqlite3.Connection) -> None:
        conn.execute("create table if not exists stable (id integer primary key)")

    migrations = (Migration(version=1, name="stable", apply=apply),)

    run_migrations(conn, migrations)
    run_migrations(conn, migrations)

    versions = [
        row["version"]
        for row in conn.execute("select version from schema_migrations order by version").fetchall()
    ]
    count = conn.execute("select count(*) from stable").fetchone()[0]
    conn.close()

    assert versions == [1]
    assert count == 0


def test_duplicate_migration_versions_fail_before_running(tmp_path: Path) -> None:
    """Guarantee 3: duplicate migration versions fail before any migration runs."""
    path = tmp_path / "dup.sqlite"
    conn = connect_sqlite(path)
    ran: list[int] = []

    def apply(conn: sqlite3.Connection) -> None:
        ran.append(1)
        conn.execute("create table dup_table (id integer primary key)")

    migrations = (
        Migration(version=1, name="one", apply=apply),
        Migration(version=1, name="one_again", apply=apply),
    )

    with pytest.raises(MigrationRegistryError, match="duplicate migration versions"):
        run_migrations(conn, migrations)

    conn.close()
    assert ran == []


def test_failed_migration_rolls_back_and_does_not_record_version(tmp_path: Path) -> None:
    """Guarantee 4: a failed migration rolls back its changes and does not write history."""
    path = tmp_path / "rollback.sqlite"
    conn = connect_sqlite(path)

    def apply_ok(conn: sqlite3.Connection) -> None:
        conn.execute("create table ok_table (id integer primary key)")

    def apply_bad(conn: sqlite3.Connection) -> None:
        conn.execute("create table partial_table (id integer primary key)")
        raise RuntimeError("intentional failure")

    migrations = (
        Migration(version=1, name="ok", apply=apply_ok),
        Migration(version=2, name="bad", apply=apply_bad),
    )

    with pytest.raises(RuntimeError, match="intentional failure"):
        run_migrations(conn, migrations)

    conn.close()

    with closing(connect_sqlite(path)) as check, check:
        tables = {
            row["name"]
            for row in check.execute("select name from sqlite_master where type='table'")
        }
        versions = [
            row["version"]
            for row in check.execute(
                "select version from schema_migrations order by version"
            ).fetchall()
        ]

    assert "ok_table" in tables
    assert "partial_table" not in tables
    assert versions == [1]


def test_stored_version_with_different_name_raises_history_error(tmp_path: Path) -> None:
    """Guarantee 5: a stored version with a different name fails with a corruption error."""
    path = tmp_path / "history.sqlite"
    conn = connect_sqlite(path)
    conn.execute(
        """
        create table schema_migrations (
          version integer primary key,
          name text not null,
          applied_at text not null default current_timestamp
        )
        """
    )
    conn.execute("insert into schema_migrations(version, name) values (1, 'legacy_name')")
    conn.commit()
    conn.close()

    conn = connect_sqlite(path)

    def apply(conn: sqlite3.Connection) -> None:
        pass

    migrations = (Migration(version=1, name="new_name", apply=apply),)

    with pytest.raises(MigrationHistoryError, match="version 1"):
        run_migrations(conn, migrations)

    conn.close()


def test_runner_owns_transaction_per_migration(tmp_path: Path) -> None:
    """Guarantee 6: the runner owns one transaction per migration; callers need not begin one."""
    path = tmp_path / "transaction.sqlite"
    conn = connect_sqlite(path)

    assert not conn.in_transaction

    def apply(conn: sqlite3.Connection) -> None:
        conn.execute("create table tx_table (id integer primary key)")

    run_migrations(conn, (Migration(version=1, name="tx", apply=apply),))

    assert not conn.in_transaction
    versions = [
        row["version"]
        for row in conn.execute("select version from schema_migrations order by version").fetchall()
    ]
    conn.close()

    assert versions == [1]


def test_foreign_keys_restored_after_success(tmp_path: Path) -> None:
    """Guarantee 7a: foreign_keys is restored to ON after success."""
    path = tmp_path / "fk_success.sqlite"
    conn = connect_sqlite(path)
    conn.execute("pragma foreign_keys=OFF")

    def apply(conn: sqlite3.Connection) -> None:
        conn.execute("create table fk_ok (id integer primary key)")

    run_migrations(conn, (Migration(version=1, name="fk_ok", apply=apply),))

    assert conn.execute("pragma foreign_keys").fetchone()[0] == 1
    conn.close()


def test_foreign_keys_restored_after_failure(tmp_path: Path) -> None:
    """Guarantee 7b: foreign_keys is restored to ON after failure."""
    path = tmp_path / "fk_fail.sqlite"
    conn = connect_sqlite(path)
    conn.execute("pragma foreign_keys=OFF")

    def apply_bad(conn: sqlite3.Connection) -> None:
        conn.execute("create table fk_partial (id integer primary key)")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_migrations(conn, (Migration(version=1, name="fk_bad", apply=apply_bad),))

    assert conn.execute("pragma foreign_keys").fetchone()[0] == 1
    conn.close()


def test_rebuilds_fk_runs_foreign_key_check_before_commit(tmp_path: Path) -> None:
    """Migrations that rebuild FK tables are checked before the history row is written."""
    path = tmp_path / "fk_check.sqlite"
    conn = connect_sqlite(path)

    def apply_parent(conn: sqlite3.Connection) -> None:
        conn.execute("create table parent (id text primary key)")
        conn.execute(
            "create table child (id integer primary key, parent_id text references parent(id))"
        )

    def apply_orphan(conn: sqlite3.Connection) -> None:
        # Defer enforcement so the insert itself succeeds but the pre-commit
        # foreign key check discovers the violation.
        conn.execute("pragma defer_foreign_keys=ON")
        conn.execute("insert into child (parent_id) values ('missing')")

    migrations = (
        Migration(version=1, name="parent_child", apply=apply_parent, rebuilds_fk=True),
        Migration(version=2, name="orphan", apply=apply_orphan, rebuilds_fk=True),
    )

    with pytest.raises(MigrationError, match="foreign key check failed"):
        run_migrations(conn, migrations)

    versions = [
        row["version"]
        for row in conn.execute("select version from schema_migrations order by version").fetchall()
    ]
    conn.close()

    assert versions == [1]


def test_phase_hook_is_invoked_at_migration_boundaries(tmp_path: Path) -> None:
    """The internal _phase_hook is called around every applied migration."""
    path = tmp_path / "hook.sqlite"
    conn = connect_sqlite(path)

    def apply_one(conn: sqlite3.Connection) -> None:
        conn.execute("create table t_one (id integer primary key)")

    def apply_two(conn: sqlite3.Connection) -> None:
        conn.execute("create table t_two (id integer primary key)")

    phases: list[str] = []

    def hook(phase: str) -> None:
        phases.append(phase)

    run_migrations(
        conn,
        (
            Migration(version=1, name="one", apply=apply_one),
            Migration(version=2, name="two", apply=apply_two),
        ),
        _phase_hook=hook,
    )
    conn.close()

    assert phases == ["pre:one", "post:one", "pre:two", "post:two"]


def test_phase_hook_interruption_rolls_back_and_restores_foreign_keys(
    tmp_path: Path,
) -> None:
    """Raising inside a phase hook aborts the active migration and leaves FKs ON."""
    path = tmp_path / "hook_interrupt.sqlite"
    conn = connect_sqlite(path)

    def apply_ok(conn: sqlite3.Connection) -> None:
        conn.execute("create table ok_table (id integer primary key)")

    def apply_bad(conn: sqlite3.Connection) -> None:
        conn.execute("create table partial_table (id integer primary key)")

    def hook(phase: str) -> None:
        if phase == "pre:bad":
            raise RuntimeError("interrupted by hook")

    migrations = (
        Migration(version=1, name="ok", apply=apply_ok),
        Migration(version=2, name="bad", apply=apply_bad),
    )

    with pytest.raises(RuntimeError, match="interrupted by hook"):
        run_migrations(conn, migrations, _phase_hook=hook)

    assert conn.execute("pragma foreign_keys").fetchone()[0] == 1
    tables = {
        row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
    }
    versions = [
        row["version"]
        for row in conn.execute("select version from schema_migrations order by version").fetchall()
    ]
    conn.close()

    assert "ok_table" in tables
    assert "partial_table" not in tables
    assert versions == [1]


def test_v009_relative_path_storage_is_registered() -> None:
    """Migration version 9 is registered with the expected name."""
    by_version = {migration.version: migration for migration in MIGRATIONS}
    assert 9 in by_version
    assert by_version[9].name == "relative_path_storage"


def test_v009_relative_path_storage_is_recorded_on_fresh_database(tmp_path: Path) -> None:
    """A fresh database records the relative path storage rollout migration."""
    path = tmp_path / "fresh.sqlite"
    init_db(path)
    conn = connect_sqlite(path)

    row = conn.execute("select version, name from schema_migrations where version = 9").fetchone()
    conn.close()

    assert row is not None
    assert row["version"] == 9
    assert row["name"] == "relative_path_storage"


def test_v009_relative_path_storage_is_recorded_on_legacy_database(tmp_path: Path) -> None:
    """A legacy database already at V8 records V9 after running migrations."""
    path = tmp_path / "legacy.sqlite"
    conn = connect_sqlite(path)
    with conn:
        conn.executescript(
            """
            create table schema_migrations (
              version integer primary key,
              name text not null,
              applied_at text not null default current_timestamp
            );
            insert into schema_migrations(version, name) values (1, 'executor_core');
            insert into schema_migrations(version, name) values (2, 'executor_bootstrap_state');
            insert into schema_migrations(version, name) values (3, 'legacy_columns');
            insert into schema_migrations(version, name) values (4, 'workspace_dag_foreign_keys');
            insert into schema_migrations(version, name) values (6, 'job_execution_control');
            insert into schema_migrations(version, name) values (7, 'rename_pipeline_to_workflow');
            insert into schema_migrations(version, name) values (8, 'job_node_created_at');
            """
        )

    run_migrations(conn, MIGRATIONS)

    row = conn.execute("select version, name from schema_migrations where version = 9").fetchone()
    conn.close()

    assert row is not None
    assert row["version"] == 9
    assert row["name"] == "relative_path_storage"


def test_v011_skips_rebuild_when_schema_is_incomplete(tmp_path: Path) -> None:
    """V011 does not partially rebuild tables when a dependent table is incomplete."""
    path = tmp_path / "v011_partial.sqlite"
    conn = connect_sqlite(path)
    with conn:
        conn.executescript(
            """
            create table schema_migrations (
              version integer primary key,
              name text not null,
              applied_at text not null default current_timestamp
            );
            insert into schema_migrations(version, name) values (1, 'executor_core');
            insert into schema_migrations(version, name) values (2, 'executor_bootstrap_state');
            insert into schema_migrations(version, name) values (3, 'legacy_columns');
            insert into schema_migrations(version, name) values (4, 'workspace_dag_foreign_keys');
            insert into schema_migrations(version, name) values (6, 'job_execution_control');
            insert into schema_migrations(version, name) values (7, 'rename_pipeline_to_workflow');
            insert into schema_migrations(version, name) values (8, 'job_node_created_at');

            create table workspaces (
              id text primary key,
              name text not null,
              description text not null default '',
              default_workflow_key text not null default 'question_content',
              cms_config_json text not null default '{}',
              resource_config_json text not null default '{}',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp,
              default_entity text not null default 'question',
              intake_config_json text not null default '{}'
            );
            insert into workspaces(id, name) values ('math', 'Math');

            create table job_batches (
              id text primary key,
              workspace_id text not null default 'default',
              workflow_key text not null,
              source_kind text not null,
              source_payload_json text not null default '{}',
              status text not null default 'created',
              created_count integer not null default 0,
              error_message text not null default '',
              created_at text not null default current_timestamp,
              foreign key(workspace_id) references workspaces(id) on delete cascade
            );

            create table jobs (
              id text primary key,
              workspace_id text not null default 'default',
              workflow_key text not null,
              source_type text not null,
              source_id text not null,
              batch_id text not null default '',
              title text not null default '',
              status text not null default 'queued',
              storage_dir text not null default '',
              error_message text not null default '',
              stem text not null default '',
              created_at text not null default current_timestamp,
              updated_at text not null default current_timestamp,
              execution_mode text not null default 'full',
              target_node_key text,
              execution_paused integer not null default 0,
              pause_reason text not null default '',
              foreign key(workspace_id) references workspaces(id) on delete cascade
            );

            create table job_nodes (
              id integer primary key autoincrement,
              job_id text not null,
              node_key text not null,
              status text not null default 'pending'
            );

            insert into job_batches(id, workspace_id, workflow_key, source_kind)
            values ('b1', 'math', 'question_content', 'direct_ids');
            insert into jobs(id, workspace_id, workflow_key, source_type, source_id, batch_id)
            values ('j1', 'math', 'question_content', 'question', 'Q1', 'b1');
            insert into job_nodes(job_id, node_key) values ('j1', 'fetch_question_context');
            """
        )

    run_migrations(conn, MIGRATIONS)

    # job_nodes was incomplete (missing stale_reason, error_message, started_at,
    # finished_at, created_at), so V011 should skip the whole rebuild group.
    # The job_node row must survive and there must be no dangling old tables.
    assert conn.execute("select count(*) from job_nodes").fetchone()[0] == 1
    assert conn.execute("select job_id from job_nodes").fetchone()[0] == "j1"
    assert (
        conn.execute("select 1 from sqlite_master where name like '%__v004_old%'").fetchone()
        is None
    )
    fk_violations = conn.execute("pragma foreign_key_check").fetchall()
    assert fk_violations == []
    conn.close()
