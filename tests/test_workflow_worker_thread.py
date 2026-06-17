import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from server.app.executors.config import (
    LocalCapabilityConfig,
    LocalExecutorConfig,
    PiCapabilityConfig,
    PiExecutorConfig,
)
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import ExecutorRuntimeConfig
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.workflow_worker_thread import WorkflowWorkerThread
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)
from tests.helpers import make_pipeline_worker


def _make_definition(nodes: list[WorkflowNode]) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="test",
        label="Test",
        intake=WorkflowIntake(),
        nodes={n.key: n for n in nodes},
    )


def _local_node(key: str, outputs: list[str] | None = None) -> WorkflowNode:
    return WorkflowNode(
        key=key,
        label=key,
        capability=key,
        outputs=outputs or ["output.json"],
    )


class RecordingExecutor:
    kind = "local"

    def __init__(self, executor_id: str, block_event: threading.Event | None = None):
        self.id = executor_id
        self.kind = "local"
        self.block_event = block_event or threading.Event()
        self.contexts: list[ExecutionContext] = []
        self._cancelled: set[str] = set()

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        self.contexts.append(context)
        self.block_event.wait(timeout=10)
        for output in context.expected_outputs:
            (context.job_dir / output).write_text('{"done": true}', encoding="utf-8")
        return ExecutionResult(
            status="completed",
            exit_code=0,
            produced_artifacts=tuple(context.expected_outputs),
        )

    def cancel(self, execution_id: str) -> None:
        self._cancelled.add(execution_id)


def _make_worker(
    tmp_path: Path,
    db_path: Path,
    executor: RecordingExecutor,
    definitions: list[WorkflowDefinition],
) -> WorkflowWorkerThread:
    executor_def = LocalExecutorConfig(
        kind="local",
        global_capacity=2,
        capabilities={"fetch": LocalCapabilityConfig(handler="dummy.handler")},
    )
    registry = ExecutorRegistry(
        executors={"local-default": executor},
        global_capacities={"local-default": 2},
        definitions={"local-default": executor_def},
    )
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    leases = ExecutorLeaseRepository(db_path)
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


def test_worker_creates_shared_pool_per_executor_id(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    executor = RecordingExecutor("local-default")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])

    worker._poll()

    assert "local-default" in worker._pools
    assert worker._pools["local-default"]._max_workers == 2
    # No per-workspace pools should exist
    assert not hasattr(worker, "_ws_local_executors") or not worker._ws_local_executors
    assert not hasattr(worker, "_ws_agent_executors") or not worker._ws_agent_executors

    worker.stop()


