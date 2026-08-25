"""Schema v56: workspace_job_node_status_counts trigger-maintained counters.

Issue #121: the workspace DAG endpoint counted node statuses with a
join+group-by over job_nodes ⋈ jobs — O(workspace job_nodes) per call, 48s
measured at 260k jobs / 2.9M job_nodes. The counter table
(DB-JOB-NODE-STATUS-COUNTS-001) turns the read into a PK-prefix lookup.
The latest-migration record pin moved to
tests/db/test_node_secret_sweep_migration.py (v57).
"""

from __future__ import annotations

from server.app.db.migrations.job_node_status_counts import (
    migrate_workspace_job_node_status_counts,
)
from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs import JobQueries
from tests.postgres_support import TEST_DATABASE_URL


def _seed_workspace(conn, workspace_id: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'question_comprehension_info') on conflict do nothing",
        (workspace_id, workspace_id),
    )


def _insert_job(
    conn, job_id: str, workspace_id: str, workflow_key: str = "video_knowledge"
) -> None:
    conn.execute(
        "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, status)"
        " values (%s, %s, %s, 'test', %s, 'queued')",
        (job_id, workspace_id, workflow_key, job_id),
    )


def _insert_node(conn, job_id: str, node_key: str, status: str) -> None:
    conn.execute(
        "insert into job_nodes(job_id, node_key, status) values (%s, %s, %s)",
        (job_id, node_key, status),
    )


def _group_by_counts(conn, workspace_id: str, workflow_key: str) -> dict[str, dict[str, int]]:
    rows = conn.execute(
        """
        select jn.node_key, jn.status, count(*) as cnt
        from job_nodes jn
        join jobs j on j.id = jn.job_id
        where j.workspace_id = %s and j.workflow_key = %s
        group by 1, 2
        """,
        (workspace_id, workflow_key),
    ).fetchall()
    result: dict[str, dict[str, int]] = {}
    for row in rows:
        result.setdefault(row["node_key"], {})[row["status"]] = int(row["cnt"])
    return result


def _table_counts(conn, workspace_id: str, workflow_key: str) -> dict[str, dict[str, int]]:
    rows = conn.execute(
        """
        select node_key, status, cnt from workspace_job_node_status_counts
        where workspace_id = %s and workflow_key = %s and cnt <> 0
        """,
        (workspace_id, workflow_key),
    ).fetchall()
    result: dict[str, dict[str, int]] = {}
    for row in rows:
        result.setdefault(row["node_key"], {})[row["status"]] = int(row["cnt"])
    return result


def test_counts_table_and_triggers_exist() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema()"
                " and table_name='workspace_job_node_status_counts'"
            ).fetchall()
        }
        triggers = {
            row["trigger_name"]
            for row in conn.execute(
                "select trigger_name from information_schema.triggers"
                " where trigger_schema=current_schema()"
            ).fetchall()
        }
    assert columns == {"workspace_id", "workflow_key", "node_key", "status", "cnt"}
    assert {
        "job_nodes_status_counts_sync",
        "jobs_node_status_counts_deduct",
        "jobs_node_status_counts_rekey",
    } <= triggers


def test_trigger_tracks_node_insert_update_delete() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "jnsc-trig-ws")
        _insert_job(conn, "jnsc-trig-1", "jnsc-trig-ws")
        _insert_node(conn, "jnsc-trig-1", "intake", "pending")
        _insert_node(conn, "jnsc-trig-1", "review", "pending")
        # Status transition: pending -> running.
        conn.execute(
            "update job_nodes set status='running' where job_id='jnsc-trig-1' and node_key='intake'"
        )
        # Direct job_nodes delete (workflow-upgrade mutation path).
        conn.execute("delete from job_nodes where job_id='jnsc-trig-1' and node_key='review'")
        assert _table_counts(conn, "jnsc-trig-ws", "video_knowledge") == _group_by_counts(
            conn, "jnsc-trig-ws", "video_knowledge"
        )
        # Updates that leave status/node_key/job_id unchanged must not drift.
        conn.execute(
            "update job_nodes set error_message='x' where job_id='jnsc-trig-1' and node_key='intake'"
        )
        assert _table_counts(conn, "jnsc-trig-ws", "video_knowledge") == _group_by_counts(
            conn, "jnsc-trig-ws", "video_knowledge"
        )


