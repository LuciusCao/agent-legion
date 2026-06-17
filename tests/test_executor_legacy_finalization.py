import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.app.db.migrations.errors import MigrationError
from server.app.db.migrations.report import MigrationBlockedError
from server.app.executors.config import (
    ExecutorConfig,
    LocalCapabilityConfig,
    LocalExecutorConfig,
    PiCapabilityConfig,
    PiExecutorConfig,
)
from server.app.executors.legacy_migration import finalize_legacy_executor_schema
from server.app.jobs.queries import JobQueries
from server.app.main import create_app
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)
from tests.helpers import ensure_legacy_workspace_tables


@pytest.fixture
def queries(tmp_path: Path) -> JobQueries:
    db_path = tmp_path / "jobs.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    q = JobQueries(db_path, jobs_dir)
    ensure_legacy_workspace_tables(q)
    return q


def _sample_executors() -> dict[str, ExecutorConfig]:
    return {
        "local-default": LocalExecutorConfig(
            kind="local",
            global_capacity=2,
            capabilities={
                "local_a": LocalCapabilityConfig(handler="reading_analysis.local_a"),
                "local_b": LocalCapabilityConfig(handler="reading_analysis.local_b"),
            },
        ),
        "pi-default": PiExecutorConfig(
            kind="pi",
            global_capacity=8,
            capabilities={
                "pi_a": PiCapabilityConfig(
                    skill="reading_analysis/pi_a",
                    tools=("read", "write", "bash"),
                )
            },
        ),
    }


def _sample_pipelines() -> list[WorkflowDefinition]:
    return [
        _sample_pipeline(),
        _legacy_unconfigured_agent_pipeline(),
        _question_comprehension_info_pipeline(),
    ]


def _set_pipeline_config(queries: JobQueries, workspace_id: str, config: dict[str, Any]) -> None:
    with queries.connect() as conn:
        conn.execute(
            "update workspaces set pipeline_config_json = ? where id = ?",
            (json.dumps(config, ensure_ascii=False, sort_keys=True), workspace_id),
        )


def _insert_legacy_agent_assignment(
    queries: JobQueries, workspace_id: str, agent_id: str, concurrency_limit: int
) -> None:
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_agent_assignments(workspace_id, agent_id, concurrency_limit) "
            "values (?, ?, ?) on conflict(workspace_id, agent_id) do update set "
            "concurrency_limit = excluded.concurrency_limit",
            (workspace_id, agent_id, max(1, concurrency_limit)),
        )


def _list_legacy_agent_assignments(queries: JobQueries, workspace_id: str) -> list[dict[str, Any]]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select agent_id, concurrency_limit from workspace_agent_assignments "
            "where workspace_id = ?",
            (workspace_id,),
        ).fetchall()
    return [{"agent_id": r["agent_id"], "concurrency_limit": r["concurrency_limit"]} for r in rows]


def _sample_pipeline() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="reading_analysis",
        label="Reading Analysis",
        intake=WorkflowIntake(),
        nodes={
            "local_a": WorkflowNode(
                key="local_a",
                label="Local A",
                capability="local_a",
            ),
            "local_b": WorkflowNode(
                key="local_b",
                label="Local B",
                capability="local_b",
            ),
            "pi_a": WorkflowNode(
                key="pi_a",
                label="Pi A",
                capability="pi_a",
            ),
        },
    )


def _legacy_unconfigured_agent_pipeline() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="question_content",
        label="Question Content",
        intake=WorkflowIntake(),
        nodes={
            "fetch": WorkflowNode(
                key="fetch",
                label="Fetch",
                capability="local_a",
            ),
            "understand": WorkflowNode(
                key="understand",
                label="Understand",
                capability="understand",
            ),
        },
    )


def _question_comprehension_info_pipeline() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="question_comprehension_info",
        label="Question Comprehension Info",
        intake=WorkflowIntake(),
        nodes={
            "local_a": WorkflowNode(
                key="local_a",
                label="Local A",
                capability="local_a",
            ),
        },
    )


