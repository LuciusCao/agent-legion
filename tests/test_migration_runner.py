import sqlite3
from pathlib import Path

import pytest

from server.app.db.connection import connect_sqlite
from server.app.db.migrations import (
    Migration,
    MigrationError,
    MigrationHistoryError,
    MigrationRegistryError,
    run_migrations,
)


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

    with connect_sqlite(path) as check:
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
