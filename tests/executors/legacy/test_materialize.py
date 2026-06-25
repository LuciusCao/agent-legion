from __future__ import annotations

import pytest

from server.app.db.migrations.report import MigrationBlockedError
from server.app.executors.legacy_migration import finalize_legacy_executor_schema
from server.app.jobs.queries import JobQueries
from tests.executors.legacy.helpers import (
    _fetch_all_allocations,
    _fetch_all_bindings,
    _fetch_all_node_limits,
    _insert_legacy_agent_assignment,
    _sample_executors,
    _sample_workflows,
    _set_workflow_config,
    _table_exists,
)


def test_finalizer_materializes_local_only_workspace(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="Local Workspace",
        default_workflow_key="question_comprehension_info",
    )
    workspace_id = str(workspace["id"])
    _set_workflow_config(queries, workspace_id, {"local": 4})

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    allocations = [
        row for row in _fetch_all_allocations(queries) if row["workspace_id"] == workspace_id
    ]
    assert allocations == [
        {
            "workspace_id": workspace_id,
            "executor_id": "local-default",
            "concurrency_limit": 4,
        }
    ]

    bindings = [row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace_id]
    assert bindings == [
        {
            "workspace_id": workspace_id,
            "workflow_key": "question_comprehension_info",
            "node_key": "local_a",
            "executor_id": "local-default",
        },
        {
            "workspace_id": workspace_id,
            "workflow_key": "question_comprehension_info",
            "node_key": "local_b",
            "executor_id": "local-default",
        },
    ]

    limits = [row for row in _fetch_all_node_limits(queries) if row["workspace_id"] == workspace_id]
    assert limits == [
        {
            "workspace_id": workspace_id,
            "workflow_key": "question_comprehension_info",
            "node_key": "local_a",
            "concurrency_limit": 1,
        },
        {
            "workspace_id": workspace_id,
            "workflow_key": "question_comprehension_info",
            "node_key": "local_b",
            "concurrency_limit": 1,
        },
    ]


def test_finalizer_materializes_exact_pi_assignment(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="Pi Workspace",
        default_workflow_key="question_comprehension_info",
    )
    workspace_id = str(workspace["id"])
    _set_workflow_config(queries, workspace_id, {"nodes": {"local_a": 1}})
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 3)

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    allocations = {
        row["executor_id"]: row["concurrency_limit"]
        for row in _fetch_all_allocations(queries)
        if row["workspace_id"] == workspace_id
    }
    assert allocations == {"local-default": 1, "pi-default": 3}

    bindings = {
        row["node_key"]: row["executor_id"]
        for row in _fetch_all_bindings(queries)
        if row["workspace_id"] == workspace_id
    }
    assert bindings == {
        "local_a": "local-default",
        "local_b": "local-default",
        "pi_a": "pi-default",
    }

    limits = {
        row["node_key"]: row["concurrency_limit"]
        for row in _fetch_all_node_limits(queries)
        if row["workspace_id"] == workspace_id
    }
    assert limits == {"local_a": 1, "local_b": 1}


def test_finalizer_preserves_authoritative_configuration(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Authoritative",
        default_workflow_key="question_comprehension_info",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 99)

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())
        # Pretend a user edited the configuration after the first materialization.
        conn.execute(
            "update workspace_executor_allocations set concurrency_limit = 123 "
            "where workspace_id = ? and executor_id = ?",
            (workspace_id, "local-default"),
        )
        conn.execute(
            "update workspace_executor_allocations set concurrency_limit = 456 "
            "where workspace_id = ? and executor_id = ?",
            (workspace_id, "pi-default"),
        )

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    allocations = {
        row["executor_id"]: row["concurrency_limit"]
        for row in _fetch_all_allocations(queries)
        if row["workspace_id"] == workspace_id
    }
    assert allocations == {"local-default": 123, "pi-default": 456}


def test_finalizer_blocks_on_unknown_agent(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Unknown Agent",
        default_workflow_key="question_comprehension_info",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "unknown", 2)

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    issues = {issue.constraint for issue in exc_info.value.report.issues}
    assert "agent_id" in issues
    assert _table_exists(queries, "workspace_agent_assignments")


def test_finalizer_blocks_on_invalid_legacy_limit(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="Bad Limit",
        default_workflow_key="question_comprehension_info",
    )
    _set_workflow_config(queries, str(workspace["id"]), {"local": 0})

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    issues = {issue.constraint for issue in exc_info.value.report.issues}
    assert "pipeline_config_json.local" in issues


@pytest.mark.parametrize("raw_value", ["{broken", "[]", "null"])
def test_finalizer_blocks_on_invalid_pipeline_config_json(
    queries: JobQueries, raw_value: str
) -> None:
    workspace_id = queries.create_workspace(
        name=f"Invalid JSON {raw_value}",
        default_workflow_key="question_comprehension_info",
    )["id"]
    with queries.connect() as conn:
        conn.execute(
            "update workspaces set pipeline_config_json = ? where id = ?",
            (raw_value, workspace_id),
        )

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    assert any(
        issue.row_key == workspace_id and issue.constraint == "pipeline_config_json"
        for issue in exc_info.value.report.issues
    )
    with queries._connect_read() as conn:
        stored = conn.execute(
            "select pipeline_config_json from workspaces where id = ?", (workspace_id,)
        ).fetchone()[0]
    assert stored == raw_value


def test_finalizer_blocks_on_missing_workflow_definition(queries: JobQueries) -> None:
    queries.create_workspace(
        name="Missing Workflow",
        default_workflow_key="nonexistent",
    )

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    issues = {issue.constraint for issue in exc_info.value.report.issues}
    assert "default_workflow_key" in issues


def test_finalizer_is_idempotent_after_v005(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Idempotent",
        default_workflow_key="question_comprehension_info",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 3)

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    first_allocations = _fetch_all_allocations(queries)
    first_bindings = _fetch_all_bindings(queries)
    first_limits = _fetch_all_node_limits(queries)

    assert not _table_exists(queries, "workspace_agent_assignments")
    assert not _table_exists(queries, "workspace_executor_bootstrap_state")

    with queries.connect() as conn:
        report = finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    assert report.issues == ()
    assert _fetch_all_allocations(queries) == first_allocations
    assert _fetch_all_bindings(queries) == first_bindings
    assert _fetch_all_node_limits(queries) == first_limits


def test_finalizer_does_not_bind_unallocated_agent_nodes(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Unallocated Agent",
        default_workflow_key="question_comprehension_info",
    )["id"]

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    bindings = [row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace_id]
    assert bindings == [
        {
            "workspace_id": workspace_id,
            "workflow_key": "question_comprehension_info",
            "node_key": "local_a",
            "executor_id": "local-default",
        },
        {
            "workspace_id": workspace_id,
            "workflow_key": "question_comprehension_info",
            "node_key": "local_b",
            "executor_id": "local-default",
        },
    ]


def test_finalizer_applies_v005_and_removes_pipeline_config_json(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="V005",
        default_workflow_key="question_comprehension_info",
    )
    _set_workflow_config(queries, str(workspace["id"]), {"local": 7})

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_workflows(), _sample_executors())

    with queries._connect_read() as conn:
        columns = {row["name"] for row in conn.execute("pragma table_info(workspaces)").fetchall()}
        versions = {
            row["version"]
            for row in conn.execute("select version from schema_migrations").fetchall()
        }

    assert "pipeline_config_json" not in columns
    assert 5 in versions
