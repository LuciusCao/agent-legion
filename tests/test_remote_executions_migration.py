from __future__ import annotations

from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db


def test_v021_creates_remote_executions_table(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    conn = connect_sqlite(db_path)
    try:
        tables = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        assert "remote_executions" in tables
        cols = {row["name"] for row in conn.execute("pragma table_info(remote_executions)")}
        assert {
            "execution_id",
            "lease_id",
            "job_id",
            "node_key",
            "capability",
            "bundle_name",
            "manifest_json",
            "state",
            "worker_id",
            "requeue_count",
            "last_heartbeat_at",
            "outcome_json",
            "created_at",
            "updated_at",
        } <= cols
        indexes = {
            row["name"] for row in conn.execute("select name from sqlite_master where type='index'")
        }
        assert "idx_remote_executions_dequeue" in indexes
    finally:
        conn.close()


def test_v021_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    init_db(db_path)  # 第二次 init_db 不应报错（create table if not exists）
