from __future__ import annotations

from pathlib import Path

import pytest

from server.app.scheduler_wakeup import (
    notify_schedulable_work,
    register_wakeup,
    unregister_wakeup,
)
from server.app.services.job_intake import JobIntakeService
from server.app.services.job_intake_queue import JobIntakeQueue
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.postgres_support import TEST_DATABASE_URL
from tests.workers.helpers import RecordingExecutor, _make_worker


@pytest.fixture
def registered():
    callbacks = []
    yield callbacks
    for callback in callbacks:
        unregister_wakeup(callback)


def test_notify_invokes_registered_callbacks(registered) -> None:
    calls: list[str] = []

    def _callback() -> None:
        calls.append("called")

    registered.append(_callback)
    register_wakeup(_callback)
    notify_schedulable_work()
    assert calls == ["called"]

    unregister_wakeup(_callback)
    notify_schedulable_work()
    assert calls == ["called"]


def test_notify_survives_failing_callback(registered) -> None:
    calls: list[str] = []

    def _boom() -> None:
        raise RuntimeError("boom")

    def _callback() -> None:
        calls.append("called")

    registered.extend([_boom, _callback])
    register_wakeup(_boom)
    register_wakeup(_callback)

    notify_schedulable_work()  # must not raise

    assert calls == ["called"]


def test_worker_wake_sets_wake_event(tmp_path: Path, registered) -> None:
    worker = _make_worker(tmp_path, TEST_DATABASE_URL, RecordingExecutor("local-default"), [])
    try:
        assert not worker._wake_event.is_set()
        registered.append(worker.wake)
        register_wakeup(worker.wake)
        notify_schedulable_work()
        assert worker._wake_event.is_set()
    finally:
        worker.stop()


def _create_workspace_with_revision(job_db, settings):
    workspace = job_db.create_workspace(
        "default", default_workflow_key="question_comprehension_info"
    )
    definition = WorkflowCatalogService(settings).definition("question_comprehension_info")
    WorkflowRevisionService(job_db).ensure_active_revision(workspace["id"], definition)
    return workspace


def test_job_intake_create_batch_notifies(job_db, settings, monkeypatch) -> None:
    _create_workspace_with_revision(job_db, settings)
    calls: list[int] = []
    monkeypatch.setattr(
        "server.app.services.job_intake.notify_schedulable_work", lambda: calls.append(1)
    )
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))

    result = service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "question_ids": ["Q1"],
            "knowledge_codes": [],
        },
    )

    assert result["created_count"] == 1
    assert calls == [1]


def test_intake_queue_chunk_jobs_notify(job_db, settings, monkeypatch) -> None:
    _create_workspace_with_revision(job_db, settings)
    calls: list[int] = []
    monkeypatch.setattr(
        "server.app.services.job_intake_queue.notify_schedulable_work", lambda: calls.append(1)
    )
    service = JobIntakeService(job_db, settings, WorkflowCatalogService(settings))
    result = service.create_batch(
        "default",
        {
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "question_ids": ["Q1"],
            "knowledge_codes": [],
            "async_processing": True,
        },
    )
    assert result["created_count"] == 0
    assert calls == []

    queue = JobIntakeQueue(job_db, settings)
    claimed = job_db.claim_intake_batch()
    assert claimed is not None
    queue._consume_chunk(claimed)

    assert calls == [1]
