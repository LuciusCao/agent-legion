from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.executors.config import (
    ExecutorConfig,
    LocalCapabilityConfig,
    LocalExecutorConfig,
    PiCapabilityConfig,
    PiExecutorConfig,
)
from server.app.jobs.queries import JobQueries
from server.app.main import create_app
from server.app.pipelines.definition import (
    PipelineAgent,
    PipelineConcurrency,
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


def _sample_pipeline() -> PipelineDefinition:
    return PipelineDefinition(
        key="reading_analysis",
        label="Reading Analysis",
        concurrency=PipelineConcurrency(
            local=2,
            agent=1,
            nodes={"local_a": 3, "local_b": 5},
        ),
        intake=PipelineIntake(),
        nodes={
            "local_a": PipelineNode(
                key="local_a",
                label="Local A",
                capability="local_a",
                runner="local",
            ),
            "local_b": PipelineNode(
                key="local_b",
                label="Local B",
                capability="local_b",
                runner="local",
            ),
            "pi_a": PipelineNode(
                key="pi_a",
                label="Pi A",
                capability="pi_a",
                runner="agent",
                agent=PipelineAgent(
                    engine="pi",
                    skill="reading_analysis/pi_a",
                    tools=["read", "write", "bash"],
                ),
            ),
        },
    )


def _legacy_unconfigured_agent_pipeline() -> PipelineDefinition:
    return PipelineDefinition(
        key="question_content",
        label="Question Content",
        concurrency=PipelineConcurrency(local=2, agent=1),
        intake=PipelineIntake(),
        nodes={
            "fetch": PipelineNode(
                key="fetch",
                label="Fetch",
                capability="fetch",
                runner="local",
            ),
            "understand": PipelineNode(
                key="understand",
                label="Understand",
                capability="understand",
                runner="agent",
            ),
        },
    )


def _create_legacy_workspace(queries: JobQueries) -> str:
    workspace = queries.create_workspace(
        name="Legacy Workspace",
        default_pipeline_key="reading_analysis",
        pipeline_config={"nodes": {"local_a": 1}},
    )
    workspace_id = str(workspace["id"])
    queries.upsert_workspace_agent_assignment(workspace_id, "pi", 3)
    return workspace_id


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


def test_bootstrap_creates_legacy_workspace_defaults(queries: JobQueries) -> None:
    from server.app.executors.bootstrap import bootstrap_workspace_executor_defaults

    workspace_id = _create_legacy_workspace(queries)
    executors = _sample_executors()
    pipeline = _sample_pipeline()

    bootstrap_workspace_executor_defaults(queries, [pipeline], executors)

    allocations = [
        row for row in _fetch_all_allocations(queries) if row["workspace_id"] == workspace_id
    ]
    assert allocations == [
        {
            "workspace_id": workspace_id,
            "executor_id": "local-default",
            "concurrency_limit": 2,
        },
        {
            "workspace_id": workspace_id,
            "executor_id": "pi-default",
            "concurrency_limit": 3,
        },
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
        {
            "workspace_id": workspace_id,
            "pipeline_key": "reading_analysis",
            "node_key": "pi_a",
            "executor_id": "pi-default",
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
            "concurrency_limit": 5,
        },
    ]


def test_bootstrap_is_idempotent(queries: JobQueries) -> None:
    from server.app.executors.bootstrap import bootstrap_workspace_executor_defaults

    workspace_id = _create_legacy_workspace(queries)
    executors = _sample_executors()
    pipeline = _sample_pipeline()

    bootstrap_workspace_executor_defaults(queries, [pipeline], executors)
    first_allocations = [
        row for row in _fetch_all_allocations(queries) if row["workspace_id"] == workspace_id
    ]
    first_bindings = [
        row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace_id
    ]
    first_limits = [
        row for row in _fetch_all_node_limits(queries) if row["workspace_id"] == workspace_id
    ]

    bootstrap_workspace_executor_defaults(queries, [pipeline], executors)
    assert [
        row for row in _fetch_all_allocations(queries) if row["workspace_id"] == workspace_id
    ] == first_allocations
    assert [
        row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace_id
    ] == first_bindings
    assert [
        row for row in _fetch_all_node_limits(queries) if row["workspace_id"] == workspace_id
    ] == first_limits


def test_bootstrap_materializes_legacy_assignment_added_before_authoritative_save(
    queries: JobQueries,
) -> None:
    from server.app.executors.bootstrap import bootstrap_workspace_executor_defaults

    workspace = queries.create_workspace(
        name="Legacy Workspace",
        default_pipeline_key="reading_analysis",
    )
    workspace_id = str(workspace["id"])
    executors = _sample_executors()
    pipeline = _sample_pipeline()

    bootstrap_workspace_executor_defaults(queries, [pipeline], executors)
    queries.upsert_workspace_agent_assignment(workspace_id, "pi", 3)
    bootstrap_workspace_executor_defaults(queries, [pipeline], executors)

    allocations = {
        row["executor_id"]: row["concurrency_limit"]
        for row in _fetch_all_allocations(queries)
        if row["workspace_id"] == workspace_id
    }
    assert allocations["pi-default"] == 3
    bindings = [row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace_id]
    assert any(row["node_key"] == "pi_a" for row in bindings)


def test_bootstrap_does_not_restore_configuration_removed_after_materialization(
    queries: JobQueries,
) -> None:
    from server.app.executors.bootstrap import bootstrap_workspace_executor_defaults

    workspace_id = _create_legacy_workspace(queries)
    executors = _sample_executors()
    pipeline = _sample_pipeline()

    bootstrap_workspace_executor_defaults(queries, [pipeline], executors)
    queries.update_workspace_configuration(
        workspace_id,
        name="Legacy Workspace",
        description="",
        default_pipeline_key="reading_analysis",
        default_entity="question",
        resource_config={},
        intake_config={},
        pipeline_config={},
        executor_allocations=[],
        node_bindings=[],
        node_limits=[],
    )

    bootstrap_workspace_executor_defaults(queries, [pipeline], executors)

    assert [
        row for row in _fetch_all_allocations(queries) if row["workspace_id"] == workspace_id
    ] == []
    assert [
        row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace_id
    ] == []
    assert [
        row for row in _fetch_all_node_limits(queries) if row["workspace_id"] == workspace_id
    ] == []


def test_bootstrap_preserves_user_modified_rows(queries: JobQueries) -> None:
    from server.app.executors.bootstrap import bootstrap_workspace_executor_defaults

    workspace_id = _create_legacy_workspace(queries)
    executors = _sample_executors()
    pipeline = _sample_pipeline()

    bootstrap_workspace_executor_defaults(queries, [pipeline], executors)

    with queries.connect() as conn:
        conn.execute(
            "update workspace_executor_allocations set concurrency_limit = 999 "
            "where workspace_id = ? and executor_id = ?",
            (workspace_id, "local-default"),
        )

    bootstrap_workspace_executor_defaults(queries, [pipeline], executors)

    allocations = {
        row["executor_id"]: row["concurrency_limit"]
        for row in _fetch_all_allocations(queries)
        if row["workspace_id"] == workspace_id
    }
    assert allocations["local-default"] == 999
    assert allocations["pi-default"] == 3


def test_bootstrap_raises_when_local_default_executor_missing(queries: JobQueries) -> None:
    from server.app.executors.bootstrap import bootstrap_workspace_executor_defaults

    _create_legacy_workspace(queries)
    executors: dict[str, ExecutorConfig] = {"pi-default": _sample_executors()["pi-default"]}
    pipeline = _sample_pipeline()

    with pytest.raises(RuntimeError):
        bootstrap_workspace_executor_defaults(queries, [pipeline], executors)


def test_bootstrap_raises_when_pi_default_executor_missing(queries: JobQueries) -> None:
    from server.app.executors.bootstrap import bootstrap_workspace_executor_defaults

    _create_legacy_workspace(queries)
    executors: dict[str, ExecutorConfig] = {"local-default": _sample_executors()["local-default"]}
    pipeline = _sample_pipeline()

    with pytest.raises(RuntimeError):
        bootstrap_workspace_executor_defaults(queries, [pipeline], executors)


def test_bootstrap_does_not_bind_unconfigured_agent_node_to_local(queries: JobQueries) -> None:
    from server.app.executors.bootstrap import bootstrap_workspace_executor_defaults

    workspace = queries.create_workspace(
        "Question Workspace",
        default_pipeline_key="question_content",
    )
    pipeline = _legacy_unconfigured_agent_pipeline()

    bootstrap_workspace_executor_defaults(queries, [pipeline], _sample_executors())

    bindings = [
        row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace["id"]
    ]
    assert bindings == [
        {
            "workspace_id": workspace["id"],
            "pipeline_key": "question_content",
            "node_key": "fetch",
            "executor_id": "local-default",
        }
    ]


def test_bootstrap_does_not_bind_pi_node_without_workspace_allocation(
    queries: JobQueries,
) -> None:
    from server.app.executors.bootstrap import bootstrap_workspace_executor_defaults

    workspace = queries.create_workspace(
        "Unallocated Pi Workspace",
        default_pipeline_key="reading_analysis",
    )

    bootstrap_workspace_executor_defaults(queries, [_sample_pipeline()], _sample_executors())

    bindings = [
        row for row in _fetch_all_bindings(queries) if row["workspace_id"] == workspace["id"]
    ]
    assert {row["node_key"] for row in bindings} == {"local_a", "local_b"}
    assert all(row["executor_id"] == "local-default" for row in bindings)


def _executor_config_by_id(response_json: dict) -> dict[str, int]:
    return {row["executor_id"]: row["concurrency_limit"] for row in response_json["allocations"]}


def _seed_default_workspace_assignment(tmp_path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    JobQueries(db_path, jobs_dir=jobs_dir).upsert_workspace_agent_assignment("default", "pi", 3)


def test_app_startup_materializes_executor_configuration_without_worker(tmp_path) -> None:
    _seed_default_workspace_assignment(tmp_path)
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        response = client.get("/api/workspaces/default/executor-configuration")

    assert response.status_code == 200
    assert {row["executor_id"] for row in response.json()["allocations"]} == {
        "local-default",
        "pi-default",
    }


def test_app_startup_materialization_is_idempotent(tmp_path) -> None:
    _seed_default_workspace_assignment(tmp_path)
    app = create_app(data_dir=tmp_path, start_worker=False)

    with TestClient(app) as client:
        first = client.get("/api/workspaces/default/executor-configuration").json()

    # Simulate a second startup against the same database.
    app2 = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app2) as client:
        second = client.get("/api/workspaces/default/executor-configuration").json()

    assert first == second


def test_app_startup_preserves_user_modified_executor_configuration(tmp_path) -> None:
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

    allocations = _executor_config_by_id(config)
    assert allocations["local-default"] == 999
    assert allocations["pi-default"] == 3
