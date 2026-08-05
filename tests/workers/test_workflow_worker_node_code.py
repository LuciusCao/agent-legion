"""Dispatch-time custom node code resolution (EXEC-CODE-002).

Priority: job-frozen version → published custom version → builtin (None).
"""

from __future__ import annotations

from pathlib import Path

from server.app.executors.config import CodeCapabilityConfig, CodeExecutorConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.services.node_codes import NodeCodeService, code_hash
from server.app.settings import Settings
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _local_node, _make_definition

CUSTOM_V1 = "def run(job, job_dir, runtime):\n    return 'v1'\n"
CUSTOM_V2 = "def run(job, job_dir, runtime):\n    return 'v2'\n"


def _make_worker(
    tmp_path: Path,
    job_db: JobQueries,
    executor: RecordingExecutor,
    definitions: list[WorkflowDefinition],
    *,
    custom_nodes_enabled: bool = True,
) -> WorkflowWorkerThread:
    executor_def = CodeExecutorConfig(
        kind="code",
        global_capacity=2,
        capabilities={"fetch": CodeCapabilityConfig(path="workflow_nodes/question_intake.py")},
    )
    registry = ExecutorRegistry(
        executors={"code-default": executor},
        global_capacities={"code-default": 2},
        definitions={"code-default": executor_def},
    )
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
            "workflows": {"enabled": True, "custom_nodes_enabled": custom_nodes_enabled},
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


def _prepare_job(tmp_path: Path, node: WorkflowNode, batch_payload: dict | None = None):
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS", default_workflow_key="test")
    batch_id = ""
    if batch_payload is not None:
        batch_id = str(job_db.create_batch("test", "batch_by_ids", batch_payload, ws["id"])["id"])
    job_db.create_job(
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
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (%s, %s, %s, %s)",
            (ws["id"], "test", node.key, "code-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (%s, %s, %s)",
            (ws["id"], "code-default", 2),
        )
    return job_db, ws


def _dispatch(tmp_path: Path, worker: WorkflowWorkerThread) -> None:
    worker._poll()
    for future in worker._futures.values():
        future.result(timeout=5)


def test_dispatch_uses_published_custom_code(tmp_path: Path) -> None:
    node = _local_node("fetch")
    job_db, ws = _prepare_job(tmp_path, node)
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V1, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    assert executor.contexts[0].node_code == CUSTOM_V1
    worker.stop()


def test_dispatch_prefers_frozen_code_version(tmp_path: Path) -> None:
    node = _local_node("fetch")
    codes_ws = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    seed_ws = codes_ws.create_workspace("Seed WS", default_workflow_key="test")
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(seed_ws["id"], "test", "fetch", CUSTOM_V1, "user:u1")
    codes.publish(seed_ws["id"], "test", "fetch")
    frozen = {"fetch": {"version": 1, "code_hash": code_hash(CUSTOM_V1)}}
    job_db, ws = _prepare_job(tmp_path, node, batch_payload={"node_code_versions": frozen})
    # The frozen pin rides this job's batch; the workspace pins its own codes.
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V1, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V2, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    executor = RecordingExecutor("code-default")
    worker = _make_worker(tmp_path, job_db, executor, [_make_definition([node])])
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    # The job froze v1 at intake; publishing v2 must not affect it.
    assert executor.contexts[0].node_code == CUSTOM_V1
    worker.stop()


def test_dispatch_falls_back_to_builtin_when_gate_disabled(tmp_path: Path) -> None:
    node = _local_node("fetch")
    job_db, ws = _prepare_job(tmp_path, node)
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(ws["id"], "test", "fetch", CUSTOM_V1, "user:u1")
    codes.publish(ws["id"], "test", "fetch")
    executor = RecordingExecutor("code-default")
    worker = _make_worker(
        tmp_path, job_db, executor, [_make_definition([node])], custom_nodes_enabled=False
    )
    executor.block_event.set()

    _dispatch(tmp_path, worker)

    # Gate off: dispatch never consults custom codes and nothing breaks.
    assert executor.contexts[0].node_code is None
    worker.stop()
