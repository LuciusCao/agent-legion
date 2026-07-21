"""Non-blocking remote completion path (phase 3, task 6).

Covers: submit-only ``RemoteExecutor.execute`` returning ``None``, the
runtime skipping heartbeat/finish for submit-only executors, broker
completion callbacks driving ``leases.finish`` (idempotently), the requeue
limit finishing leases as failed, and remote claims not growing per-executor
thread pools.
"""

from __future__ import annotations

import io
import tarfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.executors.config import RemoteCapabilityConfig, RemoteExecutorConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ClaimedExecution, ExecutionContext, ExecutionResult
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.remote import RemoteExecutor
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
    RemoteOutcome,
)
from server.app.executors.remote_completion import RemoteCompletionHandler
from server.app.executors.remote_payloads.pi import PiPayloadBuilder
from server.app.executors.runtime import ExecutionRuntime
from server.app.executors.runtime_config import PiRuntimeConfig
from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflow_worker_thread import WorkflowWorkerThread
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.executors.adapters.helpers import _make_skill_manager
from tests.executors.leases.helpers import (
    _claim_request,
    _create_job_in_workspace,
    _setup_workspace,
)
from tests.postgres_support import TEST_DATABASE_URL
from tests.test_workflow_worker_concurrency import _allocate, _bind

EXECUTOR_ID = "pi-remote"
CAPABILITY = "review_keywords"
SKILL = "question_comprehension_info/generate_key_info"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return TEST_DATABASE_URL


@pytest.fixture
def job_db(db_path: Path, tmp_path: Path) -> JobQueries:
    return JobQueries(db_path, tmp_path / "jobs")


@pytest.fixture
def leases(db_path: Path, job_db: JobQueries) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(db_path, job_db=job_db)


@pytest.fixture
def broker(db_path: Path, tmp_path: Path) -> RemoteExecutionBroker:
    init_db(db_path)
    return RemoteExecutionBroker(db_path, tmp_path / "bundles", claim_timeout_seconds=60.0)


@pytest.fixture
def remote_executor(tmp_path: Path, broker: RemoteExecutionBroker) -> RemoteExecutor:
    skill_manager = _make_skill_manager(tmp_path, SKILL, validate_script="#!/usr/bin/env python3\n")
    capabilities = {CAPABILITY: RemoteCapabilityConfig(skill=SKILL)}
    payload_builder = PiPayloadBuilder(
        PiRuntimeConfig(binary="pi", provider="deepseek", model="your-model-b"),
        skill_manager,
        capabilities,
    )
    return RemoteExecutor(EXECUTOR_ID, payload_builder, capabilities, broker)


@pytest.fixture
def handler(
    broker: RemoteExecutionBroker, leases: ExecutorLeaseRepository, tmp_path: Path
) -> RemoteCompletionHandler:
    completion_handler = RemoteCompletionHandler(broker, leases, tmp_path / "jobs")
    broker.register_completion_callback(completion_handler.handle_completion)
    return completion_handler


@pytest.fixture
def workspace(job_db: JobQueries) -> tuple[str, str]:
    return _setup_workspace(job_db, "WS", EXECUTOR_ID, workspace_limit=20, local_limit=None)


@pytest.fixture
def claim(leases: ExecutorLeaseRepository, workspace: tuple[str, str]) -> ClaimedExecution:
    workspace_id, job_id = workspace
    claimed = leases.try_claim(
        _claim_request(workspace_id, job_id, executor_id=EXECUTOR_ID, local_node_limit=None)
    )
    assert claimed is not None
    return claimed


@pytest.fixture
def execution_context(
    claim: ClaimedExecution, job_db: JobQueries, tmp_path: Path
) -> ExecutionContext:
    job = job_db.get_job(claim.job_id)
    assert job is not None
    job_dir = resolve_job_dir(job, tmp_path / "jobs")
    (job_dir / "in.json").write_text("{}", encoding="utf-8")
    return ExecutionContext(
        execution_id=claim.execution_id,
        lease_id=claim.lease_id,
        node_run_id=claim.node_run_id,
        executor_id=claim.executor_id,
        workspace_id=claim.workspace_id,
        job_id=claim.job_id,
        workflow_key=claim.workflow_key,
        node_key=claim.node_key,
        capability=claim.capability,
        workspace={"id": claim.workspace_id},
        job=job,
        job_dir=job_dir,
        log_path=tmp_path / "logs" / "run.log",
        inputs=("in.json",),
        expected_outputs=("out.json",),
    )


