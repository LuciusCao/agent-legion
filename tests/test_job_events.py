import asyncio
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from server.app.events import JobEventManager
from server.app.executors.leases import ExecutorLeaseRepository, _sqlite_timestamp
from server.app.executors.models import ConfigurationFailureRequest, ExecutionResult
from server.app.settings import Settings


class FakeJobDB:
    def get_job(self, job_id):
        return {"id": job_id, "workspace_id": "ws1", "storage_dir": f"jobs/{job_id}"}

    def count_jobs_by_status(self, workspace_id):
        return {"pending": 0, "running": 0, "completed": 0, "failed": 0}

    @contextmanager
    def lease_guarded_mutation(self, job_id, now, *, reject_running_nodes):
        yield MagicMock(spec=sqlite3.Connection)

    @staticmethod
    def delete_job_in_transaction(conn, job_id):
        pass


@pytest.fixture
def manager():
    m = JobEventManager()
    m._loop = asyncio.new_event_loop()
    return m


def _insert_workspace_job(conn):
    conn.execute(
        "insert into workspaces(id, name) values ('ws1', 'ws1') on conflict(id) do nothing"
    )
    conn.execute(
        """
        insert into jobs(id, workspace_id, pipeline_key, source_type, source_id)
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
            id, execution_id, executor_id, workspace_id, job_id, pipeline_key,
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
        pipeline_key="p1",
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
        lambda _conn, _lease_id, _result: (_ for _ in ()).throw(RuntimeError("simulated failure")),
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