def test_poll_submits_ready_local_node(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    processed = worker._poll()

    assert processed is True
    assert worker.leases.active_counts("local-default").get("global", 0) == 1
    assert len(worker._futures) == 1

    executor.block_event.set()
    worker.stop()


def test_poll_skips_duplicate_submissions(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    block_event = threading.Event()
    executor = RecordingExecutor("local-default", block_event=block_event)
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()
    assert worker.leases.active_counts("local-default").get("global", 0) == 1

    worker._poll()
    assert worker.leases.active_counts("local-default").get("global", 0) == 1

    block_event.set()
    worker.stop()


def test_poll_skips_paused_workspace(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
    definition = _make_definition([_local_node("fetch")])

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    control = MagicMock()
    control.is_paused = lambda ws_id: ws_id == ws["id"]
    worker.workspace_worker_control = control

    processed = worker._poll()

    assert processed is False
    assert worker.leases.active_counts("local-default").get("global", 0) == 0

    worker.stop()


def test_poll_fails_node_without_binding(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node["status"] == "failed"
    assert "No Executor binding" in node["error_message"]

    worker.stop()


def test_poll_fails_node_with_unsupported_capability(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
    executor.supports = lambda capability: capability == "other"  # type: ignore[method-assign]
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker._poll()

    node = job_db.get_job_node(job["id"], "fetch")
    assert node["status"] == "failed"
    assert "does not support capability" in node["error_message"]

    worker.stop()


def test_stop_shuts_down_shared_pools(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    executor = RecordingExecutor("local-default")
    worker = _make_worker(tmp_path, db_path, executor, [_make_definition([_local_node("fetch")])])

    worker._poll()
    pool = worker._pools["local-default"]
    worker.stop()

    assert pool._shutdown is True


def test_poll_skips_paused_job(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
    definition = _make_definition([_local_node("fetch")])

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    job_db.pause_job(job["id"], "awaiting_resources")
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    processed = worker._poll()

    assert processed is False
    assert worker.leases.active_counts("local-default").get("global", 0) == 0
    assert len(worker._futures) == 0

    worker.stop()


def test_poll_runs_only_target_closure_in_until_node_mode(tmp_path: Path) -> None:
    from server.app.workflows.execution_control import allowed_nodes

    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
    definition = _make_definition(
        [
            _local_node("root"),
            WorkflowNode(key="left", label="left", capability="left", after=["root"]),
            WorkflowNode(key="target", label="target", capability="target", after=["left"]),
            WorkflowNode(key="right", label="right", capability="right", after=["root"]),
        ]
    )

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["root", "left", "target", "right"],
        workspace_id=ws["id"],
    )
    job_db.set_job_execution_target(job["id"], "target")
    with job_db.connect() as conn:
        for node in definition.nodes.values():
            conn.execute(
                "insert into workspace_node_bindings (workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
                (ws["id"], "test", node.key, "local-default"),
            )
        conn.execute(
            "insert into workspace_executor_allocations (workspace_id, executor_id, concurrency_limit) values (?, ?, ?)",
            (ws["id"], "local-default", 2),
        )
        conn.execute(
            "insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
            (ws["id"], "test", "root", 1),
        )
        conn.execute(
            "insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
            (ws["id"], "test", "left", 1),
        )
        conn.execute(
            "insert into workspace_node_limits (workspace_id, workflow_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
            (ws["id"], "test", "target", 1),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    executor.block_event.set()

    for _ in range(20):
        worker._poll()
        job_after = job_db.get_job(job["id"])
        if job_after and job_after["status"] in ("paused", "completed"):
            break

    worker.stop()

    statuses = {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job["id"])}
    allowed = allowed_nodes(
        definition,
        {"execution_mode": "until_node", "target_node_key": "target"},
    )
    for key in allowed:
        assert statuses[key] == "completed"
    assert statuses["right"] == "pending"

    job_after = job_db.get_job(job["id"])
    assert job_after is not None
    assert job_after["status"] == "paused"
    assert job_after["execution_paused"] == 1
    assert job_after["pause_reason"] == "target_reached"


def test_make_pipeline_worker_runs_reading_analysis_local_node(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "server.app.workflows.reading_analysis.get_token",
        lambda env, config: "test-token",
    )
    monkeypatch.setattr(
        "server.app.workflows.reading_analysis.fetch_question_detail",
        lambda question_id, api_url, token: SimpleNamespace(
            question_id=question_id,
            title="Question Q100",
            normalized={},
            payload={"uuid": question_id},
        ),
    )
    queries = JobQueries(tmp_path / "video_hive.sqlite", jobs_dir=tmp_path / "jobs")
    worker, definition = make_pipeline_worker(tmp_path, queries)
    job = queries.create_job(
        workflow_key="reading_analysis",
        source_type="question",
        source_id="Q100",
        batch_id="",
        title="Question Q100",
        node_keys=list(definition.nodes),
    )

    processed = worker._poll()

    assert processed is True
    assert worker._futures
    for future in worker._futures.values():
        future.result(timeout=5)

    node = queries.get_job_node(job["id"], "fetch_questions")
    assert node["status"] == "completed"
    assert (Path(job["storage_dir"]) / "questions.json").exists()

    worker.stop()


class RecordingPiExecutor:
    kind = "pi"

    def __init__(self, executor_id: str, block_event: threading.Event | None = None):
        self.id = executor_id
        self.kind = "pi"
        self.block_event = block_event or threading.Event()
        self.contexts: list[ExecutionContext] = []

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        self.contexts.append(context)
        self.block_event.wait(timeout=10)
        for output in context.expected_outputs:
            (context.job_dir / output).write_text('{"done": true}', encoding="utf-8")
        return ExecutionResult(
            status="completed",
            exit_code=0,
            produced_artifacts=tuple(context.expected_outputs),
        )

    def cancel(self, execution_id: str) -> None:
        pass


def _make_pi_worker(
    tmp_path: Path,
    db_path: Path,
    executor: RecordingPiExecutor,
    definitions: list[WorkflowDefinition],
    agent_manager: Any | None = None,
) -> WorkflowWorkerThread:
    executor_def = PiExecutorConfig(
        kind="pi",
        global_capacity=2,
        capabilities={"fetch": PiCapabilityConfig(skill="test_skill")},
    )
    registry = ExecutorRegistry(
        executors={"pi-default": executor},
        global_capacities={"pi-default": 2},
        definitions={"pi-default": executor_def},
    )
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    leases = ExecutorLeaseRepository(db_path)
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
        agent_manager=agent_manager,
    )
    worker._definitions = definitions
    return worker


def test_poll_updates_agent_status_for_pi_executor(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    block_event = threading.Event()
    executor = RecordingPiExecutor("pi-default", block_event=block_event)
    definition = _make_definition([_local_node("fetch")])
    agent_manager = MagicMock()

    job = job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into workspace_node_bindings
            (workspace_id, workflow_key, node_key, executor_id)
            values (?, ?, ?, ?)
            """,
            (ws["id"], "test", "fetch", "pi-default"),
        )
        conn.execute(
            """
            insert into workspace_executor_allocations
            (workspace_id, executor_id, concurrency_limit)
            values (?, ?, ?)
            """,
            (ws["id"], "pi-default", 2),
        )

    worker = _make_pi_worker(tmp_path, db_path, executor, [definition], agent_manager)
    worker._poll()

    agent_manager.set_busy.assert_called_once()
    args, kwargs = agent_manager.set_busy.call_args
    assert args[0] == "pi"
    assert kwargs["workspace_id"] == ws["id"]
    assert args[1]["id"] == job["id"]
    assert args[1]["title"] == "Q1"
    assert args[1]["external_id"] == "Q1"
    assert args[1]["current_phase"] == "fetch"

    block_event.set()
    worker.stop()

    agent_manager.set_idle.assert_called_once_with("pi", workspace_id=ws["id"])


def test_poll_does_not_update_agent_status_for_local_executor(tmp_path: Path) -> None:
    db_path = tmp_path / "video_hive.sqlite"
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Test WS")
    executor = RecordingExecutor("local-default")
    definition = _make_definition([_local_node("fetch")])
    agent_manager = MagicMock()

    job_db.create_job(
        workflow_key="test",
        source_type="question",
        source_id="Q1",
        batch_id="",
        title="Q1",
        node_keys=["fetch"],
        workspace_id=ws["id"],
    )
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into workspace_node_bindings
            (workspace_id, workflow_key, node_key, executor_id)
            values (?, ?, ?, ?)
            """,
            (ws["id"], "test", "fetch", "local-default"),
        )
        conn.execute(
            """
            insert into workspace_executor_allocations
            (workspace_id, executor_id, concurrency_limit)
            values (?, ?, ?)
            """,
            (ws["id"], "local-default", 2),
        )

    worker = _make_worker(tmp_path, db_path, executor, [definition])
    worker.agent_manager = agent_manager
    worker._poll()

    executor.block_event.set()
    worker.stop()

    agent_manager.set_busy.assert_not_called()
    agent_manager.set_idle.assert_not_called()