def _fetch_all_allocations(queries: JobQueries) -> list[dict]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select workspace_id, executor_id, concurrency_limit "
            "from workspace_executor_allocations order by executor_id"
        ).fetchall()
        return [dict(row) for row in rows]


def _fetch_all_bindings(queries: JobQueries) -> list[dict]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select workspace_id, workflow_key, node_key, executor_id "
            "from workspace_node_bindings order by node_key"
        ).fetchall()
        return [dict(row) for row in rows]


def _fetch_all_node_limits(queries: JobQueries) -> list[dict]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select workspace_id, workflow_key, node_key, concurrency_limit "
            "from workspace_node_limits order by node_key"
        ).fetchall()
        return [dict(row) for row in rows]


def test_finalizer_materializes_local_only_workspace(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="Local Workspace",
        default_workflow_key="reading_analysis",
    )
    workspace_id = str(workspace["id"])
    _set_pipeline_config(queries, workspace_id, {"local": 4})

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

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
            "workflow_key": "reading_analysis",
            "node_key": "local_a",
            "executor_id": "local-default",
        },
        {
            "workspace_id": workspace_id,
            "workflow_key": "reading_analysis",
            "node_key": "local_b",
            "executor_id": "local-default",
        },
    ]

    limits = [row for row in _fetch_all_node_limits(queries) if row["workspace_id"] == workspace_id]
    assert limits == [
        {
            "workspace_id": workspace_id,
            "workflow_key": "reading_analysis",
            "node_key": "local_a",
            "concurrency_limit": 1,
        },
        {
            "workspace_id": workspace_id,
            "workflow_key": "reading_analysis",
            "node_key": "local_b",
            "concurrency_limit": 1,
        },
    ]


def test_finalizer_materializes_exact_pi_assignment(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="Pi Workspace",
        default_workflow_key="reading_analysis",
    )
    workspace_id = str(workspace["id"])
    _set_pipeline_config(queries, workspace_id, {"nodes": {"local_a": 1}})
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 3)

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

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
        default_workflow_key="reading_analysis",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 99)

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())
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
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    allocations = {
        row["executor_id"]: row["concurrency_limit"]
        for row in _fetch_all_allocations(queries)
        if row["workspace_id"] == workspace_id
    }
    assert allocations == {"local-default": 123, "pi-default": 456}


def test_finalizer_blocks_on_unknown_agent(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Unknown Agent",
        default_workflow_key="reading_analysis",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "unknown", 2)

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    issues = {issue.constraint for issue in exc_info.value.report.issues}
    assert "agent_id" in issues
    assert _table_exists(queries, "workspace_agent_assignments")


def test_finalizer_blocks_on_invalid_legacy_limit(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="Bad Limit",
        default_workflow_key="reading_analysis",
    )
    _set_pipeline_config(queries, str(workspace["id"]), {"local": 0})

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    issues = {issue.constraint for issue in exc_info.value.report.issues}
    assert "pipeline_config_json.local" in issues


@pytest.mark.parametrize("raw_value", ["{broken", "[]", "null"])
def test_finalizer_blocks_on_invalid_pipeline_config_json(
    queries: JobQueries, raw_value: str
) -> None:
    workspace_id = queries.create_workspace(
        name=f"Invalid JSON {raw_value}",
        default_workflow_key="reading_analysis",
    )["id"]
    with queries.connect() as conn:
        conn.execute(
            "update workspaces set pipeline_config_json = ? where id = ?",
            (raw_value, workspace_id),
        )

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    assert any(
        issue.row_key == workspace_id and issue.constraint == "pipeline_config_json"
        for issue in exc_info.value.report.issues
    )
    with queries._connect_read() as conn:
        stored = conn.execute(
            "select pipeline_config_json from workspaces where id = ?", (workspace_id,)
        ).fetchone()[0]
    assert stored == raw_value


