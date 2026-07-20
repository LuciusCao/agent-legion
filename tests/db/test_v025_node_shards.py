from __future__ import annotations

import sqlite3

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"pragma table_info({table})")}


def test_v025_creates_node_shards_table(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)  # 跑全部 migrations
    conn = connect_sqlite(db_path)
    try:
        assert {
            "job_id",
            "node_key",
            "shard_index",
            "status",
            "input_json",
            "output_json",
            "error_message",
            "execution_id",
            "started_at",
            "finished_at",
        } <= _columns(conn, "node_shards")
        pk = {r[1] for r in conn.execute("pragma table_info(node_shards)") if r[5]}
        assert pk == {"job_id", "node_key", "shard_index"}
        # 默认值：新插入行只需 (job_id, node_key, shard_index)
        conn.execute("insert into workspaces(id, name) values ('w1', 'ws')")
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, title, status, storage_dir) values ('j1','w1','wf','s','s1','t','pending','d')"
        )
        conn.execute(
            "insert into node_shards(job_id, node_key, shard_index) values ('j1','review',0)"
        )
        row = conn.execute(
            "select status, input_json, output_json, error_message, execution_id, started_at, finished_at from node_shards where job_id='j1'"
        ).fetchone()
        assert tuple(row) == ("pending", "{}", "", "", "", None, None)
        # FK 级联：删 job 级联删 node_shards
        conn.execute("delete from jobs where id='j1'")
        assert conn.execute("select count(*) from node_shards where job_id='j1'").fetchone()[0] == 0
    finally:
        conn.close()