def _make_registry(executor: object) -> ExecutorRegistry:
    return ExecutorRegistry(
        executors={EXECUTOR_ID: executor},  # type: ignore[dict-item]
        global_capacities={EXECUTOR_ID: 20},
        definitions={
            EXECUTOR_ID: RemoteExecutorConfig(
                kind="remote",
                global_capacity=20,
                capabilities={CAPABILITY: RemoteCapabilityConfig(skill=SKILL)},
            )
        },
    )


@pytest.fixture
def runtime(leases: ExecutorLeaseRepository, remote_executor: RemoteExecutor) -> ExecutionRuntime:
    return ExecutionRuntime(
        leases=leases,
        registry=_make_registry(remote_executor),
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=60,
    )


def _result_archive(path: Path, *, node_key: str, run_token: str, output_name: str) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, content in (
            (output_name, b"{}"),
            (f"runs/{node_key}/{run_token}/events.jsonl", b'{"type":"done"}\n'),
            (f"runs/{node_key}/{run_token}/run.json", b"{}"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


def _complete_with_archive(broker: RemoteExecutionBroker, output_name: str = "out.json") -> bool:
    """Dequeue as worker w1, publish a result archive, and report completion."""
    remote_claim = broker.dequeue("w1", {CAPABILITY})
    assert remote_claim is not None
    archive = broker.bundle_dir / f"{remote_claim.execution_id}.result.tar.gz"
    _result_archive(
        archive,
        node_key=remote_claim.manifest["node_key"],
        run_token=remote_claim.manifest["run_token"],
        output_name=output_name,
    )
    outcome = RemoteOutcome(
        status="completed",
        exit_code=0,
        command=("pi", "--mode", "json"),
        skill_version=str(remote_claim.manifest["skill_version"]),
        result_archive_name=archive.name,
    )
    return broker.complete(remote_claim.execution_id, "w1", outcome)


def _node_run(job_db: JobQueries, job_id: str) -> dict[str, object]:
    with job_db.connect() as conn:
        row = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchone()
    assert row is not None
    return dict(row)


# ---- submit-only execute / runtime ----


def test_execute_returns_immediately_without_waiting(
    remote_executor: RemoteExecutor, execution_context: ExecutionContext
) -> None:
    started = threading.Event()
    result_box: list[object] = []

    def run() -> None:
        started.set()
        result_box.append(remote_executor.execute(execution_context))

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(5)
    thread.join(timeout=5)
    assert not thread.is_alive(), "execute must not block on wait_result"
    assert result_box == [None]


def test_runtime_run_skips_finish_for_submit_only(
    runtime: ExecutionRuntime,
    claim: ClaimedExecution,
    execution_context: ExecutionContext,
    leases: ExecutorLeaseRepository,
) -> None:
    result = runtime.run(claim, execution_context)
    assert result is None
    # The lease stays active (not finished); the completion callback drives finish.
    assert leases.has_active_for_job(claim.job_id, _utcnow())


class _ResultReturningSubmitOnlyExecutor:
    """Submit-only executor that violates the contract by returning a result."""

    id = EXECUTOR_ID
    kind = "remote"
    submit_only = True

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(
            status="failed",
            exit_code=1,
            error_message=f"capability {context.capability!r} is not supported",
            log_path=str(context.log_path),
        )

    def cancel(self, execution_id: str) -> None:
        return None


def test_submit_only_executor_returning_result_finishes_failed(
    leases: ExecutorLeaseRepository,
    claim: ClaimedExecution,
    execution_context: ExecutionContext,
    job_db: JobQueries,
) -> None:
    runtime = ExecutionRuntime(
        leases=leases,
        registry=_make_registry(_ResultReturningSubmitOnlyExecutor()),
        heartbeat_interval_seconds=1,
        lease_ttl_seconds=60,
    )
    result = runtime.run(claim, execution_context)
    assert result is None
    # The non-None return is a contract violation: logged and finished as failed.
    assert not leases.has_active_for_job(claim.job_id, _utcnow())
    node = job_db.get_job_node(claim.job_id, claim.node_key)
    assert node["status"] == "failed"
    assert "not supported" in node["error_message"]


# ---- broker completion callbacks ----


def test_completion_callback_finishes_lease_and_backfills(
    remote_executor: RemoteExecutor,
    execution_context: ExecutionContext,
    leases: ExecutorLeaseRepository,
    broker: RemoteExecutionBroker,
    handler: RemoteCompletionHandler,
    job_db: JobQueries,
) -> None:
    assert remote_executor.execute(execution_context) is None
    assert _complete_with_archive(broker) is True
    # The handler is registered on the broker; finish happens synchronously in the callback.
    assert not leases.has_active_for_job(execution_context.job_id, _utcnow())
    assert (execution_context.job_dir / "out.json").is_file()
    node = job_db.get_job_node(execution_context.job_id, execution_context.node_key)
    assert node["status"] == "completed"
    run = _node_run(job_db, execution_context.job_id)
    assert run["status"] == "completed"
    assert run["runner"] == "w1"


def test_duplicate_result_report_is_idempotent(
    remote_executor: RemoteExecutor,
    execution_context: ExecutionContext,
    broker: RemoteExecutionBroker,
    handler: RemoteCompletionHandler,
    leases: ExecutorLeaseRepository,
) -> None:
    callback_calls: list[str] = []
    broker.register_completion_callback(
        lambda execution_id, outcome: callback_calls.append(execution_id)
    )
    assert remote_executor.execute(execution_context) is None
    assert _complete_with_archive(broker) is True
    # A duplicate report is deduplicated by the broker state machine: no second callback.
    assert (
        broker.complete(
            execution_context.execution_id, "w1", RemoteOutcome(status="completed", exit_code=0)
        )
        is False
    )
    assert callback_calls == [execution_context.execution_id]
    assert not leases.has_active_for_job(execution_context.job_id, _utcnow())


def test_finish_cancel_race_single_winner(
    broker: RemoteExecutionBroker,
    handler: RemoteCompletionHandler,
    leases: ExecutorLeaseRepository,
    job_db: JobQueries,
    workspace: tuple[str, str],
) -> None:
    workspace_id, _ = workspace
    callback_calls: list[str] = []
    broker.register_completion_callback(
        lambda execution_id, outcome: callback_calls.append(execution_id)
    )
    outcome_completed = RemoteOutcome(status="completed", exit_code=0)

    for _ in range(50):
        job_id = _create_job_in_workspace(job_db, workspace_id)
        claim = leases.try_claim(
            _claim_request(workspace_id, job_id, executor_id=EXECUTOR_ID, local_node_limit=None)
        )
        assert claim is not None
        broker.submit(
            RemoteExecutionPayload(
                execution_id=claim.execution_id,
                lease_id=claim.lease_id,
                job_id=claim.job_id,
                node_key=claim.node_key,
                capability=CAPABILITY,
                bundle_name=f"{claim.execution_id}.tar.gz",
                manifest={
                    "job_id": claim.job_id,
                    "node_key": claim.node_key,
                    "run_token": "tok",
                    "expected_outputs": [],
                },
            )
        )
        assert broker.dequeue("w1", {CAPABILITY}) is not None

        threads = [
            threading.Thread(target=broker.cancel, args=(claim.execution_id,)),
            threading.Thread(
                target=broker.complete, args=(claim.execution_id, "w1", outcome_completed)
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
        # Exactly one of cancel/complete wins, so the callback fires exactly once
        # and the lease is finished exactly once, in a single final state.
        assert callback_calls.count(claim.execution_id) == 1
        assert not leases.has_active_for_job(job_id, _utcnow())


def test_requeue_limit_exceeded_finishes_failed(
    db_path: Path,
    tmp_path: Path,
    leases: ExecutorLeaseRepository,
    job_db: JobQueries,
    claim: ClaimedExecution,
) -> None:
    current = [_utcnow()]
    broker = RemoteExecutionBroker(
        db_path,
        tmp_path / "bundles-requeue",
        claim_timeout_seconds=10.0,
        requeue_limit=1,
        time_source=lambda: current[0],
    )
    outcomes: list[RemoteOutcome] = []
    handler = RemoteCompletionHandler(broker, leases, tmp_path / "jobs")
    broker.register_completion_callback(handler.handle_completion)
    broker.register_completion_callback(lambda execution_id, outcome: outcomes.append(outcome))
    broker.submit(
        RemoteExecutionPayload(
            execution_id=claim.execution_id,
            lease_id=claim.lease_id,
            job_id=claim.job_id,
            node_key=claim.node_key,
            capability=CAPABILITY,
            bundle_name=f"{claim.execution_id}.tar.gz",
            manifest={
                "job_id": claim.job_id,
                "node_key": claim.node_key,
                "run_token": "tok",
                "expected_outputs": [],
            },
        )
    )
    assert broker.dequeue("w1", {CAPABILITY}) is not None
    current[0] += timedelta(seconds=11)
    # First stale sweep requeues (requeue_count=1); w2 picks the row up.
    assert broker.dequeue("w2", {CAPABILITY}) is not None
    current[0] += timedelta(seconds=11)
    # Second stale sweep exceeds the requeue limit: done/failed, callback fires.
    assert broker.dequeue("w3", {CAPABILITY}) is None

    assert len(outcomes) == 1
    assert outcomes[0].status == "failed"
    assert "requeue limit" in outcomes[0].error_message
    assert not leases.has_active_for_job(claim.job_id, _utcnow())
    run = _node_run(job_db, claim.job_id)
    assert run["status"] == "failed"
    assert "requeue limit" in str(run["error_message"])


def test_cancel_completion_finishes_lease(
    remote_executor: RemoteExecutor,
    execution_context: ExecutionContext,
    broker: RemoteExecutionBroker,
    handler: RemoteCompletionHandler,
    leases: ExecutorLeaseRepository,
    job_db: JobQueries,
) -> None:
    assert remote_executor.execute(execution_context) is None
    remote_executor.cancel(execution_context.execution_id)
    # The cancel lands on the broker and drives the same completion callback.
    assert not leases.has_active_for_job(execution_context.job_id, _utcnow())
    run = _node_run(job_db, execution_context.job_id)
    assert run["status"] == "cancelled"
    assert "cancelled" in str(run["error_message"])
    assert list(broker.bundle_dir.iterdir()) == []  # bundle cleaned up by the handler


def test_active_lease_ids(broker: RemoteExecutionBroker) -> None:
    payload = RemoteExecutionPayload(
        execution_id="e1",
        lease_id="lease-e1",
        job_id="job1",
        node_key="node_a",
        capability=CAPABILITY,
        bundle_name="e1.tar.gz",
        manifest={"job_id": "job1", "node_key": "node_a"},
    )
    broker.submit(payload)
    assert broker.active_lease_ids() == ["lease-e1"]
    assert broker.dequeue("w1", {CAPABILITY}) is not None
    assert broker.active_lease_ids() == ["lease-e1"]  # claimed rows are still active
    assert broker.complete("e1", "w1", RemoteOutcome(status="completed", exit_code=0)) is True
    assert broker.active_lease_ids() == []


# ---- completion handler result translation ----


class _SpyLeases:
    """Delegate to the real repository while recording finish calls."""

    def __init__(self, repo: ExecutorLeaseRepository) -> None:
        self._repo = repo
        self.finished: list[tuple[str, ExecutionResult]] = []

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(self._repo, name)

    def finish(self, lease_id: str, result: ExecutionResult) -> bool:
        self.finished.append((lease_id, result))
        return self._repo.finish(lease_id, result)


def test_completion_handler_result_fields(
    remote_executor: RemoteExecutor,
    execution_context: ExecutionContext,
    broker: RemoteExecutionBroker,
    leases: ExecutorLeaseRepository,
    tmp_path: Path,
) -> None:
    spy = _SpyLeases(leases)
    handler = RemoteCompletionHandler(broker, spy, tmp_path / "jobs")  # type: ignore[arg-type]
    broker.register_completion_callback(handler.handle_completion)
    assert remote_executor.execute(execution_context) is None
    assert _complete_with_archive(broker) is True

    assert len(spy.finished) == 1
    lease_id, result = spy.finished[0]
    assert lease_id == execution_context.lease_id
    assert result.status == "completed", result.error_message
    assert result.exit_code == 0
    assert result.command == ("pi", "--mode", "json")
    assert result.produced_artifacts == ("out.json",)
    run_dir = Path(result.run_dir)
    assert run_dir.is_dir()
    assert (run_dir / "events.jsonl").is_file()
    assert result.skill_version  # 40-char git commit from the fake skill repo
    assert result.runner == "w1"
    # bundle + result archive cleaned up by the completion handler
    assert list(broker.bundle_dir.iterdir()) == []


def test_completion_handler_worker_reported_failure(
    remote_executor: RemoteExecutor,
    execution_context: ExecutionContext,
    broker: RemoteExecutionBroker,
    handler: RemoteCompletionHandler,
    job_db: JobQueries,
) -> None:
    assert remote_executor.execute(execution_context) is None
    assert broker.dequeue("w1", {CAPABILITY}) is not None
    outcome = RemoteOutcome(
        status="failed",
        exit_code=1,
        error_message="Missing outputs after Pi run: out.json",
    )
    assert broker.complete(execution_context.execution_id, "w1", outcome) is True
    node = job_db.get_job_node(execution_context.job_id, execution_context.node_key)
    assert node["status"] == "failed"
    assert "Missing outputs" in node["error_message"]


def test_completion_handler_missing_outputs(
    remote_executor: RemoteExecutor,
    execution_context: ExecutionContext,
    broker: RemoteExecutionBroker,
    handler: RemoteCompletionHandler,
    job_db: JobQueries,
) -> None:
    assert remote_executor.execute(execution_context) is None
    # Worker reports success but the archive does not contain the expected output.
    assert _complete_with_archive(broker, output_name="unexpected.json") is True
    node = job_db.get_job_node(execution_context.job_id, execution_context.node_key)
    assert node["status"] == "failed"
    assert "Missing outputs" in node["error_message"]


def test_completion_handler_unpack_failure(
    remote_executor: RemoteExecutor,
    execution_context: ExecutionContext,
    broker: RemoteExecutionBroker,
    handler: RemoteCompletionHandler,
    job_db: JobQueries,
) -> None:
    assert remote_executor.execute(execution_context) is None
    remote_claim = broker.dequeue("w1", {CAPABILITY})
    assert remote_claim is not None
    archive = broker.bundle_dir / f"{remote_claim.execution_id}.result.tar.gz"
    archive.write_bytes(b"not a valid tar.gz")
    outcome = RemoteOutcome(
        status="completed",
        exit_code=0,
        skill_version=str(remote_claim.manifest["skill_version"]),
        result_archive_name=archive.name,
    )
    assert broker.complete(remote_claim.execution_id, "w1", outcome) is True
    node = job_db.get_job_node(execution_context.job_id, execution_context.node_key)
    assert node["status"] == "failed"
    assert "failed to unpack" in node["error_message"]


# ---- worker thread pools ----


def test_no_thread_pool_growth_for_remote_claims(
    tmp_path: Path,
    db_path: Path,
    job_db: JobQueries,
    leases: ExecutorLeaseRepository,
    broker: RemoteExecutionBroker,
    remote_executor: RemoteExecutor,
) -> None:
    registry = _make_registry(remote_executor)
    runtime = ExecutionRuntime(
        leases=leases, registry=registry, heartbeat_interval_seconds=1, lease_ttl_seconds=60
    )
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        videos_dir=tmp_path / "videos",
        logs_dir=tmp_path / "logs",
        packages_dir=tmp_path / "packages",
        jobs_dir=tmp_path / "jobs",
        config={"workflows": {"enabled": True}},
        executor_definitions=registry.definitions(),
    )
    worker = WorkflowWorkerThread(
        job_db=job_db,
        leases=leases,
        registry=registry,
        runtime=runtime,
        settings=settings,
    )
    worker._definitions = [
        WorkflowDefinition(
            key="test",
            label="Test",
            intake=WorkflowIntake(),
            nodes={
                CAPABILITY: WorkflowNode(
                    key=CAPABILITY,
                    label=CAPABILITY,
                    capability=CAPABILITY,
                    outputs=["out.json"],
                )
            },
        )
    ]
    workspace = job_db.create_workspace("WS", default_workflow_key="question_comprehension_info")
    for i in range(20):
        job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id=f"Q{i}",
            batch_id="",
            title=f"Q{i}",
            node_keys=[CAPABILITY],
            workspace_id=workspace["id"],
        )
    _bind(job_db, workspace["id"], "test", CAPABILITY, EXECUTOR_ID)
    _allocate(job_db, workspace["id"], EXECUTOR_ID, 20)

    threads_before = len(threading.enumerate())
    for _ in range(10):
        worker._poll()
        if leases.active_counts(EXECUTOR_ID).get("global", 0) >= 20:
            break

    assert leases.active_counts(EXECUTOR_ID).get("global", 0) == 20
    # Remote executors share one bounded submit pool; no per-executor pool is created.
    assert EXECUTOR_ID not in worker._pools
    growth = len(threading.enumerate()) - threads_before
    assert growth < 5, f"remote claims must not grow thread pools unboundedly (+{growth})"
    worker.stop()