def test_finalizer_blocks_on_missing_pipeline_definition(queries: JobQueries) -> None:
    queries.create_workspace(
        name="Missing Pipeline",
        default_workflow_key="nonexistent",
    )

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    issues = {issue.constraint for issue in exc_info.value.report.issues}
    assert "default_workflow_key" in issues


def test_finalizer_is_idempotent_after_v005(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Idempotent",
        default_workflow_key="reading_analysis",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 3)

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    first_allocations = _fetch_all_allocations(queries)
    first_bindings = _fetch_all_bindings(queries)
    first_limits = _fetch_all_node_limits(queries)

    assert not _table_exists(queries, "workspace_agent_assignments")
    assert not _table_exists(queries, "workspace_executor_bootstrap_state")

    with queries.connect() as conn:
        report = finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    assert report.issues == ()
    assert _fetch_all_allocations(queries) == first_allocations
    assert _fetch_all_bindings(queries) == first_bindings
    assert _fetch_all_node_limits(queries) == first_limits


def test_finalizer_does_not_bind_unallocated_agent_nodes(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Unallocated Agent",
        default_workflow_key="question_content",
    )["id"]

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    bindings = [row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace_id]
    assert bindings == [
        {
            "workspace_id": workspace_id,
            "workflow_key": "question_content",
            "node_key": "fetch",
            "executor_id": "local-default",
        }
    ]


def test_finalizer_applies_v005_and_removes_pipeline_config_json(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="V005",
        default_workflow_key="reading_analysis",
    )
    _set_pipeline_config(queries, str(workspace["id"]), {"local": 7})

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    with queries._connect_read() as conn:
        columns = {row["name"] for row in conn.execute("pragma table_info(workspaces)").fetchall()}
        versions = {
            row["version"]
            for row in conn.execute("select version from schema_migrations").fetchall()
        }

    assert "pipeline_config_json" not in columns
    assert 5 in versions


def test_v005_rolls_back_when_drop_column_is_not_supported(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Unsupported SQLite",
        default_workflow_key="reading_analysis",
    )["id"]
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_executor_bootstrap_state(workspace_id) values (?)",
            (workspace_id,),
        )

    with pytest.raises(MigrationError), queries.connect() as conn:
        conn.set_authorizer(
            lambda action, *_args: (
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE else sqlite3.SQLITE_OK
            )
        )
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    assert _table_exists(queries, "workspace_agent_assignments")
    assert _table_exists(queries, "workspace_executor_bootstrap_state")
    with queries._connect_read() as conn:
        columns = {row["name"] for row in conn.execute("pragma table_info(workspaces)")}
        version = conn.execute("select 1 from schema_migrations where version = 5").fetchone()
        allocation = conn.execute(
            "select 1 from workspace_executor_allocations where workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    assert "pipeline_config_json" in columns
    assert version is None
    assert allocation is None


def _table_exists(queries: JobQueries, table: str) -> bool:
    with queries._connect_read() as conn:
        row = conn.execute(
            "select 1 from sqlite_master where type='table' and name=?", (table,)
        ).fetchone()
        return row is not None


def test_dry_run_returns_report_without_writing(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Dry Run",
        default_workflow_key="reading_analysis",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 3)

    with queries.connect() as conn:
        report = finalize_legacy_executor_schema(
            conn, _sample_pipelines(), _sample_executors(), dry_run=True
        )

    assert report.issues == ()
    assert _fetch_all_allocations(queries) == []
    assert _table_exists(queries, "workspace_agent_assignments")


def test_dry_run_raises_blocked_error_and_leaves_legacy_data(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Blocked Dry Run",
        default_workflow_key="reading_analysis",
    )["id"]
    _insert_legacy_agent_assignment(queries, workspace_id, "bad-agent", 2)

    with pytest.raises(MigrationBlockedError), queries.connect() as conn:
        finalize_legacy_executor_schema(
            conn, _sample_pipelines(), _sample_executors(), dry_run=True
        )

    assert _table_exists(queries, "workspace_agent_assignments")
    rows = _list_legacy_agent_assignments(queries, workspace_id)
    assert any(row["agent_id"] == "bad-agent" for row in rows)


def _seed_default_workspace_assignment(tmp_path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir=jobs_dir)
    ensure_legacy_workspace_tables(queries)
    _insert_legacy_agent_assignment(queries, "default", "pi", 3)


def test_app_startup_materializes_executor_configuration_without_worker(tmp_path: Path) -> None:
    _seed_default_workspace_assignment(tmp_path)
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        response = client.get("/api/workspaces/default/executor-configuration")

    assert response.status_code == 200
    assert {row["executor_id"] for row in response.json()["allocations"]} == {
        "local-default",
        "pi-default",
    }
    backups = list(tmp_path.glob("video_hive-before-v005-*.sqlite"))
    assert len(backups) == 1


def test_app_startup_materialization_is_idempotent(tmp_path: Path) -> None:
    _seed_default_workspace_assignment(tmp_path)
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        first = client.get("/api/workspaces/default/executor-configuration").json()

    app2 = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app2) as client:
        second = client.get("/api/workspaces/default/executor-configuration").json()

    assert first == second


