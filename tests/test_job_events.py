import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from server.app.events import JobEventManager, record_job_update
from server.app.executors.leases import ExecutorLeaseRepository, _sqlite_timestamp
from server.app.executors.models import ConfigurationFailureRequest, ExecutionResult
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workspace_executor_configuration import (
    WorkspaceExecutorConfigurationService,
)
from server.app.settings import Settings


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
        yield MagicMock(spec=sqlite3.Connection)

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
    m = JobEventManager()
    m._loop = asyncio.new_event_loop()
    return m


@pytest.fixture
def job_query_service(job_db, settings):
    return JobQueryService(
        job_db,
        settings,
        WorkflowCatalogService(settings),
        WorkspaceExecutorConfigurationService(job_db),
    )


def _insert_workspace_job(conn):
    conn.execute(
        "insert into workspaces(id, name) values ('ws1', 'ws1') on conflict(id) do nothing"
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
    now_str = _sqlite_timestamp(now)
    conn.execute("insert into job_nodes(job_id, node_key, status) values ('j1', 'n1', 'pending')")
    cursor = conn.execute(
        """
        insert into node_runs(job_id, node_key, status, started_at)
        values ('j1', 'n1', 'running', ?)
        """,
        (now_str,),
    )
    node_run_id = cursor.lastrowid
    conn.execute(
        """
        insert into executor_leases(
            id, execution_id, executor_id, workspace_id, job_id, workflow_key,
            node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
        ) values (?, ?, 'e1', 'ws1', 'j1', 'p1', 'n1', ?, ?, ?, ?, ?)
        """,
        (
            lease_id,
            f"ex-{lease_id}",
            node_run_id,
            status,
            now_str,
            now_str,
            _sqlite_timestamp(expires_at),
        ),
    )


def _ws1_queue(manager):
    queue = asyncio.Queue()
    manager._get_workspace_queues("ws1").add(queue)
    return queue


def test_broadcast_jobs_created_queues_message(manager):
    queue = asyncio.Queue()
    manager._get_workspace_queues("ws1").add(queue)
    manager.broadcast_jobs_created("ws1", [{"id": "j1"}], {"pending": 1})
    assert not queue.empty()
    data = queue.get_nowait()
    assert '"type": "jobs_created"' in data
    assert '"workspace_id": "ws1"' in data


def test_broadcast_isolated_by_workspace(manager):
    q1 = asyncio.Queue()
    q2 = asyncio.Queue()
    manager._get_workspace_queues("ws1").add(q1)
    manager._get_workspace_queues("ws2").add(q2)
    manager.broadcast_job_updated("ws1", "j1", {"pending": 1})
    assert not q1.empty()
    assert q2.empty()


def test_finish_broadcasts_job_updated(manager, tmp_path):
    lease_repo = ExecutorLeaseRepository(
        tmp_path / "leases.sqlite",
        job_db=FakeJobDB(),
        job_event_manager=manager,
    )
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    conn = sqlite3.connect(lease_repo.path)
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
        tmp_path / "leases.sqlite",
        job_db=FakeJobDB(),
        job_event_manager=manager,
    )
    conn = sqlite3.connect(lease_repo.path)
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
        tmp_path / "leases.sqlite",
        job_db=FakeJobDB(),
        job_event_manager=manager,
    )
    expires_at = datetime.now(UTC) - timedelta(seconds=1)
    conn = sqlite3.connect(lease_repo.path)
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
        tmp_path / "leases.sqlite",
        job_db=FakeJobDB(),
        job_event_manager=manager,
    )
    conn = sqlite3.connect(lease_repo.path)
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
    from server.app import executors

    lease_repo = ExecutorLeaseRepository(
        tmp_path / "leases.sqlite",
        job_db=FakeJobDB(),
        job_event_manager=manager,
    )
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    conn = sqlite3.connect(lease_repo.path)
    try:
        _insert_lease(conn, "l1", expires_at)
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        executors.leases,
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
    m = JobEventManager()
    m._loop = asyncio.new_event_loop()
    m.MAX_CLIENTS = 2
    q1 = asyncio.Queue()
    q2 = asyncio.Queue()
    m._get_workspace_queues("ws1").add(q1)
    m._get_workspace_queues("ws2").add(q2)

    async def add_third() -> None:
        request = MagicMock(spec=Request)
        await m.connect(request, "ws3")

    asyncio.set_event_loop(m._loop)
    m._loop.run_until_complete(add_third())

    assert q1 not in m._get_workspace_queues("ws1")
    assert q2 in m._get_workspace_queues("ws2")


def test_job_deletion_broadcasts_job_deleted(manager, tmp_path):
    from server.app.services.job_deletion import JobDeletionService

    lease_repo = ExecutorLeaseRepository(tmp_path / "leases.sqlite")
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

    lease_repo = ExecutorLeaseRepository(tmp_path / "leases.sqlite")
    settings = MagicMock(spec=Settings)
    settings.logs_dir = tmp_path / "logs"
    settings.jobs_dir = tmp_path / "jobs"
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    service = JobDeletionService(FakeJobDB(), lease_repo, settings, job_event_manager=manager)

    expires_at = datetime.now(UTC) + timedelta(hours=1)
    conn = sqlite3.connect(lease_repo.path)
    try:
        _insert_lease(conn, "l1", expires_at)
        conn.commit()
    finally:
        conn.close()

    queue = _ws1_queue(manager)
    result = service.delete("ws1", "j1")
    assert result["status"] == "failed"
    assert queue.empty()


