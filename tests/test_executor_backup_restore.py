"""Focused tests for SQLite backup creation and restore."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from server.app.executors.backup import (
    RestoreError,
    quiesce_sqlite_database,
    restore_sqlite_database,
)
from server.app.jobs import JobQueries


def test_restore_rejects_missing_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    db_path.write_text("not a database")
    with pytest.raises(RestoreError, match="backup not found"):
        restore_sqlite_database(tmp_path / "missing.sqlite", db_path)


def test_restore_rejects_missing_target(tmp_path: Path) -> None:
    backup_path = tmp_path / "backup.sqlite"
    with closing(sqlite3.connect(backup_path)) as conn:
        conn.execute("create table t (id integer primary key)")
    with pytest.raises(RestoreError, match="target database not found"):
        restore_sqlite_database(backup_path, tmp_path / "missing.sqlite")


def test_restore_rejects_active_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("pragma journal_mode=WAL")
    conn.execute("create table t (id integer primary key)")
    conn.execute("insert into t(id) values (1)")
    conn.commit()
    # Deliberately leave the connection open so the WAL is active.

    backup_path = tmp_path / "backup.sqlite"
    backup = sqlite3.connect(backup_path)
    conn.backup(backup)
    backup.close()

    with pytest.raises(RestoreError, match="active WAL"):
        restore_sqlite_database(backup_path, db_path)

    conn.close()


def _seed_database(db_path: Path) -> tuple[Path, str, str]:
    jobs_dir = db_path.parent / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir)
    workspace = queries.create_workspace(
        name="Original", default_workflow_key="question_comprehension_info"
    )
    workspace_id = str(workspace["id"])
    batch = queries.create_batch(
        workflow_key="question_comprehension_info",
        source_kind="mixed",
        source_payload={"ids": [1]},
        workspace_id=workspace_id,
    )
    job = queries.create_job(
        workflow_key="question_comprehension_info",
        source_type="question_id",
        source_id="Q1",
        batch_id=batch["id"],
        title="Original Job",
        node_keys=["fetch"],
        workspace_id=workspace_id,
    )
    return db_path, workspace_id, str(job["id"])


def test_restore_preserves_current_database_and_returns_rows(tmp_path: Path) -> None:
    db_path, workspace_id, job_id = _seed_database(tmp_path / "original.sqlite")

    # Make a backup via the production helper.
    backup_path = tmp_path / "backup.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
    finally:
        conn.close()

    # Quiesce so restore does not reject the active WAL.
    quiesce_sqlite_database(db_path)

    preserved, history = restore_sqlite_database(backup_path, db_path)

    assert preserved.is_file()
    assert preserved != db_path

    # Reopen and verify the restored database.
    restored = sqlite3.connect(db_path)
    restored.row_factory = sqlite3.Row
    try:
        assert restored.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert restored.execute("pragma foreign_key_check").fetchall() == []
        row = restored.execute("select id from workspaces where id = ?", (workspace_id,)).fetchone()
        assert row is not None
        row = restored.execute(
            "select id from jobs where workspace_id = ?", (workspace_id,)
        ).fetchone()
        assert row is not None
        assert (
            restored.execute("select 1 from job_nodes where job_id = ?", (job_id,)).fetchone()
            is not None
        )
        assert history
        assert any(row["version"] == 1 for row in history)
    finally:
        restored.close()


def test_restore_uses_same_directory_atomic_replace(tmp_path: Path) -> None:
    db_path, workspace_id, _job_id = _seed_database(tmp_path / "atomic.sqlite")
    backup_path = tmp_path / "backup.sqlite"

    conn = sqlite3.connect(db_path)
    try:
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
    finally:
        conn.close()

    quiesce_sqlite_database(db_path)

    preserved, _history = restore_sqlite_database(backup_path, db_path)

    # The restored database lives at the original path.
    assert db_path.is_file()
    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    try:
        row = check.execute("select id from workspaces where id = ?", (workspace_id,)).fetchone()
        assert row is not None
    finally:
        check.close()

    # The preserved original is a separate file in the same directory.
    assert preserved.parent == db_path.parent
    assert preserved.name != db_path.name
    assert preserved.is_file()


def test_restore_rejects_corrupt_backup_without_replacing_live_database(tmp_path: Path) -> None:
    db_path, workspace_id, _job_id = _seed_database(tmp_path / "live.sqlite")
    corrupt_backup = tmp_path / "corrupt.sqlite"
    corrupt_backup.write_text("not a sqlite database", encoding="utf-8")

    quiesce_sqlite_database(db_path)

    with pytest.raises(RestoreError, match="backup validation failed"):
        restore_sqlite_database(corrupt_backup, db_path)

    live = sqlite3.connect(db_path)
    try:
        assert live.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert (
            live.execute(
                "select count(*) from workspaces where id = ?", (workspace_id,)
            ).fetchone()[0]
            == 1
        )
    finally:
        live.close()


def test_restore_sidecar_cleanup_failure_leaves_live_database_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, workspace_id, _job_id = _seed_database(tmp_path / "live.sqlite")
    backup_path = tmp_path / "backup.sqlite"
    source = sqlite3.connect(db_path)
    try:
        backup = sqlite3.connect(backup_path)
        source.backup(backup)
        backup.close()
    finally:
        source.close()

    queries = JobQueries(db_path, tmp_path / "jobs")
    queries.update_workspace(workspace_id=workspace_id, name="Mutated")
    quiesce_sqlite_database(db_path)

    shm_path = db_path.with_name(f"{db_path.name}-shm")
    shm_path.write_bytes(b"stale sidecar")
    original_unlink = Path.unlink

    def fail_sidecar_unlink(path: Path, *args, **kwargs) -> None:
        if path == shm_path:
            raise OSError("sidecar is locked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_sidecar_unlink)

    with pytest.raises(RestoreError, match="atomic replace failed"):
        restore_sqlite_database(backup_path, db_path)

    live = sqlite3.connect(db_path)
    try:
        assert (
            live.execute("select name from workspaces where id = ?", (workspace_id,)).fetchone()[0]
            == "Mutated"
        )
    finally:
        live.close()
