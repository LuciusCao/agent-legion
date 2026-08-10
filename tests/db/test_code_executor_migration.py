"""Schema v22: rebind CMS first nodes to the code executor."""

from __future__ import annotations

from server.app.db.migrations import migrate_code_executor_bindings
from server.app.db.schema import SCHEMA_VERSION
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _seed_workspace(conn, workspace_id: str, concurrency: int = 4) -> None:
    conn.execute(
        "insert into workspaces(id, name) values (%s, %s) on conflict do nothing",
        (workspace_id, workspace_id),
    )
    conn.execute(
        "insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)"
        " values (%s, 'local-default', %s) on conflict do nothing",
        (workspace_id, concurrency),
    )
    for workflow_key, node_key in (
        ("question_comprehension_info", "fetch_questions"),
        ("video_knowledge", "download_video"),
        ("video_knowledge", "transcribe_video"),
    ):
        conn.execute(
            "insert into workspace_node_bindings(workspace_id, workflow_key, node_key, executor_id)"
            " values (%s, %s, %s, 'local-default') on conflict do nothing",
            (workspace_id, workflow_key, node_key),
        )


def test_schema_v33_recorded() -> None:
    assert SCHEMA_VERSION == 33
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "agent_requests_cancelled_recent_index"


def test_migration_rebinds_first_nodes_and_copies_concurrency() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "code-mig-ws", concurrency=6)
        migrate_code_executor_bindings(conn)

        allocation = conn.execute(
            "select concurrency_limit from workspace_executor_allocations"
            " where workspace_id='code-mig-ws' and executor_id='code-default'"
        ).fetchone()
        bindings = conn.execute(
            "select workflow_key, node_key, executor_id from workspace_node_bindings"
            " where workspace_id='code-mig-ws'"
        ).fetchall()

    assert allocation is not None
    assert allocation["concurrency_limit"] == 6
    by_node = {(row["workflow_key"], row["node_key"]): row["executor_id"] for row in bindings}
    assert by_node[("question_comprehension_info", "fetch_questions")] == "code-default"
    assert by_node[("video_knowledge", "download_video")] == "code-default"
    # Untouched nodes keep their existing binding.
    assert by_node[("video_knowledge", "transcribe_video")] == "local-default"


def test_migration_defaults_concurrency_without_local_allocation() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('code-mig-ws2', 'code-mig-ws2')"
            " on conflict do nothing"
        )
        migrate_code_executor_bindings(conn)
        allocation = conn.execute(
            "select concurrency_limit from workspace_executor_allocations"
            " where workspace_id='code-mig-ws2' and executor_id='code-default'"
        ).fetchone()
    assert allocation is not None
    assert allocation["concurrency_limit"] == 1


def test_migration_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "code-mig-ws3", concurrency=3)
        migrate_code_executor_bindings(conn)
        conn.execute(
            "update workspace_executor_allocations set concurrency_limit=9"
            " where workspace_id='code-mig-ws3' and executor_id='code-default'"
        )
        migrate_code_executor_bindings(conn)
        allocation = conn.execute(
            "select concurrency_limit from workspace_executor_allocations"
            " where workspace_id='code-mig-ws3' and executor_id='code-default'"
        ).fetchone()
    # A later operator edit is not clobbered by a replay.
    assert allocation["concurrency_limit"] == 9
