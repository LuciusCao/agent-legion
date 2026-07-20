from __future__ import annotations

import sqlite3

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"pragma table_info({table})")}


def test_v024_creates_artifact_tables_and_worker_columns(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)  # 跑全部 migrations
    conn = connect_sqlite(db_path)
    try:
        assert {"hash", "size", "created_at"} <= _columns(conn, "artifacts")
        assert {"job_id", "node_key", "name", "hash"} <= _columns(conn, "artifact_refs")
        pk = {r[1] for r in conn.execute("pragma table_info(artifact_refs)") if r[5]}
        assert pk == {"job_id", "node_key", "name"}
        worker_cols = _columns(conn, "remote_workers")
        assert {"token_hash", "labels_json", "revoked_at"} <= worker_cols
        # FK 级联：删 job 级联删 artifact_refs
        conn.execute("insert into workspaces(id, name) values ('w1', 'ws')")
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, title, status, storage_dir) values ('j1','w1','wf','s','s1','t','pending','d')"
        )
        conn.execute(
            "insert into artifact_refs(job_id, node_key, name, hash) values ('j1','n1','out','abc')"
        )
        conn.execute("delete from jobs where id='j1'")
        assert (
            conn.execute("select count(*) from artifact_refs where job_id='j1'").fetchone()[0] == 0
        )
    finally:
        conn.close()
