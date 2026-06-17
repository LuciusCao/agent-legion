import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db
from server.app.executors.config import LocalCapabilityConfig, LocalExecutorConfig
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, LeaseClaimRequest
from server.app.executors.registry import ExecutorRegistry
from server.app.executors.runtime import ExecutionRuntime
from server.app.jobs.queries import JobQueries
from server.app.pipeline_worker_thread import PipelineWorkerThread
from server.app.pipelines.definition import (
    PipelineDefinition,
    PipelineIntake,
    PipelineNode,
)
from server.app.settings import Settings


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    path = tmp_path / "recovery.sqlite"
    init_db(path)
    return path


@pytest.fixture
def queries(tmp_db: Path) -> JobQueries:
    jobs_dir = tmp_db.parent / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return JobQueries(tmp_db, jobs_dir)


@pytest.fixture
def repo(tmp_db: Path) -> ExecutorLeaseRepository:
    return ExecutorLeaseRepository(tmp_db)


def _local_node(key: str) -> PipelineNode:
    return PipelineNode(
        key=key,
        label=key,
        capability=key,
        outputs=[f"{key}.json"],
    )


def _make_definition() -> PipelineDefinition:
    return PipelineDefinition(
        key="recovery_test",
        label="Recovery Test",
        intake=PipelineIntake(),
        nodes={"fetch": _local_node("fetch")},
    )


def _setup_workspace(queries: JobQueries, name: str) -> tuple[str, str]:
    workspace = queries.create_workspace(name=name, default_workflow_key="recovery_test")
    workspace_id = workspace["id"]
    job = queries.create_job(
        workflow_key="recovery_test",
        source_type="question",
        source_id=f"src-{uuid.uuid4().hex[:8]}",
        batch_id="",
        title=f"Job {name}",
        node_keys=["fetch"],
        workspace_id=workspace_id,
    )
    job_id = str(job["id"])
    with queries.connect() as conn:
        conn.execute(
            """
            insert into workspace_executor_allocations(workspace_id, executor_id, concurrency_limit)
            values (?, ?, ?)
            """,
            (workspace_id, "local-default", 1),
        )
        conn.execute(
            """
            insert into workspace_node_bindings(workspace_id, pipeline_key, node_key, executor_id)
            values (?, ?, ?, ?)
            """,
            (workspace_id, "recovery_test", "fetch", "local-default"),
        )
        conn.execute(
            """
            insert into workspace_node_limits(workspace_id, pipeline_key, node_key, concurrency_limit)
            values (?, ?, ?, ?)
            """,
            (workspace_id, "recovery_test", "fetch", 1),
        )
    return workspace_id, job_id


def _claim(workspace_id: str, job_id: str, repo: ExecutorLeaseRepository) -> None:
    request = LeaseClaimRequest(
        executor_id="local-default",
        global_capacity=1,
        workspace_id=workspace_id,
        job_id=job_id,
        workflow_key="recovery_test",
        node_key="fetch",
        capability="fetch",
        local_node_limit=1,
        lease_ttl_seconds=60,
        log_path="/tmp/recovery.log",
    )
    claim = repo.try_claim(request)
    assert claim is not None


def _set_expired(repo: ExecutorLeaseRepository, lease_id: str) -> None:
    past = datetime.now(UTC) - timedelta(seconds=10)
    conn = connect_sqlite(repo.path)
    try:
        conn.execute(
            "update executor_leases set expires_at=? where id=?",
            (past.strftime("%Y-%m-%d %H:%M:%S.%f"), lease_id),
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_recovery_state(queries: JobQueries, job_id: str, lease_id: str, node_run_id: int):
    with queries.connect() as conn:
        lease = conn.execute("select * from executor_leases where id=?", (lease_id,)).fetchone()
        run = conn.execute("select * from node_runs where id=?", (node_run_id,)).fetchone()
        node = conn.execute(
            "select * from job_nodes where job_id=? and node_key=?",
            (job_id, "fetch"),
        ).fetchone()
        job = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
    return lease, run, node, job


class _NoOpExecutor:
    kind = "local"
    id = "local-default"

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: object) -> ExecutionResult:
        return ExecutionResult(status="completed", exit_code=0)

    def cancel(self, execution_id: str) -> None:
        pass


def _make_worker(
    tmp_path: Path, queries: JobQueries, repo: ExecutorLeaseRepository
) -> PipelineWorkerThread:
    executor_def = LocalExecutorConfig(
        kind="local",
        global_capacity=1,
        capabilities={"fetch": LocalCapabilityConfig(handler="dummy.handler")},
    )
    registry = ExecutorRegistry(
        executors={"local-default": _NoOpExecutor()},
        global_capacities={"local-default": 1},
        definitions={"local-default": executor_def},
    )
    runtime = ExecutionRuntime(
        leases=repo,
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
        config={"pipelines": {"enabled": True}},
        executor_definitions=registry.definitions(),
    )
    worker = PipelineWorkerThread(
        job_db=queries,
        leases=repo,
        registry=registry,
        runtime=runtime,
        settings=settings,
    )
    worker._definitions = [_make_definition()]
    return worker


