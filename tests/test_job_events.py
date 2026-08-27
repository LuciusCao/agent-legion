import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from server.app.db.connection import DatabaseConnection, connect_database
from server.app.events import JobEventManager
from server.app.events.aggregator import (
    build_job_patch_batch_payload,
    build_resync_required_payload,
    record_job_update,
)
from server.app.events.bus import _EVICTED, InProcessEventBus, workspace_channel
from server.app.executors.leases import ExecutorLeaseRepository, database_timestamp
from server.app.executors.models import ConfigurationFailureRequest, ExecutionResult
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_patch_queries import JobPatchQueryService
from server.app.services.job_queries import JobQueryService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings
from tests.helpers import publish_builtin_revision
from tests.postgres_support import TEST_DATABASE_URL


class FakeJobDB:
    def __init__(self):
        self._jobs = {
            "j1": {
                "id": "j1",
                "workspace_id": "ws1",
                "storage_dir": "jobs/j1",
                "workflow_key": "p1",
            }
        }
        self._nodes = {"j1": [{"job_id": "j1", "node_key": "n1", "status": "pending"}]}

    def get_job(self, job_id):
        return self._jobs.get(job_id)

    def count_jobs_by_status(self, workspace_id):
        return {"pending": 0, "running": 0, "completed": 0, "failed": 0}

    def list_job_nodes(self, job_id):
        return self._nodes.get(job_id, [])

    def get_job_node(self, job_id, node_key):
        for node in self._nodes.get(job_id, []):
            if node["node_key"] == node_key:
                return node
        return None

    @contextmanager
    def lease_guarded_mutation(self, job_id, now, *, reject_running_nodes):
        yield MagicMock(spec=DatabaseConnection)

    @staticmethod
    def mark_nodes_for_rerun_in_transaction(conn, job_id, node_keys, downstream_map):
        pass

    @staticmethod
    def resume_job(job_id):
        pass

    @staticmethod
    def delete_job_in_transaction(conn, job_id):
        pass


@pytest.fixture
def manager():
    bus = InProcessEventBus()
    bus.attach_loop(asyncio.new_event_loop())
    return JobEventManager(bus)


@pytest.fixture
def job_query_service(job_db, settings):
    return JobQueryService(
        job_db,
        settings,
        WorkspaceExecutorConfigurationService(job_db),
    )


@pytest.fixture
def job_patch_query_service(job_db, settings):
    return JobPatchQueryService(
        job_db,
        settings,
        WorkspaceExecutorConfigurationService(job_db),
    )


def _insert_workspace_job(conn):
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values ('ws1', 'ws1', 'education_video_problems_generation') on conflict(id) do nothing"
    )
    conn.execute(
        """
        insert into jobs(id, workspace_id, workflow_key, source_type, source_id)
        values ('j1', 'ws1', 'p1', 'test', 's1')
        on conflict(id) do nothing
        """
    )


def _insert_lease(conn, lease_id, expires_at, status="active"):
    _insert_workspace_job(conn)
    now = datetime.now(UTC)
    now_str = database_timestamp(now)
    conn.execute("insert into job_nodes(job_id, node_key, status) values ('j1', 'n1', 'pending')")
    cursor = conn.execute(
        """
        insert into node_runs(job_id, node_key, status, started_at)
        values ('j1', 'n1', 'running', %s)
        returning id
        """,
        (now_str,),
    )
    node_run_id = cursor.fetchone()["id"]
    conn.execute(
        """
        insert into executor_leases(
            id, execution_id, executor_id, workspace_id, job_id, workflow_key,
            node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
        ) values (%s, %s, 'e1', 'ws1', 'j1', 'p1', 'n1', %s, %s, %s, %s, %s)
        """,
        (
            lease_id,
            f"ex-{lease_id}",
            node_run_id,
            status,
            now_str,
            now_str,
            database_timestamp(expires_at),
        ),
    )


def _ws1_queue(manager):
    return manager.bus.subscribe(workspace_channel("ws1"))


def test_broadcast_jobs_created_queues_message(manager):
    queue = manager.bus.subscribe(workspace_channel("ws1"))
    manager.broadcast_jobs_created("ws1", [{"id": "j1"}], {"pending": 1})
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "jobs_created"' in data
    assert '"workspace_id": "ws1"' in data


