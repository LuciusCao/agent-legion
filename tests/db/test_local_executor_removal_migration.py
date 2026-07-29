"""Schema v18: retire the local executor kind (rebind to code-default)."""

from __future__ import annotations

from server.app.db.migrations import migrate_local_executor_removal
from server.app.db.transaction import write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _seed_workspace(conn, workspace_id: str) -> None:
    conn.execute(
        "insert into workspaces(id, name) values (?, ?) on conflict do nothing",
        (workspace_id, workspace_id),
    )
    conn.execute(
        "insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)"
        " values (?, 'local-default', 4) on conflict do nothing",
        (workspace_id,),
    )
    conn.execute(
        "insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)"
        " values (?, 'code-default', 2) on conflict do nothing",
        (workspace_id,),
    )
    for node_key, executor_id in (
        ("clean_and_parse", "local-default"),
        ("transcribe_video", "local-default"),
        ("review_keywords", "pi-default"),
    ):
        conn.execute(
            "insert into workspace_node_bindings(workspace_id, workflow_key, node_key, executor_id)"
            " values (?, 'question_comprehension_info', ?, ?) on conflict do nothing",
            (workspace_id, node_key, executor_id),
        )
    conn.execute(
        "insert into workspace_node_limits(workspace_id, workflow_key, node_key, concurrency_limit)"
        " values (?, 'question_comprehension_info', 'clean_and_parse', 1)"
        " on conflict do nothing",
        (workspace_id,),
    )


def _bindings(conn, workspace_id: str) -> dict[str, str]:
    rows = conn.execute(
        "select node_key, executor_id from workspace_node_bindings where workspace_id=?",
        (workspace_id,),
    ).fetchall()
    return {row["node_key"]: row["executor_id"] for row in rows}


def _allocations(conn, workspace_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select executor_id, concurrency_limit from workspace_executor_allocations"
        " where workspace_id=?",
        (workspace_id,),
    ).fetchall()
    return {row["executor_id"]: row["concurrency_limit"] for row in rows}


def test_migration_rebinds_local_bindings_to_code_default() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "local-removal-ws1")
        migrate_local_executor_removal(conn)
        bindings = _bindings(conn, "local-removal-ws1")

    assert bindings["clean_and_parse"] == "code-default"
    assert bindings["transcribe_video"] == "code-default"
    # Bindings for other executors are untouched.
    assert bindings["review_keywords"] == "pi-default"


def test_migration_deletes_local_allocation_and_keeps_code_allocation() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "local-removal-ws2")
        migrate_local_executor_removal(conn)
        allocations = _allocations(conn, "local-removal-ws2")

    assert "local-default" not in allocations
    assert allocations["code-default"] == 2


def test_migration_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "local-removal-ws3")
        migrate_local_executor_removal(conn)
        first_bindings = _bindings(conn, "local-removal-ws3")
        first_allocations = _allocations(conn, "local-removal-ws3")
        migrate_local_executor_removal(conn)
        second_bindings = _bindings(conn, "local-removal-ws3")
        second_allocations = _allocations(conn, "local-removal-ws3")

    assert first_bindings == second_bindings
    assert first_allocations == second_allocations


def test_migration_keeps_node_limits() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "local-removal-ws4")
        migrate_local_executor_removal(conn)
        row = conn.execute(
            "select concurrency_limit from workspace_node_limits"
            " where workspace_id='local-removal-ws4'"
            " and workflow_key='question_comprehension_info' and node_key='clean_and_parse'"
        ).fetchone()

    assert row is not None
    assert row["concurrency_limit"] == 1
