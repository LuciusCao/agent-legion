"""Watermark delta scan tests (DB-SCAN-INCREMENTAL-001).

Covers ``MarkStore`` (per-workflow mark cache with delta refresh) and the
worker-level wiring: after the first full marks fetch, poll passes must
only run the delta query, while new/completed/deleted jobs still converge.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.jobs import JobQueries
from server.app.workflow_worker.mark_scan import MarkStore
from tests.helpers.executor_worker import (
    allocate,
    bind,
    local_def,
    local_node,
    make_definition,
    make_registry,
    make_worker,
)
from tests.postgres_support import TEST_DATABASE_URL

_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


class BlockingExecutor:
    kind = "code"

    def __init__(self, executor_id: str, block_event: threading.Event) -> None:
        self.id = executor_id
        self.block_event = block_event

    def supports(self, capability: str) -> bool:
        return True

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        assert self.block_event.wait(timeout=10), "executor was not released in time"
        for output in context.expected_outputs:
            (context.job_dir / output).write_text('{"done": true}', encoding="utf-8")
        return ExecutionResult(status="completed", exit_code=0)

    def cancel(self, execution_id: str) -> None:
        pass


def _make_jobs(job_db: JobQueries, workspace_id: str, count: int, prefix: str = "Q") -> list[str]:
    job_ids = []
    for i in range(count):
        job = job_db.create_job(
            workflow_key="test",
            source_type="question",
            source_id=f"{prefix}{i}",
            batch_id="",
            title=f"{prefix}{i}",
            node_keys=["fetch"],
            workspace_id=workspace_id,
        )
        job_ids.append(str(job["id"]))
    return job_ids


def _spy(monkeypatch: pytest.MonkeyPatch, obj: object, attr: str) -> dict[str, int]:
    calls = {"count": 0}
    real = getattr(obj, attr)

    def spy(*args, **kwargs):
        calls["count"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(obj, attr, spy)
    return calls


def test_delta_query_returns_terminal_rows(tmp_path: Path) -> None:
    """The delta query has no status filter: terminal transitions stay visible."""
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Mark Scan WS")
    (job_id,) = _make_jobs(job_db, ws["id"], 1)
    job_db.update_job_status(job_id, "completed")

    marks = job_db.list_changed_job_marks("test", _EPOCH)
    assert [mark["id"] for mark in marks] == [job_id]
    assert marks[0]["status"] == "completed"


def test_store_full_then_delta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Mark Scan WS")
    _make_jobs(job_db, ws["id"], 3)
    full_calls = _spy(monkeypatch, job_db, "list_active_job_marks")
    delta_calls = _spy(monkeypatch, job_db, "list_changed_job_marks")

    store = MarkStore()
    assert len(store.refresh(job_db, "test")) == 3
    assert len(store.refresh(job_db, "test")) == 3
    assert full_calls["count"] == 1
    assert delta_calls["count"] == 1


def test_store_delta_sees_new_and_terminal_jobs(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Mark Scan WS")
    first, second = _make_jobs(job_db, ws["id"], 2)
    store = MarkStore()
    assert {mark["id"] for mark in store.refresh(job_db, "test")} == {first, second}

    job_db.update_job_status(first, "failed")
    (third,) = _make_jobs(job_db, ws["id"], 1, prefix="N")
    marks = store.refresh(job_db, "test")
    assert {mark["id"] for mark in marks} == {second, third}


def test_store_keeps_newest_first_order_after_delta(tmp_path: Path) -> None:
    """New jobs arriving via delta must not queue behind the cached backlog:
    claim order follows created_at desc (list_active_job_marks contract)."""
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Mark Scan WS")
    old_ids = _make_jobs(job_db, ws["id"], 2)
    store = MarkStore()
    store.refresh(job_db, "test")
    # Backdate the cached jobs so the new one is unambiguously newest;
    # created_at is not a mark_key field, so this does not perturb the delta.
    with job_db.connect() as conn:
        conn.execute(
            "update jobs set created_at=current_timestamp - interval '1 day' where id = any(%s)",
            (old_ids,),
        )
    (new_id,) = _make_jobs(job_db, ws["id"], 1, prefix="N")

    marks = store.refresh(job_db, "test")
    assert [mark["id"] for mark in marks][0] == new_id


def test_store_keeps_paused_and_recovers_on_resume(tmp_path: Path) -> None:
    """paused is not terminal: the mark stays cached (downstream is_runnable
    filters it), and a resume bumps updated_at so the delta re-admits it."""
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Mark Scan WS")
    (job_id,) = _make_jobs(job_db, ws["id"], 1)
    store = MarkStore()
    assert [mark["id"] for mark in store.refresh(job_db, "test")] == [job_id]

    job_db.update_job_status(job_id, "paused")
    marks = store.refresh(job_db, "test")
    assert [(mark["id"], mark["status"]) for mark in marks] == [(job_id, "paused")]

    job_db.update_job_status(job_id, "queued")
    marks = store.refresh(job_db, "test")
    assert [(mark["id"], mark["status"]) for mark in marks] == [(job_id, "queued")]


def test_store_readmits_rerun_job(tmp_path: Path) -> None:
    """failed -> queued (rerun) must bring the job back via the delta."""
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Mark Scan WS")
    (job_id,) = _make_jobs(job_db, ws["id"], 1)
    store = MarkStore()
    store.refresh(job_db, "test")

    job_db.update_job_status(job_id, "failed")
    assert store.refresh(job_db, "test") == []
    job_db.update_job_status(job_id, "queued")
    assert [mark["id"] for mark in store.refresh(job_db, "test")] == [job_id]


def test_store_burst_does_not_pin_delta_lower_bound(tmp_path: Path) -> None:
    """Rows sharing one bulk-insert commit timestamp must not be re-fetched
    on every pass once the overlap window has slid past the burst."""
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Mark Scan WS")
    _make_jobs(job_db, ws["id"], 3)

    seen: list[int] = []
    real = job_db.list_changed_job_marks

    def counting(*args, **kwargs):
        rows = real(*args, **kwargs)
        seen.append(len(rows))
        return rows

    job_db.list_changed_job_marks = counting  # type: ignore[method-assign]

    # Default overlap: right after the burst the window still covers it.
    store = MarkStore()
    store.refresh(job_db, "test")
    store.refresh(job_db, "test")
    assert seen[-1] == 3

    # Overlap slid past the burst (simulated via a future-shifted horizon):
    # the delta no longer returns the burst rows.
    store = MarkStore(overlap_seconds=-3600)
    store.refresh(job_db, "test")
    store.refresh(job_db, "test")
    assert seen[-1] == 0


def test_store_deleted_job_pruned_by_full_rescan(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Mark Scan WS")
    (job_id,) = _make_jobs(job_db, ws["id"], 1)
    store = MarkStore()
    assert len(store.refresh(job_db, "test")) == 1

    job_db.delete_job(job_id)
    # Deletion leaves no delta trace: the mark survives until the next full rescan.
    assert len(store.refresh(job_db, "test")) == 1
    store._states["test"].last_full_scan = 0.0
    assert store.refresh(job_db, "test") == []


def _setup_worker(tmp_path: Path, job_count: int, capacity: int):
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, jobs_dir=tmp_path / "jobs")
    ws = job_db.create_workspace("Mark Scan WS")
    block_event = threading.Event()
    registry = make_registry(
        {"code-default": BlockingExecutor("code-default", block_event)},
        {"code-default": local_def(capacity, {"fetch"})},
    )
    definition = make_definition([local_node("fetch")])
    _make_jobs(job_db, ws["id"], job_count)
    bind(job_db, ws["id"], "test", "fetch", "code-default")
    allocate(job_db, ws["id"], "code-default", capacity)
    worker = make_worker(tmp_path, db_path, registry, [definition])
    return worker, ws, block_event


def test_worker_second_pass_scans_delta_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the first pass pulls all marks; later passes run the delta query."""
    worker, _ws, block_event = _setup_worker(tmp_path, job_count=2, capacity=3)
    full_calls = _spy(monkeypatch, worker.job_db, "list_active_job_marks")
    delta_calls = _spy(monkeypatch, worker.job_db, "list_changed_job_marks")

    assert worker._poll() is True
    assert worker.leases.active_counts("code-default")["global"] == 2
    # Both jobs are running: no new claims, but the scan still happens via delta.
    assert worker._poll() is False
    assert full_calls["count"] == 1
    assert delta_calls["count"] == 1

    block_event.set()
    worker.stop()