def test_broadcast_isolated_by_workspace(manager):
    q1 = manager.bus.subscribe(workspace_channel("ws1"))
    q2 = manager.bus.subscribe(workspace_channel("ws2"))
    manager.broadcast_job_updated("ws1", "j1", {"pending": 1})
    assert not q1.empty()
    assert q2.empty()


def test_finish_broadcasts_job_updated(manager, tmp_path):
    lease_repo = ExecutorLeaseRepository(
        TEST_DATABASE_URL,
        job_db=FakeJobDB(),
        job_event_manager=manager,
        data_dir=tmp_path,
    )
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    conn = connect_database(lease_repo.path)
    try:
        _insert_lease(conn, "l1", expires_at)
        conn.commit()
    finally:
        conn.close()

    queue = _ws1_queue(manager)
    result = ExecutionResult(status="completed", exit_code=0)
    assert lease_repo.finish("l1", result) is True
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "job_updated"' in data
    assert '"workspace_id": "ws1"' in data
    assert '"job_id": "j1"' in data


def test_fail_without_lease_broadcasts_job_updated(manager, tmp_path):
    lease_repo = ExecutorLeaseRepository(
        TEST_DATABASE_URL,
        job_db=FakeJobDB(),
        job_event_manager=manager,
        data_dir=tmp_path,
    )
    conn = connect_database(lease_repo.path)
    try:
        _insert_workspace_job(conn)
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('j1', 'n1', 'pending')"
        )
        conn.commit()
    finally:
        conn.close()

    queue = _ws1_queue(manager)
    request = ConfigurationFailureRequest(
        workspace_id="ws1",
        job_id="j1",
        workflow_key="p1",
        node_key="n1",
        capability="c1",
        log_path="",
    )
    run_id = lease_repo.fail_without_lease(request, "error")
    assert run_id is not None
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "job_updated"' in data
    assert '"workspace_id": "ws1"' in data
    assert '"job_id": "j1"' in data


def test_expire_stale_broadcasts_job_updated(manager, tmp_path):
    lease_repo = ExecutorLeaseRepository(
        TEST_DATABASE_URL,
        job_db=FakeJobDB(),
        job_event_manager=manager,
        data_dir=tmp_path,
    )
    expires_at = datetime.now(UTC) - timedelta(seconds=1)
    conn = connect_database(lease_repo.path)
    try:
        _insert_lease(conn, "l1", expires_at)
        conn.commit()
    finally:
        conn.close()

    queue = _ws1_queue(manager)
    expired = lease_repo.expire_stale(datetime.now(UTC))
    assert "l1" in expired
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "job_updated"' in data
    assert '"workspace_id": "ws1"' in data
    assert '"job_id": "j1"' in data


def test_recover_orphaned_running_jobs_broadcasts_job_updated(manager, tmp_path):
    lease_repo = ExecutorLeaseRepository(
        TEST_DATABASE_URL,
        job_db=FakeJobDB(),
        job_event_manager=manager,
        data_dir=tmp_path,
    )
    conn = connect_database(lease_repo.path)
    try:
        _insert_workspace_job(conn)
        conn.execute("update jobs set status='running' where id='j1'")
        conn.commit()
    finally:
        conn.close()

    queue = _ws1_queue(manager)
    recovered = lease_repo.recover_orphaned_running_jobs(datetime.now(UTC))
    assert recovered == ["j1"]
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "job_updated"' in data
    assert '"workspace_id": "ws1"' in data
    assert '"job_id": "j1"' in data


def test_finish_rollback_does_not_broadcast(manager, tmp_path, monkeypatch):
    from server.app.executors import _lease_write_paths

    lease_repo = ExecutorLeaseRepository(
        TEST_DATABASE_URL,
        job_db=FakeJobDB(),
        job_event_manager=manager,
        data_dir=tmp_path,
    )
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    conn = connect_database(lease_repo.path)
    try:
        _insert_lease(conn, "l1", expires_at)
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        _lease_write_paths,
        "finish_lease",
        lambda _conn, _lease_id, _result, _data_dir=None: (_ for _ in ()).throw(
            RuntimeError("simulated failure")
        ),
    )

    queue = _ws1_queue(manager)
    with pytest.raises(RuntimeError, match="simulated failure"):
        lease_repo.finish("l1", ExecutionResult(status="completed", exit_code=0))
    assert queue.empty()


