"""Dispatch-time executor node config injection (spec D15)."""

from __future__ import annotations

from pathlib import Path

from server.app.executors.config import LocalCapabilityConfig, LocalExecutorConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker_thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _local_node, _make_definition

SCHEMA = {
    "type": "object",
    "properties": {
        "bank_version": {"type": "string", "default": "v5"},
        "country_id": {"type": "string"},
    },
}


def _make_worker(
    tmp_path: Path,
    executor: RecordingExecutor,
    definitions: list[WorkflowDefinition],
) -> WorkflowWorkerThread:
    executor_def = LocalExecutorConfig(
        kind="local",
        global_capacity=2,
        capabilities={
            "fetch": LocalCapabilityConfig(handler="dummy.handler", config_schema=SCHEMA),
        },
    )
    registry = ExecutorRegistry(
        executors={"local-default": executor},
        global_capacities={"local-default": 2},
        definitions={"local-default": executor_def},
    )
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    leases = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    runtime = ExecutionRuntime(
        leases=leases,
        registry=registry,
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=5,
    )
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={},
        executor_definitions=registry.definitions(),
    )
    settings.executor_runtime = ExecutorRuntimeConfig.model_validate(
        {
            "workflows": {"enabled": True},
            "openclaw": {"command_template": ["openclaw"]},
        }
    )
    worker = WorkflowWorkerThread(
        job_db=job_db,
        leases=leases,
        registry=registry,
        runtime=runtime,
        settings=settings,
    )
    worker._definitions = definitions
    return worker


def _prepare_job(
    job_db: JobQueries,
    node: WorkflowNode,
    *,
    workspace: dict | None = None,
    batch_id: str = "",
) -> tuple[dict, dict]:
    ws = workspace or job_db.create_workspace("Test WS", default_workflow_key="test")
    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id=batch_id,
        title="Q1",
        node_keys=[node.key],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", node.key, "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )
    return ws, job


def test_dispatch_injects_live_node_config_chain(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    executor = RecordingExecutor("local-default")
    node = WorkflowNode(
        key="fetch",
        label="fetch",
        capability="fetch",
        config={"bank_version": "v9"},
        outputs=["output.json"],
    )
    definition = _make_definition([node])
    ws, _job = _prepare_job(job_db, node)
    job_db.update_workspace(ws["id"], node_config={"test": {"fetch": {"country_id": "9"}}})
    worker = _make_worker(tmp_path, executor, [definition])
    executor.block_event.set()

    assert worker._poll() is True
    for future in worker._futures.values():
        future.result(timeout=5)

    # schema default ← workflow node config ← workspace override
    assert executor.contexts[0].node_config == {"bank_version": "v9", "country_id": "9"}
    worker.stop()


def test_dispatch_prefers_frozen_batch_node_config(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    executor = RecordingExecutor("local-default")
    node = _local_node("fetch")
    ws = job_db.create_workspace("Test WS", default_workflow_key="test")
    batch = job_db.create_batch(
        "test",
        "batch_by_ids",
        {"node_config": {"fetch": {"bank_version": "frozen"}}},
        ws["id"],
    )
    _ws, _job = _prepare_job(job_db, node, workspace=ws, batch_id=str(batch["id"]))
    job_db.update_workspace(ws["id"], node_config={"test": {"fetch": {"bank_version": "v9"}}})
    worker = _make_worker(tmp_path, executor, [_make_definition([node])])
    executor.block_event.set()

    assert worker._poll() is True
    for future in worker._futures.values():
        future.result(timeout=5)

    # The intake snapshot wins over any live layer, unchanged.
    assert executor.contexts[0].node_config == {"bank_version": "frozen"}
    worker.stop()


def test_dispatch_fails_node_on_invalid_workspace_override(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    executor = RecordingExecutor("local-default")
    node = _local_node("fetch")
    _ws, job = _prepare_job(job_db, node)
    job_db.update_workspace(_ws["id"], node_config={"test": {"fetch": {"nope": 1}}})
    worker = _make_worker(tmp_path, executor, [_make_definition([node])])

    worker._poll()

    failed = job_db.get_job_node(job["id"], "fetch")
    assert failed is not None
    assert failed["status"] == "failed"
    assert "unknown keys" in failed["error_message"]
    assert not executor.contexts
    worker.stop()
