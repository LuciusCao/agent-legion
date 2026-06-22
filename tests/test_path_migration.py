from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from server.app.db.schema import init_db

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrate-paths-to-relative.py"


def _load_migration_module() -> Any:
    name = "migrate_paths_to_relative"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def migration_module() -> Any:
    return _load_migration_module()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "video_hive.sqlite"
    init_db(path)
    return path


@pytest.fixture
def old_data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _insert_video(conn: sqlite3.Connection, video_id: str, storage_dir: str) -> None:
    conn.execute(
        "insert into videos(id, source_url, title, content_type, storage_dir) values (?, ?, ?, ?, ?)",
        (video_id, "http://example.com", "title", "knowledge", storage_dir),
    )


def _insert_phase_run(conn: sqlite3.Connection, log_path: str) -> int:
    cur = conn.execute(
        "insert into phase_runs(video_id, phase_key, status, log_path) values (?, ?, ?, ?)",
        ("video-1", "download", "running", log_path),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _insert_job(conn: sqlite3.Connection, job_id: str, storage_dir: str) -> None:
    conn.execute(
        "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, storage_dir) values (?, ?, ?, ?, ?, ?)",
        (job_id, "default", "question_comprehension_info", "question", "q1", storage_dir),
    )


def _insert_node_run(
    conn: sqlite3.Connection,
    log_path: str,
    run_dir: str,
    session_dir: str,
) -> int:
    cur = conn.execute(
        "insert into node_runs(job_id, node_key, status, log_path, run_dir, session_dir) values (?, ?, ?, ?, ?, ?)",
        ("job-1", "node-1", "running", log_path, run_dir, session_dir),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def _insert_package(conn: sqlite3.Connection, path: str) -> int:
    cur = conn.execute("insert into packages(path) values (?)", (path,))
    assert cur.lastrowid is not None
    return cur.lastrowid


def _fetch_column(
    conn: sqlite3.Connection, table: str, column: str, pk_column: str, row_id: Any
) -> str:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        f"select {column} from {table} where {pk_column} = ?",
        (row_id,),
    ).fetchone()
    return row[column] if row else ""


def _list_backups(db_path: Path) -> list[Path]:
    return sorted(db_path.parent.glob(f"{db_path.stem}-before-relative-path-migration-*.sqlite"))


class TestMigratePathsToRelative:
    def test_nested_suffix_preserved_for_every_column(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
    ) -> None:
        conn = sqlite3.connect(db_path)
        try:
            _insert_video(conn, "video-1", str(old_data_dir / "videos" / "nested" / "deep"))
            phase_run_id = _insert_phase_run(
                conn, str(old_data_dir / "logs" / "nested" / "deep.log")
            )
            _insert_job(conn, "job-1", str(old_data_dir / "jobs" / "ws" / "job-1"))
            node_run_id = _insert_node_run(
                conn,
                str(old_data_dir / "logs" / "node" / "run.log"),
                str(old_data_dir / "jobs" / "ws" / "job-1" / "runs" / "node-1"),
                str(old_data_dir / "jobs" / "ws" / "job-1" / "sessions" / "node-1"),
            )
            package_id = _insert_package(
                conn, str(old_data_dir / "packages" / "nested" / "pkg.zip")
            )
            conn.commit()
        finally:
            conn.close()

        counts = migration_module.run_migration(db_path, old_data_dir)

        assert sum(counts.values()) == 7
        conn = sqlite3.connect(db_path)
        try:
            assert (
                _fetch_column(conn, "videos", "storage_dir", "id", "video-1")
                == "videos/nested/deep"
            )
            assert (
                _fetch_column(conn, "phase_runs", "log_path", "id", phase_run_id)
                == "logs/nested/deep.log"
            )
            assert _fetch_column(conn, "jobs", "storage_dir", "id", "job-1") == "jobs/ws/job-1"
            assert (
                _fetch_column(conn, "node_runs", "log_path", "id", node_run_id)
                == "logs/node/run.log"
            )
            assert (
                _fetch_column(conn, "node_runs", "run_dir", "id", node_run_id)
                == "jobs/ws/job-1/runs/node-1"
            )
            assert (
                _fetch_column(conn, "node_runs", "session_dir", "id", node_run_id)
                == "jobs/ws/job-1/sessions/node-1"
            )
            assert (
                _fetch_column(conn, "packages", "path", "id", package_id)
                == "packages/nested/pkg.zip"
            )
        finally:
            conn.close()

    def test_mixed_absolute_and_relative_rows(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
    ) -> None:
        conn = sqlite3.connect(db_path)
        try:
            _insert_video(conn, "video-1", str(old_data_dir / "videos" / "v1"))
            _insert_video(conn, "video-2", "videos/v2")
            _insert_job(conn, "job-1", "jobs/ws/j1")
            _insert_job(conn, "job-2", str(old_data_dir / "jobs" / "ws" / "j2"))
            conn.commit()
        finally:
            conn.close()

        counts = migration_module.run_migration(db_path, old_data_dir)

        assert counts[("videos", "storage_dir")] == 1
        assert counts[("jobs", "storage_dir")] == 1
        conn = sqlite3.connect(db_path)
        try:
            assert _fetch_column(conn, "videos", "storage_dir", "id", "video-1") == "videos/v1"
            assert _fetch_column(conn, "videos", "storage_dir", "id", "video-2") == "videos/v2"
            assert _fetch_column(conn, "jobs", "storage_dir", "id", "job-1") == "jobs/ws/j1"
            assert _fetch_column(conn, "jobs", "storage_dir", "id", "job-2") == "jobs/ws/j2"
        finally:
            conn.close()

    def test_dry_run_rolls_back_and_creates_no_backup(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
    ) -> None:
        conn = sqlite3.connect(db_path)
        try:
            _insert_video(conn, "video-1", str(old_data_dir / "videos" / "v1"))
            conn.commit()
        finally:
            conn.close()

        counts = migration_module.run_migration(db_path, old_data_dir, dry_run=True)

        assert counts[("videos", "storage_dir")] == 1
        assert _list_backups(db_path) == []
        conn = sqlite3.connect(db_path)
        try:
            assert _fetch_column(conn, "videos", "storage_dir", "id", "video-1") == str(
                old_data_dir / "videos" / "v1"
            )
        finally:
            conn.close()

    def test_real_run_creates_backup(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
    ) -> None:
        conn = sqlite3.connect(db_path)
        try:
            _insert_video(conn, "video-1", str(old_data_dir / "videos" / "v1"))
            conn.commit()
        finally:
            conn.close()

        migration_module.run_migration(db_path, old_data_dir)

        backups = _list_backups(db_path)
        assert len(backups) == 1
        assert backups[0].is_file()

    def test_real_run_reports_backup_path(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
    ) -> None:
        reported: list[Path] = []

        migration_module.run_migration(db_path, old_data_dir, on_backup=reported.append)

        assert reported == _list_backups(db_path)

    def test_backup_is_created_while_competing_writers_are_locked(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("create table concurrent_writes(value text)")
            conn.commit()
        finally:
            conn.close()

        outcomes: list[str] = []
        original_backup = migration_module.backup_sqlite_connection

        def observe_lock(source: sqlite3.Connection, backup_path: Path) -> None:
            competing = sqlite3.connect(db_path, timeout=0.01)
            try:
                competing.execute("insert into concurrent_writes values ('late')")
                competing.commit()
                outcomes.append("wrote")
            except sqlite3.OperationalError:
                outcomes.append("locked")
            finally:
                competing.close()
            original_backup(source, backup_path)

        monkeypatch.setattr(migration_module, "backup_sqlite_connection", observe_lock)

        migration_module.run_migration(db_path, old_data_dir)

        assert outcomes == ["locked"]

    def test_second_run_changes_zero_rows(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
    ) -> None:
        conn = sqlite3.connect(db_path)
        try:
            _insert_video(conn, "video-1", str(old_data_dir / "videos" / "v1"))
            _insert_job(conn, "job-1", str(old_data_dir / "jobs" / "ws" / "j1"))
            conn.commit()
        finally:
            conn.close()

        first_counts = migration_module.run_migration(db_path, old_data_dir)
        assert sum(first_counts.values()) == 2

        second_counts = migration_module.run_migration(db_path, old_data_dir)
        assert all(count == 0 for count in second_counts.values())

        conn = sqlite3.connect(db_path)
        try:
            assert _fetch_column(conn, "videos", "storage_dir", "id", "video-1") == "videos/v1"
            assert _fetch_column(conn, "jobs", "storage_dir", "id", "job-1") == "jobs/ws/j1"
        finally:
            conn.close()

    def test_outside_root_aborts_and_rolls_back(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
    ) -> None:
        conn = sqlite3.connect(db_path)
        try:
            _insert_video(conn, "video-1", str(old_data_dir / "videos" / "v1"))
            _insert_job(conn, "job-1", "/outside/job-1")
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(migration_module.PathMigrationError) as exc_info:
            migration_module.run_migration(db_path, old_data_dir)

        assert exc_info.value.table == "jobs"
        assert exc_info.value.column == "storage_dir"
        assert exc_info.value.row_id == "job-1"

        conn = sqlite3.connect(db_path)
        try:
            assert _fetch_column(conn, "videos", "storage_dir", "id", "video-1") == str(
                old_data_dir / "videos" / "v1"
            )
            assert _fetch_column(conn, "jobs", "storage_dir", "id", "job-1") == "/outside/job-1"
        finally:
            conn.close()

    def test_wrong_category_relative_aborts_and_rolls_back(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
    ) -> None:
        conn = sqlite3.connect(db_path)
        try:
            _insert_video(conn, "video-1", str(old_data_dir / "videos" / "v1"))
            _insert_job(conn, "job-1", "videos/should-be-jobs")
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(migration_module.PathMigrationError) as exc_info:
            migration_module.run_migration(db_path, old_data_dir)

        assert exc_info.value.table == "jobs"
        assert exc_info.value.column == "storage_dir"
        assert exc_info.value.row_id == "job-1"

        conn = sqlite3.connect(db_path)
        try:
            assert _fetch_column(conn, "videos", "storage_dir", "id", "video-1") == str(
                old_data_dir / "videos" / "v1"
            )
            assert (
                _fetch_column(conn, "jobs", "storage_dir", "id", "job-1") == "videos/should-be-jobs"
            )
        finally:
            conn.close()

    def test_wrong_category_absolute_aborts_and_rolls_back(
        self,
        migration_module: Any,
        db_path: Path,
        old_data_dir: Path,
    ) -> None:
        conn = sqlite3.connect(db_path)
        try:
            _insert_video(conn, "video-1", str(old_data_dir / "videos" / "v1"))
            _insert_job(conn, "job-1", str(old_data_dir / "videos" / "job-1"))
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(migration_module.PathMigrationError) as exc_info:
            migration_module.run_migration(db_path, old_data_dir)

        assert exc_info.value.table == "jobs"
        assert exc_info.value.column == "storage_dir"
        assert exc_info.value.row_id == "job-1"

        conn = sqlite3.connect(db_path)
        try:
            assert _fetch_column(conn, "videos", "storage_dir", "id", "video-1") == str(
                old_data_dir / "videos" / "v1"
            )
            assert _fetch_column(conn, "jobs", "storage_dir", "id", "job-1") == str(
                old_data_dir / "videos" / "job-1"
            )
        finally:
            conn.close()

    def test_different_old_data_dir_and_active_data_dir(
        self,
        migration_module: Any,
        db_path: Path,
        tmp_path: Path,
    ) -> None:
        active_data_dir = tmp_path / "active"
        active_data_dir.mkdir(parents=True, exist_ok=True)
        old_data_dir = tmp_path / "historical"
        old_data_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(db_path)
        try:
            _insert_video(conn, "video-1", str(old_data_dir / "videos" / "v1"))
            conn.commit()
        finally:
            conn.close()

        counts = migration_module.run_migration(db_path, old_data_dir)

        assert counts[("videos", "storage_dir")] == 1
        conn = sqlite3.connect(db_path)
        try:
            assert _fetch_column(conn, "videos", "storage_dir", "id", "video-1") == "videos/v1"
        finally:
            conn.close()