def test_connect_evicts_oldest_at_capacity():
    bus = InProcessEventBus()
    loop = asyncio.new_event_loop()
    bus.attach_loop(loop)
    bus.MAX_CLIENTS = 2
    m = JobEventManager(bus)
    q1 = bus.subscribe(workspace_channel("ws1"))
    q2 = bus.subscribe(workspace_channel("ws2"))

    async def add_third() -> None:
        request = MagicMock(spec=Request)
        await m.connect(request, workspace_channel("ws3"))

    asyncio.set_event_loop(loop)
    loop.run_until_complete(add_third())

    assert q1.get_nowait() is _EVICTED
    assert q1 not in bus._subscribers.get(workspace_channel("ws1"), {})
    assert q2 in bus._subscribers[workspace_channel("ws2")]


def test_job_deletion_broadcasts_job_deleted(manager, tmp_path):
    from server.app.services.job_deletion import JobDeletionService

    lease_repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    settings = MagicMock(spec=Settings)
    settings.logs_dir = tmp_path / "logs"
    settings.jobs_dir = tmp_path / "jobs"
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    service = JobDeletionService(FakeJobDB(), lease_repo, settings, job_event_manager=manager)
    queue = _ws1_queue(manager)
    result = service.delete("ws1", "j1")
    assert result["status"] == "succeeded"
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "job_deleted"' in data
    assert '"workspace_id": "ws1"' in data
    assert '"job_id": "j1"' in data


def test_job_deletion_active_lease_does_not_broadcast(manager, tmp_path):
    from server.app.services.job_deletion import JobDeletionService

    lease_repo = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)
    settings = MagicMock(spec=Settings)
    settings.logs_dir = tmp_path / "logs"
    settings.jobs_dir = tmp_path / "jobs"
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    service = JobDeletionService(FakeJobDB(), lease_repo, settings, job_event_manager=manager)

    expires_at = datetime.now(UTC) + timedelta(hours=1)
    conn = connect_database(lease_repo.path)
    try:
        _insert_lease(conn, "l1", expires_at)
        conn.commit()
    finally:
        conn.close()

    queue = _ws1_queue(manager)
    from server.app.services.job_operation_error import JobOperationError

    with pytest.raises(JobOperationError) as exc_info:
        service.delete("ws1", "j1")
    assert exc_info.value.status == "failed"
    assert queue.empty()


def test_rerun_broadcasts_job_updated(manager, monkeypatch):
    from server.app.services.job_rerun import JobRerunService
    from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode

    job_db = FakeJobDB()
    lease_repo = MagicMock(spec=ExecutorLeaseRepository)
    lease_repo.has_active_for_node.return_value = False
    settings = MagicMock(spec=Settings)
    definition = WorkflowDefinition(
        key="p1",
        label="P1",
        intake=WorkflowIntake(),
        nodes={"n1": WorkflowNode(key="n1", label="N1", capability="c1")},
    )
    import server.app.services._job_rerun_eligibility as _eligibility
    import server.app.services._job_rerun_single as _single

    monkeypatch.setattr(
        _eligibility, "require_workspace_active_definition", lambda *args: definition
    )
    monkeypatch.setattr(_single, "require_workspace_active_definition", lambda *args: definition)
    artifact_service = MagicMock()
    staged = MagicMock()
    artifact_service.stage_outputs.return_value = staged

    service = JobRerunService(
        job_db,
        lease_repo,
        settings,
        artifact_service=artifact_service,
        job_event_manager=manager,
    )
    queue = _ws1_queue(manager)
    result = service.rerun("ws1", "j1", "n1")
    assert result["status"] == "succeeded"
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "job_updated"' in data
    assert '"workspace_id": "ws1"' in data
    assert '"job_id": "j1"' in data


def test_continue_job_broadcasts_job_updated(manager):
    from server.app.services.job_execution import JobExecutionService

    job_db = FakeJobDB()
    lease_repo = MagicMock(spec=ExecutorLeaseRepository)
    artifact_mutation = MagicMock(spec=JobArtifactMutationService)
    service = JobExecutionService(
        job_db,
        artifact_mutation,
        lease_repo,
        job_event_manager=manager,
    )
    queue = _ws1_queue(manager)
    result = service.continue_job("ws1", "j1")
    assert result["status"] == "succeeded"
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "job_updated"' in data
    assert '"workspace_id": "ws1"' in data
    assert '"job_id": "j1"' in data