def test_worker_delta_picks_up_new_job(tmp_path: Path) -> None:
    """A job enqueued after the first pass is claimed via the delta refresh."""
    worker, ws, block_event = _setup_worker(tmp_path, job_count=1, capacity=3)

    assert worker._poll() is True
    _make_jobs(worker.job_db, ws["id"], 1, prefix="N")
    assert worker._poll() is True
    assert worker.leases.active_counts("code-default")["global"] == 2

    block_event.set()
    worker.stop()


def test_full_marks_query_never_seq_scans(job_db: JobQueries) -> None:
    """Pin the performance property: the periodic full rescan must stay index
    driven. EXPLAIN runs the production query string itself (not a hand-copied
    predicate) so a drift in ``ACTIVE_MARKS_SQL`` fails here. Without the
    schema v35 partial index the planner seq-scans and sorts the whole jobs
    table once per rescan window (production: 138k rows, ~0.9s idle / 1-3s
    under load, flushing the page cache each time)."""
    from server.app.jobs.queries.job_scan_marks import ACTIVE_MARKS_SQL

    with job_db.connect() as conn:
        rows = conn.execute(f"explain {ACTIVE_MARKS_SQL}", ("questions",)).fetchall()

    plan = "\n".join(str(row[0]) for row in rows)
    assert "Seq Scan on jobs" not in plan