def test_rerun_broadcasts_job_updated(manager):
    from server.app.services.job_rerun import JobRerunService
    from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode

    job_db = FakeJobDB()
    lease_repo = MagicMock(spec=ExecutorLeaseRepository)
    lease_repo.has_active_for_node.return_value = False
    settings = MagicMock(spec=Settings)
    workflows = MagicMock(spec=WorkflowCatalogService)
    workflows.definition.return_value = WorkflowDefinition(
        key="p1",
        label="P1",
        intake=WorkflowIntake(),
        nodes={"n1": WorkflowNode(key="n1", label="N1", capability="c1")},
    )
    artifact_service = MagicMock()
    staged = MagicMock()
    artifact_service.stage_outputs.return_value = staged

    service = JobRerunService(
        job_db,
        lease_repo,
        settings,
        workflows,
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
    workflows = MagicMock(spec=WorkflowCatalogService)
    service = JobExecutionService(
        job_db,
        artifact_mutation,
        lease_repo,
        workflows,
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
    m = JobEventManager()
    m._loop = asyncio.new_event_loop()
    m.MAX_CLIENTS = 2

    async def connect_workspace(ws: str) -> asyncio.Queue[str]:
        request = MagicMock(spec=Request)
        await m.connect(request, ws)
        return next(iter(m._get_workspace_queues(ws)))

    asyncio.set_event_loop(m._loop)
    q1 = m._loop.run_until_complete(connect_workspace("ws1"))
    stop_event_q1 = m._stop_events[q1]
    q2 = m._loop.run_until_complete(connect_workspace("ws2"))
    q3 = m._loop.run_until_complete(connect_workspace("ws3"))

    assert q1 not in m._get_workspace_queues("ws1")
    assert q2 in m._get_workspace_queues("ws2")
    assert q3 in m._get_workspace_queues("ws3")
    # The evicted queue's stop event should be set so its generator exits.
    assert stop_event_q1.is_set()
    assert m._stop_events.get(q1) is None


def test_dead_queue_stops_streaming():
    m = JobEventManager()
    m._loop = asyncio.new_event_loop()

    queue = MagicMock(spec=asyncio.Queue)
    queue.put_nowait.side_effect = RuntimeError("dead")
    m._get_workspace_queues("ws1").add(queue)
    stop_event = asyncio.Event()
    m._stop_events[queue] = stop_event

    m._broadcast("ws1", json.dumps({"type": "ping"}))
    assert queue not in m._get_workspace_queues("ws1")
    assert stop_event.is_set()


def test_run_to_broadcasts_job_updated(manager, tmp_path):
    from server.app.jobs import JobQueries
    from server.app.services.job_execution import JobExecutionService
    from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode

    db_path = tmp_path / "jobs.sqlite"
    jobs_dir = tmp_path / "jobs"
    job_db = JobQueries(db_path, jobs_dir)

    with job_db.connect() as conn:
        conn.execute("insert into workspaces(id, name) values ('ws1', 'ws1')")
        conn.execute(
            """
            insert into jobs(
                id, workspace_id, workflow_key, source_type, source_id,
                batch_id, title, storage_dir
            )
            values ('j1', 'ws1', 'p1', 'test', 's1', 'b1', 'J1', ?)
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
    workflows = MagicMock(spec=WorkflowCatalogService)

    service = JobExecutionService(
        job_db,
        artifact_mutation,
        lease_repo,
        workflows,
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
    workflows = MagicMock(spec=WorkflowCatalogService)
    workflows.definition.return_value = WorkflowDefinition(
        key="p1",
        label="P1",
        intake=WorkflowIntake(),
        nodes={"node_a": WorkflowNode(key="node_a", label="A", capability="cap_a")},
    )

    service = JobRerunService(
        job_db,
        lease_repo,
        settings,
        workflows,
        artifact_service=artifact_service,
        job_event_manager=manager,
    )
    queue = _ws1_queue(manager)
    result = service.rerun("ws1", "j1", "node_a")
    assert result["status"] == "skipped"
    assert result["reason_code"] == "conflict"
    assert queue.empty()


def test_job_event_manager_builds_patch_batch_payload():
    manager = JobEventManager()

    payload = manager.build_job_patch_batch(
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
    manager = JobEventManager()

    payload = manager.build_resync_required(
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
        self.created = []
        self.deleted = []

    def record_job_updated(self, workspace_id, job_id):
        self.updated.append((workspace_id, job_id))
        return 1

    def record_job_created(self, workspace_id, job_id):
        self.created.append((workspace_id, job_id))
        return 1

    def record_job_deleted(self, workspace_id, job_id):
        self.deleted.append((workspace_id, job_id))
        return 1


def test_record_job_update_uses_event_buffer(fake_job_db):
    buffer = FakeJobEventBuffer()
    fake_job_db.jobs["job1"] = {"id": "job1", "workspace_id": "ws1"}

    record_job_update(fake_job_db, buffer, "job1")

    assert buffer.updated == [("ws1", "job1")]