def test_evicted_client_stops_streaming():
    bus = InProcessEventBus()
    loop = asyncio.new_event_loop()
    bus.attach_loop(loop)
    bus.MAX_CLIENTS = 2
    m = JobEventManager(bus)

    async def connect_workspace(ws: str) -> asyncio.Queue[str]:
        request = MagicMock(spec=Request)
        await m.connect(request, workspace_channel(ws))
        return next(iter(bus._subscribers[workspace_channel(ws)]))

    asyncio.set_event_loop(loop)
    q1 = loop.run_until_complete(connect_workspace("ws1"))
    q2 = loop.run_until_complete(connect_workspace("ws2"))
    q3 = loop.run_until_complete(connect_workspace("ws3"))

    assert q1.get_nowait() is _EVICTED
    assert q1 not in bus._subscribers.get(workspace_channel("ws1"), {})
    assert q2 in bus._subscribers[workspace_channel("ws2")]
    assert q3 in bus._subscribers[workspace_channel("ws3")]


def test_dead_queue_stops_streaming():
    bus = InProcessEventBus()
    bus.attach_loop(asyncio.new_event_loop())

    queue = MagicMock(spec=asyncio.Queue)
    queue.put_nowait.side_effect = RuntimeError("dead")
    bus._subscribers.setdefault(workspace_channel("ws1"), {})[queue] = None

    bus.publish(workspace_channel("ws1"), json.dumps({"type": "ping"}))
    assert queue not in bus._subscribers.get(workspace_channel("ws1"), {})


def test_run_to_broadcasts_job_updated(manager, tmp_path):
    from server.app.jobs import JobQueries
    from server.app.services.job_execution import JobExecutionService
    from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode

    db_path = TEST_DATABASE_URL
    jobs_dir = tmp_path / "jobs"
    job_db = JobQueries(db_path, jobs_dir)

    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws1', 'ws1', 'education_video_problems_generation')"
        )
        conn.execute(
            """
            insert into jobs(
                id, workspace_id, workflow_key, source_type, source_id,
                run_id, title, storage_dir
            )
            values ('j1', 'ws1', 'p1', 'test', 's1', 'b1', 'J1', %s)
            """,
            (str(jobs_dir / "ws1" / "j1"),),
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('j1', 'node_a', 'pending')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('j1', 'node_b', 'pending')"
        )

    artifact_mutation = MagicMock(spec=JobArtifactMutationService)
    lease_repo = MagicMock(spec=ExecutorLeaseRepository)
    lease_repo.has_active_for_job.return_value = False

    service = JobExecutionService(
        job_db,
        artifact_mutation,
        lease_repo,
        job_event_manager=manager,
    )
    simple_definition = WorkflowDefinition(
        key="p1",
        label="P1",
        intake=WorkflowIntake(),
        nodes={
            "node_a": WorkflowNode(key="node_a", label="A", capability="cap_a"),
            "node_b": WorkflowNode(key="node_b", label="B", capability="cap_b", after=["node_a"]),
        },
    )
    service._definition = lambda _workflow_key: simple_definition

    queue = _ws1_queue(manager)
    result = service.run_to("ws1", "j1", "node_b")
    assert result["status"] == "succeeded"
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "job_updated"' in data
    assert '"workspace_id": "ws1"' in data
    assert '"job_id": "j1"' in data