def test_fresh_repo_expire_stale_marks_recovery_state(
    tmp_path: Path, queries: JobQueries, repo: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "fresh-repo")
    _claim(workspace_id, job_id, repo)

    with queries.connect() as conn:
        lease_row = conn.execute(
            "select * from executor_leases where job_id=? and node_key=?",
            (job_id, "fetch"),
        ).fetchone()
        run_row = conn.execute(
            "select * from node_runs where job_id=? and node_key=?",
            (job_id, "fetch"),
        ).fetchone()
    lease_id = lease_row["id"]
    node_run_id = run_row["id"]

    _set_expired(repo, lease_id)

    fresh_repo = ExecutorLeaseRepository(queries.path)
    expired = fresh_repo.expire_stale(datetime.now(UTC))

    assert expired == [lease_id]
    lease, run, node, job = _fetch_recovery_state(queries, job_id, lease_id, node_run_id)
    assert lease["status"] == "expired"
    assert run["status"] == "failed"
    assert "lease expired" in run["error_message"]
    assert node["status"] == "failed"
    assert "lease expired" in node["error_message"]
    assert node["stale_reason"] == ""
    assert job["status"] == "failed"


def test_fresh_worker_start_expires_stale_leases(
    tmp_path: Path, queries: JobQueries, repo: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "fresh-worker")
    _claim(workspace_id, job_id, repo)

    with queries.connect() as conn:
        lease_row = conn.execute(
            "select * from executor_leases where job_id=? and node_key=?",
            (job_id, "fetch"),
        ).fetchone()
    lease_id = lease_row["id"]
    _set_expired(repo, lease_id)

    worker = _make_worker(tmp_path, queries, repo)
    worker.start()
    worker.stop()

    with queries.connect() as conn:
        lease = conn.execute("select * from executor_leases where id=?", (lease_id,)).fetchone()
        node = conn.execute(
            "select * from job_nodes where job_id=? and node_key=?",
            (job_id, "fetch"),
        ).fetchone()
        job = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
    assert lease["status"] == "expired"
    assert node["status"] == "failed"
    assert job["status"] == "failed"


def test_recovery_frees_global_and_workspace_capacity(
    tmp_path: Path, queries: JobQueries, repo: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id_a = _setup_workspace(queries, "capacity-a")
    job_b = queries.create_job(
        workflow_key="recovery_test",
        source_type="question",
        source_id=f"src-{uuid.uuid4().hex[:8]}",
        batch_id="",
        title="Job capacity-b",
        node_keys=["fetch"],
        workspace_id=workspace_id,
    )
    job_id_b = str(job_b["id"])

    _claim(workspace_id, job_id_a, repo)

    with queries.connect() as conn:
        lease_row = conn.execute(
            "select * from executor_leases where job_id=? and node_key=?",
            (job_id_a, "fetch"),
        ).fetchone()
    lease_id = lease_row["id"]
    _set_expired(repo, lease_id)

    fresh_repo = ExecutorLeaseRepository(queries.path)
    expired = fresh_repo.expire_stale(datetime.now(UTC))
    assert expired == [lease_id]

    claim_b = fresh_repo.try_claim(
        LeaseClaimRequest(
            executor_id="local-default",
            global_capacity=1,
            workspace_id=workspace_id,
            job_id=job_id_b,
            workflow_key="recovery_test",
            node_key="fetch",
            capability="fetch",
            local_node_limit=1,
            lease_ttl_seconds=60,
            log_path="/tmp/recovery-b.log",
        )
    )
    assert claim_b is not None
    assert claim_b.job_id == job_id_b


def test_recovery_does_not_resubmit_failed_node(
    tmp_path: Path, queries: JobQueries, repo: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "no-resubmit")
    _claim(workspace_id, job_id, repo)

    with queries.connect() as conn:
        lease_row = conn.execute(
            "select * from executor_leases where job_id=? and node_key=?",
            (job_id, "fetch"),
        ).fetchone()
    lease_id = lease_row["id"]
    _set_expired(repo, lease_id)

    worker = _make_worker(tmp_path, queries, repo)
    worker.start()
    worker.stop()

    processed = worker._poll()
    assert processed is False

    with queries.connect() as conn:
        node = conn.execute(
            "select * from job_nodes where job_id=? and node_key=?",
            (job_id, "fetch"),
        ).fetchone()
    assert node["status"] == "failed"


def test_recovery_is_idempotent(
    tmp_path: Path, queries: JobQueries, repo: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "idempotent")
    _claim(workspace_id, job_id, repo)

    with queries.connect() as conn:
        lease_row = conn.execute(
            "select * from executor_leases where job_id=? and node_key=?",
            (job_id, "fetch"),
        ).fetchone()
        run_row = conn.execute(
            "select * from node_runs where job_id=? and node_key=?",
            (job_id, "fetch"),
        ).fetchone()
    lease_id = lease_row["id"]
    node_run_id = run_row["id"]
    _set_expired(repo, lease_id)

    fresh_repo = ExecutorLeaseRepository(queries.path)
    first = fresh_repo.expire_stale(datetime.now(UTC))
    second = fresh_repo.expire_stale(datetime.now(UTC))

    assert first == [lease_id]
    assert second == []
    lease, _, node, job = _fetch_recovery_state(queries, job_id, lease_id, node_run_id)
    assert lease["status"] == "expired"
    assert node["status"] == "failed"
    assert job["status"] == "failed"
