from __future__ import annotations

import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from server.app.jobs.queries import JobQueries
from tests.helpers import ensure_legacy_workspace_tables

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "finalize-workspace-executor-migration.py"
)


def _run_cli(
    args: list[str], data_dir: Path, *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args, "--data-dir", str(data_dir)],
        capture_output=True,
        text=True,
        check=check,
    )


def _legacy_database(data_dir: Path, *, agent_id: str = "pi") -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "video_hive.sqlite"
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir)
    ensure_legacy_workspace_tables(queries)
    workspace = queries.create_workspace(
        name="Legacy Workspace",
        default_pipeline_key="reading_analysis",
    )
    workspace_id = str(workspace["id"])
    with queries.connect() as conn:
        conn.execute(
            "update workspaces set pipeline_config_json = ? where id = ?",
            (
                json.dumps({"local": 4, "agent": 8}, ensure_ascii=False, sort_keys=True),
                workspace_id,
            ),
        )
        conn.execute(
            "insert into workspace_agent_assignments(workspace_id, agent_id, concurrency_limit) "
            "values (?, ?, ?)",
            (workspace_id, agent_id, 3),
        )
    return db_path


def _table_exists(db_path: Path, table: str) -> bool:
    import sqlite3

    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        row = conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = ?", (table,)
        ).fetchone()
    return row is not None


def _migration_version(db_path: Path, version: int) -> bool:
    import sqlite3

    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        row = conn.execute(
            "select 1 from schema_migrations where version = ?", (version,)
        ).fetchone()
    return row is not None


def test_check_returns_clean_report_without_writing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _legacy_database(data_dir)

    result = _run_cli(["--check"], data_dir)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["migration_version"] == 5
    assert report["migration_name"] == "remove_legacy_executor_paths"
    assert report["issues"] == []
    assert _table_exists(db_path, "workspace_agent_assignments")
    assert _table_exists(db_path, "workspace_executor_bootstrap_state")
    assert not _migration_version(db_path, 5)


def test_check_does_not_apply_pending_structural_migrations(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _legacy_database(data_dir)
    import sqlite3

    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        conn.execute("delete from schema_migrations where version in (3, 4)")
        before = conn.execute(
            "select version, name from schema_migrations order by version"
        ).fetchall()

    result = _run_cli(["--check"], data_dir)

    assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        after = conn.execute(
            "select version, name from schema_migrations order by version"
        ).fetchall()
    assert after == before


def test_apply_creates_backup_and_finalizes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _legacy_database(data_dir)

    result = _run_cli(["--apply"], data_dir)

    assert result.returncode == 0, result.stderr
    assert "Backup created:" in result.stdout
    assert "Workspace executor migration finalized." in result.stdout
    backups = list(data_dir.glob("video_hive-*.sqlite"))
    assert len(backups) == 1
    assert backups[0].exists()
    assert not _table_exists(db_path, "workspace_agent_assignments")
    assert not _table_exists(db_path, "workspace_executor_bootstrap_state")
    assert _migration_version(db_path, 5)


def test_apply_backup_contains_committed_wal_data(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _legacy_database(data_dir)
    import sqlite3

    writer = sqlite3.connect(str(db_path))
    try:
        writer.execute("pragma journal_mode=WAL")
        writer.execute("pragma wal_autocheckpoint=0")
        writer.execute("update workspaces set name = 'WAL Workspace' where id != 'default'")
        writer.commit()

        result = _run_cli(["--apply"], data_dir)
        assert result.returncode == 0, result.stderr
        backup = next(data_dir.glob("video_hive-before-v005-*.sqlite"))
        with closing(sqlite3.connect(str(backup))) as backup_conn, backup_conn:
            stored = backup_conn.execute(
                "select name from workspaces where id != 'default'"
            ).fetchone()[0]
        assert stored == "WAL Workspace"
    finally:
        writer.close()


def test_check_is_idempotent_after_finalization(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _legacy_database(data_dir)

    _run_cli(["--apply"], data_dir, check=True)
    result = _run_cli(["--check"], data_dir)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["issues"] == []


def test_apply_blocked_returns_nonzero_with_report(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = _legacy_database(data_dir, agent_id="unknown-agent")

    result = _run_cli(["--apply"], data_dir)

    assert result.returncode == 1
    report = json.loads(result.stderr)
    assert report["migration_version"] == 5
    assert report["migration_name"] == "remove_legacy_executor_paths"
    assert any(
        issue["table"] == "workspace_agent_assignments" and issue["constraint"] == "agent_id"
        for issue in report["issues"]
    )
    assert _table_exists(db_path, "workspace_agent_assignments")
    assert not _migration_version(db_path, 5)
