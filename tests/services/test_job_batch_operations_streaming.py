"""Streaming filter/failure selections: freeze, backpressure, and replay (#1362)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_batch_operations import (
    BatchOpsExecutors,
    JobBatchOpsFeeder,
    dispatch_batch_selection,
    dispatch_rerun_by_failure,
)
from server.app.services.job_batch_operations.selection import (
    load_chunk,
    prepare_batch_selection,
)
from server.app.services.job_packages import JobPackageService
from server.app.services.job_service_factory import JobServices
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.settings import Settings
from tests.helpers import publish_builtin_revision

WORKSPACE = "ops-stream-ws"
NODE_KEYS = ["intake_knowledge_points", "write_script", "review_script", "publish_content"]


@pytest.fixture
def workspace(job_db: JobQueries) -> str:
    created = job_db.create_workspace(WORKSPACE)
    publish_builtin_revision(job_db, created["id"])
    return str(created["id"])


def _create_job(job_db: JobQueries, workspace_id: str, source_id: str) -> dict[str, Any]:
    return job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id=source_id,
        run_id="",
        title=source_id,
        node_keys=list(NODE_KEYS),
        workspace_id=workspace_id,
    )


def _executors(job_db: JobQueries, settings: Settings) -> BatchOpsExecutors:
    services = JobServices(
        job_db,
        settings,
        WorkspaceExecutionConfigurationService(job_db, settings),
        None,
        None,
    )
    return BatchOpsExecutors(
        rerun=services.rerun,
        deletion=services.deletion,
        execution=services.execution,
        pause=services.pause,
        upgrade=services.workflow_upgrade,
        packages=JobPackageService(job_db, settings),
        approvals=services.approvals,
    )


def _feeder(job_db: JobQueries, settings: Settings) -> JobBatchOpsFeeder:
    return JobBatchOpsFeeder(job_db, settings, _executors(job_db, settings))


def _drain(feeder: JobBatchOpsFeeder) -> None:
    for _ in range(100):
        if not feeder.consume_once():
            return
    raise AssertionError("feeder did not quiesce within 100 ticks")


def _result_ids(job_db: JobQueries, op_id: str) -> list[str]:
    rows = job_db.list_job_batch_operation_results_page(op_id, limit=1000)
    return [str(row["job_id"]) for row in rows]


def _fail(job_db: JobQueries, job: dict[str, Any], category: str = "technical") -> None:
    run = job_db.start_node_run(job["id"], "write_script", ["cmd"], f"logs/{job['id']}.log")
    with job_db.connect() as conn:
        conn.execute(
            "update node_runs set status='failed', error_message='boom',"
            " failure_category=%s, failure_detail='d', finished_at=current_timestamp"
            " where id=%s",
            (category, run["id"]),
        )
        conn.execute(
            "update job_nodes set status='failed' where job_id=%s and node_key='write_script'",
            (job["id"],),
        )
        conn.execute("update jobs set status='failed' where id=%s", (job["id"],))


def test_filter_stream_freezes_late_jobs_and_discovers_total(
    workspace: str, job_db: JobQueries, settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 1
    )
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.feeder.JOB_BATCH_OPS_CHUNK_SIZE", 1
    )
    original = [_create_job(job_db, workspace, f"S-{index}") for index in range(3)]
    ids, operation = dispatch_batch_selection(
        job_db, workspace, "pause", None, JobListFilter(), (), {}, "user:tester"
    )
    assert operation is not None and len(ids) == 2
    op_id = str(operation["id"])
    spec = json.loads(str(operation["target_spec_json"]))
    assert spec["stream"]["source"] == "filter"
    assert spec["stream"]["freeze_at"]
    assert len(spec["job_ids"]) == 2
    late = _create_job(job_db, workspace, "S-late")

    feeder = _feeder(job_db, settings)
    assert feeder.consume_once() is True
    assert job_db.get_job_batch_operation(op_id)["items_total"] == 2
    _drain(feeder)

    operation = job_db.get_job_batch_operation(op_id)
    assert operation["status"] == "completed"
    assert operation["items_total"] == operation["next_index"] == 3
    assert set(_result_ids(job_db, op_id)) == {str(job["id"]) for job in original}
    assert str(late["id"]) not in _result_ids(job_db, op_id)
    assert not job_db.get_job(str(late["id"]))["execution_paused"]


def test_failure_stream_is_deterministic_and_excludes_late_failures(
    workspace: str, job_db: JobQueries, settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 1
    )
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.feeder.JOB_BATCH_OPS_CHUNK_SIZE", 1
    )
    original = [_create_job(job_db, workspace, f"F-{index}") for index in range(3)]
    for job in original:
        _fail(job_db, job)
    _, operation = dispatch_rerun_by_failure(
        job_db,
        workspace,
        category="technical",
        strategy="auto",
        from_node_key=None,
        job_ids=[],
        job_filter=None,
        exclude_ids=(),
        created_by="user:tester",
    )
    assert operation is not None
    op_id = str(operation["id"])
    assert json.loads(str(operation["target_spec_json"]))["stream"]["source"] == "failures"
    late = _create_job(job_db, workspace, "F-late")
    _fail(job_db, late)

    _drain(_feeder(job_db, settings))

    operation = job_db.get_job_batch_operation(op_id)
    assert operation["status"] == "completed"
    result_ids = _result_ids(job_db, op_id)
    assert len(result_ids) == len(set(result_ids)) == 3
    assert set(result_ids) == {str(job["id"]) for job in original}
    assert str(late["id"]) not in result_ids


def test_stream_crash_after_results_replays_without_reexecuting(
    workspace: str, job_db: JobQueries, settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 1
    )
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.feeder.JOB_BATCH_OPS_CHUNK_SIZE", 1
    )
    jobs = [_create_job(job_db, workspace, f"C-{index}") for index in range(3)]
    _, operation = dispatch_batch_selection(
        job_db, workspace, "pause", None, JobListFilter(), (), {}, "user:tester"
    )
    assert operation is not None
    op_id = str(operation["id"])
    executors = _executors(job_db, settings)
    calls: list[str] = []
    original_pause = executors.pause.pause

    def counting_pause(workspace_id: str, job_id: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(job_id)
        return original_pause(workspace_id, job_id, *args, **kwargs)

    monkeypatch.setattr(executors.pause, "pause", counting_pause)
    feeder = JobBatchOpsFeeder(job_db, settings, executors)
    assert feeder.consume_once() is True
    assert feeder.consume_once() is True
    assert job_db.get_job_batch_operation(op_id)["next_index"] == 2
    claimed = job_db.claim_job_batch_operation()
    assert claimed is not None
    original_advance = job_db.advance_job_batch_operation

    def crash_before_advance(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated process crash before cursor commit")

    monkeypatch.setattr(job_db, "advance_job_batch_operation", crash_before_advance)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        feeder._consume_chunk(claimed)  # refills and executes page 2, then crashes before its CAS
    assert len(calls) == 3
    crashed = job_db.get_job_batch_operation(op_id)
    assert crashed["next_index"] == 2
    assert len(json.loads(str(crashed["target_spec_json"]))["job_ids"]) == 2
    monkeypatch.setattr(job_db, "advance_job_batch_operation", original_advance)
    job_db.transition_job_batch_operation(op_id, from_statuses=("running",), to_status="pending")

    _drain(feeder)

    assert calls == [str(jobs[2]["id"]), str(jobs[1]["id"]), str(jobs[0]["id"])]
    operation = job_db.get_job_batch_operation(op_id)
    assert operation["status"] == "completed" and operation["next_index"] == 3
    assert len(_result_ids(job_db, op_id)) == 3


def test_pause_does_not_refill_and_resume_keeps_frozen_stream_cursor(
    workspace: str, job_db: JobQueries, settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 1
    )
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.feeder.JOB_BATCH_OPS_CHUNK_SIZE", 1
    )
    jobs = [_create_job(job_db, workspace, f"P-{index}") for index in range(3)]
    _, operation = dispatch_batch_selection(
        job_db, workspace, "pause", None, JobListFilter(), (), {}, "user:tester"
    )
    assert operation is not None
    op_id = str(operation["id"])
    frozen_spec = str(operation["target_spec_json"])
    executors = _executors(job_db, settings)
    calls: list[str] = []
    original_pause = executors.pause.pause

    def counting_pause(workspace_id: str, job_id: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(job_id)
        return original_pause(workspace_id, job_id, *args, **kwargs)

    monkeypatch.setattr(executors.pause, "pause", counting_pause)
    original_advance = job_db.advance_job_batch_operation
    advance_calls = 0

    def pause_on_second_advance(op_id: str, **kwargs: Any) -> Any:
        nonlocal advance_calls
        advance_calls += 1
        if advance_calls == 2:
            job_db.transition_job_batch_operation(
                op_id, from_statuses=("running",), to_status="paused"
            )
        return original_advance(op_id, **kwargs)

    monkeypatch.setattr(job_db, "advance_job_batch_operation", pause_on_second_advance)
    feeder = JobBatchOpsFeeder(job_db, settings, executors)
    assert feeder.consume_once() is True
    assert feeder.consume_once() is True

    paused = job_db.get_job_batch_operation(op_id)
    assert paused["status"] == "paused" and paused["next_index"] == 2
    assert paused["items_total"] == 2
    assert str(paused["target_spec_json"]) == frozen_spec
    assert feeder.consume_once() is False
    assert len(calls) == 2

    monkeypatch.setattr(job_db, "advance_job_batch_operation", original_advance)
    job_db.transition_job_batch_operation(op_id, from_statuses=("paused",), to_status="pending")
    _drain(feeder)

    completed = job_db.get_job_batch_operation(op_id)
    assert completed["status"] == "completed" and completed["next_index"] == 3
    assert len(calls) == len(set(calls)) == 3
    assert set(calls) == {str(job["id"]) for job in jobs}


def test_stream_page_failure_marks_operation_failed(
    workspace: str, job_db: JobQueries, settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 1
    )
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.feeder.JOB_BATCH_OPS_CHUNK_SIZE", 1
    )
    for index in range(3):
        _create_job(job_db, workspace, f"E-{index}")
    _, operation = dispatch_batch_selection(
        job_db, workspace, "pause", None, JobListFilter(), (), {}, "user:tester"
    )
    assert operation is not None
    op_id = str(operation["id"])
    feeder = _feeder(job_db, settings)
    assert feeder.consume_once() is True
    assert feeder.consume_once() is True

    def fail_next_page(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected paging failure")

    monkeypatch.setattr(
        "server.app.services.job_batch_operations.selection.resolve_job_ids_page",
        fail_next_page,
    )
    assert feeder.consume_once() is True
    operation = job_db.get_job_batch_operation(op_id)
    assert operation["status"] == "failed"
    assert operation["next_index"] == 2
    assert "injected paging failure" in operation["error_message"]


def test_watermark_blocks_stream_resolution(
    workspace: str, job_db: JobQueries, settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 1
    )
    for index in range(2):
        job = _create_job(job_db, workspace, f"W-{index}")
        job_db.update_job_status(job["id"], "failed")
    _, operation = dispatch_batch_selection(
        job_db,
        workspace,
        "rerun",
        None,
        JobListFilter(status="failed"),
        (),
        {"node_key": "write_script"},
        "user:tester",
    )
    assert operation is not None
    feeder = _feeder(job_db, settings)
    monkeypatch.setattr(feeder, "_watermark", lambda: 0)
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.feeder.load_chunk",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resolved while gated")),
    )
    assert feeder.consume_once() is False
    assert job_db.get_job_batch_operation(str(operation["id"]))["next_index"] == 0


@pytest.mark.no_db
def test_hundred_thousand_selection_uses_bounded_buffers(monkeypatch) -> None:
    total = 100_000
    calls: list[int] = []

    def fake_page(
        job_db: Any,
        workspace_id: str,
        job_filter: JobListFilter,
        limit: int,
        *,
        cursor: str | None = None,
        freeze_at: str | None = None,
        exclude_ids: Any = (),
    ) -> tuple[list[str], str | None, str | None]:
        calls.append(limit)
        start = int(cursor or 0)
        stop = min(start + limit, total)
        next_cursor = str(stop) if stop < total else None
        return [f"job-{index}" for index in range(start, stop)], next_cursor, freeze_at or "0"

    monkeypatch.setattr(
        "server.app.services.job_batch_operations.selection.resolve_job_ids_page", fake_page
    )
    ids, stream = prepare_batch_selection(None, "ws", None, JobListFilter(), (), 1000)
    assert len(ids) == 1001 and stream is not None and calls == [1001]
    payload: dict[str, Any] = {"job_ids": ids, "params": {}, "stream": stream}
    processed = 0
    max_buffer = len(ids)
    while True:
        buffer, buffer_start, discovered, payload, _ = load_chunk(
            None, "ws", payload, processed, 200
        )
        max_buffer = max(max_buffer, len(buffer))
        end = min(processed + 200, discovered)
        assert buffer[0] == f"job-{buffer_start}"
        assert end - processed <= len(buffer)
        processed = end
        if processed == discovered and payload["stream"]["next_cursor"] is None:
            break
    assert processed == total
    assert max_buffer == 1001
    assert len(payload["job_ids"]) <= 200
    assert len(json.dumps(payload)) < 20_000