def test_job_delete_cascade_deducts_counts() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "jnsc-del-ws")
        _insert_job(conn, "jnsc-del-1", "jnsc-del-ws")
        _insert_job(conn, "jnsc-del-2", "jnsc-del-ws")
        for job_id in ("jnsc-del-1", "jnsc-del-2"):
            _insert_node(conn, job_id, "intake", "completed")
            _insert_node(conn, job_id, "review", "failed")
        # Deleting the job cascades to job_nodes; counts must leave entirely.
        conn.execute("delete from jobs where id='jnsc-del-1'")
        assert _table_counts(conn, "jnsc-del-ws", "video_knowledge") == _group_by_counts(
            conn, "jnsc-del-ws", "video_knowledge"
        )


def test_job_rekey_moves_counts() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "jnsc-mv-ws")
        _seed_workspace(conn, "jnsc-mv-ws2")
        _insert_job(conn, "jnsc-mv-1", "jnsc-mv-ws")
        _insert_node(conn, "jnsc-mv-1", "intake", "completed")
        _insert_node(conn, "jnsc-mv-1", "review", "pending")
        # Workspace move: counts must leave the old workspace entirely.
        conn.execute("update jobs set workspace_id='jnsc-mv-ws2' where id='jnsc-mv-1'")
        assert _table_counts(conn, "jnsc-mv-ws", "video_knowledge") == {}
        assert _table_counts(conn, "jnsc-mv-ws2", "video_knowledge") == _group_by_counts(
            conn, "jnsc-mv-ws2", "video_knowledge"
        )
        # Workflow re-key: counts move to the new workflow_key.
        conn.execute("update jobs set workflow_key='wf_other' where id='jnsc-mv-1'")
        assert _table_counts(conn, "jnsc-mv-ws2", "video_knowledge") == {}
        assert _table_counts(conn, "jnsc-mv-ws2", "wf_other") == _group_by_counts(
            conn, "jnsc-mv-ws2", "wf_other"
        )


def test_backfill_rebuilds_and_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "jnsc-bf-ws")
        _insert_job(conn, "jnsc-bf-1", "jnsc-bf-ws")
        _insert_node(conn, "jnsc-bf-1", "intake", "completed")
        _insert_node(conn, "jnsc-bf-1", "review", "failed")
        # Wipe the counter table so the backfill is exercised from scratch.
        conn.execute("delete from workspace_job_node_status_counts")
        migrate_workspace_job_node_status_counts(conn)
        first = _table_counts(conn, "jnsc-bf-ws", "video_knowledge")
        migrate_workspace_job_node_status_counts(conn)
        second = _table_counts(conn, "jnsc-bf-ws", "video_knowledge")
    assert first == {"intake": {"completed": 1}, "review": {"failed": 1}}
    assert second == first


def test_count_workspace_job_nodes_by_status_matches_group_by(tmp_path) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "jnsc-read-ws")
        _insert_job(conn, "jnsc-read-1", "jnsc-read-ws")
        _insert_job(conn, "jnsc-read-2", "jnsc-read-ws")
        _insert_job(conn, "jnsc-read-3", "jnsc-read-ws", workflow_key="wf_other")
        _insert_node(conn, "jnsc-read-1", "intake", "completed")
        _insert_node(conn, "jnsc-read-1", "review", "pending")
        _insert_node(conn, "jnsc-read-2", "intake", "failed")
        _insert_node(conn, "jnsc-read-3", "intake", "completed")
    with read_connection(TEST_DATABASE_URL) as conn:
        expected = _group_by_counts(conn, "jnsc-read-ws", "video_knowledge")
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    assert (
        queries.count_workspace_job_nodes_by_status("jnsc-read-ws", "video_knowledge") == expected
    )
    # Counts are scoped per workflow_key, and an unknown workspace is empty.
    assert queries.count_workspace_job_nodes_by_status("jnsc-read-ws", "wf_other") == {
        "intake": {"completed": 1}
    }
    assert queries.count_workspace_job_nodes_by_status("jnsc-missing-ws", "video_knowledge") == {}