def test_rerun_conflict_does_not_broadcast(manager, tmp_path, monkeypatch):
    from contextlib import contextmanager

    from server.app.jobs.atomic_mutations import JobMutationConflict
    from server.app.services.job_rerun import JobRerunService
    from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode

    job_db = FakeJobDB()
    job_db._nodes = {"j1": [{"job_id": "j1", "node_key": "node_a", "status": "pending"}]}

    @contextmanager
    def _conflict(*args, **kwargs):
        raise JobMutationConflict("conflict", "simulated conflict")

    monkeypatch.setattr(job_db, "lease_guarded_mutation", _conflict)

    artifact_service = MagicMock()
    lease_repo = MagicMock(spec=ExecutorLeaseRepository)
    lease_repo.has_active_for_node.return_value = False
    settings = MagicMock()
    definition = WorkflowDefinition(
        key="p1",
        label="P1",
        intake=WorkflowIntake(),
        nodes={"node_a": WorkflowNode(key="node_a", label="A", capability="cap_a")},
    )
    import server.app.services._job_rerun_eligibility as _eligibility
    import server.app.services._job_rerun_single as _single

    monkeypatch.setattr(
        _eligibility, "require_workspace_active_definition", lambda *args: definition
    )
    monkeypatch.setattr(_single, "require_workspace_active_definition", lambda *args: definition)

    service = JobRerunService(
        job_db,
        lease_repo,
        settings,
        artifact_service=artifact_service,
        job_event_manager=manager,
    )
    queue = _ws1_queue(manager)
    from server.app.services.job_operation_error import JobOperationError

    with pytest.raises(JobOperationError) as exc_info:
        service.rerun("ws1", "j1", "node_a")
    assert exc_info.value.status == "skipped"
    assert exc_info.value.reason_code == "conflict"
    assert queue.empty()


def test_job_event_manager_builds_patch_batch_payload():
    payload = build_job_patch_batch_payload(
        workspace_id="ws1",
        revision=42,
        stats={"running": 2},
        jobs=[{"id": "job1", "status": "running"}],
        deleted_job_ids=["job2"],
    )

    data = json.loads(payload)
    assert data["type"] == "job_patch_batch"
    assert data["workspace_id"] == "ws1"
    assert data["revision"] == 42
    assert data["stats"] == {"running": 2}
    assert data["jobs"] == [{"id": "job1", "status": "running"}]
    assert data["deleted_job_ids"] == ["job2"]


def test_job_event_manager_builds_resync_payload():
    payload = build_resync_required_payload(
        workspace_id="ws1",
        latest_revision=99,
        reason="revision_too_old",
    )

    data = json.loads(payload)
    assert data == {
        "type": "resync_required",
        "workspace_id": "ws1",
        "latest_revision": 99,
        "reason": "revision_too_old",
    }


@pytest.fixture
def fake_job_db():
    class _FakeJobDB:
        def __init__(self):
            self.jobs = {}

        def get_job(self, job_id):
            return self.jobs.get(job_id)

    return _FakeJobDB()


class FakeJobEventBuffer:
    def __init__(self):
        self.updated = []
        self.deleted = []

    def record_job_updated(self, workspace_id, job_id):
        self.updated.append((workspace_id, job_id))
        return 1

    def record_job_deleted(self, workspace_id, job_id):
        self.deleted.append((workspace_id, job_id))
        return 1


def test_record_job_update_uses_event_buffer(fake_job_db):
    buffer = FakeJobEventBuffer()
    fake_job_db.jobs["job1"] = {"id": "job1", "workspace_id": "ws1"}

    record_job_update(fake_job_db, buffer, "job1")

    assert buffer.updated == [("ws1", "job1")]


def test_job_query_service_lists_patch_summaries_by_ids(job_patch_query_service, job_db):
    job_db.create_workspace("ws1", default_workflow_key="education_video_problems_generation")
    publish_builtin_revision(job_db, "ws1")
    batch1 = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["q1"]},
        workspace_id="ws1",
    )
    job1 = job_db.create_job(
        workspace_id="ws1",
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="q1",
        run_id=batch1["id"],
        title="Question 1",
        node_keys=["question_understanding"],
    )
    batch2 = job_db.create_run(
        "education_video_problems_generation",
        "batch_by_ids",
        {"question_ids": ["q2"]},
        workspace_id="ws1",
    )
    job2 = job_db.create_job(
        workspace_id="ws1",
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id="q2",
        run_id=batch2["id"],
        title="Question 2",
        node_keys=["question_understanding"],
    )

    summaries = job_patch_query_service.list_patch_summaries("ws1", [job1["id"]])

    assert [summary["id"] for summary in summaries] == [job1["id"]]
    assert summaries[0]["workspace_id"] == "ws1"
    assert "status" in summaries[0]
    assert "active_node_key" in summaries[0]
    assert "completed_nodes" in summaries[0]
    assert "total_nodes" in summaries[0]
    assert job2["id"] not in [summary["id"] for summary in summaries]