def test_app_startup_preserves_user_modified_executor_configuration(tmp_path: Path) -> None:
    _seed_default_workspace_assignment(tmp_path)
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        client.get("/api/workspaces/default/executor-configuration")

    with app.state.job_db.connect() as conn:
        conn.execute(
            "update workspace_executor_allocations set concurrency_limit = ? "
            "where workspace_id = ? and executor_id = ?",
            (999, "default", "local-default"),
        )

    app2 = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app2) as client:
        config = client.get("/api/workspaces/default/executor-configuration").json()

    allocations = {row["executor_id"]: row["concurrency_limit"] for row in config["allocations"]}
    assert allocations["local-default"] == 999
    assert allocations["pi-default"] == 3


def test_app_startup_aborts_when_finalization_blocked(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir=jobs_dir)
    ensure_legacy_workspace_tables(queries)
    _insert_legacy_agent_assignment(queries, "default", "unknown-agent", 2)

    with pytest.raises(RuntimeError, match="finalize-workspace-executor-migration.py --check"):
        create_app(data_dir=tmp_path, start_worker=False)


def test_finalizer_interruption_before_commit_retains_backup_and_reruns(
    queries: JobQueries,
) -> None:
    """A crash before the finalizer commits leaves the backup and allows a safe rerun."""
    workspace_id = queries.create_workspace(name="Legacy", default_workflow_key="reading_analysis")[
        "id"
    ]
    _set_pipeline_config(queries, workspace_id, {"local": 3})
    _insert_legacy_agent_assignment(queries, workspace_id, "pi", 2)

    backup_path = queries.path.parent / "v005-backup.sqlite"

    def block_schema_history_insert(action: int, *args: object) -> int:
        if action == sqlite3.SQLITE_INSERT and args[0] == "schema_migrations":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    with pytest.raises(sqlite3.DatabaseError), queries.connect() as conn:
        conn.set_authorizer(block_schema_history_insert)
        finalize_legacy_executor_schema(
            conn, _sample_pipelines(), _sample_executors(), backup_path=backup_path
        )

    assert backup_path.is_file()
    assert _table_exists(queries, "workspace_agent_assignments")

    with queries.connect() as conn:
        report = finalize_legacy_executor_schema(
            conn, _sample_pipelines(), _sample_executors(), backup_path=backup_path
        )

    assert report.issues == ()
    assert not _table_exists(queries, "workspace_agent_assignments")

    allocations = {
        row["executor_id"]: row["concurrency_limit"]
        for row in _fetch_all_allocations(queries)
        if row["workspace_id"] == workspace_id
    }
    assert allocations == {"local-default": 3, "pi-default": 2}
    assert backup_path.is_file()
