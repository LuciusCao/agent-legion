import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

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
from server.app.pipelines.definition import (
    PipelineDefinition,
    PipelineIntake,
    PipelineNode,
)


@pytest.fixture
def queries(tmp_path: Path) -> JobQueries:
    db_path = tmp_path / "jobs.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return JobQueries(db_path, jobs_dir)


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


def _sample_pipelines() -> list[PipelineDefinition]:
    return [_sample_pipeline(), _legacy_unconfigured_agent_pipeline()]


def _set_pipeline_config(queries: JobQueries, workspace_id: str, config: dict[str, Any]) -> None:
    with queries.connect() as conn:
        conn.execute(
            "update workspaces set pipeline_config_json = ? where id = ?",
            (json.dumps(config, ensure_ascii=False, sort_keys=True), workspace_id),
        )


def _sample_pipeline() -> PipelineDefinition:
    return PipelineDefinition(
        key="reading_analysis",
        label="Reading Analysis",
        intake=PipelineIntake(),
        nodes={
            "local_a": PipelineNode(
                key="local_a",
                label="Local A",
                capability="local_a",
            ),
            "local_b": PipelineNode(
                key="local_b",
                label="Local B",
                capability="local_b",
            ),
            "pi_a": PipelineNode(
                key="pi_a",
                label="Pi A",
                capability="pi_a",
            ),
        },
    )


def _legacy_unconfigured_agent_pipeline() -> PipelineDefinition:
    return PipelineDefinition(
        key="question_content",
        label="Question Content",
        intake=PipelineIntake(),
        nodes={
            "fetch": PipelineNode(
                key="fetch",
                label="Fetch",
                capability="local_a",
            ),
            "understand": PipelineNode(
                key="understand",
                label="Understand",
                capability="understand",
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
            "select workspace_id, pipeline_key, node_key, executor_id "
            "from workspace_node_bindings order by node_key"
        ).fetchall()
        return [dict(row) for row in rows]


def _fetch_all_node_limits(queries: JobQueries) -> list[dict]:
    with queries._connect_read() as conn:
        rows = conn.execute(
            "select workspace_id, pipeline_key, node_key, concurrency_limit "
            "from workspace_node_limits order by node_key"
        ).fetchall()
        return [dict(row) for row in rows]


def test_finalizer_materializes_local_only_workspace(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="Local Workspace",
        default_pipeline_key="reading_analysis",
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
            "pipeline_key": "reading_analysis",
            "node_key": "local_a",
            "executor_id": "local-default",
        },
        {
            "workspace_id": workspace_id,
            "pipeline_key": "reading_analysis",
            "node_key": "local_b",
            "executor_id": "local-default",
        },
    ]

    limits = [row for row in _fetch_all_node_limits(queries) if row["workspace_id"] == workspace_id]
    assert limits == [
        {
            "workspace_id": workspace_id,
            "pipeline_key": "reading_analysis",
            "node_key": "local_a",
            "concurrency_limit": 1,
        },
        {
            "workspace_id": workspace_id,
            "pipeline_key": "reading_analysis",
            "node_key": "local_b",
            "concurrency_limit": 1,
        },
    ]


def test_finalizer_materializes_exact_pi_assignment(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="Pi Workspace",
        default_pipeline_key="reading_analysis",
    )
    workspace_id = str(workspace["id"])
    _set_pipeline_config(queries, workspace_id, {"nodes": {"local_a": 1}})
    queries.upsert_workspace_agent_assignment(workspace_id, "pi", 3)

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
        default_pipeline_key="reading_analysis",
    )["id"]
    queries.upsert_workspace_agent_assignment(workspace_id, "pi", 99)

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
        default_pipeline_key="reading_analysis",
    )["id"]
    queries.upsert_workspace_agent_assignment(workspace_id, "unknown", 2)

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    issues = {issue.constraint for issue in exc_info.value.report.issues}
    assert "agent_id" in issues
    assert _table_exists(queries, "workspace_agent_assignments")


def test_finalizer_blocks_on_invalid_legacy_limit(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="Bad Limit",
        default_pipeline_key="reading_analysis",
    )
    _set_pipeline_config(queries, str(workspace["id"]), {"local": 0})

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    issues = {issue.constraint for issue in exc_info.value.report.issues}
    assert "pipeline_config_json.local" in issues


def test_finalizer_blocks_on_missing_pipeline_definition(queries: JobQueries) -> None:
    queries.create_workspace(
        name="Missing Pipeline",
        default_pipeline_key="nonexistent",
    )

    with pytest.raises(MigrationBlockedError) as exc_info, queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    issues = {issue.constraint for issue in exc_info.value.report.issues}
    assert "default_pipeline_key" in issues


def test_finalizer_is_idempotent_after_v005(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Idempotent",
        default_pipeline_key="reading_analysis",
    )["id"]
    queries.upsert_workspace_agent_assignment(workspace_id, "pi", 3)

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
        default_pipeline_key="question_content",
    )["id"]

    with queries.connect() as conn:
        finalize_legacy_executor_schema(conn, _sample_pipelines(), _sample_executors())

    bindings = [row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace_id]
    assert bindings == [
        {
            "workspace_id": workspace_id,
            "pipeline_key": "question_content",
            "node_key": "fetch",
            "executor_id": "local-default",
        }
    ]


def test_finalizer_applies_v005_and_removes_pipeline_config_json(queries: JobQueries) -> None:
    workspace = queries.create_workspace(
        name="V005",
        default_pipeline_key="reading_analysis",
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


def _table_exists(queries: JobQueries, table: str) -> bool:
    with queries._connect_read() as conn:
        row = conn.execute(
            "select 1 from sqlite_master where type='table' and name=?", (table,)
        ).fetchone()
        return row is not None


def test_dry_run_returns_report_without_writing(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Dry Run",
        default_pipeline_key="reading_analysis",
    )["id"]
    queries.upsert_workspace_agent_assignment(workspace_id, "pi", 3)

    with queries.connect() as conn:
        report = finalize_legacy_executor_schema(
            conn, [_sample_pipeline()], _sample_executors(), dry_run=True
        )

    assert report.issues == ()
    assert _fetch_all_allocations(queries) == []
    assert _table_exists(queries, "workspace_agent_assignments")


def test_dry_run_raises_blocked_error_and_leaves_legacy_data(queries: JobQueries) -> None:
    workspace_id = queries.create_workspace(
        name="Blocked Dry Run",
        default_pipeline_key="reading_analysis",
    )["id"]
    queries.upsert_workspace_agent_assignment(workspace_id, "bad-agent", 2)

    with pytest.raises(MigrationBlockedError), queries.connect() as conn:
        finalize_legacy_executor_schema(
            conn, [_sample_pipeline()], _sample_executors(), dry_run=True
        )

    assert _table_exists(queries, "workspace_agent_assignments")
    rows = queries.list_workspace_agents(workspace_id)
    assert any(row["agent_id"] == "bad-agent" for row in rows)


def _seed_default_workspace_assignment(tmp_path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    JobQueries(db_path, jobs_dir=jobs_dir).upsert_workspace_agent_assignment("default", "pi", 3)


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
    queries.upsert_workspace_agent_assignment("default", "unknown-agent", 2)

    with pytest.raises(RuntimeError, match="finalize-workspace-executor-migration.py --check"):
        create_app(data_dir=tmp_path, start_worker=False)
